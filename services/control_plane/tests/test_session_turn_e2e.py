"""A whole session, end to end, against a real database and a stand-in app-server (P2).

WHAT RUNS HERE FOR REAL, and it is nearly everything: PostgreSQL, the session store, the turn
handler, the async app-server client over a real subprocess and a real stdio protocol, the
turn's configuration handed to that process in `--config` arguments (E9, 21.09: it used to be
written into the connection's shared `config.toml`), the `wenext` MCP server started FROM those
arguments as a separate process, its capability file, and every tool call going through it to
the database and back.

WHAT DOES NOT RUN: a model. `codex_runtime.fake_app_server` decides which tools to call, from a
script this file writes. So these tests prove the MECHANICS of a session and prove NOTHING about
judgment, wording or whether the agent chose well. Every sentence of the report has to keep that
distinction; the tests keep it by never asserting anything about the content of an answer beyond
the fact that it was recorded.

THE FOUR THINGS BEING MEASURED, in the order the milestone states them:

  1. a person writes a goal, a session and a job appear, the agent discovers its tools, reads,
     remembers and answers WITH REFERENCES taken from the trace rather than from its prose;
  2. the person adds a correction and the agent gets it, on the SAME engine thread;
  3. a restart mid-turn loses neither the session nor its history, and does NOT repeat a tool
     call that writes. The turn itself is repeated on purpose; the effect is not;
  4. after the restart the screen shows the same state and the same history, which here means
     the trace read back by sequence number.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_MESSAGE,
    STATUS_PAUSED,
    STATUS_WAITING_USER,
    SessionStore,
)
from control_plane.sessions.handlers import (
    OUTCOME_ANSWERED,
    OUTCOME_NO_CONNECTION,
    OUTCOME_NOT_CLAIMED,
    SessionTurnHandlers,
)

pytestmark = [pytest.mark.db, pytest.mark.p2]

GOAL = "sprawdź, dlaczego odzew jest mały, i zaproponuj zmianę"

#: Ile NAJDŁUŻEJ czekamy na zdarzenie, którego test potrzebuje, żeby ruszyć dalej. Dużo, bo to
#: nie jest asercja o szybkości: pętla i tak kończy się w chwili, w której zdarzenie przyjdzie.
WAIT_S = 60.0


async def _until(check, *, what: str, deadline_s: float = WAIT_S):
    """Czekaj na ZDARZENIE, a nie na ustaloną liczbę obrotów pętli (E8).

    CO BYŁO. Dwa testy tej rodziny czekały `for _ in range(100)` po `sleep(0.05)`, czyli
    najwyżej pięć sekund WEDŁUG LICZNIKA, ale każdy obrót robi też zapytanie do bazy. Na
    obciążonym hoście (zmierzone 20.09: load 20, Docker VM 650%) pętla przekraczała okno,
    w którym tura jeszcze trwała, a test przewracał się na asercji o czymś zupełnie innym, więc
    zgłoszenie mówiło „przerwanie nie zadziałało", choć nie zadziałało CZEKANIE.

    CO JEST. Termin liczony zegarem, koniec w chwili zdarzenia i GŁOŚNA porażka z nazwą tego, na
    co czekaliśmy, gdy nie przyjdzie. Stara pętla po prostu wychodziła i szła dalej, więc
    prawdziwa przyczyna była niewidoczna aż do czytania kodu.
    """
    started = time.monotonic()
    while time.monotonic() - started < deadline_s:
        value = await check()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(f"nie doczekałem się: {what} (termin {deadline_s:g} s)")


def _codex_shim(tmp_path: Path) -> Path:
    """An executable that answers to `<bin> app-server --listen stdio://` with the fake.

    The client spawns `codex app-server --listen stdio://` because that is what the real binary
    wants; the shim swallows those arguments. Writing the shim rather than special-casing the
    client keeps the client identical to the one that will run against the real Codex.
    """
    shim = tmp_path / "codex-fake"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m codex_runtime.fake_app_server "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _script(tmp_path: Path, script: dict) -> Path:
    path = tmp_path / f"script-{uuid4().hex[:8]}.json"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return path


async def _project(pool, tenant) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel projektu') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


async def _thread_to_read(pool, tenant) -> str:
    async with pool.acquire() as conn:
        community = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', 'r/devops', 'Reddit r/devops', 'ACCOUNT_REQUIRED', 600) "
            "RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        thread_id = await conn.fetchval(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title, body,
                 author, created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, 'e2e001', 'https://www.reddit.com/r/devops/comments/e2e001/x/',
                    'jak wy to robicie na produkcji', 'treść posta', 'op',
                    now(), now(), now(), $4)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            community,
            "1" * 64,
        )
        await conn.execute(
            """
            INSERT INTO observed_comments
                (organization_id, workspace_id, thread_id, external_id, parent_external_id,
                 author, body, created_at_source, observed_at, depth, score)
            VALUES ($1, $2, $3, 'c1', NULL, 'ktos', 'u nas to wygląda tak', now(), now(), 0, 3)
            """,
            tenant["organization"],
            tenant["workspace"],
            thread_id,
        )
    return "e2e001"


def _handlers(pool, tenant, tmp_path, dsn, *, home: Path | None, queue: object | None = None):
    """`queue` domyślnie None, bo większość tych testów nie łańcuchuje tur.

    Test, który sprawdza POWRÓT (rozmowa bez silnika ma ruszyć sama), musi dostać prawdziwą
    kolejkę: z atrapą „zaplanowano powrót" znaczyłoby tylko „kod doszedł do tej linii", a to jest
    dokładnie to zdanie, którego E1 nie wolno oprzeć na niczym innym niż wiersz w `jobs`.
    """

    async def codex_home_for(_session):
        return home

    return SessionTurnHandlers(
        pool=pool,
        sessions=SessionStore(pool),
        queue=queue,  # type: ignore[arg-type]  # None: no chaining in these tests
        organization_id=tenant["organization"],
        dsn=dsn,
        codex_bin=_codex_shim(tmp_path),
        codex_home_for=codex_home_for,
        capability_dir=tmp_path / "caps",
    )


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("WENEXT_TEST_DSN")
    if not value:
        pytest.fail("brak WENEXT_TEST_DSN: ten test wymaga bazy")
    return value


# ---------------------------------------------------------------------------
# 1. A goal becomes a session, and an answer stands on tool calls
# ---------------------------------------------------------------------------


async def test_a_goal_becomes_a_session_that_discovers_reads_remembers_and_answers(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    external_id = await _thread_to_read(pool, tenant)
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )

    script = _script(
        tmp_path,
        {
            "thread_id": "th_e2e",
            "turns": [
                {
                    "steps": [
                        {"tool": "catalog.list", "arguments": {}},
                        {"tool": "thread.read", "arguments": {"thread_id": external_id}},
                        {
                            "tool": "memory.write",
                            "arguments": {
                                # INTERPRETATION, not FACT: a fact needs a source row and the
                                # agent has not read one. The schema would refuse it, which is
                                # the rule working rather than a limitation of this test.
                                "kind": "INTERPRETATION",
                                "statement": "wątek e2e001 ma tylko jeden komentarz",
                                "source_span": "thread.read e2e001",
                            },
                        },
                        {"message": "Odzew jest mały, bo wchodzimy późno. Źródło: wątek e2e001."},
                    ],
                    "status": "completed",
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    result = await handlers.run_once(session.id)

    assert result.outcome == OUTCOME_ANSWERED, result.detail

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    kinds = [e.kind for e in events]
    assert kinds[0] == EVENT_USER_MESSAGE, "cel nie otwiera śladu"
    tool_calls = [e.payload["tool"] for e in events if e.kind == EVENT_TOOL_CALL]
    assert tool_calls == ["catalog.list", "thread.read", "memory.write"], (
        f"wywołania narzędzi w śladzie: {tool_calls}. Te wywołania przeszły przez prawdziwy "
        "serwer MCP, więc ich brak znaczy, że narzędzia nie zadziałały"
    )
    answers = [e.payload["text"] for e in events if e.kind == EVENT_ASSISTANT_MESSAGE]
    assert answers and "e2e001" in answers[-1]

    fresh = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None
    assert fresh.status == STATUS_WAITING_USER, (
        "sesja zamknęła się sama. Czy cel jest osiągnięty, rozstrzyga człowiek"
    )
    assert fresh.engine_thread_id == "th_e2e", "nie zapisano wątku silnika, więc nie ma co wznawiać"
    assert [ref["tool"] for ref in fresh.result_refs] == tool_calls, (
        "odnośniki wyniku nie pochodzą ze śladu wywołań. Odnośnik napisany przez model w zdaniu "
        "jest twierdzeniem, a wywołanie w śladzie jest zapisem"
    )

    # The tool really ran: the memory row exists, written by the MCP server in its own process.
    async with pool.acquire() as conn:
        remembered = await conn.fetch(
            "SELECT statement, kind, stated_by, source_span, session_id FROM knowledge_items "
            "WHERE organization_id = $1 AND project_id = $2",
            tenant["organization"],
            session.project_id,
        )
    assert len(remembered) == 1
    assert remembered[0]["session_id"] == session.id
    assert remembered[0]["kind"] == "INTERPRETATION"
    assert remembered[0]["stated_by"] == "AGENT"
    assert remembered[0]["source_span"] == "thread.read e2e001"


async def test_the_capability_file_does_not_outlive_the_turn(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """It carries a DSN. A file with a database password left behind after every turn would be a
    growing pile of credentials in a directory nobody looks at."""
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_minutes": 1},
    )
    script = _script(tmp_path, {"turns": [{"steps": [{"message": "gotowe"}]}]})
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    await handlers.run_once(session.id)

    caps = list((tmp_path / "caps").glob("*.json"))
    assert caps == [], f"po turze zostały pliki zdolności: {caps}"


# ---------------------------------------------------------------------------
# 2. The person adds a correction
# ---------------------------------------------------------------------------


async def test_a_late_correction_keeps_its_provenance_beyond_500_events(pool, tenant, tmp_path):
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={},
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind, payload) "
            "SELECT $1, $2, $3, n, 'STATUS', '{\"level\":\"engine\"}'::jsonb "
            "FROM generate_series(100, 650) AS n",
            tenant["organization"],
            tenant["workspace"],
            session.id,
        )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_ASSISTANT_MESSAGE,
        payload={"text": "Pierwsza odpowiedź"},
    )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind="STATUS",
        payload={"level": "engine", "wenext_wejscie_do_seq": 1},
    )
    correction = await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "Bez demo. Badamy problem."},
    )
    handler = _handlers(pool, tenant, tmp_path, os.environ["WENEXT_TEST_DSN"], home=None)
    message = await handler._message_for_turn(session)
    assert message == f"[Wiadomość człowieka, seq={correction.seq}]\nBez demo. Badamy problem."
    assert await handler._last_user_seq(session) == correction.seq


async def test_a_correction_reaches_the_agent_on_the_same_engine_thread(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_korekta",
            "turns": [
                {"steps": [{"message": "Pierwsza odpowiedź."}]},
                {"steps": [{"message": "Poprawiam: krócej, bez ogólników."}]},
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    assert (await handlers.run_once(session.id)).outcome == OUTCOME_ANSWERED
    after_first = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert after_first is not None and after_first.engine_thread_id == "th_korekta"

    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "za długie, skróć i podaj źródło"},
    )
    assert (await handlers.run_once(session.id)).outcome == OUTCOME_ANSWERED

    fresh = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None
    assert fresh.engine_thread_id == "th_korekta", (
        "druga tura założyła nowy wątek silnika zamiast wznowić stary; model zaczyna od zera"
    )
    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    answers = [e.payload["text"] for e in events if e.kind == EVENT_ASSISTANT_MESSAGE]
    assert len(answers) == 2 and "Poprawiam" in answers[-1]


async def test_every_session_thread_names_the_locked_default_model(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """27.09: a session named no model, the engine chose `gpt-5.6-sol`, and the server began
    refusing it for a ChatGPT account, so every NEW session failed before the model ran. The
    thread is now started AND resumed with the default route of `versions.lock.toml`."""
    from control_plane.sessions.handlers import SESSION_MODEL

    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_model",
            "turns": [
                {"steps": [{"message": "Pierwsza odpowiedź."}]},
                {"steps": [{"message": "Druga odpowiedź."}]},
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    assert (await handlers.run_once(session.id)).outcome == OUTCOME_ANSWERED
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "dalej"},
    )
    assert (await handlers.run_once(session.id)).outcome == OUTCOME_ANSWERED

    asked = Path(f"{script}.state").with_suffix(".model").read_text(encoding="utf-8").split("\n")
    assert SESSION_MODEL == "gpt-5.6-terra"
    assert [line for line in asked if line] == [
        f"start {SESSION_MODEL}",
        f"resume {SESSION_MODEL}",
    ]


async def test_a_session_with_no_connection_waits_with_its_goal_and_invents_nothing(
    pool, tenant, tmp_path, dsn
) -> None:
    """Brak silnika to CZEKANIE, nie koniec rozmowy (E1, audyt UX 20.09).

    Do 20.09 ta sama sytuacja kończyła turę zdaniem „podłącz połączenie i napisz ponownie", czyli
    kazała człowiekowi napisać cel drugi raz. Cel, który już napisał, zostawał w śladzie bez
    skutku. Test pilnuje trzech rzeczy naraz: rozmowa czeka, karta mówi CO zrobić, i nic nie
    udaje odpowiedzi modelu, którego tu nie było.
    """
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore
    from control_plane.sessions.store import STATUS_WAITING_EVENT
    from control_plane.tools.cards import CARD_ENGINE, ENGINE_DISCONNECTED

    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={},
    )
    handlers = _handlers(
        pool,
        tenant,
        tmp_path,
        dsn,
        home=None,
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
    )

    result = await handlers.run_once(session.id)

    assert result.outcome == OUTCOME_NO_CONNECTION
    fresh = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None and fresh.status == STATUS_WAITING_EVENT
    assert "cel jest zapisany" in (fresh.result_summary or "")
    assert fresh.goal == GOAL, "cel człowieka ma przeżyć brak silnika nietknięty"
    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    assert not [e for e in events if e.kind == EVENT_ASSISTANT_MESSAGE], (
        "sesja bez silnika wyprodukowała odpowiedź, czyli odpowiedź bez modelu"
    )
    cards = [
        card
        for event in events
        for card in (event.payload.get("karty") or [])
        if card.get("rodzaj") == CARD_ENGINE
    ]
    assert len(cards) == 1, cards
    assert cards[0]["stan"] == ENGINE_DISCONNECTED
    assert cards[0]["akcje"][0]["nazwa"] == "polacz", cards[0]
    async with pool.acquire() as conn:
        waiting = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'session.turn' "
            "AND status = 'READY'",
            tenant["organization"],
        )
    assert waiting == 1, "nikt nie wróci do tej rozmowy, jeśli nic na nią nie czeka"


# ---------------------------------------------------------------------------
# 3. A restart in the middle of a turn
# ---------------------------------------------------------------------------


async def test_a_restart_mid_turn_keeps_the_session_and_does_not_repeat_the_write(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """THE test of this file.

    The first turn writes a memory entry and then hangs, and the worker is killed mid-turn (the
    task is cancelled, which is what a dead worker looks like from the session's side). The
    replacement turn runs the same script again, exactly as a resumed session would. The turn IS
    repeated, on purpose; the effect must not be.

    GRANICA GWARANCJI, NAZWANA WPROST (E8, decyzja koordynatora 21.09): to samo żądanie zapisu,
    zadane dwa razy w jednej rozmowie, wykonuje się raz, O ILE PIERWSZY WYNIK ZDĄŻYŁ TRAFIĆ DO
    ŚLADU. `_already_done` rozpoznaje powtórkę po PARZE zdarzeń, wywołaniu i wyniku, więc worker
    zabity MIĘDZY nimi zostawia powtórkę nierozpoznawalną. Dlatego ten test czeka na WYNIK, a nie
    na samo wywołanie: mierzy gwarancję, którą produkt daje, a nie tę, której nie daje.

    Co się dzieje w tej luce: skutek w pamięci i tak jest jeden (0027), znika tylko ZNACZNIK, że
    to było powtórzenie, więc człowiek czytający rozmowę zobaczy dwa zapisy. Rozpoznawanie samego
    wywołania byłoby zmianą PRODUKTU, nie testu, i osobną decyzją: wywołanie bez wyniku znaczy też
    „próba, która mogła się nie udać".
    """
    store = SessionStore(pool)
    project_id = await _project(pool, tenant)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    remember = {
        "tool": "memory.write",
        "arguments": {
            "kind": "INTERPRETATION",
            "statement": "odpowiadamy w ciągu godziny od publikacji",
            "source_span": "rozmowa",
        },
    }
    script = _script(
        tmp_path,
        {
            "thread_id": "th_restart",
            "default_turn": {
                "steps": [remember, {"delay_ms": 4000}, {"message": "skończone"}],
                "status": "completed",
            },
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    # The worker dies while the turn is in its delay, after the write has happened.
    turn = asyncio.create_task(handlers.run_once(session.id))

    async def wrote() -> list:
        """WYNIK, nie samo wywołanie: od niego zaczyna się gwarancja opisana w docstringu."""
        return [
            e
            for e in await store.events(
                organization_id=tenant["organization"], session_id=session.id
            )
            if e.kind == EVENT_TOOL_RESULT
        ]

    await _until(wrote, what="wynik zapisu do pamięci w pierwszej turze")
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    mid = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert mid is not None and mid.status == "RUNNING", (
        "po zabiciu workera sesja nie została w stanie, z którego widać, że tura trwała"
    )
    history_before = await store.events(
        organization_id=tenant["organization"], session_id=session.id
    )
    assert [e.kind for e in history_before].count(EVENT_TOOL_CALL) == 1

    async with pool.acquire() as conn:
        rows_before = await conn.fetchval(
            "SELECT count(*) FROM knowledge_items WHERE project_id = $1", project_id
        )
    assert rows_before == 1, "pierwszy zapis nie doszedł, więc test nie mierzy powtórzenia"

    # The replacement worker. A session stuck in RUNNING is released by the reaper in
    # production; here it is released directly, because what is being measured is the effect,
    # not the reaper (`test_reaper_single_transaction.py` measures that).
    await store.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_WAITING_USER
    )
    second = await handlers.run_once(session.id)
    assert second.outcome == OUTCOME_ANSWERED, second.detail

    async with pool.acquire() as conn:
        rows_after = await conn.fetchval(
            "SELECT count(*) FROM knowledge_items WHERE project_id = $1", project_id
        )
    assert rows_after == 1, (
        f"po restarcie w pamięci projektu są {rows_after} wiersze zamiast jednego. Tura powtarza "
        "się celowo; skutek powtarzać się nie może"
    )

    # And the history the screen shows is the SAME history, with the restart visible in it
    # rather than hidden: the events from before the kill are still there, in order.
    history_after = await store.events(
        organization_id=tenant["organization"], session_id=session.id
    )
    assert [e.seq for e in history_after][: len(history_before)] == [
        e.seq for e in history_before
    ], "ślad sprzed restartu zmienił się po restarcie"
    assert len(history_after) > len(history_before)
    repeated = [
        e
        for e in history_after
        if e.kind == "TOOL_RESULT" and "powtorzone" in json.dumps(e.payload, ensure_ascii=False)
    ]
    assert repeated, (
        "powtórzone wywołanie zapisu nie zostawiło śladu, że było powtórzeniem. Człowiek "
        "czytający rozmowę zobaczy dwa zapisy i nie dowie się, że powstał jeden wiersz"
    )


async def test_two_jobs_racing_on_one_session_run_one_turn(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Two turns on one engine thread is the P2 shape of publishing the same comment twice."""
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_minutes": 1},
    )
    script = _script(
        tmp_path,
        {"default_turn": {"steps": [{"delay_ms": 800}, {"message": "jedna odpowiedź"}]}},
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    first, second = await asyncio.gather(
        handlers.run_once(session.id), handlers.run_once(session.id)
    )
    outcomes = sorted([first.outcome, second.outcome])
    assert outcomes == sorted([OUTCOME_ANSWERED, OUTCOME_NOT_CLAIMED]), outcomes

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    answers = [e for e in events if e.kind == EVENT_ASSISTANT_MESSAGE]
    assert len(answers) == 1, f"dwie tury odpowiedziały na jednym wątku: {len(answers)}"


# ---------------------------------------------------------------------------
# 4. What the screen reads back
# ---------------------------------------------------------------------------


async def test_the_history_reads_back_by_sequence_number_after_a_restart(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """What the screen does: ask for everything after the last number it has. A process that
    restarted has no memory of that number, and neither does the screen; both read it from the
    trace."""
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_minutes": 1},
    )
    script = _script(
        tmp_path,
        {
            "turns": [
                {"steps": [{"tool": "catalog.list", "arguments": {}}, {"message": "pierwsza"}]},
                {"steps": [{"message": "druga"}]},
            ]
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    await handlers.run_once(session.id)
    seen = await store.last_seq(organization_id=tenant["organization"], session_id=session.id)

    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "jeszcze raz, krócej"},
    )
    await handlers.run_once(session.id)

    # A fresh store, standing in for a process that has just started with no memory at all.
    reopened = SessionStore(pool)
    catch_up = await reopened.events(
        organization_id=tenant["organization"], session_id=session.id, after_seq=seen
    )
    assert catch_up, "po restarcie ekran nie doczytał niczego, choć rozmowa poszła dalej"
    assert [e.seq for e in catch_up] == list(range(seen + 1, seen + 1 + len(catch_up)))
    everything = await reopened.events(
        organization_id=tenant["organization"], session_id=session.id, limit=1000
    )
    assert [e.seq for e in everything] == list(range(1, len(everything) + 1)), (
        "numery w odtworzonej historii nie są gęste, czyli ekran po restarcie pokaże dziurę"
    )
    assert [e.payload.get("text") for e in everything if e.kind == EVENT_ASSISTANT_MESSAGE] == [
        "pierwsza",
        "druga",
    ]


async def test_an_interrupted_turn_pauses_the_session_and_says_so(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Interrupt is a human act with a state of its own: PAUSED, resumable, visible in the trace.

    And it arrives THE WAY IT WILL IN PRODUCTION: as an `INTERRUPT` event written by an HTTP
    request in another process. The running turn notices it in its own trace and asks the engine
    to stop. Nothing is passed in memory between the two, because in production there is nothing
    to pass it through.
    """
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_minutes": 1},
    )
    # OKNO TURY DŁUGIE NA TYLE, ŻEBY WYŚCIG NIE ISTNIAŁ (E8). Było 6 sekund i test padał raz na
    # kilka przebiegów przy obciążonym hoście: żeby przerwanie zdążyło, musiał zmieścić się w
    # tym oknie CAŁY łańcuch, czyli start procesu silnika, uzgodnienie, otwarcie wątku, zapisy do
    # bazy, zauważenie zdarzenia przez pętlę tury (co sekundę) i dojście prośby do silnika. Przy
    # trzydziestu sekundach tura nie ma szans skończyć się sama, więc cokolwiek się wydarzy, jest
    # skutkiem PRZERWANIA, a nie tego, kto zdążył pierwszy. Test nie trwa przez to dłużej:
    # przerwanie zwykle dochodzi w sekundę i tura kończy się wtedy, a nie po trzydziestu.
    # Asercji nie rozluźniam: dalej wymagam stanu PAUSED i braku odpowiedzi w śladzie.
    script = _script(
        tmp_path,
        {"default_turn": {"steps": [{"delay_ms": 30000}, {"message": "nie dojdzie"}]}},
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    async def press_interrupt() -> None:
        # Wait until the turn is really running, then do exactly what the API route does.
        async def started() -> bool:
            current = await store.get(organization_id=tenant["organization"], session_id=session.id)
            return current is not None and bool(current.engine_thread_id)

        await _until(started, what="wątek silnika w bazie, czyli ruszająca tura")
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind="INTERRUPT",
            payload={"detail": "człowiek nacisnął przerwij"},
        )

    turn = asyncio.create_task(handlers.run_once(session.id))
    await press_interrupt()
    result = await turn

    fresh = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert fresh is not None
    assert fresh.status == STATUS_PAUSED, (
        f"po przerwaniu sesja jest w stanie {fresh.status}, a człowiek nacisnął „przerwij”, "
        f"nie „zepsuj” (wynik tury: {result.outcome})"
    )
    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    assert [e for e in events if e.kind == "INTERRUPT"], "przerwanie nie zostawiło śladu"
    assert not [e for e in events if e.kind == EVENT_ASSISTANT_MESSAGE], (
        "przerwana tura mimo to dopisała odpowiedź, czyli przerwanie nic nie zmieniło"
    )


async def test_a_turn_survives_a_server_request_instead_of_hanging(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """AWARIA LIVE Z 19.09, ODTWORZONA OD KOŃCA DO KOŃCA.

    O 18:38:46 UTC app-server przysłał `mcpServer/elicitation/request`, klient zapisał to jako
    zwykłe zdarzenie i nie odpowiedział, a tura wisiała w RUNNING ponad siedem minut. Atrapa
    robi tu dokładnie to samo i BLOKUJE na odpowiedzi: klient, który jej nie wyśle, zawiesi turę,
    a ten test padnie na timeout, zamiast przejść przez przypadek.

    Sprawdzane są trzy rzeczy naraz: tura się KOŃCZY, żądanie trafia do ŚLADU razem z naszą
    decyzją (bo odmowa, o której nikt się nie dowiedział, jest nie do odróżnienia od awarii), i
    krok po żądaniu wykonuje się dalej, czyli sesja nie została po cichu okaleczona.
    """
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script = _script(
        tmp_path,
        {
            "turns": [
                {
                    "steps": [
                        {"message": "Zaczynam od katalogu narzędzi."},
                        {"elicit": {"server": "wenext", "message": "czy mogę zawołać narzędzie?"}},
                        {"tool": "catalog.list", "arguments": {}},
                        {"message": "Skończone mimo pytania z serwera."},
                    ],
                    "status": "completed",
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    result = await handlers.run_once(session.id)

    assert result.outcome == OUTCOME_ANSWERED, result.detail

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    # Żądanie serwera jest w śladzie RAZEM Z DECYZJĄ: odmowa albo zgoda, o której nikt się nie
    # dowiedział, jest nie do odróżnienia od awarii.
    requests = [
        e for e in events if isinstance(e.payload, dict) and e.payload.get("wenext_zadanie_serwera")
    ]
    assert requests, [e.kind for e in events]
    assert requests[0].payload["wenext_decyzja"] == "przyjete"
    assert requests[0].payload["wenext_serwer"] == "wenext"

    # I krok PO żądaniu wykonał się normalnie: sesja nie została okaleczona po drodze.
    tool_calls = [e.payload["tool"] for e in events if e.kind == EVENT_TOOL_CALL]
    assert "catalog.list" in tool_calls, tool_calls


async def test_a_request_from_a_foreign_server_is_refused_and_written_down(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Zgoda, o której nikt nie decydował, jest warta mniej niż odmowa. Odmowa ZAPISANA jest
    czymś, na co człowiek może zareagować, więc tura kończy się normalnie, a w śladzie zostaje
    nazwa serwera i powód."""
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script = _script(
        tmp_path,
        {
            "turns": [
                {
                    "steps": [
                        {"elicit": {"server": "ktos-obcy", "message": "wpuść mnie"}},
                        {"message": "Odmówiono, kończę."},
                    ],
                    "status": "completed",
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    result = await handlers.run_once(session.id)
    assert result.outcome == OUTCOME_ANSWERED, result.detail

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    requests = [
        e for e in events if isinstance(e.payload, dict) and e.payload.get("wenext_zadanie_serwera")
    ]
    assert requests, [e.kind for e in events]
    assert requests[0].payload["wenext_decyzja"] == "odmowa"
    assert requests[0].payload["wenext_serwer"] == "ktos-obcy"
    assert "nikt w tej sesji nie decydował" in requests[0].payload["wenext_powod"]


# ---------------------------------------------------------------------------
# 6. Dopowiedzenie w trakcie tury i profil runtime rozmowy (D-046)
# ---------------------------------------------------------------------------


async def test_a_sentence_written_during_a_turn_reaches_that_turn(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """D-046: człowiek dopowiada W TRAKCIE pracy, a nie po niej.

    DLACZEGO TO NIE DZIAŁAŁO SAMO. Job zamówiony przez `/message` trafia na `claim_for_turn`,
    które odmawia, bo tura już trwa, i kończy się jako NIEZAJĘTY; nikt go nie ponawia. Zdanie
    czekało więc na cokolwiek, co uruchomi następną turę. Teraz idzie do trwającej tury przez
    `turn/steer`, a znacznik zużycia zapisujemy DOPIERO po tym, jak silnik je przyjął.
    """
    import asyncio

    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal=GOAL,
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script_path = _script(
        tmp_path,
        {
            "thread_id": "th_steer",
            "turns": [
                {
                    "steps": [{"delay_ms": 6000}, {"message": "Zrobione."}],
                    "status": "completed",
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script_path))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    running = asyncio.create_task(handlers.run_once(session.id))
    # The sentence must arrive DURING the turn. A fixed 1.5 s sleep raced the fake engine's own
    # start under load (27.09, full suite beside an image build): the sentence landed before the
    # turn and went in with its input, never through `turn/steer`. The fake writes its turn
    # counter when the turn begins, so that file is the moment to wait for.
    started = Path(f"{script_path}.state")
    for _ in range(300):
        if started.exists():
            break
        await asyncio.sleep(0.1)
    assert started.exists(), "atrapa silnika nie zaczęła tury w 30 s"
    await asyncio.sleep(0.3)
    said = await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "zmień kierunek, zacznij od r/SaaS"},
    )
    result = await running

    assert result.outcome == OUTCOME_ANSWERED, result
    steered = Path(f"{script_path}.steer")
    assert steered.exists(), "atrapa nie dostała dopowiedzenia"
    steered_text = steered.read_text(encoding="utf-8")
    assert "zmień kierunek, zacznij od r/SaaS" in steered_text
    assert steered_text.count("zmień kierunek, zacznij od r/SaaS") == 1, (
        "jedna dopisana wiadomość nie może być ponawiana co sekundę przez całą turę"
    )

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    consumed = [
        event.payload.get("wenext_wejscie_do_seq")
        for event in events
        if event.kind == "STATUS" and "wenext_wejscie_do_seq" in (event.payload or {})
    ]
    assert said.seq in consumed, (
        "zdanie weszło do tury, ale nie zostało oznaczone jako zużyte: następna tura wysłałaby "
        f"je drugi raz. Znaczniki: {consumed}, szukane seq: {said.seq}"
    )


async def test_a_conversation_runs_with_web_search_and_the_pilot_does_not(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Profil runtime dociera do SILNIKA, i po E9 (21.09) dociera tam ARGUMENTAMI PROCESU.

    Atrapa odpowiada na `config/read` konfiguracją efektywną, czyli plikiem domu z nałożonymi
    `--config`, dokładnie tak jak prawdziwa binarka (zmierzone na 0.147.0). Ten test mierzy więc
    całą drogę: profil sesji, generator argumentów, start procesu, odczyt. Gdyby po drodze
    cokolwiek gubiło wartość, sesja zapisałaby w śladzie niezgodność, a nie milczenie.
    """
    from control_plane.sessions.store import SESSION_PRESENCE

    store = SessionStore(pool)
    project = await _project(pool, tenant)
    script_path = _script(
        tmp_path,
        {
            "thread_id": "th_profile",
            "default_turn": {"steps": [{"message": "Gotowe."}], "status": "completed"},
            "turns": [],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script_path))
    # A PRESENCE session runs only under a live delegation (26.09: `run_once` checks it before
    # the engine starts), so the project gets the smallest one: research, no accounts.
    from control_plane.presence.scope import parse_budget, parse_scope
    from control_plane.presence.store import PresenceStore

    await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project,
        mode="RESEARCH",
        scope=parse_scope({"accounts": [], "communities": [], "actions": ["RESEARCH"]}),
        budget=parse_budget({}),
        operator_id=tenant["operator"],
    )

    for kind, expected in ((None, "live"), (SESSION_PRESENCE, "disabled")):
        home = tmp_path / f"home-{expected}"
        session = await store.create(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project,
            goal=GOAL,
            budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
            **({"kind": kind} if kind else {}),
        )
        handlers = _handlers(pool, tenant, tmp_path, dsn, home=home)
        result = await handlers.run_once(session.id)
        assert result.outcome == OUTCOME_ANSWERED, (kind, result)

        # DOM ZOSTAJE BEZ WPISU SERWERA NARZĘDZI, i to jest cała poprawka E9: plik jest wspólny
        # dla rozmów i pilota, a wpis niesie ścieżkę do zdolności JEDNEJ tury. Brak pliku też
        # jest brakiem wpisu, bo tura niczego tam nie zapisuje.
        config_file = home / "config.toml"
        written = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
        assert "[mcp_servers.wenext]" not in written, (
            "tura zapisała wpis serwera narzędzi do wspólnego domu; po E9 ma go podawać "
            f"w argumentach procesu. {written[:200]}"
        )

        events = await store.events(organization_id=tenant["organization"], session_id=session.id)
        broken = [
            event
            for event in events
            if event.kind == "STATUS" and "niezgodnosci" in (event.payload or {})
        ]
        assert broken == [], [event.payload for event in broken]


async def test_the_brief_scenario_runs_end_to_end_on_the_fake_engine(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Scenariusz z briefu D-046, od prośby człowieka do kart w rozmowie.

    CO TEN TEST DOWODZI, czego nie dowodzą testy pojedynczych narzędzi: że karty powstają PO
    DRODZE PRZEZ SILNIK. Narzędzie wywołane wprost z testu zwraca słownik; tutaj wynik przechodzi
    przez serwer MCP, ślad sesji i projekcję, czyli przez wszystkie miejsca, w których karta może
    zginąć albo przyjść przycięta.

    Drugi krok jest ważniejszy od pierwszego: po zalogowaniu konta W BAZIE, bez ani jednego
    nowego zdarzenia w rozmowie, karta konta ma zmienić stan. Tak wygląda człowiek, który
    kończy logowanie w drugiej zakładce i wraca do rozmowy.
    """
    from control_plane.sessions.projection import cards_for, progress_for
    from control_plane.tools.cards import (
        ACCOUNT_CONNECTED,
        ACCOUNT_DISCONNECTED,
        COMMUNITY_OBSERVING,
        fingerprint,
    )

    project_id = await _project(pool, tenant)
    login = f"anna-{uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'PROVISIONED') RETURNING id",
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

    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal=(
            "Sprawdź, gdzie warto promować ten projekt. Wykorzystaj konto Anny i zacznij "
            "od sensownych rozmów."
        ),
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 30},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_brief",
            "turns": [
                {
                    "steps": [
                        {"tool": "catalog.list", "arguments": {}},
                        {"tool": "accounts.status", "arguments": {}},
                        {
                            "tool": "communities.observe",
                            "arguments": {
                                "community": "r/SaaS",
                                "uzasadnienie": "rozmowy o produkcie i pierwszych klientach",
                            },
                        },
                        {
                            "tool": "communities.observe",
                            "arguments": {
                                "community": "r/startups",
                                "uzasadnienie": "doświadczenia osób budujących firmy",
                            },
                        },
                        {
                            "message": (
                                "Zacząłbym tutaj. Podepnij Reddita, żebym mógł też dołączyć "
                                "do rozmów."
                            )
                        },
                    ],
                    "status": "completed",
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")

    result = await handlers.run_once(session.id)
    assert result.outcome == OUTCOME_ANSWERED, result

    events = list(await store.events(organization_id=tenant["organization"], session_id=session.id))
    cards = await cards_for(
        pool,
        events,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    by_kind = {}
    for card in cards:
        by_kind.setdefault(card["rodzaj"], []).append(card)

    assert len(by_kind.get("SPOLECZNOSC", [])) == 2, cards
    assert {one["klucz"] for one in by_kind["SPOLECZNOSC"]} == {"r/SaaS", "r/startups"}
    assert {one["stan"] for one in by_kind["SPOLECZNOSC"]} == {COMMUNITY_OBSERVING}, (
        "obserwacja jest włączona, a pierwszy odczyt dopiero stoi w kolejce"
    )
    assert all(one["obserwacja"] is True for one in by_kind["SPOLECZNOSC"]), cards
    assert all(one["akcje"] == [] for one in by_kind["SPOLECZNOSC"]), (
        "„Dołącz kontem” nie ma trasy, więc karta nie pokazuje przycisku"
    )
    assert by_kind["SPOLECZNOSC"][0]["uzasadnienie"], "zdanie agenta przeżyło drogę przez silnik"

    account = by_kind["KONTO"][0]
    assert account["konto"] == login
    assert account["stan"] == ACCOUNT_DISCONNECTED
    assert account["akcje"][0]["nazwa"] == "polacz"

    progress = progress_for(events)
    assert progress is not None and progress["licznik"]["wszystkie"] >= 1, progress

    before = fingerprint(cards)

    # CZŁOWIEK LOGUJE SIĘ W DRUGIEJ ZAKŁADCE. W rozmowie nie dzieje się NIC.
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = 'READY' WHERE id = $1", account_id)
    again = await cards_for(
        pool,
        events,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )
    account_now = next(one for one in again if one["rodzaj"] == "KONTO")
    assert account_now["stan"] == ACCOUNT_CONNECTED
    assert account_now["akcje"] == []
    assert fingerprint(again) != before, "bez zmiany odcisku ekran nigdy by się nie przeładował"
