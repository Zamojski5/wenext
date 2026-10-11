"""`/api/codex-connections` — access to the ENGINE, which is not a social account.

ZAKRES-FUNKCJONALNY §2 names this as the first distinction the interface may not blur: a social
account is the identity the agent writes under in public, a Codex connection is the access to the
model that does the work. Confusing them costs the user money or a post under the wrong name. So
they live behind different routes, different screens and different tables, and neither payload
mentions the other.

WHAT THIS MODULE REFUSES TO INVENT:

  * **the volume root is not adopted automatically.** The founder's existing `CODEX_HOME` is
    where the live pilot runs. A row created for it by a migration or by a first request would
    claim that somebody registered that connection. `GET` reports it as `adoptable`, and a
    deliberate `POST /adopt-root` creates the row — writing nothing into that directory.
  * **nothing here talks to the app-server.** Starting a device-code login means holding a
    process for up to fifteen minutes while a human types a code; that is a queue job
    (`workspace/handlers.py`), and these routes only enqueue it and read the row it writes.
  * **limits are rendered field by field.** In the pinned contract the only required field of a
    rate-limit window is `usedPercent`; `resetsAt` and `windowDurationMins` may be absent. So
    "nie wiemy" is a state of a single field, and this payload carries `null` for it rather than
    dropping the field or filling in a zero (ZAKRES-FUNKCJONALNY §3E, §6 rule 2).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.sessions.rate_limit import moment_from_epoch
from control_plane.workspace import WorkspaceStore
from control_plane.workspace.codex_home import (
    ROOT_READ_DISABLED_REASON,
    ROOT_REF,
    home_facts,
    home_root,
    resolve_home,
)
from control_plane.workspace.connections import (
    CodexConnectionRow,
    CodexConnectionStore,
    HomeAlreadyLive,
)
from control_plane.workspace.handlers import schedule_login, schedule_read

router = APIRouter(prefix="/api", tags=["codex"])

CODEX_CONNECTIONS_PATH = "/api/codex-connections"

MAX_LABEL_LENGTH = 120


class CreateConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    #: `null` (the default) means "available to every workspace of this organization". A value
    #: narrows it to one; WIZJA-WORKSPACE allows both and the screen says which.
    workspace_id: UUID | None = None


class AdoptRootBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="Konto z konfiguracji", min_length=1, max_length=MAX_LABEL_LENGTH)
    workspace_id: UUID | None = None


async def connection_store(request: Request) -> CodexConnectionStore:
    return CodexConnectionStore(await pool_for(request.app))


def _checked_label(value: str) -> str:
    """Trim, then REFUSE what is left when it is empty.

    Pydantic's `min_length=1` counts characters, so `"\t"` reaches here; stripping it and passing
    the result on would store an empty label, and the database CHECK would not catch it either
    until it was widened (measured: `btrim(x)` with no second argument trims spaces only). Two
    halves of one rule: strip, then check what the strip produced.
    """
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail="połączenie musi mieć nazwę")
    return trimmed


def _reset_moment(value: Any) -> str | None:
    """Chwila odnowienia okna jako napis ISO 8601 w UTC, albo `None`.

    TRZY WEJŚCIA, BO SILNIK MOŻE ZMIENIĆ ZDANIE: liczba (epoka, dziś sekundy), napis (zostaje
    jak jest, bo to już jest chwila w formacie, którego ekran oczekuje) i wszystko inne, czyli
    brak. Liczba nierozpoznana jako epoka daje `None`, a nie własną interpretację: „nie wiem"
    jest na ekranie uczciwe, a zgadnięta godzina nie.
    """
    if isinstance(value, str):
        return value or None
    when = moment_from_epoch(value)
    return when.isoformat() if when is not None else None


def _windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`primary` and `secondary` flattened, with `null` where the engine said nothing.

    Both windows are ALWAYS listed, even when absent from the payload: a window the screen cannot
    see is indistinguishable from a window at zero percent, and one of those is a measurement.

    `resets_at` WYCHODZI STĄD JAKO NAPIS ISO, nie jako liczba, i to jest poprawka z 20.09: silnik
    podaje epokę w sekundach (`resetsAt: 1789885996`), a schemat ekranu wymaga `string | null`, bo
    to jest chwila do pokazania człowiekowi. Ekran „Połączenia Codex" odrzucał więc cały odczyt
    zdaniem „brakuje pól albo mają zły typ (resets_at)" o połączeniu, które było zdrowe.
    Przeliczenie robi `sessions.rate_limit.moment_from_epoch`, ta sama funkcja, którą liczy się
    powrót po limicie: dwa własne rozpoznania tej samej liczby już raz się rozjechały (D-045).
    """
    snapshot = payload.get("rateLimits") or {}
    windows: list[dict[str, Any]] = []
    for name in ("primary", "secondary"):
        window = snapshot.get(name)
        if not isinstance(window, dict):
            windows.append(
                {
                    "name": name,
                    "present": False,
                    "used_percent": None,
                    "resets_at": None,
                    "window_minutes": None,
                }
            )
            continue
        windows.append(
            {
                "name": name,
                "present": True,
                "used_percent": window.get("usedPercent"),
                # Optional in the pinned contract. `null` means the engine did not say, and the
                # screen prints exactly that.
                "resets_at": _reset_moment(window.get("resetsAt")),
                "window_minutes": window.get("windowDurationMins"),
            }
        )
    return windows


