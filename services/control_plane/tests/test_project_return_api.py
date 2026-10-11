"""Widok powrotu i korekta jednym zdaniem, przez HTTP, przeciwko PostgreSQL (P4 i P5).

NA JAKIE PYTANIE ODPOWIADA TEN EKRAN: „co się wydarzyło, odkąd mnie nie było". Każdy przypadek
tutaj to jeden sposób, w jaki odpowiedź mogłaby skłamać:

  * okno liczone od doby zamiast od poprzedniej wizyty, więc po dwóch dniach człowiek widzi jedną
    trzecią tego, co się stało;
  * okno przesuwane przy każdym odświeżeniu, więc znika w trakcie czytania;
  * cisza pokazana jako zero, bez rozróżnienia „kolektor pracował i nie było o czym pisać" od
    „kolektor nie obudził agenta ani razu";
  * publikacja bez obserwacji pokazana jako publikacja, która zniknęła;
  * korekta zapisana jako reguła na przyszłość, choć człowiek powiedział tylko „dobra robota";
  * korekta, która nie dotarła do żadnej sesji, a na ekranie wygląda na wysłaną.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.db

WORKSPACES = "/api/workspaces"


def _return_path(workspace_id: str, project_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/projects/{project_id}/return"


async def _project(client, workspace_id: str, name: str | None = None) -> str:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/projects",
        json={"name": name or f"projekt-{uuid.uuid4().hex[:8]}", "goal": "obserwuj i odpowiadaj"},
    )
    assert response.status_code == 201, response.content[:300]
    return response.json()["project"]["project_id"]


async def _wakeup(
    pool, tenant, project_id, *, reason: str = "NEW_EVENT", skipped: str | None = None, when=None
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO wakeups
                (organization_id, workspace_id, project_id, reason, skipped_reason, created_at)
            VALUES ($1, $2, $3, $4, $5, coalesce($6, now()))
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            reason,
            skipped,
            when,
        )


async def _session(pool, tenant, project_id, *, status="DONE", summary="zrobione", tools=()):
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            """
            INSERT INTO agent_sessions
                (organization_id, workspace_id, project_id, goal, status, result_summary,
                 finished_at)
            VALUES ($1, $2, $3, 'cel sesji', $4, $5,
                    CASE WHEN $4 IN ('DONE', 'FAILED', 'BUDGET_EXHAUSTED') THEN now() END)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            status,
            summary,
        )
        for seq, tool in enumerate(tools, start=1):
            await conn.execute(
                """
                INSERT INTO session_events
                    (organization_id, workspace_id, session_id, seq, kind, payload)
                VALUES ($1, $2, $3, $4, 'TOOL_CALL', $5::jsonb)
                """,
                tenant["organization"],
                tenant["workspace"],
                session_id,
                seq,
                json.dumps({"tool": tool}),
            )
    return session_id


