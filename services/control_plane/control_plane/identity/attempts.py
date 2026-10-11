"""Slowing down somebody who is guessing passwords (P2 etap B).

THE GAP THIS CLOSES was reported in `api/session.py` from F1 onwards and left open on purpose
while the product had one operator and a loopback-only port. P7 changed the arithmetic: four
routes now accept credentials from anybody who can reach the dashboard, and Argon2id costs an
attacker time but not attempts.

FOUR DECISIONS, EACH BECAUSE OF A WAY A LIMITER CAN BE USELESS:

  1. **the counter is in PostgreSQL, not in the process.** A counter in memory resets on every
     restart and does not exist for a second API process. Both are ordinary events, so an
     in-process limit is a limit that disappears exactly when somebody leans on it. The test
     that matters (`test_the_block_survives_a_restart_of_the_process`) is the one that would be
     impossible with a dictionary;
  2. **two keys, not one.** Per email alone, one machine may try a thousand accounts five times
     each; per source address alone, a botnet may try one account for ever. Whichever crosses
     first does the blocking;
  3. **the delay grows.** Five free attempts, then one minute, two, four, eight, fifteen and
     fifteen for ever after. A fixed lockout is either too short to matter or long enough to be
     a denial of service against the person whose address somebody else is guessing;
  4. **being blocked says nothing about the account.** A counter row is created for every failed
     credential, including addresses nobody ever registered, so neither the answer nor the table
     distinguishes "wrong password" from "no such account".

WHAT IT CANNOT DO, said plainly. The source address is `request.client.host`, which behind a
reverse proxy is the PROXY. `X-Forwarded-For` is not read, because a header a caller can set is
not an identity, and trusting it would hand the attacker a fresh key per request. A deployment
behind a proxy therefore gets per-email limiting that works and per-address limiting that
collapses onto one key; fixing that belongs in the proxy, and `ops/` is where it will be said.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

KIND_EMAIL = "EMAIL"
KIND_ADDRESS = "ADDRESS"
KINDS = (KIND_EMAIL, KIND_ADDRESS)

#: How many failures cost nothing. Five: a person mistyping a password twice and trying two old
#: ones is not an attack, and a limiter that punishes them teaches nobody anything.
FREE_ATTEMPTS = 5

#: The delay after each failure past the free ones, in minutes, and the last value repeats. Not
#: doubling for ever: an hour-long lockout on an address somebody else is guessing is a denial of
#: service performed by the defence.
BACKOFF_MINUTES: tuple[int, ...] = (1, 2, 4, 8, 15)

#: A counter nobody has touched for this long is forgotten, so a person who mistyped their
#: password a month ago does not start today three attempts from a lockout.
FORGET_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class Verdict:
    """Whether this credential may be checked at all, and for how long it may not."""

    allowed: bool
    retry_after_seconds: int = 0
    #: Which key blocked it. For the log and the tests, never for the response body.
    reason: str = ""


def _delay_for(failures: int) -> timedelta:
    """The wait after `failures` failed attempts. Zero while they are still free."""
    if failures <= FREE_ATTEMPTS:
        return timedelta(0)
    index = min(failures - FREE_ATTEMPTS, len(BACKOFF_MINUTES)) - 1
    return timedelta(minutes=BACKOFF_MINUTES[index])


class AttemptLimiter:
    """The counter, over one pool. Two calls: ask before, record after."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def check(self, *, email: str | None, address: str | None) -> Verdict:
        """May this credential be verified now? Asked BEFORE the password is checked.

        Checking first is the point: a blocked caller must not get the Argon2id verification
        either, or the limiter would still be paying the attacker's compute bill.
        """
        keys = _keys(email, address)
        if not keys:
            return Verdict(allowed=True)
        now = datetime.now(UTC)
        # Two parallel arrays rather than an array of records: asyncpg refuses to send an
        # anonymous composite type ("input of anonymous composite types is not supported"),
        # measured rather than remembered. `unnest` of two arrays is the portable shape.
        kinds = [kind for kind, _key in keys]
        values = [key for _kind, key in keys]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.kind, a.key, a.blocked_until
                  FROM credential_attempts a
                  JOIN unnest($1::text[], $2::text[]) AS asked(kind, key)
                    ON asked.kind = a.kind AND asked.key = a.key
                """,
                kinds,
                values,
            )
        for row in rows:
            blocked_until = row["blocked_until"]
            if blocked_until is not None and blocked_until > now:
                return Verdict(
                    allowed=False,
                    retry_after_seconds=max(1, int((blocked_until - now).total_seconds())),
                    reason=f"{row['kind']}:{row['key']}",
                )
        return Verdict(allowed=True)

    async def record_failure(self, *, email: str | None, address: str | None) -> Verdict:
        """One failed credential. Returns the verdict that now applies to it.

        The row is created for every failure, including for an address nobody registered: a
        counter that existed only for real accounts would answer "does this account exist" by
        the timing of its own blocking.
        """
        keys = _keys(email, address)
        if not keys:
            return Verdict(allowed=True)
        now = datetime.now(UTC)
        worst = Verdict(allowed=True)
        async with self._pool.acquire() as conn, conn.transaction():
            for kind, key in keys:
                row = await conn.fetchrow(
                    """
                    INSERT INTO credential_attempts (kind, key, failures, first_failed_at,
                                                     last_failed_at)
                    VALUES ($1, $2, 1, now(), now())
                    ON CONFLICT (kind, key) DO UPDATE SET
                        -- A counter nobody has touched for a day starts again from one: the
                        -- person who mistyped their password last month is not an attacker.
                        failures = CASE
                            WHEN credential_attempts.last_failed_at < now() - $3::interval THEN 1
                            ELSE credential_attempts.failures + 1
                        END,
                        first_failed_at = CASE
                            WHEN credential_attempts.last_failed_at < now() - $3::interval
                            THEN now() ELSE credential_attempts.first_failed_at
                        END,
                        last_failed_at = now()
                    RETURNING kind, key, failures
                    """,
                    kind,
                    key,
                    FORGET_AFTER,
                )
                delay = _delay_for(row["failures"])
                if delay > timedelta(0):
                    blocked_until = now + delay
                    await conn.execute(
                        "UPDATE credential_attempts SET blocked_until = $3 "
                        "WHERE kind = $1 AND key = $2",
                        kind,
                        key,
                        blocked_until,
                    )
                    seconds = max(1, int(delay.total_seconds()))
                    if seconds > worst.retry_after_seconds:
                        worst = Verdict(
                            allowed=False,
                            retry_after_seconds=seconds,
                            reason=f"{kind}:{key}",
                        )
        return worst

    async def record_success(self, *, email: str | None, address: str | None) -> None:
        """A credential that worked clears its counters. Both of them.

        Clearing the ADDRESS counter too is deliberate: the person who finally typed their
        password right is the same person whose earlier attempts filled it.
        """
        keys = _keys(email, address)
        if not keys:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM credential_attempts a
                 USING unnest($1::text[], $2::text[]) AS asked(kind, key)
                 WHERE asked.kind = a.kind AND asked.key = a.key
                """,
                [kind for kind, _key in keys],
                [key for _kind, key in keys],
            )

    async def state(self, *, kind: str, key: str) -> dict[str, Any] | None:
        """One counter, for the tests and for an operator looking at an incident."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT kind, key, failures, first_failed_at, last_failed_at, blocked_until "
                "FROM credential_attempts WHERE kind = $1 AND key = $2",
                kind,
                key.strip().lower() if kind == KIND_EMAIL else key,
            )
        return dict(row) if row is not None else None


def _keys(email: str | None, address: str | None) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if email:
        keys.append((KIND_EMAIL, email.strip().lower()))
    if address:
        keys.append((KIND_ADDRESS, address.strip()))
    return keys
