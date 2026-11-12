"""Seed the learning loop from what already exists and take the snapshots that are due (13.10).

WHY A SCRIPT. The loop (`control_plane/learning`) runs inside the hourly experience sweep once
the migration 0040 is applied and the `api` container restarted. This is what you run RIGHT AFTER,
so the first lessons do not wait for the founder's history to be re-measured: it registers
every published item already in the database (replies, own posts, hand-posted replies with a
link), fills their 1, 6 and 24 hour snapshots from the observations the publisher took while they
were young (about 7,000 on file), reads what is due now from the public mirror, imports the
founder's post log as the seed, and writes the lessons that follow.

    DATABASE_URL=postgresql://... python tools/dev/backfill_learning.py \\
        --organization <uuid> --project <uuid> \\
        --diary /path/to/DZIENNIK-POSTOW.md --seed-rules

    ... --dry-run     list what would be registered, imported and labelled; read and write nothing
    ... --no-network  do everything except the live reads of the public mirror
    ... --passes N    sweeps to run (each makes at most 4 live reads; default 3, enough for today's)

WHAT IT DOES NOT DO. It does not use the capsule (the host cannot reach it), so the live reads are
the public mirror's (`api.fxtwitter.com`, one GET per read, two seconds apart), which shows the same
four counters. It does not touch a publication, an approval or a signature; it writes only the
learning tables and `knowledge_items` of kind LESSON. Running it twice changes nothing the second
time (every write is keyed by the item or the horizon).

`--seed-rules` stores the founder's rule "humor replies never pitch" as a LESSON marked as a RULE
(not a measurement). It is kept only while it stands: if the founder retracts it, run without the
flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "control_plane"))

from control_plane.knowledge.store import KnowledgeStore, NewItem, Scope
from control_plane.learning import diary
from control_plane.learning.service import LearningService, SweepResult
from control_plane.learning.sources import LearningSources
from control_plane.learning.store import LearningStore
from control_plane.learning.voice import SEEDED_RULES


class OfflineSources(LearningSources):
    """No live reads at all: the database-only steps still run."""

    async def read_x_item(self, permalink, *, post_id):
        return None

    async def read_x_target(self, target_url, *, target_id):
        return None


async def _organization(pool: asyncpg.Pool, given: UUID | None) -> UUID:
    if given is not None:
        return given
    rows = await pool.fetch("SELECT id FROM organizations LIMIT 2")
    if len(rows) != 1:
        raise SystemExit("more than one organization: pass --organization")
    return rows[0]["id"]


async def _seed_rules(
    pool: asyncpg.Pool, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> int:
    knowledge = KnowledgeStore(pool)
    scope = Scope(organization_id=organization_id, workspace_id=workspace_id, project_id=project_id)
    written = 0
    for rule in SEEDED_RULES:
        item = NewItem(
            kind="LESSON",
            statement=rule.statement,
            stated_by="AGENT",
            project_id=project_id,
            payload={
                "lesson_key": rule.key,
                "rule": True,
                "measured": False,
                "source": rule.source,
                "seeded_by": "tools/dev/backfill_learning.py",
            },
        )
        before = await knowledge.list(scope, kind="LESSON", limit=200)
        if any(one.payload.get("lesson_key") == rule.key for one in before):
            continue
        await knowledge.write(item, scope, measured=True)
        written += 1
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--organization", type=UUID)
    parser.add_argument("--project", type=UUID, help="the project the diary and the rule belong to")
    parser.add_argument("--diary", type=Path, help="DZIENNIK-POSTOW.md")
    parser.add_argument("--seed-rules", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--passes", type=int, default=3)
    args = parser.parse_args()
    if not args.dsn:
        print("no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=3)
    try:
        organization_id = await _organization(pool, args.organization)
        workspace_id: UUID | None = None
        if args.project is not None:
            workspace_id = await pool.fetchval(
                "SELECT workspace_id FROM projects WHERE organization_id = $1 AND id = $2",
                organization_id,
                args.project,
            )
            if workspace_id is None:
                print("no such project in this organization", file=sys.stderr)
                return 2
        if (args.diary or args.seed_rules) and workspace_id is None:
            print("--diary and --seed-rules need --project", file=sys.stderr)
            return 2
        store = LearningStore(pool)
        now = datetime.now(UTC)
        rows = diary.parse_diary(args.diary.read_text("utf-8")) if args.diary else []

        if args.dry_run:
            requests = await store.untracked_requests(organization_id, limit=500)
            manual = await store.untracked_manual(organization_id, limit=500)
            print(
                f"{len(requests)} published request(s) and {len(manual)} hand-posted "
                "repl(ies) would be registered"
            )
            if rows:
                report = await diary.apply_diary(
                    store, rows, organization_id=organization_id,
                    workspace_id=workspace_id, project_id=args.project, now=now, dry_run=True,
                )  # fmt: skip
                print(json.dumps(diary.summary(report), indent=2, ensure_ascii=False))
            print("dry run: nothing was written")
            return 0

        sources = OfflineSources() if args.no_network else LearningSources()
        service = LearningService(pool=pool, organization_id=organization_id, sources=sources)
        total = SweepResult()
        for number in range(max(1, args.passes)):
            result = await service.sweep(datetime.now(UTC))
            print(
                f"pass {number + 1}: registered {result.registered}, from observations "
                f"{result.harvested}, read {result.read} (failed {result.read_failed}, gone "
                f"{result.gone}), followers {result.followers}, lessons +{result.lessons_written} "
                f"~{result.lessons_corrected} -{result.lessons_retracted}"
            )
            for field_name in ("registered", "harvested", "read", "followers"):
                setattr(total, field_name, getattr(total, field_name) + getattr(result, field_name))
        if rows:
            report = await diary.apply_diary(
                store, rows, organization_id=organization_id, workspace_id=workspace_id,
                project_id=args.project, now=datetime.now(UTC),
            )  # fmt: skip
            print("diary:", json.dumps(diary.summary(report), indent=2, ensure_ascii=False))
        if args.seed_rules:
            written = await _seed_rules(pool, organization_id, workspace_id, args.project)
            print(f"seeded rules written: {written}")
        final = SweepResult()
        await service.update_lessons(datetime.now(UTC), final)
        print(
            f"lessons after the seed: +{final.lessons_written} ~{final.lessons_corrected} "
            f"-{final.lessons_retracted}"
        )
        coverage = await store.coverage(organization_id)
        print("coverage:", json.dumps(coverage.to_json(), ensure_ascii=False))
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
