"""A person's correction of one waiting proposal: a NEW revision of the same proposal (D-046).

THIS IS THE BODY OF THE DASHBOARD'S `revise` ROUTE, MOVED HERE UNCHANGED SO THAT A SECOND CALLER
CAN SHARE IT, the same way `human_approval.py` is the body of the `approve` route. The route
(`api/project_approvals.py`) and the card in the person's own agent (`tools/card_actions.py`) both
end in the same steps: the old revision is kept as history (never overwritten, because what the
agent really wrote is the only material the agent's work can be judged on), the correction becomes
the NEXT revision of the same proposal with the person as its author, and the approval window is
not extended. Two copies of that would be two definitions of what a correction is.

WHAT THIS NEVER DOES: approve or publish. It writes a revision in READY_FOR_APPROVAL; the person's
consent is a separate act on the new revision (its digest differs, so a confirmation minted for the
old text is worth nothing). The sentences of the refusals are the route's, byte for byte.

WHAT IS NOT HERE: the checks of a text typed in a card (length, the long-dash rule, X's weighted
length). The dashboard's route never had them and its tests pin that; the card's tool adds them in
front of this function (`tools/card_actions.py`), where they are about what a person typed there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from community_os_contracts.transitions import IllegalTransition

from control_plane.approval.store import ApprovalStore, ApprovalStoreError
from control_plane.pilot.publication import join_media, split_media
from control_plane.presence.human_approval import HumanApprovalRefused, payload_of

REFUSED_UNCHANGED = "BEZ_ZMIAN"
REFUSED_OUTDATED = "NIEAKTUALNY"


@dataclass(frozen=True)
class HumanRevision:
    """What the correction produced: the new revision and the text as stored (image line too)."""

    revision_id: UUID
    text: str
    #: The payload of the revision that was corrected (the new one differs in `text_and_media`).
    previous_payload: dict[str, Any]


def hash_input_with_text(payload: dict[str, Any], text: str) -> ApprovalHashInput:
    """Ten sam odcisk zgody, z jedną zmienioną rzeczą: treścią.

    Pola przepisane z payloadu poprzedniej rewizji, bo zgoda ma dotyczyć TEJ SAMEJ akcji pod tym
    samym celem. Gdyby cokolwiek poza tekstem powstawało tu na nowo, poprawka byłaby inną akcją
    udającą tę samą.
    """
    return ApprovalHashInput(
        organization=UUID(str(payload["organization"])),
        workspace=UUID(str(payload["workspace"])),
        assignment_version=UUID(str(payload["assignment_version"])),
        account=UUID(str(payload["account"])),
        platform=str(payload["platform"]),
        action_type=SemanticCapability(str(payload["action_type"])),
        target_external_id=str(payload["target_external_id"]),
        target_url=str(payload["target_url"]),
        source_fingerprint=str(payload["source_fingerprint"]),
        text_and_media=text,
        intelligence_profile_version=UUID(str(payload["intelligence_profile_version"])),
        plan_version=UUID(str(payload["plan_version"])),
        memory_snapshot=UUID(str(payload["memory_snapshot"])),
        adapter_version=str(payload["adapter_version"]),
    )


def corrected_text(payload: dict[str, Any], corrected: str) -> str:
    """The field the consent will cover for `corrected` (what the person typed), image line kept.

    The card shows the text without the image line (27.09), so a correction arrives without it and
    the image the agent attached stays attached. Another image is another draft.
    """
    consented = str(payload.get("text_and_media") or "")
    _, media = split_media(consented)
    fixed = corrected.strip()
    return (
        join_media(fixed, media) if media is not None and split_media(fixed)[1] is None else fixed
    )


async def revise_into_new_revision(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    row: Any,
    text: str,
    actor_ref: str,
) -> HumanRevision:
    """Record `text` as the next revision of `row`'s proposal, or raise `HumanApprovalRefused`.

    `text` is what the person typed (it may lack the image line; see `corrected_text`). `row` is a
    revision of ONE project as `human_approval.revision_row` reads it: the caller has already shown
    it belongs there.
    """
    payload = payload_of(row)
    revision_id: UUID = row["revision_id"]
    consented = str(payload.get("text_and_media") or "")
    stored = corrected_text(payload, text)
    if stored == consented.strip():
        raise HumanApprovalRefused(409, "poprawka nie zmienia treści", code=REFUSED_UNCHANGED)

    approvals = ApprovalStore(pool)
    new_input = hash_input_with_text(payload, stored)
    state = str(row["state"])
    try:
        if state == ActionProposalState.APPROVED.value:
            # PO ZGODZIE edycja ma swoją własną, opisaną ścieżkę: poprzednia rewizja idzie w
            # SUPERSEDED, a zgoda, która jej dotyczyła, przestaje cokolwiek autoryzować.
            _, successor = await approvals.edit_into_new_revision(
                revision_id=revision_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                new_hash_input=new_input,
                actor_ref=actor_ref,
            )
            ready = await approvals.move_revision(
                successor.id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                target=ActionProposalState.READY_FOR_APPROVAL,
                actor_ref=actor_ref,
            )
        else:
            # PRZED ZGODĄ nie ma strzałki do SUPERSEDED (L2775: jedyne wejście do tego stanu to
            # APPROVED). Poprawka jest więc tym, czym naprawdę jest: człowiek odrzuca wersję
            # agenta i pisze własną jako KOLEJNĄ rewizję tej samej propozycji. Historia zostaje
            # w komplecie, bo to ona jest jedynym materiałem do oceny pracy agenta.
            await approvals.move_revision(
                revision_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                target=ActionProposalState.REJECTED,
                actor_ref=actor_ref,
            )
            ready = await approvals.record_revision(
                organization_id=organization_id,
                workspace_id=workspace_id,
                proposal_id=row["proposal_id"],
                revision_no=int(row["revision"]) + 1,
                hash_input=new_input,
                state=ActionProposalState.READY_FOR_APPROVAL,
                # OKNO ZGODY SIĘ NIE PRZEDŁUŻA (L5539): poprawka jest poprawką tej samej
                # propozycji, a nie nową sprawą z nowym terminem.
                expires_at=row["expires_at"],
            )
    except ApprovalStoreError as exc:
        raise HumanApprovalRefused(
            409, f"poprawka nie przeszła: {exc}", code=REFUSED_OUTDATED
        ) from exc
    except IllegalTransition as exc:
        # POWTÓRZONA POPRAWKA, czyli drugie kliknięcie na karcie, której ekran jeszcze nie
        # odświeżył. Rewizja stoi już w REJECTED albo SUPERSEDED, a maszyna stanów nie ma z nich
        # strzałki; `IllegalTransition` nie dziedziczy po `ApprovalStoreError`, więc lądowało
        # wyżej jako 500 (zgłoszenie wenext-ce, odtworzenie: e2e
        # `clicking_the_stale_correction_again_makes_no_second_proposal`).
        raise HumanApprovalRefused(
            409,
            "ta wersja szkicu nie jest już aktualna: ktoś ją w międzyczasie poprawił albo "
            "zatwierdził. Odśwież rozmowę i popraw najnowszą wersję.",
            code=REFUSED_OUTDATED,
        ) from exc
    return HumanRevision(revision_id=ready.id, text=stored, previous_payload=payload)
