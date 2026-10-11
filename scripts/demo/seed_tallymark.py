#!/usr/bin/env python3
"""The recording sandbox for the launch film: a fictional product, fictional people, real tools.

    python seed_tallymark.py seed    [--dry-run]    create what is missing, then put the take at its start
    python seed_tallymark.py reset   [--dry-run]    the same: wipe the take, seed it again (between takes)
    python seed_tallymark.py post    [--watch]      stage the last beat: the queued send becomes "Posted"
    python seed_tallymark.py status                 what the sandbox holds now
    python seed_tallymark.py ids                    the ids the Codex project config needs

THE STORY IT TELLS. A founder, "Maya", has just finished `tallymark`, a CLI that prints MRR, churn
and net revenue retention from a Stripe CSV. The sandbox project of that name holds four people
who wrote about that problem (two on Reddit, two on X, all invented) and a ready reply to each, in
the state the card shows a send button for. Nothing here is real: not the handles, not the posts.

THREE PROMISES, EACH ENFORCED IN CODE AND NOT BY CARE.

  1. IT TOUCHES ONE ORGANIZATION. Every `DELETE`, `UPDATE` and `INSERT` below carries `DEMO_ORG`,
     and `_delete` and `_stage` are the only places that build one. A test reads this file and
     fails on a statement that does not, and another runs a reset next to a second organization
     and compares its rows before and after. The founder's workspace is never named.
  2. IT WRITES THROUGH THE PRODUCT'S OWN TOOLS. The opportunities and the drafts are made by
     `opportunities.mark` and `draft.create` of a `ToolRegistry` holding a Codex capability, the
     same call the card's world makes, so what the card shows is what the product produces.
     Only the expiry differs (see `DRAFT_LIFETIME`).
  3. THE LAST BEAT IS STAGED, AND SAYS SO. The sandbox organization has no dispatcher
     (`control_plane/demo.py`), so a queued send is never picked up by the real publisher and
     nothing reaches a platform. `post` plays the publisher's last step: it writes the reply to
     the fixture site (a local stand-in with a login wall, nothing else on it) and leaves the same
     rows `pilot.publisher` leaves, with `source = ACTUATOR` and `raw.staged = true`.

RUNNING IT. Against a throwaway database: any Python that can import `control_plane`
(`services/control_plane/.venv/bin/python`), with `--dsn` or DATABASE_URL. Against the live stack
the files are not in the container, so they are piped in (`make demo-seed` does exactly this):

    docker compose exec -T api python - seed < scripts/demo/seed_tallymark.py

and the script REFUSES to run there unless the container's `control_plane` already knows the
sandbox (`control_plane.demo`): with an older image the running API would give this organization a
dispatcher wired to the real capsules.

No long dashes anywhere in this file or in what it writes (the founder's rule).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------------------------

#: Spelled again here on purpose: this file runs piped into a container whose `control_plane` may
#: predate `control_plane/demo.py`. `tests/test_demo_seed_unit.py` compares the two.
DEMO_ORG = UUID("dddddddd-0000-4000-8000-000000000001")
DEMO_WORKSPACE = UUID("dddddddd-0000-4000-8000-000000000002")
DEMO_PROJECT = UUID("dddddddd-0000-4000-8000-000000000003")
DEMO_REDDIT_ACCOUNT = UUID("dddddddd-0000-4000-8000-000000000004")
DEMO_X_ACCOUNT = UUID("dddddddd-0000-4000-8000-000000000005")

ORG_NAME = "Wenext demo (tallymark)"
WORKSPACE_NAME = "tallymark workspace"
PROJECT_NAME = "tallymark"
PROJECT_GOAL = (
    "Find the first users for tallymark, a small CLI that prints MRR, churn and net revenue "
    "retention from a Stripe CSV export. Its author is a solo founder who wants a few honest "
    "conversations with people who compute these numbers by hand."
)

#: Fictional accounts the founder "posts as". They exist only in this sandbox.
REDDIT_HANDLE = "maya_builds_things"
X_HANDLE = "maya_builds"

#: Ids the script refuses to ever touch, spelled out so a mistake in `DEMO_ORG` cannot reach them.
FOUNDER_ORG = UUID("11111111-1111-1111-1111-111111111111")
FOUNDER_WORKSPACE = UUID("22222222-2222-2222-2222-222222222222")

#: The tool server's own draft lifetime is 72 hours; a film is shot over days, so the seed asks for
#: two weeks. Only this process changes it (`registry._consent_window`); the server is untouched.
DRAFT_LIFETIME = timedelta(days=14)

FIXTURE_URL_DEFAULT = "http://fixture-site:8000"
#: The evidence table accepts `https://` addresses only. `.test` is reserved (RFC 6761): it can
#: never resolve, so the staged permalink cannot lead anywhere real.
STAGED_PERMALINK_HOST = "https://fixture-site.test"


def _hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def conversations() -> list[dict[str, Any]]:
    """The four conversations and the four replies. Built per call so the ages are fresh."""
    return [
        {
            "mark": {
                "platform": "REDDIT",
                "url": "https://www.reddit.com/r/SaaS/comments/1o3k7qa/how_do_you_calculate_mrr_from_a_stripe_export/",
                "community": "r/SaaS",
                "title": "How do you calculate MRR from a Stripe export when you have annual plans?",
                "excerpt": (
                    "I export subscriptions from Stripe every month and sum the Amount column, but "
                    "the number is way off because a few customers pay yearly. Do you divide by 12 "
                    "in a spreadsheet? What do you do about discounts and trials? I want one number "
                    "I can trust before I show it to my cofounder."
                ),
                "author": "quiet_ledger",
                "created_at": _hours_ago(0.7),
                "score": 18,
                "num_comments": 14,
                "dlaczego": (
                    "Asks exactly what the CLI answers: annual plans, discounts and trials in a "
                    "Stripe export. Fourteen comments and nobody has given a worked answer."
                ),
                "kat": "Give the formula and the traps first. Mention tallymark once, as its author, at the end.",
            },
            "draft": (
                "Normalize every subscription to a monthly amount first, then sum. Yearly plan: "
                "price divided by 12. Billed every 3 months: price divided by 3. Do it from the "
                "subscription's price, not from the invoices, because invoices are lumpy. One "
                "annual payment in January makes January look twelve times bigger.\n\n"
                "Three things quietly break the number:\n"
                "- Coupons. Use the discounted amount that is actually billed, not the list price.\n"
                "- Trials. Leave them out until the first paid invoice.\n"
                "- Refunds and one-off charges. They are not recurring, so they stay out.\n\n"
                "In the export, take the Subscriptions list (not Payments), add one column for the "
                "monthly amount and sum it. If you are more than a couple of percent off the "
                "dashboard, it is almost always a coupon or a trial.\n\n"
                "I wrote a small CLI called tallymark that does exactly this from the CSV, so take "
                "that with a grain of salt. A spreadsheet is fine until the export gets long."
            ),
        },
        {
            "mark": {
                "platform": "REDDIT",
                "url": "https://www.reddit.com/r/indiehackers/comments/1o3m2zd/churn_customers_or_revenue_and_what_about_paused/",
                "community": "r/indiehackers",
                "title": "Churn: do you count customers or revenue, and what about paused subscriptions?",
                "excerpt": (
                    "Honest question. Our churn looks like 3% if I count customers and 7% if I "
                    "count revenue, and I have a few subscriptions paused for the summer. Which "
                    "number do you actually report, and do paused ones count as churned?"
                ),
                "author": "ramen_mrr",
                "created_at": _hours_ago(3.5),
                "score": 9,
                "num_comments": 7,
                "dlaczego": (
                    "A founder unsure which churn number to report. A plain definition settles it "
                    "and the thread has none yet."
                ),
                "kat": "Answer with the two definitions and when each one matters. No product mention.",
            },
            "draft": (
                "Report both, but call them by their names: customer churn and revenue churn.\n\n"
                "Customer churn is the customers who paid on the first day of the month and pay "
                "nothing on the last day, divided by the customers on the first day. Revenue churn "
                "is the MRR those customers took with them (plus any downgrades), divided by the "
                "MRR on the first day.\n\n"
                "A gap like 3% of customers against 7% of revenue means the customers who left "
                "were bigger than your average one. That is the number to worry about.\n\n"
                "Paused subscriptions: I would take them out of MRR while they are paused and not "
                "call them churn unless they do not come back. Whatever you pick, write it down "
                "and keep it the same every month, so the trend means something."
            ),
        },
        {
            "mark": {
                "platform": "X",
                "url": "https://x.com/pia_in_prod/status/2043817650321884161",
                "excerpt": (
                    "Spent my Sunday getting net revenue retention out of a Stripe CSV export. "
                    "Pivot tables, three tabs, one wrong number. There has to be a simpler way, right?"
                ),
                "author": "pia_in_prod",
                "created_at": _hours_ago(1.6),
                "score": 23,
                "dlaczego": (
                    "Asks for a simpler way to get NRR from a Stripe CSV. That is the one thing "
                    "the CLI does, and naming it is the answer."
                ),
                "kat": "Say plainly that you are building a tool for exactly this, and offer to help.",
            },
            "draft": (
                "Been fighting exactly this. I'm building tallymark: point it at your Stripe CSV "
                "and it prints MRR, churn and NRR in one command, annual plans and coupons "
                "handled. No pivot tables. Happy to run it on your export if you want to try."
            ),
        },
        {
            "mark": {
                "platform": "X",
                "url": "https://x.com/mo_runs_numbers/status/2043902218870432769",
                "excerpt": (
                    "Stripe says my MRR is 4,820. My sheet says 5,310. Same export. Annual plans? "
                    "Trials? Tax? Where does the difference come from?"
                ),
                "author": "mo_runs_numbers",
                "created_at": _hours_ago(5.2),
                "score": 6,
                "dlaczego": (
                    "Two MRR numbers that disagree on the same export, and the likely causes are "
                    "listed in the question itself."
                ),
                "kat": "Answer which of the three is usually it. No product mention.",
            },
            "draft": (
                "Usually annual plans, coupons or trials. Sum each subscription's monthly amount "
                "(yearly price / 12, minus the discount) and leave trials out until the first paid "
                "invoice. Tax is the other usual suspect: use amounts before tax. Does the gap "
                "close?"
            ),
        },
    ]


# ---------------------------------------------------------------------------------------------
# What a reset removes. One list, children first, every row filtered by DEMO_ORG.
# ---------------------------------------------------------------------------------------------

#: Tables a take writes into. Not listed on purpose: `audit_events` and `session_events` (append
#: only, by trigger), and the organization's skeleton (organization, workspace, project, accounts,
#: operator, sessions), which stays and is reused.
TAKE_TABLES: tuple[str, ...] = (
    "publication_observations",
    "publication_requests",
    "execution_evidence",
    "execution_attempts",
    "execution_intents",
    "project_opportunities",
    "approval_decisions",
    "action_proposal_revisions",
    "action_proposals",
    "project_delegations",
    "job_attempts",
    "job_leases",
    "jobs",
)


class Refusal(Exception):
    """Something the person must fix. Printed, exit code 2."""


def _say(message: str) -> None:
    print(message, flush=True)


def _guard(organization: UUID) -> None:
    """Every write goes through here first: the one organization it may name is the sandbox."""
    if organization != DEMO_ORG or DEMO_ORG == FOUNDER_ORG:
        raise Refusal(f"refusing to touch organization {organization}: not the demo organization")


async def _delete(conn: Any, table: str, organization: UUID = DEMO_ORG) -> int:
    """The ONLY place a DELETE is built: a table of `TAKE_TABLES`, rows of `DEMO_ORG`."""
    _guard(organization)
    if table not in TAKE_TABLES:
        raise Refusal(f"{table} is not a table a reset may empty")
    status = await conn.execute(f"DELETE FROM {table} WHERE organization_id = $1", organization)
    return int(status.rsplit(" ", 1)[-1])


async def _count(conn: Any, table: str, organization: UUID) -> int:
    return int(
        await conn.fetchval(f"SELECT count(*) FROM {table} WHERE organization_id = $1", organization)
    )


async def _counts(conn: Any, organization: UUID) -> dict[str, int]:
    return {table: await _count(conn, table, organization) for table in TAKE_TABLES}


#: Tables the live stack writes to every few seconds, so a before/after comparison of the
#: founder's organization would blame this script for the stack's own work.
CHURNING = frozenset({"jobs", "job_attempts", "job_leases", "publication_observations"})


async def _founder_counts(conn: Any) -> dict[str, int]:
    """Read only: the founder's rows in the tables a take uses, minus the ones that churn."""
    return {
        table: await _count(conn, table, FOUNDER_ORG) for table in TAKE_TABLES if table not in CHURNING
    }


