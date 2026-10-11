"""How observe work gets into the queue — job types, payload refs, idempotency keys, cadence.

Everything here goes through `domain.commands.enqueue_job`, like the API and the dev CLI: a
scheduler with its own INSERT would be the one writer that never learns about a new invariant.

THE ONE IDEA: the schedule is a chain of jobs, each idempotent on a time slot, and the chain is
restart-safe because the queue is (L5212). There is no cron, no in-process timer that dies with
the process, and no second copy of "when is the next poll" outside the `jobs` table:

* `observe.poll_community` for community C and slot S = floor(now / interval) + 1 has the key
  `poll:C:S`. Whoever enqueues it twice adds nothing (`EnqueuedJob.created == False`). The poll
  handler enqueues the next slot AFTER a successful read — a chain, not a timer.
* `observe.snapshot_thread` for thread T at 1 h / 6 h / 24 h after first sight: `snapshot:T:1h`
  etc. Three keys, three rows, once.
* `understand.community_picture` for C and day D: `picture:C:D`. Once a day, whichever poll
  is the first to notice the day has turned.
* `judge.thread` for T on day D: `judge:T:D`. Re-enqueued at most once a day while the thread
  is young and unjudged — the daily sweep in the poll handler is what makes a failed judgment
  self-heal without a retry policy of its own.

Payload refs are `community/<uuid>` and `thread/<uuid>`: `jobs.payload_ref` is a REFERENCE
column (0003), and an entity id is exactly a reference. No payload table, no JSON blob.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from control_plane.domain import commands
from control_plane.domain.models import EnqueuedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.backoff import ATTEMPT_LIMIT

JOB_POLL = "observe.poll_community"
JOB_SNAPSHOT = "observe.snapshot_thread"
JOB_PICTURE = "understand.community_picture"
JOB_JUDGE = "judge.thread"
#: Etap 3 M0 — the community's voice card: how people write here, measured on the comments of the
#: last days and described in plain words. Once a UTC day per community, like the picture.
JOB_VOICE = "understand.community_voice"
#: D-033 — a bounded read of the liveliest threads of a community we only listen to. The listing
#: gives posts, never comments, so a community entered to learn how people write there had nothing
#: to build a voice card from (measured 2026-09-17: 0 comments in eleven communities, 0 cards).
#: This reads a handful of threads WITH their comments, once a day, and then asks for the card.
JOB_SAMPLE = "observe.sample_comments"
OBSERVE_JOB_TYPES: tuple[str, ...] = (
    JOB_POLL,
    JOB_SNAPSHOT,
    JOB_PICTURE,
    JOB_JUDGE,
    JOB_VOICE,
    JOB_SAMPLE,
)

#: One routing key for the whole module. `jobs.routing_key` is required by 0003 and, with one
#: process and no broker (E3), a second key would route nothing anywhere.
ROUTING_KEY = "observe"

#: Lower number = earlier. Judgments before snapshots before polls before the daily picture:
#: a decision about a live thread is worth more now than a metric read is.
PRIORITY_JUDGE = 10
PRIORITY_SNAPSHOT = 20
PRIORITY_POLL = 30
PRIORITY_PICTURE = 40
#: Before the voice card: the card is written from what this read brings in.
PRIORITY_SAMPLE = 44
PRIORITY_VOICE = 45

#: Seconds after FIRST sight at which a thread's metrics are read again (roadmap 22.9 "1/6/24 h").
SNAPSHOT_DELAYS: tuple[tuple[str, int], ...] = (("1h", 3600), ("6h", 21600), ("24h", 86400))

#: The daily picture is drawn once the UTC day has turned and this many hours have passed —
#: so the window it covers is a whole day, not the first ten minutes of one.
PICTURE_EARLIEST_HOUR_UTC = 5

#: How long a thread stays eligible for a (re-)judgment sweep after it was first seen.
JUDGE_SWEEP_HOURS = 48


def community_ref(community_id: UUID) -> str:
    return f"community/{community_id}"


def thread_ref(thread_id: UUID) -> str:
    return f"thread/{thread_id}"


def parse_ref(payload_ref: str | None, expected_kind: str) -> UUID:
    """`<kind>/<uuid>` → the uuid. Anything else raises: a handler given the wrong ref must not
    run against a guessed entity."""
    if payload_ref is None or "/" not in payload_ref:
        raise ValueError(f"payload_ref={payload_ref!r} nie ma postaci `{expected_kind}/<uuid>`")
    kind, _, raw = payload_ref.partition("/")
    if kind != expected_kind:
        raise ValueError(f"payload_ref={payload_ref!r}: oczekiwano `{expected_kind}/…`")
    return UUID(raw)


def poll_slot(now: datetime, interval_seconds: int) -> int:
    return int(now.timestamp() // interval_seconds)


def day_key(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


async def schedule_poll(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    community_id: UUID,
    interval_seconds: int,
    now: datetime,
    immediately: bool,
) -> EnqueuedJob:
    """The next poll of a community: the current slot right now, or the next slot when it is due.

    `immediately=True` is what "enable observation" uses — the operator should see the first
    read within seconds. The poll handler uses `False`: the next read waits for its slot.
    """
    slot = poll_slot(now, interval_seconds) + (0 if immediately else 1)
    if immediately:
        delay = 0.0
    else:
        next_slot_at = slot * interval_seconds
        delay = max(0.0, next_slot_at - now.timestamp())
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_POLL,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_POLL,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"poll:{community_id}:{slot}",
        payload_ref=community_ref(community_id),
        delay_seconds=delay,
    )


async def schedule_snapshots(
    port: JobQueuePort, *, organization_id: UUID, workspace_id: UUID, thread_id: UUID
) -> tuple[EnqueuedJob, ...]:
    """Three metric reads of a newly seen thread. Keys per delay label, so a re-run adds nothing."""
    results = []
    for label, delay in SNAPSHOT_DELAYS:
        results.append(
            await commands.enqueue_job(
                port,
                organization_id=organization_id,
                workspace_id=workspace_id,
                job_type=JOB_SNAPSHOT,
                routing_key=ROUTING_KEY,
                priority=PRIORITY_SNAPSHOT,
                attempt_limit=ATTEMPT_LIMIT,
                idempotency_key=f"snapshot:{thread_id}:{label}",
                payload_ref=thread_ref(thread_id),
                delay_seconds=float(delay),
            )
        )
    return tuple(results)


async def schedule_picture(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    community_id: UUID,
    now: datetime,
) -> EnqueuedJob:
    """Today's picture of a community. One per UTC day per community."""
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_PICTURE,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_PICTURE,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"picture:{community_id}:{day_key(now)}",
        payload_ref=community_ref(community_id),
    )


