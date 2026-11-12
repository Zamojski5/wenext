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

PER PLATFORM AND PER ACCOUNT (v11, 13.10). One bucket for the whole project was wrong in the one
place that matters: X replies at six an hour waited behind Reddit's one an hour, and Reddit's slow
pace was spent by whoever got there first. A signed budget can now carry `platform_limits` and
`account_limits` (`scope.AccountCaps`: replies per hour, replies per day, own posts per day), and
`limits_for` is the ONE reading of what binds a given account:

    account entry  ->  platform entry  ->  the project's own number (signed, else the default)

with replies and own posts counted SEPARATELY (an own post is no reply and a reply is no own post).
The old totals (`publications_per_hour`, `publications_per_day`) stay in two roles: with NO
per-account limit signed they are the one shared bucket they always were (every counter below
and every message is the one it was), and with one signed they are an optional OVERALL CEILING
over every account together, binding only when signed (an unsigned total is not a hidden cap on a
scope that spelled out its numbers). An account nobody gave a number gets the product's default
pace, never "unlimited".

WHAT THIS MODULE DOES NOT DO: decide. It reports what was spent and what the delegation allows;
`approval.py` refuses, and it does so in the same transaction as the write it is refusing, so
two ticks cannot both see room for the last publication.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import KIND_OWN_POST

from .scope import AccountCaps, Budget
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
    #: v11: what was signed per platform (`"X"`, `"REDDIT"`) and per account. Both empty is the
    #: legacy reading: ONE bucket for the project, the three numbers above.
    platforms: tuple[tuple[str, AccountCaps], ...] = ()
    accounts: tuple[tuple[UUID, AccountCaps], ...] = ()

    @property
    def per_account(self) -> bool:
        """Whether limits are per platform / per account (otherwise: one shared bucket)."""
        return bool(self.platforms or self.accounts)

    def overall_per_hour(self) -> int | None:
        """The ceiling over EVERY account per hour, or `None` when none binds. Legacy: the one
        bucket. Per account: only a total somebody signed (an unsigned one is not a hidden cap)."""
        if self.per_account and "publikacje_godzina" in self.defaults_used:
            return None
        return self.publications_per_hour

    def overall_per_day(self) -> int | None:
        if self.per_account and "publikacje_doba" in self.defaults_used:
            return None
        return self.publications_per_day

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "publikacje_godzina": self.publications_per_hour,
            "publikacje_doba": self.publications_per_day,
            "wlasne_posty_doba": self.own_posts_per_day,
            "tury_modelu_doba": self.model_turns_per_day,
            "domyslne": list(self.defaults_used),
        }
        if self.per_account:
            # Only when signed, so the shape older readers (the dashboard's) know is unchanged.
            out["wedlug_kont"] = True
            out["platformy"] = {name: _caps_json(caps) for name, caps in self.platforms}
            out["konta"] = {str(account): _caps_json(caps) for account, caps in self.accounts}
        return out


def _caps_json(caps: AccountCaps) -> dict[str, int]:
    """The card's words for `AccountCaps`: per hour, per day (replies), own posts a day."""
    return {
        key: value
        for key, value in (
            ("godzina", caps.replies_per_hour),
            ("doba", caps.replies_per_day),
            ("wlasne_doba", caps.own_posts_per_day),
        )
        if value is not None
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
        platforms=budget.platform_limits,
        accounts=budget.account_limits,
    )


@dataclass(frozen=True)
class AccountLimits:
    """What binds ONE account: replies per hour and per day, own posts per day.

    `sources` says which level each number came from (`account`, `platform` or `project`: the
    project's own number, signed or default) so the panel can show what the person did not set.
    `shared` is the legacy reading: no per-account limit was signed, so these are the project's
    totals and they are counted over EVERY account together, not per account.
    """

    account_id: UUID | None
    platform: str | None
    replies_per_hour: int
    replies_per_day: int
    own_posts_per_day: int
    sources: tuple[tuple[str, str], ...] = ()
    shared: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "godzina": self.replies_per_hour,
            "doba": self.replies_per_day,
            "wlasne_doba": self.own_posts_per_day,
            "zrodlo": dict(self.sources),
            "wspolny_kosz": self.shared,
        }