def _limits_payload(rate_limits: dict[str, Any]) -> dict[str, Any]:
    if not rate_limits.get("read"):
        return {
            "read": False,
            "reason": rate_limits.get("reason") or "limitów nie odczytano",
            "at": None,
            "windows": [],
            "plan_type": None,
        }
    payload = rate_limits.get("payload") or {}
    snapshot = payload.get("rateLimits") or {}
    return {
        "read": True,
        "reason": None,
        "at": rate_limits.get("at"),
        "windows": _windows(payload),
        "plan_type": snapshot.get("planType"),
    }


def _models_payload(models: dict[str, Any]) -> dict[str, Any]:
    """The catalogue with its completeness flag, which is not decoration.

    A snapshot that stopped before `nextCursor = null` must not be routed on (L2189), so
    `complete: false` travels to the screen and the screen says the catalogue is incomplete
    rather than listing what it happens to have as if that were all of it.
    """
    return {
        "complete": bool(models.get("complete", False)),
        "count": models.get("count", 0) if models else 0,
        "models": models.get("models", []) if models else [],
        "fetched_at": models.get("fetched_at"),
        "reason": models.get("reason"),
        "read": bool(models),
    }


def connection_payload(row: CodexConnectionRow, *, home: dict[str, Any]) -> dict[str, Any]:
    return {
        "connection_id": str(row.id),
        "workspace_id": str(row.workspace_id) if row.workspace_id else None,
        "scope": "workspace" if row.workspace_id else "organization",
        "label": row.label,
        "home_ref": row.home_ref,
        "status": row.status,
        # May be null WITH a logged-in account: the pinned schema allows `email: null`, so the
        # screen says "e-mail nieujawniony" and never reads this as "not logged in".
        "account_email": row.account_email,
        "plan_type": row.plan_type,
        "installation_id": row.installation_id,
        "models": _models_payload(row.models),
        "limits": _limits_payload(row.rate_limits),
        "login": row.login_ticket or None,
        "last_read_at": row.last_read_at.isoformat() if row.last_read_at else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat(),
        "disconnected_at": row.disconnected_at.isoformat() if row.disconnected_at else None,
        "active": row.disconnected_at is None,
        # What is really on disk, read without starting an app-server. `has_auth` is weaker than
        # `account/read` and is labelled as such on the screen.
        "home": home,
        # Whether this product will open an app-server on this directory at all. A BOOLEAN from
        # the backend rather than a sentence the screen matches on: the reason is prose and prose
        # gets edited, while a screen keying off it would silently stop showing the state.
        "engine_read": {
            "enabled": row.home_ref != ROOT_REF,
            "reason": ROOT_READ_DISABLED_REASON if row.home_ref == ROOT_REF else None,
        },
    }


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    connection_id: UUID,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=operator.organization_id,
            workspace_id=None,
            subject_ref=f"codex_connection:{connection_id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload=payload,
        )


