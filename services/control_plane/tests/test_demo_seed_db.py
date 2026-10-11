"""The recording sandbox against PostgreSQL: the whole take, and nobody else's rows.

WHAT THIS PROVES, IN THE ORDER THE FILM USES IT:

  * `seed` builds the organization, the project, two fictional accounts and four conversations with
    a ready reply each, through the product's own tools, in the state the card shows a send button
    for, with an expiry that outlives a week of takes;
  * the card, as a plugin session of the sandbox, shows them, and a founder's two clicks
    ("Send", "Yes, send") travel the REAL path (`room.send_token`, `room.send_draft`,
    `approve_and_queue`) and end in the publisher's queue;
  * nothing picks that request up: the sandbox has no dispatcher, so it is still QUEUED until `post`
    stages the last beat, which writes to a (stub) fixture site and leaves the rows `room.send_status`
    reads as PUBLISHED, with an address that cannot resolve;
  * `reset` brings the take back to its start, for the next take, and a second send works;
  * all of it leaves another organization's rows exactly as they were. That is the test that the
    script cannot be run against the founder's data by accident: a bystander organization has a
    sent draft, a signature, a queued request and a job, and every table the reset empties is
    compared before and after.

The fixture site is a stub HTTP server here. The real one was driven by the same client on 11.10
(`status` of the report); a test cannot import it, its package is not in this environment.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from control_plane.runtime import _organizations
from control_plane.sessions.store import SessionStore
from control_plane.tools import ToolRegistry, issue
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_send_db import _drafted, _token
from tests.test_opportunities_db import _world

pytestmark = pytest.mark.db

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "demo" / "seed_tallymark.py"


@pytest.fixture(scope="module")
def seedmod():
    spec = importlib.util.spec_from_file_location("seed_tallymark_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Site(BaseHTTPRequestHandler):
    """The part of the fixture site the client uses: login wall, second factor, one comment."""

    writes: list[dict] = []
    broken = False

    def log_message(self, *args) -> None:  # silence
        pass

    def _reply(self, status: int, body: bytes = b"ok", headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/public/threads"):
            if _Site.broken:
                return self._reply(500)
            data = {"threads": [{"url": "/c/demo-forum/t/a-thread"}]}
            return self._reply(200, json.dumps(data).encode())
        if self.path == "/debug/mfa":
            data = {"pending": [{"token": "pending-1", "username": "fixture-user", "code": "424242"}]}
            return self._reply(200, json.dumps(data).encode())
        if self.path == "/debug/writes":
            return self._reply(200, json.dumps({"writes": _Site.writes}).encode())
        return self._reply(200)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = dict(pair.split("=", 1) for pair in self.rfile.read(length).decode().split("&") if "=" in pair)
        from urllib.parse import unquote_plus

        form = {key: unquote_plus(value) for key, value in form.items()}
        if self.path == "/login":
            return self._reply(
                303, headers={"Location": "/mfa", "Set-Cookie": "wenext_fixture_pending=pending-1; Path=/"}
            )
        if self.path == "/mfa":
            ok = form.get("code") == "424242"
            return self._reply(
                303 if ok else 401,
                headers={"Location": "/c/demo-forum/t/a-thread", "Set-Cookie": "wenext_fixture_session=s; Path=/"},
            )
        if self.path.endswith("/comments"):
            _Site.writes.append({"kind": "THREAD_COMMENT", "payload": {"body": form.get("body", "")[:2000]}})
            return self._reply(303, headers={"Location": "/c/demo-forum/t/a-thread?posted=1"})
        return self._reply(404)


@pytest.fixture
def site():
    _Site.writes = []
    _Site.broken = False
    server = HTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


async def _card(pool, seedmod) -> ToolRegistry:
    """A plugin session of the sandbox, a different one from the session that seeded it."""
    session = await SessionStore(pool).create(
        organization_id=seedmod.DEMO_ORG,
        workspace_id=seedmod.DEMO_WORKSPACE,
        project_id=seedmod.DEMO_PROJECT,
        goal="plugin session of the demo",
        budget={"host": "codex"},
    )
    return ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session.id,
            organization_id=seedmod.DEMO_ORG,
            workspace_id=seedmod.DEMO_WORKSPACE,
            project_id=seedmod.DEMO_PROJECT,
            dsn="postgresql://unused/never-connected",
            host="codex",
        ),
    )


async def _two_clicks(card: ToolRegistry, draft: dict) -> dict:
    """Send, then Yes, send: the two tools the card calls, in the order it calls them."""
    minted = await card.call("room.send_token", {"revision_id": draft["revision_id"], "odcisk": draft["odcisk"]})
    assert minted["stan"] == "WYKONANE", minted
    return await card.call(
        "room.send_draft",
        {"revision_id": draft["revision_id"], "token": minted["_mcp_meta"][META_TOKEN]},
    )


async def _demo_counts(pool, seedmod) -> dict[str, int]:
    async with pool.acquire() as conn:
        return await seedmod._counts(conn, seedmod.DEMO_ORG)


async def _bystander(pool, tenant) -> dict:
    """Another organization with a sent draft: a signature, a request, a job, a decision, evidence."""
    world = await _world(pool, tenant)
    draft = await _drafted(world, post="by5tnd", text="Reply of a bystander.")
    minted, token = await _token(world, draft)
    sent = await world.registry.call("room.send_draft", {"revision_id": draft["revision"], "token": token})
    assert sent["stan"] == "OCZEKUJE", sent
    return {"world": world, "request": sent["wniosek"]}


async def _snapshot(pool, organization, tables) -> dict[str, list]:
    """Every row of every listed table for one organization, as comparable text."""
    out: dict[str, list] = {}
    async with pool.acquire() as conn:
        for table in tables:
            rows = await conn.fetch(f"SELECT to_jsonb(t)::text AS row FROM {table} t WHERE organization_id = $1", organization)
            out[table] = sorted(row["row"] for row in rows)
    return out


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------


async def test_a_dry_run_reads_and_writes_nothing(pool, seedmod, capsys) -> None:
    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM organizations")
    assert await seedmod.seed(pool, dry_run=True, allow_old=False) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "founder organization rows" in out
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM organizations") == before


async def test_the_seed_builds_the_sandbox_through_the_products_own_tools(pool, seedmod) -> None:
    assert await seedmod.seed(pool, dry_run=False, allow_old=False) == 0
    async with pool.acquire() as conn:
        org = await conn.fetchval("SELECT name FROM organizations WHERE id = $1", seedmod.DEMO_ORG)
        accounts = await conn.fetch(
            "SELECT platform, external_account_id, state FROM social_accounts WHERE organization_id = $1 ORDER BY platform",
            seedmod.DEMO_ORG,
        )
        assigned = await conn.fetchval(
            "SELECT count(*) FROM project_account_assignments WHERE project_id = $1 AND unassigned_at IS NULL",
            seedmod.DEMO_PROJECT,
        )
        operators = await conn.fetchval(
            "SELECT count(*) FROM operators WHERE organization_id = $1 AND disabled_at IS NULL", seedmod.DEMO_ORG
        )
        revisions = await conn.fetch(
            "SELECT state, expires_at, payload FROM action_proposal_revisions WHERE organization_id = $1",
            seedmod.DEMO_ORG,
        )
        opportunities = await conn.fetchval(
            "SELECT count(*) FROM project_opportunities WHERE project_id = $1", seedmod.DEMO_PROJECT
        )
    assert org == seedmod.ORG_NAME
    assert [(a["platform"], a["external_account_id"], a["state"]) for a in accounts] == [
        ("REDDIT", seedmod.REDDIT_HANDLE, "READY"),
        ("X", seedmod.X_HANDLE, "READY"),
    ]
    assert assigned == 2 and operators == 1 and opportunities == 4
    assert len(revisions) == 4
    for row in revisions:
        assert row["state"] == "READY_FOR_APPROVAL"
        assert row["expires_at"] > datetime.now(UTC) + timedelta(days=13), "well in the future"


async def test_the_card_shows_four_people_with_a_reply_each_and_the_best_one_first(pool, seedmod) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    room = await card.call("room.show", {})
    assert room["stan"] == "WYKONANE"
    assert len(room["okazje"]) == 4 and len(room["szkice"]) == 4
    assert sorted(d["platforma"] for d in room["szkice"]) == ["REDDIT", "REDDIT", "X", "X"]
    assert all(d["stan"] == "czeka_na_zgode" for d in room["szkice"])
    # Nothing has gone out in a fresh take: the card's Results section has nothing invented in it.
    assert room["wyniki"]["opublikowane"] == 0 and room["wyniki"]["pozycje"] == []
    first = room["szkice"][0]
    assert first["platforma"] == "REDDIT" and "MRR" in first["tytul"], "the film opens on the flagship post"
    assert first["konto"] == seedmod.REDDIT_HANDLE
    assert first["wygasa"] > (datetime.now(UTC) + timedelta(days=13)).isoformat()


async def test_the_seed_is_idempotent_and_a_reseed_is_a_fresh_take(pool, seedmod) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    ids_before = await _skeleton_ids(pool, seedmod)
    first_drafts = await _revision_ids(pool, seedmod)
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    assert await _skeleton_ids(pool, seedmod) == ids_before, "the skeleton is reused, not rebuilt"
    again = await _revision_ids(pool, seedmod)
    assert len(again) == 4 and not set(again) & set(first_drafts), "the take is new"
    counts = await _demo_counts(pool, seedmod)
    assert counts["project_opportunities"] == 4 and counts["action_proposals"] == 4


async def _skeleton_ids(pool, seedmod) -> dict:
    async with pool.acquire() as conn:
        return {
            "operators": sorted(
                str(r["id"])
                for r in await conn.fetch(
                    "SELECT id FROM operators WHERE organization_id = $1", seedmod.DEMO_ORG
                )
            ),
            "accounts": sorted(
                str(r["id"])
                for r in await conn.fetch(
                    "SELECT id FROM social_accounts WHERE organization_id = $1", seedmod.DEMO_ORG
                )
            ),
        }


async def _revision_ids(pool, seedmod) -> list[str]:
    async with pool.acquire() as conn:
        return [
            str(r["id"])
            for r in await conn.fetch(
                "SELECT id FROM action_proposal_revisions WHERE organization_id = $1", seedmod.DEMO_ORG
            )
        ]


# ---------------------------------------------------------------------------
# The two clicks, and the staged last beat
# ---------------------------------------------------------------------------


async def test_send_then_yes_queues_the_request_and_nothing_publishes_it_but_the_stage(
    pool, seedmod, site
) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    draft = (await card.call("room.show", {}))["szkice"][0]
    sent = await _two_clicks(card, draft)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True, sent
    request = sent["wniosek"]
    queued = (await card.call("room.send_status", {"request_id": request}))["wniosek"]
    assert queued["status"] == "QUEUED" and queued["adres"] is None

    # No dispatcher exists for the sandbox: the job that nudges the publisher is written and waits.
    assert uuid.UUID(str(seedmod.DEMO_ORG)) not in await _organizations(pool)
    counts = await _demo_counts(pool, seedmod)
    assert counts["jobs"] >= 1 and counts["publication_requests"] == 1
    async with pool.acquire() as conn:
        statuses = await conn.fetch("SELECT status FROM jobs WHERE organization_id = $1", seedmod.DEMO_ORG)
    assert {row["status"] for row in statuses} == {"READY"}, "nothing claimed the sandbox's job"

    # The staged last beat: written to the (stub) fixture site, then the rows the publisher leaves.
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 1
    assert [w["payload"]["body"] for w in _Site.writes] == [await _text_of(pool, seedmod, draft)]
    done = (await card.call("room.send_status", {"request_id": request}))["wniosek"]
    assert done["status"] == "PUBLISHED" and done["opublikowano"]
    assert done["adres"].startswith("https://fixture-site.test/"), "an address that can never resolve"
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT raw::text FROM execution_evidence WHERE organization_id = $1", seedmod.DEMO_ORG
        )
    assert json.loads(raw)["staged"] is True and json.loads(raw)["fixture_written"] is True
    # Staging twice does nothing: the request is no longer waiting.
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 0
    # The opportunity now reads as answered, from the data, like any other.
    room = await card.call("room.show", {})
    states = {o["url"]: o["stan"] for o in room["okazje"]}
    assert "ODPOWIEDZIANO" in states.values()


async def _text_of(pool, seedmod, draft: dict) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT payload ->> 'text_and_media' FROM action_proposal_revisions WHERE id = $1",
            uuid.UUID(draft["revision_id"]),
        )


async def test_a_fixture_that_is_down_does_not_stop_the_stage_and_says_so(pool, seedmod, site) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    draft = (await card.call("room.show", {}))["szkice"][0]
    await _two_clicks(card, draft)
    _Site.broken = True
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 1
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT raw::text FROM execution_evidence WHERE organization_id = $1", seedmod.DEMO_ORG)
    assert json.loads(raw)["fixture_written"] is False and not _Site.writes


async def test_the_watcher_waits_before_it_stages(pool, seedmod, site) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    await _two_clicks(card, (await card.call("room.show", {}))["szkice"][0])
    assert await seedmod.post_once(pool, fixture_url=site, older_than=3600) == 0, "too soon: still QUEUED"
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 1


async def test_a_reset_between_takes_starts_clean_and_the_second_send_works(pool, seedmod, site) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    await _two_clicks(card, (await card.call("room.show", {}))["szkice"][0])
    await seedmod.post_once(pool, fixture_url=site, older_than=0)
    assert (await _demo_counts(pool, seedmod))["execution_evidence"] == 1

    await seedmod.seed(pool, dry_run=False, allow_old=False)  # `reset` is the same operation
    counts = await _demo_counts(pool, seedmod)
    assert counts["publication_requests"] == 0 and counts["execution_evidence"] == 0
    assert counts["project_delegations"] == 0, "the signature of the first take is gone: take two signs again"
    assert counts["jobs"] == 0 and counts["project_opportunities"] == 4

    again = (await card.call("room.show", {}))["szkice"][0]
    sent = await _two_clicks(card, again)
    assert sent["stan"] == "OCZEKUJE" and sent["podpisano"] is True
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 1


# ---------------------------------------------------------------------------
# Nobody else's rows
# ---------------------------------------------------------------------------


async def test_nothing_of_another_organization_is_touched_by_seed_post_or_reset(
    pool, tenant, seedmod, site
) -> None:
    bystander = await _bystander(pool, tenant)
    tables = (*seedmod.TAKE_TABLES, "agent_sessions", "projects", "social_accounts", "communities", "observed_threads")
    before = await _snapshot(pool, tenant["organization"], tables)
    assert before["publication_requests"] and before["project_delegations"] and before["jobs"]

    await seedmod.seed(pool, dry_run=False, allow_old=False)
    card = await _card(pool, seedmod)
    await _two_clicks(card, (await card.call("room.show", {}))["szkice"][0])
    assert await seedmod.post_once(pool, fixture_url=site, older_than=0) == 1
    await seedmod.seed(pool, dry_run=False, allow_old=False)

    after = await _snapshot(pool, tenant["organization"], tables)
    assert after == before, {t: (len(before[t]), len(after[t])) for t in tables if before[t] != after[t]}
    # And the stage did not take the bystander's queued request for its own.
    status = (await bystander["world"].registry.call("room.send_status", {"request_id": bystander["request"]}))["wniosek"]
    assert status["status"] == "QUEUED"


async def test_the_script_refuses_to_write_where_the_sandbox_is_not_named(pool, seedmod) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(seedmod.Refusal):
            await seedmod._delete(conn, "jobs", organization=seedmod.FOUNDER_ORG)
        with pytest.raises(seedmod.Refusal):
            await seedmod._delete(conn, "projects")


async def test_the_sandbox_has_no_dispatcher_and_the_others_do(pool, tenant, seedmod) -> None:
    await seedmod.seed(pool, dry_run=False, allow_old=False)
    listed = await _organizations(pool)
    assert tenant["organization"] in listed
    assert seedmod.DEMO_ORG not in listed


async def test_the_old_image_flag_unlocks_a_dry_run_and_nothing_else(pool, seedmod) -> None:
    # The flag exists to look at a live stack whose image predates the sandbox. Writing the sandbox
    # into such a stack is exactly what must stay refused.
    with pytest.raises(seedmod.Refusal, match="dry-run"):
        await seedmod.seed(pool, dry_run=False, allow_old=True)
    assert await seedmod.seed(pool, dry_run=True, allow_old=True) == 0
