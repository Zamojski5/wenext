"""Awaryjne ustawienie hasła i sesja trybu lokalnego, przeciwko PostgreSQL.

CZEGO PILNUJĄ TE TESTY, bo obie te drogi omijają zwykłe drzwi i dlatego muszą być wąskie:

  * hasło z ARGUMENTU zamiast ze zmiennej (zostaje w `ps` i w historii powłoki);
  * dwa pasujące konta i skrypt, który wybiera pierwsze, czyli zmienia hasło komuś innemu;
  * hasło ustawione bez śladu w dzienniku;
  * sesja bez hasła otwarta na wdrożeniu, które ma drzwi z internetu;
  * sesja bez hasła otwarta, choć przełącznik jest wyłączony.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from control_plane.identity.local_access import LOCAL_AUTOLOGIN_VARIABLE, PUBLIC_DOMAIN_VARIABLE
from control_plane.identity.password_cli import MIN_PASSWORD_LENGTH, ResetRefused, set_password
from control_plane.identity.store import IdentityStore

pytestmark = pytest.mark.db

GOOD_PASSWORD = "nowe-haslo-wlasciciela"


def local_client(app) -> httpx.AsyncClient:
    """Klient, którego rozmowa NAPRAWDĘ wygląda na lokalną: oba końce na pętli zwrotnej.

    Domyślny `client` z suity puka pod `http://control-plane` i nie podaje adresu dzwoniącego,
    czyli wygląda dokładnie tak, jak wywołanie z sieci; trasa odmawia mu i to jest poprawne
    zachowanie, a nie przeszkoda w teście. Tryb lokalny opisuje maszynę, więc test, który go
    sprawdza, musi opisać tę maszynę.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
        base_url="http://127.0.0.1",
    )


async def _user(pool, *, email: str | None, organization: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with pool.acquire() as conn:
        organization_id = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", organization
        )
        user_id = await conn.fetchval(
            "INSERT INTO users (email, credential_hash) VALUES ($1, $2) RETURNING id",
            email,
            "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$" + "a" * 43,
        )
        await conn.execute(
            "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'OWNER')",
            user_id,
            organization_id,
        )
    return user_id, organization_id


async def test_the_new_password_really_opens_the_door(pool) -> None:
    address = f"wlasciciel-{uuid.uuid4().hex[:8]}@firma.test"
    user_id, _ = await _user(pool, email=address, organization=f"firma-{uuid.uuid4().hex[:6]}")

    async with pool.acquire() as conn:
        changed = await set_password(conn, email=address, organization=None, password=GOOD_PASSWORD)
    assert changed == user_id

    store = IdentityStore(pool)
    assert await store.verify_login(email=address, password=GOOD_PASSWORD) is not None
    assert await store.verify_login(email=address, password="stare-haslo") is None


async def test_an_account_without_an_address_is_found_by_its_organization(pool) -> None:
    """Konto przeniesione ze starego operatora (0018) nie ma adresu; to jest jego jedyna droga."""
    organization = f"pilot-{uuid.uuid4().hex[:6]}"
    user_id, _ = await _user(pool, email=None, organization=organization)

    async with pool.acquire() as conn:
        changed = await set_password(
            conn,
            email=None,
            organization=organization,
            password=GOOD_PASSWORD,
            set_email=f"nowy-{uuid.uuid4().hex[:6]}@firma.test",
        )
    assert changed == user_id
    async with pool.acquire() as conn:
        address = await conn.fetchval("SELECT email FROM users WHERE id = $1", user_id)
    assert address is not None, "USER_SET_EMAIL miało nadać adres kontu bez adresu"


async def test_two_matches_are_a_refusal_not_a_guess(pool) -> None:
    organization = f"dwoje-{uuid.uuid4().hex[:6]}"
    await _user(pool, email=None, organization=organization)
    async with pool.acquire() as conn:
        organization_id = await conn.fetchval(
            "SELECT id FROM organizations WHERE name = $1", organization
        )
        second = await conn.fetchval(
            "INSERT INTO users (email, credential_hash) VALUES (NULL, $1) RETURNING id",
            "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$" + "b" * 43,
        )
        await conn.execute(
            "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'MEMBER')",
            second,
            organization_id,
        )
        with pytest.raises(ResetRefused, match="nie zgaduję"):
            await set_password(conn, email=None, organization=organization, password=GOOD_PASSWORD)


