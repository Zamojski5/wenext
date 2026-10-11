"""`PUBLISHED` says what WE did. `public_visibility` says what anybody else can see (P0, 0012).

THE INCIDENT THIS FILE IS BUILT FROM, verbatim from STAN-PRAC §8.22: on 17.09 at 21:03 and 21:06
we published in two new communities. AutoModerator removed both about an hour later. Our control
read of the r/ecommerce request at 21:14, ALREADY AFTER the removal, returned `still_present =
true`. The cause is structural, not a fluke: we read logged in as the author, and Reddit shows an
author their own removed comments. Every verdict about how our comments performed carries that
limitation.

So the measurement that matters here is one scenario, and the rest of the file exists to stop it
being reached by accident:

    a request that is PUBLISHED, with ACTUATOR evidence, and an AUTHOR observation saying
    still_present = true, but whose ANONYMOUS read does not find the comment, is REMOVED.
    Never CONFIRMED. Never "still_present, so fine".

Around it, the three ways a system like this lies to itself:

  * it announces a result it did not measure. Every request published before 0012 is UNVERIFIED,
    and stays UNVERIFIED until an anonymous read happens. `all([])` is `True` and a default of
    CONFIRMED would have been exactly that mistake in a column (D-007, D-011).
  * it reads a refusal as an answer. A 403 or a 429 from Reddit is "we could not look", and if it
    became REMOVED the rate limiter would be writing our verdicts.
  * it lets the transport decide. With no anonymous reader wired in at all, the worker must leave
    every request UNVERIFIED rather than fall back to the author's read under a public label.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from community_observer import RemovalReport, SourceUnavailable
from control_plane.pilot.publication import (
    REMOVAL_FROM_INBOX,
    VIEWER_ANONYMOUS,
    VIEWER_AUTHOR,
    VISIBILITY_CONFIRMED,
    VISIBILITY_REMOVED,
    VISIBILITY_UNVERIFIED,
    AnonymousLook,
    visibility_of,
)
from control_plane.pilot.publisher import (
    ANONYMOUS_CHECK_INTERVAL,
    MAX_ANONYMOUS_READS_PER_TICK,
    OUTCOME_PUBLISHED,
    visibility_check_key,
)
from test_pilot_publisher import ACCOUNT, approved, thread_json, world

pytestmark = [pytest.mark.db, pytest.mark.p0]

OUR_TEXT = "Odpowiedź, którą opublikowaliśmy."

#: A FIXED point in time, and only for the tests that never touch the database. `visibility_of`
#: is a pure function; giving it a literal moment makes the expectations readable.
FIXED = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def now() -> datetime:
    """The clock the database-backed tests use, read at the START of each test.

    NOT a module constant. A literal `NOW` here passed at 12:04 and failed at 12:11 with
    `intent_expires_after_it_was_issued`: `issue_intent` writes `issued_at = now()` from the
    SERVER and `expires_at` from the clock it is handed, so a literal ten minutes in the past
    produces an intent that expired before it was issued. A test whose result depends on the
    wall-clock time of day is worse than no test: it is green all morning and red at noon, and
    whoever sees it red learns to read red as noise (docs/STAN-PRAC.md §7).
    """
    return datetime.now(UTC)


class ScriptedAnonymousReader:
    """The anonymous transport, scripted. Separate from the logged-in `ScriptedRedditSource` on
    purpose: a single fake answering both would make every test below pass against a worker that
    never left the author's session."""

    def __init__(self) -> None:
        self.threads: dict[str, Any] = {}
        self.raise_with: Exception | None = None
        self.reads: list[str] = []

    async def read_thread_payload_anonymously(
        self, community_key: str, thread_external_id: str
    ) -> Any:
        self.reads.append(thread_external_id)
        if self.raise_with is not None:
            raise self.raise_with
        return self.threads[thread_external_id]


