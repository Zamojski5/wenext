"""`GET /api/approvals` — the order and the digest (L4595, DoD L5218).

TWO REQUIREMENTS, both of which a plausible implementation gets wrong without any symptom.

1.  **Ascending by `expires_at`** (L4595). Every other list in this service is newest-first, so
    `ORDER BY created_at DESC` is what a hand reaches for; it looks natural, it survives an eyeball
    review, and it hands the operator the cards that will outlive everything before the ones that
    expire in a minute. The test below builds a queue whose creation order and expiry order
    CONTRADICT each other — with a tie in `expires_at` on top, because a sort key that only ever
    sees distinct values proves nothing about the tie-break. A queue built in expiry order would
    pass under either implementation, which is why this one is not built that way.

2.  **The digest is rendered in full, all 64 characters.** "approval wskazuje dokładny payload
    hash" (L5218). The test compares the JSON against the digest recomputed by
    `ApprovalHashInput.payload_hash()` — not against the string the API returned, which would
    agree with itself no matter how much of it was cut off.

The rows for the ordering test are inserted with RAW SQL, through no code of this package. That is
the same choice `test_jobs_api.py` makes and for the same reason: the screen has to show what the
canonical store holds, and a test that writes through the API and reads through the API also passes
against an in-memory registry. This repository has one of those (`TicketRegistry` in
`services/stream_gateway`), whose own docstring calls it a shortcut and whose every test is green.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from community_os_contracts import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.api.approvals import APPROVALS_PATH, PAYLOAD_HASH_LENGTH, _entry_payload
from control_plane.approval.models import ApprovalQueueEntry
from control_plane.approval.store import ApprovalStore
from fastapi import HTTPException

pytestmark = [pytest.mark.db]

#: Five cards. Both offsets are measured from ONE base moment: `created_after` fixes the creation
#: order, `expires_after` the expiry order. Deliberately not "created_at plus a TTL" — with a TTL
#: the two cards meant to TIE on `expires_at` would miss each other by exactly the difference in
#: their creation times, and the tie-break half of this test would silently stop being exercised.
#: The two orders DISAGREE, and two cards tie:
#:
#:   creation order (A B C D E)   vs   expiry order (C, then B and E tied, then D, then A)
#:
#: A queue ordered by `created_at` therefore comes back A B C D E, and one ordered by `expires_at`
#: comes back C B E D A. No implementation can satisfy both.
#: The `state` column carries a THIRD job: three different values appear, so the route is also shown
#: not to filter by state. A default filter is the most comfortable way to build a list nobody can
#: trust — the page returns rows, looks alive, and a whole category is invisible with no error.
_CARDS: tuple[tuple[str, timedelta, timedelta, ActionProposalState], ...] = (
    ("A", timedelta(seconds=0), timedelta(minutes=50), ActionProposalState.READY_FOR_APPROVAL),
    ("B", timedelta(seconds=1), timedelta(minutes=30), ActionProposalState.DRAFT),
    ("C", timedelta(seconds=2), timedelta(minutes=10), ActionProposalState.SUPERSEDED),
    ("D", timedelta(seconds=3), timedelta(minutes=40), ActionProposalState.READY_FOR_APPROVAL),
    ("E", timedelta(seconds=4), timedelta(minutes=30), ActionProposalState.APPROVED),
)

#: The order the requirement demands. `B` before `E` because they tie on `expires_at` and the
#: tie-break is `created_at` — see `approval.store._QUEUE_SQL`, where `created_at` appears ONLY
#: behind `expires_at` and never as the leading key.
_EXPECTED_ORDER = ("C", "B", "E", "D", "A")

#: The order a `created_at` sort would produce. Named so the failure message can say which of the
#: two mistakes was made, rather than only that the list was wrong.
_CREATED_AT_ORDER = ("A", "B", "C", "D", "E")

_INSERT_REVISION_SQL = """
INSERT INTO action_proposal_revisions
    (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
     source_fingerprint, expires_at, state, created_at, source_observed_at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $10)
RETURNING id
"""


async def _proposal(connection, organization_id: UUID, workspace_id: UUID) -> UUID:
    """A `social_accounts` row and an `action_proposals` row hanging off it (composite FK, D-020)."""
    account = await connection.fetchval(
        """
        INSERT INTO social_accounts (organization_id, platform, external_account_id)
        VALUES ($1, 'linkedin', $2)
        RETURNING id
        """,
        organization_id,
        f"external-{uuid4()}",
    )
    return await connection.fetchval(
        """
        INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        organization_id,
        workspace_id,
        account,
    )


