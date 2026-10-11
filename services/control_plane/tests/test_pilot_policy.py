"""D-029, tryb 7 dni: the signed policy judges pending drafts and approves the passing ones through
the SAME approval path a human consent document uses; the publish chain posts them. No capsule, no
Reddit, no Codex: a scripted source, a fake port and a scripted judge. What is measured: the three
gates (rules, judge, approval) in order, the trail each approval leaves, and every way the policy
stops."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from control_plane.approval.store import ApprovalStore
from control_plane.execution.store import ExecutionStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe import prompts
from control_plane.observe.store import ObservationStore
from control_plane.observe.turns import FakeTurnRunner, ModelUnavailable
from control_plane.pilot.policy import (
    JUDGE_CHECKS,
    JUDGE_PROMPT_NAME,
    POLICY_EXPIRED,
    POLICY_REVOKED,
    POLICY_STOPPED,
    VERDICT_PASS,
    VERDICT_POLICY_REJECT,
    VERDICT_REJECT,
    PolicyAutoApprover,
    PolicyError,
    PolicyStore,
    judge_result_from_output,
    rule_check,
)
from control_plane.pilot.publication import PublicationStore
from control_plane.pilot.publisher import OUTCOME_NO_REQUEST, OUTCOME_PUBLISHED, PublishHandlers
from control_plane.pilot.store import PilotStore
from test_pilot_publication import seeded_draft
from test_pilot_publisher import (
    ACCOUNT,
    READY,
    FakePort,
    ScriptedRedditSource,
    approved,
    thread_json,
)

pytestmark = pytest.mark.db

ALL_KINDS = ("HELP", "DISAGREE", "DIAGNOSE", "CLARIFY")


def judge_output(verdict: str = VERDICT_PASS, **overrides):
    out = {
        "verdict": verdict,
        **{check: True for check in JUDGE_CHECKS},
        "reasons": {check: "ok" for check in JUDGE_CHECKS},
        "rationale": "Konkretna odpowiedź w rejestrze autora.",
    }
    if verdict == VERDICT_REJECT:
        out["stays_within_profile"] = False
        out["rationale"] = "Przypisuje autorowi doświadczenie, którego profil nie zna."
    out.update(overrides)
    return out


async def sign(
    pool,
    tenant,
    community_id,
    *,
    min_confidence=0.8,
    valid_from=None,
    valid_until=None,
    max_per_hour=1,
    max_per_day=6,
):
    prompt = prompts.get(JUDGE_PROMPT_NAME)
    now = datetime.now(UTC)
    return await PolicyStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        operator_id=tenant["operator"],
        valid_from=valid_from or now - timedelta(hours=1),
        valid_until=valid_until or now + timedelta(days=6),
        community_ids=[community_id],
        account_handle=ACCOUNT,
        allowed_kinds=ALL_KINDS,
        min_confidence=min_confidence,
        max_chars=700,
        max_per_hour=max_per_hour,
        max_per_day=max_per_day,
        judge_prompt_name=prompt.name,
        judge_prompt_sha256=prompt.sha256,
        source_text="test: założyciel podpisał politykę",
    )


def build(pool, tenant, *, judge: FakeTurnRunner | None, source=None, port=None):
    source = source or ScriptedRedditSource()
    port = port or FakePort()

    async def capsule_state():
        return dict(READY)

    auto = PolicyAutoApprover(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        policies=PolicyStore(pool),
        turns=judge,
        organization_id=tenant["organization"],
    )
    handlers = PublishHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        execution=ExecutionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        source=source,
        capsule_state=capsule_state,
        port_factory=lambda account: port,
        organization_id=tenant["organization"],
        autoapprover=auto,
    )
    return handlers, auto, source, port


async def decision_reason(pool, request) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT reason FROM approval_decisions WHERE id = $1", request.approval_decision_id
        )


async def test_a_passing_draft_is_approved_by_the_policy_and_published_by_the_chain(
    pool, tenant
) -> None:
    run, reaction = await seeded_draft(
        pool, tenant, external_id="1pol001", draft="Measure the queue depth before tuning workers."
    )
    policy = await sign(pool, tenant, run.community_id)
    judge = FakeTurnRunner(outputs=[judge_output()])
    handlers, _, source, port = build(pool, tenant, judge=judge)
    source.threads["1pol001"] = thread_json("1pol001", comments=[{"id": "k1"}])

    result = await handlers.run_once(datetime.now(UTC))

    assert result.outcome == OUTCOME_PUBLISHED, result
    assert len(judge.calls) == 1 and '"candidate"' in judge.calls[0].user_input
    assert '"draft": "Measure the queue depth' in judge.calls[0].user_input
    assert len(port.calls) == 1 and port.calls[0].text_and_media.startswith("Measure the queue")
    request = await PublicationStore(pool).request_for_reaction(reaction.id)
    assert request is not None and request.status == "PUBLISHED"
    assert request.consent_source.startswith(f"policy:{policy.id} judgment:")
    assert str(policy.id) in (await decision_reason(pool, request)), "decyzja wskazuje politykę"
    judgment = await PolicyStore(pool).judgment_for(reaction.id)
    assert judgment is not None and judgment.verdict == VERDICT_PASS
    assert judgment.prompt_sha256 == policy.judge_prompt_sha256 and judgment.model == "fake-model"
    refreshed = await PilotStore(pool).get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == run.model_calls_used + 1, (
        "sędzia kosztuje jedno wywołanie z budżetu runu"
    )


async def test_the_active_policys_pace_governs_publication_not_the_runs_manifest(
    pool, tenant
) -> None:
    """D-030: the run's manifest says 1/h and 6/day; the founder signed 2/h and 12/day. Two
    human consents from the same run go out in the same hour while the policy is active, and the
    third waits with the policy named in the pacing detail."""
    first, second, third = await approved(
        pool,
        tenant,
        ("1pace01", "First in the hour."),
        ("1pace02", "Second in the same hour."),
        ("1pace03", "Third waits."),
    )
    run = await PilotStore(pool).get_run(first.run_id)
    assert run is not None and run.max_publications_per_hour == 1, "manifest runu mówi 1/h"
    policy = await sign(pool, tenant, run.community_id, max_per_hour=2, max_per_day=12)
    handlers, _, source, port = build(pool, tenant, judge=FakeTurnRunner(outputs=[]))
    for external_id in ("1pace01", "1pace02", "1pace03"):
        source.threads[external_id] = thread_json(external_id, comments=[{"id": "k"}])
    now = datetime.now(UTC)

    outcomes = [(await handlers.run_once(now + timedelta(minutes=i))).outcome for i in range(3)]

    assert outcomes == [OUTCOME_PUBLISHED, OUTCOME_PUBLISHED, "PACED"], outcomes
    assert len(port.calls) == 2
    waiting = await PublicationStore(pool).request_for_reaction(third.id)
    assert waiting is not None and waiting.status == "QUEUED"
    assert waiting.detail is not None and "2/2 w ostatniej godzinie" in waiting.detail
    assert str(policy.id) in waiting.detail, "szczegół tempa wskazuje politykę, która nim rządzi"
    assert (await PublicationStore(pool).request_for_reaction(second.id)).status == "PUBLISHED"


async def test_a_rejected_draft_stays_pending_with_the_judges_reason(pool, tenant) -> None:
    run, reaction = await seeded_draft(
        pool, tenant, external_id="1pol002", draft="In my 12 years running Fortune 500 sales..."
    )
    await sign(pool, tenant, run.community_id)
    judge = FakeTurnRunner(outputs=[judge_output(VERDICT_REJECT)])
    handlers, _, source, port = build(pool, tenant, judge=judge)
    source.threads["1pol002"] = thread_json("1pol002", comments=[{"id": "k1"}])

    result = await handlers.run_once(datetime.now(UTC))

    assert result.outcome == OUTCOME_NO_REQUEST and port.calls == []
    judgment = await PolicyStore(pool).judgment_for(reaction.id)
    assert judgment is not None and judgment.verdict == VERDICT_REJECT
    assert judgment.checks["stays_within_profile"]["ok"] is False
    assert "profil nie zna" in judgment.rationale
    assert await PublicationStore(pool).request_for_reaction(reaction.id) is None
    pending = await PilotStore(pool).get_reaction(reaction.id)
    assert pending is not None and pending.review_status == "HUMAN_REVIEW_PENDING"
    # The same draft is not judged twice: the next tick finds no candidate and calls no model.
    await handlers.run_once(datetime.now(UTC) + timedelta(minutes=10))
    assert len(judge.calls) == 1


async def test_a_draft_that_breaks_a_rule_is_refused_without_a_model_call(pool, tenant) -> None:
    run, reaction = await seeded_draft(
        pool, tenant, external_id="1pol003", draft="Try https://example.com/tool, it solves this."
    )
    policy = await sign(pool, tenant, run.community_id)
    judge = FakeTurnRunner(outputs=[])
    handlers, _, _, port = build(pool, tenant, judge=judge)

    result = await handlers.run_once(datetime.now(UTC))

    assert result.outcome == OUTCOME_NO_REQUEST and judge.calls == [] and port.calls == []
    judgment = await PolicyStore(pool).judgment_for(reaction.id)
    assert judgment is not None and judgment.verdict == VERDICT_POLICY_REJECT
    assert "URL" in judgment.rationale and judgment.prompt_sha256 is None
    # The rules themselves, one by one.
    assert rule_check(policy, reaction) is not None
    from dataclasses import replace

    assert "poniżej progu" in (
        rule_check(policy, replace(reaction, draft="Plain.", confidence=0.5)) or ""
    )
    assert "długa pauza" in (rule_check(policy, replace(reaction, draft="A — B")) or "")
    assert "znaków" in (rule_check(policy, replace(reaction, draft="x" * 701)) or "")
    assert "niekompletny" in (
        rule_check(policy, replace(reaction, draft="Ok.", context_complete=False)) or ""
    )
    assert "poza polityką" in (
        rule_check(policy, replace(reaction, draft="Ok.", decision="HUMOR")) or ""
    )
    assert rule_check(policy, replace(reaction, draft="Plain, useful sentence.")) is None
    # 16.09: the topic may be bots and automation; only the text talking about ITSELF as a
    # machine is refused (the first live fresh-post draft was lost to the word "bot").
    assert (
        rule_check(
            policy,
            replace(
                reaction, draft="A support bot that sees its first hostile prompt in production."
            ),
        )
        is None
    )
    assert rule_check(policy, replace(reaction, draft="Automation of the rollout is fine.")) is None
    for self_talk in (
        "I am a bot and this is an automated reply.",
        "As an AI, I would suggest waiting.",
        "This comment was generated by a language model.",
        "See www.example.com for the tool.",
    ):
        assert "automacie" in (rule_check(policy, replace(reaction, draft=self_talk)) or "") or (
            "URL" in (rule_check(policy, replace(reaction, draft=self_talk)) or "")
        ), self_talk


async def test_an_expired_policy_approves_nothing_and_is_marked_expired(pool, tenant) -> None:
    run, reaction = await seeded_draft(pool, tenant, external_id="1pol004", draft="Plain advice.")
    now = datetime.now(UTC)
    policy = await sign(
        pool,
        tenant,
        run.community_id,
        valid_from=now - timedelta(hours=2),
        valid_until=now - timedelta(hours=1),
    )
    judge = FakeTurnRunner(outputs=[])
    _, auto, _, _ = build(pool, tenant, judge=judge)

    assert await auto.tick(now) == []
    assert judge.calls == [] and await PolicyStore(pool).judgment_for(reaction.id) is None
    stored = await PolicyStore(pool).get(policy.id)
    assert stored is not None and stored.status == POLICY_EXPIRED


async def test_revoke_and_publish_stop_end_the_policy_and_a_blocked_write_stops_it_too(
    pool, tenant
) -> None:
    run, _ = await seeded_draft(pool, tenant, external_id="1pol005", draft="Plain advice.")
    policies = PolicyStore(pool)
    now = datetime.now(UTC)
    first = await sign(pool, tenant, run.community_id)
    assert (await policies.active(organization_id=tenant["organization"], now=now)) is not None
    revoked = await policies.stop_all(
        organization_id=tenant["organization"],
        reason="policy-revoke",
        at=now,
        status=POLICY_REVOKED,
    )
    assert revoked == 1
    assert (await policies.active(organization_id=tenant["organization"], now=now)) is None
    assert (await policies.get(first.id)).status == POLICY_REVOKED
    # Signing again works; a BLOCKED write (foreign session) stops it through the publisher.
    second = await sign(pool, tenant, run.community_id)
    judge = FakeTurnRunner(outputs=[judge_output()])
    handlers, _, source, _ = build(
        pool, tenant, judge=judge, source=ScriptedRedditSource(username="somebodyelse")
    )
    source.threads["1pol005"] = thread_json("1pol005", comments=[{"id": "k1"}])
    result = await handlers.run_once(now)
    assert result.outcome == "BLOCKED", result
    stopped = await policies.get(second.id)
    assert stopped is not None and stopped.status == POLICY_STOPPED
    assert stopped.stop_reason is not None and stopped.stop_reason.startswith("BLOCKED")
    with pytest.raises(PolicyError):
        await policies.stop(second.id, reason="x", at=now, status="EXPIRED")


async def test_two_published_comments_below_zero_stop_the_policy_before_any_approval(
    pool, tenant
) -> None:
    first, second = await approved(
        pool, tenant, ("1neg001", "Negative one."), ("1neg002", "Negative two.")
    )
    judge = FakeTurnRunner(outputs=[])
    handlers, auto, source, _ = build(pool, tenant, judge=judge)
    source.threads["1neg001"] = thread_json("1neg001", comments=[{"id": "k1"}])
    source.threads["1neg002"] = thread_json("1neg002", comments=[{"id": "k2"}])
    now = datetime.now(UTC)
    assert (await handlers.run_once(now)).outcome == OUTCOME_PUBLISHED
    assert (await handlers.run_once(now + timedelta(hours=2))).outcome == OUTCOME_PUBLISHED
    publication = PublicationStore(pool)
    for reaction in (first, second):
        request = await publication.request_for_reaction(reaction.id)
        assert request is not None and request.status == "PUBLISHED"
        await publication.record_observation(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            request_id=request.id,
            observed_at=request.published_at + timedelta(hours=3),
            still_present=True,
            score=-1,
            reply_count=0,
            raw={},
        )
    run, _ = await seeded_draft(pool, tenant, external_id="1neg003", draft="Plain advice.")
    policy = await sign(pool, tenant, run.community_id)

    events = await auto.tick(now + timedelta(hours=4))

    assert len(events) == 1 and "poniżej zera" in events[0]
    stopped = await PolicyStore(pool).get(policy.id)
    assert stopped is not None and stopped.status == POLICY_STOPPED
    assert judge.calls == [], "po zatrzymaniu żaden szkic nie idzie do sędziego"


async def test_a_model_outage_returns_the_reservation_and_leaves_the_draft_for_the_next_tick(
    pool, tenant
) -> None:
    run, reaction = await seeded_draft(pool, tenant, external_id="1pol006", draft="Plain advice.")
    await sign(pool, tenant, run.community_id)
    judge = FakeTurnRunner(outputs=[ModelUnavailable("usage limit"), judge_output()])
    handlers, auto, source, _ = build(pool, tenant, judge=judge)
    source.threads["1pol006"] = thread_json("1pol006", comments=[{"id": "k1"}])
    now = datetime.now(UTC)

    events = await auto.tick(now)
    assert len(events) == 1 and events[0].startswith("MODEL_UNAVAILABLE")
    after = await PilotStore(pool).get_run(run.id)
    assert after is not None and after.model_calls_used == run.model_calls_used, (
        "rezerwacja zwrócona"
    )
    assert await PolicyStore(pool).judgment_for(reaction.id) is None

    result = await handlers.run_once(now + timedelta(minutes=10))
    assert result.outcome == OUTCOME_PUBLISHED and len(judge.calls) == 2


def test_signing_refuses_a_window_over_seven_days_and_unknown_kinds() -> None:
    now = datetime.now(UTC)
    store = PolicyStore.__new__(PolicyStore)  # validation runs before any SQL
    import asyncio

    with pytest.raises(PolicyError):
        asyncio.run(
            store.create(
                organization_id=None,
                workspace_id=None,
                operator_id=None,
                valid_from=now,
                valid_until=now + timedelta(days=8),
                community_ids=[now],
                account_handle=ACCOUNT,
                allowed_kinds=ALL_KINDS,
                min_confidence=0.8,
                max_chars=700,
                max_per_hour=1,
                max_per_day=6,
                judge_prompt_name="draft_judge",
                judge_prompt_sha256="0" * 64,
                source_text="x",
            )
        )
    with pytest.raises(PolicyError):
        asyncio.run(
            store.create(
                organization_id=None,
                workspace_id=None,
                operator_id=None,
                valid_from=now,
                valid_until=now + timedelta(days=1),
                community_ids=[now],
                account_handle=ACCOUNT,
                allowed_kinds=("HUMOR",),
                min_confidence=0.8,
                max_chars=700,
                max_per_hour=1,
                max_per_day=6,
                judge_prompt_name="draft_judge",
                judge_prompt_sha256="0" * 64,
                source_text="x",
            )
        )


def test_signing_refuses_a_pace_above_the_founders_ceiling() -> None:
    """D-030: three an hour and twenty-four a day are ceilings, and a day cannot allow fewer
    than one hour does."""
    now = datetime.now(UTC)
    store = PolicyStore.__new__(PolicyStore)  # validation runs before any SQL
    import asyncio

    for per_hour, per_day in ((4, 6), (0, 6), (2, 1), (1, 25)):
        with pytest.raises(PolicyError, match="tempo"):
            asyncio.run(
                store.create(
                    organization_id=None,
                    workspace_id=None,
                    operator_id=None,
                    valid_from=now,
                    valid_until=now + timedelta(days=1),
                    community_ids=[now],
                    account_handle=ACCOUNT,
                    allowed_kinds=ALL_KINDS,
                    min_confidence=0.8,
                    max_chars=700,
                    max_per_hour=per_hour,
                    max_per_day=per_day,
                    judge_prompt_name="draft_judge",
                    judge_prompt_sha256="0" * 64,
                    source_text="x",
                )
            )


def test_a_pass_with_a_failed_check_is_a_reject_by_contract() -> None:
    prompt = prompts.get(JUDGE_PROMPT_NAME)
    result = judge_result_from_output(
        judge_output(VERDICT_PASS, safe_to_post=False),
        model="m",
        effort="e",
        prompt=prompt,
        input_sha="0" * 64,
    )
    assert result.verdict == VERDICT_REJECT and "liczy się pytanie" in result.rationale
    assert result.checks["safe_to_post"]["ok"] is False
