"""Sending a draft from the card, against PostgreSQL: ONLY THE HUMAN'S CLICK CAN DO IT.

Nothing here publishes. A send ends in a row of the publisher's QUEUE (`publication_requests`,
status QUEUED) and one job row that nudges the publisher; no adapter, no capsule and no network is
reached, and the tests that want a "published" request write the rows the publisher leaves behind,
exactly as `test_opportunities_db` does. The live stack is not touched: the database is the
throwaway one of `run_db_tests.sh`.

THE QUESTIONS, EACH A WAY THE CARD COULD SEND WHAT NOBODY MEANT TO SEND:

  * does the first click of a project with no signed scope sign ONLY the minimal REVIEW scope the
    card said, through the same store the dashboard form uses, and then send through the same
    code as the dashboard's "Opublikuj" (`presence/human_approval.py`)?
  * does a click that was not a click (no token, a guessed one, an expired one, a spent one, one
    minted for another draft) sign nothing and queue nothing?
  * does a changed world refuse: a draft rewritten or rejected after the card showed it, a scope
    revoked, a pause (a stop a click must not lift), a scope that is not REVIEW?
  * does the pace the signature binds still bind?
  * (11.10) does ONE consent cover every account of the project the platform matrix lets write,
    named by the server and never by the card; does a refusal that passes (a busy queue, a scope
    that moved) clear by asking again, without a send until the person clicks?
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from community_os_contracts.execution import ActionProposalState
from control_plane.approval.store import ApprovalStore
from control_plane.presence.scope import Budget, Scope, digest
from control_plane.presence.store import PresenceStore
from control_plane.tools.card_send import META_TOKEN, SendTokens
from tests.test_opportunities_db import (
    World,
    _account,
    _draft,
    _mark,
    _mark_published,
    _reddit_args,
    _world,
    _x_args,
)

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _drafted(world: World, post: str = "abc123", text: str = "Moja odpowiedź.") -> dict:
    """A conversation marked and answered with a draft. Returns `{revision, odcisk}`."""
    marked = await _mark(world, **_reddit_args(post=post))
    drafted = await _draft(world, marked["okazja"]["url"], text)
    assert drafted["stan"] == "OCZEKUJE", drafted
    return {
        "revision": drafted["propozycja"]["revision_id"],
        "odcisk": drafted["propozycja"]["payload_hash"],
    }


async def _drafted_x(world: World, login: str, status: str = "2103587050346250341") -> dict:
    """A reply on X from the project's X account `login`. Returns `{revision, odcisk}`."""
    marked = await _mark(world, **_x_args(status=status))
    drafted = await world.registry.call(
        "draft.create",
        {
            "kind": "REPLY",
            "target_url": marked["okazja"]["url"],
            "text": "Krótko i na temat.",
            "account": login,
        },
    )
    assert drafted["stan"] == "OCZEKUJE", drafted
    return {
        "revision": drafted["propozycja"]["revision_id"],
        "odcisk": drafted["propozycja"]["payload_hash"],
    }


async def _let_the_queue_go(pool, world: World, status: str = "SKIPPED") -> None:
    """What the publisher does in a minute: the requests leave the queue, so a new signature no
    longer stops anything and the hour's pace is free again."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status = $2, detail = 'test' WHERE project_id = $1",
            world.project_id,
            status,
        )


async def _scope_accounts(pool, world: World) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT scope FROM project_delegations WHERE project_id = $1 AND revoked_at IS NULL",
            world.project_id,
        )
    assert len(rows) == 1, "jeden żywy podpis"
    return set(_j(rows[0]["scope"])["accounts"])


async def _token(world: World, draft: dict) -> tuple[dict, str | None]:
    """The card's first step. Returns the answer and the confirmation, which is only in `_meta`."""
    minted = await world.registry.call(
        "room.send_token", {"revision_id": draft["revision"], "odcisk": draft["odcisk"]}
    )
    return minted, (minted.get("_mcp_meta") or {}).get(META_TOKEN)


async def _send(world: World, draft: dict, token: str) -> dict:
    return await world.registry.call(
        "room.send_draft", {"revision_id": draft["revision"], "token": token}
    )


async def _click(world: World, draft: dict) -> dict:
    """A person's two clicks: "Wyślij", then "Tak, wyślij"."""
    minted, token = await _token(world, draft)
    assert minted["stan"] == "WYKONANE" and token, minted
    return await _send(world, draft, token)