async def test_a_short_password_is_refused_before_anything_is_written(pool) -> None:
    address = f"krotkie-{uuid.uuid4().hex[:8]}@firma.test"
    await _user(pool, email=address, organization=f"firma-{uuid.uuid4().hex[:6]}")
    async with pool.acquire() as conn:
        with pytest.raises(ResetRefused, match=str(MIN_PASSWORD_LENGTH)):
            await set_password(conn, email=address, organization=None, password="krotkie")
    store = IdentityStore(pool)
    assert await store.verify_login(email=address, password="krotkie") is None


async def test_the_change_leaves_a_row_in_the_log_without_the_password(pool) -> None:
    address = f"dziennik-{uuid.uuid4().hex[:8]}@firma.test"
    user_id, _ = await _user(pool, email=address, organization=f"firma-{uuid.uuid4().hex[:6]}")
    async with pool.acquire() as conn:
        await set_password(conn, email=address, organization=None, password=GOOD_PASSWORD)
        rows = await conn.fetch(
            "SELECT payload::text AS payload FROM audit_events "
            "WHERE kind = 'USER_PASSWORD_RESET' AND subject_ref = $1",
            f"user:{user_id}",
        )
    assert len(rows) == 1, rows
    assert GOOD_PASSWORD not in rows[0]["payload"]


async def test_the_local_session_route_refuses_when_the_switch_is_off(client, monkeypatch) -> None:
    """JEDNA REGUŁA TEJ TRASY: albo otwiera sesję, albo odpowiada PUSTYM 401.

    Powód odmowy idzie do logu serwera, nie do ciała. Zdanie „nie ma domeny, więc tryb lokalny
    jest wyłączony" powiedziane obcemu bez poświadczenia opisuje mu wdrożenie, a granica
    operatora ma nie odpowiadać na pytania zadane bez poświadczenia. Dzięki tej regule
    `test_operator_boundary` nie potrzebuje wyjątku dla tej jednej trasy.
    """
    monkeypatch.delenv(LOCAL_AUTOLOGIN_VARIABLE, raising=False)
    response = await client.post("/api/auth/local-session")
    assert response.status_code == 401, response.content[:200]
    assert response.content == b"", response.content[:200]


async def test_the_local_session_route_refuses_on_a_deployment_with_a_domain(
    app, tenant, monkeypatch
) -> None:
    """Serwer z domeną odmawia NAWET przy włączonym przełączniku. To jest ten warunek, który
    chroni wdrożenie produkcyjne przed zmienną wpisaną tam przez pomyłkę."""
    monkeypatch.setenv(LOCAL_AUTOLOGIN_VARIABLE, "wlasciciel@firma.test")
    monkeypatch.setenv(PUBLIC_DOMAIN_VARIABLE, "wenext.example")
    response = await local_client(app).post("/api/auth/local-session")
    assert response.status_code == 401, response.content[:200]
    assert response.content == b"", response.content[:200]
    assert "wenext_operator_session" not in response.cookies, dict(response.cookies)


async def test_the_local_session_route_opens_a_session_for_the_named_owner(
    app, pool, monkeypatch
) -> None:
    address = f"lokalny-{uuid.uuid4().hex[:8]}@firma.test"
    user_id, organization_id = await _user(
        pool, email=address, organization=f"lokalna-{uuid.uuid4().hex[:6]}"
    )
    monkeypatch.setenv(LOCAL_AUTOLOGIN_VARIABLE, address)
    monkeypatch.delenv(PUBLIC_DOMAIN_VARIABLE, raising=False)

    response = await local_client(app).post("/api/auth/local-session")

    assert response.status_code == 200, response.content[:300]
    assert response.json()["session"]["user_id"] == str(user_id)
    # Ślad w dzienniku przy KAŻDEJ sesji auto: bez niego „kto to zrobił" nie ma odpowiedzi.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT actor_ref FROM audit_events WHERE kind = 'OPERATOR_SESSION_OPENED' "
            "AND organization_id = $1 AND actor_ref = 'local-mode'",
            organization_id,
        )
    assert len(rows) == 1, rows
