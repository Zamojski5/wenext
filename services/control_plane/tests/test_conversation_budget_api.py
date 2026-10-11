"""Limit rozmowy ustawiany w projekcie i wyjście z rozmowy, która do niego doszła (0026).

SKĄD TO SIĘ WZIĘŁO: uwaga założyciela z 20.09, 01:02. Rozmowa skończyła się po trzech jego
zdaniach, komunikat nazwał limit kluczami (`tool_calls 5, turns 3`), a ekran zostawił go bez
ruchu: „Sesja jest zakończona. Zacznij nową". Nowej nie było gdzie zacząć.

CO JEST MIERZONE PRZY BAZIE, bo tego nie da się zmierzyć bez niej: że ustawienie z projektu
naprawdę trafia do nowej rozmowy, że da się je cofnąć do wartości domyślnej, że nonsensu nie
przyjmujemy, i że „Nowa rozmowa" zakłada sesję WSKAZUJĄCĄ poprzednią, zamiast wznawiać
wyczerpaną.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.p2]

WORKSPACES = "/api/workspaces"
GOAL = "sprawdź, dlaczego odzew jest mały, i zaproponuj zmianę"


async def _workspace(client, name: str = "Przestrzeń 0026") -> str:
    response = await client.post(WORKSPACES, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["workspace"]["workspace_id"]


async def _project(client, workspace_id: str, name: str = "Projekt 0026") -> dict:
    response = await client.post(f"{WORKSPACES}/{workspace_id}/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["project"]


def _path(workspace_id: str, project_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/projects/{project_id}/sessions"


async def _patch(client, workspace_id: str, project_id: str, **body) -> dict:
    response = await client.patch(
        f"{WORKSPACES}/{workspace_id}/projects/{project_id}",
        json={"name": "Projekt 0026", "mode": "AUTONOMOUS", **body},
    )
    assert response.status_code == 200, response.text
    return response.json()["project"]


async def test_a_project_with_no_setting_still_shows_the_limit_that_binds(
    pool, logged_in_client
) -> None:
    """Limit, którego nie widać, wygląda na limit cudzy. Ustawienia nie ma, wartość domyślna
    jedzie obok, i ekran ma z czego powiedzieć, ile przysługuje."""
    workspace_id = await _workspace(logged_in_client)
    project = await _project(logged_in_client, workspace_id)

    assert project["conversation_budget"] is None
    assert project["conversation_budget_default"]["max_turns"] >= 30
    assert project["conversation_budget_default"]["max_tool_calls"] >= 120


async def test_what_the_project_sets_reaches_the_next_conversation(pool, logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = (await _project(logged_in_client, workspace_id))["project_id"]

    saved = await _patch(
        logged_in_client,
        workspace_id,
        project_id,
        conversation_budget={"max_turns": 80, "max_tool_calls": 300},
    )
    assert saved["conversation_budget"] == {"max_turns": 80, "max_tool_calls": 300}

    started = await logged_in_client.post(_path(workspace_id, project_id), json={"goal": GOAL})
    assert started.status_code == 201, started.text
    session_id = started.json()["session"]["session_id"]
    detail = await logged_in_client.get(f"{_path(workspace_id, project_id)}/{session_id}")
    assert detail.status_code == 200
    budzet = detail.json()["budzet"]
    assert budzet["tury"]["limit"] == 80
    assert budzet["narzedzia"]["limit"] == 300
    # I ekran wie, że to ustawienie człowieka, a nie wartość domyślna produktu.
    assert budzet["wlasny"] is True


async def test_an_empty_setting_goes_back_to_the_default_rather_than_to_zero(
    pool, logged_in_client
) -> None:
    """„Wróć do domyślnego" musi być osiągalne. Gdyby puste pole znaczyło „nie ruszaj", własnego
    ustawienia nie dałoby się cofnąć; gdyby znaczyło zero, rozmowa nie mogłaby się zacząć."""
    workspace_id = await _workspace(logged_in_client)
    project_id = (await _project(logged_in_client, workspace_id))["project_id"]
    await _patch(logged_in_client, workspace_id, project_id, conversation_budget={"max_turns": 80})

    cleared = await _patch(
        logged_in_client, workspace_id, project_id, conversation_budget_default=True
    )

    assert cleared["conversation_budget"] is None
    started = await logged_in_client.post(_path(workspace_id, project_id), json={"goal": GOAL})
    session_id = started.json()["session"]["session_id"]
    detail = await logged_in_client.get(f"{_path(workspace_id, project_id)}/{session_id}")
    budzet = detail.json()["budzet"]
    assert budzet["tury"]["limit"] == cleared["conversation_budget_default"]["max_turns"]
    assert budzet["wlasny"] is False


@pytest.mark.parametrize(
    "budget",
    [
        {"max_turns": 0},
        {"max_turns": -5},
        {"max_turns": 10_000},
        {"czego_takiego_nie_ma": 5},
        {"max_turns": "dużo"},
    ],
)
async def test_nonsense_is_refused_with_a_sentence_not_accepted(
    pool, logged_in_client, budget
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = (await _project(logged_in_client, workspace_id))["project_id"]

    response = await logged_in_client.patch(
        f"{WORKSPACES}/{workspace_id}/projects/{project_id}",
        json={"name": "Projekt 0026", "mode": "AUTONOMOUS", "conversation_budget": budget},
    )

    assert response.status_code in (400, 422), response.text


async def test_a_conversation_that_hit_the_limit_continues_into_a_new_one(
    pool, logged_in_client
) -> None:
    """NIE WZNAWIA WYCZERPANEJ, tylko zakłada następną, która ją wskazuje. Doliczenie tur do
    zamkniętej rozmowy zrobiłoby z budżetu liczbę, która nic nie znaczy."""
    workspace_id = await _workspace(logged_in_client)
    project_id = (await _project(logged_in_client, workspace_id))["project_id"]
    await _patch(logged_in_client, workspace_id, project_id, conversation_budget={"max_turns": 40})
    started = await logged_in_client.post(_path(workspace_id, project_id), json={"goal": GOAL})
    parent_id = started.json()["session"]["session_id"]

    response = await logged_in_client.post(
        f"{_path(workspace_id, project_id)}/{parent_id}/continue"
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parent_session_id"] == parent_id
    child_id = body["session"]["session_id"]
    assert child_id != parent_id
    assert body["session"]["goal"] == GOAL

    async with pool.acquire() as conn:
        parent_of_child = await conn.fetchval(
            "SELECT parent_session_id FROM agent_sessions WHERE id = $1", child_id
        )
    assert str(parent_of_child) == parent_id

    detail = await logged_in_client.get(f"{_path(workspace_id, project_id)}/{child_id}")
    assert detail.json()["budzet"]["tury"]["limit"] == 40, "nowa rozmowa bierze limit z projektu"


async def test_a_session_from_another_project_cannot_be_continued_here(
    pool, logged_in_client
) -> None:
    workspace_id = await _workspace(logged_in_client)
    first = (await _project(logged_in_client, workspace_id, name="Pierwszy"))["project_id"]
    second = (await _project(logged_in_client, workspace_id, name="Drugi"))["project_id"]
    started = await logged_in_client.post(_path(workspace_id, first), json={"goal": GOAL})
    session_id = started.json()["session"]["session_id"]

    response = await logged_in_client.post(f"{_path(workspace_id, second)}/{session_id}/continue")

    assert response.status_code == 404, response.text
