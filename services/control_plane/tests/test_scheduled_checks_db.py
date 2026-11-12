"""A scheduled reply is looked at again when its moment comes, and is sent exactly as approved or not
at all (v11, "Send all"), against PostgreSQL and a scripted Reddit and X.

THE QUESTION: "Send all" approves a batch and spaces it out over hours, so a reply may wait long after
the person looked at its thread. What does the publisher do at the moment? (`pilot/send_checks.py`
holds the reasoning.) Held here, through the real path (`approve_and_queue` with a `not_before`, then
`PublishHandlers.run_once`):

  * the checks the publisher ALREADY has still run on a scheduled request, and say why they skipped:
    a thread that is gone or locked, a comment that is gone, our account already there;
  * THE NEW ONE: a thread that went stale while the reply waited (X older than six hours, Reddit
    older than 48, configurable) is SKIPPED with `wątek jest już za stary`, and a thread that is not
    stale is sent; a reply that was NEVER scheduled is not touched by it (it waited on nobody's
    schedule), so the behaviour of every older request is as it was;
  * A SESSION THAT IS DOWN keeps the request QUEUED with the reason and pushes its moment later
    (`SCHEDULED_RETRY_AFTER`), so it is tried again and does not starve the others; on Reddit a
    scheduled request whose browser is simply not signed in waits instead of stopping the whole
    queue, while a session that belongs to SOMEBODY ELSE still stops it, scheduled or not;
  * THE TEXT IS NEVER TOUCHED: what the port receives is the approved text, byte for byte;
  * `room.send_status` tells the reason (`powod`) with a stable code (`powod_kod`) and the moment
    (`najwczesniej`), which is what the card's "Scheduled" list shows.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from community_observer.x_browser import XPost, XSessionState
from control_plane.pilot import send_checks
from control_plane.pilot.publication import SCHEDULED_RETRY_AFTER, PublicationStore
from control_plane.pilot.publisher import (
    OUTCOME_BLOCKED,
    OUTCOME_PUBLISHED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
)
from control_plane.presence.human_approval import approve_and_queue, revision_row
from tests.test_pilot_publisher import ACCOUNT, FakePort, ScriptedRedditSource, thread_json

from tests import test_publish_unrecorded_thread as reddit
from tests import test_x_publication as xpub

pytestmark = pytest.mark.db


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


def _aged_thread(post_id: str, age: timedelta, **fields: Any) -> list[dict[str, Any]]:
    """`thread_json` with the post created `age` ago (its fixed timestamp is five weeks old)."""
    payload = thread_json(post_id, **fields)
    payload[0]["data"]["children"][0]["data"]["created_utc"] = time.time() - age.total_seconds()
    return payload


async def _scheduled_reddit(pool, tenant, seeded, *, text: str = reddit.TEXT) -> UUID:
    """An approved Reddit reply WITH a moment that has come (what "Send all" leaves, a while on)."""
    request_id = await reddit._approved_reply(pool, tenant, seeded, text=text)
    await _schedule(pool, request_id)
    return request_id


async def _schedule(pool, request_id: UUID, *, minutes_ago: int = 1) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET not_before = now() - make_interval(mins => $2) "
            "WHERE id = $1",
            request_id,
            minutes_ago,
        )


async def test_a_scheduled_reply_into_a_thread_that_went_cold_is_skipped_with_the_reason(
    pool, tenant
) -> None:
    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=60))

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED, result
    assert port.calls == [], "nothing was written"
    skipped = await reddit._request(pool, request_id)
    assert skipped is not None and skipped.status == "SKIPPED"
    assert "za stary" in (skipped.detail or "") and "60 h" in (skipped.detail or "")
    assert "tekst nie został zmieniony" in (skipped.detail or "")
    assert send_checks.reason_code(skipped.detail) == send_checks.REASON_STALE


async def test_a_scheduled_reply_into_a_young_enough_thread_goes_out_exactly_as_approved(
    pool, tenant
) -> None:
    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=47))

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    assert [call.text_and_media for call in port.calls] == [reddit.TEXT], "the text, byte for byte"
    published = await reddit._request(pool, request_id)
    assert published is not None and published.status == "PUBLISHED"


async def test_a_reply_that_was_never_scheduled_is_not_judged_by_the_age_of_its_thread(
    pool, tenant
) -> None:
    """Every older request behaves as it did: the consent was given in front of the screen."""
    seeded = await reddit._seed(pool, tenant)
    request_id = await reddit._approved_reply(pool, tenant, seeded)  # not_before NULL
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=300))

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    published = await reddit._request(pool, request_id)
    assert published is not None and published.not_before is None


async def test_the_age_limit_is_configurable_and_a_bad_value_does_not_switch_it_off(
    pool, tenant, monkeypatch
) -> None:
    seeded = await reddit._seed(pool, tenant)
    await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=60))

    monkeypatch.setenv(send_checks.ENV_MAX_AGE_REDDIT_HOURS, "100")
    result = await reddit._publisher(pool, tenant, source, port).run_once(now())
    assert result.outcome == OUTCOME_PUBLISHED, "a hundred hours allowed: sixty is young enough"

    other = "other1"
    second = await reddit._approved_reply(
        pool,
        tenant,
        seeded,
        target=f"t3_{other}",
        url=reddit.URL.replace(reddit.POST_ID, other),
        text="Another reply under another thread.",
    )
    await _schedule(pool, second)
    source.threads[other] = _aged_thread(other, timedelta(hours=60))
    monkeypatch.setenv(send_checks.ENV_MAX_AGE_REDDIT_HOURS, "nie-liczba")
    result = await reddit._publisher(pool, tenant, source, port).run_once(now())
    assert result.outcome == OUTCOME_SKIPPED and "najwyżej 48 h" in result.detail, result


@pytest.mark.parametrize(
    ("script", "fragment", "code"),
    [
        ("locked", "zablokowany", send_checks.REASON_LOCKED),
        ("archived", "zarchiwizowany", send_checks.REASON_LOCKED),
        ("ours_already_there", "naszego konta", send_checks.REASON_REPLIED),
        ("comment_gone", "zniknął", send_checks.REASON_GONE),
    ],
)
async def test_the_checks_the_publisher_already_has_run_on_a_scheduled_reply_too(
    pool, tenant, script: str, fragment: str, code: str
) -> None:
    seeded = await reddit._seed(pool, tenant)
    target = "t1_gone" if script == "comment_gone" else f"t3_{reddit.POST_ID}"
    url = f"{reddit.URL}gone/" if script == "comment_gone" else reddit.URL
    request_id = await reddit._approved_reply(pool, tenant, seeded, target=target, url=url)
    await _schedule(pool, request_id)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(
        reddit.POST_ID,
        timedelta(hours=2),
        comments=[{"id": "k1", "author": ACCOUNT}] if script == "ours_already_there" else [],
        locked=script == "locked",
        archived=script == "archived",
    )

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED and port.calls == [], result
    skipped = await reddit._request(pool, request_id)
    assert skipped is not None and skipped.status == "SKIPPED"
    assert fragment in (skipped.detail or "")
    assert send_checks.reason_code(skipped.detail) == code


async def test_a_thread_that_is_gone_is_skipped_when_its_moment_comes(pool, tenant) -> None:
    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()  # the thread is not there

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED and port.calls == []
    skipped = await reddit._request(pool, request_id)
    assert skipped is not None and "zniknął" in (skipped.detail or "")
    assert send_checks.reason_code(skipped.detail) == send_checks.REASON_GONE


async def test_a_scheduled_reply_whose_reddit_session_is_down_waits_and_the_queue_is_not_stopped(
    pool, tenant
) -> None:
    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(username=None), FakePort()  # signed out
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=2))
    before = now()

    result = await reddit._publisher(pool, tenant, source, port).run_once(before)

    assert result.outcome == OUTCOME_UNAVAILABLE, result
    assert port.calls == [] and source.reads == [], "no thread was read, nothing was written"
    waiting = await reddit._request(pool, request_id)
    assert waiting is not None and waiting.status == "QUEUED", "kept, not skipped"
    assert "brak sesji Reddita" in (waiting.detail or "") and "kolejną próbę" in waiting.detail
    assert send_checks.reason_code(waiting.detail) == send_checks.REASON_SESSION
    # pushed later, so it is not the head of the queue again at once
    assert waiting.not_before is not None
    assert waiting.not_before >= before + SCHEDULED_RETRY_AFTER - timedelta(seconds=30)
    assert await PublicationStore(pool).next_queued(organization_id=tenant["organization"]) is None
    # and NOTHING was stopped: the queue is not blocked and publishing is not switched off
    async with pool.acquire() as conn:
        stopped = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE project_id = $1 "
            "AND status IN ('BLOCKED', 'STOPPED')",
            seeded["project"],
        )
    assert stopped == 0


async def test_a_session_that_belongs_to_somebody_else_still_stops_the_queue(pool, tenant) -> None:
    """Retrying would make that worse: the browser is signed in as another account."""
    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(username="someone_else"), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=2))

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_BLOCKED and port.calls == [], result
    blocked = await reddit._request(pool, request_id)
    assert blocked is not None and blocked.status == "BLOCKED"
    assert send_checks.reason_code(blocked.detail) == send_checks.REASON_OTHER_ACCOUNT


async def test_a_reply_that_was_never_scheduled_and_a_signed_out_browser_still_blocks(
    pool, tenant
) -> None:
    """The publisher's older rule is untouched for a request nobody scheduled."""
    seeded = await reddit._seed(pool, tenant)
    request_id = await reddit._approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(username=None), FakePort()

    result = await reddit._publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_BLOCKED and port.calls == [], result
    blocked = await reddit._request(pool, request_id)
    assert blocked is not None and blocked.status == "BLOCKED" and blocked.not_before is None