async def _queue(request: Request, operator: OperatorSession) -> PostgresJobTransport:
    pool = await pool_for(request.app)
    return PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)


def _home_of(row: CodexConnectionRow) -> dict[str, Any]:
    root = home_root()
    if root is None:
        return {"configured": False, "exists": False, "has_auth": False, "installation_id": None}
    try:
        facts = home_facts(resolve_home(root, row.home_ref))
    except ValueError as exc:
        # A row whose `home_ref` cannot be turned into a path is a fact worth showing, not a 500.
        return {
            "configured": True,
            "exists": False,
            "has_auth": False,
            "installation_id": None,
            "reason": str(exc),
        }
    return {"configured": True, **facts}


@router.get("/codex-connections")
async def list_connections(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> dict[str, Any]:
    """Every connection of the organization, plus the state of the configured volume root.

    `root` is the honest answer to "what about the account this deployment was already using":
    it says whether `WENEXT_CODEX_HOME` is configured, whether an `auth.json` is really there,
    and whether any live connection has adopted it yet.
    """
    rows = await store.list_connections(organization_id=operator.organization_id)
    taken = await store.live_home_refs(organization_id=operator.organization_id)
    root = home_root()
    root_state: dict[str, Any] = {"configured": root is not None, "adopted": ROOT_REF in taken}
    if root is not None:
        root_state.update(home_facts(resolve_home(root, ROOT_REF)))
        root_state["path"] = str(root)
    return {
        "connections": [connection_payload(row, home=_home_of(row)) for row in rows],
        "count": len(rows),
        "root": root_state,
    }


@router.post("/codex-connections/adopt-root", status_code=201)
async def adopt_root(
    body: AdoptRootBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> dict[str, Any]:
    """Register the configured `CODEX_HOME` as a connection. WRITES NOTHING INTO THAT DIRECTORY.

    The directory holds the founder's `auth.json` and the `config.toml` written when that account
    was logged in; the live pilot runs on it. Adoption creates a ROW and enqueues one read, so
    the screen can say what the engine reports — and `prepare_connection_home` refuses this
    `home_ref` outright, so no code path here can rewrite the hardening under a running pilot.
    """
    root = home_root()
    if root is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "to wdrożenie nie ma skonfigurowanego WENEXT_CODEX_HOME, więc nie ma czego "
                "przyjąć jako połączenie"
            ),
        )
    await _checked_workspace(request, operator, body.workspace_id)
    try:
        created = await store.create(
            organization_id=operator.organization_id,
            workspace_id=body.workspace_id,
            label=_checked_label(body.label),
            home_ref=ROOT_REF,
        )
    except HomeAlreadyLive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit(
        request,
        kind="CODEX_CONNECTION_REGISTERED",
        operator=operator,
        connection_id=created.id,
        payload={"connection_id": str(created.id), "home_ref": ROOT_REF},
    )
    await schedule_read(
        await _queue(request, operator),
        organization_id=operator.organization_id,
        connection_id=created.id,
    )
    return {"connection": connection_payload(created, home=_home_of(created))}


@router.post("/codex-connections", status_code=201)
async def create_connection(
    body: CreateConnectionBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> dict[str, Any]:
    """A NEW connection: its own subdirectory, its own hardened config, its own login.

    The identifier of the row IS the name of the directory, so the two cannot drift apart and
    nobody has to keep a second mapping anywhere.
    """
    root = home_root()
    if root is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "to wdrożenie nie ma skonfigurowanego WENEXT_CODEX_HOME, więc nowe połączenie "
                "nie miałoby gdzie trzymać swojego katalogu"
            ),
        )
    await _checked_workspace(request, operator, body.workspace_id)

    connection_id = uuid.uuid4()
    home_ref = str(connection_id)
    # The directory FIRST: a row pointing at a directory that was never created would show as a
    # connection whose every read fails, with nothing saying why.
    from control_plane.workspace.codex_home import prepare_connection_home

    try:
        prepare_connection_home(root, home_ref)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"nie udało się przygotować katalogu połączenia: {exc}"
        ) from exc

    try:
        created = await store.create(
            organization_id=operator.organization_id,
            workspace_id=body.workspace_id,
            label=_checked_label(body.label),
            home_ref=home_ref,
            connection_id=connection_id,
        )
    except HomeAlreadyLive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit(
        request,
        kind="CODEX_CONNECTION_REGISTERED",
        operator=operator,
        connection_id=created.id,
        payload={"connection_id": str(created.id), "home_ref": home_ref},
    )
    await schedule_login(
        await _queue(request, operator),
        organization_id=operator.organization_id,
        connection_id=created.id,
    )
    await _audit(
        request,
        kind="CODEX_CONNECTION_LOGIN_STARTED",
        operator=operator,
        connection_id=created.id,
        payload={"connection_id": str(created.id)},
    )
    return {"connection": connection_payload(created, home=_home_of(created))}


