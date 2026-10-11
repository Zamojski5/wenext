"""`/api/user-profile` — the four fields the judge reads about the person it represents.

`docs/OUTCOMES.md` §3 point 4 and §6 point 2: what the user knows deeply, the positions they
hold, what they would never claim, and a few samples of their voice. Versioned, never edited:
a judgment is comparable only with judgments grounded in the same version.

Saving a profile is what UNBLOCKS judging — without one, `judge.thread` parks in
`WAITING_CAPABILITY` — so the write also runs the judgment sweep at once, instead of waiting for
the next poll to notice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.handlers import ObserveHandlers
from control_plane.observe.store import ObservationStore, ProfileRow
from control_plane.pilot.handlers import PilotGuard
from control_plane.pilot.store import PilotStore
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["user profile"])

USER_PROFILE_PATH = "/api/user-profile"

#: Enough for "20-40 lines" (`docs/OUTCOMES.md` §6 point 2) with room; not a book.
FIELD_MAX_CHARS = 8000


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knows_deeply: str = Field(min_length=1, max_length=FIELD_MAX_CHARS)
    positions: str = Field(min_length=1, max_length=FIELD_MAX_CHARS)
    would_never_claim: str = Field(min_length=1, max_length=FIELD_MAX_CHARS)
    voice_samples: str = Field(default="", max_length=FIELD_MAX_CHARS)


async def observation_store(request: Request) -> ObservationStore:
    return ObservationStore(await pool_for(request.app))


def profile_payload(row: ProfileRow) -> dict[str, Any]:
    return {
        "profile_id": str(row.id),
        "version": row.version,
        "knows_deeply": row.knows_deeply,
        "positions": row.positions,
        "would_never_claim": row.would_never_claim,
        "voice_samples": row.voice_samples,
        "created_at": row.created_at.isoformat(),
    }


async def _workspace(request: Request, operator: OperatorSession):
    """The workspace this pre-pivot screen works in: the organization's OLDEST.

    It used to be `ObservationStore.sole_workspace`, which answers None as soon as there are two.
    P1 (ROADMAP-PIVOT, D-040) makes a second workspace something the user creates from the
    studio, and the day that landed this route began answering 409 for the whole organization —
    measured, not predicted: the end-to-end suite caught `/community` rendering "brak odpowiedzi"
    for the profile as soon as a second workspace existed.

    `default_workspace` is not a guess: the oldest workspace is where the pilot, its communities
    and its profile versions already live, so these screens keep describing exactly the rows they
    described before. No workspace at all is still a 409.
    """
    workspace_id = await WorkspaceStore(await pool_for(request.app)).default_workspace(
        organization_id=operator.organization_id
    )
    if workspace_id is None:
        raise HTTPException(
            status_code=409,
            detail="organizacja nie ma ani jednego workspace'u — nie ma czego opisać",
        )
    return workspace_id


@router.get("/user-profile")
async def read_profile(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    workspace_id = await _workspace(request, operator)
    row = await store.latest_profile(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="brak profilu użytkownika — osądy czekają")
    return profile_payload(row)


@router.post("/user-profile", status_code=201)
async def save_profile(
    body: ProfileBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    workspace_id = await _workspace(request, operator)
    row = await store.save_profile(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        knows_deeply=body.knows_deeply,
        positions=body.positions,
        would_never_claim=body.would_never_claim,
        voice_samples=body.voice_samples,
    )
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="USER_PROFILE_SAVED",
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=f"user_profile:{row.id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"version": row.version},
        )
    queue = PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)
    handlers = ObserveHandlers(
        store=store,
        queue=queue,
        sources={},
        turns=None,
        organization_id=operator.organization_id,
        # A community with an active 24 h pilot gets no judgments from this sweep either: the
        # pilot's manifest owns that community's model budget (docs/START-PILOTA-24H.md).
        pilot_guard=PilotGuard(PilotStore(pool), queue, organization_id=operator.organization_id),
    )
    scheduled = await handlers.sweep_judgments(workspace_id=workspace_id, now=datetime.now(UTC))
    return {"profile": profile_payload(row), "judgments_scheduled": scheduled}
