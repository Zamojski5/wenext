"""Karty i postęp w odczycie sesji: projekcja, nie stan przeglądarki (D-046).

TRZY PYTANIA, NA KTÓRE TE TESTY ODPOWIADAJĄ:

  1. Czy karta, która powstała w śladzie, jest w odczycie sesji także wtedy, gdy ekran prosi
     tylko o najnowsze zdarzenia? (Bo tak działa lista rozmowy: `after_seq`.)
  2. Czy stan karty jest DZISIEJSZY? Konto zalogowane w innej zakładce ma zmienić kartę bez
     żadnego nowego zdarzenia w rozmowie.
  3. Czy postęp liczy etapy, które NAPRAWDĘ się zaczęły, zamiast planu, którego nikt nie ma?
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.sessions.store import EVENT_TOOL_CALL, EVENT_TOOL_RESULT, SessionStore
from control_plane.tools.cards import (
    ACCOUNT_CONNECTED,
    ACCOUNT_DISCONNECTED,
    COMMUNITY_OBSERVED,
    COMMUNITY_OBSERVING,
    COMMUNITY_PROPOSED,
    account_card,
    community_card,
    fingerprint,
)

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


async def _session(pool, tenant, project_id):
    return await SessionStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="sprawdź, gdzie warto się odezwać",
        budget={},
    )


async def test_research_card_reflects_signature_and_pause_without_another_model_turn(
    pool, tenant, logged_in_client
):
    from control_plane.tools.cards import action, delegation_card

    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    route = f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/delegation"
    body = {"mode": "RESEARCH", "accounts": [], "communities": [], "actions": ["RESEARCH"],
            "budget": {"model_turns_per_day": 4, "publications_per_day": 0}}
    proposal = delegation_card(
        communities=[], allowed_actions=["RESEARCH"], limits={}, state="do_podpisu",
        actions=[action("powierz", "POST", route, body)],
    )
    await _tool_result(pool, tenant, session.id, tool="delegation.propose", cards=[proposal],
                       summary="Research")
    assert (await logged_in_client.post(route, json=body)).status_code == 201
    current = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()["karty"][0]
    assert current["stan"] == "podpisana"
    assert current["akcje"] == []
    assert current["dzialania"] == ["RESEARCH"]
    assert current["limity"]["publikacje_doba"] == 0
    assert (await logged_in_client.post(route + "/pause")).status_code == 200
    paused = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()["karty"][0]
    assert paused["stan"] == "blad"
    assert paused["akcje"] == []
    assert "wstrzymana" in paused["powod"]


async def _tool_result(pool, tenant, session_id, *, tool: str, cards: list[dict], summary: str):
    store = SessionStore(pool)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session_id,
        kind=EVENT_TOOL_CALL,
        payload={"tool": tool, "arguments": {"community": "r/SaaS"}, "summary": summary},
    )
    return await store.append_event(
        organization_id=tenant["organization"],
        session_id=session_id,
        kind=EVENT_TOOL_RESULT,
        payload={"tool": tool, "status": "ok", "summary": summary, "karty": cards},
    )


def _path(tenant, project_id, session_id, suffix: str = "") -> str:
    return (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/sessions/{session_id}{suffix}"
    )


async def test_a_card_from_the_trace_is_in_the_read_even_when_the_screen_asks_for_the_tail(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', 'r/SaaS', 'Reddit r/SaaS', 'ACCOUNT_REQUIRED', 600)",
            tenant["organization"],
            tenant["workspace"],
        )
    card = community_card(
        key="r/SaaS",
        platform="REDDIT",
        name="Reddit r/SaaS",
        rationale="rozmowy o pierwszych klientach",
        state=COMMUNITY_OBSERVING,
        observing=True,
    )
    event = await _tool_result(
        pool,
        tenant,
        session.id,
        tool="communities.observe",
        cards=[card],
        summary="obserwuję r/SaaS",
    )

    response = await logged_in_client.get(
        _path(tenant, project_id, session.id), params={"after_seq": event.seq}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["events"] == [], "prosiliśmy o ogon listy i nic nowego nie przyszło"
    assert len(body["karty"]) == 1, body["karty"]
    assert body["karty"][0]["klucz"] == "r/SaaS"
    assert body["karty"][0]["uzasadnienie"] == "rozmowy o pierwszych klientach"


async def test_the_state_of_a_card_comes_from_the_product_not_from_the_moment_it_was_made(
    pool, tenant, logged_in_client
) -> None:
    """Konto zalogowane gdzie indziej zmienia kartę bez żadnego nowego zdarzenia w rozmowie.
    Gdyby karta niosła stan z chwili wywołania, „brak połączenia” zostałoby na zawsze."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    login = f"konto-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'PROVISIONED')",
            tenant["organization"],
            login,
        )
    await _tool_result(
        pool,
        tenant,
        session.id,
        tool="accounts.connect",
        cards=[
            account_card(
                platform="REDDIT",
                login=login,
                profile="Anna",
                state=ACCOUNT_DISCONNECTED,
                actions=[],
            )
        ],
        summary="proszę o podłączenie konta",
    )

    before = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    assert before["karty"][0]["stan"] == ACCOUNT_DISCONNECTED

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE social_accounts SET state = 'READY' WHERE organization_id = $1 "
            "AND external_account_id = $2",
            tenant["organization"],
            login,
        )

    after = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    assert after["karty"][0]["stan"] == ACCOUNT_CONNECTED
    assert after["karty"][0]["akcje"] == [], "nie ma czego łączyć, więc nie ma przycisku"
    assert fingerprint(before["karty"]) != fingerprint(after["karty"]), (
        "odcisk musi się zmienić, bo inaczej ekran nigdy się nie przeładuje"
    )


