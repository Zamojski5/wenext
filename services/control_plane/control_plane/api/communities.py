"""`/api/communities` — which real community is observed, and its latest picture.

Outcome A (`docs/OUTCOMES.md` §3 point 1): the founder names ONE community; the system does the
rest. So the write here is small — platform, key, poll interval — and everything that follows
(first poll, snapshots, judgments, the daily picture) is scheduled work, visible on `/jobs`.

Every route sits behind the operator boundary (mounted by `control_plane.app.discover_routers`)
and reads the organization out of the session, never out of the body. The workspace is the
organization's OLDEST (`WorkspaceStore.default_workspace`), not "the only one": since P1 the user
creates further workspaces from the studio, and a route that refused as soon as a second one
existed took the whole operator panel down with it. No workspace at all is still a 409.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from community_os_contracts import ObservationPlatform
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.observe.membership import MembershipRefused, start_observing
from control_plane.observe.store import CommunityRow, ObservationStore, PictureRow, VoiceCardRow
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["community"])

COMMUNITIES_PATH = "/api/communities"

#: Ten minutes between reads of one community. Not a D-017 threshold; the platform's own limits
#: are two orders of magnitude away. The CHECK in 0004 refuses anything under 60 s.
DEFAULT_POLL_INTERVAL_SECONDS = 600
MIN_POLL_INTERVAL_SECONDS = 60
MAX_POLL_INTERVAL_SECONDS = 86400


class EnableCommunityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: ObservationPlatform
    community_key: str = Field(min_length=1, max_length=200)
    poll_interval_seconds: int = Field(
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        ge=MIN_POLL_INTERVAL_SECONDS,
        le=MAX_POLL_INTERVAL_SECONDS,
    )


async def observation_store(request: Request) -> ObservationStore:
    return ObservationStore(await pool_for(request.app))


def community_payload(row: CommunityRow, counts: dict[str, int] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "community_id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "platform": row.platform.value,
        "community_key": row.community_key,
        "ref": row.ref,
        "display_name": row.display_name,
        "access_mode": row.access_mode.value,
        "observe_enabled": row.observe_enabled,
        "poll_interval_seconds": row.poll_interval_seconds,
        "last_polled_at": row.last_polled_at.isoformat() if row.last_polled_at else None,
        "watermark_created_at": (
            row.watermark_created_at.isoformat() if row.watermark_created_at else None
        ),
        "created_at": row.created_at.isoformat(),
        # 0025: decyzja człowieka o własnych wątkach. TRZY STANY, więc `null` nie jest tu
        # brakiem danych, tylko stanem „nikt nie zdecydował", i ekran ma go pokazać jako pytanie.
        "own_posts_allowed": row.own_posts_allowed,
        "own_posts_decided_at": (
            row.own_posts_decided_at.isoformat() if row.own_posts_decided_at else None
        ),
    }
    if counts is not None:
        payload["counts"] = counts
    return payload


def picture_payload(row: PictureRow) -> dict[str, Any]:
    return {
        "picture_id": str(row.id),
        "community_id": str(row.community_id),
        "version": row.version,
        "window_start": row.window_start.isoformat(),
        "window_end": row.window_end.isoformat(),
        "thread_count": row.thread_count,
        "invalid_ref_count": row.invalid_ref_count,
        "model": row.model,
        "effort": row.effort,
        "prompt_name": row.prompt_name,
        "prompt_sha256": row.prompt_sha256,
        "created_at": row.created_at.isoformat(),
        "content": row.content,
    }


@router.get("/communities")
async def list_communities(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    rows = await store.list_communities(organization_id=operator.organization_id)
    communities = []
    for row in rows:
        counts = await store.counts(organization_id=operator.organization_id, community_id=row.id)
        communities.append(community_payload(row, counts))
    return {"communities": communities, "count": len(communities)}


@router.post("/communities", status_code=201)
async def enable_community(
    body: EnableCommunityBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    """Start observing one community: validate the key with its adapter, upsert, audit, and put
    the FIRST poll in the queue right away — the operator should see a read within seconds."""
    # The workspace of the pre-pivot operator screens: the organization's OLDEST, not "the only
    # one". `sole_workspace` answers None as soon as a second workspace exists, and since P1 the
    # user creates those from the studio — so this route began refusing every request the moment
    # that milestone landed. The oldest workspace is where the pilot and its communities already
    # live, so nothing this screen describes moves. See `WorkspaceStore.default_workspace`.
    workspace_id = await WorkspaceStore(await pool_for(request.app)).default_workspace(
        organization_id=operator.organization_id
    )
    if workspace_id is None:
        raise HTTPException(
            status_code=409,
            detail="organizacja nie ma ani jednego workspace'u — nie ma gdzie obserwować",
        )
    # JEDNA usługa dla ekranu i dla agenta (D-046). Kliknięcie „Obserwuj" na karcie w rozmowie
    # wywołuje `communities.observe`, które woła dokładnie te same dwie funkcje co ta trasa.
    # TA TRASA WŁĄCZA OBSERWACJĘ, nie zapisuje konta do społeczności (E3, audyt UX 20.09).
    try:
        joined = await start_observing(
            await pool_for(request.app),
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            platform=body.platform.value,
            community_key=body.community_key,
            actor_ref=f"operator:{operator.operator_id}",
            poll_interval_seconds=body.poll_interval_seconds,
        )
    except MembershipRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert joined.row is not None  # usługa zawsze zwraca wiersz przy dołączeniu
    return {
        "community": community_payload(joined.row),
        "first_poll_job_id": str(joined.first_poll_job_id),
        "first_poll_created": joined.first_poll_created,
    }


class OwnPostsDecision(BaseModel):
    """Trzy stany, nie dwa. `allowed=None` cofa decyzję do „nikt nie zdecydował"."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool | None = None