async def schedule_voice(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    community_id: UUID,
    now: datetime,
) -> EnqueuedJob:
    """Today's voice card of a community. One per UTC day per community; `picture_is_due` decides
    when, with the latest card's `window_end` in place of the picture's."""
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_VOICE,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_VOICE,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"voice:{community_id}:{day_key(now)}",
        payload_ref=community_ref(community_id),
    )


async def schedule_sample(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    community_id: UUID,
    now: datetime,
) -> EnqueuedJob:
    """Today's sample read of a community's liveliest threads. One per UTC day per community, like
    the picture; the handler asks for the voice card when it is done."""
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_SAMPLE,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_SAMPLE,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"sample:{community_id}:{day_key(now)}",
        payload_ref=community_ref(community_id),
    )


async def schedule_judgment(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    thread_id: UUID,
    now: datetime,
) -> EnqueuedJob:
    """A judgment of one thread, at most once per UTC day."""
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_JUDGE,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_JUDGE,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"judge:{thread_id}:{day_key(now)}",
        payload_ref=thread_ref(thread_id),
    )


def picture_is_due(now: datetime, latest_window_end: datetime | None) -> bool:
    """Whether today's picture should be drawn now.

    Due when the UTC clock has passed `PICTURE_EARLIEST_HOUR_UTC` and the latest picture ends
    before today's midnight — or there is none. A picture drawn at 00:05 would describe five
    minutes, so the hour gate exists; the midnight comparison makes it once a day.
    """
    utc_now = now.astimezone(UTC)
    if utc_now.hour < PICTURE_EARLIEST_HOUR_UTC:
        return False
    midnight = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return latest_window_end is None or latest_window_end < midnight
