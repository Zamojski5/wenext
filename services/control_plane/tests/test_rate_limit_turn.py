"""Tura, która trafiła na limit konta Codex, CZEKA i wraca sama (zgłoszenie założyciela 03:24).

Mierzone na atrapie app-servera, bo pytanie nie brzmi „czy Codex naprawdę ma limit", tylko „co
robi nasz produkt, gdy silnik odpowie takim zdaniem". Cztery rzeczy, każda inaczej kosztowna:
sesja nie umiera, człowiek dostaje zdanie z godziną, surowy komunikat zostaje w śladzie, a
wznowienie jest zaplanowane raz, nie dwa razy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.sessions.handlers import OUTCOME_WAITING_LIMIT, REASON_ACCOUNT_LIMIT
from control_plane.sessions.store import EVENT_STATUS, SessionStore
from test_session_turn_e2e import _handlers, _project, _script

pytestmark = [pytest.mark.db, pytest.mark.p2]


@pytest.fixture
def dsn() -> str:
    """Ta sama fikstura, co w `test_session_turn_e2e.py`: fikstur nie dziedziczy się przez
    import, a ten plik używa tej samej atrapy silnika."""
    import os

    value = os.environ.get("WENEXT_TEST_DSN")
    if not value:
        pytest.fail("brak WENEXT_TEST_DSN: ten test wymaga bazy")
    return value


LIMIT_TEXT = "You've hit your usage limit. Upgrade to Pro (…) or try again at 3:23 AM."


class RecordingQueue:
    """Kolejka, która tylko zapisuje, o co ją poproszono. Wznowienie to fakt do sprawdzenia.

    Podpis `create_job` przepisany z `JobQueuePort`, nie wymyślony: atrapa o innym podpisie
    przyjęłaby wywołanie, którego prawdziwa kolejka by odrzuciła, i test zieleniłby się na
    kodzie, który w produkcji nie działa.
    """

    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def create_job(self, **fields):
        self.asked.append(dict(fields))
        from control_plane.domain.models import EnqueuedJob

        return EnqueuedJob(job_id=uuid4(), created=True)


async def _session_with_limit(pool, tenant, tmp_path, dsn, monkeypatch, *, queue):
    """Sesja, której atrapa silnika odpowiada komunikatem o limicie jako TREŚCIĄ tury."""
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await _project(pool, tenant),
        goal="Sprawdź, co słychać w społeczności",
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_limit",
            "default_turn": {"steps": [{"message": LIMIT_TEXT}], "status": "completed"},
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn, home=tmp_path / "home")
    handlers._queue = queue  # kolejka atrapowa: wznowienie ma być faktem, nie deklaracją
    return session, handlers, store


async def test_a_usage_limit_puts_the_session_on_hold_instead_of_killing_it(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    queue = RecordingQueue()
    session, handlers, store = await _session_with_limit(
        pool, tenant, tmp_path, dsn, monkeypatch, queue=queue
    )

    result = await handlers.run_once(session.id)

    assert result.outcome == OUTCOME_WAITING_LIMIT, result.detail
    after = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert after is not None
    assert after.status == "WAITING_EVENT", "limit konta to czekanie, nie koniec pracy"
    assert "Konto Codex doszło do limitu" in (after.result_summary or "")
    assert "Możesz zamknąć okno" in (after.result_summary or "")
    # I ani słowa o kupowaniu czegokolwiek w tym, co widzi człowiek.
    assert "Upgrade" not in (after.result_summary or "")


async def test_the_raw_message_survives_in_the_trace(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Zdanie po polsku jest NASZYM streszczeniem. To, co naprawdę powiedziała platforma, musi
    dać się przeczytać, inaczej po tygodniu nikt nie odtworzy, czy limit był konta, czy modelu."""
    queue = RecordingQueue()
    session, handlers, store = await _session_with_limit(
        pool, tenant, tmp_path, dsn, monkeypatch, queue=queue
    )

    await handlers.run_once(session.id)

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    waiting = [
        one
        for one in events
        if one.kind == EVENT_STATUS and one.payload.get("powod") == REASON_ACCOUNT_LIMIT
    ]
    assert len(waiting) == 1, [one.payload for one in events]
    payload = waiting[0].payload
    assert "usage limit" in payload["surowy_komunikat"]
    assert payload["odnowi_sie"], "godzina odnowienia jest w śladzie, nie tylko w zdaniu"
    assert payload["skad"] in ("engine", "message", "blind")


