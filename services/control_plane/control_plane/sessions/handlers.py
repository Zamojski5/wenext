"""One turn of a session, as a job in the existing queue (P2).

WHY A JOB AND NOT A BACKGROUND TASK. Everything P0 measured about the queue is what a session
needs and would otherwise have to grow its own, worse version of: a lease so two workers never
run one turn, a heartbeat so a turn longer than the lease keeps it, a reaper that terminalizes
rather than silently retries, and a recorded state that survives the process. A turn is minutes
long and reaches a model; that is exactly the work the queue was built for.

THE SHAPE OF ONE TURN:

  1. claim the session with a compare-and-set (`claim_for_turn`). `None` means somebody else has
     it or it is finished, and this job stops. That is the single-turn-per-thread guarantee;
  2. count the budget FROM THE TRACE, not from memory, and stop as BUDGET_EXHAUSTED if it is
     spent. A counter in the process resets when the process does;
  3. issue a capability, write it 0600, and add `[mcp_servers.wenext]` to the session's
     `config.toml`. The tool server is started BY CODEX from that file, which is why it can
     reach the database and why its scope is not something the model can argue with;
  4. start or RESUME the engine thread. First turn: `thread/start` without `ephemeral`, and the
     id is stored. Every later turn resumes that id, so the model has its own history instead of
     a prompt we rebuilt;
  5. send what the person said and stream every notification into `session_events` as it
     arrives. The trace is written DURING the turn, because that trace is the screen and it is
     what a restart recovers from;
  6. finish: an answer with references and WAITING_USER, or FAILED with the reason, or PAUSED
     after an interrupt. Each of those is a state a person can read.

WHAT A RESTART MUST NOT DO, and how this file stops it. A worker killed mid-turn leaves a
session RUNNING with a trace up to the moment it died. The replacement turn resumes the SAME
engine thread and re-sends the same message, and the model may well call the same tools again.
Read-only tools repeating is free. The three tools that write are not, so `ToolRegistry` refuses
to perform an effect whose identical call is already in this session's trace and returns what it
returned the first time. The guarantee is therefore not "the turn is never repeated" (it is)
but "the same write, asked for twice in one session, happens once".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
from community_os_contracts import ThreadRole

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.observe.turns import DEFAULT_ROUTES
from control_plane.tools.cards import ENGINE_CONNECTING

from .context import BASE_INSTRUCTIONS, starting_context
from .runtime_profile import WEB_SEARCH_OFF, RuntimeProfile, hardening_is_broken, profile_for
from .store import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_ERROR,
    EVENT_INTERRUPT,
    EVENT_STATUS,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_USER_MESSAGE,
    SESSION_PRESENCE,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_WAITING_EVENT,
    STATUS_WAITING_USER,
    SessionRow,
    SessionStore,
    host_of,
)
from .trace_payloads import returned_ids, short_result, tool_call_payloads

_LOG = logging.getLogger(__name__)

#: The model of every session thread, started or resumed: the default route of
#: `versions.lock.toml` (`default_model`), the value the observe path's WORKER uses as well.
#: Until 27.09 a session named no model and the engine chose its own default (`gpt-5.6-sol`);
#: that afternoon the server began refusing it ("not supported when using Codex with a ChatGPT
#: account") and every NEW session failed before the model ran, while old threads kept working.
SESSION_MODEL = DEFAULT_ROUTES[ThreadRole.WORKER][0]

JOB_SESSION_TURN = "session.turn"

#: Klucz w zdarzeniu STATUS, pod którym stoi numer ostatniej wiadomości człowieka ZUŻYTEJ przez
#: turę. Nie nowy rodzaj zdarzenia: rodzaje ma zamknięta lista w 0015, a to jest znacznik, a nie
#: nowa kategoria rzeczy, które dzieją się w sesji.
#: POZIOM ZDARZENIA DLA CZŁOWIEKA, nie dla maszyny. `engine` znaczy „to jest mechanika, nie
#: rozmowa": ekran chowa takie wiersze pod „szczegóły techniczne" i nigdy nie liczy ich jako
#: kroków agenta. Zdarzenie bez tego klucza jest zwykłym wierszem rozmowy.
LEVEL_ENGINE = "engine"

#: ZDARZENIA SILNIKA, KTÓRYCH NIE ZAPISUJEMY WIERSZ PO WIERSZU, tylko zliczamy. Wszystkie są
#: licznikami albo kawałkami tekstu, który i tak przyjdzie w całości w `item/completed`:
#:
#:   * `item/agentMessage/delta` to POJEDYNCZY KAWAŁEK odpowiedzi. Jedna odpowiedź to setki
#:     takich ramek; zapisane jako osobne wiersze zamieniają rozmowę w taśmę telegraficzną,
#:     w której zdanie modelu ginie między „zdarzenie silnika" a „zdarzenie silnika".
#:     Zmierzone na live 19.09: 333 ramki silnika na jedną turę i jedna odpowiedź na końcu.
#:   * `thread/tokenUsage/updated` i `account/rateLimits/updated` to liczniki, które zmieniają
#:     się po każdej ramce i nie mówią nic o tym, co agent ZROBIŁ.
#:
#: NIE ZNIKAJĄ BEZ ŚLADU: po turze idzie jeden wiersz `level=engine` z licznikiem każdego
#: rodzaju. „Trzysta delt" to jest informacja; trzysta wierszy to jest jej brak.
ENGINE_NOISE: frozenset[str] = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/delta",
        "item/agentMessage/summary/delta",
        "thread/tokenUsage/updated",
        "account/rateLimits/updated",
    }
)

_CONSUMED_KEY = "wenext_wejscie_do_seq"
SESSION_TURN_PRIORITY = 4
#: A turn that reaches a model is not retried by the queue on its own: a second run would resume
#: the same thread and send the same message again. One attempt, and the reaper's terminalization
#: is what a human sees.
ATTEMPT_LIMIT = 1

#: Budget defaults, used when a session was created without one. Deliberately small: a session
#: that needs more says so, and an unbounded default is how a quota disappears overnight.
#: ILE PRZYSŁUGUJE JEDNEJ ROZMOWIE, gdy nikt nie ustawił inaczej.
#:
#: Podniesione 20.09 z 12 tur i 60 wywołań, po uwadze założyciela: rozmowa skończyła się po
#: TRZECH jego zdaniach, bo tura modelu bierze kilka wywołań narzędzi na każdą odpowiedź, a
#: dwanaście tur ustawiono pod jednorazowe zlecenie z P2, nie pod rozmowę, w której człowiek
#: dopisuje zdanie po zdaniu. Trzydzieści tur to około godziny pisania w obie strony.
#:
#: `max_minutes` dotyczy JEDNEJ TURY, nie sesji, i sześćdziesiąt minut nie jest zaproszeniem do
#: godzinnego czekania: ciszę silnika łapie limit 240 s w `codex_runtime.session_client`, a ten
#: tutaj jest ostatnim bezpiecznikiem dla tury, która coś robi, ale za długo.
#: D-046: rozmowa jest miejscem pracy, a nie dwudziestominutowym zleceniem, więc sufit ma być
#: taki, żeby człowiek go nie dotykał w normalnym dniu. `max_minutes` jest limitem POJEDYNCZEJ
#: TURY, nie doby: z niego wyliczany jest też czas życia pliku zdolności (`ttl_minutes`), więc
#: wpisanie tu doby oznaczałoby dobowy token z DSN-em przy każdej turze. Turę, która utknęła,
#: i tak ubija zegar ciszy w kliencie (240 s bez zdarzenia), więc ten limit wiąże tylko turę,
#: która PRACUJE. Doba z D-046 dotyczy życia rozmowy, a tego nie ogranicza dziś nic: rozmowa
#: żyje tak długo, jak człowiek do niej pisze.
DEFAULT_BUDGET: dict[str, int] = {"max_turns": 200, "max_minutes": 180, "max_tool_calls": 800}

#: Sesja obecności to jeden tik: obudzenie, kilka odczytów, jedna decyzja. Zostaje przy starych
#: liczbach, bo tam limitem, który naprawdę wiąże, jest budżet z powierzenia (tury modelu na
#: dobę), a ten tutaj pilnuje tylko, żeby pojedynczy tik nie chodził w kółko.
PRESENCE_BUDGET: dict[str, int] = {"max_turns": 12, "max_minutes": 20, "max_tool_calls": 60}

#: How often a running turn looks at its own trace for an interrupt asked for elsewhere. Short
#: enough that the button feels connected, long enough that a ten-minute turn costs six hundred
#: cheap reads and not sixty thousand.
INTERRUPT_POLL_SECONDS = 1.0

#: Where capability files live. One per session turn, removed in a `finally`.
CAPABILITY_DIR = Path(os.environ.get("WENEXT_SESSION_RUN_DIR", "/tmp/wenext-sessions"))

Clock = Callable[[], datetime]
ClientFactory = Callable[..., Any]


def budget_of(session: SessionRow) -> dict[str, int]:
    """Budżet TEJ sesji: domyślny dla jej rodzaju, nadpisany tym, co zapisano przy starcie.

    RODZAJ DECYDUJE, bo rozmowa i obecność mierzą co innego: rozmowa liczy wymianę zdań z
    człowiekiem, obecność jeden tik. Jedna liczba dla obu musiałaby być zła dla jednego z nich,
    i do 20.09 była zła dla rozmowy.

    Funkcja modułu, nie metoda, bo liczy ją także EKRAN (`api/sessions.py`): dwa miejsca liczące
    ten sam limit osobno to dwie odpowiedzi na pytanie „ile mi zostało".
    """
    base = PRESENCE_BUDGET if session.kind == SESSION_PRESENCE else DEFAULT_BUDGET
    merged = {**base}
    for key, value in (session.budget or {}).items():
        if key in base and isinstance(value, int | float) and int(value) > 0:
            merged[key] = int(value)
    return merged


def budget_sentence(budget: dict[str, int], spent: dict[str, int]) -> str:
    """Dlaczego rozmowa się zatrzymała, zdaniem dla człowieka.

    TRZY RZECZY, KTÓRYCH TO ZDANIE NIE MOŻE POMINĄĆ, i każda z nich to nieporozumienie, które
    już się wydarzyło (uwaga założyciela z 20.09, 01:02):

      * ŻE TO NASZ LIMIT, NIE CUDZY. „Budżet sesji wyczerpany: tool_calls 5, turns 3" zostało
        odczytane jako limit konta Codex: „mam limity, czemu pokazuje, że nie mam". Zdanie mówi
        wprost, czyj to limit i czyj nie jest.
      * KTÓRY LIMIT, po ludzku. `tool_calls` to nazwa klucza, a nie słowo, którego ktoś używa.
      * CO Z TYM ZROBIĆ. Dwie drogi, obie wykonalne od razu: nowa rozmowa albo wyższy limit w
        ustawieniach projektu.
    """
    turns, limit_turns = spent.get("turns", 0), budget.get("max_turns", 0)
    tools, limit_tools = spent.get("tool_calls", 0), budget.get("max_tool_calls", 0)
    if turns > limit_turns:
        what = f"{limit_turns} wymian zdań"
    elif tools > limit_tools:
        what = f"{limit_tools} użyć narzędzi przez agenta"
    else:  # wyczerpanie, którego nie umiemy nazwać, nadal ma powiedzieć prawdę
        what = "ustawionego limitu"
    return (
        f"Ta rozmowa doszła do limitu ustawionego w Wenext ({what}). To nie jest limit Twojego "
        "konta Codex. Zacznij nową rozmowę albo podnieś limit w ustawieniach projektu."
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TurnResult:
    """What one tick of `session.turn` did. Returned for tests and logs, never persisted: the
    trace is the record."""

    outcome: str
    detail: str
    session_id: UUID | None = None


OUTCOME_ANSWERED = "ANSWERED"
OUTCOME_NOT_CLAIMED = "NOT_CLAIMED"
OUTCOME_NO_CONNECTION = "NO_CONNECTION"
OUTCOME_BUDGET = "BUDGET_EXHAUSTED"
OUTCOME_FAILED = "FAILED"
OUTCOME_INTERRUPTED = "INTERRUPTED"
#: Tura, która nie padła i nie skończyła pracy: czeka na odnowienie limitu konta Codex i wróci
#: sama. Osobny wynik od `FAILED`, bo dla człowieka i dla raportu doby to są dwie różne rzeczy.
OUTCOME_WAITING_LIMIT = "WAITING_ACCOUNT_LIMIT"
#: 26.09: a background turn that must not run (the project's delegation is paused, past its
#: deadline or revoked, or the project is paused), and a turn that has nothing to answer.
OUTCOME_STOPPED = "STOPPED"
OUTCOME_NOTHING_NEW = "NOTHING_NEW"

#: Powód czekania, zapisywany w śladzie obok zdania dla człowieka. Kod, nie napis do czytania:
#: ekran grupuje po nim, a zdanie i tak przychodzi osobno.
REASON_ACCOUNT_LIMIT = "LIMIT_KONTA_CODEX"
#: Powód czekania, gdy organizacja nie ma jeszcze silnika (E1). Osobny od limitu konta, bo to
#: jest inna sprawa dla człowieka: tam czeka się na platformę, tu na jedno logowanie.
REASON_NO_ENGINE = "BRAK_SILNIKA"


async def schedule_turn(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    session_id: UUID,
    reason: str,
    delay_seconds: float = 0.0,
) -> Any:
    """Ask for one turn of this session.

    The idempotency key carries the REASON, so "the person wrote something" and "the session was
    created" are different jobs, while two identical requests collapse into one. A key of the
    session alone would make a second message during a turn disappear.
    """
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_SESSION_TURN,
        routing_key="observe",
        priority=SESSION_TURN_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"session-turn:{session_id}:{reason}",
        payload_ref=f"session/{session_id}",
        delay_seconds=max(0.0, delay_seconds),
    )


class SessionTurnHandlers:
    """The `session.turn` handler and the one-turn logic behind it."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        sessions: SessionStore,
        queue: JobQueuePort,
        organization_id: UUID,
        dsn: str,
        codex_bin: Path | None = None,
        codex_home_for: Callable[[SessionRow], Awaitable[Path | None]] | None = None,
        client_factory: ClientFactory | None = None,
        clock: Clock = _utc_now,
        capability_dir: Path = CAPABILITY_DIR,
    ) -> None:
        self._pool = pool
        self._sessions = sessions
        self._queue = queue
        self._organization_id = organization_id
        self._dsn = dsn
        self._codex_bin = codex_bin
        self._codex_home_for = codex_home_for
        self._client_factory = client_factory
        self._clock = clock
        self._capability_dir = capability_dir
        # Licznik szumu silnika i znacznik „agent już pisze", OBA PER SESJA. Dyspozytor wykonuje
        # dziś joby po kolei, więc jeden licznik na obiekcie by wystarczył; klucz po sesji jest
        # tu dlatego, że komentarz o kolejności przestałby być prawdą po cichu, gdyby kiedyś
        # joby ruszyły równolegle, a licznik pomieszany między dwie tury opowiada o trzeciej,
        # której nie było. Wpisy znikają po turze (`_record_engine_noise`).
        self._noise: dict[UUID, dict[str, int]] = {}
        self._typing: set[UUID] = set()

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {JOB_SESSION_TURN: self.run_turn_job}

    async def run_turn_job(self, claim: ClaimedJob) -> None:
        session_id = _session_of_payload(claim.payload_ref)
        if session_id is None:
            _LOG.warning(
                "session.turn bez identyfikatora sesji w payload_ref=%r", claim.payload_ref
            )
            return
        reason = await self._reason_of(claim)
        result = await self.run_once(session_id, reason=reason)
        _LOG.info("session.turn %s: %s (%s)", session_id, result.outcome, result.detail[:200])

    async def _reason_of(self, claim: ClaimedJob) -> str | None:
        """Why this turn was ordered: the tail of `session-turn:{session}:{reason}`.

        Read from the job row rather than carried in `payload_ref`, so the payload format that
        other readers parse stays as it was. `None` when the key has another shape.
        """
        async with self._pool.acquire() as conn:
            key = await conn.fetchval(
                "SELECT idempotency_key FROM jobs WHERE organization_id = $1 AND id = $2",
                self._organization_id,
                claim.job_id,
            )
        if not isinstance(key, str) or key.count(":") < 2:
            return None
        return key.split(":", 2)[2]

    # -- one turn --------------------------------------------------------------------------

    async def run_once(self, session_id: UUID, *, reason: str | None = None) -> TurnResult:
        session = await self._sessions.claim_for_turn(
            session_id, organization_id=self._organization_id
        )
        if session is None:
            return TurnResult(
                OUTCOME_NOT_CLAIMED,
                "sesja jest już w toku albo zakończona; ten job nie rusza drugiej tury",
                session_id,
            )

        # SESJA PROWADZONA W AGENCIE CZŁOWIEKA NIE MA TURY U NAS. Jej model to agent, którego
        # człowiek uruchomił u siebie (wtyczka), a my jesteśmy tylko jego narzędziami. Job tury
        # dla takiej sesji nie powinien powstać (trasy API odmawiają), ale gdyby powstał, nie
        # wolno mu uruchomić drugiego modelu na tej samej rozmowie: claim zmienił stan na RUNNING,
        # więc wraca do WAITING_USER, a model nie rusza.
        if host_of(session) is not None:
            return await self._host_session_stopped(session)

        # PRACA W TLE NIE RUSZA BEZ ŻYWEGO POWIERZENIA (26.09). Tik sprawdzał powierzenie przed
        # otwarciem sesji, ale tura zamówiona później (powrót po limicie konta, czekanie na
        # silnik, sesja potomna) ruszała mimo pauzy albo terminu, a przerwanie zapisane przy
        # pauzie leżało pod granicą obserwacji nowej tury i nikt go nie widział.
        stopped = await self._background_stopped(session)
        if stopped is not None:
            return stopped

        # WIADOMOŚĆ JUŻ DOSZŁA, WIĘC NIE MA DRUGIEJ TURY (26.09). Zdanie wpisane w trakcie tury
        # wchodzi do niej przez `turn/steer`, a `/message` i tak zamawia turę `message-{seq}`.
        # Ta tura ruszała po skończonej, nie miała czego odpowiedzieć i wysyłała modelowi cel
        # sesji jeszcze raz: pełna, zbędna tura po każdym dopowiedzeniu.
        if (
            reason is not None
            and reason.startswith("message-")
            and not await self._has_unanswered_message(session)
        ):
            await self._sessions.set_status(
                session.id,
                organization_id=self._organization_id,
                status=STATUS_WAITING_USER,
            )
            return TurnResult(
                OUTCOME_NOTHING_NEW,
                "wiadomość dotarła już do poprzedniej tury; model nie był uruchamiany",
                session.id,
            )

        over, spent = await self._budget_spent(session)
        if over:
            detail = budget_sentence(budget_of(session), spent)
            await self._record(
                session,
                EVENT_STATUS,
                {"status": STATUS_BUDGET_EXHAUSTED, "detail": detail, **spent},
            )
            await self._sessions.set_status(
                session.id,
                organization_id=self._organization_id,
                status=STATUS_BUDGET_EXHAUSTED,
                result_summary=detail,
            )
            return TurnResult(OUTCOME_BUDGET, "budżet wyczerpany", session.id)

        codex_home = await self._resolve_home(session)
        if codex_home is None:
            return await self._waits_for_engine(session)

        return await self._run_with_engine(session, codex_home)

    async def _host_session_stopped(self, session: SessionRow) -> TurnResult:
        """Give a plugin session back as it was and run nothing (mirrors `_background_stopped`)."""
        detail = (
            "Ta rozmowa jest prowadzona w agencie człowieka (Codex albo Claude Code), więc "
            "silnik Wenext nie uruchamia w niej tury. Model nie był uruchamiany."
        )
        await self._record(session, EVENT_STATUS, {"status": STATUS_WAITING_USER, "detail": detail})
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            status=STATUS_WAITING_USER,
        )
        return TurnResult(OUTCOME_STOPPED, detail, session.id)

    async def _background_stopped(self, session: SessionRow) -> TurnResult | None:
        """For a PRESENCE session: the reason it must not run now, recorded; `None` otherwise.

        A conversation is not gated here: it is the human talking, and a paused or expired
        delegation ends what the agent does WITHOUT them. The delegation is read through
        `PresenceStore`, so `live` means here exactly what it means to the tick and the publisher
        (revoked, paused, past the deadline, signer gone).
        """
        if session.kind != SESSION_PRESENCE:
            return None
        from control_plane.presence.store import PresenceStore

        delegation = await PresenceStore(self._pool).delegation_for(
            organization_id=self._organization_id, project_id=session.project_id
        )
        async with self._pool.acquire() as conn:
            project_paused = await conn.fetchval(
                "SELECT paused_at IS NOT NULL OR archived_at IS NOT NULL FROM projects "
                "WHERE organization_id = $1 AND id = $2",
                self._organization_id,
                session.project_id,
            )
        if delegation is not None and delegation.live and not project_paused:
            return None
        if delegation is None:
            why, status = "projekt nie ma powierzenia", STATUS_DONE
        elif delegation.expired or delegation.revoked_at is not None:
            why, status = f"powierzenie {delegation.state}", STATUS_DONE
        elif not delegation.live:
            why, status = f"powierzenie {delegation.state}", STATUS_PAUSED
        else:
            why, status = "projekt jest wstrzymany", STATUS_PAUSED
        detail = f"Praca w tle nie ruszyła: {why}. Model nie był uruchamiany."
        await self._record(session, EVENT_STATUS, {"status": status, "detail": detail})
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            status=status,
            result_summary=detail,
        )
        return TurnResult(OUTCOME_STOPPED, detail, session.id)

    async def _has_unanswered_message(self, session: SessionRow) -> bool:
        """Is there a human message the engine has not received yet (the same rule as
        `_message_for_turn`: above the last consumption marker)."""
        events = await self._sessions.turn_input_events(
            organization_id=self._organization_id, session_id=session.id
        )
        consumed_upto = 0
        for event in events:
            if event.kind == EVENT_STATUS and _CONSUMED_KEY in (event.payload or {}):
                consumed_upto = max(consumed_upto, int(event.payload[_CONSUMED_KEY] or 0))
        return any(
            event.kind == EVENT_USER_MESSAGE
            and event.seq > consumed_upto
            and str((event.payload or {}).get("text") or "").strip()
            for event in events
        )

    async def _run_with_engine(self, session: SessionRow, codex_home: Path) -> TurnResult:
        from control_plane.tools import issue, write_capability

        budget = budget_of(session)
        capability = issue(
            session_id=session.id,
            organization_id=session.organization_id,
            workspace_id=session.workspace_id,
            project_id=session.project_id,
            codex_connection_id=session.codex_connection_id,
            dsn=self._dsn,
            ttl_minutes=max(5, int(budget.get("max_minutes", 20)) + 5),
        )
        capability_file = self._capability_dir / f"{session.id}.json"
        write_capability(capability_file, capability)
        profile = profile_for(session.kind)
        overrides = _turn_overrides(capability_file, web_search=profile.web_search)
        # KATALOG MA ISTNIEĆ, PLIK NIE MA SIĘ ZMIENIĆ. Dotąd gwarantowało to przepisanie
        # konfiguracji przy każdej turze; po E9 nic do domu nie piszemy, więc zostaje samo
        # założenie katalogu z prawami 0700. Bez tego tura w domu, który ktoś skasował, startuje
        # app-servera na nieistniejącej ścieżce zamiast powiedzieć, że nie ma tam logowania.
        _ensure_home(codex_home)

        # GRANICA OBSERWACJI ŚLADU, policzona ZANIM cokolwiek ruszy. Wszystko, co człowiek zrobi
        # od tej chwili, należy do TEJ tury: przerwanie wciśnięte w trakcie otwierania wątku albo
        # startu tury ma być widziane, a nie zgubione pod granicą policzoną później.
        watch_from = await self._sessions.last_seq(
            organization_id=self._organization_id, session_id=session.id
        )
        client = self._build_client(codex_home, session, overrides)
        # Liczniki na TĘ turę. Zerowane tutaj, bo handler żyje dłużej niż tura i licznik
        # odziedziczony po poprzedniej opowiadałby o czymś, czego w tej turze nie było.
        self._noise.pop(session.id, None)
        self._typing.discard(session.id)
        outcome: TurnResult
        try:
            await client.start()
            await client.initialize()
            await self._check_runtime(session, client, profile)
            thread_id = await self._open_thread(client, session)
            message = await self._message_for_turn(session)
            consumed_upto = await self._last_user_seq(session)
            handle = await client.start_turn(thread_id, message)
            # Dopiero TERAZ wiadomości są zużyte: tura ruszyła i dostała je na wejściu.
            # `level=engine`, bo to jest zakładka dla NASTĘPNEJ tury, a nie zdanie dla człowieka:
            # w rozmowie wyglądałaby jak krok, którego agent nie zrobił.
            if consumed_upto:
                await self._record(
                    session,
                    EVENT_STATUS,
                    {_CONSUMED_KEY: consumed_upto, "level": LEVEL_ENGINE},
                )
            deadline_s = float(budget.get("max_minutes", 20)) * 60.0
            turn = await self._await_turn(client, handle, session, deadline_s, watch_from)
            outcome = await self._record_outcome(session, turn, client=client)
        except TimeoutError:
            detail = (
                f"Tura przekroczyła budżet czasu ({budget.get('max_minutes')} min) i została "
                "przerwana."
            )
            await self._record(session, EVENT_ERROR, {"detail": detail})
            await self._sessions.set_status(
                session.id,
                organization_id=self._organization_id,
                status=STATUS_BUDGET_EXHAUSTED,
                result_summary=detail,
            )
            outcome = TurnResult(OUTCOME_BUDGET, detail, session.id)
        except Exception as exc:
            # LIMIT KONTA ZGŁOSZONY PRZY STARCIE TEŻ JEST LIMITEM (26.09). Wykrywaliśmy go tylko z
            # wyniku tury; odmowa już przy `thread/resume` albo `turn/start` kończyła sesję jako
            # FAILED, bez powrotu po odnowieniu, choć to ten sam limit tego samego konta.
            limited = None
            with contextlib.suppress(Exception):
                limited = await self._rate_limited(session, f"{exc}", client=client)
            if limited is not None:
                outcome = limited
            else:
                detail = f"Silnik nie wykonał tury: {type(exc).__name__}: {exc}"
                await self._record(session, EVENT_ERROR, {"detail": detail})
                await self._sessions.set_status(
                    session.id,
                    organization_id=self._organization_id,
                    status=STATUS_FAILED,
                    result_summary=detail,
                )
                outcome = TurnResult(OUTCOME_FAILED, detail, session.id)
        finally:
            with contextlib.suppress(Exception):
                await client.close()
            # Szum idzie do śladu JEDNYM wierszem, po turze, także wtedy, gdy tura padła: to
            # wtedy liczba ramek przed awarią mówi najwięcej.
            with contextlib.suppress(Exception):
                await self._record_engine_noise(session)
            # The capability outlives nothing: the file carries a DSN and the turn is over.
            with contextlib.suppress(OSError):
                capability_file.unlink(missing_ok=True)
            # NIE MA CZEGO SPRZĄTAĆ W DOMU, i to jest cała zmiana z E9 (21.09). Wpis serwera
            # narzędzi nie leży już w `config.toml`, tylko jedzie w argumentach TEGO procesu,
            # więc znika razem z nim. Wcześniej zdejmowaliśmy go tutaj, bo plik jest WSPÓLNY dla
            # rozmów i pilota, a wpis po turze celował w skasowany plik zdolności i wywracał
            # każdą następną turę w tym domu („required MCP servers failed to initialize",
            # zmierzone na live). Sprzątanie po wpisach sprzed tej zmiany robi `reconcile_homes`
            # przy starcie procesu i można je usunąć po dwóch wdrożeniach bez takich wpisów.
        return outcome

    async def _waits_for_engine(self, session: SessionRow) -> TurnResult:
        """Rozmowa bez połączenia Codex CZEKA, a nie kończy się (E1, audyt UX 20.09).

        CO BYŁO. Pierwsze wejście do projektu bez silnika kończyło turę zdaniem „podłącz
        połączenie i napisz ponownie", czyli kazało człowiekowi zrobić dwie rzeczy: skonfigurować
        coś na innym ekranie i NAPISAĆ CEL DRUGI RAZ. Cel, który już napisał, zostawał w śladzie
        bez skutku.

        CO JEST. Sesja idzie w `WAITING_EVENT` z kartą SILNIK, a tura rusza sama, gdy połączenie
        przejdzie w gotowe: ten sam mechanizm, co przy limicie konta, bo to ten sam kształt
        sprawy („czekamy na coś poza nami i wrócimy sami"). Cel zostaje zapisany i nietknięty.

        TEMPO PYTANIA zależy od tego, czy człowiek właśnie coś robi: gdy trwa logowanie
        urządzenia, pytamy co pół minuty, bo za chwilę się skończy; gdy nie ma nawet połączenia,
        odstęp rośnie od dwóch minut, żeby projekt porzucony na tygodniu nie budził się co chwilę.

        ZDANIE ZALEŻY OD TEGO, CZY POWRÓT NAPRAWDĘ STOI W KOLEJCE. Cała obietnica E1 („zacznę
        sam") wisi na jednym jobie, więc jego zamówienie idzie PRZED napisaniem zdania: gdy
        kolejka odmówi, człowiek dostaje prawdę („napisz do mnie, gdy podłączysz") zamiast
        obietnicy, której nikt nie dotrzyma, a ślad niesie powód odmowy.
        """
        from control_plane.sessions import rate_limit as limits

        card, state = await self._engine_card(session)
        attempt = await self._reason_attempts(session, REASON_NO_ENGINE) + 1
        delay = 30.0 if state == ENGINE_CONNECTING else limits.backoff(attempt).total_seconds()
        queue_refused: str | None = None
        try:
            await schedule_turn(
                self._queue,
                organization_id=self._organization_id,
                workspace_id=session.workspace_id,
                session_id=session.id,
                reason=f"silnik-{attempt}",
                delay_seconds=delay,
            )
        # Szeroki wyjątek świadomie: powód jedzie do śladu, a tura i tak ma się skończyć
        # zdaniem dla człowieka, a nie wyjątkiem w logu workera.
        except Exception as exc:
            queue_refused = f"{type(exc).__name__}: {exc}"
        if queue_refused is not None:
            detail = (
                "Ta rozmowa nie ma jeszcze silnika, na którym mogłaby ruszyć. Podłącz Codexa "
                "kartą obok i napisz do mnie, gdy będzie gotowy; cel jest zapisany. Nie umiem "
                "dziś wrócić tu sam."
            )
        elif state == ENGINE_CONNECTING:
            detail = (
                "Trwa logowanie do Codexa. Zacznę pracę, gdy tylko się skończy; cel jest zapisany."
            )
        else:
            detail = (
                "Ta rozmowa nie ma jeszcze silnika, na którym mogłaby ruszyć. Podłącz Codexa "
                "kartą obok; cel jest zapisany i zacznę sam, gdy połączenie będzie gotowe."
            )
        await self._record(
            session,
            EVENT_STATUS,
            {
                "status": STATUS_WAITING_EVENT,
                "detail": detail,
                "powod": REASON_NO_ENGINE,
                "proba": attempt,
                "karty": [card],
                **({"kolejka_odmowila": queue_refused} if queue_refused else {}),
            },
        )
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            status=STATUS_WAITING_EVENT,
            result_summary=detail,
        )
        return TurnResult(OUTCOME_NO_CONNECTION, detail, session.id)

    async def _engine_card(self, session: SessionRow) -> tuple[dict[str, Any], str]:
        """Karta SILNIK z aktualnego stanu połączeń tej organizacji."""
        from control_plane.sessions.engine_card import card_for_organization

        return await card_for_organization(
            self._pool,
            organization_id=self._organization_id,
            workspace_id=session.workspace_id,
        )

    async def _reason_attempts(self, session: SessionRow, reason: str) -> int:
        """Ile razy TA rozmowa czekała już z tego powodu. Liczone ze śladu, nie z pamięci."""
        events = await self._sessions.events(
            organization_id=self._organization_id, session_id=session.id, limit=500
        )
        return len(
            [
                one
                for one in events
                if one.kind == EVENT_STATUS and (one.payload or {}).get("powod") == reason
            ]
        )

    async def _limit_attempts(self, session: SessionRow) -> int:
        """Ile razy TA rozmowa trafiła już w limit konta. Liczone ze śladu, nie z pamięci.

        Ślad jest jedynym miejscem, które przeżywa restart procesu, a licznik w pamięci
        zerowałby odstęp przy każdym wdrożeniu, czyli dokładnie wtedy, gdy sesje wracają tłumnie.
        """
        return await self._reason_attempts(session, REASON_ACCOUNT_LIMIT)

    async def _record_engine_noise(self, session: SessionRow) -> None:
        """Jeden wiersz z liczbą ramek, których nie zapisaliśmy pojedynczo.

        DLACZEGO W OGÓLE, skoro to szum: bo „trzysta delt" i „zero delt" to dwa różne zdania o
        turze, a po awarii tylko to pierwsze mówi, że model zdążył coś napisać. Zliczone, więc
        nie znika; policzone, więc nie zalewa. Wiersz ma `level=engine`, czyli nie pokazuje się
        w głównym nurcie rozmowy.
        """
        counted = self._noise.pop(session.id, None)
        self._typing.discard(session.id)
        if not counted:
            return
        total = sum(counted.values())
        breakdown = ", ".join(f"{name}: {count}" for name, count in sorted(counted.items()))
        await self._record(
            session,
            EVENT_STATUS,
            {
                "detail": f"silnik: {total} ramek pominiętych w zapisie ({breakdown})",
                "level": LEVEL_ENGINE,
                "wenext_szum": dict(counted),
            },
        )

    async def _await_turn(
        self, client: Any, handle: Any, session: SessionRow, deadline_s: float, since: int
    ) -> Any:
        """Wait for the turn, and watch the trace for a human pressing "przerwij".

        THE INTERRUPT ARRIVES IN A DIFFERENT PROCESS. The turn runs in a worker; the button is
        an HTTP request to the API. There is no object they share, so the API appends an
        `INTERRUPT` event and this loop notices it and calls `turn/interrupt` on the engine. The
        alternative, keeping the turn's handle in memory and reaching for it from the request,
        works only while both happen to be the same process and stops working the moment there
        are two workers.

        The turn still ends through `turn/completed`, with status `interrupted`. Nothing here
        assumes the interrupt took effect: the engine says whether it did.

        `since` PRZYCHODZI Z ZEWNĄTRZ I TO JEST POPRAWKA, NIE UPORZĄDKOWANIE (E8, zmierzone
        21.09). Liczone tutaj, czyli PO `turn/start`, gubiło przerwanie wciśnięte w tej właśnie
        chwili: zdarzenie dostawało numer niższy niż granica i pętla nie widziała go NIGDY. Dla
        człowieka wyglądało to tak, że nacisnął „przerwij", zdarzenie stoi w śladzie, a tura
        spokojnie kończy pracę. Pomiar rodziny dał dwie porażki na osiem przebiegów i obie przy
        NISKIM obciążeniu hosta, co jest podpisem tego wyścigu: im szybciej działa maszyna, tym
        łatwiej wcisnąć przycisk przed policzeniem granicy.
        """
        waiting = asyncio.ensure_future(client.wait_for_turn(handle, timeout_s=deadline_s))
        asked = False
        steering = True
        try:
            while True:
                done, _ = await asyncio.wait({waiting}, timeout=INTERRUPT_POLL_SECONDS)
                if waiting in done:
                    return waiting.result()
                if asked and not steering:
                    continue
                fresh = await self._sessions.events(
                    organization_id=self._organization_id,
                    session_id=session.id,
                    after_seq=since,
                )
                if not asked and any(event.kind == EVENT_INTERRUPT for event in fresh):
                    asked = True
                    # PROŚBA O PRZERWANIE ZOSTAWIA ŚLAD, także gdy się nie uda. Stało tu
                    # `contextlib.suppress(Exception)`, więc odmowa silnika znikała bez słowa, a
                    # człowiek widział turę, która „nie dała się przerwać", bez wyjaśnienia.
                    try:
                        await client.interrupt_turn(handle)
                    except Exception as exc:
                        await self._record(
                            session,
                            EVENT_STATUS,
                            {
                                "detail": (
                                    "Silnik nie przyjął prośby o przerwanie: "
                                    f"{type(exc).__name__}: {exc}"
                                ),
                                "level": LEVEL_ENGINE,
                            },
                        )
                if steering and not asked:
                    steering = await self._steer(session, client, handle, fresh)
        finally:
            if not waiting.done():
                waiting.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await waiting

    async def _steer(self, session: SessionRow, client: Any, handle: Any, fresh: list[Any]) -> bool:
        """Wpuść zdania, które człowiek napisał W TRAKCIE tury, do tej tury (D-046).

        DLACZEGO TO NIE MOŻE CZEKAĆ NA NASTĘPNĄ TURĘ. Job zamówiony przez `/message` trafia na
        `claim_for_turn`, które odmawia, bo tura już trwa, i job kończy się jako NIEZAJĘTY. Nikt
        go nie ponawia. Więc dopowiedzenie w trakcie pracy leżało w śladzie do czasu, aż coś
        innego uruchomiło turę; przy długiej turze znaczyło to „nie usłyszał".

        Znacznik `wenext_wejscie_do_seq` jest zapisywany DOPIERO po tym, jak silnik przyjął
        zdanie, i tylko wtedy. Gdy `turn/steer` odmówi (na przykład tura właśnie się skończyła i
        `expectedTurnId` się nie zgadza), wiadomość zostaje niezużyta i weźmie ją następna tura,
        tak jak przedtem. Zwracane `False` wyłącza sterowanie do końca tej tury, żeby odmowa nie
        powtarzała się co sekundę.

        Wyścig, który zostaje i jest policzony: jeżeli wiadomość wejdzie do tury w ostatniej
        chwili, a job tej wiadomości zdąży zająć sesję po zakończeniu tury, ruszy dodatkowa tura
        z samym celem. Kosztuje jedną turę i nie gubi niczyich słów, więc nie jest tego wart
        drugi mechanizm.
        """
        steer = getattr(client, "steer_turn", None)
        if steer is None:
            return False
        consumed_upto = max(
            (
                int(event.payload[_CONSUMED_KEY] or 0)
                for event in fresh
                if event.kind == EVENT_STATUS and _CONSUMED_KEY in (event.payload or {})
            ),
            default=0,
        )
        for event in fresh:
            if event.kind != EVENT_USER_MESSAGE or event.seq <= consumed_upto:
                continue
            text = str((event.payload or {}).get("text") or "").strip()
            if not text:
                continue
            try:
                await steer(handle, f"[Wiadomość człowieka, seq={event.seq}]\n{text}")
            except Exception as exc:
                await self._record(
                    session,
                    EVENT_STATUS,
                    {
                        "detail": f"Dopowiedzenie nie weszło do trwającej tury: {exc}",
                        "level": LEVEL_ENGINE,
                    },
                )
                return False
            await self._record(
                session,
                EVENT_STATUS,
                {_CONSUMED_KEY: event.seq, "level": LEVEL_ENGINE},
            )
            consumed_upto = event.seq
        return True

    def _build_client(
        self, codex_home: Path, session: SessionRow, overrides: tuple[str, ...] = ()
    ) -> Any:
        async def sink(method: str, params: dict[str, Any]) -> None:
            await self._record_notification(session, method, params)

        if self._client_factory is not None:
            return self._client_factory(
                codex_home=codex_home, sink=sink, config_overrides=overrides
            )
        from codex_runtime.session_client import AsyncAppServerClient

        codex_bin = self._codex_bin
        if codex_bin is None:
            from codex_runtime import find_codex_bin

            codex_bin = find_codex_bin()
        return AsyncAppServerClient(
            codex_bin=codex_bin, codex_home=codex_home, sink=sink, config_overrides=overrides
        )

    async def _open_thread(self, client: Any, session: SessionRow) -> str:
        """Resume the session's thread, or start one. A resume that fails is not a failed turn.

        A connection can change under a session and a thread can be gone with it. P2 requires
        that the domain history survives that even when a new engine thread has to be made, and
        the domain history is `session_events`, which is ours. So a refused resume starts a new
        thread and SAYS SO in the trace, rather than ending the session.
        """
        from codex_runtime.session_client import ServerGone

        if session.engine_thread_id:
            try:
                resumed = await client.resume_thread(
                    session.engine_thread_id,
                    base_instructions=BASE_INSTRUCTIONS,
                    model=SESSION_MODEL,
                )
                if resumed != session.engine_thread_id:
                    await self._sessions.set_status(
                        session.id,
                        organization_id=self._organization_id,
                        status=session.status,
                        engine_thread_id=resumed,
                    )
                return resumed
            except ServerGone:
                # The transport is dead; starting another thread on it hides the actual error.
                raise
            except Exception as exc:
                await self._record(
                    session,
                    EVENT_STATUS,
                    {
                        "detail": (
                            f"Wątek silnika {session.engine_thread_id} nie dał się wznowić "
                            f"({type(exc).__name__}). Zakładam nowy; historia rozmowy zostaje."
                        )
                    },
                )
        # `approval_policy="never"` JEST CZĘŚCIĄ POPRAWKI Z 19.09, nie ozdobą. Klient umie teraz
        # odpowiedzieć na żądanie zgody, ale najlepszą odpowiedzią na pytanie, którego nikt nie
        # przeczyta, jest nie dostać go wcale: w sesji bez człowieka przy ekranie nie ma kto
        # rozważyć „czy mogę". Zakres i tak jest pilnowany tam, gdzie powstał: katalog narzędzi
        # jest zamknięty, zdolność leży w pliku 0600 z TTL, a każde narzędzie sprawdza swój
        # zasięg samo. Nazwa pola jest z przypiętego kontraktu (`v2/ThreadStartParams.json`),
        # a `to_wire` odrzuci ją, gdyby kontrakt się zmienił.
        thread_id = await client.start_thread(
            base_instructions=BASE_INSTRUCTIONS, approval_policy="never", model=SESSION_MODEL
        )
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            status=session.status,
            engine_thread_id=thread_id,
        )
        return thread_id

    async def _last_user_seq(self, session: SessionRow) -> int:
        """Numer ostatniej wiadomości człowieka w śladzie. Granica „zużyte" dla tej tury."""
        events = await self._sessions.turn_input_events(
            organization_id=self._organization_id, session_id=session.id
        )
        return max(
            (event.seq for event in events if event.kind == EVENT_USER_MESSAGE),
            default=0,
        )

    async def _message_for_turn(self, session: SessionRow) -> str:
        """Everything the person has said that the agent has not answered yet.

        Read from the trace rather than passed in, so a message that arrived while the previous
        turn was running is picked up by the next one, and a restart re-sends exactly what was
        unanswered instead of guessing.
        """
        events = await self._sessions.turn_input_events(
            organization_id=self._organization_id, session_id=session.id
        )
        # CO JUŻ POSZŁO DO MODELU, a nie „co już zostało odpowiedziane". Różnica jest tym
        # błędem, który audyt z 19.09 nazwał „Korekta zniknęła z następnej tury": wiadomość
        # wpisana W TRAKCIE tury przychodzi PRZED odpowiedzią, ale nie była częścią wejścia tej
        # tury. Poprzednia wersja czyściła listę na każdej odpowiedzi modelu, więc kasowała
        # właśnie tę korektę, której model nigdy nie widział.
        #
        # Znacznik jest zapisywany PO udanym `turn/start`, więc tura, która nie wystartowała,
        # nie zabiera ze sobą niczyjej wiadomości.
        consumed_upto = 0
        for event in events:
            if event.kind == EVENT_STATUS and _CONSUMED_KEY in (event.payload or {}):
                consumed_upto = max(consumed_upto, int(event.payload[_CONSUMED_KEY] or 0))
        unanswered: list[str] = []
        for event in events:
            if event.kind == EVENT_USER_MESSAGE and event.seq > consumed_upto:
                text = str(event.payload.get("text") or "").strip()
                if text:
                    unanswered.append(f"[Wiadomość człowieka, seq={event.seq}]\n{text}")
        if not unanswered:
            unanswered = [session.goal]
        first_turn = not any(e.kind == EVENT_ASSISTANT_MESSAGE for e in events)
        if first_turn:
            from control_plane.tools import issue

            capability = issue(
                session_id=session.id,
                organization_id=session.organization_id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
                dsn=self._dsn,
            )
            context = await starting_context(self._pool, capability=capability, goal=session.goal)
            return context.render() + "\n\nWIADOMOŚCI CZŁOWIEKA:\n" + "\n\n".join(unanswered)
        return "\n\n".join(unanswered)

    # -- the trace -------------------------------------------------------------------------

    async def _record_notification(
        self, session: SessionRow, method: str, params: dict[str, Any]
    ) -> None:
        """One server notification into the trace, as it arrives.

        TRZY LOSY RAMKI SILNIKA, i różnica między nimi jest tym, co odróżnia zapis rozmowy od
        taśmy telegraficznej:

          * ROZMOWA: odpowiedź, wywołanie narzędzia i jego wynik, przerwanie, błąd, żądanie
            serwera wraz z naszą decyzją. To są wiersze, które człowiek czyta.
          * MECHANIKA: metoda, której nie znamy. Wiersz z `level=engine`, schowany na ekranie
            pod „szczegóły techniczne", ale ZAPISANY, bo zmiana protokołu ma być widoczna.
          * SZUM: delty i liczniki z `ENGINE_NOISE`. Zliczane, nie zapisywane; podsumowanie
            idzie jednym wierszem po turze (`_record_engine_noise`).

        Unrecognised methods are recorded rather than dropped. A protocol change that leaves no
        trace shows up first as a conversation with a hole in it that nobody can explain.
        """
        try:
            if method in ENGINE_NOISE:
                counted = self._noise.setdefault(session.id, {})
                counted[method] = counted.get(method, 0) + 1
                # JEDEN ZNAK ŻYCIA NA WYPOWIEDŹ, nie na kawałek: ekran na żywo ma prawo
                # pokazać „agent pisze", a nie wiersz na każdy token.
                if method == "item/agentMessage/delta" and session.id not in self._typing:
                    self._typing.add(session.id)
                    await self._record(
                        session,
                        EVENT_STATUS,
                        {
                            "detail": "agent pisze odpowiedź",
                            "level": LEVEL_ENGINE,
                            "wenext_pisze": True,
                        },
                    )
                return
            if method in ("item/started",):
                return  # the completed form carries the result; the started form would double
            if method == "item/completed":
                await self._record_item(session, params.get("item") or {})
                return
            if method == "item/mcpToolCall/progress":
                await self._record(
                    session,
                    EVENT_STATUS,
                    {"detail": str(params.get("message") or "narzędzie pracuje")},
                )
                return
            if method in ("thread/started", "turn/completed", "thread/status/changed"):
                return  # handled by the turn's own result, not worth a second row
            if params.get("wenext_zadanie_serwera"):
                # A REQUEST the app-server made of us, and what we answered. Recorded with the
                # decision, not as a bare "engine event": that is what the trace looked like on
                # 19.09, when `mcpServer/elicitation/request` appeared as one anonymous STATUS
                # line and nothing said that nobody had replied to it. A refusal nobody can see
                # is indistinguishable from a failure.
                await self._record(
                    session,
                    EVENT_STATUS,
                    {
                        "detail": (
                            f"żądanie silnika {method} od serwera "
                            f"{params.get('wenext_serwer')}: {params.get('wenext_decyzja')}"
                        ),
                        "wenext_zadanie_serwera": True,
                        "wenext_serwer": params.get("wenext_serwer"),
                        "wenext_decyzja": params.get("wenext_decyzja"),
                        "wenext_powod": params.get("wenext_powod"),
                    },
                )
                return
            await self._record(
                session,
                EVENT_STATUS,
                {"detail": f"zdarzenie silnika: {method}", "level": LEVEL_ENGINE},
            )
        except Exception:
            # A trace that cannot be written must not take the turn down with it; the turn's
            # own result still lands, and the gap is visible as a missing row.
            _LOG.exception("session.turn: nie udało się zapisać zdarzenia %s", method)

    async def _record_item(self, session: SessionRow, item: dict[str, Any]) -> None:
        kind = str(item.get("type") or "")
        if kind == "agentMessage":
            text = str(item.get("text") or "").strip()
            if text:
                # `message_id` jest identyfikatorem WYPOWIEDZI, nie wiersza: gdy ta sama
                # wypowiedź przyjdzie drugi raz (wznowienie, ponowne czytanie śladu), ekran ma
                # ją zastąpić, a nie dopisać drugą obok. Delty do niej już nie dojdą, bo tekst
                # jest tu w całości.
                await self._record(
                    session,
                    EVENT_ASSISTANT_MESSAGE,
                    {"text": text, "message_id": str(item.get("id") or "")},
                )
                self._typing.discard(session.id)
            return
        if kind == "mcpToolCall":
            # The shaping is shared with the plugin tool server (`trace_payloads`), so the two
            # kinds of session cannot record the same call differently.
            call_payload, result_payload = tool_call_payloads(item)
            await self._record(session, EVENT_TOOL_CALL, call_payload)
            await self._record(session, EVENT_TOOL_RESULT, result_payload)
            return
        if kind in ("reasoning", "userMessage"):
            return
        await self._record(
            session,
            EVENT_STATUS,
            {"detail": f"element wątku: {kind or '?'}", "level": LEVEL_ENGINE},
        )

    async def _record(self, session: SessionRow, kind: str, payload: dict[str, Any]) -> None:
        await self._sessions.append_event(
            organization_id=self._organization_id,
            session_id=session.id,
            kind=kind,
            payload=payload,
        )

    async def _record_outcome(
        self, session: SessionRow, turn: Any, *, client: Any = None
    ) -> TurnResult:
        from codex_runtime.session_client import TURN_INTERRUPTED

        text = turn.assistant_text()
        # LIMIT KONTA CODEX NIE JEST KOŃCEM PRACY, tylko czekaniem (zgłoszenie założyciela
        # 20.09, 03:24). Sprawdzane przed wszystkim innym, bo komunikat przychodzi raz jako
        # TREŚĆ tury, a raz jako jej błąd, i w obu przypadkach znaczy to samo.
        waited = await self._rate_limited(session, text or (turn.error or ""), client=client)
        if waited is not None:
            return waited
        if turn.status == TURN_INTERRUPTED:
            await self._record(
                session, EVENT_INTERRUPT, {"detail": "tura przerwana przez człowieka"}
            )
            await self._sessions.set_status(
                session.id,
                organization_id=self._organization_id,
                status=STATUS_PAUSED,
                result_summary=text or "Tura przerwana; wznów, żeby kontynuować.",
            )
            return TurnResult(OUTCOME_INTERRUPTED, "przerwana", session.id)
        if not turn.ok:
            detail = turn.error or f"tura zakończyła się stanem {turn.status}"
            await self._record(session, EVENT_ERROR, {"detail": detail})
            await self._sessions.set_status(
                session.id,
                organization_id=self._organization_id,
                status=STATUS_FAILED,
                result_summary=detail,
            )
            return TurnResult(OUTCOME_FAILED, detail, session.id)

        refs = await self._references(session)
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            # WAITING_USER, not DONE. The agent answered; whether the GOAL is met is the
            # person's call, and a session that closed itself would take that call away.
            status=STATUS_WAITING_USER,
            result_summary=text or "(model nie zwrócił treści)",
            result_refs=refs,
        )
        return TurnResult(OUTCOME_ANSWERED, text[:200] or "(pusta odpowiedź)", session.id)

    async def _rate_limited(
        self, session: SessionRow, text: str, *, client: Any = None
    ) -> TurnResult | None:
        """Sesja czeka na odnowienie limitu konta, zamiast umierać. `None`, gdy limitu nie ma.

        DWA STOPNIE, bo tekst sam w sobie nie jest dowodem: znacznik w treści tylko każe ZAPYTAĆ
        silnik (`account/rateLimits/read`), a dopiero jego odpowiedź daje godzinę. Gdy odczyt się
        nie uda (starszy app-server, konto niezalogowane), zostaje godzina z komunikatu, a gdy i
        jej nie ma, ślepa próba co kwadrans. Każdy z tych trzech przypadków mówi na ekranie co
        innego, bo dla człowieka znaczą co innego.

        WZNOWIENIE IDZIE ISTNIEJĄCĄ KOLEJKĄ: jeden job `session.turn` z opóźnieniem i kluczem
        idempotencji po sesji i godzinie, więc dwa wykrycia tego samego limitu nie zrobią dwóch
        wznowień.
        """
        from control_plane.sessions import rate_limit as limits

        if not limits.looks_like_a_limit(text):
            return None
        now = self._clock()
        answer: Any = None
        from_engine = None
        if client is not None:
            try:
                answer = await client.read_rate_limits()
                from_engine = limits.resets_from_engine(answer, now=now)
            except Exception as exc:  # brak konta, starszy app-server, zerwane połączenie
                _LOG.info("session.turn: nie udało się odczytać limitów konta: %s", exc)
        from_text = limits.resets_from_text(text, now=now)

        # SILNIK WYGRYWA, GDY JEGO LICZBA JEST WIARYGODNA, i to jest korekta reguły z tego samego
        # dnia. Poprzednia wersja przy rozjeździe wybierała TEKST, bo to on jest tym, co widzi
        # człowiek. Zmierzone na żywo 20.09, drugi przebieg: silnik podał 06:33:16 UTC (dobrze),
        # a tekst „try again at 8:33 AM" sparsował się na JUTRO, bo 8:33 właśnie minęło. Tekst
        # niesie samą godzinę, bez daty, więc przy rozjeździe to on jest tą gorszą informacją, a
        # wybranie go kosztowało dobę czekania. Tekst zostaje zapasem i materiałem na zdanie.
        resets_at, source = from_engine, "engine"
        disagrees = (
            from_engine is not None
            and from_text is not None
            and abs((from_text - from_engine).total_seconds()) > 300
        )
        if from_engine is None:
            resets_at, source = from_text, "message"
        if resets_at is None:
            resets_at, source = now + limits.BLIND_RETRY, "blind"

        # MINIMALNY ODSTĘP, rosnący przy kolejnych próbach. Odnowienie podane co do sekundy bywa
        # dwie sekundy do przodu (zmierzone), a ponowienie „dokładnie wtedy" trafia w ten sam
        # limit i zaczyna pętlę po kilka sekund, z pełnym startem app-servera za każdym razem.
        attempt = await self._limit_attempts(session) + 1
        floor = now + limits.backoff(attempt)
        if resets_at < floor:
            resets_at = floor
        # DŁUGIE CZEKANIE (28-29.09: plan darmowy, okno 30 dni). Prawdziwa data zostaje w zdaniu
        # i w śladzie, a powrót jest planowany najpóźniej za `LONG_WAIT_CHECK`: reset limitu albo
        # zmiana planu mają zdjąć czekanie od razu, nie w dniu odnowienia.
        facts = limits.account_facts(answer)
        check_at = now + limits.LONG_WAIT_CHECK if resets_at - now > limits.LONG_WAIT_FROM else None
        next_try = check_at or resets_at
        limit = limits.RateLimit(
            resets_at=resets_at,
            source=source,
            raw=text[:2000],
            plan=facts.plan,
            resets_available=facts.resets_available,
            check_at=check_at,
        )
        detail = limits.sentence(limit)
        await self._record(
            session,
            EVENT_STATUS,
            {
                "status": STATUS_WAITING_EVENT,
                "detail": detail,
                "powod": REASON_ACCOUNT_LIMIT,
                # Kiedy spróbujemy znowu (czyta to też bramka obecności); przy długim czekaniu
                # wcześniej niż odnowienie, które stoi w `limit_do`.
                "odnowi_sie": next_try.isoformat(),
                "limit_do": resets_at.isoformat(),
                "plan": facts.plan,
                "resety_dostepne": facts.resets_available,
                "skad": source,
                # SUROWY KOMUNIKAT SILNIKA ZOSTAJE. Ekran chowa go pod szczegóły techniczne, ale
                # nie kasuje: to jedyne miejsce, w którym widać, co naprawdę powiedziała
                # platforma, a zdanie po polsku jest naszym streszczeniem, nie jej słowami.
                "surowy_komunikat": limit.raw,
                # SUROWA ODPOWIEDŹ `rateLimits/read`, bo kształtu tej metody nie ma w SDK i
                # pierwsze spotkanie z nią kosztowało nas rok 2083. Bez tego zapisu diagnoza
                # wymaga powtórzenia całej sytuacji na żywym koncie.
                "surowa_odpowiedz_limitow": short_result(answer),
                "z_silnika": from_engine.isoformat() if from_engine else None,
                "z_komunikatu": from_text.isoformat() if from_text else None,
                # OSTRZEŻENIE PRZY ROZJEŹDZIE, bo obie wartości bywają prawdziwe i obie bywają
                # mylące: liczba silnika nie ma strefy do pomylenia, a tekst nie ma daty.
                "rozjazd": disagrees,
                "proba": attempt,
            },
        )
        await self._sessions.set_status(
            session.id,
            organization_id=self._organization_id,
            status=STATUS_WAITING_EVENT,
            result_summary=detail,
        )
        delay = max(60.0, (next_try - self._clock()).total_seconds() + 60.0)
        with contextlib.suppress(Exception):
            await schedule_turn(
                self._queue,
                organization_id=self._organization_id,
                workspace_id=session.workspace_id,
                session_id=session.id,
                reason=f"limit-{next_try.isoformat(timespec='minutes')}",
                delay_seconds=delay,
            )
        return TurnResult(OUTCOME_WAITING_LIMIT, detail, session.id)

    async def _references(self, session: SessionRow) -> list[dict[str, Any]]:
        """What the answer stands on: the tools it called, their arguments, and WHAT CAME BACK.

        Taken from the trace rather than parsed out of the model's prose. A reference the model
        wrote in a sentence is a claim; a tool call in the trace is a record.

        THE ARGUMENTS ARE NOT ENOUGH, and that is why the results are read too. `memory.list`
        takes a filter and returns findings; `sources.read` takes an address and returns a
        material. The identifiers of what the answer actually rests on are in the RESULT, so a
        `result_refs` built from arguments alone can say "it searched" but never "it used this
        finding". P3 measured the same thing in the trace; this puts it where the screen and the
        day report already look.

        The call and its result are paired IN ORDER, by position in the trace, not by tool name:
        two calls of the same tool in one turn are two different references, and matching on the
        name would give the first one the second one's identifiers.
        """
        events = await self._sessions.events(
            organization_id=self._organization_id, session_id=session.id, limit=500
        )
        refs: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        for event in events:
            if event.kind == EVENT_TOOL_CALL:
                if pending is not None:  # a call whose result never arrived is still a call
                    refs.append(pending)
                pending = {
                    "tool": event.payload.get("tool"),
                    "arguments": event.payload.get("arguments"),
                }
                continue
            if event.kind == EVENT_TOOL_RESULT and pending is not None:
                returned = returned_ids(event.payload.get("result"))
                if returned:
                    pending["returned_ids"] = returned
                refs.append(pending)
                pending = None
        if pending is not None:
            refs.append(pending)
        return refs[-20:]

    # -- budget and connection --------------------------------------------------------------

    async def _budget_spent(self, session: SessionRow) -> tuple[bool, dict[str, int]]:
        budget = budget_of(session)
        counts = await self._sessions.counts_for_budget(
            organization_id=self._organization_id, session_id=session.id
        )
        over = counts["turns"] > int(budget.get("max_turns", 0)) or counts["tool_calls"] > int(
            budget.get("max_tool_calls", 0)
        )
        return over, counts

    async def _check_runtime(
        self, session: SessionRow, client: Any, profile: RuntimeProfile
    ) -> None:
        """Read the EFFECTIVE configuration and say when it is not what this session asked for.

        MEASURED, NOT ASSUMED (D12). We write `config.toml` at the start of every turn, but the
        file is not the configuration: the runtime resolves it, and a value it does not accept
        is dropped silently. The read costs one frame and answers the only question that matters
        before the model starts: does this session have what it thinks it has.

        Two severities, because two things can be wrong here. A `[features]` flag that is not
        `false` means the hardening did not take and the model may have a shell, so it goes into
        the trace as a STATUS a person can see. A `web_search` other than asked for means the
        session has one tool fewer, which is worth recording but is not an alarm, so it goes in
        at engine level. The turn runs either way: a session that refused to work because search
        was unavailable would be a worse failure than a session that works without it.
        """
        try:
            config = await client.read_config()
        except Exception as exc:  # pragma: no cover - zależy od silnika, nie od nas
            await self._record(
                session,
                EVENT_STATUS,
                {
                    "detail": f"Nie udało się odczytać konfiguracji silnika: {exc}",
                    "level": LEVEL_ENGINE,
                },
            )
            return
        from codex_runtime.hardening import effective_config_mismatches

        mismatches = effective_config_mismatches(config, web_search=profile.web_search)
        if not mismatches:
            return
        broken = hardening_is_broken(mismatches)
        if broken:
            await self._record(
                session,
                EVENT_STATUS,
                {
                    "detail": (
                        "Silnik wystartował z konfiguracją inną niż zapisana: "
                        + "; ".join(broken)
                        + ". To są flagi, które dają modelowi powłokę."
                    ),
                    "niezgodnosci": mismatches,
                },
            )
            return
        await self._record(
            session,
            EVENT_STATUS,
            {"detail": "; ".join(mismatches), "level": LEVEL_ENGINE},
        )

    async def _resolve_home(self, session: SessionRow) -> Path | None:
        if self._codex_home_for is not None:
            return await self._codex_home_for(session)
        return None


