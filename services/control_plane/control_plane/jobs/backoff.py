"""The frozen thresholds of the job queue (D-017) and the ONE place a retry delay is computed.

    "job leases, heartbeat, reaper, attempt limit i terminalizacja ostatniej próby" (L5194)
    "Poniżej limitu zwiększa `lease_generation` i `dispatch_version`, tworzy replacement
     `job_attempt(attempt_no+1, not_before=backoff)`" (L2669)

THIS MODULE IS THE ONE CONFIGURATION MODULE D-017 DEMANDS. Its exact words: "wartości mieszkają
w JEDNYM module konfiguracji, nie rozsypane po wywołaniach". Every number below was written down
BEFORE the queue was measured, which is the only thing that makes a later measurement worth
anything (KONWENCJE.md: a threshold picked after seeing the results invalidates the result).
Changing one is an edit of D-017, and it invalidates every earlier soak run of the queue.

WHAT IS NOT HERE, and it is a gap rather than a decision: D-017's table also freezes the
artifact retention (30 days) and the source-content limit (1 MiB). Neither is a queue threshold,
neither is used by any code in this package, and inventing a second home for them here would
create exactly the duplication D-017 forbids. They belong with the artifacts package. Reported to
the orchestrator rather than silently absorbed.

TWO PROPERTIES OF THE FUNCTIONS BELOW, both load-bearing:

1.  **They return SECONDS, never a moment and never a duration object.** Every deadline in this
    subsystem is computed by PostgreSQL as `now() + make_interval(secs => …)` (L2668), so a
    delay crosses this boundary as a number. The Definition of Done greps this file for the
    duration type of the standard library and expects nothing — which is why that type is named
    nowhere here, not even inside this explanation. The first version of this docstring named it
    and failed its own check, the same trap `jobs/store.py` documents.

2.  **The jitter source is injectable.** `backoff_seconds` takes the random source as an
    argument, so a test can hand it a seeded one and assert the exact number. A module-level
    generator would leave the jitter untestable, and an untestable jitter is how a "full jitter"
    that is really half jitter — or no jitter at all — survives a green suite.
"""

from __future__ import annotations

import random
from typing import Final

# ---------------------------------------------------------------------------
# The five frozen queue thresholds (D-017)
# ---------------------------------------------------------------------------

#: Attempts a job gets before the reaper terminalizes it instead of scheduling another one
#: (L5214). D-017: "czwarta próba wypada po ~6 min backoffu; piąta jest ostatnią i terminalizuje".
#:
#: This value is the DEFAULT for new jobs and nothing else. The reaper compares `attempt_no`
#: against `jobs.attempt_limit` — the value stored on the row — never against this constant, so
#: a job enqueued under an older limit keeps the limit it was enqueued with. Migration 0003 makes
#: the same choice: the column is `NOT NULL` with no `DEFAULT`.
ATTEMPT_LIMIT: Final[int] = 5

#: Seconds a job lease lives unless renewed. D-017: "dłuższy niż najdłuższa operacja DB w F1,
#: krótszy niż cierpliwość operatora".
LEASE_TTL_SECONDS: Final[float] = 60.0

#: Seconds between heartbeats of a running attempt. D-017 derives it as TTL/4 — three missed
#: beats before the lease dies, so one hiccup does not cost the job.
HEARTBEAT_INTERVAL_SECONDS: Final[float] = 15.0

#: Seconds between reaper passes. Shorter than the TTL on purpose, so an expired lease does not
#: wait a full TTL to be noticed.
REAPER_INTERVAL_SECONDS: Final[float] = 10.0

#: The backoff ceilings, in order. The delay before the next attempt is drawn uniformly from
#: `[0, ceiling]` — FULL jitter, not half. D-017: "thundering herd przy restarcie jest realny
#: przy jednym dispatcherze i wielu jobach".
BACKOFF_CEILING_SECONDS: Final[tuple[float, ...]] = (5.0, 15.0, 60.0, 300.0, 900.0)

