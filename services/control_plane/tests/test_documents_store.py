"""Zapis dokumentów projektu przy prawdziwej bazie (P8 etap A, 0028).

CO TU JEST MIERZONE, A CZEGO NIE DA SIĘ ZMIERZYĆ BEZ BAZY: więzy. Zdanie bez podstawy, podstawa
wskazująca ustalenie z cudzej przestrzeni, druga wersja dokumentu bez zmian w rejestrze i
kolejność wersji to wszystko rzeczy, które albo rozstrzyga PostgreSQL, albo transakcja.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.documents import DocumentStore, build_documents
from control_plane.documents.store import DocumentRefused
from control_plane.knowledge.store import KnowledgeStore, NewItem, Scope

pytestmark = [pytest.mark.db, pytest.mark.p8]


async def _project(pool, tenant) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


def _scope(tenant, project_id) -> Scope:
    return Scope(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )


async def _items(pool, tenant, project_id):
    store = KnowledgeStore(pool)
    scope = _scope(tenant, project_id)
    written = [
        await store.write(
            NewItem(
                kind="PREFERENCE",
                statement="piszemy krótko i bez żargonu",
                stated_by="USER",
                project_id=project_id,
                payload={"hypothesis": True},
            ),
            scope,
        ),
        await store.write(
            NewItem(
                kind="INTERPRETATION",
                statement="wieczorem odzew jest mniejszy",
                stated_by="AGENT",
                project_id=project_id,
            ),
            scope,
        ),
    ]
    return written


async def _save_all(pool, tenant, project_id, items):
    store = DocumentStore(pool)
    saved = []
    for draft in build_documents(
        list(items), project_name="Projekt", project_goal="cel", project_mode="AUTONOMOUS"
    ):
        saved.append(
            await store.save(
                draft,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                project_id=project_id,
            )
        )
    return saved


async def test_documents_are_written_with_their_sentences_and_bases(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)

    saved = await _save_all(pool, tenant, project_id, items)

    assert all(created for _, created in saved), "pierwszy zapis tworzy wszystkie cztery wersje"
    index = await DocumentStore(pool).index(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    assert [one.role for one in index] == [
        "PROJECT_CARD",
        "WHAT_WORKS",
        "DECISIONS",
        "COMMUNITIES",
    ], "kolejność ról jest stała, bo czyta ją model na starcie sesji"
    card = next(one for one in index if one.role == "PROJECT_CARD")
    document = await DocumentStore(pool).read(
        card.document_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )
    assert document is not None and document.version == 1
    assert document.sentences, "karta z hipotezą nie może być pusta"
    for sentence in document.sentences:
        assert sentence["basis"], sentence


async def test_a_second_run_over_an_unchanged_register_adds_no_version(pool, tenant) -> None:
    """Dziesięć przebiegów bez zmian w rejestrze ma zostawić jedną wersję, nie dziesięć."""
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)
    await _save_all(pool, tenant, project_id, items)

    again = await _save_all(pool, tenant, project_id, items)

    assert all(not created for _, created in again)
    assert all(version.version == 1 for version, _ in again)


async def test_a_changed_register_gives_a_new_version(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)
    await _save_all(pool, tenant, project_id, items)

    extra = await KnowledgeStore(pool).write(
        NewItem(
            kind="INTERPRETATION",
            statement="w poniedziałki jest najwięcej pytań",
            stated_by="AGENT",
            project_id=project_id,
        ),
        _scope(tenant, project_id),
    )
    after = await _save_all(pool, tenant, project_id, [*items, extra])

    decisions = next(version for version, _ in after if version.role == "DECISIONS")
    assert decisions.version == 2
    assert any("poniedziałki" in one["text"] for one in decisions.sentences)


async def test_a_basis_from_another_workspace_is_refused(pool, tenant) -> None:
    """Ustalenie z cudzej przestrzeni jest tu tym samym, co nieistniejące, i dostaje tę samą
    odmowę: dokument, który się na nie powołuje, przestałby być widokiem na TEN rejestr."""
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)
    drafts = build_documents(
        list(items), project_name="Projekt", project_goal="cel", project_mode="AUTONOMOUS"
    )
    card = next(one for one in drafts if one.role == "PROJECT_CARD")
    from dataclasses import replace

    from control_plane.documents import Sentence

    forged = replace(
        card,
        sentences=(Sentence(section="Hipotezy", text="zmyślone", basis=(uuid4(),)),),
    )

    with pytest.raises(DocumentRefused) as caught:
        await DocumentStore(pool).save(
            forged,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
        )

    assert "drugą pamięcią" in str(caught.value)


async def test_an_older_version_is_readable_and_a_missing_one_is_not_invented(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)
    await _save_all(pool, tenant, project_id, items)
    extra = await KnowledgeStore(pool).write(
        NewItem(
            kind="INTERPRETATION",
            statement="druga wersja ma o jedno zdanie więcej",
            stated_by="AGENT",
            project_id=project_id,
        ),
        _scope(tenant, project_id),
    )
    after = await _save_all(pool, tenant, project_id, [*items, extra])
    decisions = next(version for version, _ in after if version.role == "DECISIONS")
    store = DocumentStore(pool)

    first = await store.read(
        decisions.document_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        version=1,
    )
    missing = await store.read(
        decisions.document_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        version=99,
    )

    assert first is not None and first.version == 1
    assert len(first.sentences) < len(decisions.sentences)
    assert missing is None, "nieistniejąca wersja to brak, nigdy najbliższa istniejąca"


async def test_one_entry_point_rebuilds_the_whole_project(pool, tenant) -> None:
    """`rebuild_project` jest tą samą drogą, którą pójdzie zasiew e2e i job z etapu B.

    Mierzone tu, bo zasiew zbudowany surowymi INSERT-ami potrafi stworzyć stan, którego produkt
    nigdy nie wyprodukuje, a wtedy ekran testuje się na czymś, czego użytkownik nie zobaczy.
    """
    project_id = await _project(pool, tenant)
    await _items(pool, tenant, project_id)
    store = DocumentStore(pool)

    first = await store.rebuild_project(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    again = await store.rebuild_project(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )

    assert [one.role for one, _ in first] == [
        "PROJECT_CARD",
        "WHAT_WORKS",
        "DECISIONS",
        "COMMUNITIES",
    ]
    assert all(created for _, created in first)
    assert not any(created for _, created in again), "drugi przebieg bez zmian nie pisze wersji"


async def test_a_document_of_another_workspace_is_absent_not_forbidden(pool, tenant) -> None:
    project_id = await _project(pool, tenant)
    items = await _items(pool, tenant, project_id)
    saved = await _save_all(pool, tenant, project_id, items)
    card = next(version for version, _ in saved if version.role == "PROJECT_CARD")

    async with pool.acquire() as conn:
        other = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            tenant["organization"],
            f"sasiednia-{uuid4().hex[:8]}",
        )
    seen = await DocumentStore(pool).read(
        card.document_id, organization_id=tenant["organization"], workspace_id=other
    )

    assert seen is None


# -- hipoteza: człowieka, ale zapisana przez agenta po potwierdzeniu (D-044) ---------------------


async def test_a_hypothesis_needs_the_persons_own_words_in_this_conversation(pool, tenant) -> None:
    """D-044: agent wypracowuje hipotezę w rozmowie i zapisuje SAM, ale tylko wtedy, gdy wskaże
    wypowiedź, w której człowiek ją potwierdził.

    Trzy odmowy i jedno przyjęcie, bo każda z tych trzech dróg dałaby ustalenie przypisane
    człowiekowi, którego on nie wypowiedział, a następna sesja czytałaby je jako jego wolę.
    """
    from control_plane.sessions.store import (
        EVENT_ASSISTANT_MESSAGE,
        EVENT_USER_MESSAGE,
        SessionStore,
    )
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    project_id = await _project(pool, tenant)
    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="sprawdź, czego brakuje tej społeczności",
        budget={},
    )
    agent = await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_ASSISTANT_MESSAGE,
        payload={"text": "Proponuję hipotezę: ludzie wolą konkret."},
    )
    human = await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "tak, sprawdźmy to"},
    )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )
    hypothesis = {
        "kind": "INTERPRETATION",
        "statement": "ludzie w tej społeczności wolą konkret od opowieści",
    }

    without = await registry.call("memory.write", {**hypothesis, "payload": {"hypothesis": True}})
    assert without["stan"] == "BLAD" and "potwierdzi ją słowami" in str(without)

    made_up = await registry.call(
        "memory.write",
        {**hypothesis, "payload": {"hypothesis": True, "confirmed_at_seq": 9999}},
    )
    assert made_up["stan"] == "BLAD" and "nie ma zdarzenia" in str(made_up)

    # Wskazanie WŁASNEJ wiadomości agenta. Numer bierzemy z zapisanego zdarzenia, a nie „1":
    # `create()` zapisuje cel jako pierwszą wypowiedź CZŁOWIEKA, więc jedynka jest tu poprawnym
    # potwierdzeniem, a nie błędem, i test na sztywnym numerze mierzyłby coś innego, niż nazywa.
    own_words = await registry.call(
        "memory.write",
        {**hypothesis, "payload": {"hypothesis": True, "confirmed_at_seq": agent.seq}},
    )
    assert own_words["stan"] == "BLAD" and "nie wypowiedź człowieka" in str(own_words)

    accepted = await registry.call(
        "memory.write",
        {**hypothesis, "payload": {"hypothesis": True, "confirmed_at_seq": human.seq}},
    )
    assert accepted["stan"] == "WYKONANE", accepted

    async with pool.acquire() as conn:
        stated_by = await conn.fetchval(
            "SELECT stated_by FROM knowledge_items WHERE id = $1",
            accepted["zapisano"]["id"],
        )
    assert stated_by == "USER", "hipoteza potwierdzona słowami jest zdaniem człowieka"


async def test_a_confirmed_hypothesis_reaches_the_project_card_without_any_form(
    pool, tenant
) -> None:
    """Dowód etapu A rozszerzony przez koordynatora: po rozmowie karta ma hipotezę z autorem
    „człowiek", a nikt niczego nie wpisywał w formularzu."""
    from control_plane.sessions.store import EVENT_USER_MESSAGE, SessionStore
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    project_id = await _project(pool, tenant)
    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="ustal, co sprawdzamy",
        budget={},
    )
    human = await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "zgoda, tak to sprawdźmy"},
    )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )
    await registry.call(
        "memory.write",
        {
            "kind": "INTERPRETATION",
            "statement": "krótkie odpowiedzi dostają więcej reakcji niż długie",
            "payload": {"hypothesis": True, "confirmed_at_seq": human.seq},
        },
    )

    await DocumentStore(pool).rebuild_project(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )

    index = await DocumentStore(pool).index(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    card = next(one for one in index if one.role == "PROJECT_CARD")
    document = await DocumentStore(pool).read(
        card.document_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )
    assert document is not None
    hypotheses = [one for one in document.sentences if one["section"] == "Hipotezy"]
    assert len(hypotheses) == 1, document.sentences
    assert "krótkie odpowiedzi" in hypotheses[0]["text"]
    assert "postawił człowiek" in hypotheses[0]["text"]
    assert hypotheses[0]["basis"], "zdanie karty wskazuje ustalenie, z którego wynika"


async def test_a_document_of_another_project_is_not_readable_by_the_tool(pool, tenant) -> None:
    """Dokument należy do PROJEKTU i tak ma go widzieć narzędzie modelu (E6).

    `documents.list` pokazywał dokumenty tego projektu, a `documents.read` brał sam identyfikator
    i oddawał każdy dokument tej samej przestrzeni. Model pracujący nad jednym projektem miał
    tędy drogę do ustaleń projektu, nad którym nie pracuje, i nie musiał tego robić złośliwie:
    wystarczyło, że przeczytał identyfikator w starszej rozmowie. Trasa HTTP miała ten warunek od
    początku, bo projekt stoi u niej w ścieżce; narzędzie nie miało.
    """
    from control_plane.tools import ToolRegistry, issue

    mine = await _project(pool, tenant)
    other = await _project(pool, tenant)
    theirs = await _save_all(pool, tenant, other, await _items(pool, tenant, other))
    document_id = theirs[0][0].document_id

    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            mine,
        )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=mine,
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )

    refused = await registry.call("documents.read", {"document_id": str(document_id)})

    assert refused["stan"] == "NIEDOSTEPNE", refused
    assert "dokument" not in refused, "odmowa oddała jednak treść cudzego dokumentu"

    # A WŁASNY dalej się czyta: zawężenie nie jest zdjęciem narzędzia.
    ours = await _save_all(pool, tenant, mine, await _items(pool, tenant, mine))
    read = await registry.call("documents.read", {"document_id": str(ours[0][0].document_id)})
    assert read["stan"] == "WYKONANE", read


