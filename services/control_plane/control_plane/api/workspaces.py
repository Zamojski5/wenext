"""`/api/workspaces` — the user's own structure: workspaces, projects, profiles, accounts.

This is the backend half of ROADMAP-PIVOT P1. Everything it serves is data the user created,
and the rules it enforces are the ones P1 is judged on:

  * **the workspace comes from the PATH and is verified against the SESSION.** Every route below
    resolves it through `WorkspaceStore.workspace(organization_id=…, workspace_id=…)` and answers
    404 when that comes back None. A workspace of another organization and a workspace that does
    not exist are the same answer on purpose: a 403 would confirm that the identifier is real.
  * **the organization NEVER comes from the request.** It is read off the session, like
    everywhere else in this package. The shape being avoided is in this repository:
    `services/stream_gateway/stream_gateway/app.py` reads `user_id` out of a POST body.
  * **an unsupported platform does not pretend to be an integration.** `platform` is validated
    against `workspace.platforms.PLATFORMS`, and every account payload carries that platform's
    real capabilities so the screen prints "integracja niedostępna" from a measurement rather
    than from a guess.
  * **detaching and unassigning keep the row.** Both are 204 with a timestamp written; neither
    deletes anything, and both answer 404 when there was no ACTIVE link to end, because a
    cheerful 204 over nothing is the same lie as a gate announcing GO with no measurement.

WHAT IS NOT HERE: sessions of the agent, conversations and Codex connections. P1 is the
structure; the conversation that drives it is P2 and the engine connections are P1 etap 2
(migration 0014). The dashboard says so in those words instead of rendering an empty panel.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.workspace import (
    AccountRow,
    AssignmentRow,
    AttachmentRow,
    PersonProfileRow,
    ProjectRow,
    WorkspaceRefusal,
    WorkspaceRow,
    WorkspaceStore,
)
from control_plane.workspace.platforms import PLATFORMS, platform_payload, support_for

router = APIRouter(prefix="/api", tags=["workspace"])

WORKSPACES_PATH = "/api/workspaces"

#: Upper bound on any name the user types. Long enough for a real project name, short enough
#: that a paste of a whole document is a refusal rather than a row nobody can render.
MAX_NAME_LENGTH = 120

#: The goal is prose the user writes for the agent, so it is allowed to be a paragraph. It is
#: still bounded: an unbounded text column reached through an HTTP body is a free allocation.
MAX_GOAL_LENGTH = 4000

#: Platform handle, e.g. a Reddit username. Bounded for the same reason.
MAX_HANDLE_LENGTH = 200

PROJECT_MODES = ("AUTONOMOUS", "REVIEW", "RESEARCH")
PROFILE_KINDS = ("PERSON", "BRAND")


class CreateWorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class CreateProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    goal: str = Field(default="", max_length=MAX_GOAL_LENGTH)
    mode: str = Field(default="AUTONOMOUS")


class UpdateProjectBody(BaseModel):
    """Every field optional: this is a PATCH and an absent field means "leave it alone".

    `extra="forbid"` matters more here than anywhere else in the module. Without it a typo in a
    field name would be silently ignored and the caller would be told the update succeeded,
    which is the comfortable failure this whole package keeps refusing.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    goal: str | None = Field(default=None, max_length=MAX_GOAL_LENGTH)
    mode: str | None = None
    paused: bool | None = None
    archived: bool | None = None
    #: 0026: ile przysługuje każdej następnej ROZMOWIE w tym projekcie. Trzy zachowania, jak w
    #: store: pominięte nie rusza, słownik ustawia, `conversation_budget_default = true` wraca do
    #: wartości domyślnej. Bez tego trzeciego nie dałoby się cofnąć własnego ustawienia.
    conversation_budget: dict[str, int] | None = None
    conversation_budget_default: bool = False


class CreateProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: str = Field(default="PERSON")


class CreateAccountBody(BaseModel):
    """A platform account, optionally attached to a profile in the same request.

    `person_profile_id` is optional because the two steps are genuinely separate in the product
    (ZAKRES-FUNKCJONALNY §4 P10: "kroki mogą być wykonywane oddzielnie"), and present because
    doing both in one request is what the screen actually does — a user adding a Reddit account
    under "Anna" should not have to perform two operations and be left with a half-attached
    account when the second one fails.
    """

    model_config = ConfigDict(extra="forbid")

    platform: str
    external_account_id: str = Field(min_length=1, max_length=MAX_HANDLE_LENGTH)
    person_profile_id: UUID | None = None


class AccountReferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    social_account_id: UUID


async def workspace_store(request: Request) -> WorkspaceStore:
    return WorkspaceStore(await pool_for(request.app))


async def _resolve(
    store: WorkspaceStore, operator: OperatorSession, workspace_id: UUID
) -> WorkspaceRow:
    """The workspace of this request, or 404. THE gate of every route with an id in the path."""
    found = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    return found


def _checked_platform(platform: str) -> str:
    if support_for(platform) is None:
        known = ", ".join(sorted(PLATFORMS))
        raise HTTPException(
            status_code=400,
            detail=f"nieznana platforma {platform!r}. Produkt zna: {known}",
        )
    return platform


def _checked_choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"nieznany {what} {value!r}. Dozwolone: {', '.join(allowed)}",
        )
    return value


def _checked_name(value: str, what: str) -> str:
    """A name that is only whitespace is a refusal, not a row rendered as an empty line."""
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail=f"{what} nie może być pustą nazwą")
    return trimmed


# ---------------------------------------------------------------------------
# Payloads. Written out by hand, one function per row type, so that a column added to the schema
# does not silently start travelling to the browser.
# ---------------------------------------------------------------------------


def workspace_payload(row: WorkspaceRow) -> dict[str, Any]:
    return {
        "workspace_id": str(row.id),
        "name": row.name,
        "created_at": row.created_at.isoformat(),
    }


#: Klucze budżetu rozmowy i ich granice. Górne, bo „milion tur" nie jest ustawieniem, tylko
#: pomyłką w pisaniu, a dolne, bo zero tur to rozmowa, która nie może się zacząć.
BUDGET_LIMITS: dict[str, tuple[int, int]] = {
    "max_turns": (1, 500),
    "max_tool_calls": (1, 5000),
    "max_minutes": (1, 240),
}


def _checked_budget(value: dict[str, int] | None) -> dict[str, int] | None:
    """Budżet z żądania, albo odmowa z nazwą pola po ludzku.

    Zamknięta lista kluczy sprawdzana TUTAJ, a nie tylko w bazie: CHECK w migracji odrzuciłby
    żądanie komunikatem PostgreSQL, a to jest zdanie dla nikogo. Tu odmowa mówi, co wpisać.
    """
    if value is None:
        return None
    cleaned: dict[str, int] = {}
    for key, number in value.items():
        bounds = BUDGET_LIMITS.get(key)
        if bounds is None:
            raise HTTPException(
                status_code=400,
                detail=f"nieznane ustawienie budżetu {key!r}; są trzy: "
                + ", ".join(sorted(BUDGET_LIMITS)),
            )
        low, high = bounds
        if not isinstance(number, int) or isinstance(number, bool) or not low <= number <= high:
            raise HTTPException(
                status_code=400,
                detail=f"{key}: liczba całkowita od {low} do {high}, a nie {number!r}",
            )
        cleaned[key] = number
    return cleaned or None


def _conversation_default() -> dict[str, int]:
    """Domyślny budżet rozmowy, wzięty stamtąd, gdzie obowiązuje: z workera.

    Import lokalny, bo `sessions.handlers` ciągnie za sobą silnik i kolejkę, a ten moduł jest
    o workspace'ach. Jedno źródło liczby zamiast dwóch: ekran pokazuje dokładnie to, czego użyje
    następna rozmowa.
    """
    from control_plane.sessions.handlers import DEFAULT_BUDGET

    return dict(DEFAULT_BUDGET)


def project_payload(row: ProjectRow) -> dict[str, Any]:
    return {
        "project_id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "name": row.name,
        "goal": row.goal,
        "mode": row.mode,
        "paused": row.paused_at is not None,
        "paused_at": row.paused_at.isoformat() if row.paused_at else None,
        "archived": row.archived_at is not None,
        "created_at": row.created_at.isoformat(),
        # 0026: `null` znaczy „nikt nie ustawił", więc ekran pokazuje wartość domyślną i mówi,
        # że nikt jej nie wybrał. Wartości domyślnej NIE mieszamy z ustawieniem: to dwa różne
        # zdania. Jedzie obok, żeby ekran nie musiał jej znać na pamięć; liczba wpisana na
        # sztywno w dashboardzie rozjechałaby się z backendem przy pierwszej zmianie i mówiłaby
        # człowiekowi o limicie, którego nie ma.
        "conversation_budget": row.conversation_budget,
        "conversation_budget_default": _conversation_default(),
    }


