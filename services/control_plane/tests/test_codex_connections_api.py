"""`/api/codex-connections` and the two queue jobs behind it, against PostgreSQL.

THE COMPLETION CRITERION OF P1 ETAP 2, ONE CASE EACH:

  * two connections in the database — the adopted volume root and a new one in a subdirectory —
    with independent state;
  * adding the second one does not change a byte of the first one's files;
  * the view shows the real state or says "brak danych", never a zero and never an empty object
    that reads as "no limits";
  * disconnecting leaves the history.

THE ENGINE IS ALWAYS A STUB HERE AND NOBODY LOGS IN ANYWHERE. `CodexEnginePort` has exactly two
methods for this reason: the whole device-code wait is one call, so a test can drive it without a
real app-server, a real account or fifteen real minutes. What a real app-server does was measured
separately (`services/codex_runtime/NOTES.md` and the probe of 2026-09-19); this file is about
what the product does with the answers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from control_plane.domain.models import ClaimedJob
from control_plane.workspace.codex_home import ROOT_READ_DISABLED_REASON, ROOT_REF
from control_plane.workspace.connections import CodexConnectionStore
from control_plane.workspace.engine import (
    EngineSnapshot,
    LoginCancelled,
    LoginRefused,
    LoginTicket,
    LoginTimedOut,
)
from control_plane.workspace.handlers import CodexConnectionHandlers, connection_ref

pytestmark = pytest.mark.db

CONNECTIONS = "/api/codex-connections"

TICKET = LoginTicket(
    login_id="login-abc",
    verification_url="https://auth.example/device",
    user_code="ABCD-1234",
)


def snapshot(**overrides: Any) -> EngineSnapshot:
    """A logged-in read, with the shapes the real adapter produces."""
    base: dict[str, Any] = {
        "logged_in": True,
        "account_email": "zalozyciel@example.com",
        "plan_type": "pro",
        "installation_id": "inst-1",
        "models": {
            "complete": True,
            "count": 2,
            "models": [{"id": "gpt-5.6-terra"}, {"id": "gpt-5.6-luna"}],
            "fetched_at": "2026-09-19T12:00:00+00:00",
        },
        "rate_limits": {
            "read": True,
            "at": "2026-09-19T12:00:00+00:00",
            "payload": {
                "rateLimits": {
                    "planType": "pro",
                    # `usedPercent` is the only required field of a window in the pinned
                    # contract; `resetsAt` and `windowDurationMins` are absent on purpose here.
                    "primary": {"usedPercent": 12.5},
                }
            },
        },
    }
    base.update(overrides)
    return EngineSnapshot(**base)


@dataclass
class StubEngine:
    """A `CodexEnginePort` that never starts a process.

    `writes_auth` exists so one test can simulate the ONE thing a real login does to the disk:
    drop an `auth.json` into its own directory. That is what makes "the first connection's file
    is untouched" a measurement rather than a restatement of the code.
    """

    result: EngineSnapshot | None = None
    raises: Exception | None = None
    writes_auth: bool = True
    ticket: LoginTicket = TICKET
    homes: list[Path] = field(default_factory=list)

    async def run_login(
        self, *, home, on_ticket, should_cancel, deadline, poll_interval_s: float = 2.0
    ) -> EngineSnapshot:
        self.homes.append(Path(home))
        await on_ticket(self.ticket)
        if self.raises is not None:
            raise self.raises
        if await should_cancel():
            raise LoginCancelled("anulowane")
        if self.writes_auth:
            (Path(home) / "auth.json").write_text('{"atrapa": true}', encoding="utf-8")
        return self.result or snapshot()

    async def read(self, *, home) -> EngineSnapshot:
        self.homes.append(Path(home))
        if self.raises is not None:
            raise self.raises
        return self.result or snapshot()


def claim(connection_id: uuid.UUID, tenant, job_type: str) -> ClaimedJob:
    """A claim shaped like the dispatcher's, built by hand: this test is about the handler."""
    return ClaimedJob(
        job_id=uuid.uuid4(),
        organization_id=tenant["organization"],
        workspace_id=None,
        job_type=job_type,
        routing_key="codex.connection",
        priority=50,
        payload_ref=connection_ref(connection_id),
        dispatch_version=1,
        attempt_limit=1,
        attempt_id=uuid.uuid4(),
        attempt_no=1,
        lease_generation=1,
        lease_owner=uuid.uuid4(),
        expires_at=None,
    )