async def _publication(pool, tenant, project_id, *, observed: bool) -> uuid.UUID:
    """Wniosek publikacyjny projektu, z całym łańcuchem, którego wymaga 0007 i 0017."""
    digest = hashlib.sha256(b"powrot-e2e").hexdigest()
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"konto-{uuid.uuid4().hex[:8]}",
        )
        proposal_id = await conn.fetchval(
            "INSERT INTO action_proposals (organization_id, workspace_id, social_account_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            account_id,
        )
        revision_id = await conn.fetchval(
            """
            INSERT INTO action_proposal_revisions
                (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
                 source_fingerprint, expires_at, state)
            VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'odcisk', now() + interval '1 day', 'APPROVED')
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            proposal_id,
            json.dumps({"text_and_media": "odpowiedź agenta", "target_url": "https://x.test/1"}),
            digest,
        )
        decision_id = await conn.fetchval(
            """
            INSERT INTO approval_decisions
                (organization_id, workspace_id, proposal_revision_id, decision,
                 approved_payload_hash, approved_by)
            VALUES ($1, $2, $3, 'APPROVED', $4, $5)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            revision_id,
            digest,
            tenant["operator"],
        )
        delegation_id = await conn.fetchval(
            """
            INSERT INTO project_delegations
                (organization_id, workspace_id, project_id, mode, scope, scope_sha256, signed_by)
            VALUES ($1, $2, $3, 'REVIEW', '{}'::jsonb, $4, $5)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            digest,
            tenant["operator"],
        )
        # PUBLISHED wymaga próby wykonania (CHECK z 0007): publikacja bez śladu wykonania
        # byłaby sukcesem, którego nikt nie wykonał. Łańcuch jest taki sam jak w produkcji.
        intent_id = await conn.fetchval(
            """
            INSERT INTO execution_intents
                (organization_id, workspace_id, proposal_revision_id, approval_decision_id,
                 one_time_grant_hash, expires_at, payload_hash, account_id, status, consumed_at)
            VALUES ($1, $2, $3, $4, $5, now() + interval '1 hour', $6, $7, 'CONSUMED', now())
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            revision_id,
            decision_id,
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            digest,
            account_id,
        )
        attempt_id = await conn.fetchval(
            """
            INSERT INTO execution_attempts
                (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
                 attempt_no, status, adapter_version, finished_at)
            VALUES ($1, $2, $3, $4, $5, 1, 'CONFIRMED', 'test-1', now())
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            intent_id,
            revision_id,
            digest,
        )
        request_id = await conn.fetchval(
            """
            INSERT INTO publication_requests
                (organization_id, workspace_id, project_id, delegation_id, proposal_revision_id,
                 approval_decision_id, intent_id, attempt_id, position, status, consent_source,
                 draft_sha256, target_external_id, target_url, published_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, 'PUBLISHED', 'delegacja', $9,
                    't3_abc123', 'https://reddit.test/r/x/comments/abc123/', now())
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            delegation_id,
            revision_id,
            decision_id,
            intent_id,
            attempt_id,
            digest,
        )
        if observed:
            await conn.execute(
                """
                INSERT INTO publication_observations
                    (organization_id, workspace_id, request_id, observed_at, still_present,
                     score, reply_count, viewer)
                VALUES ($1, $2, $3, now(), true, 7, 2, 'ANONYMOUS')
                """,
                tenant["organization"],
                tenant["workspace"],
                request_id,
            )
            await conn.execute(
                "UPDATE publication_requests SET public_visibility='CONFIRMED', "
                "public_visibility_checked_at=now() WHERE id=$1",
                request_id,
            )
    return request_id


# ---------------------------------------------------------------------------
# E6: wynik projektu to wynik TEGO projektu
# ---------------------------------------------------------------------------


async def test_project_results_carry_only_this_projects_publications(
    logged_in_client, pool, tenant
) -> None:
    """Audyt UX 20.09, punkt 6: projekt bez jednej rozmowy pokazywał 38 wysłanych publikacji.

    Strona wołała odczyt PRZESTRZENI, bo tylko taki był. Nie był to wyciek między
    organizacjami; było gorzej dla czytającego, bo cudza praca wyglądała jak własna i w dodatku
    jak praca agenta, który jeszcze nie ruszył. Kolumna `project_id` stoi w
    `publication_requests` od migracji 0017, więc filtr nie wymagał zmiany schematu, tylko
    odczytu, którego nikt nie napisał.
    """
    workspace_id = str(tenant["workspace"])
    mine = await _project(logged_in_client, workspace_id, "Mój projekt")
    other = await _project(logged_in_client, workspace_id, "Cudzy projekt")
    third = await _project(logged_in_client, workspace_id, "Trzeci projekt")
    # JEDNA PUBLIKACJA NA PROJEKT, bo `_publication` zakłada powierzenie, a 0017 pozwala na
    # jedno żywe na projekt (`project_delegations_one_live_per_project`). Trzeci projekt zamiast
    # drugiej publikacji w cudzym: liczba w przestrzeni ma być większa niż w moim projekcie.
    await _publication(pool, tenant, mine, observed=True)
    await _publication(pool, tenant, other, observed=False)
    await _publication(pool, tenant, third, observed=False)

    response = await logged_in_client.get(f"/api/workspaces/{workspace_id}/projects/{mine}/results")

    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["scope"] == {"kind": "PROJECT", "project_id": mine}
    assert [row["project_id"] for row in body["publications"]] == [mine], body["publications"]
    assert body["totals"]["published"] == 1, body["totals"]
    assert body["totals"]["confirmed_visible"] == 1, body["totals"]
    assert "communities" not in body, (
        "społeczność należy do przestrzeni, więc jej liczba nie ma prawa stać pod nazwą projektu"
    )

    whole = (await logged_in_client.get(f"/api/workspaces/{workspace_id}/results")).json()
    assert whole["totals"]["published"] == 3, "historia przestrzeni dalej liczy wszystko"
    assert {row["project_id"] for row in whole["publications"]} == {mine, other, third}, (
        "odczyt przestrzeni mówi teraz, czyja to praca; bez tego pola lista czytała się jak "
        "lista otwartego projektu"
    )


