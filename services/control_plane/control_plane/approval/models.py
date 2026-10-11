"""Values that cross the boundary out of `approval.store`. Frozen, poor, no rows, no drivers.

One type only. `contracts.execution` already owns `ActionProposalRevision`, `ApprovalDecision`,
`ExecutionIntent` and `SideEffectAuthorization`, and a second spelling of any of them here would
be a second source of the same truth. What contracts do NOT own is the shape the approval QUEUE
is read in: a row of the operator's list, which carries the revision's identity, its state and
its two deadlines and deliberately nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from community_os_contracts.execution import ActionProposalState


@dataclass(frozen=True)
class ApprovalQueueEntry:
    """One revision as the approval queue shows it (L4595, DoD L5218).

    `payload_hash` is the FULL 64-character hex digest, never a prefix. The reason is not
    cosmetic: "approval wskazuje dokładny payload hash" (L5218) is the DoD line this screen is
    evidence for, and a truncated hash is a screen on which two different payloads look
    identical. `tests/test_approvals_api.py::test_the_list_shows_the_full_sixty_four_character_
    payload_hash` fails on any shortening.

    There is no `priority_tier` field, and that absence is deliberate rather than forgotten.
    L4595 sorts the queue "rosnąco po `expires_at` w obrębie `priority_tier`", but
    `priority_tier` is a field of `ThreadQualityAssessment` (L4464) produced by the scoring
    engine of E1.F3 — in F1 there is no column for it and no source that could fill it. Sorting
    by a tier every row shares is the same order as sorting by `expires_at` alone, so F1
    implements the part it can honestly implement and this comment records the part it cannot.
    """

    revision_id: UUID
    proposal_id: UUID
    organization_id: UUID
    workspace_id: UUID
    revision_no: int
    state: ActionProposalState
    payload_hash: str
    created_at: datetime
    expires_at: datetime
    source_observed_at: datetime