# ---------------------------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------------------------


def _dsn(argument: str | None) -> str:
    raw = (argument or os.environ.get("DATABASE_URL") or os.environ.get("WENEXT_TEST_DSN") or "").strip()
    if not raw:
        raise Refusal("no database: pass --dsn, or run where DATABASE_URL is set (the api container)")
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _pool(dsn: str):
    import asyncpg

    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


def _check_the_running_code_knows_the_sandbox(allow_old: bool) -> None:
    """Refuse when this process's `control_plane` predates the sandbox exclusion.

    Inside the live `api` container this is the image the stack runs. If it cannot import
    `control_plane.demo`, its dispatcher does not leave the sandbox organization out, and the
    organization would get a dispatcher wired to the real capsules the moment it exists.
    """
    try:
        from control_plane import demo
    except ImportError:
        if allow_old:
            _say("WARNING: control_plane.demo is missing; continuing because of --allow-old-image")
            return
        raise Refusal(
            "this control_plane has no control_plane.demo: the running api would start a "
            "dispatcher for the demo organization, wired to the real capsules. Rebuild the api "
            "image first (make build, then restart api), then run this again."
        ) from None
    if demo.DEMO_ORGANIZATION_ID != DEMO_ORG:
        raise Refusal("control_plane.demo names another sandbox organization than this script")