def limits_for(
    source: DelegationRow | Budget | Limits, account_id: UUID | None, platform: str | None = None
) -> AccountLimits:
    """THE one reading of what binds an account: account entry, then platform entry, then the
    project's own number. Takes what the caller has (a delegation, a budget or limits already
    resolved) so the decision, the queue and the screens cannot read it differently.

    With no per-account limit signed the answer is the project's totals and `shared` is true.
    """
    limits = source if isinstance(source, Limits) else effective_limits(source)
    wanted = (platform or "").upper()
    if not limits.per_account:
        return AccountLimits(
            account_id=account_id,
            platform=wanted or None,
            replies_per_hour=limits.publications_per_hour,
            replies_per_day=limits.publications_per_day,
            own_posts_per_day=limits.own_posts_per_day,
            sources=(
                ("godzina", "project"),
                ("doba", "project"),
                ("wlasne_doba", "project"),
            ),
            shared=True,
        )
    by_account = dict(limits.accounts).get(account_id) if account_id is not None else None
    by_platform = dict(limits.platforms).get(wanted) if wanted else None
    chosen: dict[str, int] = {}
    sources: list[tuple[str, str]] = []
    for name, field, fallback in (
        ("godzina", "replies_per_hour", limits.publications_per_hour),
        ("doba", "replies_per_day", limits.publications_per_day),
        ("wlasne_doba", "own_posts_per_day", limits.own_posts_per_day),
    ):
        value, level = fallback, "project"
        for where, caps in (("account", by_account), ("platform", by_platform)):
            if caps is not None and getattr(caps, field) is not None:
                value, level = getattr(caps, field), where
                break
        chosen[field] = value
        sources.append((name, level))
    return AccountLimits(
        account_id=account_id,
        platform=wanted or None,
        replies_per_hour=chosen["replies_per_hour"],
        replies_per_day=chosen["replies_per_day"],
        own_posts_per_day=chosen["own_posts_per_day"],
        sources=tuple(sources),
    )


def account_label(platform: str | None, handle: str | None) -> str:
    """ "X @name", "Reddit u/name": how a refusal names an account."""
    name = (handle or "").strip().lstrip("@")
    kind = (platform or "").upper()
    if kind == "REDDIT":
        return f"Reddit u/{name.removeprefix('u/')}" if name else "Reddit"
    if kind == "X":
        return f"X @{name}" if name else "X"
    return f"{platform or 'konto'} {name}".strip()


# ---------------------------------------------------------------------------
# Ledgers: the moments of everything that counts, by what it counts against
# ---------------------------------------------------------------------------

#: A ledger is a list of moments. Which one a publication is written into:
#: ALL (every publication of the project), OWN (the project's own posts), and per account
#: ("reply", id) and ("own", id). A cap says which ledger it counts.
LEDGER_ALL: tuple[Any, ...] = ("all",)
LEDGER_OWN: tuple[Any, ...] = ("own",)


def reply_ledger(account_id: UUID) -> tuple[Any, ...]:
    return ("reply", account_id)


def own_ledger(account_id: UUID) -> tuple[Any, ...]:
    return ("own", account_id)


def member_ledgers(account_id: UUID | None, *, own_post: bool) -> tuple[tuple[Any, ...], ...]:
    """The ledgers ONE publication is written into, whichever of them a cap counts."""
    out: list[tuple[Any, ...]] = [LEDGER_ALL]
    if own_post:
        out.append(LEDGER_OWN)
    if account_id is not None:
        out.append(own_ledger(account_id) if own_post else reply_ledger(account_id))
    return tuple(out)


