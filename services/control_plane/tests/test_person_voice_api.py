"""Głos osoby przez HTTP: odczyt, zapis kolejnych wersji i propozycja z profilu pilota (0022).

CZEGO TE TESTY PILNUJĄ, po kolei, bo każdy odpowiada innemu sposobowi zepsucia tej funkcji:

  * profil bez głosu mówi „nie ma”, a nie oddaje pustych pól, które ekran pokaże jak zapisane;
  * drugi zapis tworzy WERSJĘ 2, a nie zmienia wersji 1, więc historia zostaje;
  * propozycja z profilu pilota jest PROPOZYCJĄ: samo jej zobaczenie niczego nie zapisuje,
    a po pierwszej własnej wersji znika, żeby nie kusiła do nadpisania cudzej pracy;
  * profil z innej organizacji jest nie do odróżnienia od nieistniejącego;
  * zapis zostawia wpis w dzienniku audytu, bo „kto opisał, jak ta osoba mówi” to pytanie,
    które pada po komentarzu brzmiącym nie jak ona.

Wszystko idzie przez router z prawdziwą sesją, nigdy prosto do magazynu: test omijający trasę
dowodzi, że magazyn działa, i nie mówi nic o tym, czy trasa jest w ogóle zamontowana.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db

WORKSPACES = "/api/workspaces"

VOICE = {
    "knows_deeply": "utrzymanie systemów, których nikt nie chce dotykać",
    "positions": "przepisanie od zera rzadko się zwraca",
    "would_never_claim": "że zna się na rynkach finansowych",
    "voice_samples": "Piszę krótko. Bez ozdobników.",
}


def _voice_path(workspace_id: str, profile_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/profiles/{profile_id}/voice"


async def _profile(client, workspace_id: str, name: str) -> str:
    response = await client.post(f"{WORKSPACES}/{workspace_id}/profiles", json={"name": name})
    assert response.status_code == 201, response.content[:300]
    return response.json()["profile"]["person_profile_id"]


async def _account_under(client, workspace_id: str, profile_id: str, handle: str) -> str:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/accounts",
        json={
            "platform": "REDDIT",
            "external_account_id": handle,
            "person_profile_id": profile_id,
        },
    )
    assert response.status_code == 201, response.content[:300]
    return response.json()["account"]["social_account_id"]


async def _pilot_profile(pool, tenant) -> uuid.UUID:
    """Profil, z którym pisał pilot: `user_profile_versions` z 0004."""
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO user_profile_versions
                (organization_id, workspace_id, version, knows_deeply, positions,
                 would_never_claim, voice_samples)
            VALUES ($1, $2,
                    (SELECT coalesce(max(version), 0) + 1 FROM user_profile_versions
                      WHERE organization_id = $1 AND workspace_id = $2),
                    'kolejki i systemy zdarzeniowe', 'ack jest ważniejszy niż przepustowość',
                    'że zna się na prawie', 'Krótko i z liczbami.')
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
        )


async def test_a_profile_with_no_voice_says_so_instead_of_returning_empty_fields(
    logged_in_client, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Anna")

    response = await logged_in_client.get(_voice_path(workspace_id, profile_id))

    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["voice"] is None
    assert body["history"] == []
    assert body["proposal"] is None


async def test_the_second_write_is_version_two_and_the_first_one_stays(
    logged_in_client, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Anna")

    first = await logged_in_client.post(_voice_path(workspace_id, profile_id), json=VOICE)
    assert first.status_code == 201, first.content[:300]
    assert first.json()["voice"]["version"] == 1

    changed = {**VOICE, "positions": "przepisanie od zera zwraca się tylko przy zmianie modelu"}
    second = await logged_in_client.post(_voice_path(workspace_id, profile_id), json=changed)
    assert second.status_code == 201, second.content[:300]
    assert second.json()["voice"]["version"] == 2

    read = await logged_in_client.get(_voice_path(workspace_id, profile_id))
    body = read.json()
    assert body["voice"]["version"] == 2, "obowiązuje najnowsza"
    assert body["voice"]["positions"] == changed["positions"]
    versions = [row["version"] for row in body["history"]]
    assert versions == [2, 1], versions
    older = next(row for row in body["history"] if row["version"] == 1)
    assert older["positions"] == VOICE["positions"], "wersja 1 zmieniła się, a miała zostać"


async def test_the_pilot_profile_is_offered_only_before_the_first_own_version(
    logged_in_client, pool, tenant
) -> None:
    """Propozycja to DANE do kliknięcia: sam odczyt nie zapisuje ani jednej wersji."""
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Anna")
    await _account_under(logged_in_client, workspace_id, profile_id, f"m-{uuid.uuid4().hex[:8]}")
    await _pilot_profile(pool, tenant)

    offered = await logged_in_client.get(_voice_path(workspace_id, profile_id))
    proposal = offered.json()["proposal"]
    assert proposal is not None, "profil z kontem i przestrzeń z profilem pilota: ma być co przejąć"
    assert proposal["knows_deeply"] == "kolejki i systemy zdarzeniowe"
    assert offered.json()["history"] == [], "podgląd propozycji nie może niczego zapisać"

    taken = await logged_in_client.post(
        _voice_path(workspace_id, profile_id),
        json={
            "knows_deeply": proposal["knows_deeply"],
            "positions": proposal["positions"],
            "would_never_claim": proposal["would_never_claim"],
            "voice_samples": proposal["voice_samples"],
        },
    )
    assert taken.status_code == 201, taken.content[:300]

    after = await logged_in_client.get(_voice_path(workspace_id, profile_id))
    assert after.json()["proposal"] is None, "po własnej wersji nie ma czego proponować"
    assert after.json()["voice"]["knows_deeply"] == "kolejki i systemy zdarzeniowe"


async def test_a_profile_without_an_account_is_not_offered_the_pilots_voice(
    logged_in_client, pool, tenant
) -> None:
    """Profil bez konta jest szkicem. Głos pilota należy do człowieka, który publikował."""
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Szkic")
    await _pilot_profile(pool, tenant)

    response = await logged_in_client.get(_voice_path(workspace_id, profile_id))

    assert response.status_code == 200, response.content[:300]
    assert response.json()["proposal"] is None


async def test_three_of_the_four_fields_cannot_be_blank(logged_in_client, tenant) -> None:
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Anna")

    for field in ("knows_deeply", "positions", "would_never_claim"):
        response = await logged_in_client.post(
            _voice_path(workspace_id, profile_id), json={**VOICE, field: "   "}
        )
        assert response.status_code == 400, (field, response.status_code)

    only_samples_empty = await logged_in_client.post(
        _voice_path(workspace_id, profile_id), json={**VOICE, "voice_samples": ""}
    )
    assert only_samples_empty.status_code == 201, only_samples_empty.content[:300]


async def test_a_profile_from_another_workspace_is_a_404(logged_in_client, pool, tenant) -> None:
    """Cudzy identyfikator ma być nie do odróżnienia od nieistniejącego."""
    workspace_id = str(tenant["workspace"])
    async with pool.acquire() as connection:
        other_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca') RETURNING id"
        )
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obca ws') RETURNING id",
            other_organization,
        )
        stranger = await connection.fetchval(
            "INSERT INTO person_profiles (organization_id, workspace_id, name) "
            "VALUES ($1, $2, 'Obcy') RETURNING id",
            other_organization,
            other_workspace,
        )

    read = await logged_in_client.get(_voice_path(workspace_id, str(stranger)))
    assert read.status_code == 404, read.content[:300]

    write = await logged_in_client.post(_voice_path(workspace_id, str(stranger)), json=VOICE)
    assert write.status_code == 404, write.content[:300]

    async with pool.acquire() as connection:
        written = await connection.fetchval(
            "SELECT count(*) FROM person_voice_versions WHERE person_profile_id = $1", stranger
        )
    assert written == 0, "odmowa musi być odmową także w bazie"


async def test_the_write_leaves_an_audit_row_with_the_version(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    profile_id = await _profile(logged_in_client, workspace_id, "Anna")

    response = await logged_in_client.post(_voice_path(workspace_id, profile_id), json=VOICE)
    assert response.status_code == 201, response.content[:300]

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT payload FROM audit_events WHERE kind = 'PERSON_VOICE_VERSION_WRITTEN'"
            " AND subject_ref = $1",
            f"person_profile:{profile_id}",
        )
    assert len(rows) == 1, rows
    import json as _json

    payload = rows[0]["payload"]
    payload = _json.loads(payload) if isinstance(payload, str) else payload
    assert payload["version"] == 1, payload
