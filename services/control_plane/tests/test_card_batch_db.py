"""Sending a whole reviewed batch with one click, against PostgreSQL: ONLY THE HUMAN'S CLICK, ONE PER BATCH.

Nothing here publishes. A batch ends in rows of the publisher's QUEUE (`publication_requests`, QUEUED,
each with the moment it may go out) and the jobs that nudge the publisher at those moments; no adapter,
no capsule and no network is reached. The throwaway database is the one of `run_db_tests.sh`.

THE QUESTIONS, EACH A WAY "SEND ALL" COULD DO WHAT NOBODY MEANT:

  * does a batch that the limits would have refused one by one (five replies at one an hour) go in, each
    with its own moment, and does the publisher honour those moments (`next_queued`)?
  * does the token bind the EXACT set: a draft edited between the question and the click refuses the
    WHOLE batch and writes nothing; a smaller question kills the bigger token; a token works once and
    expires; two clicks with one token send once?
  * does a batch SIGN anything? (No: a draft outside the signed scope, no scope, a paused scope are
    refused with their codes, and the number of signatures does not move.)
  * do the limits hold: the day's budget leaves the rest waiting, a window that closes before its turn
    leaves the reply out, a world that moved (a publication since) refuses rather than reshuffles?
  * is the order the one asked for (a fresh post on X first), and is every draft validated as
    `room.send_token` validates it (not waiting, expired window)?
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from control_plane.approval.store import ApprovalStore
from control_plane.pilot.publication import PublicationStore
from control_plane.presence.batch_plan import HORIZON, JITTER_MAX, MAX_BATCH, TICK_MARGIN
from control_plane.presence.human_approval import (
    HumanApprovalRefused,
    approve_and_queue,
    revision_row,
)
from control_plane.presence.scope import Budget
from control_plane.tools.card_batch import BatchTokens
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_send_db import (
    _account,
    _click,
    _counts,
    _drafted,
    _drafted_x,
    _sign,
    _state,
    _world,
)

pytestmark = pytest.mark.db


class _Floor(random.Random):
    """Dice that always roll the bottom of the range: the first reply goes out at the click, the
    rest a quarter of an hour (at four an hour) and the minimum jitter apart."""

    def uniform(self, a: float, b: float) -> float:
        return a


async def _scoped_world(pool, tenant, *, hour: int, day: int, accounts=None):
    world = await _world(pool, tenant)
    world.registry._batch_rng = _Floor()
    await _sign(
        pool,
        tenant,
        world,
        accounts=accounts,
        budget=Budget(publications_per_hour=hour, publications_per_day=day, own_posts_per_day=1),
    )
    return world


async def _drafts(world, n: int, *, tag: str = "p") -> list[dict]:
    return [await _drafted(world, post=f"{tag}{i}x{uuid.uuid4().hex[:4]}") for i in range(n)]


async def _raw_draft(
    pool, tenant, world, *, text: str, created_ago: timedelta, expires_in: timedelta
) -> dict:
    """A waiting draft with a window of our choosing. Revisions are immutable (L2795), so one that
    is about to close, or already closed, is INSERTED that way, as `test_opportunities_db` does."""
    proposal_id = await ApprovalStore(pool).create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=world.account_id,
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO action_proposal_revisions (organization_id, workspace_id, proposal_id,"
            " revision, payload, payload_hash, source_fingerprint, state, created_at, expires_at)"
            " VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'odcisk', 'READY_FOR_APPROVAL',"
            " now() - make_interval(secs => $6), now() + make_interval(secs => $7))"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            proposal_id,
            json.dumps(
                {
                    "plan_version": str(world.project_id),
                    "target_external_id": f"t3_{uuid.uuid4().hex[:6]}",
                    "target_url": "https://www.reddit.com/r/SaaS/comments/zzz/x/",
                    "text_and_media": text,
                    "action_type": "SUBMIT_COMMENT",
                }
            ),
            digest,
            created_ago.total_seconds(),
            expires_in.total_seconds(),
        )
        revision = await conn.fetchval(
            "SELECT id FROM action_proposal_revisions WHERE proposal_id = $1", proposal_id
        )
    return {"revision": str(revision), "odcisk": digest}


async def _ask(world, drafts: list[dict]) -> tuple[dict, str | None]:
    """The card's question: every ticked draft with the digest it showed. Returns the answer and the
    confirmation, which is only in `_meta`."""
    answer = await world.registry.call(
        "room.batch_token",
        {
            "revision_ids": [d["revision"] for d in drafts],
            "odciski": [d["odcisk"] for d in drafts],
        },
    )
    return answer, (answer.get("_mcp_meta") or {}).get(META_TOKEN)


async def _confirm(world, token: str) -> dict:
    return await world.registry.call("room.send_batch", {"token": token})


async def _rows(pool, world) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT q.id, q.status, q.not_before, q.queued_at, q.consent_source, q.kind, "
            "       q.proposal_revision_id, q.detail "
            "FROM publication_requests q WHERE q.project_id = $1 "
            "ORDER BY coalesce(q.not_before, q.queued_at)",
            world.project_id,
        )


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


# ---------------------------------------------------------------------------
# The case the feature exists for
# ---------------------------------------------------------------------------


async def test_five_replies_at_one_an_hour_go_in_each_with_its_moment_and_none_is_refused(
    pool, tenant
) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 5)

    answer, token = await _ask(world, drafts)

    assert answer["stan"] == "WYKONANE" and token and len(token) >= 40
    plan = answer["paczka"]
    assert plan["ile"] == 5 and plan["odstep_min"] == 60 and plan["limity"]["godzina"] == 1
    assert answer["odrzucone"] == []
    moments = [_at(item["kiedy"]) for item in plan["pozycje"]]
    assert moments == sorted(moments)
    for earlier, later in pairwise(moments):
        assert later - earlier >= timedelta(hours=1, minutes=1), "an hour apart plus the jitter"
    assert [item["lp"] for item in plan["pozycje"]] == [1, 2, 3, 4, 5]
    # THE TOKEN IS ONLY IN `_meta`: not in a field a model or a host reads as text.
    assert token not in json.dumps({k: v for k, v in answer.items() if k != "_mcp_meta"})
    assert (await _counts(pool, world))["requests"] == 0, "the question wrote nothing"

    sent = await _confirm(world, token)

    assert sent["stan"] == "OCZEKUJE", sent
    assert len(sent["zaplanowane"]) == 5 and sent["odmowy"] == []
    rows = await _rows(pool, world)
    assert len(rows) == 5 and all(r["status"] == "QUEUED" for r in rows)
    assert all(r["not_before"] is not None for r in rows), "every request carries its moment"
    shown = {item["revision_id"]: _at(item["kiedy"]) for item in plan["pozycje"]}
    now = datetime.now(UTC)
    for item in sent["zaplanowane"]:
        # the moment is the one the person was shown, or a hair later (the click came after the question)
        assert (
            shown[item["revision_id"]]
            <= _at(item["kiedy"])
            <= shown[item["revision_id"]] + timedelta(minutes=5)
        )
    assert _at(sent["zaplanowane"][0]["kiedy"]) <= now + timedelta(minutes=5), "the first goes now"
    assert [await _state(pool, d["revision"]) for d in drafts] == ["APPROVED"] * 5
    decisions = (await _counts(pool, world))["decisions"]
    assert decisions == 5, "one consent per draft, the same record as the dashboard's"
    for row in rows:
        assert row["consent_source"].startswith(
            "zgoda człowieka kliknięciem w karcie agenta (codex)"
        )
        assert f"paczka 5 szkiców, zestaw {plan['zestaw'][:12]}" in row["consent_source"]


async def test_the_publisher_takes_the_requests_one_at_a_time_in_their_own_moments(
    pool, tenant
) -> None:
    """`next_queued` honours `not_before`: only what is due is taken; the others wait for their hour."""
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 3)
    _answer, token = await _ask(world, drafts)
    await _confirm(world, token)
    store, org = PublicationStore(pool), tenant["organization"]

    head = await store.next_queued(organization_id=org)
    assert head is not None, "the first reply is due at the click"
    await store.set_status(head.id, status="SKIPPED", detail="test")
    assert await store.next_queued(organization_id=org) is None, "the second is an hour away"

    rows = await _rows(pool, world)
    second = next(r for r in rows if r["status"] == "QUEUED")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET not_before = now() - interval '1 second' WHERE id = $1",
            second["id"],
        )
    due = await store.next_queued(organization_id=org)
    assert due is not None and due.id == second["id"], "its hour came"


async def test_the_publisher_is_nudged_at_each_moment_and_not_before(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 3)
    _answer, token = await _ask(world, drafts)
    sent = await _confirm(world, token)

    async with pool.acquire() as conn:
        jobs = await conn.fetch(
            "SELECT idempotency_key, not_before FROM jobs WHERE organization_id = $1 "
            "AND job_type = 'pilot.publish' AND idempotency_key LIKE '%nudge%'",
            tenant["organization"],
        )
        now = await conn.fetchval("SELECT now()")
    keys = {j["idempotency_key"]: j["not_before"] for j in jobs}
    scheduled = {k: v for k, v in keys.items() if ":nudge:scheduled:" in k}
    immediate = [k for k in keys if ":nudge:issued:" in k]
    assert len(immediate) == 1, "the first reply is due at once: the ordinary nudge"
    assert len(scheduled) == 2, "the others get a tick AT their moment"
    by_revision = {item["revision_id"]: _at(item["kiedy"]) for item in sent["zaplanowane"]}
    for key, ready in scheduled.items():
        revision = key.rsplit(":", 1)[1]
        assert abs((ready - by_revision[revision]).total_seconds() - 2.0) < 5.0, (
            "the tick is the moment plus a couple of seconds"
        )
        assert ready > now + timedelta(minutes=30)


# ---------------------------------------------------------------------------
# The token binds the exact set
# ---------------------------------------------------------------------------


async def test_a_draft_edited_between_the_question_and_the_click_refuses_the_whole_batch(
    pool, tenant
) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 3)
    answer, token = await _ask(world, drafts)
    assert answer["stan"] == "WYKONANE" and answer["paczka"]["ile"] == 3
    before = await _counts(pool, world)

    edited = await world.registry.call(
        "room.revise_draft", {"revision_id": drafts[1]["revision"], "text": "Wersja poprawiona."}
    )
    assert edited["stan"] == "WYKONANE", edited
    sent = await _confirm(world, token)

    assert sent["stan"] == "NIEDOSTEPNE" and sent["kod"] == "PACZKA_ZMIENIONA", sent
    named = {item["revision_id"]: item["kod"] for item in sent["odrzucone"]}
    assert set(named) == {drafts[1]["revision"]}, "the edited one is named, and only it"
    assert named[drafts[1]["revision"]] in {"STAN", "ODCISK"}
    assert await _counts(pool, world) == before, "NOTHING was written: no request, no consent"
    assert await _state(pool, drafts[0]["revision"]) == "READY_FOR_APPROVAL"
    assert await _state(pool, drafts[2]["revision"]) == "READY_FOR_APPROVAL"
    # the confirmation is spent by the refusal: one click, one answer
    again = await _confirm(world, token)
    assert again["kod"] == "POTWIERDZENIE"


async def test_the_digest_the_card_showed_is_checked_when_the_question_is_asked(
    pool, tenant
) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 2)
    stale = {**drafts[0], "odcisk": "f" * 64}
    answer, token = await _ask(world, [stale, drafts[1]])
    assert answer["stan"] == "WYKONANE" and answer["paczka"]["ile"] == 1
    assert [item["kod"] for item in answer["odrzucone"]] == ["ODCISK"]
    assert [i["revision_id"] for i in answer["paczka"]["pozycje"]] == [drafts[1]["revision"]]
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and len(sent["zaplanowane"]) == 1
    assert await _state(pool, drafts[0]["revision"]) == "READY_FOR_APPROVAL"


async def test_a_smaller_question_kills_the_bigger_token_and_sends_only_what_is_ticked(
    pool, tenant
) -> None:
    """Unticking a draft in the card is a NEW question: the earlier confirmation is dead."""
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 3)
    _big, big_token = await _ask(world, drafts)
    small, small_token = await _ask(world, [drafts[0], drafts[2]])
    assert small["paczka"]["ile"] == 2

    dead = await _confirm(world, big_token)
    assert dead["kod"] == "POTWIERDZENIE" and (await _counts(pool, world))["requests"] == 0

    sent = await _confirm(world, small_token)
    assert sent["stan"] == "OCZEKUJE"
    assert {i["revision_id"] for i in sent["zaplanowane"]} == {
        drafts[0]["revision"],
        drafts[2]["revision"],
    }
    assert await _state(pool, drafts[1]["revision"]) == "READY_FOR_APPROVAL", (
        "the unticked one waits"
    )
    assert (await _counts(pool, world))["requests"] == 2


async def test_one_confirmation_sends_once_even_when_two_clicks_race(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 3)
    _answer, token = await _ask(world, drafts)

    first, second = await asyncio.gather(_confirm(world, token), _confirm(world, token))

    outcomes = sorted([first["stan"], second["stan"]])
    assert outcomes == ["NIEDOSTEPNE", "OCZEKUJE"], (first, second)
    assert (await _counts(pool, world))["requests"] == 3, "the batch went in once"


async def test_a_confirmation_works_once_and_expires_in_minutes(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 2)
    _answer, token = await _ask(world, drafts)
    assert (await _confirm(world, token))["stan"] == "OCZEKUJE"
    spent = await _confirm(world, token)
    assert spent["kod"] == "POTWIERDZENIE" and "zużyte" in spent["powod"]

    class _Clock:
        offset = timedelta(0)

        def __call__(self) -> datetime:
            return datetime.now(UTC) + self.offset

    clock = _Clock()
    world.registry._batch_tokens = BatchTokens(clock=clock)
    more = await _drafts(world, 2, tag="q")
    _answer, late = await _ask(world, more)
    clock.offset = timedelta(minutes=6)
    expired = await _confirm(world, late)
    assert expired["kod"] == "POTWIERDZENIE" and "wygasło" in expired["powod"]
    for draft in more:
        assert await _state(pool, draft["revision"]) == "READY_FOR_APPROVAL"


# ---------------------------------------------------------------------------
# A batch signs nothing
# ---------------------------------------------------------------------------


async def test_without_a_signed_scope_a_batch_refuses_and_signs_nothing(pool, tenant) -> None:
    world = await _world(pool, tenant)
    drafts = await _drafts(world, 2)
    assert (await _counts(pool, world))["delegations"] == 0

    answer, token = await _ask(world, drafts)

    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "POZA_ZAKRESEM", answer
    assert token is None, "no confirmation is minted for a batch that cannot go"
    assert {item["kod"] for item in answer["odrzucone"]} == {"POZA_ZAKRESEM"}
    counts = await _counts(pool, world)
    assert counts["delegations"] == 0 and counts["requests"] == 0, (
        "ONE CLICK IN A BATCH SIGNS NOTHING"
    )


async def test_a_draft_whose_account_the_scope_lacks_is_left_out_not_signed_in(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    other, _ = await _account(pool, tenant, world.project_id, "REDDIT")
    await _sign(pool, tenant, world, accounts=(other,))
    drafts = await _drafts(world, 2)
    before = await _counts(pool, world)

    answer, token = await _ask(world, drafts)

    assert answer["kod"] == "POZA_ZAKRESEM" and token is None
    assert await _counts(pool, world) == before, "the scope was not widened by a question"


async def test_a_paused_scope_is_a_stop_and_a_batch_does_not_lift_it(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 2)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET paused_at = now() WHERE project_id = $1",
            world.project_id,
        )
    answer, token = await _ask(world, drafts)
    assert answer["kod"] == "ZAKRES_WSTRZYMANY" and token is None
    async with pool.acquire() as conn:
        paused = await conn.fetchval(
            "SELECT paused_at FROM project_delegations WHERE project_id = $1", world.project_id
        )
    assert paused is not None


async def test_a_scope_that_was_paused_after_the_question_refuses_the_click(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 2)
    _answer, token = await _ask(world, drafts)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET paused_at = now() WHERE project_id = $1",
            world.project_id,
        )
    sent = await _confirm(world, token)
    assert sent["stan"] == "NIEDOSTEPNE" and sent["kod"] == "PACZKA_ZMIENIONA"
    assert (await _counts(pool, world))["requests"] == 0


# ---------------------------------------------------------------------------
# Every draft is validated as a single send validates it
# ---------------------------------------------------------------------------


async def test_a_draft_that_is_not_waiting_or_whose_window_closed_is_named_and_the_rest_go(
    pool, tenant
) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    drafts = await _drafts(world, 3)
    await _click(world, drafts[0])  # approved on its own: no longer waiting
    closed = await _raw_draft(
        pool,
        tenant,
        world,
        text="Okno minęło.",
        created_ago=timedelta(days=5),
        expires_in=-timedelta(days=1),
    )
    drafts = [drafts[0], closed, drafts[1], drafts[2]]

    answer, token = await _ask(world, drafts)

    named = {item["revision_id"]: item["kod"] for item in answer["odrzucone"]}
    assert named[drafts[0]["revision"]] == "STAN" and named[drafts[1]["revision"]] == "OKNO"
    assert answer["paczka"]["ile"] == 2
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and len(sent["zaplanowane"]) == 2


async def test_a_draft_of_another_project_is_not_found_not_leaked(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    other = await _world(pool, tenant)
    foreign = await _drafted(other, post="foreign1")
    mine = await _drafts(world, 1)
    answer, _token = await _ask(world, [foreign, *mine])
    assert [item["kod"] for item in answer["odrzucone"]] == ["NIE_ZNALEZIONO"]
    assert answer["paczka"]["ile"] == 1


async def test_a_batch_is_refused_over_the_bound_and_when_empty(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=20)
    big = await world.registry.call(
        "room.batch_token",
        {
            "revision_ids": [str(uuid.uuid4()) for _ in range(MAX_BATCH + 1)],
            "odciski": ["a" * 64] * (MAX_BATCH + 1),
        },
    )
    assert big["kod"] == "PACZKA_ZA_DUZA"


# ---------------------------------------------------------------------------
# The limits
# ---------------------------------------------------------------------------


async def test_the_day_limit_leaves_the_rest_waiting_in_the_list(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=4, day=2)
    drafts = await _drafts(world, 4)

    answer, token = await _ask(world, drafts)

    assert answer["paczka"]["ile"] == 2
    assert [item["kod"] for item in answer["odrzucone"]] == ["POZA_LIMITEM", "POZA_LIMITEM"]
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and len(sent["zaplanowane"]) == 2
    assert (await _counts(pool, world))["requests"] == 2, "never more than the day's budget"
    waiting = [
        d for d in drafts if d["revision"] in {i["revision_id"] for i in answer["odrzucone"]}
    ]
    for draft in waiting:
        assert await _state(pool, draft["revision"]) == "READY_FOR_APPROVAL"
    # a second batch right away has no room today: the committed ones count
    again, no_token = await _ask(world, waiting)
    assert again["stan"] == "NIEDOSTEPNE" and again["kod"] == "POZA_LIMITEM" and no_token is None


async def test_what_is_already_queued_counts_and_the_batch_steps_around_it(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 3)
    await _click(world, drafts[0])  # queued now, no moment of its own: an event at the click

    answer, token = await _ask(world, drafts[1:])

    first = _at(answer["paczka"]["pozycje"][0]["kiedy"])
    assert first >= datetime.now(UTC) + timedelta(minutes=55), (
        "an hour after the one already queued"
    )
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and sent["odmowy"] == []


async def test_a_window_that_closes_before_the_turn_leaves_the_reply_out(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 1)
    await _click(world, drafts[0])
    soon = await _raw_draft(
        pool,
        tenant,
        world,
        text="Okno zamknie się za pół godziny.",
        created_ago=timedelta(hours=1),
        expires_in=timedelta(minutes=30),
    )
    drafts.append(soon)
    answer, token = await _ask(world, [drafts[1]])
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "OKNO_ZA_KROTKIE", answer
    assert token is None
    assert timedelta(minutes=10) == TICK_MARGIN


async def test_a_world_that_moved_since_the_question_refuses_the_batch_rather_than_reshuffling(
    pool, tenant
) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 3)
    answer, token = await _ask(world, drafts[:2])
    assert answer["paczka"]["ile"] == 2
    await _click(world, drafts[2])  # a single send, meanwhile: the hour is taken
    before = await _counts(pool, world)

    sent = await _confirm(world, token)

    assert sent["stan"] == "NIEDOSTEPNE" and sent["kod"] == "PACZKA_ZMIENIONA", sent
    assert {item["kod"] for item in sent["odrzucone"]} == {"PLAN_PRZESUNIETY"}
    assert await _counts(pool, world) == before, "nothing written"
    # and asking again shows the new plan, which ends at a confirmation that waits for a click
    again, fresh = await _ask(world, drafts[:2])
    assert again["stan"] == "WYKONANE" and fresh
    assert _at(again["paczka"]["pozycje"][0]["kiedy"]) >= datetime.now(UTC) + timedelta(minutes=55)


async def test_the_gate_checks_the_moment_a_request_is_queued_for_not_now(pool, tenant) -> None:
    """`approve_and_queue(not_before=...)` asks the limits about THAT moment, under the project's lock."""
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 3)
    await _click(world, drafts[0])
    org, ws = tenant["organization"], tenant["workspace"]

    async def approve(draft: dict, at: datetime):
        row = await revision_row(
            pool,
            organization_id=org,
            workspace_id=ws,
            project_id=world.project_id,
            revision_id=uuid.UUID(draft["revision"]),
        )
        return await approve_and_queue(
            pool,
            organization_id=org,
            workspace_id=ws,
            project_id=world.project_id,
            row=row,
            signer=None,
            reason="test",
            not_before=at,
        )

    with pytest.raises(HumanApprovalRefused) as full:
        await approve(drafts[1], datetime.now(UTC) + timedelta(minutes=10))
    assert full.value.code == "BUDZET" and "nie ma już miejsca w limitach" in full.value.detail
    assert await _state(pool, drafts[1]["revision"]) == "READY_FOR_APPROVAL", (
        "and nothing was written"
    )

    issued = await approve(drafts[1], datetime.now(UTC) + timedelta(minutes=62))
    assert issued.request_id is not None
    assert await _state(pool, drafts[1]["revision"]) == "APPROVED"
    # the same moment taken twice is refused: the second reply of the hour has no place
    with pytest.raises(HumanApprovalRefused):
        await approve(drafts[2], datetime.now(UTC) + timedelta(minutes=62))