async def test_the_tool_the_model_calls_lists_only_this_projects_publications(
    logged_in_client, pool, tenant
) -> None:
    """Ta sama nieprawda co na ekranie, tylko w ustach modelu (E6).

    `publications.list` i `project.read` filtrowały WYŁĄCZNIE po organizacji, więc model
    pracujący nad jednym projektem dostawał wszystko, co organizacja kiedykolwiek wysłała, i nie
    miał jak tego rozróżnić: w odpowiedzi nie jechał ani projekt, ani przestrzeń. Cudza praca
    wchodziła do jego zdań jako dorobek tego projektu, a człowiek czytał to jako pracę agenta,
    który jeszcze nie ruszył.

    Test stoi w tym pliku, bo tu mieszka `_publication`, czyli jedyne miejsce, które składa
    prawdziwy łańcuch wniosku publikacyjnego: rewizję, decyzję, intencję, próbę i dowód.
    """
    from control_plane.tools import ToolRegistry, issue

    workspace_id = str(tenant["workspace"])
    mine = await _project(logged_in_client, workspace_id, "Mój projekt")
    other = await _project(logged_in_client, workspace_id, "Cudzy projekt")
    third = await _project(logged_in_client, workspace_id, "Trzeci projekt")
    await _publication(pool, tenant, mine, observed=True)
    await _publication(pool, tenant, other, observed=True)
    await _publication(pool, tenant, third, observed=False)
    session_id = await _session(pool, tenant, mine, status="RUNNING", summary=None)
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=uuid.UUID(mine),
            dsn="postgresql://nieuzywane-w-tescie",
        ),
    )

    listed = await registry.call("publications.list", {})
    read = await registry.call("project.read", {})

    assert listed["liczba"] == 1, listed["publikacje"]
    assert "Lista dotyczy TEGO projektu" in listed["uwaga"]
    # PUBLIKACJE SPRZED PODZIAŁU NA PROJEKTY liczą się osobno i tu jest ich zero, bo w tej
    # przestrzeni nie ma wierszy bez projektu. Przypadku NIEZEROWEGO ten test nie buduje
    # świadomie: 0017 wymaga, żeby wniosek bez projektu miał bieg pilota i reakcję
    # (`publication_requests_come_from_a_run_or_a_project`), więc tani wiersz-atrapa jest
    # niemożliwy, a prawdziwy wymaga całego świata pilota, który ma własne testy. Zapisuję to
    # jako lukę, zamiast udawać pokrycie fałszywym wierszem.
    assert listed["spoza_projektu"] == 0
    assert "sprzed podziału" not in listed["uwaga"], (
        "zdanie o publikacjach pilota pojawiło się mimo zera; wtedy mówiłoby o czymś, czego nie ma"
    )
    assert sum(row["ile"] for row in read["publikacje"]) == 1, read["publikacje"]


async def test_the_effect_of_another_projects_publication_is_not_readable_here(
    logged_in_client, pool, tenant
) -> None:
    """`observations.read` bierze identyfikator z `publications.list`, więc granica ma być ta sama.

    Zapytanie filtrowało po samej organizacji: identyfikator cudzej publikacji wpisany do
    narzędzia oddawał skutek cudzej pracy. Model nie musiał tego robić złośliwie, wystarczyło,
    że przeczytał identyfikator w starszej rozmowie tej samej organizacji.
    """
    from control_plane.tools import ToolRegistry, issue

    workspace_id = str(tenant["workspace"])
    mine = await _project(logged_in_client, workspace_id, "Mój projekt")
    other = await _project(logged_in_client, workspace_id, "Cudzy projekt")
    theirs = await _publication(pool, tenant, other, observed=True)
    session_id = await _session(pool, tenant, mine, status="RUNNING", summary=None)
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=uuid.UUID(mine),
            dsn="postgresql://nieuzywane-w-tescie",
        ),
    )

    result = await registry.call("observations.read", {"request_id": str(theirs)})

    assert result["stan"] == "NIEDOSTEPNE", result
    assert "w tym projekcie" in result["powod"], result["powod"]
    assert "publikacja" not in result, "odmowa oddała jednak wiersz cudzej publikacji"