#: The whole table as data, so a test can compare it against the decision entry instead of
#: re-typing the numbers next to the assertions. D-017's "jak to sprawdzić" is exactly this:
#: "test czytający tabelę progów z tego modułu i porównujący ją z listą powyżej".
QUEUE_THRESHOLDS: Final[dict[str, object]] = {
    "attempt_limit": ATTEMPT_LIMIT,
    "lease_ttl_seconds": LEASE_TTL_SECONDS,
    "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
    "reaper_interval_seconds": REAPER_INTERVAL_SECONDS,
    "backoff_ceiling_seconds": BACKOFF_CEILING_SECONDS,
}

#: Seconds between dispatcher polls. **NOT a D-017 value** — the decision freezes the reaper's
#: loop and says nothing about the dispatcher's, so this is a working default that reuses the
#: reaper's number, and changing it needs no amendment of that decision. It is spelled separately
#: precisely so that nobody later reads the reaper's frozen 10 s as covering this too.
#:
#: What it must NOT become is zero or "only when notified": polling is the FLOOR under the
#: dispatcher, and `dispatcher.py` explains at length what a job inserted while the process was
#: down looks like when a notification is the only wake-up.
DISPATCHER_POLL_INTERVAL_SECONDS: Final[float] = REAPER_INTERVAL_SECONDS


def backoff_ceiling_seconds(failed_attempt_no: int) -> float:
    """The ceiling of the delay after `failed_attempt_no` has ended without a result.

    Indexing, stated once so that nobody has to reverse-engineer it: the delay AFTER the k-th
    attempt is drawn under the k-th ceiling. So the first failure waits at most 5 s and the
    fourth at most 300 s; with the frozen limit of five attempts, the last entry (900 s) is
    never reached, because there is no sixth attempt to delay.

    HONEST NOTE ON D-017's ARITHMETIC, because a docstring that smooths this over is worse than
    one that admits it: under this indexing the ceilings actually used by a five-attempt job sum
    to 5 + 15 + 60 + 300 = 380 s ≈ 6.3 min, and the FIFTH attempt is the one that starts after
    them. D-017's parenthetical says "czwarta próba wypada po ~6 min backoffu", which matches
    that number only if "czwarta" counts the fourth BACKOFF rather than the fourth attempt.
    The numbers in the table are the frozen part; that sentence is a rationale and it is off by
    one somewhere. NOT resolved here — resolving it silently in code would be inventing a
    decision. `tests/test_attempt_limit.py` pins the mapping this function implements, so a
    change is a failing test rather than a surprise.

    Above the length of the schedule the last ceiling repeats. A job with a hand-raised
    `attempt_limit` therefore keeps retrying under the 900 s ceiling instead of growing past it,
    which is a cap, not an accident.
    """
    if failed_attempt_no < 1:
        raise ValueError(
            f"failed_attempt_no = {failed_attempt_no}: backoff liczy się PO próbie, a próby "
            "numerują się od 1 (schemat 0003 wymusza attempt_no >= 1). Zero albo liczba ujemna "
            "znaczyłaby, że opóźniamy próbę, której nie było"
        )
    index = min(failed_attempt_no, len(BACKOFF_CEILING_SECONDS)) - 1
    return BACKOFF_CEILING_SECONDS[index]


def backoff_seconds(failed_attempt_no: int, *, jitter: random.Random) -> float:
    """The delay before the replacement attempt, in seconds. Full jitter under the ceiling.

    FULL jitter means the whole interval `[0, ceiling]`, not `[ceiling/2, ceiling]` and not
    `ceiling` itself. D-017 says "z pełnym jitterem" and the difference is the entire point of
    having it: with a fixed delay, every job that lost its lease in the same outage comes back in
    the same second, which is the thundering herd the decision names.

    `jitter` is required and has no default. A default would be a module-level generator, and
    then no test could tell full jitter from no jitter at all — the mechanism would be
    unmeasurable, which in this repository means it does not exist.
    """
    return jitter.uniform(0.0, backoff_ceiling_seconds(failed_attempt_no))