@dataclass(frozen=True)
class Cap:
    """One rule over one ledger: at most `per_hour` in any rolling hour and `per_day` in any
    rolling day (`None`: that window is not limited by this rule). `what` names it in a refusal."""

    ledger: tuple[Any, ...]
    per_hour: int | None
    per_day: int | None
    what: str


def caps_for(
    source: DelegationRow | Budget | Limits,
    *,
    account_id: UUID | None,
    platform: str | None,
    own_post: bool,
    label: str = "",
) -> tuple[Cap, ...]:
    """Every rule a publication from this account has to satisfy.

    Legacy (one bucket): the project's totals over ALL publications, and for an own post the
    project's own-post cap over the day. Per account: the overall ceiling when one was signed,
    then the account's own rule (replies per hour and day, or own posts per day).
    """
    limits = source if isinstance(source, Limits) else effective_limits(source)
    if not limits.per_account:
        caps = [
            Cap(LEDGER_ALL, limits.publications_per_hour, limits.publications_per_day, "projekt")
        ]
        if own_post:
            caps.append(Cap(LEDGER_OWN, None, limits.own_posts_per_day, "własne posty projektu"))
        return tuple(caps)
    caps = []
    per_hour, per_day = limits.overall_per_hour(), limits.overall_per_day()
    if per_hour is not None or per_day is not None:
        caps.append(Cap(LEDGER_ALL, per_hour, per_day, "wszystkie konta razem"))
    own = limits_for(limits, account_id, platform)
    who = label or account_label(platform, None)
    if account_id is None:
        return tuple(caps)
    if own_post:
        caps.append(
            Cap(own_ledger(account_id), None, own.own_posts_per_day, f"{who}: własne posty")
        )
    else:
        caps.append(
            Cap(
                reply_ledger(account_id),
                own.replies_per_hour,
                own.replies_per_day,
                f"{who}: odpowiedzi",
            )
        )
    return tuple(caps)


async def attempt_ledger(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    accounts: Sequence[UUID],
    now: datetime,
) -> dict[tuple[Any, ...], list[datetime]]:
    """The attempts that REACHED THE PLATFORM in the last 24 hours, by ledger: ALL, and per
    account ("reply", id) or ("own", id) by the kind of the request they served (a request-less
    attempt is a reply). Real attempts only: no queue entry, which is what the publisher's pace
    counts (a request must not be paced by itself)."""
    ledger: dict[tuple[Any, ...], list[datetime]] = {LEDGER_ALL: [], LEDGER_OWN: []}
    if not accounts:
        return ledger
    rows = await conn.fetch(
        """
        SELECT p.social_account_id AS account_id, coalesce(pr.kind, 'REPLY') AS kind,
               a.started_at AS at
          FROM execution_attempts a
          JOIN action_proposal_revisions r
            ON r.id = a.proposal_revision_id AND r.organization_id = a.organization_id
          JOIN action_proposals p
            ON p.id = r.proposal_id AND p.organization_id = r.organization_id
          LEFT JOIN publication_requests pr
            ON pr.organization_id = a.organization_id
           AND pr.proposal_revision_id = a.proposal_revision_id
         WHERE a.organization_id = $1
           AND p.social_account_id = ANY($2::uuid[])
           AND a.status = ANY($3::text[])
           AND a.started_at >= $4
        """,
        organization_id,
        list(accounts),
        list(TOUCHED_THE_PLATFORM),
        now - DAY,
    )
    for row in rows:
        ledger[LEDGER_ALL].append(row["at"])
        if row["kind"] == KIND_OWN_POST:
            ledger[LEDGER_OWN].append(row["at"])
            ledger.setdefault(own_ledger(row["account_id"]), []).append(row["at"])
        else:
            ledger.setdefault(reply_ledger(row["account_id"]), []).append(row["at"])
    return ledger


