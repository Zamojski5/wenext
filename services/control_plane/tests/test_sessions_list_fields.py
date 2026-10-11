"""Lista rozmów projektu i przestrzeni: pola paska bocznego (D-045).

PROJEKT JEST ZBIOREM ROZMÓW, więc lista sesji przestała być listą techniczną i stała się tym, po
czym człowiek wybiera, do czego wrócić. Trzy rzeczy, które mogą tu pójść źle po cichu:

  * tytuł liczony w przeglądarce: ta sama rozmowa nazywałaby się inaczej w pasku, inaczej w
    nagłówku i jeszcze inaczej w prompcie modelu;
  * filtr rodzaju po stronie przeglądarki: limit tnie przed filtrem, więc przy aktywnym projekcie
    pasek pokazałby same tiki obecności, bo tych jest kilkanaście dziennie, a rozmów kilka;
  * sekcja „Ostatnie" składana z zapytania na projekt: dziesięć projektów to dziesięć odczytów
    przy każdym wejściu na dowolny ekran.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = [pytest.mark.db, pytest.mark.p2]

WORKSPACES = "/api/workspaces"


async def _workspace(client, name: str = "Przestrzeń D-045") -> str:
    response = await client.post(WORKSPACES, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["workspace"]["workspace_id"]


async def _project(client, workspace_id: str, name: str) -> str:
    response = await client.post(f"{WORKSPACES}/{workspace_id}/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _sessions(workspace_id: str, project_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/projects/{project_id}/sessions"


async def _start(client, workspace_id: str, project_id: str, goal: str) -> dict:
    response = await client.post(_sessions(workspace_id, project_id), json={"goal": goal})
    assert response.status_code == 201, response.text
    return response.json()["session"]


async def test_the_list_carries_the_name_the_screen_shows(pool, logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id, "Projekt")
    await _start(
        logged_in_client, workspace_id, project_id, "Sprawdź, czemu odzew jest mały. Potem wróć."
    )

    listing = await logged_in_client.get(_sessions(workspace_id, project_id))

    assert listing.status_code == 200, listing.text
    row = listing.json()["sessions"][0]
    assert row["tytul"] == "Sprawdź, czemu odzew jest mały."
    assert row["rodzaj"] == "CONVERSATION"
    # Rozmowa, która jeszcze nic nie powiedziała, nie zmyśla wyniku.
    assert row["wynik_zdanie"] is None
    assert row["result_summary"] is None
    assert row["goal"].startswith("Sprawdź"), "pełny cel zostaje obok tytułu"


async def test_a_goal_that_cannot_be_named_gives_null_not_an_empty_title(
    pool, logged_in_client
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id, "Projekt")
    await _start(logged_in_client, workspace_id, project_id, "ok")

    listing = await logged_in_client.get(_sessions(workspace_id, project_id))

    assert listing.json()["sessions"][0]["tytul"] is None


async def test_the_kind_filter_narrows_on_the_server(pool, logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id, "Projekt")
    conversation = await _start(
        logged_in_client, workspace_id, project_id, "Rozmowa człowieka z agentem."
    )
    async with pool.acquire() as conn:
        # `finished_at` jest wymagane przy stanie końcowym (więz z 0015): sesja zakończona
        # bez godziny zakończenia to wiersz, z którego nie da się powiedzieć, kiedy to było.
        await conn.execute(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind, "
            "status, budget, finished_at) SELECT organization_id, workspace_id, project_id, "
            "'tik obecności', 'PRESENCE', 'DONE', '{}'::jsonb, now() "
            "FROM agent_sessions WHERE id = $1",
            conversation["session_id"],
        )

    both = await logged_in_client.get(_sessions(workspace_id, project_id))
    only = await logged_in_client.get(
        _sessions(workspace_id, project_id), params={"rodzaj": "CONVERSATION"}
    )
    wrong = await logged_in_client.get(
        _sessions(workspace_id, project_id), params={"rodzaj": "COSTAM"}
    )

    assert {row["rodzaj"] for row in both.json()["sessions"]} == {"CONVERSATION", "PRESENCE"}
    assert {row["rodzaj"] for row in only.json()["sessions"]} == {"CONVERSATION"}
    # Literówka w filtrze ma być odmową, nie „oddaj wszystko": inaczej pasek pokazałby tiki
    # obecności i nikt by nie wiedział czemu.
    assert wrong.status_code == 400, wrong.text


async def test_the_workspace_listing_is_one_read_across_projects(pool, logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    first = await _project(logged_in_client, workspace_id, "Pierwszy")
    second = await _project(logged_in_client, workspace_id, "Drugi")
    await _start(logged_in_client, workspace_id, first, "Rozmowa w pierwszym projekcie.")
    await _start(logged_in_client, workspace_id, second, "Rozmowa w drugim projekcie.")

    listing = await logged_in_client.get(
        f"{WORKSPACES}/{workspace_id}/sessions",
        params={"rodzaj": "CONVERSATION", "limit": 10},
    )

    assert listing.status_code == 200, listing.text
    titles = [row["tytul"] for row in listing.json()["sessions"]]
    assert "Rozmowa w drugim projekcie." in titles
    assert "Rozmowa w pierwszym projekcie." in titles
    # Najnowsza pierwsza, ta sama kolejność co w liście projektu.
    assert titles[0] == "Rozmowa w drugim projekcie."


async def test_a_workspace_of_another_organization_is_absent(pool, logged_in_client) -> None:
    listing = await logged_in_client.get(f"{WORKSPACES}/{uuid4()}/sessions")
    assert listing.status_code == 404, listing.text