class RenderedAnonymousReader(ScriptedAnonymousReader):
    def __init__(self, comment: dict[str, Any] | None, page_text: str = "") -> None:
        super().__init__()
        self.raise_with = SourceUnavailable("Reddit odpowiedział 403 na anonimowy JSON")
        self.comment = comment
        # Tekst CAŁEJ strony, osobno od tekstu komentarza: to na nim Reddit pisze, że zniknął
        # POST, a nie nasza odpowiedź. Fałszywka bez tego pola nie umiałaby odtworzyć 26.09.
        self.page_text = page_text

    async def read_comment_publicly(
        self, community_key: str, thread_external_id: str, comment_external_id: str
    ) -> dict[str, Any]:
        return {"url": "https://www.reddit.com/", "partial": True,
                "text": self.page_text, "target_comment": self.comment}


class ScriptedInbox:
    def __init__(self, *reports: RemovalReport) -> None:
        self.reports = reports
        self.raise_with: Exception | None = None
        self.calls = 0

    async def read_removal_reports(self) -> tuple[RemovalReport, ...]:
        self.calls += 1
        if self.raise_with is not None:
            raise self.raise_with
        return self.reports


async def published_request(pool, tenant, *, external_id: str = "1vis001"):
    """One consent taken all the way to PUBLISHED with ACTUATOR evidence, the state every live
    request is in. Returns `(world, request, comment_fullname, published_at)`.

    The moment is READ HERE and handed back, so every later assertion is relative to the same
    instant and none of them depends on what time of day the suite runs.
    """
    moment = now()
    await approved(pool, tenant, (external_id, OUR_TEXT))
    w = await world(pool, tenant)
    w.source.threads[external_id] = thread_json(external_id, comments=[{"id": "k1"}])
    result = await w.handlers.run_once(moment)
    assert result.outcome == OUTCOME_PUBLISHED, result
    (request,) = await w.publication.requests(organization_id=tenant["organization"])
    assert request.status == "PUBLISHED"
    evidence = await w.publication.evidence_for(request.attempt_id)
    assert evidence is not None and evidence["source"] == "ACTUATOR"
    return w, request, str(evidence["external_id"]), moment


def _thread_with_our_comment(external_id: str, fullname: str, *, body: str = OUR_TEXT):
    """The thread as an anonymous reader would receive it, with our comment in it."""
    return thread_json(
        external_id,
        comments=[{"id": fullname.removeprefix("t1_"), "author": ACCOUNT, "body": body}],
    )


# ---------------------------------------------------------------------------
# The rule, with no database in the way
# ---------------------------------------------------------------------------


def test_no_anonymous_read_is_unverified_and_carries_no_timestamp() -> None:
    """The state of every publication made before 0012. Not "probably fine": not measured."""
    assert visibility_of(anonymous=None, removal_reported_at=None) == (VISIBILITY_UNVERIFIED, None)


def test_an_anonymous_read_that_saw_the_comment_is_the_only_way_to_confirm() -> None:
    look = AnonymousLook(observed_at=FIXED, present=True, body=OUR_TEXT, score=4)
    assert visibility_of(anonymous=look, removal_reported_at=None) == (VISIBILITY_CONFIRMED, FIXED)


@pytest.mark.parametrize(
    ("present", "body", "why"),
    [
        (False, None, "komentarza nie ma w drzewie"),
        (True, "[removed]", "jest w drzewie, ale Reddit podaje [removed]"),
        (True, "[deleted]", "jest w drzewie, ale Reddit podaje [deleted]"),
        (True, "  [REMOVED] ", "to samo, inaczej zapisane"),
    ],
)
def test_the_two_shapes_of_a_removed_comment_are_both_removed(
    present: bool, body: str | None, why: str
) -> None:
    """A removed comment does not disappear from the tree: Reddit serves `[removed]` in its place.
    Checking presence alone would call that a success, which is half of the 17.09 mistake."""
    look = AnonymousLook(observed_at=FIXED, present=present, body=body)
    assert visibility_of(anonymous=look, removal_reported_at=None) == (VISIBILITY_REMOVED, FIXED)


def test_a_removal_reported_by_the_inbox_outranks_a_read_that_still_sees_it() -> None:
    """Which is the 17.09 ordering exactly: the inbox knew at 21:0x, our read at 21:14 did not."""
    seen = AnonymousLook(observed_at=FIXED, present=True, body=OUR_TEXT)
    reported = FIXED - timedelta(minutes=6)
    visibility, checked = visibility_of(anonymous=seen, removal_reported_at=reported)
    assert visibility == VISIBILITY_REMOVED
    assert checked == FIXED, "moment sprawdzenia to ten późniejszy z dwóch, nie pierwszy z brzegu"