def pace_message(
    caps: Sequence[Cap], ledger: dict[tuple[Any, ...], Sequence[datetime]], now: datetime
) -> str | None:
    """The publisher's pace over real attempts: the first rule whose rolling hour or day is
    already full, in a sentence for the queue's detail line, or `None` when the request may go."""
    for cap in caps:
        events = ledger.get(cap.ledger, ())
        for window, limit, word in (
            (HOUR, cap.per_hour, "w ostatniej godzinie"),
            (DAY, cap.per_day, "w ostatniej dobie"),
        ):
            if limit is None:
                continue
            used = sum(1 for at in events if now - window <= at <= now)
            if used >= limit:
                return f"tempo: {cap.what}: {used}/{limit} {word}"
    return None


async def pace_blocked(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    revision_id: UUID,
    own_post: bool,
    limits: Limits,
    accounts: Sequence[UUID],
    now: datetime,
) -> str | None:
    """Whether the PUBLISHER should hold this request for its account's pace, and why.

    Only for limits per account: the request's own account and platform are read from the
    revision's proposal (never from anything the request carries), the account's rules are
    `caps_for`, and the count is of real attempts (`attempt_ledger`).
    """
    row = await conn.fetchrow(
        """
        SELECT p.social_account_id AS account_id, a.platform, a.external_account_id
          FROM action_proposal_revisions r
          JOIN action_proposals p
            ON p.id = r.proposal_id AND p.organization_id = r.organization_id
          JOIN social_accounts a
            ON a.id = p.social_account_id AND a.organization_id = p.organization_id
         WHERE r.organization_id = $1 AND r.id = $2
        """,
        organization_id,
        revision_id,
    )
    if row is None:
        return "tempo: nie wiadomo, z którego konta idzie ten wniosek"
    caps = caps_for(
        limits,
        account_id=row["account_id"],
        platform=str(row["platform"]),
        own_post=own_post,
        label=account_label(str(row["platform"]), str(row["external_account_id"])),
    )
    ledger = await attempt_ledger(conn, organization_id=organization_id, accounts=accounts, now=now)
    return pace_message(caps, ledger, now)


async def committed_ledger(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    accounts: Sequence[UUID],
    now: datetime,
) -> dict[tuple[Any, ...], list[datetime]]:
    """The moments of everything that counts against the limits, by ledger.

    EVERY NUMBER IS A QUERY, like `spent`, and the same rows: attempts that reached the platform
    (`TOUCHED_THE_PLATFORM`), and the project's queue entries that were never attempted, by the
    MOMENT each may go out (`not_before`, else `queued_at`); from the last 24 hours on, with no
    upper bound (what is committed for later is committed). Each is written into ALL, and into
    the ledger of its account by kind: ("reply", account) or ("own", account); own posts also go
    into OWN, by `published_at`, else the moment, whatever their status short of SKIPPED.
    """
    since = now - DAY
    ledger = await attempt_ledger(conn, organization_id=organization_id, accounts=accounts, now=now)
    # Own posts are written below from their REQUESTS (status short of SKIPPED, by published_at),
    # which already cover the ones that were attempted: the attempts' own entries would count each
    # of them twice.
    for key in [key for key in ledger if key[0] == "own"]:
        del ledger[key]
    ledger[LEDGER_OWN] = []
    queued = await conn.fetch(
        """
        SELECT p.social_account_id AS account_id, pr.kind AS kind,
               coalesce(pr.not_before, pr.queued_at) AS at
          FROM publication_requests pr
          JOIN action_proposal_revisions r
            ON r.id = pr.proposal_revision_id AND r.organization_id = pr.organization_id
          JOIN action_proposals p
            ON p.id = r.proposal_id AND p.organization_id = r.organization_id
         WHERE pr.organization_id = $1 AND pr.project_id = $2
           AND pr.attempt_id IS NULL AND pr.status = ANY($3::text[])
           AND coalesce(pr.not_before, pr.queued_at) >= $4
        """,
        organization_id,
        project_id,
        ["QUEUED", "PUBLISHING"],
        since,
    )
    for row in queued:
        ledger[LEDGER_ALL].append(row["at"])
        if row["kind"] != KIND_OWN_POST:
            ledger.setdefault(reply_ledger(row["account_id"]), []).append(row["at"])
    own_rows = await conn.fetch(
        """
        SELECT p.social_account_id AS account_id,
               coalesce(pr.published_at, pr.not_before, pr.queued_at) AS at
          FROM publication_requests pr
          JOIN action_proposal_revisions r
            ON r.id = pr.proposal_revision_id AND r.organization_id = pr.organization_id
          JOIN action_proposals p
            ON p.id = r.proposal_id AND p.organization_id = r.organization_id
         WHERE pr.organization_id = $1 AND pr.project_id = $2 AND pr.kind = $3
           AND pr.status <> 'SKIPPED'
           AND coalesce(pr.published_at, pr.not_before, pr.queued_at) >= $4
        """,
        organization_id,
        project_id,
        KIND_OWN_POST,
        since,
    )
    for row in own_rows:
        ledger[LEDGER_OWN].append(row["at"])
        ledger.setdefault(own_ledger(row["account_id"]), []).append(row["at"])
    return ledger