async def test_a_request_that_waits_for_a_reason_does_not_hold_back_the_next_one(
    pool, tenant
) -> None:
    """The head of the queue is the youngest due thread; a waiting one moves out of its way."""
    seeded = await reddit._seed(pool, tenant)
    first = await _scheduled_reddit(pool, tenant, seeded)
    second = await reddit._approved_reply(
        pool,
        tenant,
        seeded,
        target="t3_other1",
        url=reddit.URL.replace(reddit.POST_ID, "other1"),
        text="A second reply, under another thread.",
    )
    await _schedule(pool, second, minutes_ago=2)
    store = PublicationStore(pool)
    org = tenant["organization"]
    head = await store.next_queued(organization_id=org)
    assert head is not None
    # whichever is the head waits for a reason; the other one is then the head
    await store.set_status(
        head.id, status="QUEUED", detail="brak sesji X konta @x: ekran logowania"
    )
    after = await store.next_queued(organization_id=org)
    assert after is not None and after.id != head.id and {head.id, after.id} == {first, second}
    pushed = await store.get_request(head.id)
    assert pushed is not None and pushed.not_before is not None
    assert pushed.not_before > now() + SCHEDULED_RETRY_AFTER - timedelta(minutes=1)


async def test_a_request_nobody_scheduled_keeps_a_null_moment_when_it_waits(pool, tenant) -> None:
    seeded = await reddit._seed(pool, tenant)
    request_id = await reddit._approved_reply(pool, tenant, seeded)
    store = PublicationStore(pool)
    waiting = await store.set_status(request_id, status="QUEUED", detail="tempo: 1/1 w godzinie")
    assert waiting.not_before is None, "older behaviour: it is tried again at the next tick"
    # an explicit moment still wins, scheduled or not
    soon = now() + timedelta(minutes=3)
    moved = await store.set_status(request_id, status="QUEUED", detail="x", not_before=soon)
    assert moved.not_before is not None and abs((moved.not_before - soon).total_seconds()) < 1
    again = await store.set_status(request_id, status="QUEUED", detail="y", not_before=soon)
    assert again.not_before == moved.not_before


