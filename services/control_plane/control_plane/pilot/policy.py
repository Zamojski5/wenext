"""Tryb 7 dni (D-029): the founder signs a publication policy ONCE; the system judges each draft
and approves it through the existing approval path; the publish chain posts it.

What this module does NOT change: every publication still has a revision, an operator decision, a
one-time `ExecutionIntent`, an attempt and read-back evidence (D-027, D-028). The policy replaces
the founder's click per comment, not the trail. The decision's `reason` names the policy and the
judgment, so a row in `approval_decisions` written by this module can always be traced to the
founder's words and to the judge's verdict.

Three gates, in order, before a draft is approved:

1. **Rules of the policy** (no model call): kind allowed, confidence at or above the threshold,
   context read complete, no URL, no product or company name, no mention of tools or automation,
   no long dashes, length within the limit. A failure is a `POLICY_REJECT` judgment with the rule.
2. **The judge** (one model call, prompt `draft_judge`, pinned by hash in the policy): five
   questions from the founder's review sheet answered true/false, verdict PASS or REJECT with a
   Polish rationale for the morning report. REJECT leaves the draft `HUMAN_REVIEW_PENDING`.
3. **The approval path**: `approve_reaction` (the same function `pilot-approve` uses for a human
   consent document) creates the revision, the decision and the publication request.

Stop conditions, checked before anything is approved: policy outside its window or not ACTIVE;
the last `NEGATIVE_STREAK_TO_STOP` published comments all below zero one hour after publication;
a BLOCKED write (foreign session, refused actuator) stops the policy through the publisher.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.approval.store import ApprovalStore
from control_plane.observe import prompts
from control_plane.observe.context import ThreadContext
from control_plane.observe.reactions import (
    RELATION_NEW_ENTRY,
    ReactionSituation,
    SituationMessage,
    reaction_input,
)
from control_plane.observe.store import ObservationStore
from control_plane.observe.turns import ModelTurnPort, ModelUnavailable, TurnFailed

from .publication import PLATFORM, PublicationStore, approve_reaction
from .situation import clip_keeping_target
from .store import PilotReaction, PilotStore

_LOG = logging.getLogger(__name__)

JUDGE_PROMPT_NAME = "draft_judge"

POLICY_ACTIVE = "ACTIVE"
POLICY_REVOKED = "REVOKED"
POLICY_STOPPED = "STOPPED"
POLICY_EXPIRED = "EXPIRED"

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"
VERDICT_POLICY_REJECT = "POLICY_REJECT"

#: The longest a signed policy may live (migration 0008 has the same bound as a CHECK).
MAX_VALIDITY = timedelta(days=7)
#: The pace a policy may declare, at most (D-030): three an hour is the founder's ceiling from
#: 2026-09-06, and a day of one every hour is the daily one. `policy-sign` defaults stay 1 and 6.
MAX_PER_HOUR_CEILING = 3
MAX_PER_DAY_CEILING = 24
#: Reaction kinds that carry text and may enter a stranger's thread (`ReactionKind` docstring).
TEXT_KINDS: tuple[str, ...] = ("HELP", "DIAGNOSE", "CLARIFY", "DISAGREE")
#: How many drafts one publish tick judges at most: bounds model calls per 10 minutes.
CANDIDATES_PER_TICK = 3
#: Two published comments in a row below zero (read at least an hour after posting) stop the policy.
NEGATIVE_STREAK_TO_STOP = 2
#: The structural ban the founder signed: links, our own name, and the text talking about ITSELF
#: as a machine. It is not a ban on the words "bot" or "automation" as a topic: the first live
#: fresh-post draft (16.09 12:18, r/kubernetes, a thread about a support bot and prompt injection)
#: was refused for the word "bot", the same false alarm as the judge's "product names". Self-talk
#: patterns only; the judge (question 5) still reads the whole text for tool or automation talk.
FORBIDDEN_TEXT = re.compile(
    r"https?://|www\.|wenext"
    r"|\b(i am|i'm|this is|we are|we're)\s+(a|an)\s+(bot|ai|model|language model|assistant)\b"
    r"|\bas an ai\b|\blanguage model\b|\bai assistant\b"
    r"|\b(automated|auto-generated|generated)\s+(reply|response|comment|message|post)\b"
    r"|\bthis (comment|reply|message) (was|is) (written|generated|posted) by\b",
    re.IGNORECASE,
)
LONG_DASHES = chr(0x2014) + chr(0x2013)

JUDGE_CHECKS: tuple[str, ...] = (
    "kind_fits",
    "adds_something",
    "stays_within_profile",
    "sounds_like_them",
    "safe_to_post",
)

JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", *JUDGE_CHECKS, "reasons", "rationale"],
    "properties": {
        "verdict": {"type": "string", "enum": [VERDICT_PASS, VERDICT_REJECT]},
        **{check: {"type": "boolean"} for check in JUDGE_CHECKS},
        "reasons": {
            "type": "object",
            "additionalProperties": False,
            "required": list(JUDGE_CHECKS),
            "properties": {check: {"type": "string"} for check in JUDGE_CHECKS},
        },
        "rationale": {"type": "string"},
    },
}


class PolicyError(Exception):
    """The policy cannot be signed or applied as asked; the message names the rule."""


@dataclass(frozen=True)
class PublicationPolicy:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    operator_id: UUID
    status: str
    signed_at: datetime
    valid_from: datetime
    valid_until: datetime
    community_ids: tuple[UUID, ...]
    account_handle: str
    allowed_kinds: tuple[str, ...]
    min_confidence: float
    max_chars: int
    max_per_hour: int
    max_per_day: int
    judge_prompt_name: str
    judge_prompt_sha256: str
    source_text: str
    stopped_at: datetime | None
    stop_reason: str | None

    def active_at(self, now: datetime) -> bool:
        return self.status == POLICY_ACTIVE and self.valid_from <= now < self.valid_until


@dataclass(frozen=True)
class DraftJudgment:
    id: UUID
    policy_id: UUID
    reaction_id: UUID
    verdict: str
    checks: dict[str, Any]
    rationale: str
    model: str | None
    effort: str | None
    prompt_name: str | None
    prompt_sha256: str | None
    input_sha256: str | None
    created_at: datetime


@dataclass(frozen=True)
class JudgeResult:
    verdict: str
    checks: dict[str, Any]
    rationale: str
    model: str
    effort: str
    prompt_name: str
    prompt_sha256: str
    input_sha256: str


_POLICY_COLUMNS = (
    "id, organization_id, workspace_id, operator_id, status, signed_at, valid_from, valid_until, "
    "community_ids, account_handle, allowed_kinds, min_confidence, max_chars, max_per_hour, "
    "max_per_day, judge_prompt_name, judge_prompt_sha256, source_text, stopped_at, stop_reason"
)
_JUDGMENT_COLUMNS = (
    "id, policy_id, reaction_id, verdict, checks, rationale, model, effort, prompt_name, "
    "prompt_sha256, input_sha256, created_at"
)


def _policy_of(row: asyncpg.Record) -> PublicationPolicy:
    return PublicationPolicy(
        id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        operator_id=row["operator_id"],
        status=row["status"],
        signed_at=row["signed_at"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        community_ids=tuple(row["community_ids"]),
        account_handle=row["account_handle"],
        allowed_kinds=tuple(row["allowed_kinds"]),
        min_confidence=float(row["min_confidence"]),
        max_chars=int(row["max_chars"]),
        max_per_hour=int(row["max_per_hour"]),
        max_per_day=int(row["max_per_day"]),
        judge_prompt_name=row["judge_prompt_name"],
        judge_prompt_sha256=row["judge_prompt_sha256"],
        source_text=row["source_text"],
        stopped_at=row["stopped_at"],
        stop_reason=row["stop_reason"],
    )


def _judgment_of(row: asyncpg.Record) -> DraftJudgment:
    checks = row["checks"]
    if isinstance(checks, str):
        checks = json.loads(checks)
    return DraftJudgment(
        id=row["id"],
        policy_id=row["policy_id"],
        reaction_id=row["reaction_id"],
        verdict=row["verdict"],
        checks=dict(checks or {}),
        rationale=row["rationale"],
        model=row["model"],
        effort=row["effort"],
        prompt_name=row["prompt_name"],
        prompt_sha256=row["prompt_sha256"],
        input_sha256=row["input_sha256"],
        created_at=row["created_at"],
    )


class PolicyStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        operator_id: UUID,
        valid_from: datetime,
        valid_until: datetime,
        community_ids: Sequence[UUID],
        account_handle: str,
        allowed_kinds: Sequence[str],
        min_confidence: float,
        max_chars: int,
        max_per_hour: int,
        max_per_day: int,
        judge_prompt_name: str,
        judge_prompt_sha256: str,
        source_text: str,
    ) -> PublicationPolicy:
        if not community_ids:
            raise PolicyError("polityka bez społeczności nic nie obejmuje")
        bad = [k for k in allowed_kinds if k not in TEXT_KINDS]
        if bad or not allowed_kinds:
            raise PolicyError(
                f"rodzaje reakcji poza zamkniętą listą {TEXT_KINDS}: {bad or 'brak rodzajów'}"
            )
        if not valid_from < valid_until <= valid_from + MAX_VALIDITY:
            raise PolicyError("okno polityki musi być dodatnie i nie dłuższe niż 7 dni")
        if not 1 <= int(max_per_hour) <= MAX_PER_HOUR_CEILING:
            raise PolicyError(f"tempo na godzinę poza zakresem 1..{MAX_PER_HOUR_CEILING}")
        if not int(max_per_hour) <= int(max_per_day) <= MAX_PER_DAY_CEILING:
            raise PolicyError(
                f"tempo na dobę poza zakresem {int(max_per_hour)}..{MAX_PER_DAY_CEILING}"
            )
        if not source_text.strip():
            raise PolicyError("polityka wymaga słów założyciela jako źródła")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO publication_policies
                    (organization_id, workspace_id, operator_id, valid_from, valid_until,
                     community_ids, account_handle, allowed_kinds, min_confidence, max_chars,
                     max_per_hour, max_per_day, judge_prompt_name, judge_prompt_sha256, source_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                RETURNING {_POLICY_COLUMNS}
                """,
                organization_id,
                workspace_id,
                operator_id,
                valid_from,
                valid_until,
                list(community_ids),
                account_handle,
                list(allowed_kinds),
                float(min_confidence),
                int(max_chars),
                int(max_per_hour),
                int(max_per_day),
                judge_prompt_name,
                judge_prompt_sha256,
                source_text,
            )
        return _policy_of(row)

    async def get(self, policy_id: UUID) -> PublicationPolicy | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_POLICY_COLUMNS} FROM publication_policies WHERE id = $1", policy_id
            )
        return _policy_of(row) if row else None

    async def latest(self, *, organization_id: UUID) -> PublicationPolicy | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_POLICY_COLUMNS} FROM publication_policies WHERE organization_id = $1 "
                "ORDER BY signed_at DESC LIMIT 1",
                organization_id,
            )
        return _policy_of(row) if row else None

    async def expire_stale(self, *, organization_id: UUID, now: datetime) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE publication_policies SET status = $3 "
                "WHERE organization_id = $1 AND status = $2 AND valid_until <= $4",
                organization_id,
                POLICY_ACTIVE,
                POLICY_EXPIRED,
                now,
            )
        return int(result.split()[-1])

    async def active(self, *, organization_id: UUID, now: datetime) -> PublicationPolicy | None:
        """The one policy that may approve now: ACTIVE and inside its window. `expire_stale` first,
        so a policy past `valid_until` never answers here even if nobody updated its status."""
        await self.expire_stale(organization_id=organization_id, now=now)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_POLICY_COLUMNS} FROM publication_policies WHERE organization_id = $1 "
                "AND status = $2 AND valid_from <= $3 AND valid_until > $3 "
                "ORDER BY signed_at DESC LIMIT 1",
                organization_id,
                POLICY_ACTIVE,
                now,
            )
        return _policy_of(row) if row else None

    async def stop(
        self, policy_id: UUID, *, reason: str, at: datetime, status: str = POLICY_STOPPED
    ) -> PublicationPolicy | None:
        if status not in (POLICY_STOPPED, POLICY_REVOKED):
            raise PolicyError(f"zatrzymanie polityki to STOPPED albo REVOKED, nie {status}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE publication_policies SET status = $2, stopped_at = $3, stop_reason = $4 "
                f"WHERE id = $1 AND status = $5 RETURNING {_POLICY_COLUMNS}",
                policy_id,
                status,
                at,
                reason[:300],
                POLICY_ACTIVE,
            )
        return _policy_of(row) if row else None

    async def stop_all(
        self, *, organization_id: UUID, reason: str, at: datetime, status: str = POLICY_STOPPED
    ) -> int:
        if status not in (POLICY_STOPPED, POLICY_REVOKED):
            raise PolicyError(f"zatrzymanie polityki to STOPPED albo REVOKED, nie {status}")
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE publication_policies SET status = $2, stopped_at = $3, stop_reason = $4 "
                "WHERE organization_id = $1 AND status = $5",
                organization_id,
                status,
                at,
                reason[:300],
                POLICY_ACTIVE,
            )
        return int(result.split()[-1])

    async def candidate_ids(
        self, policy: PublicationPolicy, *, now: datetime, limit: int
    ) -> tuple[UUID, ...]:
        """Drafts the policy may look at: text reactions of its communities' runs, written inside
        the policy window, still pending, with neither a publication request nor a judgment."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT r.id
                FROM pilot_reactions r
                JOIN pilot_runs run ON run.id = r.run_id
                WHERE run.organization_id = $1
                  AND run.community_id = ANY($2::uuid[])
                  AND r.draft IS NOT NULL
                  AND r.review_status = 'HUMAN_REVIEW_PENDING'
                  AND r.created_at >= $3 AND r.created_at < $4
                  AND NOT EXISTS (SELECT 1 FROM publication_requests p WHERE p.reaction_id = r.id)
                  AND NOT EXISTS (SELECT 1 FROM draft_judgments j WHERE j.reaction_id = r.id)
                ORDER BY r.created_at
                LIMIT $5
                """,
                policy.organization_id,
                list(policy.community_ids),
                policy.valid_from,
                min(now, policy.valid_until),
                limit,
            )
        return tuple(r["id"] for r in rows)

    async def record_judgment(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        policy_id: UUID,
        reaction_id: UUID,
        verdict: str,
        checks: dict[str, Any],
        rationale: str,
        model: str | None = None,
        effort: str | None = None,
        prompt_name: str | None = None,
        prompt_sha256: str | None = None,
        input_sha256: str | None = None,
    ) -> DraftJudgment:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO draft_judgments
                    (organization_id, workspace_id, policy_id, reaction_id, verdict, checks,
                     rationale, model, effort, prompt_name, prompt_sha256, input_sha256)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
                RETURNING {_JUDGMENT_COLUMNS}
                """,
                organization_id,
                workspace_id,
                policy_id,
                reaction_id,
                verdict,
                json.dumps(checks, ensure_ascii=False, sort_keys=True),
                rationale[:1000],
                model,
                effort,
                prompt_name,
                prompt_sha256,
                input_sha256,
            )
        return _judgment_of(row)

    async def judgment_for(self, reaction_id: UUID) -> DraftJudgment | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_JUDGMENT_COLUMNS} FROM draft_judgments WHERE reaction_id = $1",
                reaction_id,
            )
        return _judgment_of(row) if row else None

    async def judgments_for_run(self, run_id: UUID) -> tuple[DraftJudgment, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {', '.join('j.' + c.strip() for c in _JUDGMENT_COLUMNS.split(','))} "
                "FROM draft_judgments j JOIN pilot_reactions r ON r.id = j.reaction_id "
                "WHERE r.run_id = $1 ORDER BY j.created_at",
                run_id,
            )
        return tuple(_judgment_of(r) for r in rows)

    async def judgment_counts(self, policy_id: UUID) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT verdict, count(*) AS n FROM draft_judgments WHERE policy_id = $1 "
                "GROUP BY verdict",
                policy_id,
            )
        return {r["verdict"]: int(r["n"]) for r in rows}

    async def recent_published_scores(
        self, *, organization_id: UUID, limit: int
    ) -> tuple[int | None, ...]:
        """Newest published comments first: the score of the latest control read taken at least an
        hour after publication, `None` when no such read exists yet."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT (
                    SELECT o.score FROM publication_observations o
                    WHERE o.request_id = r.id
                      AND o.observed_at >= r.published_at + interval '1 hour'
                    ORDER BY o.observed_at DESC LIMIT 1
                ) AS score
                FROM publication_requests r
                WHERE r.organization_id = $1 AND r.status = 'PUBLISHED'
                ORDER BY r.published_at DESC
                LIMIT $2
                """,
                organization_id,
                limit,
            )
        return tuple(None if r["score"] is None else int(r["score"]) for r in rows)


def rule_check(policy: PublicationPolicy, reaction: PilotReaction) -> str | None:
    """The policy's mechanical rules. Returns the reason of the first failure, `None` when all
    pass. No model is consulted here; a failure costs nothing and is final for this draft."""
    draft = reaction.draft or ""
    if not draft.strip():
        return "propozycja bez tekstu"
    if reaction.decision not in policy.allowed_kinds:
        return f"rodzaj {reaction.decision} poza polityką ({', '.join(policy.allowed_kinds)})"
    if reaction.confidence < policy.min_confidence:
        return f"pewność {reaction.confidence:.2f} poniżej progu {policy.min_confidence:.2f}"
    if not reaction.context_complete:
        return "kontekst odczytu niekompletny"
    if len(draft) > policy.max_chars:
        return f"{len(draft)} znaków, limit {policy.max_chars}"
    if any(ch in draft for ch in LONG_DASHES):
        return "długa pauza w tekście"
    if FORBIDDEN_TEXT.search(draft):
        return "adres URL, nasza nazwa albo tekst mówiący o sobie jako o automacie"
    return None


def situation_for_judge(
    context: ThreadContext,
    reaction: PilotReaction,
    *,
    platform: str,
    community_name: str,
) -> ReactionSituation:
    """The conversation as the draft's author saw it: the thread and the comments observed up to
    the reaction's `read_at`, clipped the same way the generator clips, target kept."""
    known = {context.external_id} | {c.external_id for c in context.comments}
    root = SituationMessage(
        message_id=context.external_id,
        parent_id=None,
        author=context.author,
        body="\n\n".join(part for part in (context.title, context.body or "") if part),
        created_at=context.created_at,
        own=False,
    )
    messages: list[SituationMessage] = [root]
    for comment in context.comments:
        if comment.created_at > reaction.read_at:
            continue
        parent = comment.parent_external_id
        if parent is None or parent not in known:
            parent = context.external_id
        messages.append(
            SituationMessage(
                message_id=comment.external_id,
                parent_id=parent,
                author=comment.author,
                body=comment.body or "[deleted]",
                created_at=comment.created_at,
                own=False,
            )
        )
    target = reaction.target_message_external_id
    if target not in {m.message_id for m in messages}:
        target = context.external_id
    kept, _ = clip_keeping_target(messages, target_id=target)
    return ReactionSituation(
        situation_id=f"judge-{reaction.id}",
        platform=platform,
        community_display_name=community_name,
        language="en",
        captured_at=reaction.read_at,
        transcript=tuple(kept),
        target_message_id=target,
        relation=RELATION_NEW_ENTRY,
        relation_evidence="pilot: konto nie pisało jeszcze w tym wątku",
        own_message_ids=(),
        materials=(),
        recent_own_drafts=(),
    )