async def test_a_community_nobody_observes_goes_back_to_a_proposal_with_a_button(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    await _tool_result(
        pool,
        tenant,
        session.id,
        tool="communities.observe",
        cards=[
            community_card(
                key="r/nieobserwowana",
                platform="REDDIT",
                name=None,
                rationale="warto tam zajrzeć",
                state=COMMUNITY_OBSERVING,
                observing=True,
            )
        ],
        summary="obserwuję",
    )

    body = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()

    card = body["karty"][0]
    assert card["stan"] == COMMUNITY_PROPOSED, "produkt nie zna tej społeczności"
    assert card["obserwacja"] is False
    assert card["akcje"][0]["nazwa"] == "obserwuj"
    assert card["akcje"][0]["payload"] == {
        "platform": "REDDIT",
        "community_key": "r/nieobserwowana",
    }, "ekran nie buduje ciała żądania sam"


async def test_a_community_moves_from_observing_to_observed_when_something_was_read(
    pool, tenant, logged_in_client
) -> None:
    """`obserwuje` i `obserwowana` to dwa zdania, nie jedno (E3).

    Człowiek, który właśnie kazał obserwować, ma prawo wiedzieć, czy z tej społeczności COŚ już
    przyszło. Różnicę rozstrzyga `last_polled_at`, czyli fakt z bazy, a nie to, że wywołanie
    narzędzia się udało: kolejka odczytu może stać, a karta nie ma prawa tego zamaskować.
    """
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', 'r/obserwowana', 'Reddit r/obserwowana', "
            "'PUBLIC_API_NO_ACCOUNT', 600)",
            tenant["organization"],
            tenant["workspace"],
        )
    await _tool_result(
        pool,
        tenant,
        session.id,
        tool="communities.observe",
        cards=[
            community_card(
                key="r/obserwowana",
                platform="REDDIT",
                name="Reddit r/obserwowana",
                rationale="tam piszą o wdrożeniach",
                state=COMMUNITY_OBSERVING,
                observing=True,
            )
        ],
        summary="obserwuję",
    )

    before = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    assert before["karty"][0]["stan"] == COMMUNITY_OBSERVING
    assert before["karty"][0]["obserwacja"] is True

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE communities SET last_polled_at = now() WHERE organization_id = $1 "
            "AND community_key = $2",
            tenant["organization"],
            "r/obserwowana",
        )

    after = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    card = after["karty"][0]
    assert card["stan"] == COMMUNITY_OBSERVED
    assert card["obserwacja"] is True
    assert "nie zapisuję" in card["co_to_znaczy"].casefold(), (
        "obserwowana społeczność mówi, czego obserwacja nie robi; bez tego zdania „obserwowana” "
        "znaczy dla człowieka tyle samo, co „dołączono”"
    )
    assert card["akcje"] == [], "nie ma przycisku „Dołącz”, bo nie ma trasy, która by to zrobiła"
    assert fingerprint(before["karty"]) != fingerprint(after["karty"])


