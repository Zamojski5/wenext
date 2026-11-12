"""What the publisher asks again, right before a SCHEDULED reply goes out (v11, 13.10).

"Send all" approves a batch with one click and spaces it out over hours (`presence/batch_plan.py`),
so a reply may wait for its turn long after the person looked at its thread. The person agreed to
the TEXT, exactly as it was approved (the digest is bound, nothing here ever edits it); they did not
agree to post it into a conversation that is no longer there. So at its moment, before anything is
written, the publisher looks again, and a scheduled reply is either sent exactly as approved or
SKIPPED with a reason a person can read in the card's "Scheduled" list.

MOST OF THE LOOK IS ALREADY THE PUBLISHER'S, for every request, and is reused as it stands
(`pilot/publisher.py::_publish_request`, `_publish_x`):

  * the post or thread is gone, locked or archived, or the comment it answers is gone: SKIPPED
    (`wątek zniknął z Reddita`, `wątek zablokowany`, `komentarz docelowy ... zniknął`, `post X ...
    jest niedostępny`);
  * our account already answered there (on the page, or in our own queue): SKIPPED (`na tym wątku
    jest już komentarz naszego konta`, `pod tym postem X jest już nasza odpowiedź`);
  * the account's session is down or cannot be checked: the request STAYS QUEUED with the reason
    (`brak sesji X konta`, `nie udało się sprawdzić sesji`, `sesja czytnika niedostępna`).

WHAT THIS MODULE ADDS, each only for a request that has a `not_before` (one a batch scheduled; a
reply approved on its own goes out when the publisher gets to it and has never waited on purpose):

  * THE THREAD IS NOW STALE: an X post older than `MAX_AGE_X` (8 hours) or a Reddit thread older
    than `MAX_AGE_REDDIT` (48 hours) at the moment of sending is SKIPPED, `wątek jest już za stary`.
    Both are configurable (`ENV_MAX_AGE_X_HOURS`, `ENV_MAX_AGE_REDDIT_HOURS`), and a value that is
    not a positive number falls back to the default rather than switching the check off;
  * A SCHEDULED REPLY WHOSE SESSION IS DOWN ON REDDIT STAYS QUEUED instead of stopping the queue
    (the publisher's `BLOCKED` is for a session that belongs to SOMEBODY ELSE, which a retry makes
    worse; a session that is simply not there is the person's to restore, and the request waits);
  * A REQUEST THAT WAITS FOR A REASON IS PUSHED `RETRY_AFTER` LATER (`PublicationStore.set_status`),
    so it does not sit at the head of the queue and starve the others (an X reply waiting for the X
    session must not hold back a Reddit reply whose session is fine).

`reason_code` turns the publisher's sentence into a stable code for the card (`room.send_status`
says `powod_kod`), because the sentences are Polish prose and the card speaks two languages; a
sentence it does not recognise has no code and the card shows the Polish one as it is.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .publication import SCHEDULED_RETRY_AFTER, PublicationRequest

ENV_MAX_AGE_X_HOURS = "WENEXT_MAX_TARGET_AGE_X_HOURS"
ENV_MAX_AGE_REDDIT_HOURS = "WENEXT_MAX_TARGET_AGE_REDDIT_HOURS"
#: THE FRESHNESS LIMIT OF A REPLY, ONE DEFINITION for three places: a scheduled request is checked
#: against it at its moment (`stale_detail`, here), a waiting draft that is older is expired and
#: leaves the card (`opportunities/freshness.py`), and the sort of the card prefers what is
#: younger (`opportunities/ranking.py`). An X conversation is over in hours (8, the founder's
#: number, 13.10), a Reddit thread lives for days (48). NOT the same number as
#: `presence/batch_plan.FRESH_X` (6 hours), which only decides what a batch sends FIRST, never
#: whether it sends.
MAX_AGE_X = timedelta(hours=8)
MAX_AGE_REDDIT = timedelta(hours=48)
#: A scheduled request that waits for a reason is looked at again after this (the publisher's own
#: chain interval), and in the meantime it is not the head of the queue.
RETRY_AFTER = SCHEDULED_RETRY_AFTER

# Stable reasons, for the card (`ref.`-style words in its dictionary, `rs.<code>`).
REASON_STALE = "ZA_STARY"
REASON_GONE = "ZNIKNAL"
REASON_LOCKED = "ZABLOKOWANY"
REASON_REPLIED = "JUZ_ODPOWIEDZIANO"
REASON_SESSION = "SESJA"
REASON_SESSION_UNKNOWN = "SESJA_NIEPEWNA"
REASON_OTHER_ACCOUNT = "INNE_KONTO"
REASON_PACE = "TEMPO"
REASON_CAPSULE = "KAPSULA"

#: The first match wins, so the specific sentences come before the general ones.
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("za stary", REASON_STALE),
    ("zablokowany", REASON_LOCKED),
    ("zarchiwizowany", REASON_LOCKED),
    ("jest już komentarz naszego konta", REASON_REPLIED),
    ("jest już nasza odpowiedź", REASON_REPLIED),
    ("już stoi jako nasz", REASON_REPLIED),
    ("zniknął", REASON_GONE),
    ("jest niedostępny", REASON_GONE),
    ("brak wątku", REASON_GONE),
    ("to nie jest wylogowanie", REASON_SESSION_UNKNOWN),
    ("nie udało się sprawdzić sesji", REASON_SESSION_UNKNOWN),
    ("sesja w kapsule to", REASON_OTHER_ACCOUNT),
    ("jest zalogowana na x jako", REASON_OTHER_ACCOUNT),
    ("brak sesji", REASON_SESSION),
    ("sesja czytnika niedostępna", REASON_SESSION),
    ("tempo:", REASON_PACE),
    ("kapsuła", REASON_CAPSULE),
    ("brak aktuatora", REASON_CAPSULE),
)


def reason_code(detail: str | None) -> str | None:
    """The stable code of a publisher sentence, or `None` for one this module does not know."""
    text = (detail or "").lower()
    for needle, code in _PATTERNS:
        if needle in text:
            return code
    return None


def _hours(raw: str | None, default: timedelta) -> timedelta:
    try:
        hours = float(str(raw).strip())
        return timedelta(hours=hours) if math.isfinite(hours) and hours > 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def max_age(on_x: bool, environ: Mapping[str, str] | None = None) -> timedelta:
    """The oldest a target may be at the moment a scheduled reply is sent."""
    source = os.environ if environ is None else environ
    if on_x:
        return _hours(source.get(ENV_MAX_AGE_X_HOURS), MAX_AGE_X)
    return _hours(source.get(ENV_MAX_AGE_REDDIT_HOURS), MAX_AGE_REDDIT)


#: X's status ids are snowflakes: the milliseconds since this epoch are in the high bits.
_X_EPOCH_MS = 1288834974657


def x_created_at(published_at: str | None, status_id: str) -> datetime | None:
    """When an X post was written: the page's own timestamp, else the snowflake in its id."""
    if published_at:
        try:
            stamp = datetime.fromisoformat(published_at.strip().replace("Z", "+00:00"))
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(((int(status_id) >> 22) + _X_EPOCH_MS) / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def reddit_created_at(post: Mapping[str, Any]) -> datetime | None:
    """When a Reddit post was written, from the thread payload read a moment ago, or `None`."""
    try:
        return datetime.fromtimestamp(float(post["created_utc"]), tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def stale_detail(
    request: PublicationRequest,
    *,
    on_x: bool,
    created_at: datetime | None,
    now: datetime,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The sentence that SKIPS a scheduled reply whose target went stale, or `None`.

    Only for a request that was scheduled (`not_before` set), only for a reply (an own post has no
    thread), and only when the target's age is known: a target whose age cannot be established is
    not called stale on a guess.
    """
    if request.not_before is None or created_at is None:
        return None
    limit = max_age(on_x, environ)
    age = now - created_at
    if age <= limit:
        return None
    hours = int(age.total_seconds() // 3600)
    allowed = int(limit.total_seconds() // 3600)
    return (
        f"wątek jest już za stary na odpowiedź: ma {hours} h, a na terminie wysyłki "
        f"dopuszczam najwyżej {allowed} h ({'X' if on_x else 'Reddit'}); "
        "tekst nie został zmieniony, wniosek pominięty"
    )