def test_visibility_never_confirms_without_a_look() -> None:
    """The property, stated as itself: there is no combination of inputs that returns CONFIRMED
    with `anonymous=None`. Enumerated rather than reasoned about, because this is the function a
    later refactor could quietly invert."""
    for reported in (None, FIXED, FIXED - timedelta(days=1)):
        visibility, _ = visibility_of(anonymous=None, removal_reported_at=reported)
        assert visibility != VISIBILITY_CONFIRMED


def test_a_new_publication_is_checked_before_a_stale_unverified_backlog() -> None:
    old = SimpleNamespace(
        public_visibility_checked_at=None,
        published_at=FIXED - timedelta(days=5),
        last_checked_at=FIXED - timedelta(hours=3),
        queued_at=FIXED - timedelta(days=5),
    )
    fresh = SimpleNamespace(
        public_visibility_checked_at=None,
        published_at=FIXED - timedelta(minutes=10),
        last_checked_at=FIXED - timedelta(minutes=10),
        queued_at=FIXED - timedelta(minutes=11),
    )
    assert sorted((old, fresh), key=lambda r: visibility_check_key(r, FIXED))[0] is fresh


# ---------------------------------------------------------------------------
# The same rule through the database
# ---------------------------------------------------------------------------


async def test_a_published_request_starts_unverified_however_good_it_looks(pool, tenant) -> None:
    """ACTUATOR evidence, an author read-back saying present, a score: still UNVERIFIED, because
    not one of those things is a read by somebody who is not us."""
    w, request, _fullname, moment = await published_request(pool, tenant)
    await w.publication.record_observation(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        request_id=request.id,
        observed_at=moment,
        still_present=True,
        score=11,
        reply_count=2,
        raw={},
        viewer=VIEWER_AUTHOR,
    )
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.status == "PUBLISHED"
    assert fresh.public_visibility == VISIBILITY_UNVERIFIED
    assert fresh.public_visibility_checked_at is None
    author_read = await w.publication.latest_observation(request.id, viewer=VIEWER_AUTHOR)
    assert author_read is not None and author_read["still_present"] is True
    assert await w.publication.latest_observation(request.id, viewer=VIEWER_ANONYMOUS) is None


async def test_the_incident_of_17_09_now_reads_as_removed(pool, tenant) -> None:
    """THE scenario. Everything the old system could see says success; the one read it could not
    make says the community never saw it."""
    w, request, _fullname, moment = await published_request(pool, tenant)
    await w.publication.record_observation(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        request_id=request.id,
        observed_at=moment,
        still_present=True,  # the author sees their own removed comment
        score=1,
        reply_count=0,
        raw={},
        viewer=VIEWER_AUTHOR,
    )

    visibility = await w.publication.record_anonymous_look(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        request_id=request.id,
        look=AnonymousLook(observed_at=moment + timedelta(minutes=8), present=False, body=None),
    )

    assert visibility == VISIBILITY_REMOVED
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.public_visibility == VISIBILITY_REMOVED, (
        "wniosek z dowodem ACTUATOR i odczytem autora still_present=true został uznany za "
        "publiczny sukces, choć anonimowy czytelnik go nie widzi, a to jest dokładnie 17.09"
    )
    assert fresh.public_visibility_checked_at == moment + timedelta(minutes=8)
    # Both reads survive, each labelled with who made it. Losing the author's row would hide that
    # the two disagree, and the disagreement is the finding.
    assert (await w.publication.latest_observation(request.id, viewer=VIEWER_AUTHOR))[
        "still_present"
    ] is True
    assert (await w.publication.latest_observation(request.id, viewer=VIEWER_ANONYMOUS))[
        "still_present"
    ] is False


async def test_an_anonymous_read_that_finds_the_comment_confirms_it(pool, tenant) -> None:
    """The other direction, so the test above cannot be satisfied by a store that always writes
    REMOVED. r/kubernetes and r/b2b_sales are the real instances of this case."""
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis002")
    visibility = await w.publication.record_anonymous_look(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        request_id=request.id,
        look=AnonymousLook(observed_at=moment, present=True, body=OUR_TEXT, score=7, reply_count=1),
    )
    assert visibility == VISIBILITY_CONFIRMED
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_CONFIRMED
    assert fresh.public_visibility_checked_at == moment