def _digest(label: str) -> str:
    """A distinct, well-formed 64-character hex digest per card.

    The column has `CHECK (payload_hash ~ '^[0-9a-f]{64}$')` in migration 0003, so a placeholder
    like `"hash-A"` would be rejected by the database — and the rejection would look like a bug in
    the code under test rather than in the fixture.
    """
    return (format(ord(label), "02x") * 32)[:64]


async def test_the_queue_is_sorted_ascending_by_expires_at(pool, tenant, logged_in_client) -> None:
    """L4595. Creation order and expiry order deliberately disagree, and one pair ties.

    The rows go in with raw SQL and in creation order, so nothing about the insertion sequence
    helps the correct answer.
    """
    async with pool.acquire() as connection:
        proposal_id = await _proposal(connection, tenant["organization"], tenant["workspace"])
        base = await connection.fetchval("SELECT now()")
        identifiers: dict[str, UUID] = {}
        for index, (label, created_after, expires_after, state) in enumerate(_CARDS, start=1):
            created_at = base + created_after
            identifiers[label] = await connection.fetchval(
                _INSERT_REVISION_SQL,
                tenant["organization"],
                tenant["workspace"],
                proposal_id,
                index,
                json.dumps({"label": label}),
                _digest(label),
                f"sha256:zrodlo-{label}",
                base + expires_after,
                state.value,
                created_at,
            )

    assert len(identifiers) == len(_CARDS) == 5, identifiers

    response = await logged_in_client.get(APPROVALS_PATH)
    assert response.status_code == 200, response.text
    body = response.json()

    # Cardinality FIRST: an empty list satisfies every ordering assertion ever written, and
    # `all([])` has produced four false passes in this repository.
    assert body["count"] == 5, body
    assert len(body["approvals"]) == 5, body

    by_id = {str(value): label for label, value in identifiers.items()}
    observed = tuple(by_id[row["revision_id"]] for row in body["approvals"])

    assert observed == _EXPECTED_ORDER, (
        f"kolejka wróciła w kolejności {observed}, a L4595 wymaga {_EXPECTED_ORDER} — rosnąco po "
        f"expires_at. Kolejność {_CREATED_AT_ORDER} znaczy sortowanie po created_at: naturalne, "
        "przechodzi każdy ogląd i podaje operatorowi najpierw karty, które i tak go przeżyją"
    )
    assert observed != _CREATED_AT_ORDER, observed

    # The property itself, independent of the table above: the sequence is non-decreasing.
    # Parsed into datetimes rather than compared as strings — a lexicographic comparison of ISO
    # timestamps is right only as long as every value carries the same offset and the same number
    # of fractional digits, which is a property of this fixture and not of the endpoint.
    expiries = [datetime.fromisoformat(row["expires_at"]) for row in body["approvals"]]
    assert expiries == sorted(expiries), expiries
    # And it is genuinely a mixture, not an accidentally sorted-by-anything list.
    assert len(set(expiries)) == 4, expiries

    assert body["sorted_by"] == "expires_at"

    # No state filter, by default or otherwise: all four states that went in come back out.
    states = {row["state"] for row in body["approvals"]}
    assert states == {state.value for _label, _c, _e, state in _CARDS}, states


