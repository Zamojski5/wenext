"""`/api/judgments` — what the agent decided, and what the operator says about it.

The read lists decisions newest first with the thread they are about and the operator's latest
verdict; the write appends ONE feedback row and its audit entry in one transaction. The
judgment itself is never edited (0004 makes it append-only): a changed mind is a new feedback
row, and `docs/KIERUNEK-PRODUKTU.md` §8 is the reason the history is kept.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from community_os_contracts import JudgmentDecision, JudgmentFeedbackVerdict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.observe.store import JudgmentRow, ObservationStore

router = APIRouter(prefix="/api", tags=["judgments"])

JUDGMENTS_PATH = "/api/judgments"
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: JudgmentFeedbackVerdict
    note: str | None = Field(default=None, max_length=2000)


async def observation_store(request: Request) -> ObservationStore:
    return ObservationStore(await pool_for(request.app))


def judgment_payload(row: JudgmentRow) -> dict[str, Any]:
    return {
        "judgment_id": str(row.id),
        "thread_id": str(row.thread_id),
        "thread_ref": row.thread_ref,
        "thread_title": row.thread_title,
        "thread_url": row.thread_url,
        "community_display_name": row.community_display_name,
        "decision": row.decision.value,
        "reasoning": row.reasoning,
        "what_user_could_add": row.what_user_could_add,
        "confidence": row.confidence,
        "user_profile_version": row.user_profile_version,
        "model": row.model,
        "effort": row.effort,
        "prompt_name": row.prompt_name,
        "prompt_sha256": row.prompt_sha256,
        "created_at": row.created_at.isoformat(),
        "latest_feedback": row.latest_feedback.value if row.latest_feedback else None,
        "latest_feedback_note": row.latest_feedback_note,
        "latest_feedback_at": row.latest_feedback_at.isoformat()
        if row.latest_feedback_at
        else None,
    }


@router.get("/judgments")
async def list_judgments(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
    decision: JudgmentDecision | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise HTTPException(
            status_code=400, detail=f"limit={limit} poza zakresem 1..{MAX_PAGE_LIMIT}"
        )
    rows = await store.list_judgments(
        organization_id=operator.organization_id, decision=decision, limit=limit
    )
    summary = await store.agreement_summary(organization_id=operator.organization_id)
    return {
        "judgments": [judgment_payload(r) for r in rows],
        "count": len(rows),
        "limit": limit,
        "truncated": len(rows) == limit,
        "decision_filter": decision.value if decision else None,
        "summary": summary,
    }


@router.post("/judgments/{judgment_id}/feedback", status_code=201)
async def record_feedback(
    judgment_id: UUID,
    body: FeedbackBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ObservationStore, Depends(observation_store)],
) -> dict[str, Any]:
    judgment = await store.get_judgment(judgment_id, organization_id=operator.organization_id)
    if judgment is None:
        raise HTTPException(status_code=404, detail=f"nie ma osądu {judgment_id}")
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        feedback_id = await store.record_feedback(
            organization_id=operator.organization_id,
            workspace_id=judgment.workspace_id,
            judgment_id=judgment.id,
            verdict=body.verdict,
            note=body.note,
            connection=connection,
        )
        await AuditWriter(connection).record(
            kind="JUDGMENT_FEEDBACK_RECORDED",
            organization_id=operator.organization_id,
            workspace_id=judgment.workspace_id,
            subject_ref=f"judgment:{judgment.id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"judgment_id": str(judgment.id), "verdict": body.verdict.value},
        )
    return {
        "feedback_id": str(feedback_id),
        "judgment_id": str(judgment.id),
        "verdict": body.verdict.value,
        "decision": judgment.decision.value,
    }