@pytest_asyncio.fixture(autouse=True)
async def empty_connections(pool):
    """No connection survives into the next case, because `home_ref` is unique GLOBALLY.

    That index is deliberately not scoped to the organization: the volume root is one directory
    per deployment, so two organizations adopting `'.'` would be sharing one account's
    credentials. The invariant is right and this fixture is the price of it — every case in this
    file builds a fresh organization but points at the same table.
    """
    async with pool.acquire() as connection:
        await _release_connections(connection)
    yield
    async with pool.acquire() as connection:
        await _release_connections(connection)


async def _release_connections(connection) -> None:
    """Zwolnij odwołania do połączeń, potem je skasuj.

    Kolejność wynika z klucza obcego `agent_sessions_point_at_connection` (0015): sesja wskazuje
    połączenie, na którym biegnie. Odkąd tik obecności (P4) naprawdę otwiera sesje na
    połączeniach, w dzielonej bazie testowej zostają wiersze, przez które samo `DELETE FROM
    codex_connections` przestało przechodzić.

    `codex_connection_id` jest NULLowalne i NULL znaczy „sesja bez połączenia", czyli dokładnie
    to, czym staje się sesja, której połączenie skasowano. Czyszczenie było i zostaje globalne,
    bo indeks unikalny na `home_ref` też jest globalny: to jest cena tego więzu, opisana wyżej.
    """
    await connection.execute(
        "UPDATE agent_sessions SET codex_connection_id = NULL WHERE codex_connection_id IS NOT NULL"
    )
    await connection.execute("DELETE FROM codex_connections")


@pytest.fixture
def volume(tmp_path, monkeypatch):
    """A stand-in for the `codex-home` volume, with the founder's connection already in its root.

    The two files are what a real logged-in root holds, and their bytes are the subject of the
    isolation test.
    """
    root = tmp_path / "codex-home"
    root.mkdir()
    (root / "auth.json").write_text('{"atrapa": "konto-zalozyciela"}', encoding="utf-8")
    (root / "config.toml").write_text('cli_auth_credentials_store = "file"\n', encoding="utf-8")
    monkeypatch.setenv("WENEXT_CODEX_HOME", str(root))
    return root


def files_of(path: Path) -> dict[str, bytes]:
    return {entry.name: entry.read_bytes() for entry in sorted(path.iterdir()) if entry.is_file()}


def handlers(pool, tenant, engine, root) -> CodexConnectionHandlers:
    return CodexConnectionHandlers(
        store=CodexConnectionStore(pool),
        engine=engine,
        home_root=root,
        organization_id=tenant["organization"],
    )


async def _adopt(client, **body) -> dict[str, Any]:
    response = await client.post(f"{CONNECTIONS}/adopt-root", json=body)
    assert response.status_code == 201, response.content[:400]
    return response.json()["connection"]


async def _create(client, label: str, **body) -> dict[str, Any]:
    response = await client.post(CONNECTIONS, json={"label": label, **body})
    assert response.status_code == 201, response.content[:400]
    return response.json()["connection"]


# ---------------------------------------------------------------------------
# The completion criterion
# ---------------------------------------------------------------------------


async def test_the_configured_root_is_reported_but_not_adopted_on_its_own(
    logged_in_client, volume
) -> None:
    """An automatically created row would claim that somebody registered that connection."""
    listing = await logged_in_client.get(CONNECTIONS)
    assert listing.status_code == 200, listing.content[:300]
    body = listing.json()
    assert body["connections"] == [], body
    assert body["root"] == {
        "configured": True,
        "adopted": False,
        "exists": True,
        "has_auth": True,
        "installation_id": None,
        "path": str(volume),
    }, body["root"]


async def test_two_connections_live_side_by_side_with_independent_state(
    logged_in_client, volume
) -> None:
    """The adopted root and a new subdirectory. Both in the table, neither aware of the other."""
    root_connection = await _adopt(logged_in_client, label="Konto założyciela")
    new_connection = await _create(logged_in_client, "Drugie konto")

    assert root_connection["home_ref"] == ROOT_REF
    assert new_connection["home_ref"] == new_connection["connection_id"]
    assert root_connection["connection_id"] != new_connection["connection_id"]

    listing = (await logged_in_client.get(CONNECTIONS)).json()
    assert listing["count"] == 2, listing
    assert listing["root"]["adopted"] is True
    refs = sorted(row["home_ref"] for row in listing["connections"])
    assert refs == sorted([ROOT_REF, new_connection["connection_id"]]), refs

    # The new one really has a directory of its own, with the hardened config.
    home = volume / new_connection["connection_id"]
    assert home.is_dir()
    assert 'cli_auth_credentials_store = "file"' in (home / "config.toml").read_text("utf-8")


