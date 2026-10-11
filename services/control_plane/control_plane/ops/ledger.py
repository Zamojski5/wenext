"""Skąd biorą się liczby w `cost_ledger` (P7 etap B).

ZASADA: KSIĘGA JEST WYPROWADZANA, NIE PISANA RĘCZNIE. Każdy wiersz powstaje z czegoś, co już się
wydarzyło i ma własny wiersz w bazie: obudzenie, które uruchomiło model; wywołanie narzędzia w
śladzie sesji; próba wykonania, która dotknęła platformy. Dzięki temu księgi nie da się rozjechać
z rzeczywistością przez pominięte wywołanie `record_cost`, bo nikt jej nie woła: `refill` czyta
te same tabele co raporty.

DLATEGO TEŻ JEST IDEMPOTENTNA. `UNIQUE (organization_id, kind, source_ref)` znaczy, że ponowne
zasilanie po awarii, po restarcie albo dwa razy z rzędu nie podwaja rachunku. To jest warunek, bez
którego „zasilanie z tabel" byłoby gorsze od ręcznego zapisu: ręczny zapis przynajmniej dzieje się
raz.

JEDNOSTKI, NIE PIENIĄDZE (0020). Tura modelu to 1 tura. Ile ona kosztuje w złotówkach, zależy od
cennika, który zmienia się bez naszego udziału i wstecz; raport mnoży, księga liczy.

CZEGO TU NIE MA: kosztu kapsuły i kosztu bazy. Nie mamy ich skąd wziąć bez zgadywania, a
zgadnięta liczba w księdze kosztów jest gorsza niż jej brak, bo wygląda jak pomiar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from .limits import KIND_FETCH, KIND_MODEL_TURN, KIND_PUBLICATION, KIND_TOOL_CALL

#: How far back one refill looks by default. A day: the report reads a week, and a refill that
#: swept a week every time would rewrite rows that have not changed since.
DEFAULT_WINDOW = timedelta(hours=24)

#: Attempt states that mean the attempt reached the platform, the same list `presence/budget.py`
#: uses and for the same reason (D-035): a refusal is a page load the platform saw.
TOUCHED_THE_PLATFORM = ("SUBMIT_STARTED", "CONFIRMED", "UNKNOWN")


@dataclass(frozen=True)
class Refill:
    """How many rows each kind contributed. Zeros are reported, not omitted: a kind that
    produced nothing is a fact about the week, and a missing key would read as a bug."""

    model_turns: int = 0
    tool_calls: int = 0
    publications: int = 0
    fetches: int = 0

    @property
    def total(self) -> int:
        return self.model_turns + self.tool_calls + self.publications + self.fetches

    def to_json(self) -> dict[str, int]:
        return {
            "tury_modelu": self.model_turns,
            "wywolania_narzedzi": self.tool_calls,
            "publikacje": self.publications,
            "pobrania": self.fetches,
            "razem": self.total,
        }


async def refill(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    since: datetime | None = None,
    now: datetime | None = None,
) -> Refill:
    """Derive ledger rows from what happened, and say how many of each kind landed.

    Each statement is `INSERT ... SELECT ... ON CONFLICT DO NOTHING`, so running it twice writes
    nothing the second time and running it after a crash finishes the job. The count returned is
    of rows ACTUALLY inserted, which is why a second run reports zeros rather than repeating the
    first run's numbers.
    """
    moment = now or datetime.now(UTC)
    window_start = since or (moment - DEFAULT_WINDOW)
    async with pool.acquire() as conn, conn.transaction():
        turns = await _insert(
            conn,
            """
            INSERT INTO cost_ledger
                (organization_id, project_id, kind, units, source_ref, occurred_at)
            SELECT w.organization_id, w.project_id, $2, 1, 'wakeup:' || w.id, w.created_at
              FROM wakeups w
             WHERE w.organization_id = $1 AND w.created_at >= $3 AND w.session_id IS NOT NULL
            ON CONFLICT (organization_id, kind, source_ref) DO NOTHING
            """,
            organization_id,
            KIND_MODEL_TURN,
            window_start,
        )
        # Tool calls come from the session trace, which is the only place that knows a tool was
        # reached for. `project_id` travels with them: a tool call belongs to the goal it served.
        calls = await _insert(
            conn,
            """
            INSERT INTO cost_ledger
                (organization_id, project_id, kind, units, source_ref, occurred_at)
            SELECT e.organization_id, s.project_id, $2, 1, 'event:' || e.id, e.created_at
              FROM session_events e
              JOIN agent_sessions s
                ON s.id = e.session_id AND s.organization_id = e.organization_id
             WHERE e.organization_id = $1 AND e.created_at >= $3 AND e.kind = 'TOOL_CALL'
            ON CONFLICT (organization_id, kind, source_ref) DO NOTHING
            """,
            organization_id,
            KIND_TOOL_CALL,
            window_start,
        )
        # A publication costs its slot when it TOUCHED the platform, not when it succeeded: a
        # refusal is a page load the platform saw (D-035). `project_id` is NULL for pilot rows,
        # and that is correct rather than missing: the pilot has no project.
        publications = await _insert(
            conn,
            """
            INSERT INTO cost_ledger
                (organization_id, project_id, kind, units, source_ref, occurred_at)
            SELECT a.organization_id, q.project_id, $2, 1, 'attempt:' || a.id, a.started_at
              FROM execution_attempts a
              LEFT JOIN publication_requests q ON q.attempt_id = a.id
             WHERE a.organization_id = $1 AND a.started_at >= $3
               AND a.status = ANY($4::text[])
            ON CONFLICT (organization_id, kind, source_ref) DO NOTHING
            """,
            organization_id,
            KIND_PUBLICATION,
            window_start,
            list(TOUCHED_THE_PLATFORM),
        )
        fetches = await _insert(
            conn,
            """
            INSERT INTO cost_ledger
                (organization_id, project_id, kind, units, source_ref, occurred_at)
            SELECT s.organization_id, s.project_id, $2, 1, 'source:' || s.id, s.fetched_at
              FROM knowledge_sources s
             WHERE s.organization_id = $1 AND s.fetched_at >= $3
            ON CONFLICT (organization_id, kind, source_ref) DO NOTHING
            """,
            organization_id,
            KIND_FETCH,
            window_start,
        )
    return Refill(model_turns=turns, tool_calls=calls, publications=publications, fetches=fetches)


async def _insert(conn: asyncpg.Connection, sql: str, *args) -> int:
    """Run one derivation and return how many rows it actually wrote.

    asyncpg hands back the command tag (`INSERT 0 12`); the second number is what `ON CONFLICT
    DO NOTHING` left, so it counts new rows rather than matched ones.
    """
    tag = await conn.execute(sql, *args)
    try:
        return int(str(tag).rsplit(" ", 1)[-1])
    except ValueError:  # an unexpected tag shape is not worth failing a refill over
        return 0