async def test_a_project_that_published_nothing_answers_empty_instead_of_failing(
    logged_in_client, pool, tenant
) -> None:
    """Pusto to normalna odpowiedź i znaczy „ten projekt jeszcze nic nie wysłał".

    Zepsuty odczyt musi wyglądać INACZEJ niż brak wyników, bo ekran mówi wtedy co innego.
    """
    workspace_id = str(tenant["workspace"])
    empty = await _project(logged_in_client, workspace_id, "Nowy projekt")
    busy = await _project(logged_in_client, workspace_id, "Pracujący projekt")
    await _publication(pool, tenant, busy, observed=True)

    response = await logged_in_client.get(
        f"/api/workspaces/{workspace_id}/projects/{empty}/results"
    )

    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["publications"] == []
    assert body["totals"] == {"published": 0, "confirmed_visible": 0, "waiting": 0}


async def test_results_of_a_project_from_another_workspace_are_not_served(
    logged_in_client, pool, tenant
) -> None:
    """Identyfikator projektu wpisany pod cudzą przestrzeń daje 404, a nie cudze liczby."""
    workspace_id = str(tenant["workspace"])
    mine = await _project(logged_in_client, workspace_id, "Mój projekt")
    await _publication(pool, tenant, mine, observed=True)

    response = await logged_in_client.get(f"/api/workspaces/{uuid.uuid4()}/projects/{mine}/results")

    assert response.status_code == 404, response.content[:300]


async def _age_session(pool, project_id, hours: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET created_at = created_at - make_interval(mins => $2) "
            "WHERE project_id = $1",
            uuid.UUID(project_id),
            int(hours * 60),
        )


async def _age_visit(pool, project_id, hours: float) -> None:
    """Cofnij znacznik wizyty, żeby następne wejście zaczęło nowe okno bez czekania kwadransa."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_visits SET last_seen_at = last_seen_at - make_interval(mins => $2), "
            "previous_seen_at = previous_seen_at - make_interval(mins => $2) "
            "WHERE project_id = $1",
            uuid.UUID(project_id),
            int(hours * 60),
        )


# ---------------------------------------------------------------------------
# Okno
# ---------------------------------------------------------------------------


async def test_the_first_visit_says_it_is_the_first_and_shows_everything(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _session(pool, tenant, project_id, summary="pierwsza sesja")

    response = await logged_in_client.get(_return_path(workspace_id, project_id))

    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["okno"]["pierwsza_wizyta"] is True
    assert body["okno"]["od"] is None
    assert "pierwsza wizyta" in body["okno"]["zdanie"]
    assert [row["wynik"] for row in body["sesje"]] == ["pierwsza sesja"]


async def test_a_refresh_does_not_wipe_the_window_in_the_middle_of_reading(
    logged_in_client, pool, tenant
) -> None:
    """Karencja. Bez niej drugie wejście w tej samej minucie pokazuje „nic się nie wydarzyło"."""
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await logged_in_client.get(_return_path(workspace_id, project_id))
    await _session(pool, tenant, project_id, summary="coś w międzyczasie")

    second = await logged_in_client.get(_return_path(workspace_id, project_id))
    third = await logged_in_client.get(_return_path(workspace_id, project_id))

    assert [row["wynik"] for row in second.json()["sesje"]] == ["coś w międzyczasie"]
    assert [row["wynik"] for row in third.json()["sesje"]] == ["coś w międzyczasie"], (
        "odświeżenie skasowało okno, którego człowiek jeszcze nie doczytał"
    )