def profile_payload(row: PersonProfileRow) -> dict[str, Any]:
    return {
        "person_profile_id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "name": row.name,
        "kind": row.kind,
        "created_at": row.created_at.isoformat(),
        "archived": row.archived_at is not None,
    }


def account_payload(row: AccountRow) -> dict[str, Any]:
    """One account, WITH the capabilities of its platform attached.

    The capabilities travel with every account rather than being fetched once by the screen: a
    screen that looked them up separately could render an account before the matrix arrived, and
    an account with no stated capability is exactly the account that looks like a working
    integration.
    """
    support = support_for(row.platform)
    return {
        "social_account_id": str(row.id),
        "platform": row.platform,
        "external_account_id": row.external_account_id,
        "state": row.state,
        "created_at": row.created_at.isoformat(),
        "person_profile_id": str(row.person_profile_id) if row.person_profile_id else None,
        "attached_at": row.attached_at.isoformat() if row.attached_at else None,
        # None only if the platform left the closed list after a row was written with it. The
        # screen treats a missing capability block as "no integration", never as a working one.
        "support": support.as_payload() if support is not None else None,
    }


def attachment_payload(row: AttachmentRow) -> dict[str, Any]:
    return {
        "attachment_id": str(row.id),
        "person_profile_id": str(row.person_profile_id),
        "social_account_id": str(row.social_account_id),
        "platform": row.platform,
        "external_account_id": row.external_account_id,
        "attached_at": row.attached_at.isoformat(),
        "detached_at": row.detached_at.isoformat() if row.detached_at else None,
        "active": row.detached_at is None,
    }