@dataclass(frozen=True)
class AccountSpent:
    """What ONE account used, against what binds it (`limits_for`). Replies and own posts apart."""

    account_id: UUID
    platform: str
    handle: str
    limits: AccountLimits
    replies_today: int
    replies_this_hour: int
    own_posts_today: int
    hour_frees_at: datetime | None = None
    day_frees_at: datetime | None = None
    own_frees_at: datetime | None = None

    @property
    def label(self) -> str:
        return account_label(self.platform, self.handle)

    def reply_blocked(self) -> str | None:
        """The first reply limit of THIS account that is spent, named with the account."""
        if self.replies_today >= self.limits.replies_per_day:
            return (
                f"{self.label}: odpowiedzi {self.replies_today} z {self.limits.replies_per_day} "
                f"w ostatniej dobie{_frees(self.day_frees_at)}"
            )
        if self.replies_this_hour >= self.limits.replies_per_hour:
            return (
                f"{self.label}: odpowiedzi {self.replies_this_hour} z "
                f"{self.limits.replies_per_hour} w ostatniej godzinie{_frees(self.hour_frees_at)}"
            )
        return None

    def own_post_blocked(self) -> str | None:
        if self.own_posts_today >= self.limits.own_posts_per_day:
            return (
                f"{self.label}: własne posty {self.own_posts_today} z "
                f"{self.limits.own_posts_per_day} w ostatniej dobie{_frees(self.own_frees_at)}"
            )
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "konto": self.handle,
            "platforma": self.platform,
            "odpowiedzi_doba": self.replies_today,
            "odpowiedzi_godzina": self.replies_this_hour,
            "wlasne_doba": self.own_posts_today,
            "limity": self.limits.to_json(),
        }


#: An account that is not among the scope's accounts has no pace to ask about, and "no pace" must
#: never read as "no limit": with limits per account the answer is a refusal (fail closed).
_NO_PACE_FOR_ACCOUNT = (
    "konto nie jest wśród kont tego zakresu, więc nie ma dla niego limitu, "
    "który dałoby się sprawdzić"
)


