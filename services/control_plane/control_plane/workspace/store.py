"""Reads and writes of migration 0013: projects, person profiles, accounts and attachments.

EVERY METHOD TAKES `organization_id` AND MOST TAKE `workspace_id`, AND BOTH GO INTO THE WHERE
CLAUSE. Not because the composite foreign keys are insufficient — they are what stops a row
being WRITTEN across a boundary — but because they do nothing about a READ. A query that filters
on `workspace_id` alone answers with the rows of another organization's workspace as soon as
there is a second tenant, and the failure is silent: the screen fills with plausible data.
`workspace()` is the one gate: the API resolves the workspace through it first and answers 404
when it comes back None, so an identifier from another organization is indistinguishable from
one that does not exist.

WHAT "DETACH" AND "UNASSIGN" DO HERE: they set a timestamp. There is no DELETE in this module
and there must not be one. P1 says "odłączenie nie usuwa historii", and the reason is not
sentimental — the account under which something was published has to remain findable from the
publication months after somebody moved it to another profile.

THE ACTIVE-ROW RULES ARE THE DATABASE'S, NOT THIS MODULE'S. `attach_account` does not check
whether the account is already attached before inserting; it inserts and lets
`person_profile_accounts_one_active_per_account` refuse, then turns the `UniqueViolationError`
into a domain error. A check-then-insert would pass its own tests and lose the race that a
partial unique index cannot lose (0001 makes the same argument about browser profiles).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg


class WorkspaceRefusal(Exception):
    """A write the domain refuses, with a sentence the operator can read (D-010).

    A distinct type rather than `HTTPException`: the store must not know about HTTP, and the
    router must not have to guess which `Exception` means "the caller asked for something
    impossible" and which means "the database is broken".
    """


class AccountAlreadyAttached(WorkspaceRefusal):
    """The account already hangs under an active profile, possibly in another workspace."""


class AccountAlreadyAssigned(WorkspaceRefusal):
    """The account is already assigned to this project and the assignment is still active."""


@dataclass(frozen=True)
class WorkspaceRow:
    id: UUID
    organization_id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True)
class ProjectRow:
    id: UUID
    workspace_id: UUID
    name: str
    goal: str
    mode: str
    paused_at: datetime | None
    created_at: datetime
    archived_at: datetime | None
    #: 0026: ile przysługuje KAŻDEJ następnej rozmowie w tym projekcie. `None` znaczy „nikt nie
    #: ustawił", czyli obowiązuje wartość domyślna z kodu, i to jest co innego niż zero.
    conversation_budget: dict[str, int] | None = None


@dataclass(frozen=True)
class PersonProfileRow:
    id: UUID
    workspace_id: UUID
    name: str
    kind: str
    created_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class AccountRow:
    """One `social_accounts` row. ORGANIZATION-scoped, as it has been since 0001.

    `person_profile_id` is the profile it is attached to right now, or None. That is the field
    that binds an organization-level account to one workspace, and it is read from
    `person_profile_accounts`, never from a column of `social_accounts`.
    """

    id: UUID
    platform: str
    external_account_id: str
    state: str
    created_at: datetime
    person_profile_id: UUID | None
    attached_at: datetime | None


@dataclass(frozen=True)
class AttachmentRow:
    id: UUID
    person_profile_id: UUID
    social_account_id: UUID
    platform: str
    external_account_id: str
    attached_at: datetime
    detached_at: datetime | None


@dataclass(frozen=True)
class AssignmentRow:
    id: UUID
    project_id: UUID
    social_account_id: UUID
    platform: str
    external_account_id: str
    assigned_at: datetime
    unassigned_at: datetime | None


def _workspace(row: asyncpg.Record) -> WorkspaceRow:
    return WorkspaceRow(
        id=row["id"],
        organization_id=row["organization_id"],
        name=row["name"],
        created_at=row["created_at"],
    )


def _budget(value: Any) -> dict[str, int] | None:
    """Budżet rozmowy z bazy: słownik albo `None`, nigdy napis i nigdy pusty słownik.

    asyncpg oddaje `jsonb` jako napis, więc rozpakowanie jest tutaj, w jednym miejscu. Pusty
    obiekt zamieniamy na `None`, bo „ustawiono nic" i „nie ustawiono" znaczą to samo dla tego,
    kto z tego korzysta, a dwie reprezentacje jednego stanu to dwie okazje do pomyłki.
    """
    if value is None or value == "" or value == {}:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict) or not value:
        return None
    return {str(key): int(number) for key, number in value.items()}


def _project(row: asyncpg.Record) -> ProjectRow:
    return ProjectRow(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        goal=row["goal"],
        mode=row["mode"],
        paused_at=row["paused_at"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        conversation_budget=_budget(row["conversation_budget"]),
    )


def _profile(row: asyncpg.Record) -> PersonProfileRow:
    return PersonProfileRow(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        kind=row["kind"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


def _account(row: asyncpg.Record) -> AccountRow:
    return AccountRow(
        id=row["id"],
        platform=row["platform"],
        external_account_id=row["external_account_id"],
        state=str(row["state"]),
        created_at=row["created_at"],
        person_profile_id=row["person_profile_id"],
        attached_at=row["attached_at"],
    )


def _attachment(row: asyncpg.Record) -> AttachmentRow:
    return AttachmentRow(
        id=row["id"],
        person_profile_id=row["person_profile_id"],
        social_account_id=row["social_account_id"],
        platform=row["platform"],
        external_account_id=row["external_account_id"],
        attached_at=row["attached_at"],
        detached_at=row["detached_at"],
    )


def _assignment(row: asyncpg.Record) -> AssignmentRow:
    return AssignmentRow(
        id=row["id"],
        project_id=row["project_id"],
        social_account_id=row["social_account_id"],
        platform=row["platform"],
        external_account_id=row["external_account_id"],
        assigned_at=row["assigned_at"],
        unassigned_at=row["unassigned_at"],
    )


class WorkspaceStore:
    """The data access of P1. One pool, no session state, no cache."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- workspaces ---------------------------------------------------------------

    async def list_workspaces(self, *, organization_id: UUID) -> tuple[WorkspaceRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, organization_id, name, created_at FROM workspaces"
                " WHERE organization_id = $1 ORDER BY created_at, id",
                organization_id,
            )
        return tuple(_workspace(row) for row in rows)

    async def workspace(self, *, organization_id: UUID, workspace_id: UUID) -> WorkspaceRow | None:
        """The gate every workspace-scoped route passes through first.

        None covers BOTH "there is no such workspace" and "it belongs to somebody else", and the
        router answers 404 for both. Telling them apart would confirm the existence of another
        tenant's workspace to anybody willing to try identifiers.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, organization_id, name, created_at FROM workspaces"
                " WHERE organization_id = $1 AND id = $2",
                organization_id,
                workspace_id,
            )
        return _workspace(row) if row is not None else None

    async def default_workspace(self, *, organization_id: UUID) -> UUID | None:
        """The workspace the PRE-PIVOT operator screens work in: the organization's OLDEST.

        `ObservationStore.sole_workspace` answers None as soon as there are two, because E1
        assumed exactly one (D-016) and guessing would have been worse than refusing. P1 makes a
        second workspace a normal thing the user creates, so that refusal turned into a real
        regression the moment this milestone landed: `/community` and `/api/user-profile` began
        answering 409 for the whole organization, and the operator panel went dark.

        The answer is not a guess and not a remembered "current workspace": it is the oldest one,
        which is the workspace the pilot, its communities and its profile versions already live
        in. Nothing moves, and the operator panel keeps reading exactly the rows it read before
        the second workspace existed. Choosing the newest, or whichever the planner returned
        first, would silently change which data those screens describe.

        None means the organization has NO workspace at all. That is still a refusal, and the
        callers still answer 409: an operator screen with no workspace has nothing to be about.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM workspaces WHERE organization_id = $1"
                " ORDER BY created_at, id LIMIT 1",
                organization_id,
            )

    async def create_workspace(self, *, organization_id: UUID, name: str) -> WorkspaceRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2)"
                " RETURNING id, organization_id, name, created_at",
                organization_id,
                name,
            )
        return _workspace(row)

    # -- projects -----------------------------------------------------------------

    async def list_projects(
        self, *, organization_id: UUID, workspace_id: UUID, include_archived: bool = False
    ) -> tuple[ProjectRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, workspace_id, name, goal, mode, paused_at, created_at,
                       archived_at, conversation_budget
                FROM projects
                WHERE organization_id = $1 AND workspace_id = $2
                  AND ($3 OR archived_at IS NULL)
                ORDER BY created_at, id
                """,
                organization_id,
                workspace_id,
                include_archived,
            )
        return tuple(_project(row) for row in rows)

    async def get_project(
        self, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> ProjectRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, workspace_id, name, goal, mode, paused_at, created_at,
                       archived_at, conversation_budget
                FROM projects
                WHERE organization_id = $1 AND workspace_id = $2 AND id = $3
                """,
                organization_id,
                workspace_id,
                project_id,
            )
        return _project(row) if row is not None else None

    async def create_project(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        name: str,
        goal: str = "",
        mode: str = "AUTONOMOUS",
    ) -> ProjectRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO projects (organization_id, workspace_id, name, goal, mode)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, workspace_id, name, goal, mode, paused_at, created_at,
                          archived_at, conversation_budget
                """,
                organization_id,
                workspace_id,
                name,
                goal,
                mode,
            )
        return _project(row)

    async def update_project(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        name: str | None = None,
        goal: str | None = None,
        mode: str | None = None,
        paused: bool | None = None,
        archived: bool | None = None,
        conversation_budget: dict[str, int] | None = None,
        clear_conversation_budget: bool = False,
    ) -> ProjectRow | None:
        """A partial update: only the fields the caller actually sent are touched.

        `COALESCE($n, column)` rather than a statement assembled from the non-None fields. A
        built string is where a caller's key ends up inside SQL, and the shape avoided is
        already in this repository's history; this version has a fixed statement and a fixed
        parameter list, so there is nothing to build.

        `paused` and `archived` are booleans that map onto timestamps, so they cannot use the
        same trick: `CASE` picks between "set now()", "clear it" and "leave it alone", and the
        third branch is the one a naive `now()`-on-true would lose — it would move the pause
        time on every unrelated edit.

        `conversation_budget` (0026) ma TRZY zachowania i dlatego ma dwa parametry: pominięte
        znaczy „nie ruszaj", słownik znaczy „ustaw to", a `clear_conversation_budget` znaczy
        „wróć do wartości domyślnej". Bez tego trzeciego nie dałoby się cofnąć własnego
        ustawienia, bo `None` jest już zajęte przez „nie ruszaj".
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE projects SET
                    name = COALESCE($4, name),
                    goal = COALESCE($5, goal),
                    mode = COALESCE($6, mode),
                    paused_at = CASE
                        WHEN $7::boolean IS NULL THEN paused_at
                        WHEN $7::boolean THEN COALESCE(paused_at, now())
                        ELSE NULL END,
                    archived_at = CASE
                        WHEN $8::boolean IS NULL THEN archived_at
                        WHEN $8::boolean THEN COALESCE(archived_at, now())
                        ELSE NULL END,
                    conversation_budget = CASE
                        WHEN $10::boolean THEN NULL
                        WHEN $9::jsonb IS NULL THEN conversation_budget
                        ELSE $9::jsonb END
                WHERE organization_id = $1 AND workspace_id = $2 AND id = $3
                RETURNING id, workspace_id, name, goal, mode, paused_at, created_at,
                          archived_at, conversation_budget
                """,
                organization_id,
                workspace_id,
                project_id,
                name,
                goal,
                mode,
                paused,
                archived,
                json.dumps(conversation_budget) if conversation_budget else None,
                clear_conversation_budget,
            )
        return _project(row) if row is not None else None

    # -- person profiles ----------------------------------------------------------

    async def list_profiles(
        self, *, organization_id: UUID, workspace_id: UUID, include_archived: bool = False
    ) -> tuple[PersonProfileRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, workspace_id, name, kind, created_at, archived_at
                FROM person_profiles
                WHERE organization_id = $1 AND workspace_id = $2
                  AND ($3 OR archived_at IS NULL)
                ORDER BY created_at, id
                """,
                organization_id,
                workspace_id,
                include_archived,
            )
        return tuple(_profile(row) for row in rows)

    async def get_profile(
        self, *, organization_id: UUID, workspace_id: UUID, profile_id: UUID
    ) -> PersonProfileRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, workspace_id, name, kind, created_at, archived_at
                FROM person_profiles
                WHERE organization_id = $1 AND workspace_id = $2 AND id = $3
                """,
                organization_id,
                workspace_id,
                profile_id,
            )
        return _profile(row) if row is not None else None

    async def create_profile(
        self, *, organization_id: UUID, workspace_id: UUID, name: str, kind: str = "PERSON"
    ) -> PersonProfileRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO person_profiles (organization_id, workspace_id, name, kind)
                VALUES ($1, $2, $3, $4)
                RETURNING id, workspace_id, name, kind, created_at, archived_at
                """,
                organization_id,
                workspace_id,
                name,
                kind,
            )
        return _profile(row)

    # -- accounts -----------------------------------------------------------------

    async def upsert_account(
        self, *, organization_id: UUID, platform: str, external_account_id: str
    ) -> tuple[UUID, bool]:
        """The account row for this (platform, handle), creating it when it is new.

        Returns (id, created). Not an INSERT that fails on a duplicate: the same handle
        registered twice is the user repeating themselves, not an error, and 0001 already makes
        `(organization_id, platform, external_account_id)` unique. `created` travels back so the
        audit entry says which of the two actually happened.
        """
        async with self._pool.acquire() as conn:
            created = await conn.fetchval(
                """
                INSERT INTO social_accounts (organization_id, platform, external_account_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (organization_id, platform, external_account_id) DO NOTHING
                RETURNING id
                """,
                organization_id,
                platform,
                external_account_id,
            )
            if created is not None:
                return created, True
            existing = await conn.fetchval(
                "SELECT id FROM social_accounts WHERE organization_id = $1 AND platform = $2"
                " AND external_account_id = $3",
                organization_id,
                platform,
                external_account_id,
            )
        return existing, False

    async def account(self, *, organization_id: UUID, account_id: UUID) -> AccountRow | None:
        """One account with its CURRENT attachment, whichever workspace that attachment is in.

        Deliberately not workspace-filtered: the caller needs to know that an account is already
        somebody else's before it tries to attach it, and a workspace-filtered read would answer
        "free" about an account taken by another workspace.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.id, a.platform, a.external_account_id, a.state, a.created_at,
                       p.person_profile_id, p.attached_at
                FROM social_accounts a
                LEFT JOIN person_profile_accounts p
                       ON p.social_account_id = a.id AND p.detached_at IS NULL
                WHERE a.organization_id = $1 AND a.id = $2
                """,
                organization_id,
                account_id,
            )
        return _account(row) if row is not None else None

    async def list_accounts(
        self, *, organization_id: UUID, workspace_id: UUID
    ) -> tuple[AccountRow, ...]:
        """Accounts ATTACHED to a profile of this workspace, plus the ones attached nowhere.

        The two halves are deliberate and they are not the same claim. An attached account is
        workspace data and a foreign workspace must not see it. An unattached account is an
        organization-level row from 0001 with no owner yet, and it is listed so the user can
        pick it up; there is one organization and one operator behind the boundary (D-016), so
        this is not a cross-tenant read. The moment it is attached it belongs to one workspace
        and disappears from the other one's list, which is what
        `test_workspace_api` asserts.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.id, a.platform, a.external_account_id, a.state, a.created_at,
                       active.person_profile_id, active.attached_at
                FROM social_accounts a
                LEFT JOIN person_profile_accounts active
                       ON active.social_account_id = a.id AND active.detached_at IS NULL
                LEFT JOIN person_profiles owner
                       ON owner.id = active.person_profile_id
                WHERE a.organization_id = $1
                  AND (active.id IS NULL OR owner.workspace_id = $2)
                ORDER BY a.platform, a.external_account_id
                """,
                organization_id,
                workspace_id,
            )
        return tuple(_account(row) for row in rows)

    async def list_attachments(
        self, *, organization_id: UUID, workspace_id: UUID, include_detached: bool = True
    ) -> tuple[AttachmentRow, ...]:
        """Every attachment of this workspace, history included.

        History is on by default because a detached account is exactly what the user needs to
        see in order to attach it again, and because "odłączenie nie usuwa historii" is only
        true if something reads it.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pa.id, pa.person_profile_id, pa.social_account_id,
                       a.platform, a.external_account_id, pa.attached_at, pa.detached_at
                FROM person_profile_accounts pa
                JOIN social_accounts a ON a.id = pa.social_account_id
                WHERE pa.organization_id = $1 AND pa.workspace_id = $2
                  AND ($3 OR pa.detached_at IS NULL)
                ORDER BY pa.attached_at, pa.id
                """,
                organization_id,
                workspace_id,
                include_detached,
            )
        return tuple(_attachment(row) for row in rows)

    async def attach_account(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        person_profile_id: UUID,
        social_account_id: UUID,
    ) -> AttachmentRow:
        """Attach, or raise `AccountAlreadyAttached`.

        The refusal comes from the partial unique index, not from a read before the write: a
        check-then-insert loses the race between two requests, and the index cannot.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    WITH inserted AS (
                        INSERT INTO person_profile_accounts
                            (organization_id, workspace_id, person_profile_id, social_account_id)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id, person_profile_id, social_account_id, attached_at, detached_at
                    )
                    SELECT inserted.*, a.platform, a.external_account_id
                    FROM inserted JOIN social_accounts a ON a.id = inserted.social_account_id
                    """,
                    organization_id,
                    workspace_id,
                    person_profile_id,
                    social_account_id,
                )
        except asyncpg.UniqueViolationError as exc:
            raise AccountAlreadyAttached(
                "to konto jest już przypięte do innego profilu. Konto platformy reprezentuje "
                "jedną tożsamość, więc najpierw odepnij je tam, gdzie jest teraz"
            ) from exc
        return _attachment(row)

    async def detach_account(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        person_profile_id: UUID,
        social_account_id: UUID,
    ) -> bool:
        """Set `detached_at` on the ACTIVE attachment. True when there was one.

        An UPDATE, never a DELETE. False means there was nothing active to detach, which the
        router turns into a 404 rather than into a cheerful 204.
        """
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                """
                UPDATE person_profile_accounts SET detached_at = now()
                WHERE organization_id = $1 AND workspace_id = $2
                  AND person_profile_id = $3 AND social_account_id = $4
                  AND detached_at IS NULL
                RETURNING id
                """,
                organization_id,
                workspace_id,
                person_profile_id,
                social_account_id,
            )
        return updated is not None

    # -- project assignments ------------------------------------------------------

    async def list_assignments(
        self, *, organization_id: UUID, workspace_id: UUID, include_removed: bool = True
    ) -> tuple[AssignmentRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pa.id, pa.project_id, pa.social_account_id,
                       a.platform, a.external_account_id, pa.assigned_at, pa.unassigned_at
                FROM project_account_assignments pa
                JOIN social_accounts a ON a.id = pa.social_account_id
                WHERE pa.organization_id = $1 AND pa.workspace_id = $2
                  AND ($3 OR pa.unassigned_at IS NULL)
                ORDER BY pa.assigned_at, pa.id
                """,
                organization_id,
                workspace_id,
                include_removed,
            )
        return tuple(_assignment(row) for row in rows)

    async def assign_account(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        social_account_id: UUID,
    ) -> AssignmentRow:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    WITH inserted AS (
                        INSERT INTO project_account_assignments
                            (organization_id, workspace_id, project_id, social_account_id)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id, project_id, social_account_id, assigned_at, unassigned_at
                    )
                    SELECT inserted.*, a.platform, a.external_account_id
                    FROM inserted JOIN social_accounts a ON a.id = inserted.social_account_id
                    """,
                    organization_id,
                    workspace_id,
                    project_id,
                    social_account_id,
                )
        except asyncpg.UniqueViolationError as exc:
            raise AccountAlreadyAssigned("to konto jest już przypisane do tego projektu") from exc
        return _assignment(row)

    async def unassign_account(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        social_account_id: UUID,
    ) -> bool:
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                """
                UPDATE project_account_assignments SET unassigned_at = now()
                WHERE organization_id = $1 AND workspace_id = $2
                  AND project_id = $3 AND social_account_id = $4
                  AND unassigned_at IS NULL
                RETURNING id
                """,
                organization_id,
                workspace_id,
                project_id,
                social_account_id,
            )
        return updated is not None

    # -- what actually happened in this workspace ---------------------------------

    async def project_results(
        self, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> dict[str, Any]:
        """Co wysłał w świat TEN projekt. Osobny odczyt, nie przefiltrowany tamten (E6).

        DLACZEGO OSOBNY KSZTAŁT, A NIE PARAMETR W `results`. Tamten odczyt niesie też
        społeczności i ich liczbę, a społeczność należy do PRZESTRZENI, nie do projektu: w
        schemacie `communities` nie ma kolumny projektu i nie jest to przeoczenie, bo jedna
        obserwowana społeczność karmi wszystkie projekty przestrzeni. Przefiltrowany `results`
        musiałby więc wpisać w te pola zera, a zero znaczy „zmierzono i nie ma", podczas gdy
        prawdą jest „to nie jest liczba tego projektu". Zamiast tego ten odczyt tych pól NIE MA.

        CO TO NAPRAWIA. Projekt bez jednej rozmowy i bez powierzenia pokazywał 14 społeczności i
        38 wysłanych publikacji, bo strona wołała odczyt przestrzeni (audyt UX 20.09, punkt 6).
        Nie był to wyciek między organizacjami, tylko cudza praca czytana jako własna, i w
        dodatku jako praca agenta, który jeszcze nie ruszył.
        """
        async with self._pool.acquire() as conn:
            publications = await conn.fetch(
                """
                SELECT r.id, r.status, r.target_url, e.permalink,
                       r.published_at, r.queued_at, r.kind, r.project_id,
                       CASE r.public_visibility
                         WHEN 'CONFIRMED' THEN true
                         WHEN 'REMOVED' THEN false
                         ELSE NULL
                       END AS still_present,
                       r.public_visibility_checked_at AS observed_at,
                       r.removal_scope, r.detail, r.target_external_id
                FROM publication_requests r
                LEFT JOIN execution_evidence e ON e.attempt_id = r.attempt_id
                WHERE r.organization_id = $1 AND r.workspace_id = $2 AND r.project_id = $3
                ORDER BY COALESCE(r.published_at, r.queued_at) DESC
                LIMIT 10
                """,
                organization_id,
                workspace_id,
                project_id,
            )
            totals = await conn.fetchrow(
                """
                SELECT
                    (SELECT count(*) FROM publication_requests
                      WHERE organization_id = $1 AND workspace_id = $2 AND project_id = $3
                        AND status = 'PUBLISHED') AS published,
                    (SELECT count(*) FROM publication_requests r
                      WHERE r.organization_id = $1 AND r.workspace_id = $2
                        AND r.project_id = $3 AND r.status = 'PUBLISHED'
                        AND r.public_visibility = 'CONFIRMED') AS confirmed,
                    (SELECT count(*) FROM publication_requests
                      WHERE organization_id = $1 AND workspace_id = $2 AND project_id = $3
                        AND status IN ('QUEUED', 'PUBLISHING')) AS waiting
                """,
                organization_id,
                workspace_id,
                project_id,
            )
        return {
            "scope": {"kind": "PROJECT", "project_id": str(project_id)},
            "publications": [self._publication_payload(row) for row in publications],
            "totals": {
                "published": totals["published"],
                "confirmed_visible": totals["confirmed"],
                # Pending execution only; a terminal refusal is not work still in the queue.
                # Liczba osobna od opublikowanych, bo „zgłoszone" i „wysłane" to dwie różne
                # rzeczy, a ich zsumowanie jest tym samym błędem, co sumowanie publikacji z
                # potwierdzonymi (ZAKRES-FUNKCJONALNY §6 reguła 7).
                "waiting": totals["waiting"],
            },
        }

    @staticmethod
    def _publication_payload(row: Any) -> dict[str, Any]:
        """Jeden wniosek publikacyjny w kształcie, który czyta ekran. Jeden dla obu odczytów."""
        return {
            "request_id": str(row["id"]),
            "status": row["status"],
            # 0023: odpowiedź pod cudzym wątkiem czy NASZ własny wątek. Bez tego pola oba
            # wyglądają na ekranie identycznie, a założenie własnego wątku jest innym działaniem
            # niż dopisanie się do cudzej rozmowy: inaczej się je ocenia i inaczej się za nie
            # odpowiada wobec społeczności.
            "kind": row["kind"],
            # CZYJA TO PRACA. `project_id` jest w `publication_requests` od 0017, a na ekranie go
            # nie było, więc lista całej przestrzeni czytała się jak lista otwartego projektu
            # (audyt UX 20.09, punkt 6). `None` znaczy „wiersz sprzed 0017", czyli publikacja z
            # czasów pilota, i to też jest odpowiedź.
            "project_id": str(row["project_id"]) if row["project_id"] else None,
            "target_url": row["target_url"],
            "permalink": row["permalink"],
            "published_at": row["published_at"].isoformat() if row["published_at"] else None,
            "queued_at": row["queued_at"].isoformat(),
            # An author can see a removed comment. Only the request's public-visibility verdict
            # answers what other people can see; an author observation must never become success.
            "still_present": row["still_present"],
            "observed_at": row["observed_at"].isoformat() if row["observed_at"] else None,
            # 0032: WHAT is gone when the verdict is "not visible": our reply (`COMMENT`) or the
            # whole thread under it (`PARENT_POST`, which says nothing about us). `None` = not
            # established. Without it the screen said "niewidoczna" for both, and people read a
            # thread removed by Reddit's filters as a community rejecting our reply (26.09).
            "removal_scope": row["removal_scope"],
            # 27.09: which platform, and why nothing went out. The screen showed a bare "SKIPPED"
            # for the first X reply, with no platform and no reason, so a person could not tell
            # an X reply from a Reddit one, nor "the actuator refused, nothing sent" from a loss.
            "platform": "X" if (row["target_external_id"] or "").startswith("x:") else "REDDIT",
            "detail": row["detail"],
        }

    async def results(self, *, organization_id: UUID, workspace_id: UUID) -> dict[str, Any]:
        """The workspace's real recent results, read from the tables that already hold them.

        THERE IS NOTHING INVENTED HERE AND NOTHING AGGREGATED ACROSS KINDS. The numbers come
        from `communities`, `observed_threads` and `publication_requests` of THIS workspace, and
        publications are split into published, confirmed-visible and unconfirmed rather than
        summed into one count — ZAKRES-FUNKCJONALNY §6 rule 7 says a publication counter without
        that split misleads, and the split exists in the data because two comments really were
        removed within an hour while a read still reported them present.

        `last_polled_at` and `published_at` travel with the rows so the screen can print WHEN,
        which rule 1 of the same section requires of every number.
        """
        async with self._pool.acquire() as conn:
            communities = await conn.fetch(
                """
                SELECT id, platform, community_key, display_name, access_mode, observe_enabled,
                       last_polled_at,
                       (SELECT count(*) FROM observed_threads t
                         WHERE t.community_id = c.id) AS thread_count
                FROM communities c
                WHERE organization_id = $1 AND workspace_id = $2
                ORDER BY community_key
                """,
                organization_id,
                workspace_id,
            )
            publications = await conn.fetch(
                """
                SELECT r.id, r.status, r.target_url, e.permalink,
                       r.published_at, r.queued_at, r.kind, r.project_id,
                       CASE r.public_visibility
                         WHEN 'CONFIRMED' THEN true
                         WHEN 'REMOVED' THEN false
                         ELSE NULL
                       END AS still_present,
                       r.public_visibility_checked_at AS observed_at,
                       r.removal_scope, r.detail, r.target_external_id
                FROM publication_requests r
                LEFT JOIN execution_evidence e ON e.attempt_id = r.attempt_id
                WHERE r.organization_id = $1 AND r.workspace_id = $2
                ORDER BY COALESCE(r.published_at, r.queued_at) DESC
                LIMIT 10
                """,
                organization_id,
                workspace_id,
            )
            totals = await conn.fetchrow(
                """
                SELECT
                    (SELECT count(*) FROM communities
                      WHERE organization_id = $1 AND workspace_id = $2) AS communities,
                    (SELECT count(*) FROM observed_threads
                      WHERE organization_id = $1 AND workspace_id = $2) AS threads,
                    (SELECT count(*) FROM publication_requests
                      WHERE organization_id = $1 AND workspace_id = $2
                        AND status = 'PUBLISHED') AS published,
                    (SELECT count(*) FROM publication_requests r
                      WHERE r.organization_id = $1 AND r.workspace_id = $2
                        AND r.status = 'PUBLISHED'
                        AND r.public_visibility = 'CONFIRMED') AS confirmed
                """,
                organization_id,
                workspace_id,
            )
        return {
            "communities": [
                {
                    "community_id": str(row["id"]),
                    "platform": row["platform"],
                    "community_key": row["community_key"],
                    "display_name": row["display_name"],
                    "access_mode": str(row["access_mode"]),
                    "observe_enabled": row["observe_enabled"],
                    "last_polled_at": (
                        row["last_polled_at"].isoformat() if row["last_polled_at"] else None
                    ),
                    "thread_count": row["thread_count"],
                }
                for row in communities
            ],
            "publications": [self._publication_payload(row) for row in publications],
            "totals": {
                "communities": totals["communities"],
                "threads": totals["threads"],
                "published": totals["published"],
                "confirmed_visible": totals["confirmed"],
            },
        }