async def test_the_list_shows_the_full_sixty_four_character_payload_hash(
    pool, tenant, logged_in_client
) -> None:
    """L5218: the consent points at the EXACT payload hash, so the screen shows the exact hash.

    The expected value is recomputed from `ApprovalHashInput`, not read off the response. Comparing
    the response with itself — `len(row["payload_hash"]) == len(row["payload_hash"])` in effect —
    is how a truncating renderer stays green.
    """
    store = ApprovalStore(pool)
    async with pool.acquire() as connection:
        account = await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'linkedin', $2)
            RETURNING id
            """,
            tenant["organization"],
            f"external-{uuid4()}",
        )
        moment = await connection.fetchval("SELECT now()")

    hash_input = ApprovalHashInput(
        organization=tenant["organization"],
        workspace=tenant["workspace"],
        assignment_version=UUID("33333333-3333-4333-8333-333333333333"),
        account=account,
        platform="linkedin",
        action_type=SemanticCapability.PUBLISH_REPLY,
        target_external_id="urn:li:activity:7000000000000000003",
        target_url="https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000003/",
        source_fingerprint="sha256:zrodlo-3",
        text_and_media="Treść karty approvalu, z polskimi znakami: ćma, żółw, ślaz.",
        intelligence_profile_version=UUID("55555555-5555-4555-8555-555555555555"),
        plan_version=UUID("66666666-6666-4666-8666-666666666666"),
        memory_snapshot=UUID("77777777-7777-4777-8777-777777777777"),
        adapter_version="linkedin-adapter@0.1.0",
    )
    expected_digest = hash_input.payload_hash()
    assert len(expected_digest) == PAYLOAD_HASH_LENGTH == 64

    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=hash_input,
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=moment + timedelta(hours=1),
    )

    response = await logged_in_client.get(APPROVALS_PATH)
    assert response.status_code == 200, response.text
    rows = response.json()["approvals"]

    assert len(rows) == 1, rows
    row = rows[0]
    assert row["revision_id"] == str(revision.id)
    assert row["payload_hash"] == expected_digest, (
        f"ekran pokazuje {row['payload_hash']!r}, a rewizja ma {expected_digest!r}. Skrócony albo "
        "podmieniony hash to ekran, na którym dwie różne treści wyglądają identycznie — a to jest "
        "dokładnie ta linia DoD, dla której ten ekran istnieje (L5218)"
    )
    assert len(row["payload_hash"]) == 64
    # The four fields the requirement names, each present and not `None`.
    assert row["state"] == ActionProposalState.READY_FOR_APPROVAL.value
    assert row["created_at"] and row["expires_at"]
    assert row["created_at"] == revision.created_at.isoformat()
    assert row["expires_at"] == revision.expires_at.isoformat()


async def test_a_revision_of_another_organization_is_not_in_the_queue(
    pool, tenant, logged_in_client
) -> None:
    """The scope comes from the session. The query string is not an identity.

    Same rule and same words as `api/audit.py` and `api/jobs.py`. Without it the approval queue is
    a cross-tenant read the moment E1's "one organization" assumption stops holding — and this is
    the queue that carries the content of drafts.
    """
    async with pool.acquire() as connection:
        stranger_org = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obcy-tenant-approvale') RETURNING id"
        )
        stranger_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            stranger_org,
        )
        foreign_proposal = await _proposal(connection, stranger_org, stranger_workspace)
        own_proposal = await _proposal(connection, tenant["organization"], tenant["workspace"])
        base = await connection.fetchval("SELECT now()")

        # The foreign card expires FIRST, so an unscoped query sorted by `expires_at` would put it
        # at the very top of the operator's screen.
        await connection.fetchval(
            _INSERT_REVISION_SQL,
            stranger_org,
            stranger_workspace,
            foreign_proposal,
            1,
            json.dumps({"label": "obcy"}),
            _digest("F"),
            "sha256:obcy",
            base + timedelta(minutes=1),
            ActionProposalState.READY_FOR_APPROVAL.value,
            base,
        )
        own_revision = await connection.fetchval(
            _INSERT_REVISION_SQL,
            tenant["organization"],
            tenant["workspace"],
            own_proposal,
            1,
            json.dumps({"label": "wlasny"}),
            _digest("O"),
            "sha256:wlasny",
            base + timedelta(minutes=30),
            ActionProposalState.READY_FOR_APPROVAL.value,
            base,
        )

    response = await logged_in_client.get(APPROVALS_PATH)
    assert response.status_code == 200, response.text
    rows = response.json()["approvals"]

    # Cardinality before membership: an empty list would satisfy "the foreign card is absent".
    assert len(rows) == 1, rows
    assert rows[0]["revision_id"] == str(own_revision)
    assert rows[0]["organization_id"] == str(tenant["organization"])


async def test_the_empty_queue_is_an_empty_list_and_never_a_placeholder(
    logged_in_client,
) -> None:
    """D-013: "Ekran pusty przy braku danych ma pokazywać stan pusty, nigdy atrapy."

    In F1 this is the NORMAL case, not an edge one: there is no producer of proposals at all, so on
    a clean database this is what the screen shows.
    """
    response = await logged_in_client.get(APPROVALS_PATH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approvals"] == []
    assert body["count"] == 0
    assert body["truncated"] is False


def test_a_truncated_digest_is_a_refusal_not_a_shorter_string() -> None:
    """The guard inside `_entry_payload`, measured directly. No database and no HTTP.

    The route's own test compares the rendered digest with the one `ApprovalHashInput` recomputes,
    so a truncation is caught there too. This test is about the SECOND lock on the same door: an
    entry that reaches the renderer already short must produce a refusal rather than a short
    string, because by then nothing downstream can tell a prefix from a digest. Migration 0003 puts
    the same rule on the column as a `CHECK`; this is the half that guards the path between the
    column and the JSON.
    """
    short = ApprovalQueueEntry(
        revision_id=uuid4(),
        proposal_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        revision_no=1,
        state=ActionProposalState.READY_FOR_APPROVAL,
        payload_hash="deadbeef",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        source_observed_at=datetime.now(UTC),
    )
    with pytest.raises(HTTPException) as refusal:
        _entry_payload(short)
    assert refusal.value.status_code == 500

    # Positive control: a full digest renders, so the guard is a guard and not a wall.
    full = replace(short, payload_hash="a" * PAYLOAD_HASH_LENGTH)
    assert _entry_payload(full)["payload_hash"] == "a" * 64


async def test_a_page_that_filled_up_says_there_may_be_more(pool, tenant, logged_in_client) -> None:
    """`truncated`, which until now was only ever seen as `False` on an EMPTY queue.

    `test_the_empty_queue_is_an_empty_list_and_never_a_placeholder` asserts `truncated is False`
    for a queue with nothing in it — and `False` is what a route returning a hard-coded `False`
    would say too. The signal was therefore reported by the response and measured by nobody in the
    one direction that carries information.

    Three pages over one queue of three cards, and the three answers are different for three
    different reasons:

    *   `limit=2` — the page is shorter than the queue: `True`, and there really is more;
    *   `limit=3` — the page is exactly as long as the queue: `True`, because the flag means
        "may be more" and this route does not read an extra row to find out. That case is here so
        the meaning is pinned rather than left to the reader;
    *   `limit=10` — the page is longer than the queue: `False`, and this is the negative control
        without which a route hard-coding `True` would pass the two above.

    WHAT THIS DOES NOT MEASURE, because it cannot: no screen consumes the flag. The panel's
    `/approvals` is a signpost that reads nothing (`apps/dashboard/lib/routes.ts` marks it
    `reads: false`), unlike the jobs and audit views which print it. The docstring of
    `DEFAULT_PAGE_LIMIT` says so in as many words instead of implying a screen that does not exist.
    """
    async with pool.acquire() as connection:
        proposal_id = await _proposal(connection, tenant["organization"], tenant["workspace"])
        base = await connection.fetchval("SELECT now()")
        for index, label in enumerate("XYZ", start=1):
            await connection.fetchval(
                _INSERT_REVISION_SQL,
                tenant["organization"],
                tenant["workspace"],
                proposal_id,
                index,
                json.dumps({"label": label}),
                _digest(label),
                f"sha256:zrodlo-{label}",
                base + timedelta(minutes=10 * index),
                ActionProposalState.READY_FOR_APPROVAL.value,
                base,
            )

    async def page(limit: int):
        response = await logged_in_client.get(APPROVALS_PATH, params={"limit": limit})
        assert response.status_code == 200, response.text
        return response.json()

    short = await page(2)
    assert short["count"] == 2 and len(short["approvals"]) == 2, short
    assert short["limit"] == 2
    assert short["truncated"] is True, (
        "strona krótsza niż kolejka zgłasza „to jest wszystko” — operator widzi dwie karty z "
        "trzech i nie ma jak się dowiedzieć, że trzecia istnieje"
    )

    exact = await page(3)
    assert exact["count"] == 3, exact
    assert exact["truncated"] is True, (
        "strona wypełniona co do wiersza zgłasza „to jest wszystko”, a flaga znaczy „może być "
        "więcej” — trasa nie czyta dodatkowego wiersza, żeby to rozstrzygnąć"
    )

    # The negative control. Without it a route hard-coding `True` passes both assertions above.
    roomy = await page(10)
    assert roomy["count"] == 3, roomy
    assert roomy["truncated"] is False, (
        "strona dłuższa niż kolejka zgłasza „może być więcej” — wtedy flaga jest stałą, a nie "
        "sygnałem, i nie niesie żadnej informacji"
    )


@pytest.mark.parametrize("limit", [0, -1, 1001])
async def test_a_page_limit_outside_the_range_is_refused(logged_in_client, limit) -> None:
    """A limit of zero returns an empty page that looks exactly like an empty queue.

    Same shape as `api/jobs.py` and `api/audit.py`: the bound is reported in the response and a
    caller asking for something outside it gets a 400 rather than a page that silently means
    something else.
    """
    response = await logged_in_client.get(APPROVALS_PATH, params={"limit": limit})
    assert response.status_code == 400, response.text