def judge_input(
    situation: ReactionSituation,
    *,
    profile: dict[str, str],
    reaction: PilotReaction,
    policy: PublicationPolicy,
    now: datetime,
) -> dict[str, Any]:
    payload = reaction_input(situation, profile=profile, now=now)
    payload["candidate"] = {
        "decision": reaction.decision,
        "draft": reaction.draft,
        "contribution": reaction.contribution,
        "brief_rationale": reaction.brief_rationale,
    }
    payload["policy"] = {
        "allowed_kinds": list(policy.allowed_kinds),
        "min_confidence": policy.min_confidence,
        "max_chars": policy.max_chars,
    }
    return payload


class JudgeContractBroken(Exception):
    """The judge's answer does not match the contract; the draft is treated as REJECT (fail
    closed) and the rationale names the break."""


def judge_result_from_output(
    output: dict[str, Any],
    *,
    model: str,
    effort: str,
    prompt: prompts.PromptVersion,
    input_sha: str,
) -> JudgeResult:
    verdict = output.get("verdict")
    if verdict not in (VERDICT_PASS, VERDICT_REJECT):
        raise JudgeContractBroken(f"verdict {verdict!r} poza {VERDICT_PASS}/{VERDICT_REJECT}")
    checks: dict[str, Any] = {}
    reasons = output.get("reasons") or {}
    for check in JUDGE_CHECKS:
        value = output.get(check)
        if not isinstance(value, bool):
            raise JudgeContractBroken(f"{check} nie jest wartością logiczną: {value!r}")
        checks[check] = {"ok": value, "reason": str(reasons.get(check) or "")[:300]}
    rationale = str(output.get("rationale") or "").strip()
    if not rationale:
        raise JudgeContractBroken("puste uzasadnienie")
    if verdict == VERDICT_PASS and not all(c["ok"] for c in checks.values()):
        # The contract: PASS means all five hold. A PASS with a false check is a REJECT.
        verdict = VERDICT_REJECT
        rationale = "Sędzia dał PASS przy niespełnionym pytaniu; liczy się pytanie. " + rationale
    return JudgeResult(
        verdict=verdict,
        checks=checks,
        rationale=rationale[:1000],
        model=model,
        effort=effort,
        prompt_name=prompt.name,
        prompt_sha256=prompt.sha256,
        input_sha256=input_sha,
    )


