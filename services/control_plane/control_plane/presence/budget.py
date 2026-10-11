"""What a delegation has spent, counted from what happened (P4).

EVERY NUMBER HERE IS A QUERY, NEVER A COUNTER. A counter in the handler resets when the worker
restarts, and a project whose worker restarted twice would get three times its budget. The rows
survive the restart, so the rows are what counts. This is the same argument `sessions/store.py`
makes about session budgets, one level up.

WHAT IS COUNTED, AND WHY EACH IS COUNTED WHERE IT IS:

  * **model turns**: wake-ups of this project that opened a session. That is the number the
    roadmap asks to bound, because a model turn is the expensive thing, and `wakeups` is the one
    table that knows both that the model ran and why;
  * **publications**: attempts that REACHED THE PLATFORM, from `execution_attempts`, exactly as
    `pilot/publication.py::attempts_since` counts them and for the reason D-035 records: a
    refusal is a page load Reddit saw, so it costs a slot. Counting successes instead would let
    a project that fails ten times in a row keep trying at full speed;
  * **plus what is ALREADY IN THE QUEUE and has never been attempted**. Without this the count
    would only see publications that already happened, and a tick could authorise a seventh text
    against a limit of six simply because the first six had not gone out yet. A queue entry is a
    publication this system has committed to, so it costs its slot at the moment it is written.
    Rows that HAVE an attempt are left out here, because the attempt already counted them.

THE WINDOW IS A ROLLING ONE, not a calendar day. "Six a day" that resets at midnight permits
twelve between 23:00 and 01:00, and the platform does not care what our clock says.

WHAT THIS MODULE DOES NOT DO: decide. It reports what was spent and what the delegation allows;
`approval.py` refuses, and it does so in the same transaction as the write it is refusing, so
two ticks cannot both see room for the last publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import KIND_OWN_POST

from .scope import Budget
from .store import DelegationRow

#: The states that mean the attempt reached the platform. SUBMIT_STARTED is included because at
#: that point the comment may already exist, and UNKNOWN because nobody knows that it does not.
TOUCHED_THE_PLATFORM = ("SUBMIT_STARTED", "CONFIRMED", "UNKNOWN")

DAY = timedelta(hours=24)
HOUR = timedelta(hours=1)

#: What a delegation permits when it named no limit of that kind. Decided by the coordinator on
#: 2026-09-19 and deliberately the SAME number everywhere: at the decision (`approval.py`) and at
#: the queue (`pilot/publisher.py`).
#:
#: WHY A DEFAULT AND NOT "NO LIMIT". `scope.py` reads an absent key as "no limit of that kind",
#: which is the right reading of a SENTENCE. It is the wrong reading of a PERMISSION TO PUBLISH:
#: unlimited there means as fast as the machine can post, which is the single thing that makes an
#: account look like a bot, and nobody signing a scope intends it. So the absent key means the
#: conservative pilot pace instead, and the delegation panel shows the effective numbers even when
#: they are the defaults, so a person never signs a scope whose real limit is invisible to them.
DEFAULT_PUBLICATIONS_PER_HOUR = 1
DEFAULT_PUBLICATIONS_PER_DAY = 6

#: Własnych postów na dobę, gdy delegacja nie mówi inaczej. JEDEN, i to nie jest ostrożność bez
#: powodu: własny post zabiera uwagę całej społeczności, a konto, które wrzuca ich kilka dziennie,
#: jest tym, co moderacja banuje najszybciej. Kto chce więcej, wpisuje liczbę do podpisu.
DEFAULT_OWN_POSTS_PER_DAY = 1


@dataclass(frozen=True)
class Limits:
    """The numbers that actually bind, with a note of which of them nobody chose."""

    publications_per_hour: int
    publications_per_day: int
    own_posts_per_day: int
    #: `None` means the model may be woken as often as there is something to do. Unlike a
    #: publication, a turn touches nothing outside this system, so "no limit" is a coherent thing
    #: to have signed and it is left as signed.
    model_turns_per_day: int | None
    #: Which limits came from the default rather than from the signature. The delegation panel
    #: shows them differently, so a person can see what they did not choose.
    defaults_used: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "publikacje_godzina": self.publications_per_hour,
            "publikacje_doba": self.publications_per_day,
            "wlasne_posty_doba": self.own_posts_per_day,
            "tury_modelu_doba": self.model_turns_per_day,
            "domyslne": list(self.defaults_used),
        }


def effective_limits(delegation: DelegationRow | Budget) -> Limits:
    """THE one reading of what a delegation permits per window.

    One function and not two readings of the same field: the decision path and the queue have to
    agree, and they stopped agreeing the moment each of them had its own `if budget.x is None`.
    Takes a delegation, or just its budget for callers that have nothing else.
    """
    budget = delegation.budget if isinstance(delegation, DelegationRow) else delegation
    defaults: list[str] = []
    per_hour = budget.publications_per_hour
    if per_hour is None:
        per_hour = DEFAULT_PUBLICATIONS_PER_HOUR
        defaults.append("publikacje_godzina")
    per_day = budget.publications_per_day
    if per_day is None:
        per_day = DEFAULT_PUBLICATIONS_PER_DAY
        defaults.append("publikacje_doba")
    own_posts = budget.own_posts_per_day
    if own_posts is None:
        own_posts = DEFAULT_OWN_POSTS_PER_DAY
        defaults.append("wlasne_posty_doba")
    return Limits(
        publications_per_hour=per_hour,
        publications_per_day=per_day,
        own_posts_per_day=own_posts,
        model_turns_per_day=budget.model_turns_per_day,
        defaults_used=tuple(defaults),
    )


@dataclass(frozen=True)
class Spent:
    """What this delegation used in the rolling windows, against the limits that bind.

    `limits`, not `budget`: what binds is what `effective_limits` says, defaults included. A
    `Spent` carrying the raw budget would let a caller re-derive the limits itself, which is the
    second reading this design exists to prevent.
    """

    model_turns_today: int
    publications_today: int
    publications_this_hour: int
    own_posts_today: int
    limits: Limits
    #: When the oldest publication of each window leaves it, so a refusal can say WHEN (27.09:
    #: the agent planned its return six minutes before the hour freed and was refused again).
    hour_frees_at: datetime | None = None
    day_frees_at: datetime | None = None

    def _frees(self, moment: datetime | None) -> str:
        return f", następne miejsce o {moment:%H:%M} UTC" if moment is not None else ""

    def exhausted(self, *, include_publications: bool = True) -> str | None:
        """The FIRST limit that is spent, named, or `None`.

        Returns which one rather than a boolean: "budget exhausted" tells a person nothing they
        can act on, while "six publications in twenty four hours" tells them when to come back.
        """
        if (
            self.limits.model_turns_per_day is not None
            and self.model_turns_today >= self.limits.model_turns_per_day
        ):
            return (
                f"tury modelu: {self.model_turns_today} z {self.limits.model_turns_per_day} "
                "w ostatniej dobie"
            )
        if not include_publications:
            return None
        return self.publication_blocked()

    def own_post_blocked(self) -> str | None:
        """Limit WŁASNYCH POSTÓW, osobno od odpowiedzi.

        Pytany tylko wtedy, gdy propozycja jest własnym postem. Odpowiedź pod cudzym wątkiem nie
        ma czekać na limit, który jej nie dotyczy, a własny post ma zostać zatrzymany także
        wtedy, gdy ogólny limit publikacji jest jeszcze daleki.
        """
        if self.own_posts_today >= self.limits.own_posts_per_day:
            return (
                f"własne posty: {self.own_posts_today} z {self.limits.own_posts_per_day} "
                "w ostatniej dobie"
            )
        return self.publication_blocked()

    def publication_blocked(self) -> str | None:
        """Only the publication limits. A turn may still run when the model budget is spent for
        publishing but not for thinking, and the two questions are asked at different moments."""
        if (
            self.limits.publications_per_day is not None
            and self.publications_today >= self.limits.publications_per_day
        ):
            return (
                f"publikacje: {self.publications_today} z {self.limits.publications_per_day} "
                f"w ostatniej dobie{self._frees(self.day_frees_at)}"
            )
        if (
            self.limits.publications_per_hour is not None
            and self.publications_this_hour >= self.limits.publications_per_hour
        ):
            return (
                f"publikacje: {self.publications_this_hour} z "
                f"{self.limits.publications_per_hour} w ostatniej godzinie"
                f"{self._frees(self.hour_frees_at)}"
            )
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "tury_modelu_doba": self.model_turns_today,
            "publikacje_doba": self.publications_today,
            "publikacje_godzina": self.publications_this_hour,
            "wlasne_posty_doba": self.own_posts_today,
            "limit_tur_modelu": self.limits.model_turns_per_day,
            "limit_publikacji_doba": self.limits.publications_per_day,
            "limit_publikacji_godzina": self.limits.publications_per_hour,
            "limity_domyslne": list(self.limits.defaults_used),
        }


async def spent(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    accounts: tuple[UUID, ...],
    limits: Limits,
    now: datetime | None = None,
) -> Spent:
    """Read the three counters ON THIS CONNECTION.

    A connection rather than a pool, so a caller can run the count and the write it authorises
    inside ONE transaction. That is the whole reason this is a function and not a store method:
    two ticks counting on separate connections both see room for the last publication.
    """
    moment = now or datetime.now(UTC)

    model_turns = await conn.fetchval(
        "SELECT count(*) FROM wakeups WHERE organization_id = $1 AND project_id = $2 "
        "AND session_id IS NOT NULL AND created_at >= $3",
        organization_id,
        project_id,
        moment - DAY,
    )

    # Publications are counted through the ACCOUNTS the delegation names, because an attempt
    # knows its revision and a revision knows its proposal's account. There is no project column
    # on `execution_attempts` and adding one would mean a second answer to "whose publication is
    # this" living next to the account that actually made it.
    oldest_day: list[datetime] = []
    oldest_hour: list[datetime] = []
    if accounts:
        per_day, per_hour, first_day, first_hour = await _publication_counts(
            conn, organization_id=organization_id, accounts=list(accounts), moment=moment
        )
        oldest_day += [first_day] if first_day else []
        oldest_hour += [first_hour] if first_hour else []
    else:
        per_day, per_hour = 0, 0

    waiting = await _queued_counts(
        conn, organization_id=organization_id, project_id=project_id, moment=moment
    )
    per_day += waiting[0]
    per_hour += waiting[1]
    oldest_day += [waiting[2]] if waiting[2] else []
    oldest_hour += [waiting[3]] if waiting[3] else []

    # Własne posty liczone z KOLEJKI po rodzaju (0023). Nie z prób wykonania, bo post czeka w
    # kolejce tak samo jak odpowiedź, a limit ma go zatrzymać, ZANIM pójdzie, nie po fakcie.
    own_posts = await conn.fetchval(
        """
        SELECT count(*) FROM publication_requests
         WHERE organization_id = $1 AND project_id = $2 AND kind = $4
           AND coalesce(published_at, queued_at) >= $3
           AND status <> 'SKIPPED'
        """,
        organization_id,
        project_id,
        moment - DAY,
        # Stała, nie literał: to samo słowo stoi w CHECK migracji, w kolejce i w skaner
        # publikacji, i ma mieć jedno źródło (`pilot/publication.py`).
        KIND_OWN_POST,
    )

    return Spent(
        model_turns_today=int(model_turns or 0),
        publications_today=per_day,
        publications_this_hour=per_hour,
        own_posts_today=int(own_posts or 0),
        limits=limits,
        hour_frees_at=min(oldest_hour) + HOUR if oldest_hour else None,
        day_frees_at=min(oldest_day) + DAY if oldest_day else None,
    )


async def _queued_counts(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    moment: datetime,
) -> tuple[int, int, datetime | None, datetime | None]:
    """Queue entries of this project that have never been attempted, by when they were queued.

    By `queued_at` and not by `now()`, so a request that has been waiting for two days stops
    counting against today: the window is the same rolling one everything else here uses, and a
    row older than it will be paced by the publisher rather than by the budget.
    """
    row = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE queued_at >= $3) AS per_day,
               count(*) FILTER (WHERE queued_at >= $4) AS per_hour,
               min(queued_at) FILTER (WHERE queued_at >= $3) AS first_day,
               min(queued_at) FILTER (WHERE queued_at >= $4) AS first_hour
          FROM publication_requests
         WHERE organization_id = $1 AND project_id = $2
           AND attempt_id IS NULL
           AND status IN ('QUEUED', 'PUBLISHING')
        """,
        organization_id,
        project_id,
        moment - DAY,
        moment - HOUR,
    )
    return int(row["per_day"]), int(row["per_hour"]), row["first_day"], row["first_hour"]


async def _publication_counts(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    accounts: list[UUID],
    moment: datetime,
) -> tuple[int, int, datetime | None, datetime | None]:
    row = await conn.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE a.started_at >= $3) AS per_day,
            count(*) FILTER (WHERE a.started_at >= $4) AS per_hour,
            min(a.started_at) FILTER (WHERE a.started_at >= $3) AS first_day,
            min(a.started_at) FILTER (WHERE a.started_at >= $4) AS first_hour
          FROM execution_attempts a
          JOIN action_proposal_revisions r
            ON r.id = a.proposal_revision_id AND r.organization_id = a.organization_id
          JOIN action_proposals p
            ON p.id = r.proposal_id AND p.organization_id = r.organization_id
         WHERE a.organization_id = $1
           AND p.social_account_id = ANY($2::uuid[])
           AND a.status = ANY($5::text[])
        """,
        organization_id,
        accounts,
        moment - DAY,
        moment - HOUR,
        list(TOUCHED_THE_PLATFORM),
    )
    return int(row["per_day"]), int(row["per_hour"]), row["first_day"], row["first_hour"]