async def _sign(pool, tenant, world: World, *, mode="REVIEW", accounts=None, budget=None):
    return await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
        mode=mode,
        scope=Scope(
            accounts=tuple(accounts or (world.account_id,)),
            communities=("r/SaaS",),
            actions=("REPLY",),
        ),
        budget=budget or Budget(publications_per_hour=6, publications_per_day=6),
        operator_id=tenant["operator"],
        expires_at=(datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0),
    )


async def _counts(pool, world: World) -> dict[str, int]:
    """What a send could have written, so a refusal can be shown to have written none of it."""
    async with pool.acquire() as conn:
        return {
            "delegations": await conn.fetchval(
                "SELECT count(*) FROM project_delegations WHERE project_id = $1", world.project_id
            ),
            "requests": await conn.fetchval(
                "SELECT count(*) FROM publication_requests WHERE project_id = $1", world.project_id
            ),
            "decisions": await conn.fetchval(
                "SELECT count(*) FROM approval_decisions d JOIN action_proposal_revisions r "
                "ON r.id = d.proposal_revision_id WHERE r.payload ->> 'plan_version' = $1::text",
                str(world.project_id),
            ),
        }


def _j(value):
    """A `jsonb` column as the driver hands it back: text, unless a codec was set."""
    return json.loads(value) if isinstance(value, str) else value


async def _state(pool, revision: str) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT state FROM action_proposal_revisions WHERE id = $1", uuid.UUID(revision)
        )


# ---------------------------------------------------------------------------
# The first click: one click signs the minimal scope and sends
# ---------------------------------------------------------------------------


async def test_the_first_click_signs_the_minimal_review_scope_and_queues_the_draft(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    assert await _counts(pool, world) == {"delegations": 0, "requests": 0, "decisions": 0}

    # Step one asks and promises: nothing is signed by a question.
    minted, token = await _token(world, draft)
    assert minted["stan"] == "WYKONANE" and token and len(token) >= 40
    assert minted["konto"] == world.login
    assert minted["zakres"]["rodzaj"] == "PODPISZ" and minted["zakres"]["tryb"] == "REVIEW"
    assert minted["zakres"]["limity"] == {"godzina": 1, "doba": 6}
    assert (await _counts(pool, world))["delegations"] == 0, "pytanie podpisało zakres"
    # THE TOKEN IS ONLY IN `_meta`: not in a field a model or a host reads as text.
    assert token not in json.dumps({k: v for k, v in minted.items() if k != "_mcp_meta"})

    sent = await _send(world, draft, token)
    assert sent["stan"] == "OCZEKUJE", sent
    assert sent["podpisano"] is True and sent["zakres"]["rodzaj"] == "PODPISZ"
    assert "opublikowano" in sent["powod"], "kolejka to nie post"

    async with pool.acquire() as conn:
        delegation = await conn.fetchrow(
            "SELECT * FROM project_delegations WHERE project_id = $1", world.project_id
        )
        request = await conn.fetchrow(
            "SELECT * FROM publication_requests WHERE project_id = $1", world.project_id
        )
        decision = await conn.fetchrow(
            "SELECT d.* FROM approval_decisions d WHERE d.id = $1", request["approval_decision_id"]
        )
        revision = await conn.fetchrow(
            "SELECT state, payload_hash FROM action_proposal_revisions WHERE id = $1",
            uuid.UUID(draft["revision"]),
        )
        audit = await conn.fetch(
            "SELECT kind, actor_ref FROM audit_events WHERE subject_ref = $1",
            f"delegation:{delegation['id']}",
        )
        jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1", tenant["organization"]
        )

    # The signature is the NORMAL one: a row of `project_delegations` with its digest, filed under
    # the organization's operator, in REVIEW, for the account of this draft and nothing else.
    scope = _j(delegation["scope"])
    budget = _j(delegation["budget"])
    assert delegation["mode"] == "REVIEW" and delegation["revoked_at"] is None
    assert delegation["signed_by"] == tenant["operator"]
    assert scope == {
        "accounts": [str(world.account_id)],
        "communities": ["r/SaaS"],
        "actions": ["REPLY"],
    }
    assert budget == {"publications_per_hour": 1, "publications_per_day": 6}
    assert abs(delegation["expires_at"] - (datetime.now(UTC) + timedelta(days=7))) < timedelta(
        minutes=1
    ), "domyślny termin formularza"
    assert delegation["scope_sha256"] == digest(
        mode="REVIEW",
        scope=Scope(accounts=(world.account_id,), communities=("r/SaaS",), actions=("REPLY",)),
        budget=Budget(publications_per_hour=1, publications_per_day=6),
        expires_at=delegation["expires_at"],
    ), "podpis z karty ma ten sam odcisk, co podpis z formularza"
    assert [(a["kind"], a["actor_ref"]) for a in audit] == [
        ("PROJECT_DELEGATION_SIGNED", "plugin:codex")
    ]

    # The send is the dashboard's approval: the consent points at the digest the person saw, and
    # the request waits in the publisher's queue, un-attempted. Nothing was published.
    assert revision["state"] == "APPROVED"
    assert decision["approved_payload_hash"] == revision["payload_hash"] == draft["odcisk"]
    assert decision["approved_by"] == tenant["operator"]
    assert request["status"] == "QUEUED" and request["attempt_id"] is None
    assert request["delegation_id"] == delegation["id"]
    assert request["consent_source"] == "zgoda człowieka kliknięciem w karcie agenta (codex)"
    assert str(request["id"]) == sent["wniosek"]
    assert jobs >= 1, "publikator dostał jedno szturchnięcie"

    queued = await world.registry.call("room.send_status", {"request_id": sent["wniosek"]})
    assert queued["wniosek"]["status"] == "QUEUED" and queued["wniosek"]["adres"] is None


