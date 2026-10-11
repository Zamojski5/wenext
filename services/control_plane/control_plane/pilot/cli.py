"""`python -m control_plane.pilot.cli start | status | stop | report`, run inside the `api`
container (it needs the database and the logged-in Codex home), fronted by the Makefile targets
`pilot-start`, `pilot-status`, `pilot-stop`, `pilot-report`.

`start` writes the manifest (fixed `starts_at`/`ends_at`, budget, profile version, prompt hash,
paused automations, the poll interval to restore), slows the community's poll to the pilot's
interval, runs ONE smoke attempt on a real candidate through the real model (it costs one call of
the budget and its result is the run's first reaction), verifies the row is in the database, and
enqueues the first `pilot.cycle`. Nothing is started when the smoke attempt cannot reach the model:
a pilot that cannot generate is not a pilot.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
from community_observer import UnknownCommunity
from community_os_contracts import ObservationPlatform

from control_plane.audit import AuditWriter
from control_plane.auth import DSN_VARIABLE
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe import prompts
from control_plane.observe.reactions import PROMPT_NAME, ReactionRejected, generate_reaction
from control_plane.observe.scheduling import schedule_poll
from control_plane.observe.store import ObservationStore
from control_plane.observe.turns import ModelUnavailable, TurnFailed
from control_plane.observe.wiring import build_publish_handlers, build_sources, build_turns

from .handlers import (
    CYCLE_INTERVAL_SECONDS,
    EXEMPLARS_PER_DRAFT,
    MIN_CYCLE_INTERVAL_SECONDS,
    OUTCOME_GENERATED,
    OUTCOME_NO_CANDIDATE,
    PAUSED_JOB_TYPES,
    POLICY_RUN_CALLS_PER_DAY,
    POLICY_RUN_CYCLE_SECONDS,
    PilotHandlers,
    planned_at,
    schedule_cycle,
)
from .policy import (
    JUDGE_PROMPT_NAME,
    MAX_PER_DAY_CEILING,
    MAX_PER_HOUR_CEILING,
    MAX_VALIDITY,
    POLICY_REVOKED,
    POLICY_STOPPED,
    TEXT_KINDS,
    PolicyError,
    PolicyStore,
    situation_for_judge,
)
from .publication import (
    VIEWER_ANONYMOUS,
    ConsentError,
    PublicationStore,
    approve_consents,
    parse_consent_document,
)
from .publisher import (
    PUBLISH_INTERVAL_SECONDS,
    RequeueRefused,
    nudge_publish,
    requeue_request,
    schedule_publish,
)
from .report import render_report
from .store import RUN_STOPPED, PilotStore

DEFAULT_BUDGET = 24
DEFAULT_HOURS = 24
DEFAULT_MAX_PER_THREAD = 2
DEFAULT_REPORT_DIR = Path(os.environ.get("WENEXT_ARTIFACTS_DIR", "/var/wenext/artifacts")) / "pilot"


async def _pool() -> asyncpg.Pool:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        raise SystemExit(f"brak zmiennej {DSN_VARIABLE}")
    return await asyncpg.create_pool(
        dsn.replace("postgresql+asyncpg://", "postgresql://", 1), min_size=1, max_size=3
    )


async def _tenant(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT o.id AS organization_id, w.id AS workspace_id
            FROM organizations o JOIN workspaces w ON w.organization_id = o.id
            ORDER BY o.created_at, w.created_at LIMIT 1
            """
        )
    if row is None:
        raise SystemExit("brak organizacji/workspace w bazie (make bootstrap-operator)")
    return row["organization_id"], row["workspace_id"]


def _handlers(pool: asyncpg.Pool, organization_id: UUID) -> PilotHandlers:
    return PilotHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        sources=build_sources(os.environ),
        turns=build_turns(os.environ),
        organization_id=organization_id,
        policies=PolicyStore(pool),
    )


async def cmd_enable_community(args: argparse.Namespace) -> int:
    """Start observing one community for the pilot the way `POST /api/communities` does (adapter
    validates the key, upsert, audit, first poll right away), without an operator session: the
    pilot runs on the founder's decision, recorded by the orchestrator. Read only, like every
    observation (D-027)."""
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        try:
            platform = ObservationPlatform(args.platform)
        except ValueError:
            print(f"nieznana platforma {args.platform!r}", file=sys.stderr)
            return 2
        source = build_sources(os.environ).get(platform)
        if source is None:
            print(f"brak adaptera dla {platform.value}", file=sys.stderr)
            return 2
        try:
            ref = source.describe(args.key)
        except UnknownCommunity as exc:
            print(str(exc), file=sys.stderr)
            return 2
        store = ObservationStore(pool)
        community = await store.upsert_community(
            organization_id=organization_id,
            workspace_id=workspace_id,
            ref=ref,
            poll_interval_seconds=args.poll_interval,
            thread_analysis_enabled=not args.to_learn,
        )
        async with pool.acquire() as connection, connection.transaction():
            await AuditWriter(connection).record(
                kind="COMMUNITY_OBSERVATION_ENABLED",
                organization_id=organization_id,
                workspace_id=workspace_id,
                subject_ref=f"community:{community.id}",
                actor_ref="operator:pilot-cli",
                payload={"community_id": str(community.id)},
            )
        first_poll = await schedule_poll(
            PostgresJobTransport(JobStore(pool), organization_id=organization_id),
            organization_id=organization_id,
            workspace_id=workspace_id,
            community_id=community.id,
            interval_seconds=community.poll_interval_seconds,
            now=datetime.now(UTC),
            immediately=True,
        )
        print(
            f"{ref.platform.value}:{ref.key} id={community.id} "
            f"observe_enabled={community.observe_enabled} poll={community.poll_interval_seconds} s "
            f"first_poll={first_poll.job_id} created={first_poll.created}"
        )
        return 0
    finally:
        await pool.close()


async def cmd_approve_consents(args: argparse.Namespace) -> int:
    """Consents from the document (pilot_reactions.id + sha256 of the draft) into the database
    through the existing approval path, and one queued publication request per consent. Every row
    is verified against the database before the first write; a mismatch refuses the whole batch."""
    text = (
        sys.stdin.read()
        if args.consents == "-"
        else Path(args.consents).read_text(encoding="utf-8")
    )
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        try:
            document = parse_consent_document(text)
            source = f"{args.source} sha256={document.sha256}"
            results = await approve_consents(
                pool,
                rows=document.rows,
                organization_id=organization_id,
                workspace_id=workspace_id,
                account_handle=args.account,
                consent_source=source,
                operator_id=UUID(args.operator_id) if args.operator_id else None,
                now=datetime.now(UTC),
                document_account=document.account,
            )
        except ConsentError as exc:
            print(f"zgody ODRZUCONE, nic nie zapisano: {exc}", file=sys.stderr)
            return 2
        created = sum(1 for r in results if r.created)
        stale = 0
        for r in results:
            note = "zapisana" if r.created else "już była"
            if r.revision_state != "APPROVED":
                stale += 1
                note += f", UWAGA rewizja {r.revision_state}: zgoda już nie autoryzuje"
            print(
                f"#{r.request.position} {r.request.reaction_id} {r.request.target_external_id} "
                f"{note} status={r.request.status} rewizja={r.request.proposal_revision_id} "
                f"payload_hash={r.payload_hash[:12]}"
            )
        print(
            f"zgód w dokumencie: {len(document.rows)}, zapisanych teraz: {created}, "
            f"nieaktualnych (rewizja poza APPROVED): {stale}, źródło: {source}"
        )
        return 0 if stale == 0 else 3
    finally:
        await pool.close()


