"""Stan konta z RZECZYWISTOŚCI, nie tylko z wiersza (D-046).

ZMIERZONE NA LIVE 20.09, i to jest cały powód istnienia tego pliku: kapsuła była zalogowana na
koncie `Demo_Maintainer` i czytała Reddita, a `social_accounts.state` stało na `PROVISIONED`,
bo tej kolumny nie aktualizował dotąd ŻADEN kod w tym repozytorium. Karta konta czyta wiersz,
więc pokazywała „Brakuje połączenia” obok działającego konta.

Testy trzymają cztery granice: konto wraca do gotowości tylko przez maszynę stanów, tylko gdy
nazwa się zgadza, nigdy z ręcznej decyzji człowieka (PAUSED), i nigdy na podstawie milczenia
kapsuły.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.workspace.account_state import note_capsule_login

pytestmark = [pytest.mark.db, pytest.mark.p4]


async def _account(pool, tenant, *, login: str, state: str) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id, "
                "state) VALUES ($1, 'REDDIT', $2, $3) RETURNING id",
                tenant["organization"],
                login,
                state,
            )
        )


async def _state(pool, account_id: str) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval("SELECT state FROM social_accounts WHERE id = $1", account_id)
        )


async def test_a_logged_in_capsule_makes_the_account_ready(pool, tenant) -> None:
    login = f"konto-{uuid4().hex[:8]}"
    account_id = await _account(pool, tenant, login=login, state="PROVISIONED")

    noted = await note_capsule_login(
        pool,
        organization_id=tenant["organization"],
        platform="REDDIT",
        logged_in=True,
        username=login,
        actor_ref="operator:test",
    )

    assert noted.changed is True
    assert await _state(pool, account_id) == "READY"
    async with pool.acquire() as conn:
        audited = await conn.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1 "
            "AND kind = 'SOCIAL_ACCOUNT_VERIFIED'",
            tenant["organization"],
        )
    assert audited == 1, "przejście konta w gotowość jest zdarzeniem, nie cichą poprawką"


async def test_noting_the_same_login_twice_changes_nothing_the_second_time(pool, tenant) -> None:
    login = f"konto-{uuid4().hex[:8]}"
    await _account(pool, tenant, login=login, state="PROVISIONED")
    kwargs = {
        "organization_id": tenant["organization"],
        "platform": "REDDIT",
        "logged_in": True,
        "username": login,
        "actor_ref": "operator:test",
    }

    first = await note_capsule_login(pool, **kwargs)
    second = await note_capsule_login(pool, **kwargs)

    assert first.changed is True
    assert second.changed is False and second.reason is None


async def test_a_name_that_matches_no_account_changes_nothing_and_says_why(pool, tenant) -> None:
    await _account(pool, tenant, login=f"konto-{uuid4().hex[:8]}", state="PROVISIONED")

    noted = await note_capsule_login(
        pool,
        organization_id=tenant["organization"],
        platform="REDDIT",
        logged_in=True,
        username="ktos-zupelnie-inny",
        actor_ref="operator:test",
    )

    assert noted.changed is False
    assert "nie jest zarejestrowane" in noted.reason


async def test_a_paused_account_does_not_come_back_because_a_browser_is_logged_in(
    pool, tenant
) -> None:
    """PAUSED i QUARANTINED to decyzje człowieka. Zalogowana przeglądarka ich nie cofa."""
    login = f"konto-{uuid4().hex[:8]}"
    account_id = await _account(pool, tenant, login=login, state="PAUSED")

    noted = await note_capsule_login(
        pool,
        organization_id=tenant["organization"],
        platform="REDDIT",
        logged_in=True,
        username=login,
        actor_ref="operator:test",
    )

    assert noted.changed is False
    assert "decyzja człowieka" in noted.reason
    assert await _state(pool, account_id) == "PAUSED"


async def test_a_capsule_that_says_nothing_changes_nothing(pool, tenant) -> None:
    """Milczenie kapsuły nie jest dowodem w żadną stronę."""
    login = f"konto-{uuid4().hex[:8]}"
    account_id = await _account(pool, tenant, login=login, state="PROVISIONED")

    silent = await note_capsule_login(
        pool,
        organization_id=tenant["organization"],
        platform="REDDIT",
        logged_in=None,
        username=None,
        actor_ref="operator:test",
    )
    nameless = await note_capsule_login(
        pool,
        organization_id=tenant["organization"],
        platform="REDDIT",
        logged_in=True,
        username=None,
        actor_ref="operator:test",
    )

    assert silent.changed is False and "nie zgłasza zalogowanej sesji" in silent.reason
    assert nameless.changed is False and "nie podała nazwy" in nameless.reason
    assert await _state(pool, account_id) == "PROVISIONED"


async def test_the_status_tool_reports_the_account_as_connected_after_a_capsule_read(
    pool, tenant, monkeypatch
) -> None:
    """Cała droga: sonda kapsuły, zapis stanu, karta. Bez kapsuły, z atrapą sondy."""
    from dataclasses import dataclass
    from datetime import UTC, datetime

    from control_plane.observe import capsule_session
    from control_plane.tools.capability import issue
    from control_plane.tools.cards import ACCOUNT_CONNECTED
    from control_plane.tools.registry import ToolRegistry

    login = f"konto-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'PROVISIONED') RETURNING id",
            tenant["organization"],
            login,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )

    @dataclass(frozen=True)
    class _Session:
        """To, co zwraca `RedditBrowserSource.session_state()`: trzy pola, nic więcej."""

        logged_in: bool
        username: str
        checked_at: datetime

    class _Probe:
        async def session_state(self):
            return _Session(True, login, datetime.now(UTC))

    monkeypatch.setattr(capsule_session, "probe_from_env", lambda *a, **k: _Probe())

    tools = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=uuid4(),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )
    answer = await tools.call("accounts.status", {})

    assert answer["stan"] == "WYKONANE", answer
    assert answer["kapsula"]["zalogowana"] is True
    assert answer["kapsula"]["zmieniono_stan_konta"] is True
    assert answer["karty"][0]["stan"] == ACCOUNT_CONNECTED, answer["karty"]
    assert answer["karty"][0]["akcje"] == [], "nie ma czego łączyć"