async def test_adding_a_second_connection_changes_no_byte_of_the_first(
    logged_in_client, volume, pool, tenant
) -> None:
    """The criterion, measured on bytes: adopt the root, add a second connection, log it in.

    The login is the step that touches the disk — a real one writes `auth.json` — so the stub
    writes one too. What must not happen is the founder's `auth.json` or `config.toml` changing,
    and the only way to know is to compare them.
    """
    await _adopt(logged_in_client, label="Konto założyciela")
    before = files_of(volume)
    assert set(before) == {"auth.json", "config.toml"}, sorted(before)

    second = await _create(logged_in_client, "Drugie konto")
    assert files_of(volume) == before, "samo dodanie połączenia zmieniło pliki korzenia"

    engine = StubEngine()
    await handlers(pool, tenant, engine, volume).wait_for_login(
        claim(uuid.UUID(second["connection_id"]), tenant, "codex.connection.login")
    )

    assert files_of(volume) == before, "logowanie drugiego połączenia zmieniło pliki korzenia"
    # And it really wrote into its OWN directory.
    assert engine.homes == [volume / second["connection_id"]], engine.homes
    assert (volume / second["connection_id"] / "auth.json").is_file()


async def test_the_volume_root_is_never_opened_with_an_app_server(
    logged_in_client, volume, pool, tenant
) -> None:
    """The fix for the collision the live deployment would have had.

    The root is `WENEXT_CODEX_HOME`, which is also the directory `observe/wiring.py` hands to the
    turn runner, and that runner starts its own app-server FOR EACH TURN. Neither side takes the
    supervisor's lease — nothing in the control plane uses it — so an overlap would not be
    refused, it would just happen, and a concurrent OAuth refresh can rewrite `auth.json` under a
    running pilot.

    So the job records a stated non-measurement and the engine is NEVER CALLED. That last part is
    the assertion that matters: a version that called it and ignored the result would pass every
    check about the row.
    """
    adopted = await _adopt(logged_in_client, label="Konto założyciela")
    engine = StubEngine()
    await handlers(pool, tenant, engine, volume).read_connection(
        claim(uuid.UUID(adopted["connection_id"]), tenant, "codex.connection.read")
    )

    assert engine.homes == [], "silnik został otwarty na korzeniu wolumenu"

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["engine_read"]["enabled"] is False, row["engine_read"]
    assert "L2159" in row["engine_read"]["reason"], row["engine_read"]
    # Not an error: nothing went wrong, we decided not to look.
    assert row["last_error"] is None, row
    assert row["status"] == "PENDING_LOGIN", row
    # And the absence is dated and explained, rather than shown as zero.
    assert row["last_read_at"] is not None, row
    assert row["limits"]["read"] is False, row["limits"]
    assert row["limits"]["windows"] == [], row["limits"]
    assert row["models"]["complete"] is False and row["models"]["count"] == 0, row["models"]
    # What CAN be read without a process is read: the file is really there.
    assert row["home"]["has_auth"] is True, row["home"]


async def test_the_two_buttons_that_would_open_the_root_are_refused(
    logged_in_client, volume
) -> None:
    """Refresh and login on the root answer 409 with the reason, so a posted form explains itself.

    The screen hides both buttons for such a connection; this is the other half, for anybody who
    sends the request anyway.
    """
    adopted = await _adopt(logged_in_client, label="Konto założyciela")
    identifier = adopted["connection_id"]
    for path in (f"{CONNECTIONS}/{identifier}/refresh", f"{CONNECTIONS}/{identifier}/login"):
        refused = await logged_in_client.post(path)
        assert refused.status_code == 409, f"{path}: {refused.content[:300]!r}"
        assert refused.json()["detail"] == ROOT_READ_DISABLED_REASON, refused.json()


