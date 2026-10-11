"""Człowiek zatwierdza i poprawia propozycję z ekranu rozmowy (D-046).

CO TE TESTY TRZYMAJĄ:

  * ZGODA CZŁOWIEKA WCHODZI DO TEJ SAMEJ KOLEJKI, co zgoda powierzenia. Nie ma drugiej drogi do
    publikacji, bo dwie drogi to dwie definicje tego, co wolno opublikować.
  * LIMITY PODPISU WIĄŻĄ TAKŻE CZŁOWIEKA; inaczej „sześć publikacji na dobę” znaczyłoby „sześć,
    chyba że ktoś kliknie”.
  * POPRAWKA TWORZY NOWĄ REWIZJĘ, a poprzednia zostaje. Nadpisanie tekstu w miejscu skasowałoby
    zapis tego, co agent naprawdę napisał.
  * CUDZA PROPOZYCJA JEST NIEZNALEZIONA, a nie zabroniona.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore

pytestmark = [pytest.mark.db, pytest.mark.p4]


async def _project(pool, tenant) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                "INSERT INTO projects (organization_id, workspace_id, name, goal) "
                "VALUES ($1, $2, $3, 'cel') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                f"projekt-{uuid4().hex[:8]}",
            )
        )


async def _account(pool, tenant, project_id) -> str:
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'READY') RETURNING id",
            tenant["organization"],
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return str(account_id)


async def _proposal(
    pool, tenant, account_id: str, *, text: str = "odpowiedź agenta", session_id=None,
    project_id=None,
) -> str:
    if project_id is None:
        async with pool.acquire() as conn:
            project_id = await conn.fetchval(
                "SELECT project_id FROM project_account_assignments "
                "WHERE organization_id=$1 AND social_account_id=$2 AND unassigned_at IS NULL "
                "ORDER BY assigned_at LIMIT 1", tenant["organization"], UUID(account_id),
            )
    store = ApprovalStore(pool)
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=UUID(account_id),
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
            account=UUID(account_id),
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id="t3_zgoda01",
            target_url="https://www.reddit.com/r/devops/comments/zgoda01/x/",
            # WIĄZANIE Z ROZMOWĄ: to jedyne pole, po którym trasa poprawki wie, gdzie dopisać
            # kartę nowej rewizji.
            source_fingerprint=f"session:{session_id or uuid4()}",
            text_and_media=text,
            intelligence_profile_version=uuid4(),
            plan_version=UUID(str(project_id)),
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    return str(revision.id)


def _path(tenant, project_id: str, revision_id: str, suffix: str) -> str:
    return (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}"
        f"/approvals/{revision_id}{suffix}"
    )


async def _sign(client, tenant, project_id: str, account_id: str, *, mode: str = "REVIEW"):
    return await client.post(
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/delegation",
        json={
            "mode": mode,
            "accounts": [account_id],
            "communities": ["r/devops"],
            "actions": ["REPLY"],
            "budget": {"publications_per_day": 6},
        },
    )


async def test_approving_puts_the_proposal_in_the_publisher_queue(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)
    assert (await _sign(logged_in_client, tenant, project_id, account_id)).status_code == 201

    response = await logged_in_client.post(_path(tenant, project_id, revision_id, "/approve"))

    assert response.status_code == 202, response.text
    card = response.json()["karta"]
    assert card["rodzaj"] == "SZKIC"
    assert card["stan"] == "publikuje", "„opublikowano” dopiero po potwierdzeniu skutku"
    assert card["akcje"] == []

    async with pool.acquire() as conn:
        requests = await conn.fetch(
            "SELECT project_id, delegation_id, consent_source FROM publication_requests "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
        decisions = await conn.fetchval(
            "SELECT count(*) FROM approval_decisions WHERE organization_id = $1",
            tenant["organization"],
        )
    assert len(requests) == 1, requests
    assert str(requests[0]["project_id"]) == project_id
    assert requests[0]["delegation_id"] is not None
    assert "człowiek" in requests[0]["consent_source"]
    assert decisions == 1, "zgoda człowieka jest zapisana, a nie domyślna"


async def test_approving_without_a_delegation_is_refused_with_a_reason(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)

    response = await logged_in_client.post(_path(tenant, project_id, revision_id, "/approve"))

    assert response.status_code == 409, response.text
    assert "powierzenia" in response.json()["detail"]
    async with pool.acquire() as conn:
        requests = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1",
            tenant["organization"],
        )
    assert requests == 0


async def test_approving_twice_does_not_queue_twice(pool, tenant, logged_in_client) -> None:
    """Drugie kliknięcie nie ma dołożyć drugiej publikacji tego samego tekstu."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)
    await _sign(logged_in_client, tenant, project_id, account_id)

    first = await logged_in_client.post(_path(tenant, project_id, revision_id, "/approve"))
    second = await logged_in_client.post(_path(tenant, project_id, revision_id, "/approve"))

    assert first.status_code == 202, first.text
    assert second.status_code == 409, second.text
    async with pool.acquire() as conn:
        requests = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1",
            tenant["organization"],
        )
    assert requests == 1