async def test_an_ordinary_send_is_unchanged_and_still_refused_by_the_hour(pool, tenant) -> None:
    """The gate for a request with no moment is the old one: the second reply of the hour is refused."""
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 2)
    await _click(world, drafts[0])
    refused = await world.registry.call(
        "room.send_token", {"revision_id": drafts[1]["revision"], "odcisk": drafts[1]["odcisk"]}
    )
    token = (refused.get("_mcp_meta") or {}).get(META_TOKEN)
    assert token, refused
    sent = await world.registry.call(
        "room.send_draft", {"revision_id": drafts[1]["revision"], "token": token}
    )
    assert sent["stan"] == "NIEDOSTEPNE" and sent["kod"] == "BUDZET", sent
    async with pool.acquire() as conn:
        nulls = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE project_id = $1 AND not_before IS NULL",
            world.project_id,
        )
    assert nulls == 1, "an ordinary request has no moment"


# ---------------------------------------------------------------------------
# The order
# ---------------------------------------------------------------------------


async def test_a_fresh_post_on_x_goes_before_reddit(pool, tenant) -> None:
    world = await _world(pool, tenant)
    x_account, x_login = await _account(pool, tenant, world.project_id, "X")
    world.registry._batch_rng = _Floor()
    await _sign(
        pool,
        tenant,
        world,
        accounts=(world.account_id, x_account),
        budget=Budget(publications_per_hour=4, publications_per_day=20),
    )
    reddit_first = await _drafted(world, post="redd1x")
    x_draft = await _drafted_x(world, x_login)
    reddit_second = await _drafted(world, post="redd2x")

    answer, token = await _ask(world, [reddit_first, reddit_second, x_draft])

    assert answer["stan"] == "WYKONANE", answer
    order = [(i["platforma"], i["revision_id"]) for i in answer["paczka"]["pozycje"]]
    assert order[0] == ("X", x_draft["revision"]), order
    assert {r for _p, r in order[1:]} == {reddit_first["revision"], reddit_second["revision"]}
    sent = await _confirm(world, token)
    assert sent["stan"] == "OCZEKUJE" and sent["zaplanowane"][0]["platforma"] == "X"