async def test_an_inbox_report_marks_the_request_removed_and_names_its_source(pool, tenant) -> None:
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis003")
    reported = moment + timedelta(hours=1)

    visibility = await w.publication.report_removal(
        request.id,
        reported_at=reported,
        source=REMOVAL_FROM_INBOX,
        detail="skrzynka: was automatically removed",
    )

    assert visibility == VISIBILITY_REMOVED
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.removal_reported_at == reported and fresh.removal_source == REMOVAL_FROM_INBOX
    assert fresh.public_visibility_checked_at == reported
    # A second notice about the same comment does not move the moment we learned of it.
    await w.publication.report_removal(
        request.id, reported_at=reported + timedelta(hours=2), source=REMOVAL_FROM_INBOX
    )
    again = await w.publication.get_request(request.id)
    assert again is not None and again.removal_reported_at == reported


async def test_a_request_is_found_by_the_comment_id_its_evidence_recorded(pool, tenant) -> None:
    """How an inbox reply is matched to a request. `target_external_id` is what we REPLIED TO;
    matching on it would attribute the report to the wrong request, or to a thread we only read."""
    w, request, fullname, _moment = await published_request(pool, tenant, external_id="1vis004")
    found = await w.publication.request_by_comment_external_id(
        organization_id=tenant["organization"], external_id=fullname
    )
    assert found is not None and found.id == request.id
    assert fullname != request.target_external_id
    assert (
        await w.publication.request_by_comment_external_id(
            organization_id=tenant["organization"], external_id="t1_somebodyelse"
        )
        is None
    )


# ---------------------------------------------------------------------------
# The worker: what it does with a transport, and without one
# ---------------------------------------------------------------------------


async def test_without_an_anonymous_transport_nothing_is_verified_and_nothing_pretends(
    pool, tenant
) -> None:
    """The wiring is not done yet (the worker is built with `anonymous=None`), so this is the
    state the live stack is in TODAY, asserted rather than assumed."""
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis005")
    await w.handlers.reconcile_only(moment + timedelta(hours=2))
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_UNVERIFIED


async def test_the_worker_checks_a_published_request_and_records_the_verdict(pool, tenant) -> None:
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis006")
    anonymous = ScriptedAnonymousReader()
    anonymous.threads["1vis006"] = _thread_with_our_comment("1vis006", fullname)
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    assert anonymous.reads == ["1vis006"]
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_CONFIRMED


async def test_a_comment_served_as_removed_to_an_anonymous_reader_is_removed(pool, tenant) -> None:
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis007")
    anonymous = ScriptedAnonymousReader()
    anonymous.threads["1vis007"] = _thread_with_our_comment("1vis007", fullname, body="[removed]")
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_REMOVED


@pytest.mark.parametrize(
    "failure",
    [
        SourceUnavailable("Reddit odpowiedział 403 na /r/kubernetes/comments/1vis008.json"),
        SourceUnavailable("Reddit odpowiedział 429 na /r/kubernetes/comments/1vis008.json"),
        SourceUnavailable("czytnik kapsuły nieosiągalny"),
    ],
    ids=["403", "429", "reader-down"],
)
async def test_a_refusal_from_reddit_is_not_a_removal(pool, tenant, failure: Exception) -> None:
    """The single most dangerous confusion available here. 403 and 429 are both things Reddit
    said to us on 17.09 (D-035, four 403s in ninety seconds); read as REMOVED they would have
    condemned live comments, and the fix would have been to stop publishing where we were fine."""
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis008")
    anonymous = ScriptedAnonymousReader()
    anonymous.raise_with = failure
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.public_visibility == VISIBILITY_UNVERIFIED, (
        "odmowa Reddita została zapisana jako usunięcie komentarza. Od tej chwili licznik "
        "blokad mierzy tempo naszych odczytów, nie los naszych treści"
    )
    assert fresh.public_visibility_checked_at is None
    assert fresh.detail and "anonimowy" in fresh.detail, (
        "wniosek został NIESPRAWDZONY bez powodu w detalu. Operator widzi UNVERIFIED i nie wie, "
        "czy transportu nie ma, czy Reddit odmawia"
    )