def _frees(moment: datetime | None) -> str:
    return f", następne miejsce o {moment:%H:%M} UTC" if moment is not None else ""


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
    #: v11: one entry per account of the scope when the limits are per account (else empty).
    accounts: tuple[AccountSpent, ...] = ()

    def _frees(self, moment: datetime | None) -> str:
        return _frees(moment)

    def account(self, account_id: UUID | None) -> AccountSpent | None:
        for one in self.accounts:
            if one.account_id == account_id:
                return one
        return None

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

    def own_post_blocked(self, account_id: UUID | None = None) -> str | None:
        """Limit WŁASNYCH POSTÓW, osobno od odpowiedzi.

        Pytany tylko wtedy, gdy propozycja jest własnym postem. Odpowiedź pod cudzym wątkiem nie
        ma czekać na limit, który jej nie dotyczy, a własny post ma zostać zatrzymany także
        wtedy, gdy ogólny limit publikacji jest jeszcze daleki.

        PER ACCOUNT (v11) the question is about THIS account's own posts, not the project's, and
        `account_id` is how the caller says which: with per-account limits and no account only the
        overall ceiling can be asked.
        """
        if self.limits.per_account:
            one = self.account(account_id)
            if account_id is not None and one is None:
                return _NO_PACE_FOR_ACCOUNT
            blocked = one.own_post_blocked() if one is not None else None
            return blocked or self.publication_blocked()
        if self.own_posts_today >= self.limits.own_posts_per_day:
            return (
                f"własne posty: {self.own_posts_today} z {self.limits.own_posts_per_day} "
                "w ostatniej dobie"
            )
        return self.publication_blocked()

    def publication_blocked(self, account_id: UUID | None = None) -> str | None:
        """Only the publication limits. A turn may still run when the model budget is spent for
        publishing but not for thinking, and the two questions are asked at different moments.

        PER ACCOUNT (v11): the reply limits of the account first (it is the narrower question),
        then the overall ceiling when one was signed. Legacy: the one bucket, as it always was.
        """
        if self.limits.per_account:
            one = self.account(account_id)
            if account_id is not None and one is None:
                return _NO_PACE_FOR_ACCOUNT
            blocked = one.reply_blocked() if one is not None else None
            if blocked is not None:
                return blocked
        per_day, per_hour = self.limits.overall_per_day(), self.limits.overall_per_hour()
        if per_day is not None and self.publications_today >= per_day:
            return (
                f"publikacje: {self.publications_today} z {per_day} "
                f"w ostatniej dobie{self._frees(self.day_frees_at)}"
            )
        if per_hour is not None and self.publications_this_hour >= per_hour:
            return (
                f"publikacje: {self.publications_this_hour} z {per_hour} "
                f"w ostatniej godzinie{self._frees(self.hour_frees_at)}"
            )
        return None

    def accounts_sentence(self) -> str:
        """Per-account limits told for the agent's mandate: what is spent of what, account by
        account, then the overall ceiling when one was signed."""
        parts = [
            f"{one.label}: odpowiedzi {one.replies_this_hour} z {one.limits.replies_per_hour} w "
            f"godzinie i {one.replies_today} z {one.limits.replies_per_day} w dobie, własne posty "
            f"{one.own_posts_today} z {one.limits.own_posts_per_day}"
            for one in self.accounts
        ]
        per_day, per_hour = self.limits.overall_per_day(), self.limits.overall_per_hour()
        if per_day is not None or per_hour is not None:
            parts.append(
                "wszystkie konta razem: "
                + ", ".join(
                    text
                    for text in (
                        f"{self.publications_this_hour} z {per_hour} w godzinie"
                        if per_hour is not None
                        else "",
                        f"{self.publications_today} z {per_day} w dobie"
                        if per_day is not None
                        else "",
                    )
                    if text
                )
            )
        return "W ostatniej dobie: " + "; ".join(parts) if parts else "Brak kont w zakresie"

    def accounts_blocked(self) -> str:
        """Every account that cannot reply right now (and the overall ceiling when it is full)."""
        out = [text for one in self.accounts if (text := one.reply_blocked())]
        overall = self.publication_blocked(None)
        if overall:
            out.append(overall)
        return "; ".join(out)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tury_modelu_doba": self.model_turns_today,
            "publikacje_doba": self.publications_today,
            "publikacje_godzina": self.publications_this_hour,
            "wlasne_posty_doba": self.own_posts_today,
            "limit_tur_modelu": self.limits.model_turns_per_day,
            "limit_publikacji_doba": self.limits.publications_per_day,
            "limit_publikacji_godzina": self.limits.publications_per_hour,
            "limity_domyslne": list(self.limits.defaults_used),
        }
        if self.limits.per_account:
            out["konta"] = [one.to_json() for one in self.accounts]
        return out


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

    per_account: tuple[AccountSpent, ...] = ()
    if limits.per_account and accounts:
        per_account = await _account_spent(
            conn,
            organization_id=organization_id,
            project_id=project_id,
            accounts=accounts,
            limits=limits,
            moment=moment,
        )

    return Spent(
        model_turns_today=int(model_turns or 0),
        publications_today=per_day,
        publications_this_hour=per_hour,
        own_posts_today=int(own_posts or 0),
        limits=limits,
        hour_frees_at=min(oldest_hour) + HOUR if oldest_hour else None,
        day_frees_at=min(oldest_day) + DAY if oldest_day else None,
        accounts=per_account,
    )