# ---------------------------------------------------------------------------
# X
# ---------------------------------------------------------------------------


def _x_conversation(age: timedelta | None) -> Any:
    focal = XPost(
        id=xpub.POST_ID,
        author="jack",
        author_name="jack",
        text="Anyone using agents for distribution?",
        text_source="post",
        permalink=xpub.POST_URL,
        published_at=(now() - age).isoformat() if age is not None else None,
        relation="focal",
        metrics={},
    )
    return xpub._page(focal)


async def _scheduled_x(pool, tenant) -> tuple[dict[str, Any], UUID]:
    seeded = await xpub._seed(pool, tenant)
    await xpub._propose(pool, tenant, seeded)
    await xpub._issue(pool, tenant, seeded)
    request = await xpub._request(pool, seeded)
    await _schedule(pool, request["id"])
    return seeded, request["id"]


async def test_a_scheduled_x_reply_under_a_post_older_than_eight_hours_is_skipped(
    pool, tenant
) -> None:
    seeded, _request_id = await _scheduled_x(pool, tenant)
    source = xpub.DumbXSource(
        xpub._logged_in(), {(xpub.POST_URL, False): _x_conversation(timedelta(hours=10))}
    )
    port = xpub.DumbPort()

    outcome = await xpub._publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_SKIPPED and port.calls == [], outcome
    request = await xpub._request(pool, seeded)
    assert request["status"] == "SKIPPED" and "za stary" in request["detail"]
    assert "najwyżej 8 h (X)" in request["detail"]
    assert send_checks.reason_code(request["detail"]) == send_checks.REASON_STALE