async def test_rendered_exact_comment_proves_removal_after_json_403(pool, tenant) -> None:
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis020")
    comment = {
        "external_id": fullname,
        "permalink": f"/r/devops/comments/1vis020/comment/{fullname[3:]}/",
        "author": "[deleted]",
        "text": "[deleted]\nComment removed by moderator",
    }
    w.handlers._anonymous = RenderedAnonymousReader(comment)

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_REMOVED
    observed = await w.publication.latest_observation(request.id, viewer=VIEWER_ANONYMOUS)
    assert observed is not None and observed["still_present"] is False


async def test_rendered_page_without_exact_comment_stays_unverified(pool, tenant) -> None:
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis021")
    w.handlers._anonymous = RenderedAnonymousReader(None)

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_UNVERIFIED


async def test_rendered_exact_author_and_body_confirm_public_visibility(pool, tenant) -> None:
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis022")
    comment = {
        "external_id": fullname,
        "permalink": f"/r/devops/comments/1vis022/comment/{fullname[3:]}/",
        "author": ACCOUNT,
        "score": "3",
        "text": f"{ACCOUNT}\n{OUR_TEXT}\nReply Share",
    }
    w.handlers._anonymous = RenderedAnonymousReader(comment)

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_CONFIRMED


async def test_the_worker_reads_at_most_one_request_anonymously_per_tick(pool, tenant) -> None:
    """D-035 measured what happens without this bound: eleven runs cycling at the same second put
    four page loads on the account in ninety seconds and all four came back 403. A backlog of
    published requests must not become a burst of reads."""
    assert MAX_ANONYMOUS_READS_PER_TICK == 1
    for n in (1, 2, 3):
        await approved(pool, tenant, (f"1vis10{n}", f"{OUR_TEXT} {n}"))
    # Three publications inside one test hour: the pace is a manifest parameter of the run, and
    # this test is about the pace of READS, so the write pace is raised out of the way here.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pilot_runs SET max_publications_per_hour = 10, max_publications_per_day = 60 "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
    moment = now()
    w = await world(pool, tenant)
    anonymous = ScriptedAnonymousReader()
    for n in (1, 2, 3):
        w.source.threads[f"1vis10{n}"] = thread_json(f"1vis10{n}", comments=[{"id": f"k{n}"}])
        w.port.evidence = type(w.port.evidence)(
            external_id=f"t1_ours0{n}",
            permalink=f"https://www.reddit.com/r/kubernetes/comments/1vis10{n}/a/ours0{n}/",
            account=ACCOUNT,
            observed_at=moment,
            raw={},
        )
        assert (await w.handlers.run_once(moment)).outcome == OUTCOME_PUBLISHED
    published = [
        r
        for r in await w.publication.requests(organization_id=tenant["organization"])
        if r.status == "PUBLISHED"
    ]
    assert len(published) == 3, [r.status for r in published]
    for n, _request in enumerate(published, start=1):
        anonymous.threads[f"1vis10{n}"] = _thread_with_our_comment(f"1vis10{n}", f"t1_ours0{n}")
    # Wired in only now, so the publishing above cannot be what did the reading.
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))
    assert len(anonymous.reads) == 1, anonymous.reads

    # The next tick takes the next one: a bound on the rate, not a request that never gets looked
    # at. Without this the first request would be re-checked for ever and the other two never.
    await w.handlers.reconcile_only(moment + timedelta(hours=2, seconds=1))
    assert len(anonymous.reads) == 2 and len(set(anonymous.reads)) == 2, anonymous.reads