# ---------------------------------------------------------------------------------------------
# Skeleton: organization, workspace, project, accounts. Idempotent, every row has a fixed id.
# ---------------------------------------------------------------------------------------------


async def ensure_skeleton(pool: Any) -> list[str]:
    from control_plane.auth import create_operator

    made: list[str] = []
    async with pool.acquire() as conn, conn.transaction():
        _guard(DEMO_ORG)
        existing = await conn.fetchrow("SELECT name FROM organizations WHERE id = $1", DEMO_ORG)
        if existing is not None and existing["name"] != ORG_NAME:
            raise Refusal(f"organization {DEMO_ORG} exists under another name: {existing['name']!r}")
        if existing is None:
            await conn.execute(
                "INSERT INTO organizations (id, name) VALUES ($1, $2)", DEMO_ORG, ORG_NAME
            )
            made.append("organization")
        if await conn.fetchval("SELECT 1 FROM workspaces WHERE id = $1", DEMO_WORKSPACE) is None:
            await conn.execute(
                "INSERT INTO workspaces (id, organization_id, name) VALUES ($1, $2, $3)",
                DEMO_WORKSPACE,
                DEMO_ORG,
                WORKSPACE_NAME,
            )
            made.append("workspace")
        if await conn.fetchval("SELECT 1 FROM projects WHERE id = $1", DEMO_PROJECT) is None:
            await conn.execute(
                "INSERT INTO projects (id, organization_id, workspace_id, name, goal, mode) "
                "VALUES ($1, $2, $3, $4, $5, 'REVIEW')",
                DEMO_PROJECT,
                DEMO_ORG,
                DEMO_WORKSPACE,
                PROJECT_NAME,
                PROJECT_GOAL,
            )
            made.append("project")
        for account_id, platform, handle in (
            (DEMO_REDDIT_ACCOUNT, "REDDIT", REDDIT_HANDLE),
            (DEMO_X_ACCOUNT, "X", X_HANDLE),
        ):
            if await conn.fetchval("SELECT 1 FROM social_accounts WHERE id = $1", account_id) is None:
                await conn.execute(
                    "INSERT INTO social_accounts (id, organization_id, platform, "
                    "external_account_id, state) VALUES ($1, $2, $3, $4, 'READY')",
                    account_id,
                    DEMO_ORG,
                    platform,
                    handle,
                )
                made.append(f"account {platform}")
            if (
                await conn.fetchval(
                    "SELECT 1 FROM project_account_assignments WHERE project_id = $1 "
                    "AND social_account_id = $2 AND unassigned_at IS NULL",
                    DEMO_PROJECT,
                    account_id,
                )
                is None
            ):
                await conn.execute(
                    "INSERT INTO project_account_assignments (organization_id, workspace_id, "
                    "project_id, social_account_id) VALUES ($1, $2, $3, $4)",
                    DEMO_ORG,
                    DEMO_WORKSPACE,
                    DEMO_PROJECT,
                    account_id,
                )
                made.append(f"assignment {platform}")
        if (
            await conn.fetchval(
                "SELECT 1 FROM operators WHERE organization_id = $1 AND disabled_at IS NULL",
                DEMO_ORG,
            )
            is None
        ):
            # The card's first click signs a scope "filed under the organization's operator", so
            # one has to exist. Its password is random and thrown away: nobody logs in as it.
            await create_operator(
                conn, organization_id=DEMO_ORG, password=hashlib.sha256(os.urandom(32)).hexdigest()
            )
            made.append("operator")
    return made