async def test_after_a_real_absence_the_window_moves_and_old_news_drops_out(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _session(pool, tenant, project_id, summary="stara wiadomość")
    await logged_in_client.get(_return_path(workspace_id, project_id))
    # Sesja jest starsza niż poprzednia wizyta, a wizyta starsza niż karencja: dopiero takie dwa
    # postarzenia opisują prawdziwy powrót po nieobecności.
    await _age_session(pool, project_id, hours=48)
    await _age_visit(pool, project_id, hours=26)

    after = await logged_in_client.get(_return_path(workspace_id, project_id))

    body = after.json()
    assert body["okno"]["pierwsza_wizyta"] is False
    assert body["okno"]["od"] is not None
    assert body["sesje"] == [], "sesja sprzed poprzedniej wizyty nie jest nowością"


# ---------------------------------------------------------------------------
# Cisza, obudzenia, sesje
# ---------------------------------------------------------------------------


async def test_a_quiet_period_says_how_many_times_the_collector_worked(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    # The moment comes from THIS clock, a few seconds back: the window ends at the API's clock,
    # and a row stamped by the database's `now()` inside a container running a few milliseconds
    # ahead fell outside it (27.09: 2 of 3 in one full run, 3 of 3 in the next).
    written = datetime.now(UTC) - timedelta(seconds=5)
    for _ in range(3):
        await _wakeup(pool, tenant, project_id, reason="DUE", skipped="BEZ_ZDARZEN", when=written)

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert body["cisza"] is True
    assert body["obudzenia"]["razem"] == 3
    assert body["obudzenia"]["pominiete"] == 3
    assert body["obudzenia"]["powody_pominiecia"] == {"BEZ_ZDARZEN": 3}
    assert any("kolektor pracował 3 razy" in note for note in body["uwagi"]), body["uwagi"]


async def test_no_wakeups_at_all_is_named_as_nobody_checked(logged_in_client, tenant) -> None:
    """Drugie milczenie, to gorsze. Zero bez tego zdania uspokaja, a nie powinno."""
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert body["obudzenia"]["razem"] == 0
    assert any("nie uruchomiono pracy agenta w tle" in note for note in body["uwagi"]), body[
        "uwagi"
    ]


async def test_a_session_comes_back_with_its_result_and_the_tools_it_used(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _session(
        pool,
        tenant,
        project_id,
        summary="przeczytałem wątek i nie odpowiadałem",
        tools=("sources.read", "memory.list"),
    )

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert len(body["sesje"]) == 1
    session = body["sesje"][0]
    assert session["wynik"] == "przeczytałem wątek i nie odpowiadałem"
    assert sorted(session["narzedzia"]) == ["memory.list", "sources.read"]
    assert session["session_id"], "bez identyfikatora nie da się przejść do źródła"


async def test_a_session_still_running_has_no_result_and_does_not_pretend_to(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _session(pool, tenant, project_id, status="RUNNING", summary=None)

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert body["sesje"][0]["wynik"] is None
    assert body["sesje"][0]["status"] == "RUNNING"


# ---------------------------------------------------------------------------
# Publikacje
# ---------------------------------------------------------------------------


async def test_a_publication_nobody_looked_at_says_so_instead_of_looking_removed(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _publication(pool, tenant, project_id, observed=False)

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert len(body["publikacje"]) == 1
    assert body["publikacje"][0]["widocznosc"] is None
    # 0023: rodzaj jedzie razem z publikacją, bo „nasz wątek" i „odpowiedź" to dwa różne
    # działania wobec społeczności i ekran ma je rozróżniać.
    assert body["publikacje"][0]["rodzaj"] == "REPLY"
    assert body["cisza"] is False


@pytest.mark.parametrize(
    "status,waiting",
    [("BLOCKED", 0), ("STOPPED", 0), ("UNKNOWN", 0), ("QUEUED", 1), ("PUBLISHING", 1)],
)
async def test_only_pending_execution_counts_as_waiting_for_publication(
    logged_in_client,
    pool,
    tenant,
    status,
    waiting,
):
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    request_id = await _publication(pool, tenant, project_id, observed=False)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status=$2, published_at=NULL WHERE id=$1",
            request_id,
            status,
        )
    from control_plane.workspace.store import WorkspaceStore

    result = await WorkspaceStore(pool).project_results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(project_id),
    )
    assert result["totals"]["waiting"] == waiting
    assert result["totals"]["published"] == 0
    assert result["publications"][0]["status"] == status


async def test_visible_count_uses_the_latest_observation_not_a_historical_success(
    logged_in_client,
    pool,
    tenant,
):
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    request_id = await _publication(pool, tenant, project_id, observed=True)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_observations SET observed_at=now()-interval '1 minute' "
            "WHERE request_id=$1",
            request_id,
        )
        await conn.execute(
            "INSERT INTO publication_observations (organization_id,workspace_id,request_id,"
            "observed_at,still_present,score,reply_count,viewer) "
            "VALUES ($1,$2,$3,now(),false,0,0,'ANONYMOUS')",
            tenant["organization"],
            tenant["workspace"],
            request_id,
        )
        await conn.execute(
            "UPDATE publication_requests SET public_visibility='REMOVED', "
            "public_visibility_checked_at=now() WHERE id=$1",
            request_id,
        )
    from control_plane.workspace.store import WorkspaceStore

    store = WorkspaceStore(pool)
    project = await store.project_results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(project_id),
    )
    workspace = await store.results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )
    assert project["totals"]["published"] == workspace["totals"]["published"] == 1
    assert project["totals"]["confirmed_visible"] == workspace["totals"]["confirmed_visible"] == 0