@router.post("/codex-connections/{connection_id}/refresh", status_code=202)
async def refresh_connection(
    connection_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> dict[str, Any]:
    """Ask the queue to re-read this connection. 202: the answer comes to the row, not here."""
    row = await store.get(organization_id=operator.organization_id, connection_id=connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma połączenia {connection_id}")
    if row.disconnected_at is not None:
        raise HTTPException(
            status_code=409, detail="to połączenie jest odłączone — nie ma czego odczytywać"
        )
    if row.home_ref == ROOT_REF:
        raise HTTPException(status_code=409, detail=ROOT_READ_DISABLED_REASON)
    await schedule_read(
        await _queue(request, operator),
        organization_id=operator.organization_id,
        connection_id=connection_id,
    )
    return {"scheduled": True, "connection_id": str(connection_id)}


@router.post("/codex-connections/{connection_id}/login", status_code=202)
async def restart_login(
    connection_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> dict[str, Any]:
    """Start a device-code login again, after one expired or was refused.

    A separate button rather than an automatic retry: a retry would start a second code while
    the human is still reading the first one, and the code on the screen would silently die.
    """
    row = await store.get(organization_id=operator.organization_id, connection_id=connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma połączenia {connection_id}")
    if row.disconnected_at is not None:
        raise HTTPException(status_code=409, detail="to połączenie jest odłączone")
    if row.home_ref == ROOT_REF:
        # A login opens an app-server on the directory, which is the whole thing we refuse to do
        # here. Logging that account in again is a step the founder takes outside this screen.
        raise HTTPException(status_code=409, detail=ROOT_READ_DISABLED_REASON)
    await schedule_login(
        await _queue(request, operator),
        organization_id=operator.organization_id,
        connection_id=connection_id,
    )
    await _audit(
        request,
        kind="CODEX_CONNECTION_LOGIN_STARTED",
        operator=operator,
        connection_id=connection_id,
        payload={"connection_id": str(connection_id)},
    )
    return {"scheduled": True, "connection_id": str(connection_id)}


@router.delete("/codex-connections/{connection_id}", status_code=204)
async def disconnect_connection(
    connection_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[CodexConnectionStore, Depends(connection_store)],
) -> None:
    """Stop using this connection. NOTHING IS DELETED and the account is NOT logged out.

    The row stays with its `disconnected_at`, the directory stays with its `auth.json`, and no
    `account/logout` is sent. Signing the account out is a different request with a different
    consequence — it invalidates the session for everything else using that account — and the
    product does not perform it because somebody pressed "odłącz".
    """
    ended = await store.disconnect(
        organization_id=operator.organization_id, connection_id=connection_id
    )
    if not ended:
        raise HTTPException(
            status_code=404, detail="nie ma aktywnego połączenia o tym identyfikatorze"
        )
    await _audit(
        request,
        kind="CODEX_CONNECTION_DISCONNECTED",
        operator=operator,
        connection_id=connection_id,
        payload={"connection_id": str(connection_id)},
    )


async def _checked_workspace(
    request: Request, operator: OperatorSession, workspace_id: UUID | None
) -> None:
    """A narrowed connection must name a workspace of THIS organization. `None` is the wide case."""
    if workspace_id is None:
        return
    store = WorkspaceStore(await pool_for(request.app))
    found = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
