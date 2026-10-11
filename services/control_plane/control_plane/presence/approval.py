"""Issuing a consent from a delegation instead of from a person at a screen (P4).

THE WHOLE PRODUCT CLAIM OF P4 RESTS HERE. "I delegate a goal and close the laptop" means that
something has to say yes to a text nobody read. The only honest way to do that is to make the
yes rest on a signature that WAS read: the delegation, its scope and its budget. This module is
the one place where that signature turns into an `approval_decisions` row, and everything it
refuses stays a proposal waiting for a person.

WHAT IS CHECKED, IN THIS ORDER, AND WHY THE ORDER MATTERS:

  1. **Is there a live delegation, and is it the one that governs now.** Read fresh on every
     consideration, never cached: revoking is the operator's stop button and it has to work for
     proposals that are ALREADY pending, not only for ones made afterwards. A paused delegation
     is not live either, and neither is silently treated as "no delegation" (the sentences
     differ, because what a person has to do to undo them differs).
  2. **The mode.** RESEARCH publishes nothing at all, REVIEW publishes nothing WITHOUT a person,
     AUTONOMOUS may. The mode is checked before the scope because it is the coarser statement
     and a person who sets RESEARCH means it whatever the scope still lists.
  3. **The scope**: this action, this account, this community.
  4. **The budget**, inside a transaction that also writes the queue entry.

The budget, decision and queue entry now commit in one transaction under the project's
advisory lock. The signer's active access is locked while recording consent; the publisher
reads the live delegation again before executing. A failed queue insert leaves no stranded
approval, and a revocation is not assumed to be stable across the run.

WHAT THIS MODULE NEVER DOES: publish. It writes a consent and a queue entry; `pilot/publisher.py`
does the identity check, the thread read, the intent and the read-back, exactly as it does for
the pilot. A delegation buys the agent a decision, not a shortcut past the execution kernel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState

from control_plane.approval.store import APPROVED_DECISION, ApprovalStore, ApprovalStoreError
from control_plane.pilot.publication import (
    FULLNAME_PREFIX,
    KIND_OWN_POST,
    KIND_REPLY,
    PublicationStore,
    draft_sha256,
)
from control_plane.workspace.platforms import support_for

from . import budget as budget_module
from .own_posts import may_post_in
from .scope import (
    ACTION_OWN_POST,
    ACTION_REPLY,
    MODE_AUTONOMOUS,
    MODE_RESEARCH,
    MODE_REVIEW,
    covers,
)
from .store import PresenceStore

_LOG = logging.getLogger(__name__)

#: What a proposal's capability means in the vocabulary a person signed. The scope talks about
#: REPLY and OWN_POST because those are the words on the delegation screen; the proposal talks
#: about SUBMIT_COMMENT and SUBMIT_POST because those are the words the execution kernel uses.
#: A capability absent from this map is one nobody delegated, and it is refused rather than
#: guessed: the silent alternative is a delegation for comments authorising something else.
ACTION_OF_CAPABILITY = {
    SemanticCapability.SUBMIT_COMMENT.value: ACTION_REPLY,
    SemanticCapability.PUBLISH_REPLY.value: ACTION_REPLY,
    SemanticCapability.SUBMIT_POST.value: ACTION_OWN_POST,
    SemanticCapability.PUBLISH_CONTENT.value: ACTION_OWN_POST,
}

#: What became of one pending proposal. Three outcomes, not two: "a person has to look at this"
#: is not a refusal, and showing it as one would teach the operator to ignore refusals.

ISSUED = "WYDANA"
REFUSED = "ODMOWA"
FOR_A_HUMAN = "DO_CZLOWIEKA"


@dataclass(frozen=True)
class Considered:
    """One proposal, and what the delegation did with it."""

    revision_id: UUID
    outcome: str
    reason: str
    request_id: UUID | None = None

    def to_json(self) -> dict[str, str | None]:
        return {
            "rewizja": str(self.revision_id),
            "wynik": self.outcome,
            "powod": self.reason,
            "wniosek": str(self.request_id) if self.request_id else None,
        }


class DelegatedApprover:
    """Turns proposals a presence session left behind into queue entries, or into reasons."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        presence: PresenceStore,
        approvals: ApprovalStore,
        publication: PublicationStore,
        organization_id: UUID,
    ) -> None:
        self._pool = pool
        self._presence = presence
        self._approvals = approvals
        self._publication = publication
        self._organization_id = organization_id

    async def consider_pending(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        now: datetime | None = None,
        revision_id: UUID | None = None,
    ) -> tuple[Considered, ...]:
        """Every proposal of this project that is waiting, oldest first.

        Oldest first so that a project whose budget runs out mid-list spends it on what the agent
        decided first, not on whichever row the planner happened to return first.
        """
        moment = now or datetime.now(UTC)
        pending = await self._pending(workspace_id=workspace_id, project_id=project_id)
        if revision_id is not None:
            pending = [row for row in pending if row["revision_id"] == revision_id]
        if not pending:
            return ()

        delegation = await self._presence.delegation_for(
            organization_id=self._organization_id, project_id=project_id
        )
        if delegation is None:
            return tuple(
                Considered(row["revision_id"], FOR_A_HUMAN, "projekt nie ma delegacji")
                for row in pending
            )
        if not delegation.live:
            return tuple(
                Considered(row["revision_id"], FOR_A_HUMAN, f"delegacja jest {delegation.state}")
                for row in pending
            )
        if delegation.mode == MODE_RESEARCH:
            return tuple(
                Considered(
                    row["revision_id"],
                    REFUSED,
                    "tryb RESEARCH: projekt czyta i zapamiętuje, nie publikuje",
                )
                for row in pending
            )
        if delegation.mode == MODE_REVIEW:
            return tuple(
                Considered(
                    row["revision_id"],
                    FOR_A_HUMAN,
                    "tryb REVIEW: każdy tekst czeka na zgodę człowieka",
                )
                for row in pending
            )
        if delegation.mode != MODE_AUTONOMOUS:  # a mode added to the database and not to the code
            return tuple(
                Considered(row["revision_id"], FOR_A_HUMAN, f"tryb {delegation.mode} nieobsłużony")
                for row in pending
            )

        considered: list[Considered] = []
        for row in pending:
            considered.append(
                await self._consider_one(
                    row, delegation=delegation, workspace_id=workspace_id, moment=moment
                )
            )
        return tuple(considered)

    async def _consider_one(
        self, row: asyncpg.Record, *, delegation, workspace_id: UUID, moment: datetime
    ) -> Considered:
        revision_id = row["revision_id"]
        action = ACTION_OF_CAPABILITY.get(str(row["action_type"]))
        if action is None:
            return Considered(
                revision_id,
                REFUSED,
                f"działanie {row['action_type']} nie ma odpowiednika w zakresie delegacji",
            )
        # SKĄD SIĘ BIERZE SPOŁECZNOŚĆ, zależy od rodzaju, i to nie jest szczegół: odpowiedź
        # wskazuje cudzy wątek, więc społeczność trzeba ODNALEŹĆ po tym wątku; własny post
        # wskazuje społeczność wprost, bo wątku jeszcze nie ma i nie ma czego szukać. Szukanie
        # wątku o identyfikatorze „r/kubernetes" nie znajduje nic i kończyło się odmową
        # „nie wiadomo, w jakiej społeczności jest r/kubernetes" dla KAŻDEGO własnego postu.
        support = support_for(str(row["platform"]))
        # PLATFORMA BEZ SPOŁECZNOŚCI (X, 26.09). Tam miejscem publikacji jest konto i jego oś
        # czasu, więc nie ma czego szukać ani z czym porównywać zakresu; `covers` pomija
        # społeczność, gdy jej nie ma, a zakres wiążą konto, działanie, limity i termin.
        community_free = support is not None and not support.scoped_by_community
        if community_free:
            community_key = None
        elif action == ACTION_OWN_POST:
            community_key = await self._known_community(
                str(row["target_external_id"]),
                workspace_id=workspace_id,
                platform=str(row["platform"]),
            )
        else:
            community_key = await self._community_of(row["target_external_id"])
        # P6: what the PLATFORM can do, next to what the person signed. A delegation may name an
        # X account for REPLY in good faith; no adapter here can carry that out, and issuing a
        # consent for it would put a request in the queue that the publisher can only ever skip.
        verdict = covers(
            mode=delegation.mode,
            scope=delegation.scope,
            action=action,
            account_id=row["social_account_id"],
            community_key=community_key,
            support=support,
        )
        if not verdict.covered:
            return Considered(revision_id, REFUSED, verdict.reason)
        if community_key is None and not community_free:
            # The scope names communities, and a target we cannot place in one cannot be checked
            # against it. Refusing is the only reading that does not widen the signature.
            return Considered(
                revision_id,
                REFUSED,
                (
                    f"społeczność {row['target_external_id']} nie jest obserwowana"
                    if action == ACTION_OWN_POST
                    else f"nie wiadomo, w jakiej społeczności jest {row['target_external_id']}"
                ),
            )
        if action == ACTION_OWN_POST and not community_free:
            # BRAMKA SPOŁECZNOŚCI PYTANA PONOWNIE, przy decyzji. Karta głosu mogła się zmienić
            # między napisaniem szkicu a jego wydaniem, a to decyzja jest tym momentem, w którym
            # zgoda naprawdę powstaje.
            async with self._pool.acquire() as conn:
                verdict = await may_post_in(
                    conn,
                    organization_id=self._organization_id,
                    workspace_id=workspace_id,
                    platform=str(row["platform"]),
                    community_key=community_key,
                )
            if not verdict.allowed:
                return Considered(revision_id, REFUSED, verdict.reason)

        return await self._issue(
            row, delegation=delegation, workspace_id=workspace_id, moment=moment
        )

    async def _issue(
        self, row: asyncpg.Record, *, delegation, workspace_id: UUID, moment: datetime
    ) -> Considered:
        """The budget, the consent and the queue entry, serialised per project.

        The transaction lock covers the budget count, consent and queue insertion. Both writes
        use the same connection so rollback leaves the revision available for a later attempt.
        """
        revision_id = row["revision_id"]
        async with self._pool.acquire() as gate, gate.transaction():
            key = f"presence-budget:{delegation.project_id}"
            await gate.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", key)
            spent = await budget_module.spent(
                gate,
                organization_id=self._organization_id,
                project_id=delegation.project_id,
                accounts=delegation.scope.accounts,
                limits=budget_module.effective_limits(delegation),
                now=moment,
            )
            # WŁASNY POST PYTA O SWÓJ LIMIT. Odpowiedź nie ma czekać na limit, który jej
            # nie dotyczy, a post ma zostać zatrzymany także wtedy, gdy ogólny limit
            # publikacji jest jeszcze daleki.
            own_post = ACTION_OF_CAPABILITY.get(str(row["action_type"])) == ACTION_OWN_POST
            blocked = spent.own_post_blocked() if own_post else spent.publication_blocked()
            if blocked is not None:
                return Considered(revision_id, REFUSED, f"budżet wyczerpany, {blocked}")
            reason = (
                f"delegacja {delegation.id} zakres {delegation.scope_sha256} tryb {delegation.mode}"
            )
            try:
                decision, approved = await self._approvals.record_decision(
                    revision_id=revision_id,
                    organization_id=self._organization_id,
                    workspace_id=workspace_id,
                    decision=APPROVED_DECISION,
                    operator_id=delegation.signed_by,
                    membership_id=delegation.signed_membership_id,
                    reason=reason,
                    connection=gate,
                )
            except ApprovalStoreError as exc:
                return Considered(revision_id, REFUSED, f"zgoda nie przeszła: {exc}"[:300])
            position = await self._publication.next_position(
                organization_id=self._organization_id, conn=gate
            )
            request = await self._publication.create_project_request(
                kind=KIND_OWN_POST if own_post else KIND_REPLY,
                organization_id=self._organization_id,
                workspace_id=workspace_id,
                project_id=delegation.project_id,
                delegation_id=delegation.id,
                proposal_revision_id=approved.id,
                approval_decision_id=decision.id,
                position=position,
                consent_source=reason,
                draft_sha256=draft_sha256(str(row["text_and_media"] or "")),
                target_external_id=str(row["target_external_id"]),
                target_url=str(row["target_url"]),
                conn=gate,
            )
        return Considered(revision_id, ISSUED, reason, request_id=request.id)

    async def _pending(self, *, workspace_id: UUID, project_id: UUID) -> list[asyncpg.Record]:
        """Proposals of this project's accounts that are waiting for a consent and still valid.

        The project is reached through `project_account_assignments`, because a proposal knows its
        ACCOUNT and an account is what a project is given. An expired revision is left out here
        rather than refused below: its consent window closed on its own, and a delegation must not
        be able to reopen it.
        """
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT r.id AS revision_id, p.social_account_id, a.platform,
                           r.payload ->> 'action_type' AS action_type,
                           r.payload ->> 'target_external_id' AS target_external_id,
                           r.payload ->> 'target_url' AS target_url,
                           r.payload ->> 'text_and_media' AS text_and_media
                      FROM action_proposal_revisions r
                      JOIN action_proposals p
                        ON p.id = r.proposal_id AND p.organization_id = r.organization_id
                      JOIN social_accounts a ON a.id = p.social_account_id
                      JOIN project_account_assignments x
                        ON x.organization_id = p.organization_id
                       AND x.social_account_id = p.social_account_id
                       AND x.unassigned_at IS NULL
                     WHERE r.organization_id = $1 AND r.workspace_id = $2
                       AND x.project_id = $3
                       AND r.payload ->> 'plan_version' = $3::text
                       AND r.state = $4
                       AND r.expires_at > now()
                       AND NOT EXISTS (
                           SELECT 1 FROM publication_requests q
                            WHERE q.proposal_revision_id = r.id)
                     ORDER BY r.created_at
                    """,
                    self._organization_id,
                    workspace_id,
                    project_id,
                    ActionProposalState.READY_FOR_APPROVAL.value,
                )
            )

    async def _known_community(
        self, community_key: str, *, workspace_id: UUID, platform: str
    ) -> str | None:
        """Ta sama nazwa z powrotem, jeśli taką społeczność obserwujemy; inaczej `None`.

        Wygląda na nic, a jest bramką: nazwa przyszła z pola, które wypełnił model, i bez tego
        sprawdzenia zakres delegacji porównywałby się z napisem, który z niczym w tej bazie nie
        koresponduje. Czwórka zakresu w całości, tak samo jak w `own_posts.may_post_in`.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT community_key FROM communities WHERE organization_id = $1 "
                "AND workspace_id = $2 AND platform = $3 AND community_key = $4",
                self._organization_id,
                workspace_id,
                platform,
                community_key,
            )

    async def _community_of(self, target_external_id: str) -> str | None:
        """The community a target is in, by the same two lookups `PublicationStore.target_for`
        uses and with the prefix stripped in the same one place."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"""
                SELECT c.community_key
                  FROM observed_threads t
                  JOIN communities c ON c.id = t.community_id
                 WHERE t.organization_id = $1
                   AND t.id = coalesce(
                       (SELECT oc.thread_id FROM observed_comments oc
                         WHERE oc.organization_id = $1
                           AND oc.external_id = regexp_replace($2, '{FULLNAME_PREFIX}', '')
                         LIMIT 1),
                       (SELECT o.id FROM observed_threads o
                         WHERE o.organization_id = $1
                           AND o.external_id = regexp_replace($2, '{FULLNAME_PREFIX}', '')
                         ORDER BY o.created_at_source DESC LIMIT 1))
                """,
                self._organization_id,
                target_external_id,
            )