async def test_a_published_request_gives_the_card_the_live_address_and_only_then(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    sent = await _click(world, draft)
    request_id = uuid.UUID(sent["wniosek"])

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status = 'PUBLISHING' WHERE id = $1", request_id
        )
    before = await world.registry.call("room.send_status", {"request_id": sent["wniosek"]})
    assert before["wniosek"]["status"] == "PUBLISHING" and before["wniosek"]["adres"] is None

    await _mark_published(pool, tenant, request_id)
    after = await world.registry.call("room.send_status", {"request_id": sent["wniosek"]})
    assert after["wniosek"]["status"] == "PUBLISHED"
    assert after["wniosek"]["adres"] == "https://www.reddit.com/r/SaaS/comments/abc123/x/ours01/"

    other = await _world(pool, tenant)
    foreign = await other.registry.call("room.send_status", {"request_id": sent["wniosek"]})
    assert foreign["stan"] == "NIEDOSTEPNE", "wniosek innego projektu jest nieznaleziony"


async def test_a_later_click_inside_the_signed_scope_signs_nothing_new(pool, tenant) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    draft = await _drafted(world)
    minted, token = await _token(world, draft)
    assert minted["zakres"]["rodzaj"] == "WYSLIJ"

    sent = await _send(world, draft, token)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is False
    counts = await _counts(pool, world)
    assert counts["delegations"] == 1 and counts["requests"] == 1
    async with pool.acquire() as conn:
        request = await conn.fetchrow(
            "SELECT delegation_id FROM publication_requests WHERE project_id = $1",
            world.project_id,
        )
    assert request["delegation_id"] == signed.id


async def test_an_account_the_review_scope_lacks_is_added_by_a_new_signature(pool, tenant) -> None:
    world = await _world(pool, tenant)
    other_account, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    old = await _sign(pool, tenant, world, accounts=(other_account,))
    draft = await _drafted(world)

    minted, token = await _token(world, draft)
    assert minted["zakres"]["rodzaj"] == "ROZSZERZ"
    sent = await _send(world, draft, token)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, revoked_at, scope, budget, expires_at FROM project_delegations "
            "WHERE project_id = $1 ORDER BY signed_at",
            world.project_id,
        )
    assert rows[0]["id"] == old.id and rows[0]["revoked_at"] is not None
    assert rows[1]["revoked_at"] is None
    assert set(_j(rows[1]["scope"])["accounts"]) == {
        str(world.account_id),
        str(other_account),
    }
    assert _j(rows[1]["budget"]) == {"publications_per_hour": 6, "publications_per_day": 6}
    assert rows[1]["expires_at"] == old.expires_at, "poszerzenie nie przedłuża terminu"


# ---------------------------------------------------------------------------
# A click that was not a click signs nothing and queues nothing
# ---------------------------------------------------------------------------


async def _assert_nothing_happened(pool, world, draft, before) -> None:
    assert await _counts(pool, world) == before, "odmowa coś zapisała"
    assert await _state(pool, draft["revision"]) == "READY_FOR_APPROVAL"