async def test_a_connection_in_its_own_directory_is_still_read_normally(
    logged_in_client, volume, pool, tenant
) -> None:
    """The refusal is about ONE directory, not about the feature.

    Without this, "we stopped opening app-servers" and "we stopped opening the root" would look
    the same from the tests, and a later simplification could turn the first into the second.
    """
    created = await _create(logged_in_client, "Drugie konto")
    engine = StubEngine()
    await handlers(pool, tenant, engine, volume).read_connection(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read")
    )
    assert engine.homes == [volume / created["connection_id"]], engine.homes

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["engine_read"]["enabled"] is True, row["engine_read"]
    assert row["status"] == "CONNECTED", row


async def test_a_completed_login_is_written_to_the_row_and_shown(
    logged_in_client, volume, pool, tenant
) -> None:
    created = await _create(logged_in_client, "Drugie konto")
    connection_id = uuid.UUID(created["connection_id"])
    assert created["status"] == "PENDING_LOGIN"

    await handlers(pool, tenant, StubEngine(), volume).wait_for_login(
        claim(connection_id, tenant, "codex.connection.login")
    )

    row = next(
        entry
        for entry in (await logged_in_client.get(CONNECTIONS)).json()["connections"]
        if entry["connection_id"] == created["connection_id"]
    )
    assert row["status"] == "CONNECTED", row
    assert row["account_email"] == "zalozyciel@example.com"
    assert row["plan_type"] == "pro"
    assert row["models"]["complete"] is True and row["models"]["count"] == 2
    assert row["last_read_at"] is not None, (
        "stan bez daty odczytu to stan, którego nie da się datować"
    )
    assert row["last_error"] is None
    # The ticket is cleared once it is spent — a code left on the screen after the login is a
    # code somebody will try to use again.
    assert row["login"] is None


async def test_the_code_the_human_must_type_is_visible_while_the_wait_runs(
    logged_in_client, volume, pool, tenant
) -> None:
    """`on_ticket` publishes before the wait, so a page opened mid-login shows the code.

    Without this the flow is unusable: the human refreshes the page and the code is gone.
    """
    created = await _create(logged_in_client, "Drugie konto")
    connection_id = uuid.UUID(created["connection_id"])
    store = CodexConnectionStore(pool)

    seen: dict[str, Any] = {}

    class Watching(StubEngine):
        async def run_login(self, *, home, on_ticket, should_cancel, deadline, poll_interval_s=2.0):
            await on_ticket(TICKET)
            row = await store.get(
                organization_id=tenant["organization"], connection_id=connection_id
            )
            seen.update(row.login_ticket if row else {})
            return snapshot()

    await handlers(pool, tenant, Watching(), volume).wait_for_login(
        claim(connection_id, tenant, "codex.connection.login")
    )
    assert seen["verification_url"] == TICKET.verification_url, seen
    assert seen["user_code"] == TICKET.user_code, seen
    assert seen["login_id"] == TICKET.login_id, seen
    assert "deadline_at" in seen, seen


async def test_a_refused_login_becomes_a_state_with_a_sentence(
    logged_in_client, volume, pool, tenant
) -> None:
    """Device code disabled on the account is the documented refusal, not a crash.

    The job COMPLETES: a failed job in the queue means "nobody diagnosed this", and this was
    diagnosed.
    """
    created = await _create(logged_in_client, "Drugie konto")
    engine = StubEngine(raises=LoginRefused("device code login nie jest włączony na tym koncie"))
    await handlers(pool, tenant, engine, volume).wait_for_login(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.login")
    )

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "ERROR", row
    assert "device code" in row["last_error"], row
    assert row["login"] is None
    assert row["last_read_at"] is not None


async def test_a_login_that_ran_out_of_time_says_so_and_is_not_retried(
    logged_in_client, volume, pool, tenant
) -> None:
    created = await _create(logged_in_client, "Drugie konto")
    engine = StubEngine(raises=LoginTimedOut("logowanie nie zostało ukończone do 12:15"))
    await handlers(pool, tenant, engine, volume).wait_for_login(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.login")
    )
    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "ERROR", row
    assert "ukończone" in row["last_error"], row


async def test_cancelling_during_the_wait_is_not_recorded_as_an_error(
    logged_in_client, volume, pool, tenant
) -> None:
    """The human ending the connection is a decision, not a fault.

    The signal is the row itself, because the cancelling request and the waiting job are two
    different tasks and, after a restart, two different processes.
    """
    created = await _create(logged_in_client, "Drugie konto")
    connection_id = uuid.UUID(created["connection_id"])

    removed = await logged_in_client.delete(f"{CONNECTIONS}/{connection_id}")
    assert removed.status_code == 204, removed.content[:300]

    engine = StubEngine()
    await handlers(pool, tenant, engine, volume).wait_for_login(
        claim(connection_id, tenant, "codex.connection.login")
    )

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "DISCONNECTED", row
    assert row["last_error"] is None, row
    # Nothing was signed in: the stub saw the cancel before it would have written anything.
    assert not (volume / created["connection_id"] / "auth.json").exists()