@router.post("/communities/{community_id}/own-posts")
async def decide_own_posts(
    request: Request,
    community_id: UUID,
    body: OwnPostsDecision,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    """Człowiek rozstrzyga, czy w tej społeczności wolno zakładać nasze własne wątki (0025).

    TO JEST ZGODA, WIĘC ZOSTAWIA ŚLAD: wiersz niesie, kto i kiedy, a dziennik dostaje wpis z
    poprzednim i nowym stanem. Za pół roku ktoś zapyta, skąd się wzięła nasza obecność w tej
    społeczności, i odpowiedzieć ma zapis, a nie czyjaś pamięć.

    Cofnięcie do stanu „nikt nie zdecydował" (`allowed: null`) JEST osobnym stanem, nie odmową:
    mówi „przemyślmy to jeszcze raz", podczas gdy `false` mówi „przemyślane, nie wolno".
    """
    before = await store.get_community(community_id, organization_id=operator.organization_id)
    if before is None:
        raise HTTPException(status_code=404, detail=f"nie ma community {community_id}")
    after = await store.decide_own_posts(
        community_id,
        organization_id=operator.organization_id,
        allowed=body.allowed,
        operator_id=operator.operator_id,
        user_id=operator.user_id,
        now=datetime.now(UTC),
    )
    if after is None:  # zniknęła między odczytem a zapisem; mówimy to samo, co przy braku
        raise HTTPException(status_code=404, detail=f"nie ma community {community_id}")
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="COMMUNITY_OWN_POSTS_DECIDED",
            organization_id=operator.organization_id,
            workspace_id=after.workspace_id,
            subject_ref=f"community:{after.id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={
                "community_key": after.community_key,
                "przed": before.own_posts_allowed,
                "po": after.own_posts_allowed,
            },
        )
    return {"community": community_payload(after)}


@router.get("/communities/{community_id}")
async def read_community(
    community_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    row = await store.get_community(community_id, organization_id=operator.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma community {community_id}")
    counts = await store.counts(organization_id=operator.organization_id, community_id=row.id)
    picture = await store.latest_picture(row.id, organization_id=operator.organization_id)
    return {
        "community": community_payload(row, counts),
        "latest_picture": picture_payload(picture) if picture else None,
    }


@router.get("/communities/{community_id}/picture")
async def read_latest_picture(
    community_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    """The latest picture, or 404. Not an empty picture: "no picture yet" is a fact about the
    schedule (the first one is drawn after the first full day), and the screen says so."""
    row = await store.get_community(community_id, organization_id=operator.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma community {community_id}")
    picture = await store.latest_picture(row.id, organization_id=operator.organization_id)
    if picture is None:
        raise HTTPException(
            status_code=404, detail=f"community {row.ref} nie ma jeszcze żadnego obrazu"
        )
    return picture_payload(picture)


def voice_payload(row: VoiceCardRow) -> dict[str, Any]:
    """Contract with the dashboard (wenext-68, „Jak tu piszą"): the row's provenance flat, the
    card's text and metrics under `content`."""
    return {
        "voice_card_id": str(row.id),
        "community_id": str(row.community_id),
        "version": row.version,
        "window_start": row.window_start.isoformat(),
        "window_end": row.window_end.isoformat(),
        "comment_count": row.comment_count,
        "invalid_quote_count": row.invalid_quote_count,
        "model": row.model,
        "effort": row.effort,
        "prompt_name": row.prompt_name,
        "prompt_sha256": row.prompt_sha256,
        "created_at": row.created_at.isoformat(),
        "content": row.content,
    }


@router.get("/communities/{community_id}/voice")
async def read_latest_voice_card(
    community_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    """The latest voice card, or 404 with the reason: fewer than `VOICE_MIN_COMMENTS` comments in
    the week, or the daily job has not run yet. Not an empty card."""
    row = await store.get_community(community_id, organization_id=operator.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma community {community_id}")
    card = await store.latest_voice_card(row.id, organization_id=operator.organization_id)
    if card is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"community {row.ref} nie ma jeszcze karty głosu (powstaje raz na dobę, gdy w "
                "tygodniu jest co najmniej 20 komentarzy z treścią)"
            ),
        )
    return voice_payload(card)