async def test_a_request_whose_read_keeps_failing_does_not_starve_the_others(pool, tenant) -> None:
    """Found by the test above, not reasoned about in advance.

    A failed read leaves no verdict and no `public_visibility_checked_at`, which is correct: it
    was not a check. But the queue is ordered by that column, so the failing request kept sorting
    first and took the tick's single read every time. Three published comments, a reader that
    refuses one thread, and the other two were never looked at. The tiebreaker is
    `last_checked_at`, which a failed read bumps.
    """
    for n in (1, 2):
        await approved(pool, tenant, (f"1vis20{n}", f"{OUR_TEXT} {n}"))
    # AFTER the consents: each one seeds its own run, so a pace raised earlier would be raised on
    # runs that do not exist yet.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pilot_runs SET max_publications_per_hour = 10, max_publications_per_day = 60 "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
    moment = now()
    w = await world(pool, tenant)
    # Every thread readable before the first publication: the queue publishes the YOUNGEST thread
    # first (D-030), not the one this loop happens to be on.
    for n in (1, 2):
        w.source.threads[f"1vis20{n}"] = thread_json(f"1vis20{n}", comments=[{"id": f"k{n}"}])
    for n in (1, 2):
        w.port.evidence = type(w.port.evidence)(
            external_id=f"t1_star0{n}",
            permalink=f"https://www.reddit.com/r/kubernetes/comments/1vis20{n}/a/star0{n}/",
            account=ACCOUNT,
            observed_at=moment,
            raw={},
        )
        result = await w.handlers.run_once(moment)
        assert result.outcome == OUTCOME_PUBLISHED, result

    anonymous = ScriptedAnonymousReader()
    anonymous.raise_with = SourceUnavailable("Reddit odpowiedział 403")
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))
    await w.handlers.reconcile_only(moment + timedelta(hours=2, seconds=1))

    assert sorted(anonymous.reads) == ["1vis201", "1vis202"], (
        f"dwa tiki, dwa odczyty, ale oba tego samego wniosku: {anonymous.reads}. Wniosek, "
        "którego nie da się odczytać, zabiera slot pozostałym w nieskończoność"
    )


async def test_a_fresh_verdict_is_not_rechecked_before_the_interval(pool, tenant) -> None:
    assert timedelta(hours=1) == ANONYMOUS_CHECK_INTERVAL
    w, _request, fullname, moment = await published_request(pool, tenant, external_id="1vis009")
    anonymous = ScriptedAnonymousReader()
    anonymous.threads["1vis009"] = _thread_with_our_comment("1vis009", fullname)
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=2))
    await w.handlers.reconcile_only(moment + timedelta(hours=2, minutes=30))
    assert len(anonymous.reads) == 1, "sprawdzony przed chwilą wniosek odczytano drugi raz"

    await w.handlers.reconcile_only(moment + timedelta(hours=3, minutes=1))
    assert len(anonymous.reads) == 2


async def test_a_request_already_removed_is_not_read_again(pool, tenant) -> None:
    """A removal does not un-happen, and the tick's single read belongs to an open question."""
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis010")
    await w.publication.report_removal(
        request.id, reported_at=moment, source=REMOVAL_FROM_INBOX, detail="skrzynka"
    )
    anonymous = ScriptedAnonymousReader()
    anonymous.threads["1vis010"] = _thread_with_our_comment("1vis010", fullname)
    w.handlers._anonymous = anonymous

    await w.handlers.reconcile_only(moment + timedelta(hours=5))

    assert anonymous.reads == []
    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_REMOVED


async def test_the_inbox_turns_an_automoderator_notice_into_a_removed_request(pool, tenant) -> None:
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis011")
    reported = moment + timedelta(hours=1)
    w.handlers._removals = ScriptedInbox(
        RemovalReport(
            comment_external_id=fullname,
            reported_at=reported,
            note="Your comment was automatically removed because your account is too new.",
        ),
        # A report about somebody else's comment must not touch any request of ours.
        RemovalReport(comment_external_id="t1_notours", reported_at=reported, note="was removed"),
    )

    await w.handlers.reconcile_only(reported + timedelta(minutes=5))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.public_visibility == VISIBILITY_REMOVED
    assert fresh.removal_source == REMOVAL_FROM_INBOX
    assert fresh.removal_reported_at == reported
    assert fresh.detail and "automatically removed" in fresh.detail