async def test_a_revision_makes_a_new_one_and_keeps_the_old(pool, tenant, logged_in_client) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id, text="pierwsza wersja agenta")

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"),
        json={"text": "wersja człowieka, krótsza i konkretniejsza"},
    )

    assert response.status_code == 201, response.text
    card = response.json()["karta"]
    assert card["stan"] == "szkic"
    assert card["tresc"] == "wersja człowieka, krótsza i konkretniejsza"
    assert card["rewizja"] != revision_id, "poprawka to NOWA rewizja"
    # TEN PROJEKT NIE MA POWIERZENIA, więc nie ma dokąd wydać tej propozycji i `opublikuj` nie
    # powstaje. `popraw` ZOSTAJE, bo poprawianie szkicu powierzenia nie wymaga: pierwsza wersja
    # tej poprawki zdejmowała oba przyciski i człowiek nie mógł poprawić szkicu drugi raz
    # (zmierzone przez wenext-ce na e2e, 21.09). Osobny test niżej sprawdza, że przy podpisanym
    # powierzeniu przycisk publikacji JEST.
    assert [one["nazwa"] for one in card["akcje"]] == ["popraw"], card["akcje"]
    assert card["akcje"][0]["payload"]["text"] == "wersja człowieka, krótsza i konkretniejsza", (
        "akcja poprawki niesie OBECNĄ treść, żeby ekran miał czym wypełnić pole edycji"
    )
    assert card["delegacja_pozwala"] is False
    assert "powierzenia" in card["dlaczego_bez_akcji"]
    assert card["tytul"] != card["spolecznosc"], (
        "w tytule stał identyfikator celu, ten sam, co w podpisie: na ekranie dwa razy to samo"
    )

    async with pool.acquire() as conn:
        states = {
            str(r["id"]): str(r["state"])
            for r in await conn.fetch(
                "SELECT id, state FROM action_proposal_revisions WHERE organization_id = $1",
                tenant["organization"],
            )
        }
    # ODRZUCONA, nie skasowana i nie nadpisana. Przed zgodą nie ma strzałki do SUPERSEDED
    # (L2775: jedyne wejście to APPROVED), więc poprawka jest tym, czym jest naprawdę: człowiek
    # odrzucił wersję agenta i napisał własną. Wersja agenta zostaje, bo to ona jest materiałem
    # do oceny jego pracy.
    assert states[revision_id] == "REJECTED", "poprzednia zostaje w bazie, nie znika"
    assert states[card["rewizja"]] == "READY_FOR_APPROVAL"


