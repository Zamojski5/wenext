"""Ślad tury ma być ZAPISEM ROZMOWY, nie taśmą telegraficzną silnika.

UWAGA ZAŁOŻYCIELA Z 19.09, po pierwszym prawdziwym użyciu studia: historia rozmowy to dziesiątki
wierszy „zdarzenie silnika: item/agentMessage/delta", a zdanie, które model naprawdę napisał,
leży na końcu tej listy. Zmierzone na p2-live-check: 333 ramki silnika na jedną turę.

CO JEST MIERZONE TUTAJ, bez bazy i bez silnika: strumień ramek wpuszczony do rejestratora zdarzeń
i policzone, co z niego zostaje. Trzy rzeczy naraz, bo każda osobno dałaby się spełnić źle:

  * ROZMOWA JEST KRÓTKA: z 333 ramek zostaje kilkanaście wierszy, a nie 333;
  * NIC NIE ZNIKA BEZ ŚLADU: pominięte ramki wracają jako JEDNA linia z liczbą;
  * ODPOWIEDŹ JEST CAŁA: tekst nie jest sklejany z delt, tylko brany z `item/completed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_plane.sessions.handlers import (
    ENGINE_NOISE,
    LEVEL_ENGINE,
    SessionTurnHandlers,
)
from control_plane.sessions.store import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_STATUS,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
)

ORGANIZATION = uuid4()


@dataclass
class Recorded:
    kind: str
    payload: dict[str, Any]


class FakeSessions:
    """Tyle `SessionStore`, ile dotyka rejestrator zdarzeń: jedna metoda, zbierająca do listy."""

    def __init__(self) -> None:
        self.events: list[Recorded] = []

    async def append_event(
        self, *, organization_id: UUID, session_id: UUID, kind: str, payload: dict[str, Any]
    ) -> None:
        self.events.append(Recorded(kind, dict(payload)))


@dataclass
class FakeSession:
    id: UUID


def _handlers(sessions: FakeSessions) -> SessionTurnHandlers:
    return SessionTurnHandlers(
        pool=None,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        organization_id=ORGANIZATION,
        dsn="postgresql://atrapa/nieuzywane",
    )


def _live_stream() -> list[tuple[str, dict[str, Any]]]:
    """Ruch jednej tury w kształcie zmierzonym na p2-live-check: 333 ramki.

    Proporcje z prawdziwego przebiegu: przytłaczającą większość stanowią delty odpowiedzi,
    reszta to liczniki, dwa wywołania narzędzi i jedna odpowiedź.
    """
    frames: list[tuple[str, dict[str, Any]]] = [("thread/started", {})]
    frames += [("item/agentMessage/delta", {"delta": "kawałek "}) for _ in range(300)]
    frames += [("thread/tokenUsage/updated", {"total": 100 + i}) for i in range(20)]
    frames += [("account/rateLimits/updated", {"left": 5}) for _ in range(5)]
    frames.append(
        (
            "item/completed",
            {
                "item": {
                    "type": "mcpToolCall",
                    "tool": "thread.read",
                    "server": "wenext",
                    "arguments": {"external_id": "t3_1w934kh"},
                    "status": "completed",
                    "durationMs": 1200,
                    "result": {"ok": True},
                }
            },
        )
    )
    frames.append(("item/mcpToolCall/progress", {"message": "czytam wątek"}))
    frames.append(
        (
            "item/completed",
            {
                "item": {
                    "type": "agentMessage",
                    "id": "msg_1",
                    "text": "**Trzy rzeczy**, po kolei, bez morałów.",
                }
            },
        )
    )
    frames.append(("turn/completed", {}))
    frames.append(("silnik/metoda-ktorej-nie-znamy", {}))
    return frames


@pytest.mark.asyncio
async def test_three_hundred_engine_frames_leave_a_handful_of_conversation_rows() -> None:
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())
    frames = _live_stream()
    # 331: tyle samo rzędu wielkości, co 333 zmierzone na live. Liczba jest asercją, żeby
    # zmiana strumienia testowego nie przeszła niezauważona razem ze zmianą wyniku.
    assert len(frames) == 331, f"strumień testowy ma {len(frames)} ramek, a miał mieć 331"

    for method, params in frames:
        await handlers._record_notification(session, method, params)  # type: ignore[arg-type]
    await handlers._record_engine_noise(session)  # type: ignore[arg-type]

    conversation = [one for one in sessions.events if one.payload.get("level") != LEVEL_ENGINE]
    assert len(conversation) <= 15, [one.kind for one in conversation]
    assert [one.kind for one in conversation] == [
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        EVENT_STATUS,  # postęp narzędzia, czyli zdanie dla człowieka
        EVENT_ASSISTANT_MESSAGE,
    ], [(one.kind, one.payload.get("detail")) for one in conversation]


@pytest.mark.asyncio
async def test_the_answer_arrives_whole_and_is_never_glued_from_deltas() -> None:
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())

    for method, params in _live_stream():
        await handlers._record_notification(session, method, params)  # type: ignore[arg-type]

    answers = [one for one in sessions.events if one.kind == EVENT_ASSISTANT_MESSAGE]
    assert len(answers) == 1, answers
    assert answers[0].payload["text"] == "**Trzy rzeczy**, po kolei, bez morałów."
    assert answers[0].payload["message_id"] == "msg_1"
    assert "kawałek" not in answers[0].payload["text"]


@pytest.mark.asyncio
async def test_skipped_frames_come_back_as_one_line_with_a_number() -> None:
    """Zdarzenie, które znika bez śladu, jest nieodróżnialne od zdarzenia, którego nie było."""
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())

    for method, params in _live_stream():
        await handlers._record_notification(session, method, params)  # type: ignore[arg-type]
    await handlers._record_engine_noise(session)  # type: ignore[arg-type]

    summaries = [one for one in sessions.events if "wenext_szum" in one.payload]
    assert len(summaries) == 1, summaries
    noise = summaries[0].payload["wenext_szum"]
    assert noise["item/agentMessage/delta"] == 300
    assert noise["thread/tokenUsage/updated"] == 20
    assert noise["account/rateLimits/updated"] == 5
    assert sum(noise.values()) == 325
    assert "325" in summaries[0].payload["detail"]
    assert summaries[0].payload["level"] == LEVEL_ENGINE


@pytest.mark.asyncio
async def test_the_screen_learns_that_the_agent_started_writing_once_not_three_hundred_times() -> (
    None
):
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())

    for method, params in _live_stream():
        await handlers._record_notification(session, method, params)  # type: ignore[arg-type]

    typing = [one for one in sessions.events if one.payload.get("wenext_pisze")]
    assert len(typing) == 1, typing
    assert typing[0].payload["level"] == LEVEL_ENGINE


@pytest.mark.asyncio
async def test_a_method_nobody_knows_is_recorded_rather_than_dropped() -> None:
    """Zmiana protokołu ma być widoczna. Wiersz jest schowany, ale istnieje."""
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())

    await handlers._record_notification(  # type: ignore[arg-type]
        session, "silnik/metoda-ktorej-nie-znamy", {}
    )

    assert len(sessions.events) == 1
    assert sessions.events[0].payload["level"] == LEVEL_ENGINE
    assert "silnik/metoda-ktorej-nie-znamy" in sessions.events[0].payload["detail"]


@pytest.mark.asyncio
async def test_every_step_carries_one_sentence_a_person_can_read() -> None:
    sessions = FakeSessions()
    handlers = _handlers(sessions)
    session = FakeSession(id=uuid4())

    for method, params in _live_stream():
        await handlers._record_notification(session, method, params)  # type: ignore[arg-type]

    calls = [one for one in sessions.events if one.kind == EVENT_TOOL_CALL]
    results = [one for one in sessions.events if one.kind == EVENT_TOOL_RESULT]
    assert calls and results
    assert calls[0].payload["summary"] == "przeczytał wątek: t3_1w934kh"
    assert results[0].payload["summary"] == "thread.read: gotowe, 1.2 s"


def test_the_noise_list_names_only_counters_and_fragments() -> None:
    """Lista jest zamknięta i ma zostać mała: każda pozycja to ramka, która nie mówi nic o tym,
    CO agent zrobił. Metoda niosąca skutek ma być wierszem, a nie liczbą."""
    for method in ENGINE_NOISE:
        assert method.endswith("/delta") or method.endswith("/updated"), method


def test_every_tool_in_the_catalog_has_a_polish_verb() -> None:
    """Człowiek czyta w swojej rozmowie, co agent zrobił, a nie jak nazywa się funkcja.

    Mapa czasowników jest ZAMKNIĘTA i to jest dobre: zmyślony czasownik byłby zdaniem o czymś,
    czego nikt nie sprawdził. Ale narzędzie bez wpisu dostaje „wywołał communities.observe",
    czyli angielską nazwę techniczną w polskim zdaniu (D-010), i nikt tego nie zauważy, dopóki
    nie przeczyta cudzej rozmowy. Ten test pilnuje tego za czytającego.

    Mapa może mieć WIĘCEJ wpisów niż katalog i to nie jest błąd: ślady starych rozmów niosą
    nazwy narzędzi, których już nie ma, a to one są tu tłumaczone.
    """
    from control_plane.sessions.trace_payloads import TOOL_VERBS
    from control_plane.tools.catalog import CATALOG

    missing = sorted({spec.name for spec in CATALOG} - set(TOOL_VERBS))
    assert missing == [], (
        f"narzędzia bez polskiego czasownika: {missing}. Człowiek zobaczy w rozmowie ich "
        "angielskie nazwy zamiast tego, co agent zrobił"
    )