async def test_author_read_cannot_confirm_public_visibility(logged_in_client, pool, tenant):
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    request_id = await _publication(pool, tenant, project_id, observed=False)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO publication_observations (organization_id,workspace_id,request_id,"
            "observed_at,still_present,score,reply_count,viewer) "
            "VALUES ($1,$2,$3,now(),true,1,0,'AUTHOR')",
            tenant["organization"],
            tenant["workspace"],
            request_id,
        )

    from control_plane.workspace.store import WorkspaceStore

    store = WorkspaceStore(pool)
    project = await store.project_results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(project_id),
    )
    workspace = await store.results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )
    for result in (project, workspace):
        assert result["totals"]["published"] == 1
        assert result["totals"]["confirmed_visible"] == 0
        assert result["publications"][0]["still_present"] is None
        assert result["publications"][0]["observed_at"] is None


async def test_project_result_links_to_the_exact_published_comment(logged_in_client, pool, tenant):
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    request_id = await _publication(pool, tenant, project_id, observed=False)
    permalink = "https://www.reddit.com/r/x/comments/abc123/comment/reply123/"
    async with pool.acquire() as conn:
        attempt_id = await conn.fetchval(
            "SELECT attempt_id FROM publication_requests WHERE id=$1", request_id
        )
        await conn.execute(
            "INSERT INTO execution_evidence "
            "(organization_id,workspace_id,attempt_id,external_id,permalink,account,observed_at,source) "
            "VALUES ($1,$2,$3,'t1_reply123',$4,'test-account',now(),'ACTUATOR')",
            tenant["organization"],
            tenant["workspace"],
            attempt_id,
            permalink,
        )

    from control_plane.workspace.store import WorkspaceStore

    result = await WorkspaceStore(pool).project_results(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(project_id),
    )
    assert result["publications"][0]["permalink"] == permalink
    assert result["publications"][0]["target_url"] != permalink


