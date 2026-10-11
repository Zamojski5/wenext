"""Reads and writes of migration 0017: delegations and wake-ups (P4).

TWO RULES THIS MODULE KEEPS, both of which the database also enforces, and the duplication is
deliberate in exactly one direction: the database refuses, this module explains.

1. **A delegation is live only while it is neither revoked nor paused.** `live_delegation`
   returns `None` for both, and the caller cannot tell them apart from the return value alone.
   That is on purpose for the decision path: a paused delegation issues no approvals and neither
   does a revoked one. The screen asks `delegation_for` instead, which returns the row with its
   timestamps, because a person does need to tell "for now" from "the end".

2. **An event wakes the agent once.** `record_event` inserts and lets the UNIQUE constraint
   refuse the second one, rather than asking first. A check-then-insert passes its own tests and
   loses the race that matters: two collectors both read "not there", both insert, and the agent
   wakes twice about the same post. The refusal is translated into `False`, which the caller
   reads as "already known", not as an error.

WHAT A WAKE-UP WITH NO SESSION MEANS. It means the model did not run, and that is the row this
table exists to make countable. `close_skipped` writes the reason; `attach_session` writes the
session. The schema forbids both at once, and there is no method here that could write both.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.approval.signers import SignerUnavailable, resolve_signer

from .scope import Budget, Scope, ScopeError, digest, parse_budget, parse_scope

REASON_NEW_EVENT = "NEW_EVENT"
REASON_DUE = "DUE"
REASON_USER_MESSAGE = "USER_MESSAGE"
REASON_RETRY = "RETRY"
WAKEUP_REASONS = (REASON_NEW_EVENT, REASON_DUE, REASON_USER_MESSAGE, REASON_RETRY)

EVENT_NEW_THREAD = "NEW_THREAD"
EVENT_REPLY = "REPLY"
EVENT_MODERATION = "MODERATION"
EVENT_DUE = "DUE"
EVENT_OUTCOME = "OUTCOME"
EVENT_KINDS = (EVENT_NEW_THREAD, EVENT_REPLY, EVENT_MODERATION, EVENT_DUE, EVENT_OUTCOME)

#: How many times an event may go back to the queue because its session failed before the model
#: did anything (27.09: a model the server refused ended a session in its first second, and the
#: reply that woke it was counted as handled). Bounded, so a failure that repeats cannot open a
#: session every ten minutes for ever.
EVENT_RETRY_LIMIT = 3

#: "This wakeup's session failed before the model produced anything": no answer, no tool call.
#: Such an event was never in front of a model, so it is postponed, not handled.
_FAILED_BEFORE_MODEL = """
    EXISTS (
        SELECT 1 FROM agent_sessions s
         WHERE s.id = w.session_id AND s.status = 'FAILED'
           AND NOT EXISTS (
               SELECT 1 FROM session_events x
                WHERE x.session_id = s.id
                  AND x.kind IN ('ASSISTANT_MESSAGE', 'TOOL_CALL')))
