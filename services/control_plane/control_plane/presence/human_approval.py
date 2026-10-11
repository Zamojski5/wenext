"""A person's yes to one waiting proposal, and the queue entry it issues (D-046, D-055).

THIS IS THE BODY OF THE DASHBOARD'S `approve` ROUTE, MOVED HERE UNCHANGED SO THAT A SECOND CALLER
CAN SHARE IT. The route (`api/project_approvals.py`) and the card in the person's own agent
(`tools/card_send.py`) both end in the same eight steps: the proposal waits and has not expired,
the project has a live signed scope, the pace that signature binds is not spent, ONE transaction
under the project's lock writes the consent and the queue entry, and the publisher is nudged. Two
copies of that would be two definitions of what may be published, and the second would be the one
somebody forgets to tighten. The sentences of the refusals are the route's, byte for byte: the
dashboard tells them apart by substring (`apps/dashboard/lib/room-actions.ts`).

WHAT IS NOT HERE: who the person is. A route knows its operator; the card's process has none (the
plugin has no cookie and must not get one), so the caller passes a `HumanSigner`, and `None` means
"the signer of the live signature", which is how `DelegatedApprover` files a consent too.

WHAT THIS NEVER DOES: publish. It writes a consent and a queue entry; the publisher does the
identity check, the thread read and the read-back, and says "opublikowano" only after the last.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from community_os_contracts.execution import ActionProposalState
from community_os_contracts.transitions import IllegalTransition

from control_plane.approval.store import ApprovalStore, ApprovalStoreError
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.pilot.publication import (
    KIND_OWN_POST,
    KIND_REPLY,
    PublicationStore,
    draft_sha256,
)
from control_plane.pilot.publisher import nudge_issued_revision
from control_plane.presence import budget as budget_module
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST, APPROVED_DECISION
from control_plane.presence.store import PresenceStore

#: What went wrong, as a code a caller can branch on. The route ignores it (it has the sentence
#: and the status); the card turns it into a state of the button.
REFUSED_STATE = "STAN"
REFUSED_WINDOW = "OKNO"
REFUSED_HASH = "ODCISK"
REFUSED_SCOPE = "ZAKRES"
REFUSED_BUDGET = "BUDZET"
REFUSED_CONSENT = "ZGODA"
REFUSED_STALE = "NIEAKTUALNY"


class HumanApprovalRefused(Exception):
    """The approval did not happen, and nothing was written. `detail` is the route's sentence."""

    def __init__(self, status: int, detail: str, *, code: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class HumanSigner:
    """Who the consent is filed under. At least one of the three, as `resolve_signer` requires."""

    operator_id: UUID | None = None
    user_id: UUID | None = None
    membership_id: UUID | None = None


@dataclass(frozen=True)
class HumanApproval:
    """What the approval produced: the approved revision and the request for the publisher."""

    revision_id: UUID
    request_id: UUID
    decision_id: UUID


#: The columns every caller needs from a proposal revision of ONE project. `payload_hash` is the
#: digest the consent will point at (`record_decision` reads it off the revision, never from an
#: argument), and `account_handle` is the login the draft leaves from.
_REVISION_SQL = """
    SELECT r.id AS revision_id, r.state, r.expires_at, r.payload, r.revision, r.payload_hash,
           r.proposal_id, p.social_account_id, a.platform,
           a.external_account_id AS account_handle
      FROM action_proposal_revisions r
      JOIN action_proposals p
        ON p.id = r.proposal_id AND p.organization_id = r.organization_id
      JOIN social_accounts a ON a.id = p.social_account_id
      JOIN project_account_assignments x
        ON x.organization_id = p.organization_id
       AND x.social_account_id = p.social_account_id
       AND x.unassigned_at IS NULL AND x.project_id = $4
     WHERE r.organization_id = $1 AND r.workspace_id = $2 AND r.id = $3
       AND r.payload ->> 'plan_version' = $4::text
"""


async def revision_row(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    revision_id: UUID,
) -> asyncpg.Record | None:
    """The revision, if it BELONGS to this project and its account is still assigned to it.

    A foreign one is `None`, the same answer as a missing one: nobody learns what exists next door.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            _REVISION_SQL, organization_id, workspace_id, revision_id, project_id
        )


def payload_of(row: Any) -> dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


def ensure_waiting(row: Any, expected_payload_hash: str | None = None) -> None:
    """Raise `HumanApprovalRefused` unless the proposal waits for a consent, in time, as shown.

    The first three checks of the approval, on their own, so the card can ask them BEFORE it asks
    the person to confirm, and the approval asks them again at the moment it matters.
    `expected_payload_hash` is what the person was SHOWN: a revision whose digest is anything else
    is refused, because what goes out must be what was on the screen. The route has none (it takes
    the revision as the page showed it by id).
    """
    if str(row["state"]) != ActionProposalState.READY_FOR_APPROVAL.value:
        raise HumanApprovalRefused(
            409,
            f"propozycja jest w stanie {row['state']}, a do zatwierdzenia nadaje się tylko "
            "ta, która czeka na zgodę",
            code=REFUSED_STATE,
        )
    expires_at = row["expires_at"]
    if expires_at is not None and expires_at <= datetime.now(UTC):
        raise HumanApprovalRefused(
            409,
            "okno zgody tej propozycji minęło; poproś agenta o nową wersję",
            code=REFUSED_WINDOW,
        )
    if expected_payload_hash is not None and str(row["payload_hash"]) != expected_payload_hash:
        raise HumanApprovalRefused(
            409,
            "ta wersja szkicu nie jest już tą, którą widziałeś: ktoś ją w międzyczasie zmienił. "
            "Odśwież Pokój i zdecyduj o najnowszej.",
            code=REFUSED_HASH,
        )


async def approve_and_queue(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    row: Any,
    signer: HumanSigner | None,
    reason: str,
    expected_payload_hash: str | None = None,
) -> HumanApproval:
    """Record the person's consent for `row` and queue it for the publisher, or refuse."""
    revision_id = row["revision_id"]
    payload = payload_of(row)
    ensure_waiting(row, expected_payload_hash)

    delegation = await PresenceStore(pool).delegation_for(
        organization_id=organization_id, project_id=project_id
    )
    if delegation is None or not delegation.live:
        # KOLEJKA PROJEKTOWA WYMAGA POWIERZENIA (0017: wpis projektowy bez delegacji jest
        # odrzucany przez CHECK). To nie jest ograniczenie tej trasy, tylko kształt zgody w tym
        # produkcie: publikacja w projekcie dzieje się w zakresie, który ktoś podpisał.
        raise HumanApprovalRefused(
            409,
            "projekt nie ma żywego powierzenia, więc nie ma zakresu, w którym ta publikacja "
            "miałaby się odbyć. Podpisz powierzenie i zatwierdź ponownie",
            code=REFUSED_SCOPE,
        )
    if signer is None:
        signer = HumanSigner(
            operator_id=delegation.signed_by, membership_id=delegation.signed_membership_id
        )

    approvals = ApprovalStore(pool)
    publication = PublicationStore(pool)
    own_post = ACTION_OF_CAPABILITY.get(str(payload.get("action_type"))) == ACTION_OWN_POST

    async with pool.acquire() as gate, gate.transaction():
        key = f"presence-budget:{project_id}"
        await gate.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", key)
        spent = await budget_module.spent(
            gate,
            organization_id=organization_id,
            project_id=project_id,
            accounts=delegation.scope.accounts,
            limits=budget_module.effective_limits(delegation),
            now=datetime.now(UTC),
        )
        blocked = spent.own_post_blocked() if own_post else spent.publication_blocked()
        if blocked is not None:
            raise HumanApprovalRefused(409, f"budżet wyczerpany, {blocked}", code=REFUSED_BUDGET)
        try:
            decision, approved = await approvals.record_decision(
                revision_id=revision_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                decision=APPROVED_DECISION,
                operator_id=signer.operator_id,
                user_id=signer.user_id,
                membership_id=signer.membership_id,
                reason=reason,
                connection=gate,
            )
        except ApprovalStoreError as exc:
            raise HumanApprovalRefused(
                409, f"zgoda nie przeszła: {exc}", code=REFUSED_CONSENT
            ) from exc
        except IllegalTransition as exc:
            # DRUGIE KLIKNIĘCIE TO NIE AWARIA. Maszyna stanów odmawia strzałki, której nie
            # ma (na przykład z rewizji już zatwierdzonej albo zastąpionej), a ten wyjątek
            # nie jest błędem tego modułu, więc leciał wyżej jako 500 z pełnym śladem
            # wyjątku. Dla człowieka to zwykłe „kliknąłem dwa razy" i taką ma dostać
            # odpowiedź (zgłoszenie wenext-ce ze zrzutów, 20.09).
            raise HumanApprovalRefused(
                409,
                "ta wersja szkicu nie czeka już na zgodę: ktoś ją w międzyczasie "
                "zatwierdził albo poprawił. Odśwież rozmowę i zdecyduj o najnowszej.",
                code=REFUSED_STALE,
            ) from exc
        position = await publication.next_position(organization_id=organization_id, conn=gate)
        request_row = await publication.create_project_request(
            kind=KIND_OWN_POST if own_post else KIND_REPLY,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            delegation_id=delegation.id,
            proposal_revision_id=approved.id,
            approval_decision_id=decision.id,
            position=position,
            consent_source=reason,
            draft_sha256=draft_sha256(str(payload.get("text_and_media") or "")),
            target_external_id=str(payload.get("target_external_id")),
            target_url=str(payload.get("target_url")),
            conn=gate,
        )

    # D-030 FOR THE PERSON'S OWN CLICK, the same nudge `drafts.publish` gives a delegated request:
    # the draft is minutes old and the person is looking at the screen, so one publisher tick now
    # rather than the chain's next ten-minute slot. After the transaction, never inside it: a
    # nudge that failed must not roll back a consent that was given.
    await nudge_issued_revision(
        PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
        workspace_id=workspace_id,
        revision_id=approved.id,
    )
    return HumanApproval(
        revision_id=approved.id, request_id=request_row.id, decision_id=decision.id
    )