# ---------------------------------------------------------------------------
# Etap B: zmiana w rejestrze zamawia przepisanie, a job je wykonuje
#
# Pierwszy przebieg z bazą (21.09) zaliczył dwa z tych trzech; trzeci padł na ręcznie
# przepisanej krotce `ClaimedJob` bez czterech pól lease'u. Krotka idzie teraz z
# `tests/job_claims.py`.
# ---------------------------------------------------------------------------


async def test_writing_a_finding_orders_a_rebuild_of_that_project(pool, tenant) -> None:
    """Wyzwalacz jest w `KnowledgeStore`, więc widać go po wierszu w kolejce, nie po wywołaniu."""
    from control_plane.documents.handlers import JOB_REBUILD, project_ref

    project_id = await _project(pool, tenant)
    await _items(pool, tenant, project_id)

    async with pool.acquire() as conn:
        jobs = await conn.fetch(
            "SELECT idempotency_key, payload_ref, workspace_id FROM jobs"
            " WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_REBUILD,
        )

    assert jobs, "zapis ustalenia nie zamówił przepisania dokumentów"
    assert {row["payload_ref"] for row in jobs} == {project_ref(project_id)}
    assert len({row["idempotency_key"] for row in jobs}) == 1, (
        "trzy zapisy w jednym oknie mają dać JEDEN job; inaczej rozmowa przepisuje dokumenty "
        "tyle razy, ile razy coś zapisała"
    )