async def test_disconnecting_keeps_the_row_and_the_directory(logged_in_client, volume) -> None:
    """P1's promise about history, again: nothing is deleted, on disk or in the table."""
    created = await _create(logged_in_client, "Drugie konto")
    home = volume / created["connection_id"]
    assert home.is_dir()

    assert (
        await logged_in_client.delete(f"{CONNECTIONS}/{created['connection_id']}")
    ).status_code == 204

    listing = (await logged_in_client.get(CONNECTIONS)).json()
    assert listing["count"] == 1, listing
    row = listing["connections"][0]
    assert row["status"] == "DISCONNECTED"
    assert row["active"] is False
    assert row["disconnected_at"] is not None
    assert home.is_dir(), "odłączenie skasowało katalog połączenia"

    # And a second disconnect is a 404, not a cheerful 204 over nothing.
    again = await logged_in_client.delete(f"{CONNECTIONS}/{created['connection_id']}")
    assert again.status_code == 404, again.content[:300]


# ---------------------------------------------------------------------------
# Honesty of the payload
# ---------------------------------------------------------------------------


async def test_limits_that_were_not_read_say_so_instead_of_showing_zero(
    logged_in_client, volume, pool, tenant
) -> None:
    """Without an account the app-server refuses; the payload carries the reason, never zeros."""
    created = await _create(logged_in_client, "Drugie konto")
    engine = StubEngine(
        result=snapshot(
            logged_in=False,
            account_email=None,
            plan_type=None,
            rate_limits={
                "read": False,
                "reason": "codex account authentication required to read rate limits",
            },
        ),
        writes_auth=False,
    )
    await handlers(pool, tenant, engine, volume).read_connection(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read")
    )

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "PENDING_LOGIN", row
    assert row["limits"]["read"] is False, row["limits"]
    assert "authentication required" in row["limits"]["reason"], row["limits"]
    assert row["limits"]["windows"] == [], row["limits"]
    # The catalogue came back anyway — `model/list` needs no account (measured 2026-09-19).
    assert row["models"]["complete"] is True, row["models"]


async def test_a_window_without_a_reset_time_keeps_the_field_as_unknown(
    logged_in_client, volume, pool, tenant
) -> None:
    """`resetsAt` is optional in the pinned contract, so `null` is a legal answer.

    Both windows are always listed: a window the screen cannot see is indistinguishable from a
    window at zero percent, and only one of those is a measurement.
    """
    created = await _create(logged_in_client, "Drugie konto")
    await handlers(pool, tenant, StubEngine(), volume).read_connection(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read")
    )
    limits = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]["limits"]
    assert limits["read"] is True
    assert [window["name"] for window in limits["windows"]] == ["primary", "secondary"]
    primary, secondary = limits["windows"]
    assert primary["present"] is True and primary["used_percent"] == 12.5
    assert primary["resets_at"] is None and primary["window_minutes"] is None
    assert secondary["present"] is False and secondary["used_percent"] is None


