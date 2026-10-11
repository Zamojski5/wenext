"""Narzędzia obsługi produktu z rozmowy: konta i społeczności (D-046, etap 1 punkt 2).

CO TE TESTY TRZYMAJĄ, poza tym że kod się wykonuje:

  * KLIKNIĘCIE I POLECENIE SŁOWNE ROBIĄ TO SAMO. `communities.observe` woła tę samą usługę co
    trasa ekranu, więc społeczność wzięta pod obserwację przez agenta wygląda dokładnie tak samo
    jak wzięta ręcznie: ten sam wiersz, ten sam wpis w audycie, ta sama kolejka odczytu.
  * NAZWA MÓWI, CO SIĘ STAŁO (E3). Obserwacja to czytanie publicznych wątków, członkostwo to
    zapisanie naszego konta na platformie. Do 20.09 jedno narzędzie robiło pierwsze i mówiło o
    drugim; tutaj stoi test na to, że `communities.join` woli odmówić, niż powtórzyć tę pomyłkę.
  * ODMOWA JEST DANĄ. Klucz, którego platforma nie zna, kończy się kartą w stanie `blad` z
    powodem, a nie wyjątkiem i nie ciszą.
  * SUKCES DOPIERO PO SKUTKU. Konto, którego nikt nie zalogował, nie dostaje stanu `polaczone`,
    choćby narzędzie zostało zawołane dziesięć razy.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.observe.scheduling import JOB_POLL
from control_plane.tools.capability import issue
from control_plane.tools.cards import (
    ACCOUNT_CONNECTED,
    ACCOUNT_DISCONNECTED,
    COMMUNITY_OBSERVED,
    COMMUNITY_OBSERVING,
    STATE_ERROR,
    fingerprint,
)
from control_plane.tools.registry import ToolRegistry

pytestmark = [pytest.mark.db, pytest.mark.p2]


async def _project(pool, tenant) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


async def _account(pool, tenant, project_id, *, state: str = "PROVISIONED") -> str:
    login = f"konto-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, $3) RETURNING id",
            tenant["organization"],
            login,
            state,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return login


def _registry(pool, tenant, project_id) -> ToolRegistry:
    return ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=uuid4(),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )


# -- społeczności ------------------------------------------------------------------------------


async def test_observing_a_community_from_the_conversation_does_what_the_screen_does(
    pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call(
        "communities.observe", {"community": "r/SaaS", "uzasadnienie": "rozmowy o produkcie"}
    )

    assert answer["stan"] == "WYKONANE", answer
    card = answer["karty"][0]
    assert card["rodzaj"] == "SPOLECZNOSC"
    assert card["stan"] == COMMUNITY_OBSERVING, "obserwacja włączona, nic jeszcze nie odczytane"
    assert card["obserwacja"] is True
    assert card["klucz"] == "r/SaaS"
    assert card["uzasadnienie"] == "rozmowy o produkcie", "zdanie agenta jedzie na karcie"
    assert card["powod"] is None
    assert "nie zapisuję" in card["co_to_znaczy"].casefold(), (
        "karta ma powiedzieć, czego obserwacja NIE robi, bo to jest pytanie, na które produkt "
        "przez jakiś czas odpowiadał źle"
    )
    assert [one["nazwa"] for one in card["akcje"]] == [], card["akcje"]

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT observe_enabled, platform FROM communities "
            "WHERE organization_id = $1 AND workspace_id = $2 AND community_key = $3",
            tenant["organization"],
            tenant["workspace"],
            "r/SaaS",
        )
        audited = await conn.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1 "
            "AND kind = 'COMMUNITY_OBSERVATION_ENABLED'",
            tenant["organization"],
        )
        polls = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_POLL,
        )
    assert row is not None and row["observe_enabled"] is True
    assert row["platform"] == "REDDIT"
    assert audited == 1, "obserwacja agenta zostawia ten sam ślad co włączona ręcznie"
    assert polls == 1, "pierwszy odczyt jest zamówiony, bo inaczej „obserwuję” nic nie znaczy"


async def test_a_second_observe_of_the_same_community_changes_nothing(pool, tenant) -> None:
    """Ponowne kliknięcie ma dać ten sam stan, nie drugą społeczność i nie błąd."""
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    first = await tools.call("communities.observe", {"community": "r/devops"})
    second = await tools.call("communities.observe", {"community": "r/devops"})

    assert first["stan"] == "WYKONANE" and second["stan"] == "WYKONANE", (first, second)
    async with pool.acquire() as conn:
        how_many = await conn.fetchval(
            "SELECT count(*) FROM communities WHERE organization_id = $1 AND community_key = $2",
            tenant["organization"],
            "r/devops",
        )
    assert how_many == 1


async def test_observing_again_after_a_read_says_observed_not_still_starting(pool, tenant) -> None:
    """Drugie wywołanie po pierwszym odczycie daje `obserwowana`, bo coś już stamtąd przyszło.

    Powód dla osobnego stanu jest praktyczny: kolejka odczytów potrafi stać, a karta mówiąca
    „obserwowana” od razu po włączeniu obiecywałaby treść, której nikt jeszcze nie pobrał.
    """
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)
    await tools.call("communities.observe", {"community": "r/python"})
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE communities SET last_polled_at = now() WHERE organization_id = $1 "
            "AND community_key = $2",
            tenant["organization"],
            "r/python",
        )

    # Inne argumenty, więc to NIE jest powtórzenie tego samego zapisu: rejestr rozpoznaje
    # powtórki po odcisku nazwy i argumentów i oddałby wtedy poprzednią kartę.
    again = await tools.call(
        "communities.observe", {"community": "r/python", "uzasadnienie": "wracam tam"}
    )

    assert again["stan"] == "WYKONANE", again
    assert again["karty"][0]["stan"] == COMMUNITY_OBSERVED
    assert again["karty"][0]["obserwacja"] is True


async def test_joining_with_our_account_refuses_instead_of_reporting_an_effect_it_never_had(
    pool, tenant
) -> None:
    """`communities.join` znaczy CZŁONKOSTWO KONTA i dziś odmawia, zamiast włączyć obserwację.

    To jest cała treść punktu 3 audytu UX z 20.09: narzędzie robiło jedno, a mówiło drugie.
    Odmowa ma nazwać to, czego produkt nie umie, i wskazać to, co umie, bo inaczej model
    spróbuje jeszcze raz tą samą drogą albo napisze człowiekowi, że dołączył.
    """
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("communities.join", {"community": "r/SaaS"})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    assert "communities.observe" in answer["powod"], answer["powod"]
    async with pool.acquire() as conn:
        how_many = await conn.fetchval(
            "SELECT count(*) FROM communities WHERE organization_id = $1 AND community_key = $2",
            tenant["organization"],
            "r/SaaS",
        )
    assert how_many == 0, "odmowa nie ma prawa po cichu włączyć obserwacji zamiast członkostwa"


async def test_a_key_the_platform_does_not_know_comes_back_as_a_card_with_a_reason(
    pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("communities.observe", {"community": "to nie jest subreddit"})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    card = answer["karty"][0]
    assert card["stan"] == STATE_ERROR
    assert card["powod"], "karta w błędzie bez powodu jest gorsza niż brak karty"
    assert card["akcje"] == []


async def test_leaving_a_community_nobody_observes_is_a_refusal_not_silence(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("communities.leave", {"community": "r/nikt-tu-nie-zaglada"})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    assert "nie jest obserwowana" in answer["powod"]


async def test_leaving_turns_observation_off_and_keeps_what_was_read(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)
    await tools.call("communities.observe", {"community": "r/startups"})

    answer = await tools.call("communities.leave", {"community": "r/startups"})

    assert answer["stan"] == "WYKONANE", answer
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT observe_enabled FROM communities WHERE organization_id = $1 "
            "AND community_key = $2",
            tenant["organization"],
            "r/startups",
        )
    assert row["observe_enabled"] is False
    assert answer["karty"][0]["akcje"][0]["nazwa"] == "obserwuj", "da się wrócić jednym kliknięciem"
    assert answer["karty"][0]["obserwacja"] is False


# -- konta -------------------------------------------------------------------------------------


async def test_a_project_without_accounts_gets_a_card_that_leads_where_it_is_fixed(
    pool, tenant
) -> None:
    """ZMIERZONE NA ŻYWO 20.09 (live-check #3): model sprawdził konta, dostał NIEDOSTEPNE i
    napisał o tym zdaniem, a w rozmowie nie było ANI JEDNEJ karty. Dla człowieka to rozmowa, w
    której nie ma czego kliknąć, choć brakuje dokładnie jednej rzeczy. Każdy NOWY projekt
    zaczyna się w tym stanie.
    """
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("accounts.status", {})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    assert "nie jest przypisane żadne konto" in answer["powod"]
    card = answer["karty"][0]
    assert card["rodzaj"] == "KONTO"
    assert card["stan"] == "niedostepne", "logowanie nie pomoże, bo nie ma czego logować"
    assert card["akcje"] == []
    assert "Profile i konta" in card["dlaczego_bez_akcji"]
    assert card["odsylacz"].endswith("/people")


async def test_an_account_nobody_logged_in_is_not_connected(pool, tenant, monkeypatch) -> None:
    project_id = await _project(pool, tenant)
    login = await _account(pool, tenant, project_id, state="PROVISIONED")
    monkeypatch.delenv("WENEXT_BROWSER_READER_URL", raising=False)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("accounts.status", {})

    card = answer["karty"][0]
    assert card["rodzaj"] == "KONTO"
    assert card["konto"] == login
    assert card["stan"] == ACCOUNT_DISCONNECTED
    assert card["akcje"][0]["nazwa"] == "polacz"
    assert card["akcje"][0]["trasa"] == {"metoda": "POST", "sciezka": "/api/capsule/session"}


async def test_a_card_that_cannot_check_says_so_instead_of_being_quietly_disconnected(
    pool, tenant, monkeypatch
) -> None:
    """Karta bez możliwości sprawdzenia MÓWI to, zamiast milczeć w `brak_polaczenia`.

    ZMIERZONE NA ŻYWO 20.09 (sesja 01ef065e): `accounts.status` oddało `brak_polaczenia` z
    `powod: null` o koncie, na którym kapsuła w tej samej chwili czytała Reddita. Powód był
    policzony i nie jechał tam, gdzie patrzy człowiek. Osobna przyczyna tamtego przebiegu, czyli
    brak zmiennych kapsuły w procesie serwera narzędzi, jest naprawiona w `_configure_tool_server`
    i pilnowana w `test_runtime_profile.py`; TU pilnujemy tego, co widzi człowiek, gdy
    sprawdzenie i tak się nie uda.

    `powod` zostaje zarezerwowany dla stanu `blad` zgodnie z kontraktem kart: brak połączenia to
    nie błąd, a mimo to wymaga wyjaśnienia, więc zdanie ma własne pole.
    """
    project_id = await _project(pool, tenant)
    await _account(pool, tenant, project_id, state="PROVISIONED")
    monkeypatch.delenv("WENEXT_BROWSER_READER_URL", raising=False)
    tools = _registry(pool, tenant, project_id)

    card = (await tools.call("accounts.status", {}))["karty"][0]

    assert card["stan"] == ACCOUNT_DISCONNECTED
    assert card["powod"] is None, "kontrakt: powod jest dla stanu blad"
    assert "w tym procesie" in card["odczyt_kapsuly"], card


async def test_a_ready_account_has_nothing_to_connect(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    await _account(pool, tenant, project_id, state="READY")
    tools = _registry(pool, tenant, project_id)

    status = await tools.call("accounts.status", {})
    connect = await tools.call("accounts.connect", {})

    assert status["karty"][0]["stan"] == ACCOUNT_CONNECTED
    assert status["karty"][0]["akcje"] == []
    assert connect["stan"] == "WYKONANE", connect
    assert "już połączone" in connect["uwaga"]


async def test_asking_for_a_connection_waits_for_the_person(pool, tenant) -> None:
    """Agent nie wystawia sobie biletu do przeglądarki człowieka. Prosi i czeka."""
    project_id = await _project(pool, tenant)
    await _account(pool, tenant, project_id, state="PROVISIONED")
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("accounts.connect", {})

    assert answer["stan"] == "OCZEKUJE", answer
    assert answer["karty"][0]["stan"] == ACCOUNT_DISCONNECTED
    assert "człowiek" in answer["powod"] or "człowiek" in answer["wskazowka"]


async def test_connecting_an_account_that_is_not_in_this_project_is_refused(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    await _account(pool, tenant, project_id)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("accounts.connect", {"account": "cudze-konto"})

    assert answer["stan"] == "BLAD", answer
    assert "nie jest przypisane" in answer["powod"]


# -- odcisk ------------------------------------------------------------------------------------


async def test_the_fingerprint_changes_with_state_and_not_with_content(pool, tenant) -> None:
    """Uzgodnione z ekranem: strumień niesie sam odcisk, a ekran przeładowuje stronę po jego
    zmianie. Odcisk liczony z treści przeładowywałby stronę przy każdej poprawce szkicu."""
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)
    joined = await tools.call("communities.observe", {"community": "r/rust"})
    left = await tools.call("communities.leave", {"community": "r/rust"})

    assert fingerprint(joined["karty"]) != fingerprint(left["karty"])
    same_card = dict(joined["karty"][0])
    same_card["uzasadnienie"] = "zupełnie inne zdanie"
    assert fingerprint([same_card]) == fingerprint(joined["karty"])


# -- publikacja i powierzenie ------------------------------------------------------------------


async def _proposal(pool, tenant, project_id, login: str) -> str:
    """Propozycja czekająca na zgodę, zapisana tą samą drogą, co robi to `draft.create`."""
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from community_os_contracts.enums import SemanticCapability
    from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
    from control_plane.approval.store import ApprovalStore

    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE organization_id = $1 "
            "AND external_account_id = $2",
            tenant["organization"],
            login,
        )
    store = ApprovalStore(pool)
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account_id,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid4(),
            account=UUID(str(account_id)),
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id="t3_pub001",
            target_url="https://www.reddit.com/r/devops/comments/pub001/x/",
            source_fingerprint=f"session:{uuid4()}",
            text_and_media="odpowiedź, która czeka na zgodę",
            intelligence_profile_version=uuid4(),
            plan_version=project_id,
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    return str(revision.id)


async def test_publishing_without_a_delegation_refuses_and_does_not_publish(pool, tenant) -> None:
    """Bez podpisanego powierzenia nic nie idzie na zewnątrz, a karta mówi, czego brakuje."""
    project_id = await _project(pool, tenant)
    login = await _account(pool, tenant, project_id, state="READY")
    revision_id = await _proposal(pool, tenant, project_id, login)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("drafts.publish", {"revision_id": revision_id})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    assert "powierzenie" in answer["powod"].lower() or "delegacj" in answer["powod"].lower()
    card = answer["karty"][0]
    assert card["rodzaj"] == "SZKIC"
    assert card["delegacja_pozwala"] is False
    assert [one["nazwa"] for one in card["akcje"]] == ["popraw"], (
        "„opublikuj” bez powierzenia nie ma prawa się pojawić: zatwierdzenie i tak odmówi, a "
        "przycisk, który odmawia po kliknięciu, jest obietnicą pękającą w ręku"
    )
    assert card["dlaczego_bez_akcji"], "brak przycisku nie mówi sam, DLACZEGO go nie ma"
    async with pool.acquire() as conn:
        requests = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1",
            tenant["organization"],
        )
    assert requests == 0, "nic nie trafiło do publikatora"


async def test_publishing_with_nothing_pending_says_so(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("drafts.publish", {})

    assert answer["stan"] == "NIEDOSTEPNE", answer
    assert "nie ma czekającej propozycji" in answer["powod"]


async def test_proposing_a_delegation_prepares_a_scope_for_a_human_signature(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    login = await _account(pool, tenant, project_id, state="READY")
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call(
        "delegation.propose",
        {
            "communities": ["r/SaaS", "r/devops"],
            "actions": ["REPLY"],
            "uzasadnienie": "tam toczą się rozmowy o pierwszych klientach",
        },
    )

    assert answer["stan"] == "OCZEKUJE", answer
    card = answer["karty"][0]
    assert card["rodzaj"] == "POWIERZENIE"
    assert card["stan"] == "do_podpisu"
    assert card["spolecznosci"] == ["r/SaaS", "r/devops"]
    assert card["limity"]["publikacje_doba"] >= 1, "sufity, które zwiąże podpis, są widoczne"
    sign_action = card["akcje"][0]
    assert sign_action["nazwa"] == "powierz"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE organization_id=$1 AND external_account_id=$2",
            tenant["organization"], login,
        )
    assert sign_action["payload"]["accounts"] == [str(account_id)]
    assert sign_action["payload"]["communities"] == ["r/SaaS", "r/devops"]

    async with pool.acquire() as conn:
        signed = await conn.fetchval(
            "SELECT count(*) FROM project_delegations WHERE organization_id = $1",
            tenant["organization"],
        )
    assert signed == 0, "agent NIE podpisuje powierzenia, tylko przygotowuje zakres"


async def test_a_scope_without_communities_or_actions_is_refused(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)

    answer = await tools.call("delegation.propose", {"communities": [], "actions": ["REPLY"]})

    assert answer["stan"] == "BLAD", answer
    assert "nie jest zakresem" in answer["powod"]


async def test_research_can_be_prepared_without_social_accounts_or_publication_rights(pool, tenant):
    project_id = await _project(pool, tenant)
    tools = _registry(pool, tenant, project_id)
    answer = await tools.call("delegation.propose", {"communities": [], "actions": ["RESEARCH"]})
    assert answer["stan"] == "OCZEKUJE"
    card = answer["karty"][0]
    payload = card["akcje"][0]["payload"]
    assert payload["mode"] == "RESEARCH"
    assert payload["accounts"] == [] and payload["actions"] == ["RESEARCH"]
    assert payload["budget"]["publications_per_day"] == 0
    assert payload["budget"]["model_turns_per_day"] == 4
    assert card["limity"]["zaplanowane_powroty"] == payload["budget"]["scheduled_returns"]


def test_the_two_account_maps_say_the_same_thing() -> None:
    """Karta z narzędzia i karta z projekcji muszą mówić o jednym koncie jeden stan. Dwie mapy
    istnieją, bo jedna jest w procesie narzędzi, a druga w API; równość pilnuje ten test."""
    from control_plane.sessions.projection import _ACCOUNT_STATE
    from control_plane.tools.registry import _ACCOUNT_CARD_STATE

    assert _ACCOUNT_STATE == _ACCOUNT_CARD_STATE