async def test_a_corrected_draft_keeps_the_button_when_the_project_has_a_delegation(
    pool, tenant, logged_in_client
) -> None:
    """Druga połowa tej samej reguły: karta mówi to, co ZROBI kliknięcie.

    Bez powierzenia nie ma przycisku, z powierzeniem jest, i w obu przypadkach odpowiedź karty
    zgadza się z tym, co odpowie trasa zgody. Jedno bez drugiego dałoby się spełnić źle:
    zdjęcie przycisku na zawsze też „naprawia" niedziałający przycisk.
    """
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id, text="pierwsza wersja agenta")
    await _sign(logged_in_client, tenant, project_id, account_id)

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"),
        json={"text": "wersja człowieka, krótsza i konkretniejsza"},
    )

    assert response.status_code == 201, response.text
    card = response.json()["karta"]
    assert [one["nazwa"] for one in card["akcje"]] == ["popraw", "opublikuj"]
    assert card["delegacja_pozwala"] is True
    assert card["dlaczego_bez_akcji"] is None


async def test_correcting_the_same_revision_twice_is_a_refusal_not_a_server_error(
    pool, tenant, logged_in_client
) -> None:
    """Drugie kliknięcie na karcie, której ekran jeszcze nie odświeżył (zgłoszenie wenext-ce).

    Pierwsza poprawka przestawia rewizję w REJECTED. Druga prosi o to samo przejście, maszyna
    stanów odmawia strzałki, której nie ma, a `IllegalTransition` nie dziedziczy po błędzie tego
    modułu, więc leciał wyżej jako 500 z pełnym śladem wyjątku w logu. Nic się przy tym nie
    psuło i nic nie ginęło, ale 500 znaczy „serwer się wywrócił", a wywrócił się człowiek
    w kliknięcie. Odtworzenie po stronie ekranu:
    `clicking_the_stale_correction_again_makes_no_second_proposal`.
    """
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id, text="pierwsza wersja agenta")

    first = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"), json={"text": "wersja człowieka"}
    )
    second = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"), json={"text": "jeszcze inna wersja"}
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert "nie jest już aktualna" in second.json()["detail"]
    async with pool.acquire() as conn:
        revisions = await conn.fetchval(
            "SELECT count(*) FROM action_proposal_revisions WHERE organization_id = $1",
            tenant["organization"],
        )
    assert revisions == 2, "druga poprawka dołożyła trzecią rewizję mimo odmowy"


async def test_a_correction_keeps_the_screenshot_the_agent_attached(
    pool, tenant, logged_in_client
) -> None:
    """27.09: the card shows the text without the image line, so a correction arrives without it.
    The image stays attached to the new revision, the card names it apart, and the correction
    field starts with the text alone. Retyping the same text is still "nothing changed"."""
    from control_plane.pilot.publication import MediaRef, join_media, split_media

    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    media = MediaRef(uuid4(), "sha256:" + "ef" * 32)
    revision_id = await _proposal(
        pool, tenant, account_id, text=join_media("wersja agenta ze zrzutem", media)
    )

    same = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"),
        json={"text": "wersja agenta ze zrzutem"},
    )
    assert same.status_code == 409, same.text

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"),
        json={"text": "wersja człowieka, z tym samym zrzutem"},
    )
    assert response.status_code == 201, response.text
    card = response.json()["karta"]
    assert card["tresc"] == "wersja człowieka, z tym samym zrzutem"
    assert card["obraz"] == str(media.artifact_id)
    assert card["akcje"][0]["payload"]["text"] == "wersja człowieka, z tym samym zrzutem"
    async with pool.acquire() as conn:
        field = await conn.fetchval(
            "SELECT payload ->> 'text_and_media' FROM action_proposal_revisions WHERE id = $1",
            UUID(card["rewizja"]),
        )
    assert split_media(field) == ("wersja człowieka, z tym samym zrzutem", media)