def _ensure_home(codex_home: Path) -> None:
    """Załóż katalog domu, jeżeli go nie ma. Nic do niego nie zapisuje.

    0700, bo w tym katalogu mieszka `auth.json` (L2199-2203). Istniejącego katalogu nie dotykamy
    ani w prawach, ani w treści: to jest dom POŁĄCZENIA, wspólny z pilotem.
    """
    with contextlib.suppress(OSError):
        codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)


def _turn_overrides(capability_file: Path, *, web_search: str = WEB_SEARCH_OFF) -> tuple[str, ...]:
    """Konfiguracja TEJ tury jako argumenty `--config`. Nic nie jest zapisywane na dysk.

    TO JEST POPRAWKA E9 Z 21.09 i jej powód jest jednym zdaniem: dom Codexa należy do
    POŁĄCZENIA, a wpis serwera narzędzi do TURY. Wcześniej każda tura przepisywała wspólny
    `config.toml` (`prepare_codex_home`), więc dwie rozmowy jednej organizacji nadpisywały
    sobie ścieżkę do pliku zdolności nawzajem. Gorszym skutkiem nie był nawet martwy wpis po
    turze, który wywracał następne tury w tym domu, tylko wpis ŻYWY, ale cudzy: tura dostawała
    wtedy narzędzia z cudzą zdolnością, czyli cudzy zasięg projektu.

    Zmierzone przed napisaniem tego kodu, na przypiętej binarce 0.147.0
    (`codex_runtime.config_probe --mcp-overrides`, przypadek `zestaw-produkcyjny`): dokładnie ten
    zestaw argumentów wraca z `config/read` co do pola, serwer narzędzi naprawdę wstaje, widzi
    swoje `env`, a `web_search` i flagi `[features]` przyjmują wartości z argumentów, nie z pliku.

    Flagi `[features]` jadą tu MIMO że stoją w domu, bo dotąd zerowało je przepisanie pliku; to
    są flagi, które dają modelowi powłokę, więc lepiej powtórzyć je w argumentach, niż liczyć na
    to, że pliku nikt nie tknął.
    """
    from codex_runtime.hardening import HardeningConfig, McpServerConfig, turn_config_overrides

    from control_plane.observe.capsule_session import capsule_env
    from control_plane.tools import CATALOG, ENV_CAPABILITY_FILE

    return turn_config_overrides(
        HardeningConfig(
            web_search=web_search,
            mcp_servers=(
                McpServerConfig(
                    name="wenext",
                    # `sys.executable`, not `shutil.which("python3")`: the tool server has to
                    # import `control_plane` and `asyncpg`, and the only interpreter guaranteed
                    # to have both is the one already running this process. On the host a
                    # system `python3` would be found first and would fail on the import.
                    command=sys.executable,
                    args=("-m", "control_plane.tools.mcp_server"),
                    # The exact list, never empty: L2502. Generated from the catalog so a tool
                    # added there cannot be silently unreachable, nor silently reachable.
                    enabled_tools=tuple(spec.name for spec in CATALOG),
                    # PLIK ZDOLNOŚCI I KAPSUŁA. Serwer narzędzi jest osobnym procesem i widzi
                    # DOKŁADNIE to, co mu tu podamy. Do 20.09 dostawał samą ścieżkę zdolności,
                    # więc `accounts.status` nie miało czym zapytać kapsuły i mówiło „brak
                    # połączenia" o koncie, które w tej samej chwili czytało Reddita. Baza
                    # jedzie plikiem zdolności; kapsuła potrzebuje swoich trzech zmiennych.
                    env={ENV_CAPABILITY_FILE: str(capability_file), **capsule_env()},
                ),
            ),
        )
    )


def _session_of_payload(payload_ref: str | None) -> UUID | None:
    if not payload_ref or not payload_ref.startswith("session/"):
        return None
    try:
        return UUID(payload_ref.split("/", 1)[1])
    except ValueError:
        return None