# ---------------------------------------------------------------------------------------------
# The take: wipe it, then fill it through the tools
# ---------------------------------------------------------------------------------------------


async def wipe_take(pool: Any) -> dict[str, int]:
    """Empty `TAKE_TABLES` for the demo organization, in one transaction. Returns rows removed."""
    removed: dict[str, int] = {}
    async with pool.acquire() as conn, conn.transaction():
        for table in TAKE_TABLES:
            removed[table] = await _delete(conn, table)
    return removed


async def fill_take(pool: Any) -> list[dict[str, Any]]:
    """Mark the four conversations and write the four drafts, with the product's own tools."""
    import control_plane.tools.registry as registry_module
    from control_plane.sessions.store import SessionStore
    from control_plane.tools import ToolRegistry, issue

    # Only this process: the server keeps its 72 hours (see DRAFT_LIFETIME).
    registry_module._consent_window = lambda: DRAFT_LIFETIME

    session, _created = await SessionStore(pool).open_host_session(
        organization_id=DEMO_ORG,
        workspace_id=DEMO_WORKSPACE,
        project_id=DEMO_PROJECT,
        host="codex",
        goal="Wtyczka: praca w Twoim agencie",
    )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session.id,
            organization_id=DEMO_ORG,
            workspace_id=DEMO_WORKSPACE,
            project_id=DEMO_PROJECT,
            dsn="postgresql://unused/never-connected",
            host="codex",
        ),
    )
    made: list[dict[str, Any]] = []
    # The room lists drafts newest first, so the one the film opens on (the freshest
    # conversation, first in `conversations()`) has to be written LAST.
    for item in reversed(conversations()):
        marked = await registry.call("opportunities.mark", item["mark"])
        if marked.get("stan") != "WYKONANE":
            raise Refusal(f"opportunities.mark refused: {marked}")
        handle = REDDIT_HANDLE if item["mark"]["platform"] == "REDDIT" else X_HANDLE
        args: dict[str, Any] = {
            "kind": "REPLY",
            "target_url": item["mark"]["url"],
            "text": item["draft"],
            "account": handle,
        }
        if item["mark"]["platform"] == "REDDIT":
            args["target_external_id"] = "t3_" + item["mark"]["url"].split("/comments/")[1].split("/")[0]
        drafted = await registry.call("draft.create", args)
        if drafted.get("stan") != "OCZEKUJE":
            raise Refusal(f"draft.create refused: {drafted}")
        made.append(
            {
                "platform": item["mark"]["platform"],
                "url": item["mark"]["url"],
                "revision_id": drafted["propozycja"]["revision_id"],
            }
        )
    return made