def assignment_payload(row: AssignmentRow) -> dict[str, Any]:
    """One assignment, WITH the capabilities of its platform.

    They ride along for the same reason they ride along with an account: the project screen lists
    the identities a project works under, and that is exactly the list where an account on a
    platform with no integration would pass for a working one. The screen must not have to look
    the capabilities up somewhere else — the first version of this did, against the list of
    assignable candidates, which by construction excludes the accounts already assigned. Every
    assigned account therefore came out with no capabilities at all and the warning disappeared
    from the one screen that needed it. Found by the end-to-end suite.
    """
    support = support_for(row.platform)
    return {
        "support": support.as_payload() if support is not None else None,
        "assignment_id": str(row.id),
        "project_id": str(row.project_id),
        "social_account_id": str(row.social_account_id),
        "platform": row.platform,
        "external_account_id": row.external_account_id,
        "assigned_at": row.assigned_at.isoformat(),
        "unassigned_at": row.unassigned_at.isoformat() if row.unassigned_at else None,
        "active": row.unassigned_at is None,
    }


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    workspace_id: UUID | None,
    subject_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """One audit row, in its own transaction, after the write it describes.

    Deliberately not inside the store: `AuditWriter` refuses a connection with no open
    transaction, and the store's writes are single statements that asyncpg commits on their own.
    A row that failed to write therefore has no audit entry, and an audit entry here always
    describes a write that already happened.
    """
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=subject_ref,
            actor_ref=f"operator:{operator.operator_id}",
            payload=payload,
        )


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@router.get("/workspaces")
async def list_workspaces(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    rows = await store.list_workspaces(organization_id=operator.organization_id)
    return {
        "workspaces": [workspace_payload(row) for row in rows],
        "count": len(rows),
        # The capability matrix rides along with the list the shell already reads, so every
        # screen has it without a second round trip.
        "platforms": platform_payload(),
    }


@router.post("/workspaces", status_code=201)
async def create_workspace(
    body: CreateWorkspaceBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    created = await store.create_workspace(
        organization_id=operator.organization_id, name=_checked_name(body.name, "workspace")
    )
    await _audit(
        request,
        kind="WORKSPACE_CREATED",
        operator=operator,
        workspace_id=created.id,
        subject_ref=f"workspace:{created.id}",
        payload={"workspace_id": str(created.id)},
    )
    return {"workspace": workspace_payload(created)}


@router.get("/workspaces/{workspace_id}")
async def read_workspace(
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    found = await _resolve(store, operator, workspace_id)
    projects = await store.list_projects(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    return {
        "workspace": workspace_payload(found),
        "projects": [project_payload(row) for row in projects],
        "count": len(projects),
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/projects")
async def list_projects(
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    rows = await store.list_projects(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    return {"projects": [project_payload(row) for row in rows], "count": len(rows)}


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
async def create_project(
    workspace_id: UUID,
    body: CreateProjectBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    created = await store.create_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        name=_checked_name(body.name, "projekt"),
        goal=body.goal,
        mode=_checked_choice(body.mode, PROJECT_MODES, "tryb pracy"),
    )
    await _audit(
        request,
        kind="PROJECT_CREATED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"project:{created.id}",
        payload={"project_id": str(created.id), "mode": created.mode},
    )
    return {"project": project_payload(created)}


@router.get("/workspaces/{workspace_id}/projects/{project_id}")
async def read_project(
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """One project with the accounts assigned to it, and with the accounts it could use.

    `candidates` is the list the assignment panel offers: accounts attached to a profile of THIS
    workspace and not already assigned to THIS project. Computed here rather than in the screen,
    so the rule lives next to the data it is about.
    """
    await _resolve(store, operator, workspace_id)
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")

    assignments = await store.list_assignments(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    mine = [row for row in assignments if row.project_id == project_id]
    active_ids = {row.social_account_id for row in mine if row.unassigned_at is None}
    accounts = await store.list_accounts(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    candidates = [
        row for row in accounts if row.person_profile_id is not None and row.id not in active_ids
    ]
    return {
        "project": project_payload(project),
        "assignments": [assignment_payload(row) for row in mine],
        "candidates": [account_payload(row) for row in candidates],
    }


@router.patch("/workspaces/{workspace_id}/projects/{project_id}")
async def update_project(
    workspace_id: UUID,
    project_id: UUID,
    body: UpdateProjectBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    updated = await store.update_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        name=_checked_name(body.name, "projekt") if body.name is not None else None,
        goal=body.goal,
        mode=(
            _checked_choice(body.mode, PROJECT_MODES, "tryb pracy")
            if body.mode is not None
            else None
        ),
        paused=body.paused,
        archived=body.archived,
        conversation_budget=_checked_budget(body.conversation_budget),
        clear_conversation_budget=body.conversation_budget_default,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")
    await _audit(
        request,
        kind="PROJECT_UPDATED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"project:{project_id}",
        payload={"project_id": str(project_id), "mode": updated.mode},
    )
    return {"project": project_payload(updated)}


@router.post("/workspaces/{workspace_id}/projects/{project_id}/accounts", status_code=201)
async def assign_account(
    workspace_id: UUID,
    project_id: UUID,
    body: AccountReferenceBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """Assign one account to one project. The account must belong to THIS workspace.

    "Belong" means: attached to a person profile of this workspace. An account that is attached
    nowhere, or attached in another workspace, is refused — otherwise a project could publish
    under an identity its workspace does not own, which is the one mistake in this area that
    costs the user their reputation rather than a minute.
    """
    await _resolve(store, operator, workspace_id)
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")

    owned = {
        row.id
        for row in await store.list_accounts(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        if row.person_profile_id is not None
    }
    if body.social_account_id not in owned:
        raise HTTPException(
            status_code=400,
            detail=(
                "to konto nie należy do żadnego profilu w tym workspace. Najpierw przypnij je "
                "do profilu osoby, a dopiero potem przypisz do projektu"
            ),
        )

    try:
        created = await store.assign_account(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            social_account_id=body.social_account_id,
        )
    except WorkspaceRefusal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit(
        request,
        kind="ACCOUNT_ASSIGNED_TO_PROJECT",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"project:{project_id}",
        payload={
            "project_id": str(project_id),
            "social_account_id": str(body.social_account_id),
            "platform": created.platform,
        },
    )
    return {"assignment": assignment_payload(created)}


@router.delete(
    "/workspaces/{workspace_id}/projects/{project_id}/accounts/{social_account_id}",
    status_code=204,
)
async def unassign_account(
    workspace_id: UUID,
    project_id: UUID,
    social_account_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> None:
    """End the assignment. The row stays, with `unassigned_at` set."""
    await _resolve(store, operator, workspace_id)
    ended = await store.unassign_account(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        social_account_id=social_account_id,
    )
    if not ended:
        raise HTTPException(
            status_code=404,
            detail="ten projekt nie ma aktywnego przypisania tego konta",
        )
    await _audit(
        request,
        kind="ACCOUNT_UNASSIGNED_FROM_PROJECT",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"project:{project_id}",
        payload={"project_id": str(project_id), "social_account_id": str(social_account_id)},
    )


# ---------------------------------------------------------------------------
# Person profiles and platform accounts
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/profiles")
async def list_profiles(
    request: Request,
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """Everything the "Profile i konta" screen needs, in one read.

    Four lists rather than one nested tree: the screen groups them, and a tree would have to
    decide here what a detached account belongs under, which is precisely the question the
    screen answers differently in different places.
    """
    await _resolve(store, operator, workspace_id)
    profiles = await store.list_profiles(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    attachments = await store.list_attachments(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    assignments = await store.list_assignments(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    accounts = await store.list_accounts(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    projects = await store.list_projects(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    return {
        "profiles": [profile_payload(row) for row in profiles],
        "attachments": [attachment_payload(row) for row in attachments],
        "assignments": [assignment_payload(row) for row in assignments],
        "accounts": [account_payload(row) for row in accounts],
        "projects": [project_payload(row) for row in projects],
        "platforms": platform_payload(),
        # P6 etap A (27.09): the latest follower reading of each account that has one. An
        # account without a reading is absent from this list, never listed with a zero.
        "audience": await _audience_of(request, operator, [row.id for row in accounts]),
    }


async def _audience_of(
    request: Request, operator: OperatorSession, account_ids: list[UUID]
) -> list[dict[str, Any]]:
    from control_plane.workspace.audience import latest_followers

    pool = await pool_for(request.app)
    async with pool.acquire() as conn:
        readings = await latest_followers(
            conn, organization_id=operator.organization_id, account_ids=account_ids
        )
    return [reading.to_json() for reading in readings.values()]


@router.post("/workspaces/{workspace_id}/profiles", status_code=201)
async def create_profile(
    workspace_id: UUID,
    body: CreateProfileBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    created = await store.create_profile(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        name=_checked_name(body.name, "profil"),
        kind=_checked_choice(body.kind, PROFILE_KINDS, "rodzaj profilu"),
    )
    await _audit(
        request,
        kind="PERSON_PROFILE_CREATED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"person_profile:{created.id}",
        payload={"person_profile_id": str(created.id), "kind": created.kind},
    )
    return {"profile": profile_payload(created)}


@router.get("/workspaces/{workspace_id}/accounts")
async def list_accounts(
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    rows = await store.list_accounts(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    return {
        "accounts": [account_payload(row) for row in rows],
        "count": len(rows),
        "platforms": platform_payload(),
    }


@router.post("/workspaces/{workspace_id}/accounts", status_code=201)
async def register_account(
    workspace_id: UUID,
    body: CreateAccountBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """Register a platform account, and attach it to a profile when one was named.

    An account on a platform with no integration is ACCEPTED. That is the point of P1's last
    sentence: the model of profiles and accounts covers X and LinkedIn from the start, and the
    product says what it cannot do on them instead of refusing the identity.
    """
    await _resolve(store, operator, workspace_id)
    platform = _checked_platform(body.platform)
    handle = body.external_account_id.strip()
    if not handle:
        raise HTTPException(status_code=400, detail="konto musi mieć nazwę na platformie")

    profile: PersonProfileRow | None = None
    if body.person_profile_id is not None:
        profile = await store.get_profile(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            profile_id=body.person_profile_id,
        )
        if profile is None:
            raise HTTPException(status_code=404, detail=f"nie ma profilu {body.person_profile_id}")

    account_id, created = await store.upsert_account(
        organization_id=operator.organization_id,
        platform=platform,
        external_account_id=handle,
    )
    if created:
        await _audit(
            request,
            kind="SOCIAL_ACCOUNT_REGISTERED",
            operator=operator,
            workspace_id=workspace_id,
            subject_ref=f"social_account:{account_id}",
            payload={"social_account_id": str(account_id), "platform": platform, "created": True},
        )

    attachment: AttachmentRow | None = None
    if profile is not None:
        try:
            attachment = await store.attach_account(
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                person_profile_id=profile.id,
                social_account_id=account_id,
            )
        except WorkspaceRefusal as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await _audit(
            request,
            kind="ACCOUNT_ATTACHED_TO_PROFILE",
            operator=operator,
            workspace_id=workspace_id,
            subject_ref=f"person_profile:{profile.id}",
            payload={
                "person_profile_id": str(profile.id),
                "social_account_id": str(account_id),
                "platform": platform,
            },
        )

    row = await store.account(organization_id=operator.organization_id, account_id=account_id)
    if row is None:  # pragma: no cover - the row was just written in this request
        raise HTTPException(status_code=500, detail="konto zniknęło zaraz po zapisie")
    return {
        "account": account_payload(row),
        # `false` means the handle was already registered in this organization. The screen says
        # so rather than claiming to have created something.
        "created": created,
        "attachment": attachment_payload(attachment) if attachment is not None else None,
    }


@router.post("/workspaces/{workspace_id}/profiles/{profile_id}/accounts", status_code=201)
async def attach_account(
    workspace_id: UUID,
    profile_id: UUID,
    body: AccountReferenceBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    await _resolve(store, operator, workspace_id)
    profile = await store.get_profile(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        profile_id=profile_id,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail=f"nie ma profilu {profile_id}")
    account = await store.account(
        organization_id=operator.organization_id, account_id=body.social_account_id
    )
    if account is None:
        raise HTTPException(status_code=404, detail=f"nie ma konta {body.social_account_id}")

    try:
        created = await store.attach_account(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            person_profile_id=profile_id,
            social_account_id=body.social_account_id,
        )
    except WorkspaceRefusal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit(
        request,
        kind="ACCOUNT_ATTACHED_TO_PROFILE",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"person_profile:{profile_id}",
        payload={
            "person_profile_id": str(profile_id),
            "social_account_id": str(body.social_account_id),
            "platform": created.platform,
        },
    )
    return {"attachment": attachment_payload(created)}


@router.delete(
    "/workspaces/{workspace_id}/profiles/{profile_id}/accounts/{social_account_id}",
    status_code=204,
)
async def detach_account(
    workspace_id: UUID,
    profile_id: UUID,
    social_account_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> None:
    """Detach. The attachment row stays as history, with `detached_at` set.

    The account's ASSIGNMENTS to projects are left alone on purpose. Ending them here would
    silently rewrite the history of which identity a project worked under; the screen shows the
    assignment as belonging to a detached account, and the user decides.
    """
    await _resolve(store, operator, workspace_id)
    ended = await store.detach_account(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        person_profile_id=profile_id,
        social_account_id=social_account_id,
    )
    if not ended:
        raise HTTPException(
            status_code=404, detail="ten profil nie ma aktywnego przypięcia tego konta"
        )
    await _audit(
        request,
        kind="ACCOUNT_DETACHED_FROM_PROFILE",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"person_profile:{profile_id}",
        payload={
            "person_profile_id": str(profile_id),
            "social_account_id": str(social_account_id),
        },
    )


# ---------------------------------------------------------------------------
# What actually happened
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/results")
async def read_results(
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """The workspace's real results, from the tables that already hold them.

    Empty is a normal answer and the screen says "brak danych", not "0 sukcesów": this workspace
    may be new, and a zero with no reading behind it is the substitution ZAKRES-FUNKCJONALNY §6
    rule 2 forbids.
    """
    await _resolve(store, operator, workspace_id)
    return await store.results(organization_id=operator.organization_id, workspace_id=workspace_id)


@router.get("/workspaces/{workspace_id}/projects/{project_id}/results")
async def read_project_results(
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
) -> dict[str, Any]:
    """Co wysłał w świat TEN projekt (E6, audyt UX 20.09 punkt 6).

    OSOBNA TRASA, NIE PARAMETR TAMTEJ. Odczyt przestrzeni niesie też społeczności, a te należą
    do przestrzeni; ta trasa ich nie ma i dlatego nie da się jej odczytać jako „wszystko o
    projekcie". Ekran, który pokaże tę listę pod nazwą projektu, pokaże pracę tego projektu.

    Pusto jest normalną odpowiedzią i znaczy „ten projekt jeszcze nic nie wysłał". Historia
    całej przestrzeni stoi pod `GET /workspaces/{id}/results` i tam jest tak nazwana.
    """
    await _resolve(store, operator, workspace_id)
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")
    return await store.project_results(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