async def test_the_reset_time_reaches_the_screen_as_a_moment_not_as_a_number(
    logged_in_client, volume, pool, tenant
) -> None:
    """Kształt ZMIERZONY na koncie założyciela 20.09 (`output/ratelimits-2026-09-20.json`).

    Silnik podaje `resetsAt` jako epokę w sekundach, a schemat ekranu
    (`apps/dashboard/lib/codex-schema.ts`) wymaga `string | null`. Zanim to przeliczenie
    powstało, ekran „Połączenia Codex" odrzucał CAŁY odczyt zdaniem „brakuje pól albo mają zły
    typ (resets_at)" o połączeniu, które było zdrowe i miało plan plus. Liczby poniżej są
    przepisane z pomiaru, nie wymyślone: 1789885996 to 20.09.2026 06:33:16 UTC.
    """
    created = await _create(logged_in_client, "Konto z limitami")
    engine = StubEngine(
        result=snapshot(
            plan_type="plus",
            rate_limits={
                "read": True,
                "at": "2026-09-20T12:00:00+00:00",
                "payload": {
                    "rateLimits": {
                        "planType": "plus",
                        "primary": {
                            "resetsAt": 1789885996,
                            "usedPercent": 100,
                            "windowDurationMins": 300,
                        },
                        "secondary": {
                            "resetsAt": 1790433639,
                            "usedPercent": 32,
                            "windowDurationMins": 10080,
                        },
                    }
                },
            },
        )
    )
    await handlers(pool, tenant, engine, volume).read_connection(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read")
    )

    limits = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]["limits"]
    primary, secondary = limits["windows"]
    assert primary["resets_at"] == "2026-09-20T06:33:16+00:00", primary
    assert secondary["resets_at"] == "2026-09-26T14:40:39+00:00", secondary
    assert primary["used_percent"] == 100 and primary["window_minutes"] == 300
    assert isinstance(primary["resets_at"], str), (
        "schemat ekranu wymaga napisu; liczba tutaj jest tym, przez co ekran odrzucał odczyt"
    )


async def test_a_reset_time_that_is_not_an_epoch_is_unknown_rather_than_guessed(
    logged_in_client, volume, pool, tenant
) -> None:
    """Liczba, która nie jest epoką, daje `null`, a nie własną interpretację.

    „Nie wiem" da się na ekranie napisać uczciwie. Zgadnięta godzina wygląda tak samo jak
    zmierzona i to ona zaplanowała kiedyś powrót na rok 2083 (D-045).
    """
    created = await _create(logged_in_client, "Konto z dziwną liczbą")
    engine = StubEngine(
        result=snapshot(
            rate_limits={
                "read": True,
                "at": "2026-09-20T12:00:00+00:00",
                "payload": {
                    "rateLimits": {"primary": {"resetsAt": 300, "usedPercent": 10}},
                },
            },
        )
    )
    await handlers(pool, tenant, engine, volume).read_connection(
        claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read")
    )

    limits = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]["limits"]
    assert limits["windows"][0]["resets_at"] is None, limits["windows"][0]
    assert limits["windows"][0]["used_percent"] == 10, "reszta okna zostaje, znika tylko godzina"


async def test_an_account_with_no_email_is_still_a_logged_in_account(
    logged_in_client, volume, pool, tenant
) -> None:
    """The pinned schema allows `email: null` on a ChatGPT account, so a null email is not a
    missing login and the row has to keep saying CONNECTED."""
    created = await _create(logged_in_client, "Drugie konto")
    await handlers(
        pool, tenant, StubEngine(result=snapshot(account_email=None)), volume
    ).read_connection(claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read"))
    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "CONNECTED", row
    assert row["account_email"] is None, row


async def test_a_deployment_without_the_engine_records_a_reason_rather_than_failing(
    logged_in_client, volume, pool, tenant
) -> None:
    created = await _create(logged_in_client, "Drugie konto")
    await CodexConnectionHandlers(
        store=CodexConnectionStore(pool),
        engine=None,
        home_root=None,
        organization_id=tenant["organization"],
    ).read_connection(claim(uuid.UUID(created["connection_id"]), tenant, "codex.connection.read"))

    row = (await logged_in_client.get(CONNECTIONS)).json()["connections"][0]
    assert row["status"] == "ERROR", row
    assert "WENEXT_CODEX_HOME" in row["last_error"], row


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


async def test_one_directory_cannot_serve_two_live_connections(logged_in_client, volume) -> None:
    """L2159. Adopting the root twice is a 409 with a sentence, and the first row survives."""
    await _adopt(logged_in_client, label="Konto założyciela")
    again = await logged_in_client.post(f"{CONNECTIONS}/adopt-root", json={"label": "Znowu"})
    assert again.status_code == 409, again.content[:300]
    assert "katalog" in again.json()["detail"]
    assert (await logged_in_client.get(CONNECTIONS)).json()["count"] == 1


async def test_a_narrowed_connection_must_name_a_workspace_of_this_organization(
    logged_in_client, volume, tenant, pool
) -> None:
    fine = await _create(
        logged_in_client, "Dla jednego workspace'u", workspace_id=str(tenant["workspace"])
    )
    assert fine["scope"] == "workspace", fine
    assert fine["workspace_id"] == str(tenant["workspace"])

    async with pool.acquire() as connection:
        other_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-0014-api') RETURNING id"
        )
        foreign = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization,
        )
    refused = await logged_in_client.post(
        CONNECTIONS, json={"label": "Obcy", "workspace_id": str(foreign)}
    )
    assert refused.status_code == 404, refused.content[:300]