async def test_a_publication_with_an_observation_carries_the_measurement(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _publication(pool, tenant, project_id, observed=True)

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    visibility = body["publikacje"][0]["widocznosc"]
    assert visibility["jest"] is True
    assert visibility["odpowiedzi"] == 2
    assert visibility["punkty"] == 7
    assert visibility["sprawdzono"]
    assert visibility["wyswietlenia"] is None, "nikt nie zmierzył wyświetleń: brak, nie zero"


async def test_the_views_the_author_saw_come_back_with_the_measurement(
    logged_in_client, pool, tenant
) -> None:
    # Measured 27.09 on X: the author's read of our post gave `{"views": 5}` and nothing else.
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    request_id = await _publication(pool, tenant, project_id, observed=False)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO publication_observations
                (organization_id, workspace_id, request_id, observed_at, still_present,
                 score, reply_count, viewer, raw)
            VALUES ($1, $2, $3, now(), true, NULL, 0, 'AUTHOR', $4::jsonb)
            """,
            tenant["organization"],
            tenant["workspace"],
            request_id,
            '{"platforma": "X", "metrics": {"views": 5}}',
        )

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    visibility = body["publikacje"][0]["widocznosc"]
    assert visibility["wyswietlenia"] == 5
    assert visibility["punkty"] is None


# ---------------------------------------------------------------------------
# Korekta jednym zdaniem
# ---------------------------------------------------------------------------


async def test_a_correction_reaches_the_live_session_as_the_persons_own_message(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    session_id = await _session(pool, tenant, project_id, status="RUNNING", summary=None)

    response = await logged_in_client.post(
        _return_path(workspace_id, project_id) + "/correction",
        json={"text": "Nie odpowiadaj w wątkach starszych niż dzień"},
    )

    assert response.status_code == 202, response.content[:300]
    body = response.json()
    assert body["session_id"] == str(session_id), "korekta założyła nową sesję zamiast dopisać"
    assert body["nowa_sesja"] is False

    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT kind, payload FROM session_events WHERE session_id = $1 ORDER BY seq DESC "
            "LIMIT 1",
            session_id,
        )
    payload = event["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert event["kind"] == "USER_MESSAGE"
    assert payload["context"] == "korekta po powrocie"


@pytest.mark.parametrize(
    "text",
    [
        "Nie odpowiadaj w wątkach starszych niż dzień",
        "Dobra robota, tak trzymaj",
        "Skróć to o połowę",
    ],
)
async def test_no_sentence_is_turned_into_a_rule_by_the_route_itself(
    logged_in_client, pool, tenant, text
) -> None:
    """E5 (audyt UX 20.09 punkt 5): trasa nie rozstrzyga, co jest wolą na przyszłość.

    Do 20.09 rozstrzygało PIERWSZE SŁOWO: „Nie odpowiadaj..." zapisywało się jako reguła,
    „Skróć to o połowę" nie, mimo że drugie zdanie jest regułą tak samo jak pierwsze, a „Dobra
    robota" nie jest nią wcale. Trzy zdania w jednym teście, bo cała rzecz polega na tym, że
    trasa traktuje je TAK SAMO; sens rozstrzyga agent, który je przeczyta, i zapisuje przez
    `memory.write` ze wskazaniem tej wypowiedzi.
    """
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await _session(pool, tenant, project_id, status="RUNNING", summary=None)

    response = await logged_in_client.post(
        _return_path(workspace_id, project_id) + "/correction", json={"text": text}
    )

    assert response.status_code == 202, response.content[:300]
    assert response.json()["zapamietane"] is None
    assert "sam rozstrzygnie" in response.json()["uwaga"]
    async with pool.acquire() as conn:
        items = await conn.fetchval(
            "SELECT count(*) FROM knowledge_items WHERE project_id = $1", uuid.UUID(project_id)
        )
    assert items == 0, "trasa zapisała ustalenie, choć nie ma czym zrozumieć zdania"


async def test_a_correction_with_no_live_session_starts_one(logged_in_client, pool, tenant) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)

    response = await logged_in_client.post(
        _return_path(workspace_id, project_id) + "/correction",
        json={"text": "Zamiast linków pisz własnymi słowami"},
    )

    assert response.status_code == 202, response.content[:300]
    assert response.json()["nowa_sesja"] is True
    async with pool.acquire() as conn:
        goal = await conn.fetchval(
            "SELECT goal FROM agent_sessions WHERE project_id = $1", uuid.UUID(project_id)
        )
    assert goal == "Zamiast linków pisz własnymi słowami"


async def test_the_correction_shows_up_in_the_next_return_view(
    logged_in_client, pool, tenant
) -> None:
    """Skutek widoczny po powrocie: korekta i to, czy jakakolwiek sesja mogła ją zobaczyć."""
    from control_plane.knowledge.store import KnowledgeStore, NewItem, Scope

    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await logged_in_client.post(
        _return_path(workspace_id, project_id) + "/correction",
        json={"text": "Mniej ogólników"},
    )
    # KOREKTA W PODSUMOWANIU POWROTU TO USTALENIE, nie sama wiadomość, i od E5 zapisuje je
    # AGENT, gdy zrozumie zdanie i wskaże wypowiedź człowieka. Tutaj wpisujemy je wprost, bo ten
    # test jest o WIDOKU: drogę zapisu sprawdza `test_documents_store.py` na prawdziwym
    # narzędziu, a powielanie jej tu mierzyłoby dwa razy to samo.
    await KnowledgeStore(pool).write(
        NewItem(
            kind="PREFERENCE",
            statement="Mniej ogólników",
            stated_by="USER",
            project_id=uuid.UUID(project_id),
        ),
        Scope(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=uuid.UUID(project_id),
        ),
    )

    before = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()
    assert [row["tresc"] for row in before["korekty"]] == ["Mniej ogólników"]
    assert before["korekty"][0]["w_zasiegu_sesji"] is False

    # Sesja, która ruszyła PO korekcie i zapytała o pamięć: dopiero to widać w bazie.
    await _session(pool, tenant, project_id, tools=("memory.list",))
    after = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()
    assert after["korekty"][0]["w_zasiegu_sesji"] is True


async def test_the_correction_leaves_an_audit_row(logged_in_client, pool, tenant) -> None:
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    await logged_in_client.post(
        _return_path(workspace_id, project_id) + "/correction",
        json={"text": "Bez emotikonów"},
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload FROM audit_events WHERE kind = 'PROJECT_RETURN_CORRECTION' "
            "AND subject_ref = $1",
            f"project:{project_id}",
        )
    assert len(rows) == 1, rows


# ---------------------------------------------------------------------------
# Granice
# ---------------------------------------------------------------------------


async def test_a_project_of_another_organization_is_a_404(logged_in_client, pool, tenant) -> None:
    workspace_id = str(tenant["workspace"])
    async with pool.acquire() as conn:
        other_organization = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca') RETURNING id"
        )
        other_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obca ws') RETURNING id",
            other_organization,
        )
        stranger = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) "
            "VALUES ($1, $2, 'cudzy') RETURNING id",
            other_organization,
            other_workspace,
        )

    read = await logged_in_client.get(_return_path(workspace_id, str(stranger)))
    write = await logged_in_client.post(
        _return_path(workspace_id, str(stranger)) + "/correction", json={"text": "Nie tak"}
    )

    assert read.status_code == 404, read.content[:200]
    assert write.status_code == 404, write.content[:200]


async def test_an_incident_is_shown_next_to_the_silence(logged_in_client, pool, tenant) -> None:
    """„Agent nic nie zrobił" i „agent nie mógł nic zrobić" to dwie różne odpowiedzi."""
    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO incidents (organization_id, component, started_at, reason, detected_by) "
            "VALUES ($1, 'DISPATCHER', $2, 'brak pulsu', 'MISSING_HEARTBEAT')",
            tenant["organization"],
            datetime.now(UTC) - timedelta(hours=2),
        )

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()

    assert len(body["incydenty"]) == 1
    assert body["incydenty"][0]["komponent"] == "DISPATCHER"
    assert body["incydenty"][0]["trwa"] is True


async def test_the_return_says_how_the_follower_count_moved_since_the_last_visit(
    logged_in_client, pool, tenant
) -> None:
    """27.09: the founder's number on X, on the screen opened first. At the first visit it is the
    latest reading; after an absence it is the reading at the window's start against the latest,
    with the change. An account nobody measured says nothing, never a zero."""
    from control_plane.workspace.audience import record_followers

    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    handle = f"konto_{uuid.uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'X', $2, 'READY') RETURNING id",
            tenant["organization"],
            handle,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            account_id,
        )
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=2,
            observed_at=datetime.now(UTC) - timedelta(hours=5),
        )

    first = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()
    assert f"Obserwujący @{handle} na X: 2 (ostatni odczyt z profilu konta)." in first["uwagi"]
    assert first["obserwujacy"][0]["zmiana"] is None

    await _age_visit(pool, project_id, hours=3)
    async with pool.acquire() as conn:
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=3,
            observed_at=datetime.now(UTC) - timedelta(hours=1),
        )

    later = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()
    assert f"Obserwujący @{handle} na X: 2 → 3 (+1) od Twojej ostatniej wizyty." in later["uwagi"]
    [row] = later["obserwujacy"]
    assert (row["bylo"], row["jest"], row["zmiana"], row["login"]) == (2, 3, 1, handle)


async def test_a_stale_follower_reading_warns_that_the_collection_stopped(
    logged_in_client, pool, tenant
) -> None:
    """29.09: the X capsule hung for a day and the only trace was the readings stopping. A
    reading older than three hours now says so on the return screen; a fresh one says nothing."""
    from control_plane.workspace.audience import record_followers

    workspace_id = str(tenant["workspace"])
    project_id = await _project(logged_in_client, workspace_id)
    handle = f"konto_{uuid.uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'X', $2, 'READY') RETURNING id",
            tenant["organization"],
            handle,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
            account_id,
        )
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=2,
            observed_at=datetime.now(UTC) - timedelta(hours=22),
        )

    body = (await logged_in_client.get(_return_path(workspace_id, project_id))).json()
    stale = [
        note for note in body["uwagi"] if note.startswith(f"Ostatni odczyt obserwujących @{handle}")
    ]
    assert len(stale) == 1 and "22 h" in stale[0] and "nie działa" in stale[0], body["uwagi"]

    async with pool.acquire() as conn:
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=2,
            observed_at=datetime.now(UTC) - timedelta(minutes=20),
        )
    fresh = (
        await logged_in_client.get(_return_path(workspace_id, project_id) + "?mark=false")
    ).json()
    assert not [note for note in fresh["uwagi"] if "Ostatni odczyt obserwujących" in note]
