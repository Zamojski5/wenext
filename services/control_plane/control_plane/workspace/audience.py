"""What the product measured about an account's audience (P6 etap A; the first writer, 27.09).

Migration 0019 made `audience_snapshots`: one row per reading, with where it came from and the
moment it was read. The first thing that writes it is the hourly X collection, which reads the
account's own profile page (`presence/x_trends.py`), because the founder's goal on X is followers
and until now the only follower count anybody had was typed into a chat by hand.

A reading that did not happen is ABSENT, never zero (ZAKRES-FUNKCJONALNY §6 rule 2): zero
followers reads as a fact about the account, and it would be a fact about us. The change over a
day exists only when there is a reading at least a day older than the latest one; before that the
answer is "not known yet", said as `None`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

METRIC_FOLLOWERS = "FOLLOWERS"
#: Readings come every hour; three missed hours are no longer a slow tick but a stopped collection.
STALE_READING = timedelta(hours=3)
PERIOD_POINT_IN_TIME = "POINT_IN_TIME"
SOURCE_PLATFORM_PAGE = "PLATFORM_PAGE"


@dataclass(frozen=True)
class FollowerReading:
    """The latest follower count of one account, with the two readings it is compared against."""

    social_account_id: UUID
    followers: int
    observed_at: datetime
    #: The newest reading at least 24 hours older than `observed_at`, or `None` before there is one.
    day_ago: int | None
    first: int
    first_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "social_account_id": str(self.social_account_id),
            "obserwujacy": self.followers,
            "odczyt": self.observed_at.isoformat(),
            "doba_temu": self.day_ago,
            "zmiana_doby": None if self.day_ago is None else self.followers - self.day_ago,
            "pierwszy_odczyt": self.first,
            "pierwszy_odczyt_o": self.first_at.isoformat(),
        }


async def record_followers(
    connection: asyncpg.Connection,
    *,
    organization_id: UUID,
    social_account_id: UUID,
    followers: int,
    observed_at: datetime,
) -> bool:
    """One reading of the follower count from the platform's page. Returns whether it was new.

    The unique key of 0019 (account, metric, period, moment) makes a repeated write of the same
    reading a no-op rather than a second row, so a collection retried within its slot counts once.
    """
    status = await connection.execute(
        """
        INSERT INTO audience_snapshots
            (organization_id, social_account_id, metric, value, period, source, observed_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (organization_id, social_account_id, metric, period, observed_at) DO NOTHING
        """,
        organization_id,
        social_account_id,
        METRIC_FOLLOWERS,
        followers,
        PERIOD_POINT_IN_TIME,
        SOURCE_PLATFORM_PAGE,
        observed_at,
    )
    return status.endswith(" 1")


async def latest_followers(
    connection: asyncpg.Connection, *, organization_id: UUID, account_ids: Iterable[UUID]
) -> dict[UUID, FollowerReading]:
    """The latest follower reading of each of these accounts that has one; others are absent."""
    ids = list(account_ids)
    if not ids:
        return {}
    rows = await connection.fetch(
        """
        SELECT l.social_account_id, l.value, l.observed_at,
               d.value AS day_ago, f.value AS first, f.observed_at AS first_at
          FROM (
                SELECT DISTINCT ON (social_account_id) social_account_id, value, observed_at
                  FROM audience_snapshots
                 WHERE organization_id = $1 AND social_account_id = ANY($2::uuid[])
                   AND metric = $3 AND period = $4
                 ORDER BY social_account_id, observed_at DESC
               ) l
          LEFT JOIN LATERAL (
                SELECT value FROM audience_snapshots
                 WHERE organization_id = $1 AND social_account_id = l.social_account_id
                   AND metric = $3 AND period = $4
                   AND observed_at <= l.observed_at - interval '24 hours'
                 ORDER BY observed_at DESC
                 LIMIT 1
               ) d ON true
          JOIN LATERAL (
                SELECT value, observed_at FROM audience_snapshots
                 WHERE organization_id = $1 AND social_account_id = l.social_account_id
                   AND metric = $3 AND period = $4
                 ORDER BY observed_at
                 LIMIT 1
               ) f ON true
        """,
        organization_id,
        ids,
        METRIC_FOLLOWERS,
        PERIOD_POINT_IN_TIME,
    )
    return {
        row["social_account_id"]: FollowerReading(
            social_account_id=row["social_account_id"],
            followers=int(row["value"]),
            observed_at=row["observed_at"],
            day_ago=None if row["day_ago"] is None else int(row["day_ago"]),
            first=int(row["first"]),
            first_at=row["first_at"],
        )
        for row in rows
    }


@dataclass(frozen=True)
class FollowerChange:
    """One account's follower count across a window: the last reading at its start and end."""

    social_account_id: UUID
    platform: str
    login: str
    #: The newest reading at or before the window's start; `None` when measuring began later.
    before: int | None
    after: int
    after_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "social_account_id": str(self.social_account_id),
            "platform": self.platform,
            "login": self.login,
            "bylo": self.before,
            "jest": self.after,
            "zmiana": None if self.before is None else self.after - self.before,
            "odczyt": self.after_at.isoformat(),
        }

    def stale_sentence(self, *, until: datetime) -> str | None:
        """A warning when the latest reading is older than `STALE_READING` (29.09): the hourly
        X collection stopped reading, which in practice means the X capsule stopped answering;
        on 28-29.09 it hung for a day and nothing on the screen said so."""
        age = until - self.after_at
        if age <= STALE_READING:
            return None
        hours = int(age.total_seconds() // 3600)
        return (
            f"Ostatni odczyt obserwujących @{self.login} jest sprzed {hours} h: cogodzinny "
            f"zbiór z {self.platform} nie działa, a to zwykle znaczy, że przeglądarka agenta "
            "nie odpowiada."
        )

    def sentence(self, *, first_visit: bool) -> str:
        """The line on the return screen. No clock time: the screen's window already has one."""
        who = f"Obserwujący @{self.login} na {self.platform}"
        if first_visit:
            return f"{who}: {self.after} (ostatni odczyt z profilu konta)."
        if self.before is None:
            return (
                f"{who}: {self.after}. Pomiar ruszył po Twojej ostatniej wizycie, więc zmianę "
                "zobaczysz przy następnej."
            )
        change = self.after - self.before
        if change == 0:
            return f"{who}: {self.after}, bez zmian od Twojej ostatniej wizyty."
        sign = "+" if change > 0 else ""
        return f"{who}: {self.before} → {self.after} ({sign}{change}) od Twojej ostatniej wizyty."


async def followers_across(
    connection: asyncpg.Connection,
    *,
    organization_id: UUID,
    account_ids: Iterable[UUID],
    since: datetime | None,
    until: datetime,
) -> list[FollowerChange]:
    """Each account's follower count at the start and at the end of a window, where measured.

    An account with no reading up to `until` is absent. `before` is the newest reading at or
    before `since`; with no `since` (a first visit) it stays `None` and so does the change.
    """
    ids = list(account_ids)
    if not ids:
        return []
    rows = await connection.fetch(
        """
        SELECT a.id, a.platform, a.external_account_id AS login,
               e.value AS after, e.observed_at AS after_at, b.value AS before
          FROM social_accounts a
          JOIN LATERAL (
                SELECT value, observed_at FROM audience_snapshots
                 WHERE organization_id = a.organization_id AND social_account_id = a.id
                   AND metric = $3 AND period = $4 AND observed_at <= $6
                 ORDER BY observed_at DESC
                 LIMIT 1
               ) e ON true
          LEFT JOIN LATERAL (
                SELECT value FROM audience_snapshots
                 WHERE organization_id = a.organization_id AND social_account_id = a.id
                   AND metric = $3 AND period = $4
                   AND $5::timestamptz IS NOT NULL AND observed_at <= $5
                 ORDER BY observed_at DESC
                 LIMIT 1
               ) b ON true
         WHERE a.organization_id = $1 AND a.id = ANY($2::uuid[])
         ORDER BY a.platform, a.external_account_id
        """,
        organization_id,
        ids,
        METRIC_FOLLOWERS,
        PERIOD_POINT_IN_TIME,
        since,
        until,
    )
    return [
        FollowerChange(
            social_account_id=row["id"],
            platform=str(row["platform"]),
            login=str(row["login"]),
            before=None if row["before"] is None else int(row["before"]),
            after=int(row["after"]),
            after_at=row["after_at"],
        )
        for row in rows
    ]