async def test_the_turn_is_scheduled_to_come_back_once(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    queue = RecordingQueue()
    session, handlers, _ = await _session_with_limit(
        pool, tenant, tmp_path, dsn, monkeypatch, queue=queue
    )

    await handlers.run_once(session.id)

    turns = [one for one in queue.asked if one["job_type"] == "session.turn"]
    assert len(turns) == 1, queue.asked
    assert turns[0]["delay_seconds"] >= 60, "wznowienie nie jest natychmiastowe"
    # Klucz niesie godzinę odnowienia, więc dwa wykrycia tego samego limitu dadzą jeden job.
    assert "limit-" in turns[0]["idempotency_key"]


async def test_the_hour_comes_from_the_message_when_the_engine_says_nothing(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Atrapa nie umie `account/rateLimits/read`, więc odczyt padnie i zostanie godzina z
    komunikatu. To jest ścieżka, którą zobaczył założyciel: „try again at 3:23 AM"."""
    queue = RecordingQueue()
    session, handlers, store = await _session_with_limit(
        pool, tenant, tmp_path, dsn, monkeypatch, queue=queue
    )

    await handlers.run_once(session.id)

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    payload = next(
        one.payload
        for one in events
        if one.kind == EVENT_STATUS and one.payload.get("powod") == REASON_ACCOUNT_LIMIT
    )
    assert payload["skad"] == "message", payload
    when = datetime.fromisoformat(payload["odnowi_sie"])
    assert when > datetime.now(UTC), "godzina odnowienia jest w przyszłości"
    assert when.astimezone().strftime("%H:%M") == "03:23"


async def test_a_month_long_limit_says_its_date_and_checks_again_within_three_hours(
    pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """28-29.09 na żywo: plan darmowy, okno 30 dni wyczerpane. Silnik znał datę odnowienia, a
    ekran pisał „nie wiem”. Teraz zdanie niesie datę i dostępny reset, ślad ma `limit_do`, a
    powrót jest za trzy godziny, bo reset albo zmiana planu mają zdjąć czekanie od razu."""
    from datetime import timedelta

    from control_plane.sessions.rate_limit import LONG_WAIT_CHECK
    from test_rate_limit_reading import FREE_SHAPE

    class FreePlanEngine:
        async def read_rate_limits(self):
            return FREE_SHAPE

    queue = RecordingQueue()
    session, handlers, store = await _session_with_limit(
        pool, tenant, tmp_path, dsn, monkeypatch, queue=queue
    )
    row = await store.get(organization_id=tenant["organization"], session_id=session.id)
    moment = datetime(2026, 9, 29, 20, 30, tzinfo=UTC)
    handlers._clock = lambda: moment

    result = await handlers._rate_limited(
        row,
        "You've hit your usage limit. Upgrade to Plus (…) or try again at Oct 27th.",
        client=FreePlanEngine(),
    )

    assert result is not None and result.outcome == OUTCOME_WAITING_LIMIT
    resets = datetime.fromtimestamp(FREE_SHAPE["rateLimits"]["primary"]["resetsAt"], tz=UTC)
    assert resets.astimezone().strftime("%d.%m o %H:%M") in result.detail
    assert "darmowy reset limitu" in result.detail and "Upgrade" not in result.detail
    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    [payload] = [
        one.payload
        for one in events
        if one.kind == EVENT_STATUS and one.payload.get("powod") == REASON_ACCOUNT_LIMIT
    ]
    assert payload["skad"] == "engine"
    assert datetime.fromisoformat(payload["limit_do"]) == resets
    assert datetime.fromisoformat(payload["odnowi_sie"]) == moment + LONG_WAIT_CHECK
    assert (payload["plan"], payload["resety_dostepne"]) == ("free", 1)
    [turn] = [one for one in queue.asked if one["job_type"] == "session.turn"]
    assert timedelta(seconds=turn["delay_seconds"]) <= LONG_WAIT_CHECK + timedelta(minutes=2), (
        "powrót najpóźniej za trzy godziny, nie 27.10"
    )