async def check_dashes(pool: Any) -> None:
    """The founder's rule, checked on what is stored and not on what this file says."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload::text AS text FROM action_proposal_revisions WHERE organization_id = $1 "
            "UNION ALL SELECT to_jsonb(o)::text FROM project_opportunities o "
            "WHERE o.organization_id = $1",
            DEMO_ORG,
        )
    for row in rows:
        # `\u2014` and `\u2013` as JSON escapes too: jsonb text keeps non-ASCII as it is, but a
        # driver or a future change might not.
        text = row["text"]
        if "\u2014" in text or "\u2013" in text or "\\u2014" in text or "\\u2013" in text:
            raise Refusal("a long dash got into a draft or an opportunity; fix conversations()")


async def seed(pool: Any, *, dry_run: bool, allow_old: bool) -> int:
    if allow_old and not dry_run:
        # The one thing the flag must never unlock: writing the sandbox into a stack whose running
        # image would give it a dispatcher wired to the real capsules.
        raise Refusal("--allow-old-image is for --dry-run only (a read of the live stack)")
    _check_the_running_code_knows_the_sandbox(allow_old)
    async with pool.acquire() as conn:
        present = await conn.fetchval("SELECT 1 FROM organizations WHERE id = $1", DEMO_ORG)
        before = await _counts(conn, DEMO_ORG) if present else {t: 0 for t in TAKE_TABLES}
        founder_before = await _founder_counts(conn)
    _say(f"demo organization: {'present' if present else 'missing (would be created)'}")
    _say("rows the take holds now (would be removed): " + json.dumps(before))
    _say("the founder's organization, same tables (never touched): " + json.dumps(founder_before))
    if not dry_run:
        made = await ensure_skeleton(pool)
        if made:
            _say("created: " + ", ".join(made))
        removed = await wipe_take(pool)
        _say("removed: " + json.dumps({k: v for k, v in removed.items() if v}))
        drafts = await fill_take(pool)
        await check_dashes(pool)
    else:
        _say(f"would then mark {len(conversations())} conversations and write {len(conversations())} drafts")
    async with pool.acquire() as conn:
        founder_after = await _founder_counts(conn)
    if founder_after == founder_before:
        _say("founder organization rows before and after: identical")
    else:
        _say(
            "NOTE: the founder organization's rows changed while this ran (live activity of the "
            f"stack, not this script, which names only the demo organization): "
            f"{founder_before} -> {founder_after}"
        )
    if dry_run:
        _say("dry run: nothing written")
        return 0
    _say(f"seeded {len(drafts)} opportunities with a ready draft each:")
    for draft in reversed(drafts):
        _say(f"  {draft['platform']:6} {draft['url']}")
    return 0


# ---------------------------------------------------------------------------------------------
# The staged last beat
# ---------------------------------------------------------------------------------------------


class Fixture:
    """A write to the local fixture site: login, second factor, one comment, read back."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, path: str, data: dict[str, str] | None = None) -> bytes:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        with self.opener.open(self.base + path, body, timeout=8) as response:
            return response.read()

    def comment(self, text: str) -> str:
        """Write `text` under the first thread and return the page path. Raises OSError on any trouble."""
        threads = json.loads(self._open("/api/public/threads?archetype=THREADED_COMMUNITY"))["threads"]
        thread = threads[0]
        user = os.environ.get("WENEXT_FIXTURE_USERNAME", "fixture-user")
        password = os.environ.get("WENEXT_FIXTURE_PASSWORD", "fixture-pass")
        self._open("/login", {"username": user, "password": password, "next": thread["url"]})
        pending = next((c.value for c in self.jar if c.name == "wenext_fixture_pending"), None)
        codes = json.loads(self._open("/debug/mfa"))["pending"]
        code = next((p["code"] for p in codes if p["token"] == pending), None)
        if code is None:
            raise OSError("the fixture site did not offer a second factor")
        self._open("/mfa", {"code": code, "next": thread["url"]})
        self._open(
            f"{thread['url']}/comments", {"body": text, "parent_id": ""}
        )
        writes = json.loads(self._open("/debug/writes"))["writes"]
        if not writes or writes[-1]["payload"].get("body") != text[:2000]:
            raise OSError("the fixture site did not record the comment")
        return thread["url"]