async def test_a_scheduled_x_reply_under_a_young_post_goes_out_exactly_as_approved(
    pool, tenant
) -> None:
    _seeded, _request_id = await _scheduled_x(pool, tenant)
    source = xpub.DumbXSource(
        xpub._logged_in(), {(xpub.POST_URL, False): _x_conversation(timedelta(hours=3))}
    )
    port = xpub.DumbPort()

    outcome = await xpub._publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert [call.text_and_media for call in port.calls] == [xpub.TEXT]


async def test_an_old_x_post_does_not_stop_a_reply_that_was_never_scheduled(pool, tenant) -> None:
    seeded = await xpub._seed(pool, tenant)
    await xpub._propose(pool, tenant, seeded)
    await xpub._issue(pool, tenant, seeded)  # not_before NULL
    source = xpub.DumbXSource(
        xpub._logged_in(), {(xpub.POST_URL, False): _x_conversation(timedelta(hours=40))}
    )
    port = xpub.DumbPort()

    outcome = await xpub._publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome


async def test_a_scheduled_x_reply_whose_session_is_down_waits_and_is_pushed_later(
    pool, tenant
) -> None:
    seeded, _request_id = await _scheduled_x(pool, tenant)
    source = xpub.DumbXSource(
        XSessionState(False, None, now(), error="ekran logowania", login_wall=True)
    )
    port = xpub.DumbPort()
    before = now()

    outcome = await xpub._publisher(pool, tenant, source, port).run_once(before)

    assert outcome.outcome == OUTCOME_UNAVAILABLE and port.calls == [], outcome
    request = await xpub._request(pool, seeded)
    assert request["status"] == "QUEUED" and "brak sesji X" in request["detail"]
    assert send_checks.reason_code(request["detail"]) == send_checks.REASON_SESSION
    assert request["not_before"] >= before + SCHEDULED_RETRY_AFTER - timedelta(seconds=30)


async def test_a_scheduled_x_reply_whose_post_is_gone_or_answered_is_skipped(pool, tenant) -> None:
    seeded, _request_id = await _scheduled_x(pool, tenant)
    gone = xpub.DumbXSource(
        xpub._logged_in(), {(xpub.POST_URL, False): xpub._page(unavailable="deleted")}
    )
    port = xpub.DumbPort()
    outcome = await xpub._publisher(pool, tenant, gone, port).run_once(now())
    assert outcome.outcome == OUTCOME_SKIPPED and "niedostępny" in outcome.detail
    assert send_checks.reason_code(outcome.detail) == send_checks.REASON_GONE

    await xpub._propose(pool, tenant, seeded, text="another reply, same post")
    await xpub._issue(pool, tenant, seeded)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET not_before = now() - interval '1 minute' "
            "WHERE project_id = $1 AND status = 'QUEUED'",
            seeded["project"],
        )
    answered = xpub.DumbXSource(
        xpub._logged_in(),
        {
            (xpub.POST_URL, False): xpub._conversation(
                xpub._post("555", xpub.ACCOUNT, relation="reply")
            )
        },
    )
    outcome = await xpub._publisher(pool, tenant, answered, port).run_once(now())
    assert outcome.outcome == OUTCOME_SKIPPED and "nasza odpowiedź" in outcome.detail
    assert send_checks.reason_code(outcome.detail) == send_checks.REASON_REPLIED
    assert port.calls == []


# ---------------------------------------------------------------------------
# What the card reads
# ---------------------------------------------------------------------------


async def test_the_status_the_card_polls_carries_the_reason_its_code_and_the_moment(
    pool, tenant
) -> None:
    from control_plane.tools import ToolRegistry, issue

    seeded = await reddit._seed(pool, tenant)
    request_id = await _scheduled_reddit(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[reddit.POST_ID] = _aged_thread(reddit.POST_ID, timedelta(hours=60))
    await reddit._publisher(pool, tenant, source, port).run_once(now())

    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            seeded["project"],
        )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            dsn="postgresql://atrapa/nieuzywane",
            host="codex",
        ),
    )
    status = await registry.call("room.send_status", {"request_id": str(request_id)})
    wniosek = status["wniosek"]
    assert wniosek["status"] == "SKIPPED"
    assert "za stary" in wniosek["powod"] and wniosek["powod_kod"] == send_checks.REASON_STALE
    assert wniosek["najwczesniej"] is not None, "the moment the batch gave it"
    assert wniosek["adres"] is None, "nothing was published"
    # `revision_row`/`approve_and_queue` are the callers' way in; imported so a rename is caught here
    assert callable(revision_row) and callable(approve_and_queue)