async def test_a_revision_that_changes_nothing_is_refused(pool, tenant, logged_in_client) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id, text="ten sam tekst")

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"), json={"text": "ten sam tekst"}
    )

    assert response.status_code == 409, response.text
    assert "nie zmienia treści" in response.json()["detail"]


async def test_a_proposal_of_another_project_does_not_exist_here(
    pool, tenant, logged_in_client
) -> None:
    mine = await _project(pool, tenant)
    theirs = await _project(pool, tenant)
    account_id = await _account(pool, tenant, theirs)
    revision_id = await _proposal(pool, tenant, account_id)

    response = await logged_in_client.post(_path(tenant, mine, revision_id, "/approve"))

    assert response.status_code == 404, response.text


async def test_a_correction_appears_in_the_conversation_and_replaces_the_old_card(
    pool, tenant, logged_in_client
) -> None:
    """ZGŁOSZENIE wenext-ce z dowodem z bazy (20.09): po poprawce baza miała dwie rewizje, a
    ekran pokazywał STARĄ z adnotacją „istnieje nowsza", której nie dało się zobaczyć.

    Powód: karty są projekcją ŚLADU, a trasa poprawki nie dopisywała do niego nic. Dla człowieka
    wyglądało to jak poprawka, która przepadła, czyli gorzej niż brak przycisku.
    """
    from control_plane.sessions.projection import cards_for
    from control_plane.sessions.store import SessionStore
    from control_plane.tools.cards import DRAFT_NEW, draft_card

    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="przygotuj odpowiedź",
        budget={},
    )
    revision_id = await _proposal(
        pool, tenant, account_id, text="pierwsza wersja agenta", session_id=session.id
    )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind="TOOL_RESULT",
        payload={
            "tool": "draft.create",
            "status": "ok",
            "karty": [
                draft_card(
                    revision=revision_id,
                    title="Propozycja",
                    community="r/devops",
                    profile="Anna",
                    text="pierwsza wersja agenta",
                    state=DRAFT_NEW,
                    delegation_allows=False,
                    actions=[],
                )
            ],
        },
    )

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"),
        json={"text": "wersja człowieka, krótsza i konkretniejsza"},
    )
    assert response.status_code == 201, response.text
    new_revision = response.json()["karta"]["rewizja"]

    events = list(await store.events(organization_id=tenant["organization"], session_id=session.id))
    corrections = [e for e in events if (e.payload or {}).get("powod") == "POPRAWKA_CZLOWIEKA"]
    assert len(corrections) == 1, "poprawka człowieka stoi w śladzie rozmowy"

    cards = await cards_for(
        pool,
        events,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=session.project_id,
    )
    drafts = [one for one in cards if one["rodzaj"] == "SZKIC"]
    assert len(drafts) == 1, f"jedna rzecz, jedna karta: {drafts}"
    assert drafts[0]["rewizja"] == new_revision
    assert drafts[0]["tresc"] == "wersja człowieka, krótsza i konkretniejsza"
    assert not any(key.startswith("_") for key in drafts[0]), "pola techniczne nie jadą na ekran"


async def test_a_replaced_draft_stays_visible_while_it_is_the_only_one(
    pool, tenant, logged_in_client
) -> None:
    """Zastąpiona bez następczyni ZOSTAJE: jest wtedy jedynym śladem tego, co napisał agent."""
    from control_plane.approval.store import ApprovalStore
    from control_plane.sessions.projection import cards_for
    from control_plane.sessions.store import SessionStore
    from control_plane.tools.cards import DRAFT_NEW, draft_card

    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="przygotuj odpowiedź",
        budget={},
    )
    revision_id = await _proposal(pool, tenant, account_id, session_id=session.id)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind="TOOL_RESULT",
        payload={
            "tool": "draft.create",
            "status": "ok",
            "karty": [
                draft_card(
                    revision=revision_id,
                    title="Propozycja",
                    community="r/devops",
                    profile="Anna",
                    text="odpowiedź agenta",
                    state=DRAFT_NEW,
                    delegation_allows=False,
                    actions=[],
                )
            ],
        },
    )
    await ApprovalStore(pool).move_revision(
        UUID(revision_id),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        target=ActionProposalState.REJECTED,
        actor_ref="operator:test",
    )

    cards = await cards_for(
        pool,
        list(await store.events(organization_id=tenant["organization"], session_id=session.id)),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=session.project_id,
    )

    drafts = [one for one in cards if one["rodzaj"] == "SZKIC"]
    assert len(drafts) == 1
    assert drafts[0]["akcje"] == []
    assert "zastąpiona" in drafts[0]["dlaczego_bez_akcji"]


