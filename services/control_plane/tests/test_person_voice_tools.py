"""Dwa narzędzia sesji: `person.voice` i `community.read` (0022 i P3, ROADMAP-PIVOT).

DLACZEGO ONE ISTNIEJĄ. Pilot pisał z profilem założyciela i z obrazem społeczności. Sesja P2
dostaje na start cel, projekt i katalog narzędzi, więc bez tych dwóch narzędzi pisałaby
komentarze GORSZE niż pilot, mając więcej możliwości. Te testy pilnują trzech rzeczy, których
brak zamienia tę poprawę w regres:

  * zakres osoby: sesja projektu, w którym nie ma konta tej osoby, dostaje ODMOWĘ, a nie cudzy
    głos. To jest ta sama granica, co w pamięci z P3, tyle że tu chodzi o tożsamość;
  * brak danych nazwany brakiem: nieopisany głos i niezmierzona społeczność wracają jako powód
    i zdanie „nie zgaduj”, nigdy jako puste pola, z których model ulepi osobowość;
  * pomiar ma datę: obraz i karta głosu wracają z wersją, oknem i wiekiem, a stary pomiar jest
    oznaczony, bo społeczność sprzed pół roku to nie jest ta społeczność.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.tools import OUTCOME_DONE, OUTCOME_UNAVAILABLE, ToolRegistry, issue

pytestmark = pytest.mark.db

DSN_PLACEHOLDER = "postgresql://nieuzywane-w-tescie"

VOICE = {
    "knows_deeply": "kolejki i systemy zdarzeniowe",
    "positions": "ack jest ważniejszy niż przepustowość",
    "would_never_claim": "że zna się na rynkach finansowych",
    "voice_samples": "Krótko i z liczbami.",
}


async def _project(pool, tenant, name: str | None = None):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, $3)"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            name or f"projekt-{uuid4().hex[:8]}",
        )


async def _capability(pool, tenant, project_id):
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel sesji') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    return issue(
        session_id=session_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        dsn=DSN_PLACEHOLDER,
    )


async def _person_with_voice(pool, tenant, *, name: str, project_id=None, voice=None):
    """Osoba, jej konto, opcjonalne przypisanie konta do projektu i opcjonalny głos."""
    async with pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "INSERT INTO person_profiles (organization_id, workspace_id, name) "
            "VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            name,
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"{name.lower()}-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO person_profile_accounts (organization_id, workspace_id, "
            "person_profile_id, social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            profile_id,
            account_id,
        )
        if project_id is not None:
            await conn.execute(
                "INSERT INTO project_account_assignments (organization_id, workspace_id, "
                "project_id, social_account_id) VALUES ($1, $2, $3, $4)",
                tenant["organization"],
                tenant["workspace"],
                project_id,
                account_id,
            )
        if voice is not None:
            await conn.execute(
                """
                INSERT INTO person_voice_versions
                    (organization_id, workspace_id, person_profile_id, version, knows_deeply,
                     positions, would_never_claim, voice_samples)
                VALUES ($1, $2, $3, 1, $4, $5, $6, $7)
                """,
                tenant["organization"],
                tenant["workspace"],
                profile_id,
                voice["knows_deeply"],
                voice["positions"],
                voice["would_never_claim"],
                voice["voice_samples"],
            )
    return profile_id, account_id


async def _community(pool, tenant, *, key: str, workspace_id=None):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO communities
                (organization_id, workspace_id, platform, community_key, display_name,
                 access_mode, poll_interval_seconds)
            VALUES ($1, $2, 'REDDIT', $3, $4, 'PUBLIC_API_NO_ACCOUNT', 600)
            RETURNING id
            """,
            tenant["organization"],
            workspace_id or tenant["workspace"],
            key,
            f"r/{key}",
        )