async def cmd_start(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        store = ObservationStore(pool)
        pilot = PilotStore(pool)
        communities = await store.list_communities(organization_id=organization_id)
        community = next(
            (c for c in communities if f"{c.platform.value}:{c.community_key}" == args.community),
            None,
        )
        if community is None:
            print(
                f"nie ma community {args.community!r}; są: "
                f"{[f'{c.platform.value}:{c.community_key}' for c in communities]}",
                file=sys.stderr,
            )
            return 2
        if await pilot.active_run_for_community(community.id) is not None:
            print(
                "ta community ma już aktywny run pilota (pilot-status / pilot-stop)",
                file=sys.stderr,
            )
            return 2
        if not args.profile_json:
            print(
                "pilot rusza wyłącznie z jawną wersją profilu pilota (--profile-json plik|-); "
                "start na najnowszej wersji profilu z placeholderami jest zabroniony",
                file=sys.stderr,
            )
            return 2
        raw = (
            json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
            if args.profile_json != "-"
            else json.load(sys.stdin)
        )
        fields = ("knows_deeply", "positions", "would_never_claim", "voice_samples")
        if (
            not isinstance(raw, dict)
            or set(raw) != set(fields)
            or not all(isinstance(raw[f], str) and raw[f].strip() for f in fields)
        ):
            print(
                f"profil pilota musi mieć dokładnie cztery niepuste pola tekstowe {list(fields)}",
                file=sys.stderr,
            )
            return 2
        profile_row = await store.save_profile(
            organization_id=organization_id,
            workspace_id=workspace_id,
            knows_deeply=raw["knows_deeply"],
            positions=raw["positions"],
            would_never_claim=raw["would_never_claim"],
            voice_samples=raw["voice_samples"],
        )
        profile_note = f"profil pilota zapisany jako v{profile_row.version}"
        prompt = prompts.get(PROMPT_NAME)
        now = datetime.now(UTC)
        starts_at = now.replace(microsecond=0)
        ends_at = starts_at + timedelta(hours=args.hours)
        interval = int(args.cycle_interval)
        if interval < MIN_CYCLE_INTERVAL_SECONDS or interval > args.hours * 3600:
            print(
                f"--cycle-interval {interval} s poza zakresem: od "
                f"{MIN_CYCLE_INTERVAL_SECONDS} s do długości okna ({args.hours * 3600} s)",
                file=sys.stderr,
            )
            return 2
        run = await pilot.create_run(
            organization_id=organization_id,
            workspace_id=workspace_id,
            community_id=community.id,
            starts_at=starts_at,
            ends_at=ends_at,
            poll_interval_seconds=interval,
            model_calls_budget=args.budget,
            max_generations_per_thread=args.max_per_thread,
            profile_version_id=profile_row.id,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
            paused_job_types=PAUSED_JOB_TYPES,
            restore={"poll_interval_seconds": community.poll_interval_seconds},
        )
        await store.set_poll_interval(community.id, interval)
        print(
            f"run {run.id}: {starts_at.isoformat()} → {ends_at.isoformat()}, budżet "
            f"{run.model_calls_budget} wywołań, {profile_note}, prompt {prompt.short}, "
            f"cykl co {interval} s, poll community {community.poll_interval_seconds} → "
            f"{interval} s, wstrzymane {list(PAUSED_JOB_TYPES)}"
        )

        handlers = _handlers(pool, organization_id)
        smoke = await handlers.run_once(run, now=datetime.now(UTC), origin="SMOKE")
        refreshed = await pilot.get_run(run.id)
        print(
            f"smoke: {smoke.outcome} ({smoke.detail}); zużyte wywołania: "
            f"{refreshed.model_calls_used if refreshed else '?'}/{run.model_calls_budget}"
        )
        if smoke.outcome == OUTCOME_NO_CANDIDATE and args.smoke_optional:
            # A small community may have nothing to react to right now; the run still starts and
            # its cycles report NO_CANDIDATE until a thread with comments shows up.
            print(
                "smoke bez kandydata (--smoke-optional): run startuje bez pierwszej reakcji",
                file=sys.stderr,
            )
        elif smoke.outcome != OUTCOME_GENERATED:
            await handlers.finish(run, now=datetime.now(UTC), status=RUN_STOPPED)
            print(
                "smoke nie dał reakcji: run zatrzymany, konfiguracja przywrócona, pilot NIE "
                "wystartował",
                file=sys.stderr,
            )
            return 1
        saved = smoke.reaction
        if saved is not None:
            check = await pilot.reactions(run.id)
            if not any(r.id == saved.id for r in check):
                await handlers.finish(run, now=datetime.now(UTC), status=RUN_STOPPED)
                print("zapis smoke nie jest w bazie: run zatrzymany", file=sys.stderr)
                return 1
            print(
                f"pierwsza reakcja: {saved.decision} na {saved.thread_url} (id {saved.id}, "
                f"{'z tekstem' if saved.draft else 'bez tekstu'}, status {saved.review_status})"
            )
        first_slot = 1
        delay = (planned_at(run, first_slot) - datetime.now(UTC)).total_seconds()
        job = await schedule_cycle(
            PostgresJobTransport(JobStore(pool), organization_id=organization_id),
            run,
            slot=first_slot,
            delay_seconds=delay,
        )
        print(f"pierwszy cykl zlecony: job {job.job_id} slot {first_slot} za {int(delay)} s")
        return 0
    finally:
        await pool.close()


async def cmd_publish_now(args: argparse.Namespace) -> int:
    """One publication tick NOW (the smoke of R4: the first consent in the queue), then the chain
    of ticks every PUBLISH_INTERVAL_SECONDS unless `--no-chain`. Every precondition applies to the
    smoke too, the pace included: the pace is part of the consent, not a convenience."""
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        handlers = build_publish_handlers(pool, organization_id=organization_id)
        result = await handlers.run_once(datetime.now(UTC))
        print(f"tik publikacji: {result.outcome} ({result.detail})")
        if result.evidence is not None:
            print(
                f"dowód: {result.evidence.external_id} {result.evidence.permalink} "
                f"konto {result.evidence.account} odczyt {result.evidence.observed_at.isoformat()}"
            )
        if not args.no_chain:
            await schedule_publish(
                PostgresJobTransport(JobStore(pool), organization_id=organization_id),
                organization_id=organization_id,
                workspace_id=workspace_id,
                delay_seconds=PUBLISH_INTERVAL_SECONDS,
            )
            print(f"łańcuch publikacji: następny tik za {PUBLISH_INTERVAL_SECONDS} s")
        return 0 if result.outcome in ("PUBLISHED", "NO_REQUEST", "PACED") else 1
    finally:
        await pool.close()


async def cmd_publish_reconcile(args: argparse.Namespace) -> int:
    """The reading half of a tick only: stale PUBLISHING to UNKNOWN, UNKNOWN reconciled against
    the thread, PUBLISHED observed. Issues no intent, publishes nothing."""
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        handlers = build_publish_handlers(pool, organization_id=organization_id)
        await handlers.reconcile_only(datetime.now(UTC))
        publication = PublicationStore(pool)
        for request in await publication.requests(organization_id=organization_id):
            # `status` says what WE did, `public_visibility` says what the community can see.
            # They are printed side by side because PUBLISHED alone was read as both for ten days.
            checked = (
                request.public_visibility_checked_at.isoformat(timespec="minutes")
                if request.public_visibility_checked_at
                else "nigdy"
            )
            print(
                f"#{request.position} {request.status} / widoczność {request.public_visibility} "
                f"(sprawdzono: {checked}): {request.detail or ''}"[:260]
            )
        return 0
    finally:
        await pool.close()


async def cmd_publish_requeue(args: argparse.Namespace) -> int:
    """Put one UNKNOWN request back in the queue under a fresh consent of the same content. Refused
    unless the failure happened before anything was sent AND a live read of the thread shows no
    comment by our account AND the recomputed digest equals the original (see `requeue_request`)."""
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        handlers = build_publish_handlers(pool, organization_id=organization_id)
        publication = PublicationStore(pool)
        operator = (
            UUID(args.operator_id)
            if args.operator_id
            else await publication.sole_operator_id(organization_id=organization_id)
        )
        try:
            request = await requeue_request(
                handlers,
                request_id=UUID(args.request),
                operator_id=operator,
                now=datetime.now(UTC),
            )
        except RequeueRefused as exc:
            print(f"ponowienie ODRZUCONE: {exc}", file=sys.stderr)
            return 2
        print(
            f"#{request.position} {request.reaction_id} -> {request.status}; rewizja "
            f"{request.proposal_revision_id}, zgoda {request.approval_decision_id}; "
            f"{request.detail}"
        )
        return 0
    finally:
        await pool.close()


async def cmd_publish_status(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        publication = PublicationStore(pool)
        rows = []
        for request in await publication.requests(organization_id=organization_id):
            evidence = (
                await publication.evidence_for(request.attempt_id) if request.attempt_id else None
            )
            observation = await publication.latest_observation(request.id)
            # The anonymous read-back is shown SEPARATELY from `last_observation`, because the
            # two answer different questions and reading one as the other is the incident of
            # 17.09 (STAN-PRAC §8.22): `last_observation` is us looking at our own comment while
            # logged in, `public_visibility` is whether anyone else can see it.
            anonymous = await publication.latest_observation(request.id, viewer=VIEWER_ANONYMOUS)
            rows.append(
                {
                    "position": request.position,
                    "request_id": str(request.id),
                    "reaction_id": str(request.reaction_id),
                    "target": request.target_external_id,
                    "thread_url": request.target_url,
                    "status": request.status,
                    "public_visibility": request.public_visibility,
                    "public_visibility_checked_at": (
                        request.public_visibility_checked_at.isoformat()
                        if request.public_visibility_checked_at
                        else None
                    ),
                    "removal_reported_at": (
                        request.removal_reported_at.isoformat()
                        if request.removal_reported_at
                        else None
                    ),
                    "removal_source": request.removal_source,
                    "detail": request.detail,
                    "published_at": request.published_at.isoformat()
                    if request.published_at
                    else None,
                    "evidence": (
                        {
                            "external_id": evidence["external_id"],
                            "permalink": evidence["permalink"],
                            "account": evidence["account"],
                            "observed_at": evidence["observed_at"].isoformat(),
                            "source": evidence["source"],
                        }
                        if evidence
                        else None
                    ),
                    "last_observation": (
                        {
                            "observed_at": observation["observed_at"].isoformat(),
                            "viewer": observation["viewer"],
                            "still_present": observation["still_present"],
                            "score": observation["score"],
                            "reply_count": observation["reply_count"],
                        }
                        if observation
                        else None
                    ),
                    "last_anonymous_read": (
                        {
                            "observed_at": anonymous["observed_at"].isoformat(),
                            "visible": anonymous["still_present"],
                            "score": anonymous["score"],
                            "reply_count": anonymous["reply_count"],
                        }
                        if anonymous
                        else None
                    ),
                }
            )
        print(
            json.dumps(
                {"requests": rows, "now": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        await pool.close()


async def cmd_publish_stop(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        publication = PublicationStore(pool)
        now = datetime.now(UTC)
        stopped = await publication.stop_all_queued(
            organization_id=organization_id, detail=f"publish-stop {now.isoformat()}"
        )
        runs = await publication.stop_publishing(organization_id=organization_id, at=now)
        policies = await PolicyStore(pool).stop_all(
            organization_id=organization_id,
            reason=f"publish-stop {now.isoformat()}",
            at=now,
            status=POLICY_STOPPED,
        )
        print(
            f"publikacja zatrzymana o {now.isoformat()}: {stopped} wniosków QUEUED -> STOPPED, "
            f"flaga w {runs} aktywnych manifestach, polityk zatrzymanych: {policies}; worker nic "
            "już nie opublikuje ani nie zatwierdzi"
        )
        return 0
    finally:
        await pool.close()


async def cmd_status(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        pilot = PilotStore(pool)
        run = (
            await pilot.get_run(UUID(args.run_id))
            if args.run_id
            else await pilot.latest_run(organization_id=organization_id)
        )
        if run is None:
            print("brak runu pilota")
            return 1
        now = datetime.now(UTC)
        cycles = await pilot.cycles(run.id)
        reactions = await pilot.reactions(run.id)
        by_outcome: dict[str, int] = {}
        for c in cycles:
            by_outcome[c.outcome] = by_outcome.get(c.outcome, 0) + 1
        by_decision: dict[str, int] = {}
        for r in reactions:
            by_decision[r.decision] = by_decision.get(r.decision, 0) + 1
        print(
            json.dumps(
                {
                    "run_id": str(run.id),
                    "status": run.status,
                    "active_now": run.active_at(now),
                    "starts_at": run.starts_at.isoformat(),
                    "ends_at": run.ends_at.isoformat(),
                    "stopped_at": run.stopped_at.isoformat() if run.stopped_at else None,
                    "now": now.isoformat(),
                    "model_calls": f"{run.model_calls_used}/{run.model_calls_budget}",
                    "cycles": len(cycles),
                    "cycles_by_outcome": by_outcome,
                    "last_cycle": (
                        {
                            "slot": cycles[-1].slot,
                            "started_at": cycles[-1].started_at.isoformat(),
                            "outcome": cycles[-1].outcome,
                            "detail": cycles[-1].detail,
                        }
                        if cycles
                        else None
                    ),
                    "reactions": len(reactions),
                    "reactions_by_decision": by_decision,
                    "host_events": run.host_events,
                    "profile_version_id": str(run.profile_version_id),
                    "prompt": f"{run.prompt_name}+{run.prompt_sha256[:12]}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        await pool.close()


async def cmd_stop(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        pilot = PilotStore(pool)
        run = (
            await pilot.get_run(UUID(args.run_id))
            if args.run_id
            else await pilot.latest_run(organization_id=organization_id)
        )
        if run is None:
            print("brak runu pilota")
            return 1
        finished = await _handlers(pool, organization_id).finish(
            run, now=datetime.now(UTC), status=RUN_STOPPED
        )
        if finished is None:
            print(f"run {run.id} nie był aktywny (status {run.status}); nic nie zmieniono")
            return 1
        stopped = finished.stopped_at.isoformat() if finished.stopped_at else "?"
        # pilot-stop halts the publication queue too (docs/ZGODY-PUBLIKACJI-2026-09-07.md).
        publication = PublicationStore(pool)
        halted = await publication.stop_all_queued(
            organization_id=organization_id, detail=f"pilot-stop {stopped}"
        )
        await publication.stop_publishing(organization_id=organization_id, at=datetime.now(UTC))
        print(
            f"run {run.id} zatrzymany o {stopped}; poll community przywrócony; "
            f"kolejny zaplanowany cykl zakończy się jako RUN_ENDED; publikacja zatrzymana "
            f"({halted} wniosków QUEUED -> STOPPED)"
        )
        return 0
    finally:
        await pool.close()


def render_outcomes(rows: Sequence[Mapping[str, Any]], *, now: datetime) -> str:
    """D-031: the research table. One row per published comment with the post's age when we
    answered, what was asked and what was written, the latest points against the thread's median
    top-level comment, replies; then the same aggregated per community and move, and per age of
    the post. Points in r/b2b_sales barely move (max 2 measured on 47 comments), so the reply share
    is printed next to them everywhere. Pure rendering: the caller supplies the rows."""

    def hours(a: datetime | None, b: datetime | None) -> float | None:
        if a is None or b is None:
            return None
        return round((a - b).total_seconds() / 3600, 1)

    def fmt(value: Any, digits: int = 1) -> str:
        if value is None:
            return "brak"
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    def age_bucket(h: float | None) -> str:
        if h is None:
            return "brak daty"
        if h < 1:
            return "poniżej 1 h"
        if h < 6:
            return "1 do 6 h"
        return "6 h i więcej"

    def median(values: list[float]) -> float | None:
        return None if not values else sorted(values)[len(values) // 2]

    def source_of(r: Mapping[str, Any]) -> str:
        return "polityka" if str(r.get("consent_source") or "").startswith("policy:") else "zgoda"

    def age_of(r: Mapping[str, Any]) -> float | None:
        return hours(r.get("published_at"), r.get("post_created_at"))

    def delta_of(r: Mapping[str, Any]) -> float | None:
        if r.get("score") is None or r.get("thread_median") is None:
            return None
        return round(float(r["score"]) - float(r["thread_median"]), 1)

    lines = [
        "# Wyniki publikacji per ruch (D-031)",
        "",
        f"Stan na {now.isoformat(timespec='minutes')}. Punkty i odpowiedzi z ostatniego odczytu "
        "kontrolnego; komentarze młodsze niż 24 h nie mają jeszcze pełnego wyniku. "
        "`Δ` = punkty naszego komentarza minus mediana komentarzy najwyższego poziomu w wątku "
        "(odczyt bieżący, nie z chwili publikacji).",
        "",
        "## Każda publikacja",
        "",
        "| Społeczność | Publikacja | Wiek posta | Rodzaj | Proszono | Napisano | Pkt | Δ | "
        "Odp. | Wątek: mediana / najlepszy / n | Źródło | Wątek |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        published = r.get("published_at")
        lines.append(
            f"| {r.get('community_key')} | "
            f"{published.strftime('%m-%d %H:%M') if published else 'brak'} | {fmt(age_of(r))} h | "
            f"{r.get('decision')} | {r.get('requested_move') or 'brak'} | "
            f"{r.get('move') or 'brak'} | {fmt(r.get('score'))} | {fmt(delta_of(r))} | "
            f"{fmt(r.get('reply_count'))} | {fmt(r.get('thread_median'))} / "
            f"{fmt(r.get('thread_best'))} / {fmt(r.get('thread_top_level'))} | {source_of(r)} | "
            f"{r.get('thread_external_id')} |"
        )

    def aggregate(key: Callable[[Mapping[str, Any]], str], title: str) -> None:
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Grupa | n | Mediana Δ | Mediana pkt | Z odpowiedzią |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for name in sorted(groups):
            group = groups[name]
            deltas = [d for d in (delta_of(g) for g in group) if d is not None]
            scores = [float(g["score"]) for g in group if g.get("score") is not None]
            replied = [g for g in group if g.get("reply_count") is not None]
            share = (
                sum(1 for g in replied if int(g["reply_count"]) > 0) / len(replied)
                if replied
                else None
            )
            lines.append(
                f"| {name} | {len(group)} | {fmt(median(deltas))} | {fmt(median(scores))} | "
                f"{'brak' if share is None else f'{share:.0%}'} |"
            )

    def by_move(r: Mapping[str, Any]) -> str:
        return f"{r.get('community_key')} · {r.get('move') or 'brak ruchu'}"

    def by_request(r: Mapping[str, Any]) -> str:
        return f"{r.get('community_key')} · proszono {r.get('requested_move') or 'brak'}"

    def by_age(r: Mapping[str, Any]) -> str:
        return f"{r.get('community_key')} · {age_bucket(age_of(r))}"

    def by_source(r: Mapping[str, Any]) -> str:
        return f"{r.get('community_key')} · {source_of(r)}"

    aggregate(by_move, "Per społeczność i ruch (jak napisano)")
    aggregate(by_request, "Per społeczność i ruch, o który proszono (eksperyment)")
    aggregate(by_age, "Per społeczność i wiek posta przy publikacji")
    aggregate(by_source, "Per społeczność i źródło zgody")
    lines.append("")
    return "\n".join(lines)


#: What a candidate community is scanned for (D-032): the audience of the founder's current
#: project. Terms are a crude filter on titles and bodies, not a classifier; they answer one
#: question, "do people here talk about the things this account can honestly talk about".
DEFAULT_AUDIENCE_TERMS: tuple[str, ...] = (
    "agent",
    "assistant",
    "llm",
    "ai ",
    " ai",
    "chatbot",
    "copilot",
    "automation",
    "checkout",
    "cart",
    "storefront",
    "shopify",
    "woocommerce",
    "ecommerce",
    "commerce",
    "conversion",
    "customer",
    "customers",
    "client",
    "clients",
    "pricing",
    "react",
    "next.js",
    "nextjs",
    "typescript",
    "component",
    "api",
    "integration",
    "sdk",
    "saas",
    "startup",
    "founder",
    "mvp",
    "launch",
    "deploy",
    "stack",
)


def render_community_probe(rows: Sequence[Mapping[str, Any]], *, now: datetime) -> str:
    """The candidate table. Read-only evidence per community: how alive it is, how much of it is
    our audience, and what people actually posted. No judgement of fit with the voice profile:
    that is the founder's call and the judge's, not a keyword's."""

    def fmt(value: Any, digits: int = 1) -> str:
        if value is None:
            return "brak"
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    lines = [
        "# Kandydaci na kolejne społeczności",
        "",
        f"Stan na {now.isoformat(timespec='minutes')}. Wyłącznie odczyt listingu `new`, bez "
        "dołączania i bez pisania. `Nasz temat` to udział postów, w których pada choć jedno "
        "słowo z listy tematów tego projektu: filtr zgrubny, nie klasyfikator.",
        "",
        "| Społeczność | Postów w próbce | Na dobę | Mediana komentarzy | Bez odpowiedzi | "
        "Nasz temat | Uwaga |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    def order(row: Mapping[str, Any]) -> tuple[bool, float]:
        return (row.get("error") is not None, -(row.get("audience_share") or 0.0))

    for r in sorted(rows, key=order):
        if r.get("error"):
            lines.append(f"| {r['key']} | brak | brak | brak | brak | brak | {r['error'][:60]} |")
            continue
        lines.append(
            f"| {r['key']} | {r['posts']} | {fmt(r['per_day'])} | {fmt(r['median_comments'], 0)} | "
            f"{r['silent_share']:.0%} | {r['audience_share']:.0%} | {r.get('note', '')} |"
        )
    lines += ["", "## Co ludzie tam piszą", ""]
    for r in sorted(rows, key=order):
        if r.get("error"):
            continue
        lines.append(f"**{r['key']}**")
        for title in r["samples"]:
            lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


async def cmd_community_probe(args: argparse.Namespace) -> int:
    """D-032: read candidate communities and say, from data, which ones are worth entering.

    Reads only the public `new` listing through the capsule, the same path the pilot already
    uses. Nothing is joined, nothing is written, no community is enabled: the output is evidence
    for the founder's decision, and enabling one is still `make pilot-community-enable`.
    """
    pool = await _pool()
    try:
        source = build_sources(os.environ).get(ObservationPlatform.REDDIT)
        if source is None:
            print("brak adaptera REDDIT w tym procesie", file=sys.stderr)
            return 2
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            print("podaj --keys r/a,r/b", file=sys.stderr)
            return 2
        terms = [t.strip().lower() for t in (args.terms.split(",") if args.terms else [])]
        terms = terms or list(DEFAULT_AUDIENCE_TERMS)
        now = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        for key in keys:
            try:
                threads = await source.list_recent_threads(key, since=None, limit=args.limit)
            except Exception as exc:  # one unreadable candidate must not end the probe
                rows.append({"key": key, "error": f"{type(exc).__name__}: {exc}"[:200]})
                print(f"{key}: ODCZYT NIEUDANY ({type(exc).__name__})", file=sys.stderr)
                continue
            if not threads:
                rows.append({"key": key, "error": "listing pusty"})
                continue
            comments = sorted(
                t.metrics.comment_count.value for t in threads if t.metrics.comment_count.known
            )
            oldest = min(t.created_at for t in threads)
            span_days = max((now - oldest).total_seconds() / 86400, 0.01)
            matched = [
                t
                for t in threads
                if any(term in f"{t.title}\n{t.body or ''}".lower() for term in terms)
            ]
            rows.append(
                {
                    "key": key,
                    "posts": len(threads),
                    "per_day": round(len(threads) / span_days, 1),
                    "median_comments": (float(comments[len(comments) // 2]) if comments else None),
                    "silent_share": (
                        sum(1 for c in comments if c == 0) / len(comments) if comments else 0.0
                    ),
                    "audience_share": len(matched) / len(threads),
                    "samples": [t.title[:110] for t in (matched[:4] or threads[:2])],
                    "note": "",
                }
            )
            print(
                f"{key}: {len(threads)} postów, nasz temat {len(matched)}/{len(threads)}",
                file=sys.stderr,
            )
        text = render_community_probe(rows, now=now)
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_REPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"community-probe-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        path.write_text(text, encoding="utf-8")
        print(text)
        print(f"zapisano {path}", file=sys.stderr)
        return 0
    finally:
        await pool.close()


async def cmd_outcomes(args: argparse.Namespace) -> int:
    """D-031: the research table for the morning, printed and saved as an artifact."""
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        rows = await PilotStore(pool).outcome_rows(organization_id=organization_id)
        now = datetime.now(UTC)
        text = render_outcomes(rows, now=now)
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_REPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"outcomes-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        path.write_text(text, encoding="utf-8")
        print(text)
        print(f"zapisano {path}", file=sys.stderr)
        return 0
    finally:
        await pool.close()


async def cmd_report(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        pilot = PilotStore(pool)
        run = (
            await pilot.get_run(UUID(args.run_id))
            if args.run_id
            else await pilot.latest_run(organization_id=organization_id)
        )
        if run is None:
            print("brak runu pilota")
            return 1
        cycles = await pilot.cycles(run.id)
        reactions = await pilot.reactions(run.id)
        now = datetime.now(UTC)
        store = ObservationStore(pool)
        community = await store.get_community(run.community_id, organization_id=organization_id)
        contexts = {}
        for thread_id in {r.thread_id for r in reactions}:
            context = await store.thread_context(thread_id, organization_id=organization_id)
            if context is not None:
                contexts[thread_id] = context
        publication = PublicationStore(pool)
        publications = []
        for request in await publication.requests(organization_id=organization_id, run_id=run.id):
            if request.status != "PUBLISHED" or request.attempt_id is None:
                continue
            evidence = await publication.evidence_for(request.attempt_id)
            if evidence is None:
                continue
            observation = await publication.latest_observation(request.id)
            replies = await publication.replies_to(
                organization_id=organization_id,
                comment_external_id=evidence["external_id"].split("_", 1)[-1],
            )
            publications.append(
                {
                    "permalink": evidence["permalink"],
                    "published_at": request.published_at,
                    "external_id": evidence["external_id"],
                    "still_present": observation["still_present"] if observation else None,
                    "score": observation["score"] if observation else None,
                    "observed_at": observation["observed_at"] if observation else None,
                    "replies": replies,
                }
            )
        judgments = [
            {
                "reaction_id": j.reaction_id,
                "verdict": j.verdict,
                "rationale": j.rationale,
                "created_at": j.created_at,
            }
            for j in await PolicyStore(pool).judgments_for_run(run.id)
        ]
        text = render_report(
            run,
            cycles,
            reactions,
            now=now,
            contexts=contexts,
            community_name=community.display_name if community else None,
            publications=publications,
            judgments=judgments,
        )
        out_dir = Path(args.out) / str(run.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"raport-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        path.write_text(text, encoding="utf-8")
        print(str(path))
        return 0
    finally:
        await pool.close()


async def cmd_policy_sign(args: argparse.Namespace) -> int:
    """D-029: the founder signs the publication policy ONCE. From then on every publish tick judges
    the pending drafts of the named communities and queues the ones that pass; the chain posts them
    at the run's pace. Refuses to sign over an active policy (revoke first) and refuses a window
    longer than 7 days. Schedules a publish tick so the first judgment happens within a minute."""
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        store = ObservationStore(pool)
        communities = await store.list_communities(organization_id=organization_id)
        known = {f"{c.platform.value}:{c.community_key}": c for c in communities}
        wanted = [key.strip() for key in args.communities.split(",") if key.strip()]
        missing = [key for key in wanted if key not in known]
        if not wanted or missing:
            print(f"nie ma community {missing or '(pusto)'}; są: {sorted(known)}", file=sys.stderr)
            return 2
        kinds = tuple(k.strip().upper() for k in args.kinds.split(",") if k.strip())
        if not args.source.strip():
            print('polityka wymaga słów założyciela: --source "..."', file=sys.stderr)
            return 2
        max_hours = int(MAX_VALIDITY.total_seconds() // 3600)
        hours = int(args.hours)
        if not 1 <= hours <= max_hours:
            print(f"--hours poza zakresem 1..{max_hours}", file=sys.stderr)
            return 2
        policies = PolicyStore(pool)
        now = datetime.now(UTC)
        active = await policies.active(organization_id=organization_id, now=now)
        if active is not None:
            print(
                f"jest już aktywna polityka {active.id} (do {active.valid_until.isoformat()}); "
                "najpierw policy-revoke",
                file=sys.stderr,
            )
            return 2
        publication = PublicationStore(pool)
        operator = (
            UUID(args.operator_id)
            if args.operator_id
            else await publication.sole_operator_id(organization_id=organization_id)
        )
        prompt = prompts.get(JUDGE_PROMPT_NAME)
        try:
            policy = await policies.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                operator_id=operator,
                valid_from=now,
                valid_until=now + timedelta(hours=hours),
                community_ids=[known[key].id for key in wanted],
                account_handle=args.account,
                allowed_kinds=kinds,
                min_confidence=float(args.min_confidence),
                max_chars=int(args.max_chars),
                max_per_hour=int(args.max_per_hour),
                max_per_day=int(args.max_per_day),
                judge_prompt_name=prompt.name,
                judge_prompt_sha256=prompt.sha256,
                source_text=args.source.strip(),
            )
        except PolicyError as exc:
            print(f"polityka odrzucona: {exc}", file=sys.stderr)
            return 2
        # Keyed by the policy, not by the slot: the slot's tick has usually run already and a
        # slot-keyed enqueue would add nothing (D-030; measured 2026-09-13 17:42 CEST).
        transport = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
        # D-033: signing says "work in these communities". A community the policy names but which
        # has no run drafts nothing, because drafts come from a run's cycles. Start one here, at
        # the signature, rather than asking for a `pilot-start` per community with a profile file
        # from outside the repo. A community that already has an active run is left alone.
        pilot = PilotStore(pool)
        profile_row = await store.latest_profile(
            organization_id=organization_id, workspace_id=workspace_id
        )
        started: list[str] = []
        if profile_row is None:
            print(
                "brak zapisanej wersji profilu: biegi nie startują (pilot-profile-save)",
                file=sys.stderr,
            )
        else:
            reaction_prompt = prompts.get(PROMPT_NAME)
            for key in wanted:
                row = known[key]
                if await pilot.active_run_for_community(row.id) is not None:
                    continue
                days = max(1.0, (policy.valid_until - now).total_seconds() / 86400)
                run = await pilot.create_run(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    community_id=row.id,
                    starts_at=now.replace(microsecond=0),
                    ends_at=policy.valid_until,
                    poll_interval_seconds=POLICY_RUN_CYCLE_SECONDS,
                    model_calls_budget=max(1, round(days * args.calls_per_day)),
                    max_generations_per_thread=1,
                    profile_version_id=profile_row.id,
                    prompt_name=reaction_prompt.name,
                    prompt_sha256=reaction_prompt.sha256,
                    paused_job_types=PAUSED_JOB_TYPES,
                    restore={"poll_interval_seconds": row.poll_interval_seconds},
                )
                await schedule_cycle(
                    transport,
                    run,
                    slot=1,
                    delay_seconds=(planned_at(run, 1) - now).total_seconds(),
                )
                started.append(f"{key} ({run.model_calls_budget} wywołań)")
        await nudge_publish(
            transport,
            organization_id=organization_id,
            workspace_id=workspace_id,
            key=f"policy:{policy.id}",
            delay_seconds=15,
        )
        print(
            f"polityka {policy.id} ACTIVE od {policy.valid_from.isoformat()} do "
            f"{policy.valid_until.isoformat()}: {', '.join(wanted)}; rodzaje {', '.join(kinds)}; "
            f"pewność >= {policy.min_confidence:.2f}; <= {policy.max_chars} znaków; tempo "
            f"{policy.max_per_hour}/h i {policy.max_per_day}/dobę (polityka rządzi tempem, gdy "
            f"jest aktywna); sędzia {prompt.short}; konto {policy.account_handle}; "
            "pierwszy tik za 15 s"
        )
        if started:
            print(f"biegi uruchomione w {len(started)} społecznościach: {', '.join(started)}")
        else:
            print("bez nowych biegów: każda społeczność polityki ma już aktywny run")
        return 0
    finally:
        await pool.close()


async def cmd_profile_save(args: argparse.Namespace) -> int:
    """Save a new version of the founder's profile (four text fields). Nothing running changes:
    active runs keep the version they started with; a run renewed under the policy (D-029) and
    every new `pilot-start` without `--profile-json` pick the newest version from here."""
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        raw = (
            json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
            if args.profile_json != "-"
            else json.load(sys.stdin)
        )
        fields = ("knows_deeply", "positions", "would_never_claim", "voice_samples")
        if (
            not isinstance(raw, dict)
            or set(raw) != set(fields)
            or not all(isinstance(raw[f], str) and raw[f].strip() for f in fields)
        ):
            print(
                f"profil musi mieć dokładnie cztery niepuste pola tekstowe {list(fields)}",
                file=sys.stderr,
            )
            return 2
        if any(ch in raw[f] for f in fields for ch in (chr(0x2014), chr(0x2013))):
            print(
                "profil zawiera długą pauzę (U+2014/U+2013); popraw przed zapisem", file=sys.stderr
            )
            return 2
        row = await ObservationStore(pool).save_profile(
            organization_id=organization_id,
            workspace_id=workspace_id,
            knows_deeply=raw["knows_deeply"],
            positions=raw["positions"],
            would_never_claim=raw["would_never_claim"],
            voice_samples=raw["voice_samples"],
        )
        print(
            f"profil zapisany jako v{row.version} ({row.id}); użyją go runy przedłużane z polityki "
            "i kolejne starty; aktywne runy piszą starą wersją do końca okna"
        )
        return 0
    finally:
        await pool.close()


async def cmd_draft_compare(args: argparse.Namespace) -> int:
    """Regenerate the newest drafted reactions with the CURRENT prompt, the newest profile and the
    community's exemplars, and print old vs new side by side. Nothing is saved and no run budget
    is touched: one model call per reaction, paid for by the founder's curiosity."""
    pool = await _pool()
    try:
        organization_id, workspace_id = await _tenant(pool)
        store = ObservationStore(pool)
        pilot = PilotStore(pool)
        turns = build_turns(os.environ)
        if turns is None:
            print("brak modelu (WENEXT_CODEX_HOME nieustawione)", file=sys.stderr)
            return 2
        profile_row = await store.latest_profile(
            organization_id=organization_id, workspace_id=workspace_id
        )
        if profile_row is None:
            print("brak profilu w bazie (pilot-profile-save)", file=sys.stderr)
            return 2
        profile = {
            "knows_deeply": profile_row.knows_deeply,
            "positions": profile_row.positions,
            "would_never_claim": profile_row.would_never_claim,
            "voice_samples": profile_row.voice_samples,
        }
        ids = [UUID(x) for x in (args.reaction_ids or [])]
        if not ids:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT r.id FROM pilot_reactions r JOIN pilot_runs run ON run.id = r.run_id "
                    "WHERE run.organization_id = $1 AND r.draft IS NOT NULL "
                    "ORDER BY r.created_at DESC LIMIT $2",
                    organization_id,
                    int(args.limit),
                )
            ids = [r["id"] for r in rows]
        prompt = prompts.get(PROMPT_NAME)
        for reaction_id in ids:
            reaction = await pilot.get_reaction(reaction_id)
            run = await pilot.get_run(reaction.run_id) if reaction else None
            if reaction is None or run is None:
                print(f"reakcja {reaction_id}: brak w bazie", file=sys.stderr)
                continue
            community = await store.get_community(run.community_id, organization_id=organization_id)
            context = await store.thread_context(
                reaction.thread_id, organization_id=organization_id
            )
            if community is None or context is None:
                print(f"reakcja {reaction_id}: brak community albo kontekstu", file=sys.stderr)
                continue
            exemplars = await store.top_comments(
                community.id, organization_id=organization_id, limit=EXEMPLARS_PER_DRAFT
            )
            situation = dataclasses.replace(
                situation_for_judge(
                    context,
                    reaction,
                    platform=community.platform.value,
                    community_name=community.display_name,
                ),
                community_exemplars=exemplars,
            )
            print(f"\n=== {community.display_name}: {reaction.thread_title}")
            print(f"    {reaction.thread_url}")
            print(
                f"--- STARY: {reaction.prompt_name}+{reaction.prompt_sha256[:8]}, "
                f"{reaction.decision} {reaction.confidence:.2f}"
            )
            print(reaction.draft or "(bez tekstu)")
            try:
                result = await generate_reaction(
                    turns, situation=situation, profile=profile, now=datetime.now(UTC)
                )
            except (ModelUnavailable, TurnFailed, ReactionRejected) as exc:
                print(f"--- NOWY: nie powstał ({type(exc).__name__}: {str(exc)[:200]})")
                continue
            new = result.reaction
            print(
                f"--- NOWY: {prompt.short}, profil v{profile_row.version}, {new.decision.value} "
                f"{new.confidence:.2f}, ruch {new.move}, przykładów {len(exemplars)}"
            )
            if new.draft:
                print(new.draft)
            else:
                print(f"(bez tekstu: {new.decision.value}; {new.brief_rationale[:200]})")
        return 0
    finally:
        await pool.close()


async def cmd_policy_status(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        policies = PolicyStore(pool)
        now = datetime.now(UTC)
        policy = await policies.active(organization_id=organization_id, now=now)
        if policy is None:
            policy = await policies.latest(organization_id=organization_id)
        if policy is None:
            print(
                json.dumps({"policy": None, "now": now.isoformat()}, ensure_ascii=False, indent=2)
            )
            return 0
        counts = await policies.judgment_counts(policy.id)
        waiting = (
            await policies.candidate_ids(policy, now=now, limit=100)
            if policy.active_at(now)
            else ()
        )
        print(
            json.dumps(
                {
                    "policy": {
                        "id": str(policy.id),
                        "status": policy.status,
                        "active_now": policy.active_at(now),
                        "valid_from": policy.valid_from.isoformat(),
                        "valid_until": policy.valid_until.isoformat(),
                        "communities": [str(c) for c in policy.community_ids],
                        "allowed_kinds": list(policy.allowed_kinds),
                        "min_confidence": policy.min_confidence,
                        "max_chars": policy.max_chars,
                        "account": policy.account_handle,
                        "judge": f"{policy.judge_prompt_name}+{policy.judge_prompt_sha256[:12]}",
                        "source": policy.source_text,
                        "stopped_at": policy.stopped_at.isoformat() if policy.stopped_at else None,
                        "stop_reason": policy.stop_reason,
                    },
                    "judgments": counts,
                    "candidates_waiting": len(waiting),
                    "now": now.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        await pool.close()


async def cmd_policy_revoke(args: argparse.Namespace) -> int:
    pool = await _pool()
    try:
        organization_id, _ = await _tenant(pool)
        now = datetime.now(UTC)
        revoked = await PolicyStore(pool).stop_all(
            organization_id=organization_id,
            reason=f"policy-revoke {now.isoformat()}",
            at=now,
            status=POLICY_REVOKED,
        )
        print(
            f"polityk odwołanych: {revoked}; wnioski już w kolejce zostają "
            "(publish-stop zatrzymuje także je)"
        )
        return 0
    finally:
        await pool.close()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pilot 24 h wenext")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--community", default="HACKER_NEWS:ask")
    start.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    start.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    start.add_argument("--max-per-thread", type=int, default=DEFAULT_MAX_PER_THREAD)
    start.add_argument(
        "--cycle-interval",
        type=int,
        default=CYCLE_INTERVAL_SECONDS,
        help=(
            "sekundy między cyklami (domyślnie 1800); w każdym cyklu odczyt wątków community "
            "i najwyżej jedna propozycja reakcji"
        ),
    )
    start.add_argument(
        "--profile-json", default=None, help="plik JSON z czterema polami profilu albo '-' (stdin)"
    )
    start.add_argument(
        "--smoke-optional",
        action="store_true",
        help="smoke bez kandydata (NO_CANDIDATE) nie zatrzymuje runu; awaria modelu nadal tak",
    )
    for name in ("status", "stop", "report"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", default=None)
    sub.choices["report"].add_argument("--out", default=str(DEFAULT_REPORT_DIR))
    probe = sub.add_parser(
        "community-probe",
        help="oceń kandydatów na społeczności z odczytu listingu (nic nie włącza, nic nie pisze)",
    )
    probe.add_argument("--keys", required=True, help="np. r/SaaS,r/nextjs,r/ecommerce")
    probe.add_argument(
        "--limit", type=int, default=100, help="ile postów z listingu na społeczność"
    )
    probe.add_argument("--terms", default=None, help="własne słowa tematu, po przecinku")
    probe.add_argument("--out-dir", default=None, help="katalog artefaktu")
    enable = sub.add_parser("enable-community", help="włącz obserwację community (tylko odczyt)")
    enable.add_argument("platform", help="np. REDDIT")
    enable.add_argument("key", help="np. r/kubernetes")
    enable.add_argument("--poll-interval", type=int, default=CYCLE_INTERVAL_SECONDS)
    enable.add_argument(
        "--to-learn",
        action="store_true",
        help="czytamy tylko, żeby poznać społeczność: bez osądu i migawek na każdy wątek (D-032)",
    )
    approve = sub.add_parser(
        "approve-consents", help="zgody z dokumentu do approval_decisions i kolejki publikacji"
    )
    approve.add_argument("--consents", required=True, help="plik Markdown z tabelą zgód albo '-'")
    approve.add_argument("--account", required=True, help="login konta na platformie")
    approve.add_argument("--source", required=True, help="skąd zgoda (nazwa dokumentu, rozmowa)")
    approve.add_argument("--operator-id", default=None, help="domyślnie jedyny aktywny operator")
    now_ = sub.add_parser("publish-now", help="jeden tik publikacji teraz + łańcuch co 10 min")
    now_.add_argument("--no-chain", action="store_true", help="bez zlecania kolejnych tików")
    sub.add_parser("publish-status", help="kolejka publikacji z dowodami (JSON)")
    sub.add_parser("publish-stop", help="zatrzymaj kolejkę publikacji natychmiast")
    sub.add_parser("publish-reconcile", help="sama rekoncyliacja odczytem, bez publikacji")
    requeue = sub.add_parser(
        "publish-requeue", help="ponów wniosek UNKNOWN po błędzie przed wysłaniem (nowa zgoda)"
    )
    requeue.add_argument("--request", required=True, help="id wniosku z publish-status")
    requeue.add_argument("--operator-id", default=None, help="domyślnie jedyny aktywny operator")
    sign = sub.add_parser(
        "policy-sign", help="podpisz politykę publikacji na najwyżej 7 dni (D-029, sędzia szkiców)"
    )
    sign.add_argument(
        "--communities", required=True, help="np. REDDIT:r/kubernetes,REDDIT:r/b2b_sales"
    )
    sign.add_argument("--hours", type=int, default=168)
    sign.add_argument("--kinds", default=",".join(TEXT_KINDS))
    sign.add_argument("--min-confidence", type=float, default=0.8)
    sign.add_argument("--max-chars", type=int, default=700)
    sign.add_argument(
        "--max-per-hour",
        type=int,
        default=1,
        help=f"publikacje na godzinę, 1..{MAX_PER_HOUR_CEILING} (D-030)",
    )
    sign.add_argument(
        "--max-per-day",
        type=int,
        default=6,
        help=f"publikacje na dobę, do {MAX_PER_DAY_CEILING} (D-030)",
    )
    sign.add_argument("--account", required=True, help="login konta na platformie")
    sign.add_argument("--source", required=True, help="słowa założyciela i dokument decyzji")
    sign.add_argument(
        "--calls-per-day",
        type=int,
        default=POLICY_RUN_CALLS_PER_DAY,
        help="budżet wywołań modelu na dobę dla biegu startowanego podpisem (D-033)",
    )
    sign.add_argument("--operator-id", default=None, help="domyślnie jedyny aktywny operator")
    sub.add_parser("policy-status", help="aktywna polityka, werdykty sędziego, kandydaci (JSON)")
    outcomes = sub.add_parser(
        "outcomes",
        help="tabela badawcza D-031: wynik każdej publikacji per ruch, wiek posta, źródło",
    )
    outcomes.add_argument(
        "--out-dir", default=None, help="katalog artefaktu (domyślnie artifacts/pilot)"
    )
    sub.add_parser("policy-revoke", help="odwołaj aktywną politykę; kolejka publikacji zostaje")
    profile = sub.add_parser(
        "profile-save", help="zapisz nową wersję profilu założyciela (4 pola JSON, plik albo '-')"
    )
    profile.add_argument("--profile-json", required=True, help="plik JSON albo '-' (stdin)")
    compare = sub.add_parser(
        "draft-compare",
        help="ostatnie szkice na nowo bieżącym promptem i profilem: stary vs nowy, bez zapisu",
    )
    compare.add_argument("--limit", type=int, default=5)
    compare.add_argument("--reaction-id", action="append", dest="reaction_ids", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    command: dict[str, Any] = {
        "start": cmd_start,
        "outcomes": cmd_outcomes,
        "community-probe": cmd_community_probe,
        "status": cmd_status,
        "stop": cmd_stop,
        "report": cmd_report,
        "enable-community": cmd_enable_community,
        "approve-consents": cmd_approve_consents,
        "publish-now": cmd_publish_now,
        "publish-status": cmd_publish_status,
        "publish-stop": cmd_publish_stop,
        "publish-reconcile": cmd_publish_reconcile,
        "publish-requeue": cmd_publish_requeue,
        "policy-sign": cmd_policy_sign,
        "policy-status": cmd_policy_status,
        "policy-revoke": cmd_policy_revoke,
        "profile-save": cmd_profile_save,
        "draft-compare": cmd_draft_compare,
    }
    return asyncio.run(command[args.command](args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
