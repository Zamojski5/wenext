"""Sesje współpracujące nad jednym projektem (P6 etap A).

CO SIĘ PSUJE, GDY SESJE ZACZYNAJĄ SOBIE PRZEKAZYWAĆ PRACĘ. Dwie rzeczy, i obie są tutaj:

  1. **Dwie identyczne publikacje.** Dwie sesje pracujące nad tym samym celem dochodzą do tego
     samego wniosku i każda przygotowuje odpowiedź pod tym samym postem. `_already_done` tego nie
     łapie, bo pilnuje JEDNEJ sesji (powtórzona tura po restarcie), a to jest inny problem: dwie
     sesje, z których każda była pierwsza w swoim śladzie. Klucz jest po projekcie, celu i
     odcisku treści.
  2. **Przekazanie jako droga do cudzego dostępu.** „Zleć to sesji w projekcie X" byłoby
     sięgnięciem po konta, do których sesja zlecająca nie ma prawa. Odmowa jest w
     `SessionStore.create`, bo CHECK w bazie nie umie odpytać innej tabeli, i jest zmierzona tu.

TRZECIA RZECZ, MNIEJ OCZYWISTA: sesja, która jeszcze pracuje, odpowiada OCZEKUJE, a nie pustym
wynikiem. „Jeszcze nie skończyła" i „skończyła i nic nie znalazła" prowadzą do różnych następnych
ruchów, a model, który ich nie odróżnia, zaraportuje drugie, gdy prawdziwe jest pierwsze.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.sessions.store import (
    SESSION_CONVERSATION,
    SESSION_PRESENCE,
    STATUS_DONE,
    SessionRefusal,
    SessionStore,
)
from control_plane.tools.capability import issue
from control_plane.tools.registry import ToolRegistry

pytestmark = [pytest.mark.db, pytest.mark.p6]

TARGET = "t3_abc123"
TEXT = "To zależy od tego, ile masz węzłów. Przy trzech nie warto."


async def _project(pool, tenant, *, name: str | None = None):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            name or f"projekt-{uuid4().hex[:8]}",
        )


async def _account(pool, tenant, project_id, *, platform: str = "REDDIT") -> str:
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            platform,
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
        return await conn.fetchval(
            "SELECT external_account_id FROM social_accounts WHERE id = $1", account_id
        )


async def _session(pool, tenant, project_id, *, kind: str = SESSION_CONVERSATION):
    return await SessionStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="popracuj nad tym",
        budget={},
        kind=kind,
    )


def _registry(pool, tenant, project_id, session_id) -> ToolRegistry:
    return ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )


def _draft_args(handle: str, *, text: str = TEXT, target: str = TARGET) -> dict:
    return {
        "target_external_id": target,
        "target_url": f"https://www.reddit.com/r/devops/comments/{target[3:]}/x/",
        "text": text,
        "account": handle,
    }


# ---------------------------------------------------------------------------
# Dwie sesje, jedna publikacja
# ---------------------------------------------------------------------------


async def test_two_sessions_do_not_propose_the_same_reply_twice(pool, tenant) -> None:
    """THE CASE THE ASSIGNMENT NAMES. Dwa ślady, w każdym to pierwsze wywołanie, a wniosek jeden.

    Gdyby deduplikacja siedziała tylko w śladzie sesji (`_already_done`), oba przeszłyby, a pod
    jednym postem stanęłyby dwie nasze odpowiedzi.
    """
    project_id = await _project(pool, tenant)
    handle = await _account(pool, tenant, project_id)
    first = await _session(pool, tenant, project_id)
    second = await _session(pool, tenant, project_id)

    one = await _registry(pool, tenant, project_id, first.id).call(
        "draft.create", _draft_args(handle)
    )
    assert one["stan"] == "OCZEKUJE", one
    two = await _registry(pool, tenant, project_id, second.id).call(
        "draft.create", _draft_args(handle)
    )
    assert two["stan"] == "OCZEKUJE", two
    assert "już czeka" in two["powod"], two
    assert two["propozycja"]["revision_id"] == one["propozycja"]["revision_id"]

    async with pool.acquire() as conn:
        proposals = await conn.fetchval(
            """
            SELECT count(*) FROM action_proposal_revisions r
              JOIN action_proposals p ON p.id = r.proposal_id
              JOIN project_account_assignments x ON x.social_account_id = p.social_account_id
             WHERE x.project_id = $1
            """,
            project_id,
        )
    assert proposals == 1, "jedna propozycja, nie dwie"


async def test_the_same_words_with_different_spacing_are_the_same_draft(pool, tenant) -> None:
    """Jedna reguła „ta sama treść", ta sama, której publikator używa do rozpoznania naszego
    komentarza na wątku (`pilot/publication.py::normalised_draft`)."""
    project_id = await _project(pool, tenant)
    handle = await _account(pool, tenant, project_id)
    first = await _session(pool, tenant, project_id)
    second = await _session(pool, tenant, project_id)

    await _registry(pool, tenant, project_id, first.id).call("draft.create", _draft_args(handle))
    again = await _registry(pool, tenant, project_id, second.id).call(
        "draft.create", _draft_args(handle, text=f"  {TEXT.upper()}\n\n ")
    )
    assert "już czeka" in again["powod"], again


async def test_a_different_text_under_the_same_target_is_allowed(pool, tenant) -> None:
    """Deduplikacja nie jest zakazem pracy nad tym samym wątkiem. Dwie RÓŻNE propozycje pod
    jednym postem są legalne; wybiera człowiek albo polityka, i tak było przed P6."""
    project_id = await _project(pool, tenant)
    handle = await _account(pool, tenant, project_id)
    first = await _session(pool, tenant, project_id)
    second = await _session(pool, tenant, project_id)

    await _registry(pool, tenant, project_id, first.id).call("draft.create", _draft_args(handle))
    other = await _registry(pool, tenant, project_id, second.id).call(
        "draft.create", _draft_args(handle, text="Zupełnie inne zdanie, inny sens.")
    )
    assert other["stan"] == "OCZEKUJE"
    assert "już czeka" not in other["powod"], other


async def test_the_same_text_under_a_different_target_is_allowed(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    handle = await _account(pool, tenant, project_id)
    first = await _session(pool, tenant, project_id)
    second = await _session(pool, tenant, project_id)

    await _registry(pool, tenant, project_id, first.id).call("draft.create", _draft_args(handle))
    elsewhere = await _registry(pool, tenant, project_id, second.id).call(
        "draft.create", _draft_args(handle, target="t3_zzz999")
    )
    assert "już czeka" not in elsewhere["powod"], elsewhere


async def test_another_project_is_not_deduplicated_against_this_one(pool, tenant) -> None:
    """Klucz jest po PROJEKCIE. Dwa projekty mogą odpowiadać pod tym samym postem: to dwie różne
    obecności, często z różnych kont."""
    here = await _project(pool, tenant)
    there = await _project(pool, tenant)
    handle_here = await _account(pool, tenant, here)
    handle_there = await _account(pool, tenant, there)

    session_here = await _session(pool, tenant, here)
    session_there = await _session(pool, tenant, there)
    await _registry(pool, tenant, here, session_here.id).call(
        "draft.create", _draft_args(handle_here)
    )
    other = await _registry(pool, tenant, there, session_there.id).call(
        "draft.create", _draft_args(handle_there)
    )
    assert "już czeka" not in other["powod"], other


# ---------------------------------------------------------------------------
# Przekazanie zadania
# ---------------------------------------------------------------------------


async def test_a_handoff_opens_a_child_session_pointing_back(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id)
    registry = _registry(pool, tenant, project_id, parent.id)

    result = await registry.call(
        "session.handoff", {"goal": "zbadaj, co ludzie mówią o cenie", "refs": [str(uuid4())]}
    )
    assert result["stan"] == "OCZEKUJE", result
    child_id = result["sesja"]["session_id"]

    sessions = SessionStore(pool)
    child = await sessions.get(organization_id=tenant["organization"], session_id=child_id)
    assert child is not None
    assert child.parent_session_id == parent.id
    assert child.project_id == project_id
    # The refs travel in the goal, as text the child model actually reads.
    assert result["sesja"]["przekazane_odwolania"][0] in child.goal

    children = await sessions.children_of(
        organization_id=tenant["organization"], session_id=parent.id
    )
    assert [one.id for one in children] == [child.id]


async def test_a_handoff_inherits_the_kind_of_the_asking_session(pool, tenant) -> None:
    """Przekazanie w tiku obecności nie otwiera po cichu rozmowy, przed którą nikt nie siedzi."""
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id, kind=SESSION_PRESENCE)
    result = await _registry(pool, tenant, project_id, parent.id).call(
        "session.handoff", {"goal": "sprawdź drugą społeczność"}
    )
    child = await SessionStore(pool).get(
        organization_id=tenant["organization"], session_id=result["sesja"]["session_id"]
    )
    assert child is not None and child.kind == SESSION_PRESENCE


async def test_a_handoff_into_another_project_is_refused(pool, tenant) -> None:
    """Przekazanie do cudzego projektu byłoby sięgnięciem po dostęp, którego sesja nie ma.

    Sprawdzenie jest w kodzie, nie w bazie, i 0019 mówi dlaczego: CHECK w PostgreSQL nie umie
    odpytać innej tabeli, a klucz obcy po trójce tenantowej łapie organizację i workspace, ale
    nie projekt.
    """
    here = await _project(pool, tenant)
    there = await _project(pool, tenant)
    parent = await _session(pool, tenant, here)

    with pytest.raises(SessionRefusal) as caught:
        await SessionStore(pool).create(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=there,
            goal="praca w cudzym projekcie",
            budget={},
            parent_session_id=parent.id,
        )
    assert "innym projektem" in str(caught.value)


async def test_reading_the_result_of_a_session_that_is_still_working(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id)
    registry = _registry(pool, tenant, project_id, parent.id)
    handed = await registry.call("session.handoff", {"goal": "policz wzmianki"})
    child_id = handed["sesja"]["session_id"]

    waiting = await registry.call("session.result", {"session_id": child_id})
    assert waiting["stan"] == "OCZEKUJE", waiting
    assert "jeszcze pracuje" in waiting["powod"]
    # Explicitly NOT an empty result: the distinction is the point of the tool.
    assert "wynik" not in waiting


async def test_reading_the_result_of_a_finished_session(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id)
    registry = _registry(pool, tenant, project_id, parent.id)
    handed = await registry.call("session.handoff", {"goal": "policz wzmianki"})
    child_id = handed["sesja"]["session_id"]

    sessions = SessionStore(pool)
    await sessions.set_status(
        child_id,
        organization_id=tenant["organization"],
        status=STATUS_DONE,
        result_summary="Znalazłem cztery wzmianki, wszystkie o cenie.",
        result_refs=[{"tool": "memory.list", "returned_ids": [str(uuid4())]}],
    )

    result = await registry.call("session.result", {"session_id": child_id})
    assert result["stan"] == "WYKONANE", result
    assert result["wynik"] == "Znalazłem cztery wzmianki, wszystkie o cenie."
    assert result["odwolania"][0]["tool"] == "memory.list"


async def test_a_session_cannot_read_the_result_of_work_it_did_not_hand_off(pool, tenant) -> None:
    """Nieobecne, nie zabronione: sesja cudzego rodzica czyta się tak samo jak nieistniejąca."""
    project_id = await _project(pool, tenant)
    asking = await _session(pool, tenant, project_id)
    stranger_parent = await _session(pool, tenant, project_id)
    handed = await _registry(pool, tenant, project_id, stranger_parent.id).call(
        "session.handoff", {"goal": "nie twoja sprawa"}
    )

    result = await _registry(pool, tenant, project_id, asking.id).call(
        "session.result", {"session_id": handed["sesja"]["session_id"]}
    )
    assert result["stan"] == "NIEDOSTEPNE", result
    assert "niczego nie zlecała" in result["powod"]


async def test_listing_handoffs_without_an_argument(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id)
    registry = _registry(pool, tenant, project_id, parent.id)
    assert (await registry.call("session.result", {}))["liczba"] == 0

    await registry.call("session.handoff", {"goal": "pierwsze"})
    await registry.call("session.handoff", {"goal": "drugie"})
    listed = await registry.call("session.result", {})
    assert listed["liczba"] == 2
    assert [one["skonczona"] for one in listed["zlecone"]] == [False, False]


async def test_a_handoff_without_a_goal_is_refused(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    parent = await _session(pool, tenant, project_id)
    result = await _registry(pool, tenant, project_id, parent.id).call(
        "session.handoff", {"goal": "   "}
    )
    assert result["stan"] == "BLAD", result