async def test_a_send_without_a_token_signs_nothing_and_queues_nothing(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    before = await _counts(pool, world)

    for args in (
        {"revision_id": draft["revision"], "token": ""},
        {"revision_id": draft["revision"], "token": "zgadnięte-przez-model"},
        {"revision_id": draft["revision"]},
    ):
        refused = await world.registry.call("room.send_draft", args)
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "POTWIERDZENIE", refused
    await _assert_nothing_happened(pool, world, draft, before)
    assert before["delegations"] == 0, "nie ma drogi do podpisu bez potwierdzenia"


async def test_a_spent_a_wrong_or_an_expired_token_is_refused(pool, tenant) -> None:
    world = await _world(pool, tenant)
    first, second = await _drafted(world), await _drafted(world, post="def456", text="Druga.")
    before = await _counts(pool, world)

    # A token minted for another draft is refused, and burned by being shown for the wrong one.
    _, token_a = await _token(world, first)
    wrong = await _send(world, second, token_a)
    assert wrong["stan"] == "NIEDOSTEPNE" and "innego szkicu" in wrong["powod"]
    again = await _send(world, first, token_a)
    assert again["stan"] == "NIEDOSTEPNE", "pokazany nie temu szkicowi, spalony"
    await _assert_nothing_happened(pool, world, first, before)
    await _assert_nothing_happened(pool, world, second, before)

    # An expired one.
    clock = [datetime.now(UTC)]
    world.registry._send_tokens = SendTokens(clock=lambda: clock[0])
    _, token_b = await _token(world, first)
    clock[0] += timedelta(minutes=4)
    late = await _send(world, first, token_b)
    assert late["stan"] == "NIEDOSTEPNE" and "wygasło" in late["powod"]
    await _assert_nothing_happened(pool, world, first, before)

    # A spent one: the first send works, the replay is refused and queues nothing more.
    world.registry._send_tokens = SendTokens()
    _, token_c = await _token(world, first)
    assert (await _send(world, first, token_c))["stan"] == "OCZEKUJE"
    after = await _counts(pool, world)
    replay = await _send(world, first, token_c)
    assert replay["stan"] != "OCZEKUJE" or replay.get("powtorzone") is True
    assert await _counts(pool, world) == after, "powtórka nic nie dopisała"
    assert after["delegations"] == 1 and after["requests"] == 1


async def test_a_new_confirmation_for_the_same_draft_kills_the_old_one(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    before = await _counts(pool, world)
    _, old = await _token(world, draft)
    _, new = await _token(world, draft)
    assert (await _send(world, draft, old))["stan"] == "NIEDOSTEPNE"
    await _assert_nothing_happened(pool, world, draft, before)
    assert (await _send(world, draft, new))["stan"] == "OCZEKUJE"


async def test_a_confirmation_needs_the_digest_the_person_saw(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    before = await _counts(pool, world)
    for shown in ("b" * 64, "nie-odcisk", ""):
        refused = await world.registry.call(
            "room.send_token", {"revision_id": draft["revision"], "odcisk": shown}
        )
        if refused.get("stan") == "BLAD":  # not even a digest: refused by the schema check
            continue
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "ODCISK"
        assert "_mcp_meta" not in refused, "odmowa nie wydaje potwierdzenia"
    await _assert_nothing_happened(pool, world, draft, before)


# ---------------------------------------------------------------------------
# A changed world refuses
# ---------------------------------------------------------------------------


async def test_a_draft_rejected_after_the_confirmation_is_not_sent_and_signs_nothing(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    _, token = await _token(world, draft)
    await ApprovalStore(pool).move_revision(
        uuid.UUID(draft["revision"]),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        target=ActionProposalState.REJECTED,
        actor_ref=f"operator:{tenant['operator']}",
    )
    refused = await _send(world, draft, token)
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "STAN"
    counts = await _counts(pool, world)
    assert counts == {"delegations": 0, "requests": 0, "decisions": 0}


async def test_a_scope_revoked_after_the_confirmation_is_a_different_sentence(pool, tenant) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    draft = await _drafted(world)
    minted, token = await _token(world, draft)
    assert minted["zakres"]["rodzaj"] == "WYSLIJ"

    await PresenceStore(pool).revoke(
        signed.id, organization_id=tenant["organization"], at=datetime.now(UTC)
    )
    refused = await _send(world, draft, token)
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "ZAKRES_ZMIENIONY"
    counts = await _counts(pool, world)
    assert counts["requests"] == 0 and counts["delegations"] == 1, "nie podpisano nowego zakresu"


async def test_a_pause_is_a_stop_a_click_does_not_lift(pool, tenant) -> None:
    world = await _world(pool, tenant)
    signed = await _sign(pool, tenant, world)
    await PresenceStore(pool).set_paused(
        signed.id, organization_id=tenant["organization"], paused=True, at=datetime.now(UTC)
    )
    draft = await _drafted(world)
    before = await _counts(pool, world)
    minted, token = await _token(world, draft)
    assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "ZAKRES_WSTRZYMANY"
    assert token is None and "obecnosc_adres" in minted
    await _assert_nothing_happened(pool, world, draft, before)


async def test_a_scope_is_not_replaced_under_a_request_still_waiting_for_the_publisher(
    pool, tenant
) -> None:
    """The publisher stops a request whose signature was replaced ("nowy podpis nie dziedziczy
    starych zgód"), so widening is refused while one waits, and nothing is signed. Once the request
    has left the queue, the same click widens."""
    world = await _world(pool, tenant)
    account_b, login_b = await _account(pool, tenant, world.project_id, "REDDIT")
    old = await _sign(pool, tenant, world, accounts=(account_b,))

    # A request of account B waits in the queue under the old signature.
    marked_b = await _mark(world, **_reddit_args(post="bbb111"))
    drafted_b = await _draft(world, marked_b["okazja"]["url"], "Dla konta B.", account=login_b)
    draft_b = {
        "revision": drafted_b["propozycja"]["revision_id"],
        "odcisk": drafted_b["propozycja"]["payload_hash"],
    }
    assert (await _click(world, draft_b))["stan"] == "OCZEKUJE"

    # A draft of this project's own account is outside the scope: widening would close `old`.
    draft_a = await _drafted(world, post="aaa222", text="Dla konta A.")
    before = await _counts(pool, world)
    minted, token = await _token(world, draft_a)
    assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "ZAKRES_KOLEJKA" and token is None
    assert "czeka w kolejce" in minted["powod"]
    await _assert_nothing_happened(pool, world, draft_a, before)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status = 'SKIPPED', detail = 'test' "
            "WHERE project_id = $1",
            world.project_id,
        )
    minted, token = await _token(world, draft_a)
    assert minted["zakres"]["rodzaj"] == "ROZSZERZ" and token
    assert (await _send(world, draft_a, token))["stan"] == "OCZEKUJE"
    async with pool.acquire() as conn:
        closed = await conn.fetchval(
            "SELECT revoked_at FROM project_delegations WHERE id = $1", old.id
        )
    assert closed is not None


@pytest.mark.parametrize("mode", ["AUTONOMOUS", "RESEARCH"])
async def test_a_click_does_not_widen_a_scope_that_is_not_review(pool, tenant, mode) -> None:
    world = await _world(pool, tenant)
    other_account, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    # The draft first: `draft.create` itself refuses in RESEARCH, so a draft waiting under such a
    # scope is one written before the person changed the mode.
    draft = await _drafted(world)
    await _sign(pool, tenant, world, mode=mode, accounts=(other_account,))
    before = await _counts(pool, world)
    minted, token = await _token(world, draft)
    assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "ZAKRES_INNY_TRYB" and token is None
    await _assert_nothing_happened(pool, world, draft, before)


async def test_an_account_no_longer_assigned_is_not_found(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_account_assignments SET unassigned_at = now() "
            "WHERE project_id = $1 AND social_account_id = $2",
            world.project_id,
            world.account_id,
        )
    minted, token = await _token(world, draft)
    assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "NIE_ZNALEZIONO" and token is None


async def test_the_pace_the_signature_binds_still_binds(pool, tenant) -> None:
    """The minimal scope allows one publication an hour. A second click is refused by the same
    budget check as the dashboard's, and the sentence says so."""
    world = await _world(pool, tenant)
    first = await _drafted(world)
    second = await _drafted(world, post="def456", text="Druga odpowiedź.")
    assert (await _click(world, first))["stan"] == "OCZEKUJE"

    minted, token = await _token(world, second)
    assert minted["zakres"]["rodzaj"] == "WYSLIJ"
    refused = await _send(world, second, token)
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "BUDZET"
    assert refused["powod"].startswith("budżet wyczerpany")
    assert (await _counts(pool, world))["requests"] == 1
    assert await _state(pool, second["revision"]) == "READY_FOR_APPROVAL"


# ---------------------------------------------------------------------------
# The room carries what the card needs, and nothing the card must not show
# ---------------------------------------------------------------------------


async def test_the_room_gives_the_card_each_digest_and_the_presence_address(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_DASHBOARD_URL", "http://127.0.0.1:3999")
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    shown = await world.registry.call("room.show", {})
    (one,) = shown["szkice"]
    assert one["revision_id"] == draft["revision"] and one["odcisk"] == draft["odcisk"]
    assert shown["obecnosc"] == f"/w/{tenant['workspace']}/p/{world.project_id}/presence"
    assert shown["obecnosc_adres"] == f"http://127.0.0.1:3999{shown['obecnosc']}"
    assert "_mcp_meta" not in shown and "wenext/potwierdzenie" not in json.dumps(shown)


# ---------------------------------------------------------------------------
# One consent covers the project's accounts (11.10), and the server names them
# ---------------------------------------------------------------------------


async def _busy_project(pool, tenant) -> tuple[World, dict]:
    """A project with a Reddit account (the world's) and an X account, plus accounts the click must
    NOT sign: Hacker News and LinkedIn (the matrix says no), a paused Reddit account (a stop), and
    an account of ANOTHER project. Returns the world and the ids by role."""
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    hn_id, _ = await _account(pool, tenant, world.project_id, "HACKER_NEWS")
    li_id, _ = await _account(pool, tenant, world.project_id, "LINKEDIN")
    paused_id, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = 'PAUSED' WHERE id = $1", paused_id)
    foreign = await _world(pool, tenant)
    return world, {
        "reddit": world.account_id,
        "x": x_id,
        "x_login": x_login,
        "hn": hn_id,
        "linkedin": li_id,
        "paused": paused_id,
        "foreign": foreign.account_id,
    }


async def test_one_click_signs_every_account_the_matrix_lets_write_and_nothing_the_card_adds(
    pool, tenant
) -> None:
    world, ids = await _busy_project(pool, tenant)
    draft = await _drafted(world)

    # The card (or a model that learned the tool's name) offers accounts, a scope, a mode: the tool
    # takes a revision and a digest, and reads everything else from the database.
    minted = await world.registry.call(
        "room.send_token",
        {
            "revision_id": draft["revision"],
            "odcisk": draft["odcisk"],
            "konta": [str(ids["foreign"]), str(ids["hn"])],
            "accounts": [str(ids["foreign"])],
            "account": str(ids["foreign"]),
            "scope": {"accounts": [str(ids["foreign"])], "actions": ["REPLY", "OWN_POST"]},
            "tryb": "AUTONOMOUS",
            "limity": {"godzina": 99, "doba": 99},
        },
    )
    token = (minted.get("_mcp_meta") or {}).get(META_TOKEN)
    assert minted["stan"] == "WYKONANE" and token, minted
    assert minted["zakres"]["rodzaj"] == "PODPISZ" and minted["zakres"]["tryb"] == "REVIEW"
    assert minted["zakres"]["konta"] == [
        {"platforma": "REDDIT", "konto": world.login},
        {"platforma": "X", "konto": ids["x_login"]},
    ], "sztywna kolejność: platforma, potem login"
    assert minted["zakres"]["limity"] == {"godzina": 1, "doba": 6}, "tempo nie wzrosło"
    assert (await _counts(pool, world))["delegations"] == 0, "pytanie niczego nie podpisało"

    sent = await world.registry.call(
        "room.send_draft",
        {
            "revision_id": draft["revision"],
            "token": token,
            "accounts": [str(ids["foreign"])],
            "konta": [str(ids["foreign"])],
        },
    )
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True, sent
    assert sent["zakres"]["konta"] == minted["zakres"]["konta"]

    # EXACTLY the eligible accounts of THIS project: Reddit and X, and none of the others.
    assert await _scope_accounts(pool, world) == {str(ids["reddit"]), str(ids["x"])}
    async with pool.acquire() as conn:
        delegation = await conn.fetchrow(
            "SELECT mode, scope, budget, scope_sha256, expires_at FROM project_delegations "
            "WHERE project_id = $1",
            world.project_id,
        )
    assert delegation["mode"] == "REVIEW"
    assert _j(delegation["budget"]) == {"publications_per_hour": 1, "publications_per_day": 6}
    assert delegation["scope_sha256"] == digest(
        mode="REVIEW",
        scope=Scope(
            accounts=tuple(sorted((ids["reddit"], ids["x"]), key=str)),
            communities=("r/SaaS",),
            actions=("REPLY",),
        ),
        budget=Budget(publications_per_hour=1, publications_per_day=6),
        expires_at=delegation["expires_at"],
    ), "ten sam odcisk, co z formularza"


async def test_the_consent_given_on_one_account_is_enough_for_the_others(pool, tenant) -> None:
    """The founder's session, replayed: X first, then the Reddit draft of the same project. The
    second click is a plain send, in the scope the first one signed, and signs nothing."""
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    x_draft = await _drafted_x(world, x_login)
    reddit_draft = await _drafted(world)

    first = await _click(world, x_draft)
    assert first["stan"] == "OCZEKUJE" and first["podpisano"] is True
    assert await _scope_accounts(pool, world) == {str(world.account_id), str(x_id)}

    # The publisher has not taken the X request yet: this is the moment v5 refused the Reddit draft.
    minted, token = await _token(world, reddit_draft)
    assert minted["stan"] == "WYKONANE" and token, minted
    assert minted["zakres"]["rodzaj"] == "WYSLIJ" and minted["zakres"]["konta"] == []

    await _let_the_queue_go(pool, world)  # the pace of an hour is not what this test is about
    sent = await _send(world, reddit_draft, token)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is False, sent
    counts = await _counts(pool, world)
    assert counts["delegations"] == 1 and counts["requests"] == 2


async def test_an_account_added_to_the_project_later_is_widened_in_by_its_own_click(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    assert (await _click(world, await _drafted_x(world, x_login)))["podpisano"] is True
    await _let_the_queue_go(pool, world)

    later_id, later_login = await _account(pool, tenant, world.project_id, "REDDIT")
    marked = await _mark(world, **_reddit_args(post="later1"))
    drafted = await _draft(world, marked["okazja"]["url"], "Z nowego konta.", account=later_login)
    draft = {
        "revision": drafted["propozycja"]["revision_id"],
        "odcisk": drafted["propozycja"]["payload_hash"],
    }
    minted, token = await _token(world, draft)
    assert minted["zakres"]["rodzaj"] == "ROZSZERZ" and token
    assert minted["zakres"]["konta"] == [{"platforma": "REDDIT", "konto": later_login}], (
        "rozszerzenie nazywa tylko to, co dodaje"
    )
    sent = await _send(world, draft, token)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True
    assert await _scope_accounts(pool, world) == {
        str(world.account_id),
        str(x_id),
        str(later_id),
    }


async def test_an_account_assigned_between_the_question_and_the_yes_is_not_signed_in_unseen(
    pool, tenant
) -> None:
    """The token binds the accounts the person was shown. A new one turns the send into
    ZAKRES_ZMIENIONY (nothing signed, nothing queued), and asking again names it."""
    world = await _world(pool, tenant)
    x_id, _ = await _account(pool, tenant, world.project_id, "X")
    draft = await _drafted(world)
    minted, token = await _token(world, draft)
    assert len(minted["zakres"]["konta"]) == 2

    new_id, new_login = await _account(pool, tenant, world.project_id, "REDDIT")
    before = await _counts(pool, world)
    refused = await _send(world, draft, token)
    assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "ZAKRES_ZMIENIONY", refused
    await _assert_nothing_happened(pool, world, draft, before)

    again, token = await _token(world, draft)
    assert {a["konto"] for a in again["zakres"]["konta"]} >= {new_login}
    assert (await _send(world, draft, token))["stan"] == "OCZEKUJE"
    assert await _scope_accounts(pool, world) == {
        str(world.account_id),
        str(x_id),
        str(new_id),
    }


# ---------------------------------------------------------------------------
# A refusal that passes clears by asking again (11.10); the send stays the person's click
# ---------------------------------------------------------------------------


async def test_the_busy_queue_refusal_clears_when_the_request_leaves_the_queue(
    pool, tenant
) -> None:
    """What the card does for ZAKRES_KOLEJKA: ask `room.send_token` again until it answers with a
    confirmation. Asking signs nothing and sends nothing; only the click that follows does."""
    world = await _world(pool, tenant)
    account_b, login_b = await _account(pool, tenant, world.project_id, "REDDIT")
    old = await _sign(pool, tenant, world, accounts=(account_b,))
    marked_b = await _mark(world, **_reddit_args(post="bbb111"))
    drafted_b = await _draft(world, marked_b["okazja"]["url"], "Dla konta B.", account=login_b)
    queued_b = {
        "revision": drafted_b["propozycja"]["revision_id"],
        "odcisk": drafted_b["propozycja"]["payload_hash"],
    }
    assert (await _click(world, queued_b))["stan"] == "OCZEKUJE"  # B's request waits in the queue
    draft_a = await _drafted(world, post="aaa222", text="Dla konta A.")
    before = await _counts(pool, world)

    for _poll in range(3):  # the card asks again every few seconds
        minted, token = await _token(world, draft_a)
        assert minted["stan"] == "NIEDOSTEPNE" and minted["kod"] == "ZAKRES_KOLEJKA" and not token
        assert await _counts(pool, world) == before, "pytanie nic nie zmieniło"

    await _mark_published(pool, tenant, uuid.UUID((await _requests(pool, world))[0]))
    minted, token = await _token(world, draft_a)
    assert minted["stan"] == "WYKONANE" and token, minted
    assert minted["zakres"]["rodzaj"] == "ROZSZERZ"
    assert (await _counts(pool, world)) == before, (
        "potwierdzenie czeka na kliknięcie: nic nie wyszło"
    )
    assert await _state(pool, draft_a["revision"]) == "READY_FOR_APPROVAL"

    sent = await _send(world, draft_a, token)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True
    async with pool.acquire() as conn:
        closed = await conn.fetchval(
            "SELECT revoked_at FROM project_delegations WHERE id = $1", old.id
        )
    assert closed is not None


async def _requests(pool, world: World) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM publication_requests WHERE project_id = $1 ORDER BY queued_at",
            world.project_id,
        )
    return [str(row["id"]) for row in rows]


async def test_four_drafts_clicked_quickly_each_heal_into_one_consent_and_sends_on_a_click(
    pool, tenant
) -> None:
    """The founder's session: four tokens asked for before any signature, one confirmed. The three
    others find the scope moved (ZAKRES_ZMIENIONY), the card asks again, and each is then a plain
    send in the scope the first click signed. No second consent, and nothing sent unclicked."""
    world = await _world(pool, tenant)
    x_id, x_login = await _account(pool, tenant, world.project_id, "X")
    drafts = [
        await _drafted_x(world, x_login),
        await _drafted(world, post="r1", text="Pierwsza."),
        await _drafted(world, post="r2", text="Druga."),
        await _drafted(world, post="r3", text="Trzecia."),
    ]
    tokens = []
    for draft in drafts:  # "Wyślij" on all four, before any "Tak"
        minted, token = await _token(world, draft)
        assert minted["zakres"]["rodzaj"] == "PODPISZ" and token
        tokens.append(token)

    assert (await _send(world, drafts[0], tokens[0]))["stan"] == "OCZEKUJE"
    assert await _scope_accounts(pool, world) == {str(world.account_id), str(x_id)}
    await _let_the_queue_go(pool, world)

    for draft, token in zip(drafts[1:], tokens[1:], strict=True):
        before = await _counts(pool, world)
        stale = await _send(world, draft, token)
        assert stale["stan"] == "NIEDOSTEPNE" and stale["kod"] == "ZAKRES_ZMIENIONY", stale
        assert await _counts(pool, world) == before, "odmowa nic nie zapisała"
        # The card asks again, shows the new sentence (a plain send), and waits for the click.
        minted, fresh = await _token(world, draft)
        assert minted["zakres"]["rodzaj"] == "WYSLIJ" and fresh and fresh != token
        assert await _counts(pool, world) == before
        assert (await _send(world, draft, fresh))["stan"] == "OCZEKUJE"
        await _let_the_queue_go(pool, world)  # one an hour: let the pace through for the test
    counts = await _counts(pool, world)
    assert counts["delegations"] == 1 and counts["requests"] == 4, counts


async def test_an_expired_or_spent_confirmation_is_replaced_by_asking_again(pool, tenant) -> None:
    world = await _world(pool, tenant)
    draft = await _drafted(world)
    clock = [datetime.now(UTC)]
    world.registry._send_tokens = SendTokens(clock=lambda: clock[0])
    _, token = await _token(world, draft)
    clock[0] += timedelta(minutes=4)
    before = await _counts(pool, world)
    late = await _send(world, draft, token)
    assert late["stan"] == "NIEDOSTEPNE" and late["kod"] == "POTWIERDZENIE"
    assert await _counts(pool, world) == before

    minted, fresh = await _token(world, draft)
    assert minted["stan"] == "WYKONANE" and fresh and fresh != token
    spent = await _send(world, draft, token)  # the old one is burned, shown or not
    assert spent["kod"] == "POTWIERDZENIE"
    assert (await _send(world, draft, fresh))["stan"] == "OCZEKUJE"