async def test_the_job_rebuilds_the_documents_and_repeats_without_a_new_version(
    pool, tenant
) -> None:
    """Job robi to samo, co `rebuild_project`, i drugi raz nie tworzy wersji.

    To jest ta część, której testy bez bazy nie mierzą: że handler potrafi wziąć ref z jobu,
    znaleźć projekt i zapisać dokumenty.
    """
    from control_plane.documents.handlers import DocumentHandlers, project_ref

    project_id = await _project(pool, tenant)
    await _items(pool, tenant, project_id)
    handlers = DocumentHandlers(pool=pool, organization_id=tenant["organization"])
    claim = _claim(tenant, project_ref(project_id))

    await handlers.rebuild(claim)
    index = await DocumentStore(pool).index(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    versions_before = {entry.role: entry.version for entry in index}
    await handlers.rebuild(claim)
    index_again = await DocumentStore(pool).index(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )

    assert versions_before, "job nie zapisał żadnego dokumentu"
    assert {entry.role: entry.version for entry in index_again} == versions_before, (
        "drugi przebieg bez zmian w rejestrze podniósł wersję; `input_digest` przestał działać"
    )


async def test_a_job_for_a_project_that_is_gone_finishes_quietly(pool, tenant) -> None:
    """Projekt skasowany po zamówieniu to nie jest awaria jobu, tylko spóźnione zamówienie."""
    from control_plane.documents.handlers import DocumentHandlers, project_ref

    handlers = DocumentHandlers(pool=pool, organization_id=tenant["organization"])

    await handlers.rebuild(_claim(tenant, project_ref(uuid4())))


def _claim(tenant, payload_ref: str):
    """Krotka z `tests/job_claims.py`, nie własna kopia: ręcznie przepisana gubiła cztery pola
    lease'u i padała dopiero w przebiegu z bazą (21.09)."""
    from control_plane.documents.handlers import JOB_REBUILD, PRIORITY_REBUILD, ROUTING_KEY
    from tests.job_claims import claimed_job

    return claimed_job(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_REBUILD,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_REBUILD,
        payload_ref=payload_ref,
    )