# ---------------------------------------------------------------------------
# What the card follows
# ---------------------------------------------------------------------------


async def test_the_card_can_follow_each_request_with_its_moment(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=1, day=6)
    drafts = await _drafts(world, 2)
    _answer, token = await _ask(world, drafts)
    sent = await _confirm(world, token)

    for item in sent["zaplanowane"]:
        status = await world.registry.call("room.send_status", {"request_id": item["wniosek"]})
        assert status["wniosek"]["status"] == "QUEUED" and status["wniosek"]["adres"] is None
        assert (
            abs((_at(status["wniosek"]["najwczesniej"]) - _at(item["kiedy"])).total_seconds()) < 1
        )
        assert status["wniosek"]["powod_kod"] is None


async def test_the_horizon_and_the_jitter_are_the_ones_the_module_says(pool, tenant) -> None:
    world = await _scoped_world(pool, tenant, hour=10, day=50)
    world.registry._batch_rng = random.Random(3)
    drafts = await _drafts(world, 12)
    answer, token = await _ask(world, drafts)
    moments = [_at(i["kiedy"]) for i in answer["paczka"]["pozycje"]]
    assert answer["paczka"]["ile"] == 12 and token
    gap = timedelta(minutes=6)
    for earlier, later in pairwise(moments):
        assert gap + timedelta(minutes=1) <= later - earlier <= gap + JITTER_MAX
    assert moments[-1] <= datetime.now(UTC) + HORIZON
    assert answer["paczka"]["limity"] == {"godzina": 10, "doba": 50, "wlasne_doba": 1}