async def judge_draft(
    model: ModelTurnPort,
    *,
    situation: ReactionSituation,
    profile: dict[str, str],
    reaction: PilotReaction,
    policy: PublicationPolicy,
    now: datetime,
) -> JudgeResult:
    """One judgment for one draft through the product's model port. Raises `ModelUnavailable` and
    `TurnFailed` unchanged; a contract break becomes `JudgeContractBroken`."""
    prompt = prompts.get(JUDGE_PROMPT_NAME)
    payload = judge_input(situation, profile=profile, reaction=reaction, policy=policy, now=now)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    input_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    outcome = await model.run(
        role=prompt.role,
        instructions=prompt.text,
        user_input=encoded,
        output_schema=JUDGE_OUTPUT_SCHEMA,
    )
    return judge_result_from_output(
        outcome.structured_output,
        model=outcome.model,
        effort=outcome.effort,
        prompt=prompt,
        input_sha=input_sha,
    )


class PolicyAutoApprover:
    """Runs inside the publish tick: judges pending drafts of the active policy and approves the
    ones that pass. Returns human-readable events for the log; never raises past the tick."""

    def __init__(
        self,
        *,
        store: ObservationStore,
        pilot: PilotStore,
        publication: PublicationStore,
        approvals: ApprovalStore,
        policies: PolicyStore,
        turns: ModelTurnPort | None,
        organization_id: UUID,
    ) -> None:
        self._store = store
        self._pilot = pilot
        self._publication = publication
        self._approvals = approvals
        self._policies = policies
        self._turns = turns
        self._organization_id = organization_id

    async def active(self, now: datetime) -> PublicationPolicy | None:
        return await self._policies.active(organization_id=self._organization_id, now=now)

    async def stop(self, *, reason: str, now: datetime) -> int:
        return await self._policies.stop_all(
            organization_id=self._organization_id, reason=reason, at=now
        )

    async def tick(self, now: datetime) -> list[str]:
        policy = await self.active(now)
        if policy is None:
            return []
        events: list[str] = []
        scores = await self._policies.recent_published_scores(
            organization_id=self._organization_id, limit=NEGATIVE_STREAK_TO_STOP
        )
        if len(scores) == NEGATIVE_STREAK_TO_STOP and all(s is not None and s < 0 for s in scores):
            reason = (
                f"{NEGATIVE_STREAK_TO_STOP} ostatnie komentarze poniżej zera ({list(scores)}): "
                "polityka zatrzymana, decyzja człowieka"
            )
            await self._policies.stop(policy.id, reason=reason, at=now)
            return [reason]
        for reaction_id in await self._policies.candidate_ids(
            policy, now=now, limit=CANDIDATES_PER_TICK
        ):
            reaction = await self._pilot.get_reaction(reaction_id)
            if reaction is None:
                continue
            event = await self._consider(policy, reaction, now)
            events.append(event)
            if event.startswith("MODEL_UNAVAILABLE"):
                break
        return events

    async def _consider(
        self, policy: PublicationPolicy, reaction: PilotReaction, now: datetime
    ) -> str:
        label = f"{reaction.thread_external_id} ({reaction.decision} {reaction.confidence:.2f})"
        run = await self._pilot.get_run(reaction.run_id)
        if run is None:
            return f"POMINIĘTA {label}: run nie istnieje"
        reason = rule_check(policy, reaction)
        if reason is not None:
            await self._policies.record_judgment(
                organization_id=self._organization_id,
                workspace_id=run.workspace_id,
                policy_id=policy.id,
                reaction_id=reaction.id,
                verdict=VERDICT_POLICY_REJECT,
                checks={"rule": reason},
                rationale=f"Reguła polityki: {reason}.",
            )
            return f"POLICY_REJECT {label}: {reason}"
        if self._turns is None:
            return f"BRAK_MODELU {label}: sędzia nie ma modelu (WENEXT_CODEX_HOME), szkic czeka"
        community = await self._store.get_community(
            run.community_id, organization_id=self._organization_id
        )
        context = await self._store.thread_context(
            reaction.thread_id, organization_id=self._organization_id
        )
        profile = await self._pilot.profile_by_id(reaction.profile_version_id)
        if community is None or context is None or profile is None:
            return f"POMINIĘTA {label}: brak community, kontekstu wątku albo profilu w bazie"
        reserved = await self._pilot.reserve_model_call(reaction.run_id)
        if reserved is None:
            return f"BUDŻET {label}: budżet runu wyczerpany, sędzia czeka"
        situation = situation_for_judge(
            context,
            reaction,
            platform=community.platform.value,
            community_name=community.display_name,
        )
        try:
            result = await judge_draft(
                self._turns,
                situation=situation,
                profile=profile,
                reaction=reaction,
                policy=policy,
                now=now,
            )
        except ModelUnavailable as exc:
            await self._pilot.release_model_call(reaction.run_id)
            return f"MODEL_UNAVAILABLE {label}: {str(exc)[:200]}"
        except TurnFailed as exc:
            return f"TURN_FAILED {label}: {str(exc)[:200]} (wywołanie zużyte, szkic czeka)"
        except JudgeContractBroken as exc:
            prompt = prompts.get(JUDGE_PROMPT_NAME)
            await self._policies.record_judgment(
                organization_id=self._organization_id,
                workspace_id=run.workspace_id,
                policy_id=policy.id,
                reaction_id=reaction.id,
                verdict=VERDICT_REJECT,
                checks={"contract": str(exc)[:300]},
                rationale=f"Odpowiedź sędziego niezgodna z kontraktem: {exc}"[:1000],
                model="unknown",
                effort="unknown",
                prompt_name=prompt.name,
                prompt_sha256=prompt.sha256,
                input_sha256="0" * 64,
            )
            return f"REJECT {label}: kontrakt sędziego złamany ({str(exc)[:120]})"
        judgment = await self._policies.record_judgment(
            organization_id=self._organization_id,
            workspace_id=run.workspace_id,
            policy_id=policy.id,
            reaction_id=reaction.id,
            verdict=result.verdict,
            checks=result.checks,
            rationale=result.rationale,
            model=result.model,
            effort=result.effort,
            prompt_name=result.prompt_name,
            prompt_sha256=result.prompt_sha256,
            input_sha256=result.input_sha256,
        )
        if result.verdict != VERDICT_PASS:
            return f"REJECT {label}: {result.rationale[:160]}"
        account_id = await self._publication.social_account_id(
            organization_id=self._organization_id,
            platform=PLATFORM,
            handle=policy.account_handle,
        )
        position = await self._publication.next_position(organization_id=self._organization_id)
        approved = await approve_reaction(
            approvals=self._approvals,
            publication=self._publication,
            reaction=reaction,
            organization_id=self._organization_id,
            workspace_id=run.workspace_id,
            account_id=account_id,
            operator_id=policy.operator_id,
            consent_source=f"policy:{policy.id} judgment:{judgment.id}",
            position=position,
            now=now,
        )
        return (
            f"PASS {label}: wniosek #{approved.request.position} {approved.request.status}, "
            f"rewizja {approved.request.proposal_revision_id}"
        )


__all__ = [
    "CANDIDATES_PER_TICK",
    "JUDGE_CHECKS",
    "JUDGE_OUTPUT_SCHEMA",
    "JUDGE_PROMPT_NAME",
    "MAX_PER_DAY_CEILING",
    "MAX_PER_HOUR_CEILING",
    "MAX_VALIDITY",
    "NEGATIVE_STREAK_TO_STOP",
    "POLICY_ACTIVE",
    "POLICY_EXPIRED",
    "POLICY_REVOKED",
    "POLICY_STOPPED",
    "TEXT_KINDS",
    "VERDICT_PASS",
    "VERDICT_POLICY_REJECT",
    "VERDICT_REJECT",
    "DraftJudgment",
    "JudgeContractBroken",
    "JudgeResult",
    "PolicyAutoApprover",
    "PolicyError",
    "PolicyStore",
    "PublicationPolicy",
    "judge_draft",
    "judge_input",
    "judge_result_from_output",
    "rule_check",
    "situation_for_judge",
]
