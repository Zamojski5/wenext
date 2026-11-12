"""One sweep of the learning loop: register, snapshot, enrich, learn (13.10).

IT RUNS INSIDE THE HOURLY EXPERIENCE SWEEP (`knowledge/handlers.py::sweep_experience`), after the
experience rows are written and before the next sweep is scheduled. There is no second clock: the
chain that already measures what came of each publication also measures how it did over time, and
it is seeded on process start and kept alive by itself (`seed_experience_chain`). A failure here is
logged and never stops that chain.

FOUR STEPS, CHEAPEST FIRST:

  1. **register**: every published request (and every hand-posted reply that came with a link)
     that has no learning row gets one, with what was known when we replied: how old the post was,
     how many replies were already under it, the hook of the text, a link, a mention of Wenext.
     Nothing here reads the network.
  2. **harvest**: for each tracked item and each horizon (about 1, 6, 24, 72 h) without a snapshot,
     the author-side observations the publisher ALREADY took (every 15 to 55 minutes for 26 hours,
     `pilot/publisher.py::_observe_published`) are merged into one reading. This is free, and it is
     also the backfill: the 7,000 observations on file become snapshots for items that are days
     old. An observation only counts inside the horizon's window; a late one is never moved
     into an earlier horizon.
  3. **read**: a horizon that is due, not expired, and still empty after the publisher had its
     chance (`HARVEST_GRACE`) is read live: the capsule first and the public mirror to fill in
     (`sources.py`). At most `MAX_LIVE_READS` per sweep and `READ_GAP_SECONDS` apart, oldest first,
     inside a time budget, so a backlog is spread over sweeps instead of arriving at X at once.
  4. **learn**: the numbers become lessons (`model.find_claims`) and the lessons become rows of
     the project's memory (`kind = LESSON`): a new one is written, one whose numbers moved is
     CORRECTED (the old stays and points to the new, as everywhere in memory), one that no longer
     holds is RETRACTED with the reason.

ENRICHMENT RIDES ALONG: an X reply whose target author's follower count is unknown gets one read
of the public mirror (`MAX_FOLLOWER_READS` per sweep, three attempts per item); the count is the one
at reading time and the row says when.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from . import model
from .sources import LearningSources, Reading, TargetFacts
from .store import (
    KIND_OWN_POST,
    KIND_REPLY,
    ORIGIN_MANUAL,
    ORIGIN_PUBLICATION,
    PLATFORM_REDDIT,
    PLATFORM_X,
    LearningStore,
    NewPost,
    ObservationRow,
    PostRow,
    handle_from_url,
    item_from_url,
)

_LOG = logging.getLogger(__name__)

#: How many live reads one sweep makes at most, and how far apart. The sweep is hourly and every
#: horizon has a window of hours, so a smaller number loses nothing and a larger one only moves
#: the same reads closer together.
MAX_LIVE_READS = 4
READ_GAP_SECONDS = 2.0
#: A sweep stops starting reads after this long. The dispatcher keeps the job's lease alive, but a
#: hung capsule must not hold the experience chain for minutes.
LIVE_BUDGET_SECONDS = 120.0
#: Follower reads of the public mirror: per sweep, and tries per item before it is left as unknown.
MAX_FOLLOWER_READS = 8
FOLLOWER_ATTEMPTS = 3
#: The publisher observes a published request for this long (`pilot/publisher.py::OBSERVE_WINDOW`);
#: inside it, a horizon that is due is given `HARVEST_GRACE` for an observation to arrive before the
#: sweep reads the post itself. A test holds this equal to the publisher's constant.
PUBLISHER_OBSERVES_FOR = timedelta(hours=26)
HARVEST_GRACE = timedelta(minutes=30)
#: How far back an already-tracked item is looked at for a missing snapshot. A NEW item is always
#: harvested, whatever its age (the backfill).
HARVEST_LOOKBACK = model.LAST_EXPIRY + timedelta(hours=24)
#: At most this many untracked items are registered per sweep (the rest follow on the next one).
REGISTER_BATCH = 100


@dataclass
class SweepResult:
    registered: int = 0
    harvested: int = 0
    read: int = 0
    read_failed: int = 0
    gone: int = 0
    followers: int = 0
    lessons_written: int = 0
    lessons_corrected: int = 0
    lessons_retracted: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# What a published request knew when we replied
# --------------------------------------------------------------------------------------------


def _minutes(published_at: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    return max(0, int((published_at - then).total_seconds() // 60))


def _position(
    published_at: datetime, candidates: Sequence[tuple[datetime | None, int | None, str]]
) -> tuple[int | None, dict[str, Any]]:
    """The reply count of the target read CLOSEST BEFORE we replied, and where it came from.

    A reading made after our own reply counts our reply and the replies that followed it, so it is
    not "how many were there when we came"; it is dropped, and with it any position.
    """
    valid = [
        (at, count, source)
        for at, count, source in candidates
        if at is not None and count is not None and at <= published_at
    ]
    if not valid:
        return None, {}
    at, count, source = max(valid, key=lambda candidate: candidate[0])
    return count, {"position_source": source, "position_read_at": at.isoformat()}


def post_from_request(row: Mapping[str, Any]) -> NewPost | None:
    """A learning row for one published request, or `None` when it is not an item of a platform.

    Pure: the row is the joined record of `LearningStore.untracked_requests`. The item's identity
    is the evidence's external id (what the platform called our post), its text the consented text
    without the image line, and everything about the target is what the database held BEFORE the
    publication; nothing is read from the network here.
    """
    from control_plane.pilot.publication import split_media
    from control_plane.pilot.send_checks import x_created_at

    target = str(row["target_external_id"] or "")
    on_x = target.startswith("x:")
    platform = PLATFORM_X if on_x else PLATFORM_REDDIT
    our_id = str(row["our_id"] or "")
    permalink = str(row["permalink"] or "")
    published_at: datetime = row["published_at"]
    found = item_from_url(permalink)
    if found is None or found[0] != platform:
        return None
    if on_x and not our_id.startswith("x:"):
        return None
    if not on_x and not our_id.startswith(("t1_", "t3_")):
        return None
    kind = KIND_OWN_POST if row["kind"] == KIND_OWN_POST else KIND_REPLY

    payload = row["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    raw_text = str((payload or {}).get("text_and_media") or "")
    text, media = split_media(raw_text)
    facts: dict[str, Any] = {"hook_rule": "classify_hook"}

    new = {
        "organization_id": row["organization_id"],
        "workspace_id": row["workspace_id"],
        "project_id": row["project_id"],
        "platform": platform,
        "kind": kind,
        "origin": ORIGIN_PUBLICATION,
        "request_id": row["request_id"],
        "external_id": our_id,
        "permalink": permalink,
        "account": row["account"],
        "body": text,
        "published_at": published_at,
        "hook": model.classify_hook(text, has_media=media is not None),
        "hook_source": model.HOOK_CLASSIFIER,
        "mentions_wenext": model.mentions_wenext(text),
        "has_link": model.has_link(text),
    }
    if kind == KIND_OWN_POST:
        if not on_x:
            facts["community"] = target
        return NewPost(**new, facts=facts)

    created: datetime | None = None
    if on_x:
        created = x_created_at(None, target.removeprefix("x:"))
        facts["target_age_source"] = "snowflake"
    else:
        created = row["th_created"] or row["opp_created"]
        facts["target_age_source"] = "thread" if created is not None else None
    position, position_facts = _position(
        published_at,
        (
            (row["opp_metrics_at"], row["opp_replies"], "opportunity"),
            (row["xt_at"], row["xt_replies"], "x_trends"),
        ),
    )
    facts.update(position_facts)
    return NewPost(
        **new,
        target_external_id=target,
        target_url=row["target_url"],
        target_author=(
            row["opp_author"]
            or row["xt_author"]
            or row["th_author"]
            or handle_from_url(row["target_url"])
        ),
        target_created_at=created,
        target_age_minutes=_minutes(published_at, created),
        position_replies=position,
        facts=facts,
    )


def post_from_manual(row: Mapping[str, Any]) -> NewPost | None:
    """A learning row for a reply the founder posted by hand and linked to. Pure, like the above."""
    from control_plane.pilot.send_checks import x_created_at

    found = item_from_url(str(row["url"] or ""))
    if found is None:
        return None
    platform, external_id = found
    if platform != (PLATFORM_X if str(row["platform"]).upper() == "X" else PLATFORM_REDDIT):
        return None
    published_at: datetime = row["posted_at"]
    facts: dict[str, Any] = {"hook_rule": "classify_hook", "published_at_source": "marked_posted"}
    if platform == PLATFORM_X:
        # The reply's own snowflake id carries the real time: the founder marks it later.
        exact = x_created_at(None, external_id.removeprefix("x:"))
        if exact is not None:
            published_at = exact
            facts["published_at_source"] = "snowflake"
    target = str(row["target_external_id"] or "")
    created: datetime | None = None
    if target.startswith("x:"):
        created = x_created_at(None, target.removeprefix("x:"))
    else:
        created = row["opp_created"]
    position, position_facts = _position(
        published_at, ((row["opp_metrics_at"], row["opp_replies"], "opportunity"),)
    )
    facts.update(position_facts)
    text = str(row["text"] or "")
    return NewPost(
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
        platform=platform,
        kind=KIND_REPLY,
        origin=ORIGIN_MANUAL,
        manual_publication_id=row["manual_id"],
        external_id=external_id,
        permalink=str(row["url"]),
        body=text,
        published_at=published_at,
        target_external_id=target or None,
        target_url=row["target_url"],
        target_author=row["opp_author"] or handle_from_url(row["target_url"]),
        target_created_at=created,
        target_age_minutes=_minutes(published_at, created),
        position_replies=position,
        hook=model.classify_hook(text),
        hook_source=model.HOOK_CLASSIFIER,
        mentions_wenext=model.mentions_wenext(text),
        has_link=model.has_link(text),
        facts=facts,
    )


# --------------------------------------------------------------------------------------------
# Snapshots from observations the publisher already took
# --------------------------------------------------------------------------------------------


def reading_from_observations(
    post: PostRow, observations: Sequence[ObservationRow], hours: int
) -> tuple[Reading, datetime] | None:
    """One reading for this horizon out of the author-side observations inside its window.

    Counters are merged across the window, each from the EARLIEST observation that showed it:
    the capsule reads X's analytics and its counters in different moments, so one observation
    often has only the views and the next only the likes. The reading's moment is the earliest
    contributing observation (the age is that of the first number). `None` when the window holds
    no observation, or when the platform said the item was gone at its first look.
    """
    window = model.WINDOW_OF[hours]
    start = post.published_at + timedelta(minutes=window.due_minutes)
    end = post.published_at + timedelta(minutes=window.expires_minutes)
    inside = [one for one in observations if start <= one.observed_at <= end]
    if not inside:
        return None
    taken: dict[str, int | None] = {"views": None, "likes": None, "replies": None, "reposts": None}
    when: dict[str, str] = {}
    for one in inside:
        metrics = one.raw.get("metrics") if isinstance(one.raw.get("metrics"), dict) else {}
        shown: dict[str, Any] = {}
        if post.platform == PLATFORM_X:
            shown = {name: metrics.get(name) for name in taken}
            if shown["likes"] is None and one.score is not None:
                shown["likes"] = one.score
        else:
            # Reddit: points are the score; the reply count is a count of the replies the read
            # found, and a read always finds a number (possibly zero).
            shown = {"likes": one.score, "replies": one.reply_count}
        for name, value in shown.items():
            if (
                taken[name] is None
                and isinstance(value, int)
                and not isinstance(value, bool)
                and (value >= 0 or name == "likes")
            ):
                taken[name] = value
                when[name] = one.observed_at.isoformat()
    if all(value is None for value in taken.values()):
        return None
    contributed = set(when.values())
    measured_at = min(
        one.observed_at for one in inside if one.observed_at.isoformat() in contributed
    )
    reading = Reading(source="OBSERVATION", raw={"observed": when}, **taken)
    return reading, measured_at


# --------------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------------


class LearningService:
    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        organization_id: UUID,
        sources: LearningSources,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._org = organization_id
        self._store = LearningStore(pool)
        self._sources = sources
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic

    @property
    def store(self) -> LearningStore:
        return self._store

    async def sweep(self, now: datetime | None = None) -> SweepResult:
        moment = now or self._clock()
        result = SweepResult()
        try:
            new_ids = await self.register_new(result)
            await self.harvest(moment, result, new_ids=new_ids)
            await self.read_due(moment, result)
            await self.enrich_followers(result)
            await self.update_lessons(moment, result)
        finally:
            await self._sources.aclose()
        return result

    # -- 1. register -----------------------------------------------------------------------------

    async def register_new(self, result: SweepResult) -> set[UUID]:
        created: set[UUID] = set()
        for row in await self._store.untracked_requests(self._org, limit=REGISTER_BATCH):
            new = post_from_request(dict(row))
            if new is None:
                continue
            post_id, was_new = await self._store.register(new)
            if was_new:
                created.add(post_id)
        for row in await self._store.untracked_manual(self._org, limit=REGISTER_BATCH):
            new = post_from_manual(dict(row))
            if new is None:
                continue
            post_id, was_new = await self._store.register(new)
            if was_new:
                created.add(post_id)
        result.registered = len(created)
        return created

    # -- 2. harvest ------------------------------------------------------------------------------

    async def harvest(
        self,
        now: datetime,
        result: SweepResult,
        *,
        new_ids: set[UUID] | frozenset[UUID] = frozenset(),
    ) -> None:
        posts = [
            post
            for post in await self._store.posts(self._org)
            if post.request_id is not None
            and (post.id in new_ids or now - post.published_at <= HARVEST_LOOKBACK)
        ]
        if not posts:
            return
        taken = await self._store.horizons_taken([post.id for post in posts])
        observed = await self._store.observations_of([post.request_id for post in posts])
        for post in posts:
            for hours in model.HORIZONS:
                if hours in taken.get(post.id, set()):
                    continue
                if now - post.published_at < timedelta(minutes=model.WINDOW_OF[hours].due_minutes):
                    continue
                found = reading_from_observations(post, observed.get(post.request_id, []), hours)
                if found is None:
                    continue
                reading, measured_at = found
                if await self._store.record_snapshot(
                    post,
                    horizon_hours=hours,
                    measured_at=measured_at,
                    views=reading.views,
                    likes=reading.likes,
                    replies=reading.replies,
                    reposts=reading.reposts,
                    source=reading.source,
                    raw=reading.raw,
                ):
                    result.harvested += 1

    # -- 3. read ---------------------------------------------------------------------------------

    async def read_due(self, now: datetime, result: SweepResult) -> None:
        posts = [
            post
            for post in await self._store.posts(self._org, since=now - model.LAST_EXPIRY)
            if not post.facts.get("gone_at")
        ]
        taken = await self._store.horizons_taken([post.id for post in posts])
        due: list[tuple[timedelta, PostRow, int]] = []
        for post in posts:
            for hours in model.due_horizons(post.published_at, now, taken.get(post.id, set())):
                late_by = (
                    now - post.published_at - timedelta(minutes=model.WINDOW_OF[hours].due_minutes)
                )
                observed_by_publisher = (
                    post.request_id is not None and now - post.published_at < PUBLISHER_OBSERVES_FOR
                )
                if observed_by_publisher and late_by < HARVEST_GRACE:
                    continue  # the publisher's next observation is the cheaper way
                due.append((late_by, post, hours))
        due.sort(key=lambda entry: entry[0], reverse=True)
        started = self._monotonic()
        done = 0
        for index, (_late, post, _hours) in enumerate(due):
            if done >= MAX_LIVE_READS or self._monotonic() - started > LIVE_BUDGET_SECONDS:
                break
            if index:
                await self._sleep(READ_GAP_SECONDS)
            done += 1
            reading = await self._read(post)
            if reading is None:
                result.read_failed += 1
                continue
            if reading.gone:
                await self._store.note_attempt(post.id, "gone_at", now.isoformat())
                result.gone += 1
                continue
            measured_at = self._clock()
            snapshot_hours = model.horizon_of(model.age_minutes(post.published_at, measured_at))
            if await self._store.record_snapshot(
                post,
                horizon_hours=snapshot_hours,
                measured_at=measured_at,
                views=reading.views,
                likes=reading.likes,
                replies=reading.replies,
                reposts=reading.reposts,
                source=reading.source,
                raw=reading.raw,
            ):
                result.read += 1

    async def _read(self, post: PostRow) -> Reading | None:
        if post.platform == PLATFORM_X:
            return await self._sources.read_x_item(
                post.permalink, post_id=post.external_id.removeprefix("x:")
            )
        if not self._sources.can_read_reddit:
            return None
        from control_plane.observe.reddit_search import parse_thread_reference

        try:
            if post.kind == KIND_OWN_POST:
                community = str(post.facts.get("community") or "")
                thread_id = post.external_id.removeprefix("t3_")
            else:
                community, thread_id = parse_thread_reference(post.target_url or "")
        except ValueError:
            return None
        if not community:
            return None
        return await self._sources.read_reddit_item(
            community_key=community,
            thread_id=thread_id,
            our_fullname=post.external_id,
            own_post=post.kind == KIND_OWN_POST,
        )

    # -- enrichment ------------------------------------------------------------------------------

    async def enrich_followers(self, result: SweepResult) -> None:
        posts = await self._store.posts_needing_followers(
            self._org, max_attempts=FOLLOWER_ATTEMPTS, limit=MAX_FOLLOWER_READS
        )
        for index, post in enumerate(posts):
            if index:
                await self._sleep(READ_GAP_SECONDS / 2)
            target_id = str(post.target_external_id or "").removeprefix("x:")
            facts: TargetFacts | None = await self._sources.read_x_target(
                post.target_url, target_id=target_id
            )
            attempts = int(post.facts.get("followers_attempts") or 0) + 1
            if facts is None or facts.followers is None:
                await self._store.note_attempt(post.id, "followers_attempts", attempts)
                continue
            await self._store.apply_context(
                post.id,
                target_followers=facts.followers,
                target_author=facts.handle,
                facts={
                    "followers_source": "fxtwitter",
                    "followers_read_at": (facts.read_at or self._clock()).isoformat(),
                    "followers_attempts": attempts,
                },
            )
            result.followers += 1

    # -- 4. learn --------------------------------------------------------------------------------

    async def update_lessons(self, now: datetime, result: SweepResult) -> None:
        from control_plane.knowledge.store import KnowledgeRefused, KnowledgeStore, NewItem, Scope

        knowledge = KnowledgeStore(self._pool)
        items = await self._store.items(self._org)
        groups: dict[tuple[UUID, UUID | None], list[model.Item]] = {}
        for item in items:
            groups.setdefault((item.workspace_id, item.project_id), []).append(item)
        for group in await self._store.lesson_groups(self._org):
            groups.setdefault(group, [])  # a group with lessons and no data may need retracting

        for (workspace_id, project_id), rows in groups.items():
            scope = Scope(
                organization_id=self._org, workspace_id=workspace_id, project_id=project_id
            )
            claims: dict[str, model.Claim] = {}
            for platform in (PLATFORM_X, PLATFORM_REDDIT):
                for claim in model.find_claims(rows, platform=platform):
                    claims[claim.key] = claim
            existing = await self._store.lessons_in_force(self._org, workspace_id, project_id)
            for key, claim in claims.items():
                statement = model.statement_of(claim)
                payload = model.payload_of(claim, measured_at=now)
                new = NewItem(
                    kind="LESSON",
                    statement=statement,
                    stated_by="AGENT",
                    project_id=project_id,
                    payload=payload,
                )
                old = existing.get(key)
                if old is not None and old.statement == statement:
                    continue
                try:
                    written = await knowledge.write(new, scope, measured=True)
                except KnowledgeRefused as exc:
                    result.notes.append(f"lekcja {key} odrzucona: {exc}")
                    continue
                if not written.in_force:
                    # The numbers went back to a sentence that held before; it comes back.
                    await self._store.reinstate_lesson(written.id, payload)
                if old is None:
                    result.lessons_written += 1
                elif await self._store.supersede_lesson(old.id, by=written.id):
                    result.lessons_corrected += 1
            for key, old in existing.items():
                if key not in claims and await knowledge.retract(
                    old.id,
                    "The measurement no longer shows this difference: the groups changed or "
                    "the gap closed below the thresholds.",
                    scope,
                ):
                    result.lessons_retracted += 1


async def run_sweep(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    sources: LearningSources,
    now: datetime | None = None,
) -> SweepResult:
    """One sweep, for the experience job and for the backfill script."""
    return await LearningService(pool=pool, organization_id=organization_id, sources=sources).sweep(
        now
    )