def _base36(seed: bytes, length: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    number = int.from_bytes(hashlib.sha256(seed).digest(), "big")
    out = ""
    while len(out) < length:
        number, remainder = divmod(number, 36)
        out += digits[remainder]
    return out


async def stage_published(conn: Any, request: Any, *, fixture_path: str, fixture_written: bool) -> None:
    """Leave the rows the publisher leaves for a published reply, for ONE request of the sandbox.

    The same chain `test_opportunities_db._mark_published` writes, in one transaction: a consumed
    intent, a confirmed attempt, the evidence, then the request itself.
    """
    _guard(request["organization_id"])
    revision = await conn.fetchrow(
        "SELECT r.payload_hash, p.social_account_id, a.external_account_id AS handle, a.platform "
        "FROM action_proposal_revisions r JOIN action_proposals p ON p.id = r.proposal_id "
        "JOIN social_accounts a ON a.id = p.social_account_id AND a.organization_id = p.organization_id "
        "WHERE r.id = $1 AND r.organization_id = $2",
        request["proposal_revision_id"],
        DEMO_ORG,
    )
    if revision is None:
        raise Refusal(f"request {request['id']} has no revision in the demo organization")
    on_x = str(revision["platform"]) == "X"
    seed = str(request["id"]).encode()
    external_id = (
        "x:" + str(int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % 10**18 + 10**18)
        if on_x
        else "t1_" + _base36(seed, 7)
    )
    permalink = f"{STAGED_PERMALINK_HOST}{fixture_path}?posted=1"
    intent_id = await conn.fetchval(
        """
        INSERT INTO execution_intents
            (organization_id, workspace_id, proposal_revision_id, approval_decision_id,
             one_time_grant_hash, expires_at, payload_hash, account_id, status, consumed_at)
        VALUES ($1, $2, $3, $4, $5, now() + interval '1 hour', $6, $7, 'CONSUMED', now())
        RETURNING id
        """,
        DEMO_ORG,
        request["workspace_id"],
        request["proposal_revision_id"],
        request["approval_decision_id"],
        hashlib.sha256(os.urandom(16)).hexdigest(),
        revision["payload_hash"],
        revision["social_account_id"],
    )
    attempt_id = await conn.fetchval(
        """
        INSERT INTO execution_attempts
            (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
             attempt_no, status, adapter_version, finished_at)
        VALUES ($1, $2, $3, $4, $5, 1, 'CONFIRMED', 'demo-staged/1', now())
        RETURNING id
        """,
        DEMO_ORG,
        request["workspace_id"],
        intent_id,
        request["proposal_revision_id"],
        revision["payload_hash"],
    )
    await conn.execute(
        "INSERT INTO execution_evidence (organization_id, workspace_id, attempt_id, external_id, "
        "permalink, account, observed_at, source, raw) VALUES ($1, $2, $3, $4, $5, $6, now(), "
        "'ACTUATOR', $7::jsonb)",
        DEMO_ORG,
        request["workspace_id"],
        attempt_id,
        external_id,
        permalink,
        revision["handle"],
        json.dumps(
            {
                "demo": True,
                "staged": True,
                "fixture_path": fixture_path,
                "fixture_written": fixture_written,
                "note": "staged by scripts/demo/seed_tallymark.py post; no platform was contacted",
            }
        ),
    )
    await conn.execute(
        "UPDATE publication_requests SET status = 'PUBLISHED', intent_id = $2, attempt_id = $3, "
        "published_at = now(), detail = $4 WHERE id = $1 AND organization_id = $5",
        request["id"],
        intent_id,
        attempt_id,
        "staged for the demo: written to the fixture site, no platform contacted",
        DEMO_ORG,
    )


#: What the stage may take over. PUBLISHING is left alone: something else is writing it.
STAGEABLE = ("QUEUED", "BLOCKED", "STOPPED")


async def post_once(pool: Any, *, fixture_url: str, older_than: float) -> int:
    """Stage every send that has waited long enough. Returns how many were staged."""
    async with pool.acquire() as conn:
        requests = await conn.fetch(
            "SELECT id, organization_id, workspace_id, proposal_revision_id, approval_decision_id, "
            "target_url FROM publication_requests WHERE organization_id = $1 AND status = ANY($2) "
            "AND queued_at <= now() - make_interval(secs => $3) ORDER BY queued_at",
            DEMO_ORG,
            list(STAGEABLE),
            older_than,
        )
    staged = 0
    for request in requests:
        async with pool.acquire() as conn:
            text = await conn.fetchval(
                "SELECT payload ->> 'text_and_media' FROM action_proposal_revisions "
                "WHERE id = $1 AND organization_id = $2",
                request["proposal_revision_id"],
                DEMO_ORG,
            )
        path, written = "/c/demo/t/staged", False
        try:
            path = await asyncio.to_thread(Fixture(fixture_url).comment, text or "")
            written = True
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            _say(f"WARNING: the fixture site did not take the write ({exc}); staging without it")
        async with pool.acquire() as conn, conn.transaction():
            await stage_published(conn, request, fixture_path=path, fixture_written=written)
        _say(f"staged request {request['id']} as PUBLISHED ({'written to the fixture site' if written else 'fixture write skipped'})")
        staged += 1
    return staged


async def post(pool: Any, *, watch: bool, delay: float, fixture_url: str, minutes: float) -> int:
    _check_the_running_code_knows_the_sandbox(allow_old=False)
    if not watch:
        staged = await post_once(pool, fixture_url=fixture_url, older_than=0)
        if not staged:
            _say("nothing queued in the demo organization")
        return 0
    # Piped into a container by `docker compose exec -T`, this process does not get the Ctrl-C of
    # the terminal that started it. It stops on its own instead of watching for ever.
    _say(
        f"watching the demo organization for {minutes:g} minutes: a send that has waited "
        f"{delay:g}s becomes Posted. Ctrl-C stops it, or it stops by itself."
    )
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        await post_once(pool, fixture_url=fixture_url, older_than=delay)
        await asyncio.sleep(1.0)
    _say("watch over; run it again for another take")
    return 0


# ---------------------------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------------------------


async def status(pool: Any) -> int:
    async with pool.acquire() as conn:
        org = await conn.fetchrow("SELECT id, name FROM organizations WHERE id = $1", DEMO_ORG)
        if org is None:
            _say("the demo organization does not exist yet: run seed")
            return 0
        _say(f"organization {org['id']} ({org['name']})")
        for row in await conn.fetch(
            "SELECT platform, url, dismissed_at FROM project_opportunities "
            "WHERE organization_id = $1 ORDER BY created_at",
            DEMO_ORG,
        ):
            skipped = " (skipped)" if row["dismissed_at"] else ""
            _say(f"  opportunity {row['platform']:6} {row['url']}{skipped}")
        for row in await conn.fetch(
            "SELECT status, public_visibility, target_url, detail FROM publication_requests "
            "WHERE organization_id = $1 ORDER BY queued_at",
            DEMO_ORG,
        ):
            _say(f"  request {row['status']:10} {row['target_url']}")
        _say("rows in the take tables: " + json.dumps(await _counts(conn, DEMO_ORG)))
    return 0


def ids() -> int:
    print(
        json.dumps(
            {
                "organization": str(DEMO_ORG),
                "workspace": str(DEMO_WORKSPACE),
                "project": str(DEMO_PROJECT),
                "project_name": PROJECT_NAME,
                "reddit_account": REDDIT_HANDLE,
                "x_account": X_HANDLE,
            },
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dsn", default=None, help="default: DATABASE_URL")
    parser.add_argument(
        "--allow-old-image",
        action="store_true",
        help="with seed --dry-run only: read a live stack whose image predates control_plane.demo",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "reset"):
        sub = commands.add_parser(name, help="wipe the take and seed it again")
        sub.add_argument("--dry-run", action="store_true", help="read only: say what would happen")
    posting = commands.add_parser("post", help="stage the queued send as Posted")
    posting.add_argument("--watch", action="store_true", help="keep going until Ctrl-C")
    posting.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="seconds a send waits before it is staged (watch); under the card's 2.5 s first look",
    )
    posting.add_argument("--minutes", type=float, default=45.0, help="how long --watch runs")
    posting.add_argument("--fixture", default=os.environ.get("WENEXT_FIXTURE_SITE_URL", FIXTURE_URL_DEFAULT))
    commands.add_parser("status", help="what the sandbox holds")
    commands.add_parser("ids", help="the ids for the Codex project config")
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    pool = await _pool(_dsn(arguments.dsn))
    try:
        if arguments.command in ("seed", "reset"):
            return await seed(pool, dry_run=arguments.dry_run, allow_old=arguments.allow_old_image)
        if arguments.command == "post":
            return await post(
                pool,
                watch=arguments.watch,
                delay=arguments.delay,
                fixture_url=arguments.fixture,
                minutes=arguments.minutes,
            )
        if arguments.command == "status":
            return await status(pool)
        raise Refusal(f"unknown command {arguments.command}")
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "ids":
            return ids()
        return asyncio.run(_run(arguments))
    except Refusal as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
