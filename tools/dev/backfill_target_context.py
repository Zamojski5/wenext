"""Give the drafts that wait for approval the post they answer (v11, 13.10).

WHY A SCRIPT AND NOT A TICK. `draft.create` keeps the post a reply answers from now on
(`opportunities/target_context.py`), but the drafts made BEFORE that change wait in the card with
only "Reply on X". This reads each of them once and writes the post into the conversation's
opportunity, so the card shows it. Run it once after the deploy; running it again is harmless
(only the drafts whose post was not read are asked again).

    DATABASE_URL=postgresql://... python tools/dev/backfill_target_context.py --organization <uuid>
    ... --dry-run          list what would be read, read and write nothing

SOURCES. An X post comes from the public mirror (`api.fxtwitter.com`, one GET per post, paced);
a Reddit post from `observe.reddit_search.read_reddit_thread` when this process has it. A post
nobody could read stays as it was: the draft is not touched, not refused, not expired. Nothing
here approves, signs, publishes or widens a scope, and it writes only `project_opportunities`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "control_plane"))

from control_plane.opportunities import target_context
from control_plane.tools.opportunity_tools import _reddit_search_module


async def _reddit_reader(url: str):
    module = _reddit_search_module()
    if module is None:
        return None
    return await module.read_reddit_thread(url, max_comments=1)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization", type=UUID, help="the organization (default: the only one)"
    )
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between reads")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dsn:
        print("no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=2)
    try:
        organization = args.organization
        if organization is None:
            rows = await pool.fetch("SELECT id FROM organizations LIMIT 2")
            if len(rows) != 1:
                print("more than one organization: pass --organization", file=sys.stderr)
                return 2
            organization = rows[0]["id"]
        if args.dry_run:
            async with pool.acquire() as conn:
                waiting = await conn.fetch(target_context._MISSING_SQL, organization, args.limit)
            for row in waiting:
                print(f"would read {row['target_url']}  (draft {row['revision_id']})")
            print(f"{len(waiting)} draft(s) without the post they answer")
            return 0
        done = await target_context.backfill_waiting_drafts(
            pool,
            organization_id=organization,
            readers=target_context.Readers(reddit_thread=_reddit_reader),
            limit=args.limit,
            pause=args.pause,
        )
        read = [one for one in done if one.source is not None]
        for one in done:
            print(f"{one.revision_id}  {one.target_external_id:<24}  {one.source or 'not read'}")
        print(f"{len(read)} of {len(done)} read")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