"""

#: Why a tick did not run the model. A closed list, because "nothing happened" is the most
#: common row in `wakeups` and an unbounded string there would make it uncountable.
SKIPPED_NO_EVENTS = "NO_EVENTS"
SKIPPED_NO_DELEGATION = "NO_DELEGATION"
SKIPPED_PAUSED = "PAUSED"
SKIPPED_BUDGET = "BUDGET_EXHAUSTED"
SKIPPED_NO_CONNECTION = "NO_CONNECTION"
#: 26.09: the Codex ACCOUNT behind the connection is at its usage limit. Not the project's budget
#: (`BUDGET_EXHAUSTED`): a different ceiling, a different owner and a different way out (wait for
#: the reset, which the detail names), so it is a different code.
SKIPPED_ENGINE_LIMIT = "ENGINE_LIMIT"
SKIPPED_ERROR = "ERROR"
#: P4 etap B: konto tego projektu jest jednocześnie kontem pilota, a polityka pilota jest wciąż
#: ACTIVE. Dwie ścieżki piszące tym samym kontem to dwie odpowiedzi pod jednym postem i dwa
#: różne dzienniki zgód na to samo. Bramka znika SAMA, gdy polityka wygaśnie albo zostanie
#: odwołana: nie ma tu niczego do odklikania.
SKIPPED_PILOT_OWNS_ACCOUNT = "PILOT_OWNS_ACCOUNT"
SKIPPED_REASONS = (
    SKIPPED_PILOT_OWNS_ACCOUNT,
    SKIPPED_NO_EVENTS,
    SKIPPED_NO_DELEGATION,
    SKIPPED_PAUSED,
    SKIPPED_BUDGET,
    SKIPPED_NO_CONNECTION,
    SKIPPED_ENGINE_LIMIT,
    SKIPPED_ERROR,
)


class PresenceRefusal(Exception):
    """A write the domain refuses, with a sentence the operator reads (D-010)."""


@dataclass(frozen=True)
class DelegationRow:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    mode: str
    scope: Scope
    budget: Budget
    scope_sha256: str
    signed_at: datetime
    signed_by: UUID | None
    paused_at: datetime | None
    revoked_at: datetime | None
    signed_membership_id: UUID | None = None
    signer_active: bool = True
    #: 0033: the signed deadline. `None` only for signatures older than the column.
    expires_at: datetime | None = None
    #: Computed by PostgreSQL in the same read (`expires_at <= now()`), never by this process's
    #: clock: the queue, the decision and the screen must agree on one moment (L2668).
    expired: bool = False

    @property
    def live(self) -> bool:
        """Not revoked, not paused, not past its deadline. The one predicate the decision path,
        the tick, the publisher and the tools consult; the deadline became part of it in 0033."""
        return (
            self.revoked_at is None
            and self.paused_at is None
            and self.signer_active
            and not self.expired
        )

    @property
    def state(self) -> str:
        """What a person should see. Four words, because they mean four different things."""
        if self.revoked_at is not None:
            return "odwołana"
        if self.expired:
            return "wygasła"
        if self.paused_at is not None or not self.signer_active:
            return "wstrzymana"
        return "aktywna"


@dataclass(frozen=True)
class WakeupRow:
    id: UUID
    project_id: UUID
    reason: str
    session_id: UUID | None
    skipped_reason: str | None
    skipped_detail: str | None
    #: 0023: kiedy agent obiecał wrócić. `None` znaczy obudzenie z obserwacji, wymagalne od razu.
    due_at: datetime | None
    cancelled_at: datetime | None
    cost: dict[str, Any] | None
    created_at: datetime
    consumed_at: datetime | None


@dataclass(frozen=True)
class WakeupEventRow:
    id: UUID
    wakeup_id: UUID
    kind: str
    event_key: str
    payload: dict[str, Any]
    created_at: datetime


_DELEGATION_COLUMNS = (
    "id, organization_id, workspace_id, project_id, mode, scope, budget, scope_sha256, "
    "signed_at, signed_by, paused_at, revoked_at, signed_membership_id, expires_at, "
    "(expires_at IS NOT NULL AND expires_at <= now()) AS expired, "
    "CASE WHEN signed_membership_id IS NOT NULL THEN EXISTS ("
    "SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
    "WHERE m.id=project_delegations.signed_membership_id "
    "AND m.organization_id=project_delegations.organization_id "
    "AND m.revoked_at IS NULL AND u.disabled_at IS NULL) ELSE EXISTS ("
    "SELECT 1 FROM operators o WHERE o.id=project_delegations.signed_by "
    "AND o.organization_id=project_delegations.organization_id "
    "AND o.disabled_at IS NULL) END AS signer_active"
)
_WAKEUP_COLUMNS = (
    "id, project_id, reason, session_id, skipped_reason, skipped_detail, due_at, "
    "cancelled_at, cost, created_at, consumed_at"
)


def _as_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _delegation_of(row: asyncpg.Record) -> DelegationRow:
    return DelegationRow(
        id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
        mode=row["mode"],
        scope=parse_scope(_as_json(row["scope"], {})),
        budget=parse_budget(_as_json(row["budget"], {})),
        scope_sha256=row["scope_sha256"],
        signed_at=row["signed_at"],
        signed_by=row["signed_by"],
        paused_at=row["paused_at"],
        revoked_at=row["revoked_at"],
        signed_membership_id=row["signed_membership_id"],
        signer_active=row["signer_active"],
        expires_at=row["expires_at"],
        expired=bool(row["expired"]),
    )


def _wakeup_of(row: asyncpg.Record) -> WakeupRow:
    return WakeupRow(
        id=row["id"],
        project_id=row["project_id"],
        reason=row["reason"],
        session_id=row["session_id"],
        skipped_reason=row["skipped_reason"],
        skipped_detail=row["skipped_detail"],
        due_at=row["due_at"],
        cancelled_at=row["cancelled_at"],
        cost=_as_json(row["cost"], None),
        created_at=row["created_at"],
        consumed_at=row["consumed_at"],
    )


class PresenceStore:
    """Holds a pool, never a connection: the same rule as every other store here."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- delegations --------------------------------------------------------------------

    async def sign(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        mode: str,
        scope: Scope,
        budget: Budget,
        operator_id: UUID,
        user_id: UUID | None = None,
        replaces: UUID | None = None,
        allow_own_posts: bool = False,
        expires_at: datetime | None = None,
    ) -> DelegationRow:
        """Sign a delegation. Refuses while a live one exists: revoke it first, deliberately.

        The digest is computed HERE from the mode, scope and budget being written, never taken
        as an argument. A caller that supplied it could name the digest of something else, and
        every decision citing this delegation compares against that column.

        TWO PLACES SPELL A MODE, AND THIS IS THE ONE THAT DECIDES (coordinator, 2026-09-19).
        `projects.mode` came with P1 and is a PREFERENCE a person edits on the project screen;
        `project_delegations.mode` is what they SIGNED, it is covered by the digest, and it is
        what the decision path reads. Signing writes the same value into `projects.mode` in THIS
        transaction, so the two cannot drift: a project screen showing AUTONOMOUS while the live
        signature says REVIEW would be a lie in the direction that publishes.
        """
        if expires_at is not None:
            # Seconds, as the digest takes them: the stored deadline and the signed one must be
            # the same instant, not two that differ by the microseconds of a clock.
            expires_at = expires_at.replace(microsecond=0)
        scope_digest = digest(mode=mode, scope=scope, budget=budget, expires_at=expires_at)
        async with self._pool.acquire() as conn, conn.transaction():
            try:
                legacy_signer, membership_id = await resolve_signer(
                    conn,
                    organization_id=organization_id,
                    operator_id=operator_id,
                    user_id=user_id,
                )
            except SignerUnavailable as exc:
                raise PresenceRefusal(str(exc)) from exc
            # A SIGNATURE PAST ITS DEADLINE IS HISTORY (0033). It still occupies the one "not
            # revoked" slot of the project (`project_delegations_one_live_per_project`), so a new
            # signature from a form that does not name it in `replaces` would be refused as a
            # second live delegation. It governs nothing any more; closing it here, in the same
            # transaction as the new signature, is what "sign again after expiry" means.
            closed_expired = {
                row["id"]
                for row in await conn.fetch(
                    "UPDATE project_delegations SET revoked_at = now() "
                    "WHERE organization_id = $1 AND workspace_id = $2 AND project_id = $3 "
                    "AND revoked_at IS NULL AND expires_at IS NOT NULL AND expires_at <= now() "
                    "RETURNING id",
                    organization_id,
                    workspace_id,
                    project_id,
                )
            }
            if replaces is not None and replaces not in closed_expired:
                replaced = await conn.fetchval(
                    "UPDATE project_delegations SET revoked_at=now() "
                    "WHERE organization_id=$1 AND workspace_id=$2 AND project_id=$3 "
                    "AND id=$4 AND revoked_at IS NULL RETURNING id",
                    organization_id,
                    workspace_id,
                    project_id,
                    replaces,
                )
                if replaced is None:
                    raise PresenceRefusal(
                        "Zakres zmienił się od przygotowania karty. Odśwież propozycję."
                    )
            if allow_own_posts:
                if mode == "RESEARCH" or "OWN_POST" not in scope.actions:
                    raise PresenceRefusal("Własne posty wymagają zakresu OWN_POST.")
                communities = await conn.fetch(
                    "SELECT id, own_posts_allowed FROM communities WHERE organization_id=$1 "
                    "AND workspace_id=$2 AND platform='REDDIT' "
                    "AND community_key=ANY($3::text[]) FOR UPDATE",
                    organization_id,
                    workspace_id,
                    list(scope.communities),
                )
                if len(communities) != len(set(scope.communities)):
                    raise PresenceRefusal("Najpierw poznaj i dodaj społeczności do obserwacji.")
                if any(row["own_posts_allowed"] is False for row in communities):
                    raise PresenceRefusal(
                        "Jedna ze społeczności ma wyłączone własne posty. Zmień tę decyzję osobno."
                    )
                await conn.execute(
                    "UPDATE communities SET own_posts_allowed=true, own_posts_decided_at=now(), "
                    "own_posts_decided_by=$4, own_posts_membership_id=$5 "
                    "WHERE organization_id=$1 AND workspace_id=$2 "
                    "AND id=ANY($3::uuid[]) AND own_posts_allowed IS NULL",
                    organization_id,
                    workspace_id,
                    [row["id"] for row in communities],
                    legacy_signer,
                    membership_id,
                )
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO project_delegations
                        (organization_id, workspace_id, project_id, mode, scope, budget,
                         scope_sha256, signed_by, signed_membership_id, expires_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
                    RETURNING {_DELEGATION_COLUMNS}
                    """,
                    organization_id,
                    workspace_id,
                    project_id,
                    mode,
                    json.dumps(scope.to_json(), ensure_ascii=False),
                    json.dumps(budget.to_json(), ensure_ascii=False),
                    scope_digest,
                    legacy_signer,
                    membership_id,
                    expires_at,
                )
                await conn.execute(
                    "UPDATE projects SET mode = $3 "
                    "WHERE organization_id = $1 AND workspace_id = $2 AND id = $4",
                    organization_id,
                    workspace_id,
                    mode,
                    project_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PresenceRefusal(
                    f"projekt {project_id} ma już żywą delegację. Odwołaj ją, zanim podpiszesz "
                    "nową: dwie żywe to dwa zakresy, na które ta sama publikacja mogłaby się "
                    "powołać"
                ) from exc
            except asyncpg.ForeignKeyViolationError as exc:
                raise PresenceRefusal(
                    f"projekt {project_id} nie należy do tego workspace'u albo operator "
                    f"{operator_id} nie należy do tej organizacji"
                ) from exc
            except asyncpg.CheckViolationError as exc:
                if "expires_after_signing" not in str(exc):
                    raise
                raise PresenceRefusal(
                    "termin powierzenia musi być późniejszy niż chwila podpisu"
                ) from exc
        return _delegation_of(row)

    async def delegation_for(
        self, *, organization_id: UUID, project_id: UUID
    ) -> DelegationRow | None:
        """The delegation of this project, live or paused. `None` when there is none at all.

        A REVOKED one is not returned: it is history, and the screen reads history through
        `delegation_history`. What this answers is "what governs this project now", and a
        revoked signature governs nothing.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_DELEGATION_COLUMNS} FROM project_delegations "
                "WHERE organization_id = $1 AND project_id = $2 AND revoked_at IS NULL",
                organization_id,
                project_id,
            )
        return _delegation_of(row) if row is not None else None

    async def live_delegation(
        self, *, organization_id: UUID, project_id: UUID
    ) -> DelegationRow | None:
        """The delegation that may currently authorise work: not revoked AND not paused."""
        found = await self.delegation_for(organization_id=organization_id, project_id=project_id)
        return found if found is not None and found.live else None

    async def delegation_history(
        self, *, organization_id: UUID, project_id: UUID, limit: int = 20
    ) -> tuple[DelegationRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_DELEGATION_COLUMNS} FROM project_delegations "
                "WHERE organization_id = $1 AND project_id = $2 ORDER BY signed_at DESC LIMIT $3",
                organization_id,
                project_id,
                limit,
            )
        return tuple(_delegation_of(r) for r in rows)

    async def set_paused(
        self, delegation_id: UUID, *, organization_id: UUID, paused: bool, at: datetime
    ) -> DelegationRow:
        """Pause or resume. Both directions through one write, so a pause always has a way out."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE project_delegations SET paused_at = $3
                 WHERE organization_id = $1 AND id = $2 AND revoked_at IS NULL
                RETURNING {_DELEGATION_COLUMNS}
                """,
                organization_id,
                delegation_id,
                at if paused else None,
            )
        if row is None:
            raise PresenceRefusal(
                f"nie ma żywej delegacji {delegation_id}; odwołanej nie da się wstrzymać "
                "ani wznowić"
            )
        return _delegation_of(row)

    async def revoke(
        self, delegation_id: UUID, *, organization_id: UUID, at: datetime
    ) -> DelegationRow:
        """Revoke. Nothing is deleted: the row stays, so a publication months old can still name
        the permission it was made under."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE project_delegations SET revoked_at = $3
                 WHERE organization_id = $1 AND id = $2 AND revoked_at IS NULL
                RETURNING {_DELEGATION_COLUMNS}
                """,
                organization_id,
                delegation_id,
                at,
            )
        if row is None:
            raise PresenceRefusal(f"nie ma żywej delegacji {delegation_id}")
        return _delegation_of(row)

    async def delegated_projects(self, *, organization_id: UUID) -> tuple[DelegationRow, ...]:
        """Every project with a delegation that is not revoked, paused ones included.

        Paused ones are returned because the tick still runs for them and writes a wake-up with
        `skipped_reason = PAUSED`. A project that simply vanished from this list would leave no
        record of the time it spent stopped.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_DELEGATION_COLUMNS} FROM project_delegations "
                "WHERE organization_id = $1 AND revoked_at IS NULL ORDER BY signed_at",
                organization_id,
            )
        return tuple(_delegation_of(r) for r in rows)

    # -- wake-ups -----------------------------------------------------------------------

    async def open_wakeup(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        reason: str,
    ) -> WakeupRow:
        if reason not in WAKEUP_REASONS:
            raise PresenceRefusal(f"powód {reason!r} poza listą {WAKEUP_REASONS}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO wakeups (organization_id, workspace_id, project_id, reason)
                VALUES ($1, $2, $3, $4)
                RETURNING {_WAKEUP_COLUMNS}
                """,
                organization_id,
                workspace_id,
                project_id,
                reason,
            )
        return _wakeup_of(row)

    async def record_event(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        wakeup_id: UUID,
        kind: str,
        event_key: str,
        payload: dict[str, Any],
    ) -> bool:
        """Attach one event. `False` means this key already woke the agent in this project.

        INSERT and let the constraint refuse, rather than asking first: two collectors that both
        read "not there" both insert, and the agent wakes twice about one post. The refusal is
        the mechanism, not an error.
        """
        if kind not in EVENT_KINDS:
            raise PresenceRefusal(f"rodzaj zdarzenia {kind!r} poza listą {EVENT_KINDS}")
        if not event_key.strip():
            raise PresenceRefusal("zdarzenie bez klucza deduplikacji budziłoby agenta bez końca")
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO wakeup_events
                        (organization_id, workspace_id, project_id, wakeup_id, kind, event_key,
                         payload)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                    """,
                    organization_id,
                    workspace_id,
                    project_id,
                    wakeup_id,
                    kind,
                    event_key.strip(),
                    json.dumps(payload, ensure_ascii=False),
                )
            except asyncpg.UniqueViolationError:
                # WIERSZ JUŻ JEST, ale to nie znaczy, że ktoś się nim zajął. Jeśli należy do
                # obudzenia, które NIE uruchomiło modelu (brak połączenia, wyczerpany budżet,
                # konto u pilota), zdarzenie jest ODŁOŻONE i ma przejść do TEGO obudzenia, które
                # właśnie je obsłuży. Bez tego pierwszy nieudany tik zabierał zdarzenie na
                # zawsze: audyt z 19.09 zmierzył to jako „Zdarzenie utracone po podłączeniu
                # Codexa".
                #
                # Przeniesienie, a nie drugi wiersz: UNIQUE ma dalej gwarantować, że jedno
                # zdarzenie budzi agenta raz, i gwarantuje.
                # 27.09: also an event whose session FAILED before the model did anything, at
                # most EVENT_RETRY_LIMIT times; each such move is counted in the payload.
                moved = await conn.fetchval(
                    """
                    UPDATE wakeup_events e SET wakeup_id = $4,
                           payload = CASE WHEN w.session_id IS NULL THEN e.payload
                                          ELSE jsonb_set(e.payload, '{ponowienia}', to_jsonb(
                                               coalesce((e.payload ->> 'ponowienia')::int, 0) + 1))
                                     END
                      FROM wakeups w
                     WHERE e.organization_id = $1 AND e.project_id = $2 AND e.event_key = $3
                       AND w.id = e.wakeup_id AND w.organization_id = e.organization_id
                       AND (w.session_id IS NULL OR (
                            coalesce((e.payload ->> 'ponowienia')::int, 0) < $5
                            AND """
                    + _FAILED_BEFORE_MODEL
                    + """))
                       -- DO INNEGO obudzenia, nigdy do tego samego. Bez tego warunku osiem
                       -- równoległych zapisów tego samego klucza w JEDNYM tiku „przenosiłoby"
                       -- wiersz do obudzenia, w którym już jest, i każdy dostawałby odpowiedź
                       -- „zapisane". Agent obudziłby się raz, ale tik policzyłby osiem zdarzeń.
                       AND e.wakeup_id <> $4
                    RETURNING e.id
                    """,
                    organization_id,
                    project_id,
                    event_key.strip(),
                    wakeup_id,
                    EVENT_RETRY_LIMIT,
                )
                return moved is not None
        return True

    async def schedule_return(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        due_at: datetime,
        note: str,
        session_id: UUID,
        limit: int,
    ) -> WakeupRow:
        """Zapisz powrót, do którego agent sam się zobowiązał.

        POWRÓT JEST WIERSZEM, NIE JOBEM W KOLEJCE, i to jest decyzja. Job z opóźnieniem zna
        tylko czas; ten wiersz niesie POWÓD i notatkę, da się go pokazać człowiekowi na ekranie
        historii obudzeń i da się go odwołać. Kolejka i tak co dziesięć minut przechodzi obok.

        LIMIT JEST SPRAWDZANY TUTAJ, w tej samej transakcji co zapis. Sprawdzenie przed
        wywołaniem przepuściłoby dwie sesje planujące naraz, a lista stu powrotów na jeden
        projekt to nie jest plan, tylko sposób na wydanie całego budżetu modelu na budzenie się.

        `session_id` w wierszu NIE jest wypełniane: to pole znaczy „obudzenie uruchomiło TĘ
        sesję”, a zaplanowany powrót jeszcze żadnej nie uruchomił. Sesja, która zaplanowała, jest
        w notatce zdarzenia, bo to jest fakt o pochodzeniu prośby, a nie o jej skutku.
        """
        if not note.strip():
            raise PresenceRefusal("powrót bez powodu jest budzikiem bez napisu, po co dzwoni")
        async with self._pool.acquire() as conn, conn.transaction():
            # ZAMEK, NIE SAMA TRANSAKCJA. Przegląd krzyżowy 19.09 wytknął, że w READ COMMITTED
            # dwa równoległe `count(*)` widzą ten sam stan i oba INSERT-y przechodzą, więc limit
            # sprawdzony „w tej samej transakcji" nie jest limitem. Wariant transakcyjny zamka
            # (nie sesyjny, jak w `approval.py`) wystarcza, bo TU cała sekwencja, liczenie i
            # zapis, idzie jedną transakcją na jednym połączeniu i zwalnia się sama przy COMMIT.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"presence-returns:{project_id}",
            )
            planned = await conn.fetchval(
                "SELECT count(*) FROM wakeups WHERE organization_id = $1 AND project_id = $2 "
                "AND due_at IS NOT NULL AND cancelled_at IS NULL AND consumed_at IS NULL",
                organization_id,
                project_id,
            )
            if int(planned or 0) >= limit:
                raise PresenceRefusal(
                    f"ten projekt ma już {planned} zaplanowanych powrotów przy limicie {limit}. "
                    "Odwołaj któryś albo poczekaj: sto powrotów to nie jest plan, tylko sposób "
                    "na wydanie budżetu modelu na samo budzenie się"
                )
            row = await conn.fetchrow(
                f"""
                INSERT INTO wakeups
                    (organization_id, workspace_id, project_id, reason, due_at)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {_WAKEUP_COLUMNS}
                """,
                organization_id,
                workspace_id,
                project_id,
                REASON_DUE,
                due_at,
            )
            wakeup = _wakeup_of(row)
            await conn.execute(
                """
                INSERT INTO wakeup_events
                    (organization_id, workspace_id, project_id, wakeup_id, kind, event_key,
                     payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                organization_id,
                workspace_id,
                project_id,
                wakeup.id,
                EVENT_DUE,
                f"{EVENT_DUE}:{wakeup.id}",
                json.dumps(
                    {"notatka": note.strip()[:1000], "zaplanowala_sesja": str(session_id)},
                    ensure_ascii=False,
                ),
            )
        return wakeup

    async def due_returns(
        self, *, organization_id: UUID, project_id: UUID, now: datetime
    ) -> tuple[WakeupRow, ...]:
        """Zaplanowane powroty, których termin już minął i których nikt nie odwołał."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_WAKEUP_COLUMNS} FROM wakeups "
                "WHERE organization_id = $1 AND project_id = $2 AND due_at IS NOT NULL "
                "AND due_at <= $3 AND cancelled_at IS NULL AND consumed_at IS NULL "
                "ORDER BY due_at",
                organization_id,
                project_id,
                now,
            )
        return tuple(_wakeup_of(row) for row in rows)

    async def planned_returns(
        self, *, organization_id: UUID, project_id: UUID
    ) -> tuple[WakeupRow, ...]:
        """Wszystko, co jeszcze przed terminem. Dla ekranu, żeby dało się to odwołać."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_WAKEUP_COLUMNS} FROM wakeups "
                "WHERE organization_id = $1 AND project_id = $2 AND due_at IS NOT NULL "
                "AND cancelled_at IS NULL AND consumed_at IS NULL ORDER BY due_at",
                organization_id,
                project_id,
            )
        return tuple(_wakeup_of(row) for row in rows)

    async def notes_of_returns(self, wakeup_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Notatka agenta dla każdego z tych obudzeń, jednym zapytaniem.

        EKRAN MA POKAZAĆ ZDANIE AGENTA, nie kod powodu. `wakeups.reason` dla zaplanowanego
        powrotu to zawsze „DUE", więc lista zaplanowanych bez notatek jest listą identycznych
        wierszy, z której człowiek nie dowiaduje się, po co agent wraca. Notatka leży w
        `wakeup_events.payload`, bo tam ją zapisał `schedule_return`.
        """
        if not wakeup_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT requested.id AS wakeup_id, e.payload FROM unnest($1::uuid[]) "
                "AS requested(id) JOIN wakeup_events e ON (e.wakeup_id=requested.id "
                "OR e.event_key='DUE:' || requested.id::text) WHERE e.kind=$2",
                list(wakeup_ids),
                EVENT_DUE,
            )
        notes: dict[UUID, str] = {}
        for row in rows:
            payload = _as_json(row["payload"], {})
            note = str((payload or {}).get("notatka") or "").strip()
            if note:
                notes.setdefault(row["wakeup_id"], note)
        return notes

    async def cancel_return(
        self,
        wakeup_id: UUID,
        *,
        organization_id: UUID,
        now: datetime,
        project_id: UUID | None = None,
    ) -> WakeupRow | None:
        """Odwołaj zaplanowany powrót. Wiersz ZOSTAJE, ze znacznikiem: „dlaczego agent nie
        wrócił, skoro obiecał" to pytanie, które ktoś zada tydzień później."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE wakeups SET cancelled_at = $3 WHERE organization_id = $1 AND id = $2 "
                f"AND due_at IS NOT NULL AND cancelled_at IS NULL AND consumed_at IS NULL "
                f"AND ($4::uuid IS NULL OR project_id = $4) "
                f"RETURNING {_WAKEUP_COLUMNS}",
                organization_id,
                wakeup_id,
                now,
                project_id,
            )
        return _wakeup_of(row) if row is not None else None

    async def consume_return(
        self, wakeup_id: UUID, *, organization_id: UUID, now: datetime
    ) -> None:
        """Oznacz powrót jako obsłużony, żeby nie budził drugi raz."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE wakeups SET consumed_at = $3 WHERE organization_id = $1 AND id = $2 "
                "AND consumed_at IS NULL",
                organization_id,
                wakeup_id,
                now,
            )

    async def events_of(self, wakeup_id: UUID) -> tuple[WakeupEventRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, wakeup_id, kind, event_key, payload, created_at FROM wakeup_events "
                "WHERE wakeup_id = $1 ORDER BY created_at, event_key",
                wakeup_id,
            )
        return tuple(
            WakeupEventRow(
                id=r["id"],
                wakeup_id=r["wakeup_id"],
                kind=r["kind"],
                event_key=r["event_key"],
                payload=_as_json(r["payload"], {}),
                created_at=r["created_at"],
            )
            for r in rows
        )

    async def attach_session(
        self, wakeup_id: UUID, *, organization_id: UUID, session_id: UUID
    ) -> WakeupRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE wakeups SET session_id = $3, consumed_at = now()
                 WHERE organization_id = $1 AND id = $2
                RETURNING {_WAKEUP_COLUMNS}
                """,
                organization_id,
                wakeup_id,
                session_id,
            )
        if row is None:
            raise PresenceRefusal(f"nie ma obudzenia {wakeup_id}")
        return _wakeup_of(row)

    async def close_skipped(
        self, wakeup_id: UUID, *, organization_id: UUID, reason: str, detail: str | None = None
    ) -> WakeupRow:
        """Close a wake-up that did NOT run the model, with why. The commonest row in the table.

        `reason` is a CODE from the closed list and `detail` the sentence for a person. Two
        columns because they answer to two readers: the day report counts codes ("12 ticks with
        nothing to do"), and a founder reading one row wants the numbers AS THEY WERE at that
        moment, which recomputing them in the evening would not give.
        """
        if reason not in SKIPPED_REASONS:
            raise PresenceRefusal(f"powód pominięcia {reason!r} poza listą {SKIPPED_REASONS}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE wakeups SET skipped_reason = $3, skipped_detail = $4, consumed_at = now()
                 WHERE organization_id = $1 AND id = $2 AND session_id IS NULL
                RETURNING {_WAKEUP_COLUMNS}
                """,
                organization_id,
                wakeup_id,
                reason,
                detail,
            )
        if row is None:
            raise PresenceRefusal(
                f"nie ma obudzenia {wakeup_id} bez sesji; obudzenie, które uruchomiło model, "
                "nie może twierdzić, że go nie uruchomiło"
            )
        return _wakeup_of(row)

    async def record_cost(
        self, wakeup_id: UUID, *, organization_id: UUID, cost: dict[str, Any]
    ) -> None:
        """What this wake-up spent. Written after the session ends, so `None` means "not yet",
        which is a different thing from zero."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE wakeups SET cost = $3::jsonb WHERE organization_id = $1 AND id = $2",
                organization_id,
                wakeup_id,
                json.dumps(cost, ensure_ascii=False),
            )

    async def wakeups_for(
        self, *, organization_id: UUID, project_id: UUID, limit: int = 50
    ) -> tuple[WakeupRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_WAKEUP_COLUMNS} FROM wakeups "
                "WHERE organization_id = $1 AND project_id = $2 "
                "ORDER BY created_at DESC LIMIT $3",
                organization_id,
                project_id,
                limit,
            )
        return tuple(_wakeup_of(r) for r in rows)

    async def since(
        self, *, organization_id: UUID, since: datetime, project_id: UUID | None = None
    ) -> tuple[WakeupRow, ...]:
        """Every wake-up in a window, for the day report. Oldest first: it reads as a story."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_WAKEUP_COLUMNS} FROM wakeups "
                "WHERE organization_id = $1 AND created_at >= $2 "
                "AND ($3::uuid IS NULL OR project_id = $3) ORDER BY created_at",
                organization_id,
                since,
                project_id,
            )
        return tuple(_wakeup_of(r) for r in rows)

    async def known_event_keys(
        self, *, organization_id: UUID, project_id: UUID, keys: list[str]
    ) -> set[str]:
        """Which of these keys ALREADY WOKE THE AGENT. Planning for the collector, not the
        deduplication itself: that is the UNIQUE constraint in `record_event`.

        „OBUDZIŁO AGENTA" ZNACZY: DOSZŁO DO MODELU. Zdarzenie zapisane przy tiku, który modelu
        nie uruchomił (brak połączenia Codex, wyczerpany budżet, konto zajęte przez pilota),
        jest ODŁOŻONE, nie obsłużone, i ma wrócić w następnym tiku. Audyt z 19.09 nazwał to
        wprost: „Zdarzenie utracone po podłączeniu Codexa" — pierwszy tik zapisał wiersz, drugi
        uznał go za znany i nie miał już o czym myśleć, a post nigdy nie doczekał się
        odpowiedzi.

        Dlatego warunkiem jest `w.session_id IS NOT NULL`: zdarzenie należy do obudzenia, które
        NAPRAWDĘ otworzyło sesję. Wiersz zostaje w bazie w obu przypadkach, bo jest dowodem, że
        coś się wydarzyło; różnica jest w tym, czy ktoś się tym zajął.
        """
        if not keys:
            return set()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.event_key FROM wakeup_events e
                  JOIN wakeups w
                    ON w.id = e.wakeup_id AND w.organization_id = e.organization_id
                 WHERE e.organization_id = $1 AND e.project_id = $2
                   AND e.event_key = ANY($3::text[])
                   AND w.session_id IS NOT NULL
                   -- 27.09: a session that failed before the model did anything did not handle
                   -- its event, unless the event already went back EVENT_RETRY_LIMIT times.
                   AND NOT (coalesce((e.payload ->> 'ponowienia')::int, 0) < $4
                            AND """
                + _FAILED_BEFORE_MODEL
                + """)
                """,
                organization_id,
                project_id,
                keys,
                EVENT_RETRY_LIMIT,
            )
        return {r["event_key"] for r in rows}


__all__ = [
    "EVENT_DUE",
    "EVENT_KINDS",
    "EVENT_MODERATION",
    "EVENT_NEW_THREAD",
    "EVENT_REPLY",
    "REASON_DUE",
    "REASON_NEW_EVENT",
    "REASON_RETRY",
    "REASON_USER_MESSAGE",
    "SKIPPED_BUDGET",
    "SKIPPED_ENGINE_LIMIT",
    "SKIPPED_ERROR",
    "SKIPPED_NO_CONNECTION",
    "SKIPPED_NO_DELEGATION",
    "SKIPPED_NO_EVENTS",
    "SKIPPED_PAUSED",
    "SKIPPED_REASONS",
    "WAKEUP_REASONS",
    "DelegationRow",
    "PresenceRefusal",
    "PresenceStore",
    "ScopeError",
    "WakeupEventRow",
    "WakeupRow",
]