async def _observed_thread(pool, tenant, *, external_id: str, key: str) -> None:
    """Społeczność i wątek, którego dotyczy propozycja: tak wygląda prawdziwy cel odpowiedzi.

    Agent proponuje odpowiedź pod wątkiem, który WCZEŚNIEJ obserwowaliśmy, więc nazwa społeczności
    jest w bazie. Bez tych dwóch wierszy trasa nie ma skąd jej wziąć i to też jest osobny test.
    """
    async with pool.acquire() as conn:
        community_id = await conn.fetchval(
            """
            INSERT INTO communities
                (organization_id, workspace_id, platform, community_key, display_name,
                 access_mode, observe_enabled, poll_interval_seconds)
            VALUES ($1, $2, 'REDDIT', $3, $4, 'PUBLIC_API_NO_ACCOUNT', true, 600)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            key,
            f"Reddit · {key}",
        )
        await conn.execute(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title,
                 created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, $4, $5, 'Wątek testowy', now(), now(), now(), $6)
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            external_id,
            f"https://www.reddit.com/r/devops/comments/{external_id}/x/",
            "0" * 64,
        )


async def test_the_corrected_card_is_signed_with_the_community_not_the_target_id(
    pool, tenant, logged_in_client
) -> None:
    """USTERKA ZE ZRZUTU RUNDY 2, i to jest jej test.

    Po poprawce w podpisie karty stał `t3_zgoda01`, czyli identyfikator wątku zamiast nazwy
    społeczności. Kontrakt kart mówi „społeczność · profil", a nie „cel · profil", i człowiek
    czyta ten podpis, żeby wiedzieć, GDZIE to pójdzie.
    """
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)
    await _observed_thread(pool, tenant, external_id="t3_zgoda01", key="r/devops")

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"), json={"text": "wersja człowieka"}
    )

    assert response.status_code == 201, response.text
    card = response.json()["karta"]
    assert card["spolecznosc"] == "r/devops", "tak samo, jak nazwał ją agent w karcie wcześniej"
    assert "t3_" not in str(card["spolecznosc"])


async def test_the_approved_card_is_signed_the_same_way(pool, tenant, logged_in_client) -> None:
    """Ta sama rodzina pomyłki stała w DRUGIM miejscu tej samej trasy, więc ma i drugi test."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)
    await _observed_thread(pool, tenant, external_id="t3_zgoda01", key="r/devops")
    assert (await _sign(logged_in_client, tenant, project_id, account_id)).status_code == 201

    response = await logged_in_client.post(_path(tenant, project_id, revision_id, "/approve"))

    assert response.status_code == 202, response.text
    assert response.json()["karta"]["spolecznosc"] == "r/devops"


async def test_without_a_known_thread_the_card_has_no_community_rather_than_an_identifier(
    pool, tenant, logged_in_client
) -> None:
    """Brak danych to nie identyfikator. Pusty podpis mówi „nie wiem", `t3_...` mówi nieprawdę."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    revision_id = await _proposal(pool, tenant, account_id)

    response = await logged_in_client.post(
        _path(tenant, project_id, revision_id, "/revise"), json={"text": "wersja człowieka"}
    )

    assert response.status_code == 201, response.text
    assert response.json()["karta"]["spolecznosc"] is None