async def test_progress_counts_the_stages_that_started(pool, tenant, logged_in_client) -> None:
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    store = SessionStore(pool)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_TOOL_CALL,
        payload={"tool": "project.read", "arguments": {}, "summary": "czytam projekt"},
    )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_TOOL_RESULT,
        payload={"tool": "project.read", "status": "ok", "summary": "cel i tryb są w kontekście"},
    )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_TOOL_CALL,
        payload={
            "tool": "threads.search",
            "arguments": {"community": "r/SaaS"},
            "summary": "szukam rozmów",
        },
    )

    body = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()

    progress = body["postep"]
    assert progress is not None, body
    assert progress["naglowek"] == "czytam rozmowy", "nagłówek to etap, który TRWA"
    assert progress["licznik"] == {"zrobione": 1, "wszystkie": 2}
    stages = {one["etykieta"]: one for one in progress["pozycje"]}
    assert stages["poznaję projekt"]["stan"] == "zrobione"
    assert stages["poznaję projekt"]["podlinia"] == "cel i tryb są w kontekście"
    assert stages["czytam rozmowy"]["stan"] == "trwa"
    assert stages["czytam rozmowy"]["zrodla"] == ["r/SaaS"], "chipy źródeł z argumentów"
    assert progress["linia_zdarzenia"] == "szukam rozmów"


async def test_a_turn_that_called_nothing_we_can_name_has_no_progress(
    pool, tenant, logged_in_client
) -> None:
    """Brak danych to nie zero: pusty postęp jest `null`, a nie licznikiem „0 z 0”."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)

    body = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()

    assert body["postep"] is None
    assert body["karty"] == []


async def test_a_draft_follows_the_publication_request_without_a_new_event(
    pool, tenant, logged_in_client
) -> None:
    """Karta SZKIC idzie za RZECZYWISTOŚCIĄ tak samo jak karta konta.

    Do 20.09 projekcja odświeżała tylko KONTO i SPOŁECZNOŚĆ, więc szkic zostawał na „publikuje”
    także wtedy, gdy publikator dawno skończył, i na ekranie wyglądało to jak zawieszenie. Tu
    zmienia się WYŁĄCZNIE wiersz w `publication_requests`; w rozmowie nie powstaje ani jedno nowe
    zdarzenie, a karta i odcisk mają to zobaczyć.
    """
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from community_os_contracts.enums import SemanticCapability
    from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
    from control_plane.approval.store import ApprovalStore
    from control_plane.pilot.publication import PublicationStore, draft_sha256
    from control_plane.tools.cards import DRAFT_PUBLISHING, STATE_ERROR, draft_card

    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    login = f"konto-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'READY') RETURNING id",
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
    signed = await logged_in_client.post(
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/delegation",
        json={
            "mode": "REVIEW",
            "accounts": [str(account_id)],
            "communities": ["r/devops"],
            "actions": ["REPLY"],
            "budget": {"publications_per_day": 6},
        },
    )
    assert signed.status_code == 201, signed.text
    delegation_id = signed.json()["delegation"]["delegation_id"]

    approvals = ApprovalStore(pool)
    proposal_id = await approvals.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=UUID(str(account_id)),
    )
    revision = await approvals.record_revision(
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
            target_external_id="t3_pub777",
            target_url="https://www.reddit.com/r/devops/comments/pub777/x/",
            source_fingerprint=f"session:{session.id}",
            text_and_media="odpowiedź agenta",
            intelligence_profile_version=uuid4(),
            plan_version=uuid4(),
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    decision, approved = await approvals.record_decision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision="APPROVED",
        operator_id=tenant["operator"],
        reason="zgoda człowieka w teście",
    )
    publication = PublicationStore(pool)
    request = await publication.create_project_request(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=UUID(str(project_id)),
        delegation_id=UUID(delegation_id),
        proposal_revision_id=approved.id,
        approval_decision_id=decision.id,
        position=await publication.next_position(organization_id=tenant["organization"]),
        consent_source="zgoda człowieka w teście",
        draft_sha256=draft_sha256("odpowiedź agenta"),
        target_external_id="t3_pub777",
        target_url="https://www.reddit.com/r/devops/comments/pub777/x/",
    )
    await _tool_result(
        pool,
        tenant,
        session.id,
        tool="drafts.publish",
        cards=[
            draft_card(
                revision=str(revision.id),
                title="Propozycja",
                community="r/devops",
                profile=login,
                text="odpowiedź agenta",
                state=DRAFT_PUBLISHING,
                delegation_allows=True,
                actions=[],
            )
        ],
        summary="wydaję do publikatora",
    )

    queued = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    assert queued["karty"][0]["stan"] == DRAFT_PUBLISHING, queued["karty"]

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status = 'BLOCKED', detail = $2 WHERE id = $1",
            request.id,
            "platforma odrzuciła komentarz: konto za młode",
        )

    after = (await logged_in_client.get(_path(tenant, project_id, session.id))).json()
    card = after["karty"][0]
    assert card["stan"] == STATE_ERROR, card
    assert card["powod"] == "platforma odrzuciła komentarz: konto za młode"
    assert card["akcje"] == []
    assert fingerprint(queued["karty"]) != fingerprint(after["karty"]), (
        "bez zmiany odcisku ekran nie przeładowałby się po zmianie losu publikacji"
    )