async def test_an_unreadable_inbox_does_not_confirm_anything(pool, tenant) -> None:
    """A capsule running without `WENEXT_READER_ALLOW_INBOX` answers 400, which arrives here as
    `SourceUnavailable`. An empty inbox and an unreadable one must not look the same."""
    w, request, _fullname, moment = await published_request(pool, tenant, external_id="1vis012")
    inbox = ScriptedInbox()
    inbox.raise_with = SourceUnavailable("czytnik kapsuły odrzucił URL /message/inbox.json")
    w.handlers._removals = inbox

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_UNVERIFIED
    assert fresh.removal_reported_at is None


# -- zakres usunięcia: nasza odpowiedź czy cały wątek pod nią (0032) ----------
#
# CO TO ZAMYKA, zmierzone 26.09.2026. Jedyna nowa odpowiedź z 23.09 (`t1_pblmb3d`) była
# anonimowo „Comment removed by moderator”, więc produkt zapisał REMOVED. 26.09 odczyt wątku
# `1woa28h` pokazał `removed_by_category = reddit` i stronę mówiącą „Sorry, this post was
# removed by Reddit's filters”: zniknął CAŁY POST, nie nasza odpowiedź, a konto było czyste
# (`is_suspended = false`, 233 karmy, `user_is_banned = false`). Zapis bez tej różnicy podsunął
# ludziom hipotezę o wieku i karmie konta i trafił do PROBA-RDZENIA-2026-09-23.


def _removed_comment(fullname: str, thread: str) -> dict[str, Any]:
    return {
        "external_id": fullname,
        "permalink": f"/r/devops/comments/{thread}/comment/{fullname[3:]}/",
        "author": "[deleted]",
        "text": "[deleted]\nComment removed by moderator",
    }


@pytest.mark.parametrize(
    "page_text_shown",
    [
        "Sorry, this post was removed by Reddit\u2019s filters.",
        "Sorry, this post was removed by Reddit's filters.",
        "Sorry, this post has been removed by the moderators of r/devops.",
    ],
)
async def test_the_whole_thread_is_gone_not_our_reply(pool, tenant, page_text_shown: str) -> None:
    """Werdykt zostaje REMOVED, ale zakres mówi, że usunięto POST, nie nas."""
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis030")
    w.handlers._anonymous = RenderedAnonymousReader(
        _removed_comment(fullname, "1vis030"), page_text=page_text_shown
    )

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None
    assert fresh.public_visibility == VISIBILITY_REMOVED
    scope = await pool.fetchval(
        "SELECT removal_scope FROM publication_requests WHERE id = $1", request.id
    )
    assert scope == "PARENT_POST", (
        "usunięcie całego wątku zapisano jako usunięcie naszej odpowiedzi; "
        "następna sesja wyciągnie z tego wniosek o koncie albo o społeczności"
    )


async def test_removing_only_our_reply_has_comment_scope(pool, tenant) -> None:
    """Post stoi, naszej odpowiedzi nie widać: to jest usunięcie komentarza i tak ma być zapisane."""
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis031")
    w.handlers._anonymous = RenderedAnonymousReader(
        _removed_comment(fullname, "1vis031"),
        page_text="Reviewing an agent's restart request\nComments Section\nSort by:",
    )

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_REMOVED
    scope = await pool.fetchval(
        "SELECT removal_scope FROM publication_requests WHERE id = $1", request.id
    )
    assert scope == "COMMENT"


async def test_a_visible_reply_gets_no_scope(pool, tenant) -> None:
    """Granica: zakres istnieje wyłącznie przy usunięciu. CHECK z 0032 pilnuje tego w bazie."""
    w, request, fullname, moment = await published_request(pool, tenant, external_id="1vis032")
    comment = {
        "external_id": fullname,
        "permalink": f"/r/devops/comments/1vis032/comment/{fullname[3:]}/",
        "author": ACCOUNT,
        "score": "3",
        "text": f"{ACCOUNT}\n{OUR_TEXT}\nReply Share",
    }
    w.handlers._anonymous = RenderedAnonymousReader(comment, page_text="zwykła strona wątku")

    await w.handlers.reconcile_only(moment + timedelta(hours=2))

    fresh = await w.publication.get_request(request.id)
    assert fresh is not None and fresh.public_visibility == VISIBILITY_CONFIRMED
    scope = await pool.fetchval(
        "SELECT removal_scope FROM publication_requests WHERE id = $1", request.id
    )
    assert scope is None
