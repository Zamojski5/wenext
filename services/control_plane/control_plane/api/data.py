"""`/api/organizations/{id}/data` — taking your data out, and having it deleted (P7 etap A).

    POST /exports              ask for a file with everything this organization put in
    GET  /exports              what was asked for and what is ready
    GET  /exports/{id}/file    download it
    POST /deletion             delete this organization, with a grace period
    DELETE /deletion           change your mind, while the grace period lasts

WHY THESE ROUTES EXIST AT ALL. A service that can be joined without asking anybody and left only
by writing to its owner is one people are right not to trust. The milestone says it as a
criterion; these five routes are the smallest honest answer to it.

THREE DECISIONS VISIBLE IN THE CODE:

  * **the export is built by a worker, not by the request.** A whole organization can be large,
    and an HTTP handler holding that is a worker nobody gets back. The route answers 202 and the
    job writes the file; the screen shows the state;
  * **deleting asks for the password again.** Not a confirmation checkbox: this is the one
    action in the product that destroys somebody's work, and the person doing it should have to
    prove they are still the person;
  * **credentials die immediately, data dies after the grace period.** A grace period on a
    stolen credential is a grace period for whoever stole it; a grace period on the data is what
    lets a mistake be undone. The two are separated on purpose and the response says both.

WHAT DELETION DOES NOT REMOVE, said here as well as in `identity/data.py`: `audit_events`. The
table is append-only by trigger, and a log the subject of the log can erase is not a log.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api.auth import identity_store
from control_plane.api.organizations import _here, _owner_only
from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.identity.data import DataStore
from control_plane.identity.store import MAX_CREDENTIAL_LENGTH, IdentityStore

router = APIRouter(prefix="/api/organizations", tags=["dane organizacji"])


class DeletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The password again. See the module docstring: this is the one destructive action here.
    password: str = Field(min_length=1, max_length=MAX_CREDENTIAL_LENGTH)


async def data_store(request: Request) -> DataStore:
    return DataStore(await pool_for(request.app))


def _job_payload(job) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "status": job.status,
        "requested_at": job.requested_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "purge_after": job.purge_after.isoformat() if job.purge_after else None,
        "detail": job.detail,
        "has_file": getattr(job, "artifact_id", None) is not None,
    }


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    organization_id: UUID,
    subject_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=organization_id,
            subject_ref=subject_ref,
            actor_ref=f"user:{operator.user_id or operator.operator_id}",
            payload=payload,
        )


@router.post("/{organization_id}/data/exports", status_code=202)
async def request_export(
    organization_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    identity: Annotated[IdentityStore, Depends(identity_store)],
    data: Annotated[DataStore, Depends(data_store)],
) -> dict[str, Any]:
    """Ask for the file. 202, because a worker builds it."""
    membership = await _here(operator, organization_id, identity)
    _owner_only(membership)
    job = await data.request_export(
        organization_id=organization_id,
        requested_by=operator.user_id,  # type: ignore[arg-type]  # `_here` refused None
    )
    from control_plane.identity.handlers import schedule_export
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    pool = await pool_for(request.app)
    await schedule_export(
        PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
        job_id=job.id,
    )
    await _audit(
        request,
        kind="DATA_EXPORT_REQUESTED",
        operator=operator,
        organization_id=organization_id,
        subject_ref=f"export:{job.id}",
        payload={"job_id": str(job.id)},
    )
    return {"export": _job_payload(job)}


@router.get("/{organization_id}/data/exports")
async def list_exports(
    organization_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    identity: Annotated[IdentityStore, Depends(identity_store)],
    data: Annotated[DataStore, Depends(data_store)],
) -> dict[str, Any]:
    membership = await _here(operator, organization_id, identity)
    _owner_only(membership)
    return {"exports": [_job_payload(job) for job in await data.exports(organization_id)]}


@router.get("/{organization_id}/data/exports/{job_id}/file")
async def download_export(
    organization_id: UUID,
    job_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    identity: Annotated[IdentityStore, Depends(identity_store)],
    data: Annotated[DataStore, Depends(data_store)],
) -> Response:
    """The file itself. 404 while it is not ready, with the state in the sentence."""
    membership = await _here(operator, organization_id, identity)
    _owner_only(membership)
    job = await data.export_job(organization_id=organization_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"nie ma eksportu {job_id}")
    if job.status != "READY" or job.artifact_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"eksport nie jest gotowy (stan {job.status}). Odśwież za chwilę",
        )

    from control_plane.knowledge.ingest import artifact_volume
    from control_plane.storage.artifacts import ArtifactReadRefused, ArtifactStore

    pool = await pool_for(request.app)
    volume = getattr(request.app.state, "artifact_volume", None) or artifact_volume()
    try:
        async with pool.acquire() as connection:
            content = await ArtifactStore().read(
                connection,
                organization_id=organization_id,
                artifact_id=job.artifact_id,
                reader="OPERATOR",
                volume=volume,
            )
    except (ArtifactReadRefused, OSError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"pliku eksportu już nie ma ({type(exc).__name__}). Zamów go ponownie",
        ) from exc
    return Response(
        content=content,
        media_type="application/json",
        headers={"content-disposition": f'attachment; filename="wenext-export-{job_id}.json"'},
    )


@router.post("/{organization_id}/data/deletion", status_code=202)
async def request_deletion(
    organization_id: UUID,
    body: DeletionBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    identity: Annotated[IdentityStore, Depends(identity_store)],
    data: Annotated[DataStore, Depends(data_store)],
) -> dict[str, Any]:
    """Delete this organization. Access and secrets go now; the data goes after the grace period."""
    membership = await _here(operator, organization_id, identity)
    _owner_only(membership)
    if not await identity.verify_password_of(operator.user_id, body.password):  # type: ignore[arg-type]
        raise HTTPException(
            status_code=403, detail="hasło się nie zgadza. Usunięcie przestrzeni wymaga hasła"
        )
    if await data.pending_deletion(organization_id) is not None:
        raise HTTPException(status_code=409, detail="usunięcie tej przestrzeni już trwa")

    job = await data.request_deletion(
        organization_id=organization_id,
        requested_by=operator.user_id,  # type: ignore[arg-type]
    )
    # Secrets first and without waiting: a grace period on a credential is a grace period for
    # whoever stole it. The home of the volume root is never touched (see `identity/data.py`).
    from control_plane.workspace.codex_home import home_root

    removed = await data.destroy_secrets(organization_id=organization_id, home_root=home_root())
    await _audit(
        request,
        kind="ORGANIZATION_DELETION_REQUESTED",
        operator=operator,
        organization_id=organization_id,
        subject_ref=f"organization:{organization_id}",
        payload={"job_id": str(job.id), "count": removed},
    )
    return {
        "deletion": _job_payload(job),
        "secrets_destroyed": True,
        "codex_homes_removed": removed,
        "uwaga": (
            "Dostęp do tej przestrzeni jest już zablokowany, a poświadczenia platform "
            "i katalogi Codeksa usunięte. Dane własne znikną po terminie karencji; do tego "
            "czasu można to cofnąć."
        ),
    }


@router.delete("/{organization_id}/data/deletion", status_code=200)
async def cancel_deletion(
    organization_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    identity: Annotated[IdentityStore, Depends(identity_store)],
    data: Annotated[DataStore, Depends(data_store)],
) -> dict[str, Any]:
    """Change your mind while the grace period lasts.

    REACHABLE ONLY BY A SESSION OF ANOTHER ORGANIZATION OR BY THE OPERATOR, and that is an
    honest limitation rather than a design: a pending deletion blocks the membership check, so
    the person who asked for it cannot come back through this route themselves. Today undoing it
    means asking the deployment's owner; the screen says so.
    """
    if operator.organization_id != organization_id and operator.user_id is not None:
        raise HTTPException(status_code=404, detail=f"nie ma przestrzeni {organization_id}")
    cancelled = await data.cancel_deletion(organization_id=organization_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="nie ma trwającego usunięcia tej przestrzeni")
    await _audit(
        request,
        kind="ORGANIZATION_DELETION_CANCELLED",
        operator=operator,
        organization_id=organization_id,
        subject_ref=f"organization:{organization_id}",
    )
    return {"cancelled": True}
