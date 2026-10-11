"""Slowing down a password guesser, against PostgreSQL (P2 etap B).

THE GAP THIS CLOSES has been reported in `api/session.py` since F1 and stayed open while the
product had one operator behind a loopback port. P7 opened registration; four routes now take
credentials from anybody, so "Argon2id costs the attacker time" stopped being an answer.

WHAT THESE TESTS ARE ABOUT, and each is a way a limiter can be theatre:

  * the counter lives in the process, so a restart or a second worker resets it — the one case a
    dictionary could never pass, and the reason the table exists;
  * the block is checked AFTER the password, so the attacker still gets the verification work;
  * being blocked says which accounts exist, because only real ones are counted;
  * the lockout never ends, so a person whose address somebody else is guessing is locked out
    for good;
  * one key only, so either a single address may try every account or a botnet may try one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from control_plane.identity.attempts import (
    BACKOFF_MINUTES,
    FREE_ATTEMPTS,
    KIND_ADDRESS,
    KIND_EMAIL,
    AttemptLimiter,
)

pytestmark = pytest.mark.db

PASSWORD = "wystarczajaco-dlugie-haslo"


@pytest.fixture
def limiter(pool) -> AttemptLimiter:
    return AttemptLimiter(pool)


@pytest.fixture
def stamp() -> str:
    return uuid.uuid4().hex[:8]


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("203.0.113.7", 0))
    return httpx.AsyncClient(transport=transport, base_url="http://control-plane")


# ---------------------------------------------------------------------------
# The counter itself
# ---------------------------------------------------------------------------


async def test_the_first_attempts_are_free_and_then_the_wait_grows(limiter, stamp) -> None:
    email = f"probujacy-{stamp}@firma.test"
    for attempt in range(FREE_ATTEMPTS):
        verdict = await limiter.record_failure(email=email, address=None)
        assert verdict.allowed, f"próba {attempt + 1} z {FREE_ATTEMPTS} nie powinna blokować"

    first = await limiter.record_failure(email=email, address=None)
    assert not first.allowed
    assert first.retry_after_seconds == BACKOFF_MINUTES[0] * 60

    second = await limiter.record_failure(email=email, address=None)
    assert second.retry_after_seconds == BACKOFF_MINUTES[1] * 60, (
        "odstęp musi rosnąć: stały lockout jest albo za krótki, żeby cokolwiek zmienić, albo "
        "dość długi, żeby stać się odmową usługi dla właściciela adresu"
    )


async def test_the_wait_stops_growing_at_the_declared_ceiling(limiter, stamp) -> None:
    email = f"uparty-{stamp}@firma.test"
    for _ in range(FREE_ATTEMPTS + len(BACKOFF_MINUTES) + 3):
        verdict = await limiter.record_failure(email=email, address=None)
    assert verdict.retry_after_seconds == BACKOFF_MINUTES[-1] * 60


async def test_both_keys_count_and_either_one_blocks(limiter, stamp) -> None:
    """Per email alone, one machine tries a thousand accounts; per address alone, a botnet tries
    one account for ever."""
    address = f"198.51.100.{int(stamp[:2], 16) % 200}"
    for index in range(FREE_ATTEMPTS + 1):
        # A different account every time: only the ADDRESS key accumulates.
        await limiter.record_failure(email=f"{stamp}-{index}@firma.test", address=address)

    blocked = await limiter.check(email=f"{stamp}-nowy@firma.test", address=address)
    assert not blocked.allowed
    assert blocked.reason.startswith(KIND_ADDRESS)
    # And the fresh email on its own is not blocked: the address is what crossed the line.
    assert (await limiter.check(email=f"{stamp}-nowy@firma.test", address=None)).allowed


async def test_a_counter_exists_for_addresses_nobody_ever_registered(limiter, stamp) -> None:
    """Otherwise the timing of the block would answer "does this account exist"."""
    ghost = f"nie-ma-takiego-{stamp}@firma.test"
    await limiter.record_failure(email=ghost, address=None)
    state = await limiter.state(kind=KIND_EMAIL, key=ghost)
    assert state is not None and state["failures"] == 1


async def test_a_correct_credential_clears_both_counters(limiter, stamp) -> None:
    email = f"wrocil-{stamp}@firma.test"
    address = "198.51.100.10"
    for _ in range(FREE_ATTEMPTS + 1):
        await limiter.record_failure(email=email, address=address)
    assert not (await limiter.check(email=email, address=address)).allowed

    await limiter.record_success(email=email, address=address)

    assert (await limiter.check(email=email, address=address)).allowed
    assert await limiter.state(kind=KIND_EMAIL, key=email) is None
    assert await limiter.state(kind=KIND_ADDRESS, key=address) is None


async def test_a_counter_nobody_touched_for_a_day_starts_again(limiter, pool, stamp) -> None:
    """A person who mistyped their password last month does not start today near a lockout."""
    email = f"dawno-{stamp}@firma.test"
    for _ in range(FREE_ATTEMPTS):
        await limiter.record_failure(email=email, address=None)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE credential_attempts SET last_failed_at = $2, first_failed_at = $2 "
            "WHERE kind = 'EMAIL' AND key = $1",
            email,
            datetime.now(UTC) - timedelta(days=2),
        )

    verdict = await limiter.record_failure(email=email, address=None)

    assert verdict.allowed, "licznik sprzed doby musi zaczynać od nowa"
    state = await limiter.state(kind=KIND_EMAIL, key=email)
    assert state["failures"] == 1


async def test_the_block_survives_a_restart_of_the_process(pool, stamp) -> None:
    """THE case a dictionary could never pass, and the reason this lives in PostgreSQL.

    Two limiter objects with no shared state stand in for two processes: a restart, or a second
    API worker behind the same database. The block has to hold across both.
    """
    email = f"restart-{stamp}@firma.test"
    before = AttemptLimiter(pool)
    for _ in range(FREE_ATTEMPTS + 1):
        await before.record_failure(email=email, address=None)

    after = AttemptLimiter(pool)

    verdict = await after.check(email=email, address=None)
    assert not verdict.allowed, (
        "limit zniknął razem z obiektem, czyli zniknąłby przy restarcie kontenera i nie "
        "istniałby dla drugiego procesu API"
    )


async def test_the_wait_really_ends(limiter, pool, stamp) -> None:
    """A lockout that never ends is a denial of service performed by the defence."""
    email = f"czeka-{stamp}@firma.test"
    for _ in range(FREE_ATTEMPTS + 1):
        await limiter.record_failure(email=email, address=None)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE credential_attempts SET blocked_until = now() - interval '1 second' "
            "WHERE kind = 'EMAIL' AND key = $1",
            email,
        )

    assert (await limiter.check(email=email, address=None)).allowed


# ---------------------------------------------------------------------------
# The four doors
# ---------------------------------------------------------------------------


async def test_login_blocks_after_the_free_attempts_and_lets_the_right_password_through_later(
    app, pool, stamp
) -> None:
    async with await client_for(app) as client:
        registered = await client.post(
            "/api/auth/register",
            json={
                "email": f"blokada-{stamp}@firma.test",
                "password": PASSWORD,
                "organization_name": f"Blokada {stamp}",
            },
        )
        assert registered.status_code == 201, registered.text

        for attempt in range(FREE_ATTEMPTS):
            refused = await client.post(
                "/api/auth/login",
                json={"email": f"blokada-{stamp}@firma.test", "password": "nie to haslo"},
            )
            assert refused.status_code == 401, f"próba {attempt}: {refused.status_code}"
            assert refused.content == b"", "odmowa nie może nieść ciała"

        blocked = await client.post(
            "/api/auth/login",
            json={"email": f"blokada-{stamp}@firma.test", "password": "nie to haslo"},
        )
        assert blocked.status_code == 429
        assert blocked.content == b"", "429 też nie mówi nic o koncie"
        assert int(blocked.headers["retry-after"]) > 0

        # The correct password is refused too while the block lasts: a limiter that let the
        # right password through would be a limiter an attacker walks past on the attempt that
        # matters.
        during = await client.post(
            "/api/auth/login",
            json={"email": f"blokada-{stamp}@firma.test", "password": PASSWORD},
        )
        assert during.status_code == 429

        # Time passes.
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE credential_attempts SET blocked_until = now() - interval '1 second'"
            )

        after = await client.post(
            "/api/auth/login",
            json={"email": f"blokada-{stamp}@firma.test", "password": PASSWORD},
        )
        assert after.status_code == 200, after.text


async def test_the_operator_password_route_is_limited_too(app, pool, tenant) -> None:
    """The fourth door. It predates accounts and had no limit at all until P2 etap B."""
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM credential_attempts")
    async with await client_for(app) as client:
        for _ in range(FREE_ATTEMPTS):
            refused = await client.post("/api/session", json={"password": "nie to hasło"})
            assert refused.status_code == 401

        blocked = await client.post("/api/session", json={"password": "nie to hasło"})
        assert blocked.status_code == 429
        assert blocked.content == b""


async def test_registration_is_limited_by_address_and_not_by_the_address_being_taken(
    app, pool, stamp
) -> None:
    """A per-email key here would let anybody lock a stranger out of registering under their own
    address; a per-source key is the one that matches the attack."""
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM credential_attempts")
    async with await client_for(app) as client:
        first = await client.post(
            "/api/auth/register",
            json={
                "email": f"pierwszy-{stamp}@firma.test",
                "password": PASSWORD,
                "organization_name": "Pierwsza",
            },
        )
        assert first.status_code == 201

        # Every one of these is refused for the same reason and counts as a failure. One more
        # than the free allowance, because the block starts ON the failure that crosses it and
        # the next REQUEST is the one that meets it.
        for _ in range(FREE_ATTEMPTS + 1):
            again = await client.post(
                "/api/auth/register",
                json={
                    "email": f"pierwszy-{stamp}@firma.test",
                    "password": PASSWORD,
                    "organization_name": "Druga",
                },
            )
            assert again.status_code == 400

        blocked = await client.post(
            "/api/auth/register",
            json={
                "email": f"zupelnie-inny-{stamp}@firma.test",
                "password": PASSWORD,
                "organization_name": "Trzecia",
            },
        )
        assert blocked.status_code == 429


async def test_an_empty_body_is_still_an_empty_401_and_costs_no_counter(app, pool) -> None:
    """The boundary comes first: a request with no credential is refused before the limiter, so
    an unauthenticated caller cannot fill somebody else's counter by sending nothing."""
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM credential_attempts")
    async with await client_for(app) as client:
        for _ in range(FREE_ATTEMPTS + 3):
            refused = await client.post("/api/auth/login", json={})
            assert refused.status_code == 401
            assert refused.content == b""
    async with pool.acquire() as connection:
        counters = await connection.fetchval("SELECT count(*) FROM credential_attempts")
    assert counters == 0