async def _account_spent(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    accounts: Sequence[UUID],
    limits: Limits,
    moment: datetime,
) -> tuple[AccountSpent, ...]:
    """Each account of the scope, counted on ITS OWN ledgers (`committed_ledger`)."""
    named = await conn.fetch(
        "SELECT id, platform, external_account_id FROM social_accounts "
        "WHERE organization_id = $1 AND id = ANY($2::uuid[]) "
        "ORDER BY platform, external_account_id",
        organization_id,
        list(accounts),
    )
    ledger = await committed_ledger(
        conn, organization_id=organization_id, project_id=project_id, accounts=accounts, now=moment
    )
    out: list[AccountSpent] = []
    for row in named:
        account_id = row["id"]
        replies = ledger.get(reply_ledger(account_id), [])
        own = ledger.get(own_ledger(account_id), [])
        in_hour = [at for at in replies if moment - HOUR <= at <= moment]
        in_day = [at for at in replies if at >= moment - DAY]
        out.append(
            AccountSpent(
                account_id=account_id,
                platform=str(row["platform"]),
                handle=str(row["external_account_id"]),
                limits=limits_for(limits, account_id, str(row["platform"])),
                replies_today=len(in_day),
                replies_this_hour=len(in_hour),
                own_posts_today=sum(1 for at in own if at >= moment - DAY),
                hour_frees_at=min(in_hour) + HOUR if in_hour else None,
                day_frees_at=min(in_day) + DAY if in_day else None,
                own_frees_at=min(at for at in own if at >= moment - DAY) + DAY
                if any(at >= moment - DAY for at in own)
                else None,
            )
        )
    return tuple(out)


async def _queued_counts(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    moment: datetime,
) -> tuple[int, int, datetime | None, datetime | None]:
    """Queue entries of this project that have never been attempted, by the moment each may go out.

    By `coalesce(not_before, queued_at)` and not by `now()`, so a request that has been waiting
    for two days stops counting against today: the window is the same rolling one everything
    else here uses, and a row older than it will be paced by the publisher rather than by the
    budget.
    """
    # BY THE MOMENT A REQUEST MAY GO OUT, `coalesce(not_before, queued_at)`, the same moment
    # `batch_plan.committed_events` uses ("Send all" spaces a batch out with `not_before`): a
    # request scheduled for three hours from now is no publication of THIS hour, and a single send
    # right after a batch is not refused for slots that are hours away. The hour window ends at
    # `moment` (a request for later is not in "the last hour"); the day window has no upper
    # bound: what is committed for later is committed.
    row = await conn.fetchrow(
        """
        WITH q AS (
            SELECT coalesce(not_before, queued_at) AS at
              FROM publication_requests
             WHERE organization_id = $1 AND project_id = $2
               AND attempt_id IS NULL
               AND status IN ('QUEUED', 'PUBLISHING')
        )
        SELECT count(*) FILTER (WHERE at >= $3) AS per_day,
               count(*) FILTER (WHERE at >= $4 AND at <= $5) AS per_hour,
               min(at) FILTER (WHERE at >= $3) AS first_day,
               min(at) FILTER (WHERE at >= $4 AND at <= $5) AS first_hour
          FROM q
        """,
        organization_id,
        project_id,
        moment - DAY,
        moment - HOUR,
        moment,
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