async def test_a_connection_with_no_workspace_is_available_to_the_whole_organization(
    logged_in_client, volume
) -> None:
    created = await _create(logged_in_client, "Wspólne")
    assert created["scope"] == "organization", created
    assert created["workspace_id"] is None, created


@pytest.mark.parametrize("blank", ["   ", "\t", "\n"])
async def test_a_blank_connection_label_is_refused(logged_in_client, volume, blank) -> None:
    """Pydantic's `min_length=1` counts characters, so a tab reaches the handler.

    Stripping it and passing the result on would have stored an EMPTY label: the database CHECK
    did not catch that either until it was widened, because `btrim(x)` trims spaces only. The
    handler now strips and then refuses what the strip produced.
    """
    for body in ({"label": blank}, {"label": blank, "workspace_id": None}):
        refused = await logged_in_client.post(CONNECTIONS, json=body)
        assert refused.status_code == 400, refused.content[:300]
    adopted = await logged_in_client.post(f"{CONNECTIONS}/adopt-root", json={"label": blank})
    assert adopted.status_code == 400, adopted.content[:300]


async def test_no_route_of_this_module_is_reachable_without_a_session(client, volume) -> None:
    paths = [
        ("GET", CONNECTIONS),
        ("POST", CONNECTIONS),
        ("POST", f"{CONNECTIONS}/adopt-root"),
        ("POST", f"{CONNECTIONS}/{uuid.uuid4()}/refresh"),
        ("POST", f"{CONNECTIONS}/{uuid.uuid4()}/login"),
        ("DELETE", f"{CONNECTIONS}/{uuid.uuid4()}"),
    ]
    assert len(paths) == 6, paths
    for method, path in paths:
        response = await client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"
        assert response.content == b"", f"{method} {path}: {response.content[:200]!r}"


async def test_registering_and_disconnecting_leave_audit_entries(
    logged_in_client, volume, pool, tenant
) -> None:
    created = await _create(logged_in_client, "Drugie konto")
    await logged_in_client.delete(f"{CONNECTIONS}/{created['connection_id']}")

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT kind FROM audit_events WHERE organization_id = $1 AND kind LIKE 'CODEX%'",
            tenant["organization"],
        )
    assert sorted({row["kind"] for row in rows}) == [
        "CODEX_CONNECTION_DISCONNECTED",
        "CODEX_CONNECTION_LOGIN_STARTED",
        "CODEX_CONNECTION_REGISTERED",
    ], sorted({row["kind"] for row in rows})


async def test_a_refresh_of_a_disconnected_connection_is_refused(logged_in_client, volume) -> None:
    created = await _create(logged_in_client, "Drugie konto")
    await logged_in_client.delete(f"{CONNECTIONS}/{created['connection_id']}")
    refused = await logged_in_client.post(f"{CONNECTIONS}/{created['connection_id']}/refresh")
    assert refused.status_code == 409, refused.content[:300]


async def test_without_a_configured_volume_the_screen_says_so(
    logged_in_client, monkeypatch
) -> None:
    """No `WENEXT_CODEX_HOME` is a configuration fact, not an empty list of connections."""
    monkeypatch.delenv("WENEXT_CODEX_HOME", raising=False)
    listing = (await logged_in_client.get(CONNECTIONS)).json()
    assert listing["root"] == {"configured": False, "adopted": False}, listing["root"]
    refused = await logged_in_client.post(CONNECTIONS, json={"label": "Nie ma gdzie"})
    assert refused.status_code == 409, refused.content[:300]
    assert "WENEXT_CODEX_HOME" in refused.json()["detail"]


def test_the_stub_engine_matches_the_port() -> None:
    """A stub that drifted from the port would make every case above prove nothing about the code
    that runs in production."""
    from control_plane.workspace.engine import AppServerEngine

    for name in ("run_login", "read"):
        assert hasattr(StubEngine, name), name
        assert hasattr(AppServerEngine, name), name


def test_now_is_not_frozen() -> None:
    """Sanity: the module under test writes `last_read_at` from the process clock, so a test that
    compared it against a constant would be pinning the wrong thing."""
    assert datetime.now(UTC).year >= 2026
