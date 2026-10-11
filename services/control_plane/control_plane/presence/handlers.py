"""`presence.tick`: one cycle of the agent working while nobody is watching (P4).

WHAT ONE TICK IS. Look at what changed, decide whether that is worth a model turn, and if it is,
run one presence session. The model issues a selected draft through `drafts.publish`, whose
delegation checks remain authoritative. A draft alone is never a publication request. Each tick ends
with a row in `wakeups`, including the ticks that did nothing: "there was nothing to do" is an
answer, and a table that only records the expensive ticks cannot answer "why did it stay quiet
all afternoon".

THE FOUR GATES BEFORE THE MODEL RUNS, in the order they are cheapest to check:

  1. the project is not paused, and neither is its delegation. The operator's stop must work
     while they are asleep, which means it is read here and not cached anywhere;
  2. something happened that this project has not already been woken for (the collector, and
     `wakeup_events`' UNIQUE constraint behind it);
  3. the model-turn budget of the delegation is not spent;
  4. the project has a Codex connection to run on. Without one the tick is honest about it
     instead of opening a session that cannot move.

Only then does a PRESENCE session open. It is a real session, the same rows and the same tools as
a conversation, and that is deliberate: after the fact a person can open it and read exactly what
the agent did, in the same screen where they read the sessions they had themselves.

WHY THE TURN RUNS INSIDE THIS JOB rather than being enqueued as `session.turn`. A tick is one
story: woken for this, thought that, proposed this, spent that. Enqueued, the cost and the
outcome would land in a later tick and `wakeups.cost` would trail one cycle behind reality. The
dispatcher runs jobs one at a time either way, so nothing is gained by splitting it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.ops.limits import org_spend
from control_plane.sessions.handlers import SessionTurnHandlers
from control_plane.sessions.store import SESSION_PRESENCE, SessionRefusal, SessionStore

from . import budget as budget_module
from .approval import DelegatedApprover
from .collector import MAX_EVENTS_PER_TICK, EventCollector
from .scope import MODE_AUTONOMOUS
from .store import (
    EVENT_DUE,
    REASON_DUE,
    REASON_NEW_EVENT,
    SKIPPED_BUDGET,
    SKIPPED_ENGINE_LIMIT,
    SKIPPED_ERROR,
    SKIPPED_NO_CONNECTION,
    SKIPPED_NO_EVENTS,
    SKIPPED_PILOT_OWNS_ACCOUNT,
    PresenceStore,
)

_LOG = logging.getLogger(__name__)

JOB_PRESENCE_TICK = "presence.tick"
PRESENCE_PRIORITY = 55
ATTEMPT_LIMIT = 1

#: How often a delegated project looks around. Ten minutes is the pilot's publish cadence and the
#: same reasoning applies (D-030): a reply is worth most while the post is still on `new`, and
#: below ten minutes the reads cost more than the freshness is worth.
TICK_INTERVAL_SECONDS = 600

#: How far back a tick looks when the project has never been woken. A day, so that switching a
#: project on does not replay a month of threads as if they were news.
FIRST_LOOK = timedelta(hours=24)

OUTCOME_WORKED = "PRACOWAL"
OUTCOME_QUIET = "CISZA"
OUTCOME_PAUSED = "WSTRZYMANY"
OUTCOME_BUDGET = "BUDZET"
OUTCOME_NO_CONNECTION = "BRAK_POLACZENIA"
#: 26.09: the Codex account is at its usage limit; the tick waits for the reset without a model.
OUTCOME_ENGINE_LIMIT = "LIMIT_SILNIKA"
#: P4 etap B: konto tego projektu jest jeszcze kontem żywej polityki pilota.
OUTCOME_PILOT = "KONTO_U_PILOTA"
OUTCOME_FAILED = "BLAD"


@dataclass(frozen=True)
class TickResult:
    """What one tick did. Returned for tests and logs; `wakeups` is the record."""

    outcome: str
    detail: str
    project_id: UUID
    wakeup_id: UUID | None = None
    session_id: UUID | None = None
    events: int = 0
    issued: int = 0


async def schedule_tick(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    delay_seconds: float = TICK_INTERVAL_SECONDS,
) -> Any:
    """One `presence.tick` per project per interval slot, idempotent within the slot.

    Keyed by the slot rather than by the project alone, exactly as `schedule_publish` is: two
    schedulers agreeing on the next cycle enqueue one job, and a tick that takes longer than the
    interval does not pile a queue of its successors behind itself.
    """
    slot = int((datetime.now(UTC) + timedelta(seconds=delay_seconds)).timestamp())
    slot //= TICK_INTERVAL_SECONDS
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_PRESENCE_TICK,
        routing_key="observe",
        priority=PRESENCE_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"presence:{project_id}:{slot}",
        payload_ref=f"presence/{project_id}",
        delay_seconds=max(0.0, delay_seconds),
    )


async def seed_presence_chain(pool: asyncpg.Pool, *, organization_id: UUID) -> int:
    """Zasiej tik obecności dla każdego powierzonego projektu tej organizacji.

    PIERWSZE OGNIWO, dokładnie jak w `ops/handlers.py::seed_ops_chain` i z tego samego powodu:
    `tick_job` zamawia swój następny bieg sam, więc łańcuch podtrzymuje się bez pomocy, ale nie
    zaczyna się sam. Projekt z podpisaną delegacją stałby powierzony i nigdy nie ruszył.

    PER PROJEKT, a nie raz na organizację, bo `presence.tick` niesie projekt w `payload_ref`.
    Lista bierze się z delegacji nieodwołanych, więc projekt, któremu ktoś dopiero co podpisał
    zakres, dołącza przy najbliższym starcie procesu albo przy przebudowie grupy zadań.

    Idempotentne po kluczu slotu: restart dołącza do istniejącego łańcucha zamiast budować drugi.
    Zwraca liczbę zasianych projektów, żeby wołający mógł to zalogować zamiast zgadywać.
    """
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    from .store import PresenceStore

    delegated = await PresenceStore(pool).delegated_projects(organization_id=organization_id)
    queue = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
    for one in delegated:
        await schedule_tick(
            queue,
            organization_id=organization_id,
            workspace_id=one.workspace_id,
            project_id=one.project_id,
            delay_seconds=0.0,
        )
    return len(delegated)


def _project_of_payload(payload_ref: str | None) -> UUID | None:
    if not payload_ref or not payload_ref.startswith("presence/"):
        return None
    try:
        return UUID(payload_ref.split("/", 1)[1])
    except ValueError:
        return None


class PresenceHandlers:
    """The `presence.tick` handler and the one-cycle logic behind it."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        presence: PresenceStore,
        sessions: SessionStore,
        turns: SessionTurnHandlers,
        approver: DelegatedApprover,
        collector: EventCollector,
        queue: JobQueuePort,
        organization_id: UUID,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._presence = presence
        self._sessions = sessions
        self._turns = turns
        self._approver = approver
        self._collector = collector
        self._queue = queue
        self._organization_id = organization_id
        self._clock = clock

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {JOB_PRESENCE_TICK: self.tick_job}

    async def tick_job(self, claim: ClaimedJob) -> None:
        project_id = _project_of_payload(claim.payload_ref)
        if project_id is None:
            _LOG.warning("presence.tick bez projektu w payload_ref=%r", claim.payload_ref)
            return
        # The hour's X collection is asked for BEFORE the turn (27.09): this tick awaits the
        # model's whole turn, so a collection asked for after it arrived when the hour was over.
        current = await self._presence.delegation_for(
            organization_id=self._organization_id, project_id=project_id
        )
        if current is not None and current.live:
            await self._ask_for_x_collection(current, project_id)
        result = await self.run_once(project_id, self._clock())
        _LOG.info("presence.tick %s: %s (%s)", project_id, result.outcome, result.detail[:200])
        # The next cycle is scheduled whatever happened, INCLUDING after a failure: a project
        # that stops ticking because one read timed out is a project that silently stops being
        # present, and that is the failure this milestone exists to prevent.
        delegation = await self._presence.delegation_for(
            organization_id=self._organization_id, project_id=project_id
        )
        if delegation is not None:
            await schedule_tick(
                self._queue,
                organization_id=self._organization_id,
                workspace_id=delegation.workspace_id,
                project_id=project_id,
            )

    async def _ask_for_x_collection(self, delegation: Any, project_id: UUID) -> None:
        """This hour's X collection for a delegated project with a ready X account (27.09).

        Asked on every tick and enqueued once per hour slot (`x_trends.schedule_collect`), so a
        freshly signed delegation starts collecting within one tick and a restart joins the slot.
        A failure here is logged and never ends the tick: presence matters more than trends.
        """
        from .x_trends import schedule_collect

        try:
            async with self._pool.acquire() as conn:
                has_x = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM project_account_assignments x
                          JOIN social_accounts a
                            ON a.organization_id = x.organization_id
                           AND a.id = x.social_account_id
                         WHERE x.organization_id = $1 AND x.project_id = $2
                           AND x.unassigned_at IS NULL AND a.platform = 'X'
                           AND a.state = 'READY')
                    """,
                    self._organization_id,
                    project_id,
                )
            if has_x:
                await schedule_collect(
                    self._queue,
                    organization_id=self._organization_id,
                    workspace_id=delegation.workspace_id,
                    project_id=project_id,
                    now=self._clock(),
                )
        except Exception:
            _LOG.exception("presence.tick %s: zamówienie zbioru X nieudane", project_id)

    async def _engine_limited_until(self, connection_id: UUID) -> datetime | None:
        """When the Codex account behind this connection comes back, if it is limited NOW.

        Read from the sessions' own records of the limit (`LIMIT_KONTA_CODEX` with
        `odnowi_sie`, written by `SessionTurnHandlers._rate_limited` from the engine's answer),
        compared with the database clock. `None` when no recorded limit is still in force.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT max((e.payload ->> 'odnowi_sie')::timestamptz)
                  FROM session_events e
                  JOIN agent_sessions s
                    ON s.organization_id = e.organization_id AND s.id = e.session_id
                 WHERE e.organization_id = $1
                   AND s.codex_connection_id = $2
                   AND e.kind = 'STATUS'
                   AND e.payload ->> 'powod' = 'LIMIT_KONTA_CODEX'
                   AND e.payload ? 'odnowi_sie'
                   AND e.created_at > now() - interval '8 days'
                HAVING max((e.payload ->> 'odnowi_sie')::timestamptz) > now()
                """,
                self._organization_id,
                connection_id,
            )

    async def _note_expiry(self, delegation: Any) -> bool:
        """Record, ONCE, that this delegation reached its deadline, and stop background work.

        Returns whether this call was the one that recorded it. The marker is the audit row
        itself (`PROJECT_DELEGATION_EXPIRED` for `delegation:<id>`), read and written in one
        transaction under the project's advisory lock, so two ticks racing each other write one
        row. Interrupts go to PRESENCE sessions only: a conversation is the human talking, and
        the deadline ends what the agent does without them, not the conversation.
        """
        from control_plane.audit import AuditWriter
        from control_plane.sessions.store import EVENT_INTERRUPT

        subject = f"delegation:{delegation.id}"
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"expiry:{subject}"
            )
            already = await conn.fetchval(
                "SELECT 1 FROM audit_events WHERE organization_id = $1 AND kind = $2 "
                "AND subject_ref = $3 LIMIT 1",
                self._organization_id,
                "PROJECT_DELEGATION_EXPIRED",
                subject,
            )
            if already:
                return False
            await AuditWriter(conn).record(
                kind="PROJECT_DELEGATION_EXPIRED",
                organization_id=self._organization_id,
                workspace_id=delegation.workspace_id,
                subject_ref=subject,
                actor_ref="system:presence.tick",
                payload={
                    "delegation_id": str(delegation.id),
                    "project_id": str(delegation.project_id),
                    "mode": delegation.mode,
                    "payload_hash": delegation.scope_sha256,
                },
            )
            running = await conn.fetch(
                "SELECT id FROM agent_sessions WHERE organization_id = $1 AND project_id = $2 "
                "AND kind = $3 AND finished_at IS NULL",
                self._organization_id,
                delegation.project_id,
                SESSION_PRESENCE,
            )
        for row in running:
            try:
                await self._sessions.append_event(
                    organization_id=self._organization_id,
                    session_id=row["id"],
                    kind=EVENT_INTERRUPT,
                    payload={"detail": "powierzenie wygasło"},
                )
            except Exception:  # finished between the read and here: nothing left to stop
                continue
        _LOG.warning(
            "delegacja %s projektu %s wygasła o %s; praca w tle zatrzymana",
            delegation.id,
            delegation.project_id,
            delegation.expires_at,
        )
        return True

    # -- one cycle -------------------------------------------------------------------------

    async def run_once(self, project_id: UUID, now: datetime) -> TickResult:
        delegation = await self._presence.delegation_for(
            organization_id=self._organization_id, project_id=project_id
        )
        if delegation is None:
            return TickResult(
                OUTCOME_PAUSED, "projekt nie ma delegacji; nie ma czego wykonywać", project_id
            )
        if delegation.expired:
            # 0033: THE PRODUCT STOPS AT THE DEADLINE, not a human looking at a clock. Recorded
            # once (audit) and whatever still runs in the background is asked to stop, the same
            # way a pause asks it; the next ticks see `not live` and do nothing.
            await self._note_expiry(delegation)
        if not delegation.live:
            # No `wakeups` row: a paused project ticking every ten minutes would write a hundred
            # rows a day saying the same thing, and drown the ones that mean something.
            return TickResult(OUTCOME_PAUSED, f"delegacja jest {delegation.state}", project_id)
        if await self._project_paused(project_id):
            return TickResult(OUTCOME_PAUSED, "projekt jest wstrzymany", project_id)

        since = await self._since(project_id, now)
        found = await self._collector.collect(delegation, since=since)
        # P4 etap C: powroty, do których agent sam się zobowiązał, są ZDARZENIEM jak każde inne.
        # Doklejane z przodu, bo obietnica złożona przez agenta ma pierwszeństwo przed cudzym
        # ruchem: jeśli limit tiku przytnie listę, ma przyciąć nowe posty, a nie to, co sam
        # zaplanował, bo tego nikt inny mu nie przypomni.
        due = await self._due_returns(delegation, project_id, now)
        found = (*due, *found)
        keys = {event.event_key for event in found}
        known: frozenset[str] = frozenset()
        if keys:
            known = frozenset(
                await self._presence.known_event_keys(
                    organization_id=self._organization_id, project_id=project_id, keys=keys
                )
            )
            found = tuple(event for event in found if event.event_key not in known)
        # LIMIT TIKU NAKŁADANY TUTAJ, po odsianiu znanych. Nadmiar nie jest zapisywany, więc
        # wraca w następnym tiku zamiast przepadać (audyt 19.09: „13. zdarzenie nie dotarło do
        # modelu"). Sto nowych postów w nocy to nie jest sto powodów do myślenia; to jest jeden
        # powód i dwanaście najnowszych postów w ręku.
        found = found[:MAX_EVENTS_PER_TICK]

        reason = REASON_NEW_EVENT if found else REASON_DUE
        wakeup = await self._presence.open_wakeup(
            organization_id=self._organization_id,
            workspace_id=delegation.workspace_id,
            project_id=project_id,
            reason=reason,
        )
        recorded = []
        for event in found:
            # `record_event` returns False when another collector got there first. That is the
            # deduplication doing its job, not an error, and the event simply is not ours.
            if await self._presence.record_event(
                organization_id=self._organization_id,
                workspace_id=delegation.workspace_id,
                project_id=project_id,
                wakeup_id=wakeup.id,
                kind=event.kind,
                event_key=event.event_key,
                payload=event.payload,
            ):
                recorded.append(event)

        # POWRÓT ZUŻYWA SIĘ WTEDY, GDY JEGO ZDARZENIE GDZIEŚ TRAFIŁO, i ani chwili wcześniej.
        # Zapisane znaczy obsłużone przez ten tik, nawet jeśli model nie ruszy: zdarzenie czeka
        # wtedy przy odłożonym obudzeniu i wróci z nim. Znane znaczy, że trafiło wcześniej.
        # Zużywanie wszystkich terminów z góry gubiło te, które limit tiku przyciął z listy:
        # termin przepadał, a agent nigdy nie wracał do sprawy, którą sam sobie obiecał.
        delivered = known
        for one in due:
            if one.event_key not in delivered:
                continue
            await self._presence.consume_return(
                UUID(one.payload["wakeup_id"]), organization_id=self._organization_id, now=now
            )

        if not recorded:
            await self._presence.close_skipped(
                wakeup.id,
                organization_id=self._organization_id,
                reason=SKIPPED_NO_EVENTS,
                detail="nic nowego od ostatniego przebudzenia; model nie był uruchamiany",
            )
            return TickResult(OUTCOME_QUIET, "nic nowego", project_id, wakeup_id=wakeup.id)

        async with self._pool.acquire() as conn:
            spent = await budget_module.spent(
                conn,
                organization_id=self._organization_id,
                project_id=project_id,
                accounts=delegation.scope.accounts,
                limits=budget_module.effective_limits(delegation),
                now=now,
            )
        # P7: DWA SUFITY, sprawdzane po kolei i nigdy sumowane. Delegacja ogranicza TEN projekt;
        # sufit organizacji ogranicza wszystkie razem, bo dziesięć projektów z limitem sześciu
        # publikacji to sześćdziesiąt publikacji, których nikt nie podpisał. Odmowa mówi, KTÓRY
        # sufit się wyczerpał, bo to jest różnica między „podnieś limit projektu" a „poczekaj".
        async with self._pool.acquire() as conn:
            organization = await org_spend(conn, organization_id=self._organization_id, now=now)
        org_exhausted = organization.exhausted()

        turns_allowed = spent.limits.model_turns_per_day
        over_project = turns_allowed is not None and spent.model_turns_today >= turns_allowed
        if over_project or org_exhausted is not None:
            detail = (
                org_exhausted
                if org_exhausted is not None
                else (
                    f"budżet wyczerpany, tury modelu: {spent.model_turns_today} z "
                    f"{turns_allowed} w ostatniej dobie"
                )
            )
            await self._presence.close_skipped(
                wakeup.id,
                organization_id=self._organization_id,
                reason=SKIPPED_BUDGET,
                detail=detail,
            )
            return TickResult(
                OUTCOME_BUDGET, detail, project_id, wakeup_id=wakeup.id, events=len(recorded)
            )

        # P4 etap B: CZY KONTO JEST JESZCZE ZAJĘTE PRZEZ PILOTA. Dopóki podpisana polityka pilota
        # żyje, pilot publikuje tym kontem. Druga ścieżka wydająca zgody na to samo konto
        # postawiłaby dwie nasze odpowiedzi pod jednym postem, każdą powołującą się na inny
        # podpis. Bramka stoi TUTAJ, przed sesją, a nie za nią: tura modelu, której wynik i tak
        # nie mógłby zostać wydany, jest budżetem wydanym na pewno na nic.
        #
        # Dotyczy WYŁĄCZNIE trybu AUTONOMOUS, bo tylko on wydaje zgody bez człowieka. RESEARCH
        # niczego nie publikuje, a REVIEW kończy się na człowieku, który widzi obie ścieżki i
        # sam rozstrzyga. Zatrzymanie ich byłoby zatrzymaniem pracy, która nie może zaszkodzić.
        if delegation.mode == MODE_AUTONOMOUS:
            owned_by_pilot = await self._pilot_owns_account(delegation, now)
            if owned_by_pilot is not None:
                await self._presence.close_skipped(
                    wakeup.id,
                    organization_id=self._organization_id,
                    reason=SKIPPED_PILOT_OWNS_ACCOUNT,
                    detail=owned_by_pilot,
                )
                return TickResult(
                    OUTCOME_PILOT,
                    owned_by_pilot,
                    project_id,
                    wakeup_id=wakeup.id,
                    events=len(recorded),
                )

        connection_id = await self._codex_connection(delegation.workspace_id)
        if connection_id is None:
            detail = "projekt nie ma połączenia Codex; sesja nie miałaby na czym działać"
            await self._presence.close_skipped(
                wakeup.id,
                organization_id=self._organization_id,
                reason=SKIPPED_NO_CONNECTION,
                detail=detail,
            )
            return TickResult(
                OUTCOME_NO_CONNECTION,
                detail,
                project_id,
                wakeup_id=wakeup.id,
                events=len(recorded),
            )

        # LIMIT KONTA CODEX (26.09). Tik z nowym zdarzeniem otwierał sesję także wtedy, gdy konto
        # silnika stało na limicie: sesja trafiała w limit, zamawiała własny powrót po odnowieniu
        # i zabierała projektowi turę z budżetu, a przy kilku zdarzeniach robiła to kilka razy.
        # Zdarzenia nie przepadają: pominięte przebudzenie nie ma sesji, więc następny tik po
        # odnowieniu zbierze je jeszcze raz.
        engine_back_at = await self._engine_limited_until(connection_id)
        if engine_back_at is not None:
            detail = (
                "konto Codex doszło do limitu; odnowi się o "
                f"{engine_back_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC i wtedy praca ruszy sama. "
                "To limit konta silnika, nie budżet projektu"
            )
            await self._presence.close_skipped(
                wakeup.id,
                organization_id=self._organization_id,
                reason=SKIPPED_ENGINE_LIMIT,
                detail=detail,
            )
            return TickResult(
                OUTCOME_ENGINE_LIMIT,
                detail,
                project_id,
                wakeup_id=wakeup.id,
                events=len(recorded),
            )

        goal = await self._goal(project_id, delegation, recorded)
        try:
            session = await self._sessions.create(
                organization_id=self._organization_id,
                workspace_id=delegation.workspace_id,
                project_id=project_id,
                goal=goal,
                budget=budget_module.effective_limits(delegation).to_json(),
                codex_connection_id=connection_id,
                kind=SESSION_PRESENCE,
            )
        except SessionRefusal as exc:
            detail = f"sesja nie powstała: {exc}"[:300]
            await self._presence.close_skipped(
                wakeup.id,
                organization_id=self._organization_id,
                reason=SKIPPED_ERROR,
                detail=detail,
            )
            return TickResult(OUTCOME_FAILED, detail, project_id, wakeup_id=wakeup.id)

        await self._presence.attach_session(
            wakeup.id, organization_id=self._organization_id, session_id=session.id
        )
        # A recorded event can still be deferred by budget or a missing connection. Consume
        # a scheduled return only after a real session owns it; otherwise it disappears forever.
        delivered_now = {event.event_key for event in recorded}
        for event in due:
            if event.event_key in delivered_now:
                await self._presence.consume_return(
                    UUID(event.payload["wakeup_id"]),
                    organization_id=self._organization_id,
                    now=now,
                )
        turn = await self._turns.run_once(session.id)

        # Do not sweep pending drafts into publication after a turn. The model may have
        # deliberately left a draft for later, changed direction, or stopped after a refusal.
        # Only its explicit drafts.publish call (or a review-mode user action) can issue it.
        counts = await self._sessions.counts_for_budget(
            organization_id=self._organization_id, session_id=session.id
        )
        await self._presence.record_cost(
            wakeup.id,
            organization_id=self._organization_id,
            cost={
                **counts,
                "wynik_tury": turn.outcome,
            },
        )
        return TickResult(
            OUTCOME_WORKED,
            f"{turn.outcome}: {turn.detail}"[:300],
            project_id,
            wakeup_id=wakeup.id,
            session_id=session.id,
            events=len(recorded),
        )

    # -- the small reads -------------------------------------------------------------------

    async def _pilot_owns_account(self, delegation, now: datetime) -> str | None:
        """Czy któreś konto z zakresu delegacji jest kontem ŻYWEJ polityki pilota.

        Zwraca zdanie dla człowieka albo `None`. Sprawdzane przy każdym tiku, nie raz na starcie:
        polityka wygasa o konkretnej godzinie (D-029: 24.09) i od tej chwili bramka ma zniknąć
        sama, bez niczyjej interwencji. Odwrotnie też: polityka podpisana w trakcie zatrzymuje
        wydawanie od następnego tiku.

        Porównanie idzie po UCHWYCIE konta, bo polityka pilota trzyma nazwę konta, a nie jego
        identyfikator (0008). To jest ta sama tożsamość widziana z dwóch stron systemu.
        """
        if not delegation.scope.accounts:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.account_handle, p.valid_until
                  FROM publication_policies p
                 WHERE p.organization_id = $1 AND p.status = 'ACTIVE'
                   AND p.valid_from <= $2 AND p.valid_until > $2
                   AND p.account_handle IN (
                       SELECT a.external_account_id FROM social_accounts a
                        WHERE a.organization_id = $1 AND a.id = ANY($3::uuid[]))
                 ORDER BY p.signed_at DESC LIMIT 1
                """,
                self._organization_id,
                now,
                list(delegation.scope.accounts),
            )
        if row is None:
            return None
        return (
            f"konto {row['account_handle']} jest zajęte przez pilota do "
            f"{row['valid_until'].isoformat()}; sesja pracowała, ale zgody nie są wydawane, "
            "żeby dwie ścieżki nie pisały tym samym kontem"
        )

    async def _due_returns(self, delegation, project_id: UUID, now: datetime) -> tuple:
        """Zaplanowane powroty, których termin minął, jako zdarzenia z notatką agenta."""
        from .collector import Event

        planned = await self._presence.due_returns(
            organization_id=self._organization_id, project_id=project_id, now=now
        )
        events = []
        # A deferred event may have moved to another wake-up. The return's stable key
        # still owns its note; reading only events attached to the original row loses it.
        notes = await self._presence.notes_of_returns([one.id for one in planned])
        for one in planned:
            note = notes.get(one.id, "")
            events.append(
                Event(
                    EVENT_DUE,
                    f"{EVENT_DUE}:{one.id}",
                    {
                        "notatka": note or "(bez notatki)",
                        "zaplanowano": one.created_at.isoformat(),
                        "termin": one.due_at.isoformat() if one.due_at else "",
                        # Niesione, żeby tik wiedział, który wiersz oznaczyć jako obsłużony.
                        "wakeup_id": str(one.id),
                    },
                )
            )
        return tuple(events)

    async def _project_paused(self, project_id: UUID) -> bool:
        """`projects.paused_at` is a SECOND stop, next to the delegation's.

        Two stops and no attempt to unify them: the project's pause is what P1's screen writes
        and the delegation's is what the delegation screen writes, and a person who used either
        of them meant "stop". Honouring both can only ever stop more, never less.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT paused_at, archived_at FROM projects "
                "WHERE organization_id = $1 AND id = $2",
                self._organization_id,
                project_id,
            )
        return row is None or row["paused_at"] is not None or row["archived_at"] is not None

    async def _since(self, project_id: UUID, now: datetime) -> datetime:
        """Jak daleko wstecz patrzy kolektor. ZAWSZE `FIRST_LOOK`, nie „od ostatniego obudzenia".

        Okno „od ostatniego obudzenia" było optymalizacją, która GUBIŁA PRACĘ, i audyt z 19.09
        zmierzył to jako „13. zdarzenie nie dotarło do modelu": tik zebrał dwanaście zdarzeń z
        trzynastu, zapisał obudzenie, a następny tik zaczynał okno OD TEGO OBUDZENIA, więc
        trzynasty wątek, obserwowany wcześniej, wypadał z zakresu na zawsze.

        Teraz okno jest stałe, a odsiewaniem zajmuje się deduplikacja po kluczu, która i tak
        musi działać. Cena to szersze zapytanie raz na dziesięć minut, z LIMIT-em w kolektorze;
        zysk to brak klasy błędów, w której praca znika, bo poprzedni tik był zajęty.
        """
        return now - FIRST_LOOK

    async def _codex_connection(self, workspace_id: UUID) -> UUID | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM codex_connections WHERE organization_id = $1 "
                "AND (workspace_id = $2 OR workspace_id IS NULL) "
                "AND disconnected_at IS NULL AND status = 'CONNECTED' "
                "ORDER BY (workspace_id IS NOT NULL) DESC, created_at DESC LIMIT 1",
                self._organization_id,
                workspace_id,
            )

    async def _mandate(self, project_id: UUID, delegation) -> str:
        """What the signature allows and what is left of it, read from the signed delegation.

        27.09: a background session published the day's first X post and then planned nothing for
        twelve hours. It knew "AUTONOMOUS" and nothing else: not that the signature covered
        replies and a second post, nor how much of the day's budget was left. These are the
        founder's signed numbers, the same the approver counts against; no advice added.
        """
        names = {
            "OWN_POST": "własne posty",
            "REPLY": "odpowiedzi w cudzych wątkach",
            "RESEARCH": "research",
        }
        actions = ", ".join(names.get(one, one) for one in delegation.scope.actions) or "brak"
        limits = budget_module.effective_limits(delegation)
        async with self._pool.acquire() as conn:
            spent = await budget_module.spent(
                conn,
                organization_id=self._organization_id,
                project_id=project_id,
                accounts=delegation.scope.accounts,
                limits=limits,
                now=self._clock(),
            )
        turns = (
            f"{spent.model_turns_today} z {limits.model_turns_per_day}"
            if limits.model_turns_per_day is not None
            else f"{spent.model_turns_today}, bez limitu"
        )
        blocked = spent.publication_blocked()
        deadline = (
            f"; ważne do {delegation.expires_at:%Y-%m-%d %H:%M} UTC"
            if delegation.expires_at is not None
            else ""
        )
        return (
            f"Zakres powierzenia: {actions}. W ostatniej dobie: publikacje "
            f"{spent.publications_today} z {limits.publications_per_day} "
            f"({spent.publications_this_hour} z {limits.publications_per_hour} w godzinie), "
            f"własne posty {spent.own_posts_today} z {limits.own_posts_per_day}, "
            f"tury modelu {turns}{deadline}." + (f" Teraz: {blocked}." if blocked else "")
        )

    async def _goal(self, project_id: UUID, delegation, events) -> str:
        """The goal of a presence session: the project's own words, then what woke it.

        The project's goal comes FIRST and the events after it, because the events are the
        occasion and the goal is the reason. A session opened the other way round tends to
        produce a reply to the newest post rather than work on what the project is for.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, goal FROM projects WHERE organization_id = $1 AND id = $2",
                self._organization_id,
                project_id,
            )
        standing = (row["goal"] if row else "") or "(cel projektu nie został zapisany)"
        lines = [
            f"Projekt: {row['name'] if row else project_id}",
            f"Cel powierzony przez człowieka: {standing}",
            f"Tryb: {delegation.mode}. Społeczności w zakresie: "
            + (", ".join(delegation.scope.communities) or "żadne"),
            await self._mandate(project_id, delegation),
            "",
            "Co się wydarzyło od ostatniego przebudzenia:",
        ]
        for event in events:
            lines.append(f"  - {event.kind}: {_describe(event)}")
        lines += [
            "",
            "Nikt teraz nie patrzy na ekran. Zrób to, co jest sensowne wobec celu: odpowiedz, "
            "przygotuj tekst, zapisz wniosek w Materiałach albo świadomie nic nie rób i napisz "
            "dlaczego. Korzystaj z doświadczeń poprzednich publikacji w memory.search/read, "
            "obserwacji i faktycznych odpowiedzi. Liczba polubień nie dowodzi popytu: "
            "oceń jakość rozmów, etap produktu, dopasowanie odbiorców i cel projektu. "
            "Własny post ma wnosić nową wartość wynikającą ze źródeł lub wniosków, "
            "a nie powielać ostatni tekst. Zapisz istotny wniosek z podstawą, aby kolejna "
            "sesja mogła go sprawdzić. Zaplanuj powrót tylko gdy ma konkretny cel i termin.",
            (
                "Masz podpisane powierzenie AUTONOMOUS: przygotuj wybrane posty przez "
                "draft.create i wydaj je przez drafts.publish w jego zakresie. Nie czekaj "
                "na akceptację każdej treści. Kolejka nie jest jeszcze publikacją; sprawdź wynik."
                if delegation.mode == MODE_AUTONOMOUS
                else "W trybie REVIEW przygotuj propozycję do przeglądu człowieka; "
                "w RESEARCH badaj i zapisuj wnioski bez publikowania."
            ),
        ]
        return "\n".join(lines)


def _describe(event) -> str:
    payload = event.payload or {}
    parts = [str(value) for value in payload.values() if value]
    return "; ".join(parts)[:400] or event.event_key