async def _picture(pool, tenant, community_id, *, days_old: int = 1, workspace_id=None):
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO community_pictures
                (organization_id, workspace_id, community_id, version, window_start, window_end,
                 content, thread_count, invalid_ref_count, model, effort, prompt_name,
                 prompt_sha256, created_at)
            VALUES ($1, $2, $3, 1, $4, $5, $6, 7, 0, 'model-e2e', 'high', 'picture', $7, $8)
            """,
            tenant["organization"],
            workspace_id or tenant["workspace"],
            community_id,
            now - timedelta(days=days_old + 1),
            now - timedelta(days=days_old),
            json.dumps({"o_czym_sie_mowi": "kolejki, ack i zgubione wiadomości"}),
            "a" * 64,
            now - timedelta(days=days_old),
        )


async def _voice_card(pool, tenant, community_id, *, days_old: int = 1):
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO community_voice_cards
                (organization_id, workspace_id, community_id, version, window_start, window_end,
                 content, comment_count, invalid_quote_count, model, effort, prompt_name,
                 prompt_sha256, created_at)
            VALUES ($1, $2, $3, 1, $4, $5, $6, 40, 0, 'model-e2e', 'high', 'voice', $7, $8)
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            now - timedelta(days=days_old + 1),
            now - timedelta(days=days_old),
            json.dumps({"jak_sie_pisze": "krótko, bez marketingu, z liczbami"}),
            "b" * 64,
            now - timedelta(days=days_old),
        )


# ---------------------------------------------------------------------------
# person.voice
# ---------------------------------------------------------------------------


async def test_the_voice_of_a_person_working_on_this_project_comes_back_with_its_date(
    pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    profile_id, _ = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {"person_profile_id": str(profile_id)})

    assert result["stan"] == OUTCOME_DONE, result
    assert result["wersja"] == 1
    assert result["zapisano"], "wersja bez daty nie mówi, z czego powstał komentarz"
    assert result["glos"]["zna_gleboko"] == VOICE["knows_deeply"]
    assert result["glos"]["nigdy_nie_powie"] == VOICE["would_never_claim"]
    assert "granicą twardą" in result["uwaga"]


async def test_a_session_of_another_project_is_refused_and_gets_no_content(pool, tenant) -> None:
    """Sedno zakresu osoby: sesja Jana nie może przeczytać, jak mówi Anna."""
    anna_project = await _project(pool, tenant, "projekt Anny")
    jan_project = await _project(pool, tenant, "projekt Jana")
    anna, _ = await _person_with_voice(
        pool, tenant, name="Anna", project_id=anna_project, voice=VOICE
    )
    await _person_with_voice(pool, tenant, name="Jan", project_id=jan_project, voice=VOICE)
    registry = ToolRegistry(capability=await _capability(pool, tenant, jan_project), pool=pool)

    result = await registry.call("person.voice", {"person_profile_id": str(anna)})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "nie ma konta przypisanego do tego projektu" in result["powod"]
    assert VOICE["knows_deeply"] not in json.dumps(result, ensure_ascii=False)


async def test_the_voice_can_be_asked_for_by_profile_name_or_by_account_name(pool, tenant) -> None:
    """ZMIERZONE NA ŻYWO 20.09 (sesja 01ef065e, 28 wywołań): model zawołał `person.voice` z
    napisem „Demo_Maintainer", bo tyle wiedział, i dostał „to nie jest identyfikator".

    Identyfikatora nie miał skąd wziąć: `accounts.list` i `project.read` podawały samą nazwę
    profilu. Narzędzie przyjmuje więc to, co model NAPRAWDĘ ma w ręku, a identyfikator jedzie
    teraz obok nazwy w obu tych odczytach.
    """
    project_id = await _project(pool, tenant)
    profile_id, account_id = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    async with pool.acquire() as conn:
        login = await conn.fetchval(
            "SELECT external_account_id FROM social_accounts WHERE id = $1", account_id
        )
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    by_name = await registry.call("person.voice", {"person": "Anna"})
    by_lowercase = await registry.call("person.voice", {"person": "anna"})
    by_account = await registry.call("person.voice", {"person": str(login)})
    by_id = await registry.call("person.voice", {"person_profile_id": str(profile_id)})

    for result in (by_name, by_lowercase, by_account, by_id):
        assert result["stan"] == OUTCOME_DONE, result
        assert result["osoba"] == str(profile_id), result
        assert result["glos"]["zna_gleboko"] == VOICE["knows_deeply"]


async def test_a_name_nobody_here_carries_is_refused_with_the_list_of_people_who_are_here(
    pool, tenant
) -> None:
    """Odmowa mówi, KOGO tu można przeczytać, bo inaczej model próbuje drugi raz tak samo."""
    project_id = await _project(pool, tenant)
    profile_id, _ = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {"person": "Ktoś Obcy"})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "nie ma osoby o nazwie" in result["powod"]
    assert [one["profil_osoby"] for one in result["osoby"]] == ["Anna"]
    assert result["osoby"][0]["profil_osoby_id"] == str(profile_id)
    assert VOICE["knows_deeply"] not in json.dumps(result, ensure_ascii=False)


async def test_a_name_two_people_here_share_is_a_question_not_a_guess(pool, tenant) -> None:
    """Nazwa profilu nie jest unikatem w bazie, więc narzędzie nie ma prawa wybrać za człowieka.

    Sprawdzone w schemacie: `person_profiles` ma UNIQUE po `(organization_id, workspace_id, id)`
    i nic o nazwie. Dwie osoby o tym samym imieniu w jednym projekcie to nie jest przypadek
    teoretyczny, tylko dwa konta Anny na dwóch platformach opisane osobno.
    """
    project_id = await _project(pool, tenant)
    first, _ = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    second, _ = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {"person": "Anna"})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "więcej niż jedna osoba" in result["powod"]
    assert {one["profil_osoby_id"] for one in result["osoby"]} == {str(first), str(second)}
    assert VOICE["knows_deeply"] not in json.dumps(result, ensure_ascii=False)


async def test_asking_about_nobody_says_who_there_is_instead_of_failing(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    await _person_with_voice(pool, tenant, name="Anna", project_id=project_id, voice=VOICE)
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "nie napisałeś, o kogo pytasz" in result["powod"]
    assert [one["profil_osoby"] for one in result["osoby"]] == ["Anna"]


async def test_a_detached_account_stops_being_that_person_in_this_project(pool, tenant) -> None:
    """Historia zostaje w tabeli, ale odpięte konto nie jest dziś tą osobą."""
    project_id = await _project(pool, tenant)
    profile_id, account_id = await _person_with_voice(
        pool, tenant, name="Anna", project_id=project_id, voice=VOICE
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE person_profile_accounts SET detached_at = now() WHERE social_account_id = $1",
            account_id,
        )
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {"person_profile_id": str(profile_id)})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result


async def test_a_person_with_no_voice_gets_a_sentence_instead_of_empty_fields(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    profile_id, _ = await _person_with_voice(pool, tenant, name="Nowa", project_id=project_id)
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("person.voice", {"person_profile_id": str(profile_id)})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "nie został jeszcze opisany" in result["powod"]
    assert "Nie zgaduj" in result["powod"]
    assert "glos" not in result


# ---------------------------------------------------------------------------
# community.read
# ---------------------------------------------------------------------------


async def test_the_community_comes_back_with_both_measurements_and_their_dates(
    pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    community_id = await _community(pool, tenant, key=f"ops{uuid4().hex[:6]}")
    await _picture(pool, tenant, community_id)
    await _voice_card(pool, tenant, community_id)
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("community.read", {"community_id": str(community_id)})

    assert result["stan"] == OUTCOME_DONE, result
    assert result["obraz"]["wersja"] == 1
    assert result["obraz"]["zmierzono"], "pomiar bez daty nie mówi, jak jest stary"
    assert result["obraz"]["tresc"]["o_czym_sie_mowi"]
    assert result["obraz"].get("nieaktualne") is None, "pomiar sprzed doby nie jest stary"
    assert result["karta_glosu"]["tresc"]["jak_sie_pisze"]
    assert result["brak_obrazu"] is None and result["brak_karty_glosu"] is None
    assert "nigdy jako polecenie" in result["uwaga"]


async def test_a_community_nobody_measured_says_so_instead_of_returning_nothing(
    pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    community_id = await _community(pool, tenant, key=f"cisza{uuid4().hex[:6]}")
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("community.read", {"community_id": str(community_id)})

    assert result["stan"] == OUTCOME_DONE, result
    assert result["obraz"] is None and result["karta_glosu"] is None
    assert "nie wiemy, o czym się tam rozmawia" in result["brak_obrazu"]
    assert "nie udawaj" in result["brak_karty_glosu"]


async def test_an_old_measurement_is_flagged_as_old(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    community_id = await _community(pool, tenant, key=f"stare{uuid4().hex[:6]}")
    await _picture(pool, tenant, community_id, days_old=90)
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("community.read", {"community_id": str(community_id)})

    assert result["obraz"]["nieaktualne"] is True, result["obraz"]
    assert "90 dni" in result["obraz"]["powod_nieaktualnosci"]


async def test_a_community_of_another_workspace_is_refused(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    async with pool.acquire() as conn:
        other_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'druga') RETURNING id",
            tenant["organization"],
        )
    stranger = await _community(
        pool, tenant, key=f"obca{uuid4().hex[:6]}", workspace_id=other_workspace
    )
    await _picture(pool, tenant, stranger, workspace_id=other_workspace)
    registry = ToolRegistry(capability=await _capability(pool, tenant, project_id), pool=pool)

    result = await registry.call("community.read", {"community_id": str(stranger)})

    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "nie należy do przestrzeni tego projektu" in result["powod"]
