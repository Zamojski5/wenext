"""The recording sandbox, without a database: what the seed script says it touches, and the words.

THE QUESTIONS, EACH A WAY THE DEMO COULD DAMAGE THE FOUNDER'S REAL SETUP OR THE FILM:

  * does any statement in `scripts/demo/seed_tallymark.py` reach a row that is not the sandbox's?
    A reader would have to trust the author; this reads the file and fails on the first statement
    that does not carry the organization;
  * does the script spell the same organization as the code that leaves it without a dispatcher?
    The two spellings exist on purpose (the script is piped into a container whose image may be
    older) and this is what keeps them one;
  * are the replies what the brief asked for: four of them, both platforms, helpful, the product
    named at most once and never where it does not help, no long dash, X inside its limit?
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from uuid import UUID

import pytest
from control_plane import demo

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "demo" / "seed_tallymark.py"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed():
    return _load(SCRIPT, "seed_tallymark_under_test")


class FakeConnection:
    """Records what would have been sent. A DELETE is the only thing the helper may build."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "DELETE 0"


# ---------------------------------------------------------------------------
# One organization, spelled twice
# ---------------------------------------------------------------------------


def test_the_script_and_the_server_name_the_same_sandbox(seed) -> None:
    assert seed.DEMO_ORG == demo.DEMO_ORGANIZATION_ID
    assert demo.is_demo_organization(seed.DEMO_ORG)
    assert seed.DEMO_ORG in demo.DEMO_ORGANIZATION_IDS


def test_the_sandbox_is_not_the_founders_organization_nor_workspace(seed) -> None:
    founder = {seed.FOUNDER_ORG, seed.FOUNDER_WORKSPACE}
    mine = {seed.DEMO_ORG, seed.DEMO_WORKSPACE, seed.DEMO_PROJECT}
    assert not founder & mine
    assert seed.FOUNDER_WORKSPACE == UUID("22222222-2222-2222-2222-222222222222")
    assert not demo.is_demo_organization(seed.FOUNDER_ORG)


# ---------------------------------------------------------------------------
# Every write names the sandbox
# ---------------------------------------------------------------------------


def _statements(source: str) -> list[str]:
    """Every string in the file that starts like a write, with `{...}` holes kept as `{}`."""
    found: list[str] = []
    tree = ast.parse(source)
    # The literal pieces of an f-string are part of it, not statements of their own.
    pieces = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        text = None
        if id(node) in pieces:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
            )
        if text and re.match(r"\s*(DELETE FROM|UPDATE|INSERT INTO)\b", text, re.IGNORECASE):
            found.append(" ".join(text.split()))
    return found


def test_every_write_in_the_script_carries_the_organization() -> None:
    statements = _statements(SCRIPT.read_text(encoding="utf-8"))
    assert len(statements) >= 10, "the scan found almost nothing: it is not reading the script"
    for sql in statements:
        if re.match(r"INSERT INTO organizations\b", sql):
            # The organization row itself: its id is the sandbox's, passed as the first argument.
            assert "(id, name)" in sql
            continue
        assert "organization_id" in sql, f"a write that does not name the organization: {sql}"
        if sql.upper().startswith(("DELETE", "UPDATE")):
            assert re.search(r"organization_id = \$\d", sql), f"no organization filter: {sql}"


def test_the_only_delete_is_the_guarded_helper() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    deletes = [s for s in _statements(source) if s.upper().startswith("DELETE")]
    assert deletes == ["DELETE FROM {} WHERE organization_id = $1"], deletes


async def test_the_helper_deletes_only_listed_tables_of_the_sandbox(seed) -> None:
    conn = FakeConnection()
    await seed._delete(conn, "jobs")
    (sql, args), = conn.calls
    assert sql == "DELETE FROM jobs WHERE organization_id = $1" and args == (seed.DEMO_ORG,)
    for table in ("projects", "organizations", "social_accounts", "audit_events", "users"):
        with pytest.raises(seed.Refusal):
            await seed._delete(conn, table)
    with pytest.raises(seed.Refusal):
        await seed._delete(conn, "jobs", organization=seed.FOUNDER_ORG)
    assert len(conn.calls) == 1, "a refused delete must not reach the connection"


def test_a_reset_removes_no_append_only_table(seed) -> None:
    # audit_events and session_events refuse DELETE by trigger; listing one would turn a reset
    # into an error halfway through its transaction.
    assert not {"audit_events", "session_events", "thread_metric_snapshots"} & set(seed.TAKE_TABLES)
    # Children before parents, or the foreign keys refuse.
    order = list(seed.TAKE_TABLES)
    assert order.index("publication_requests") < order.index("execution_intents")
    assert order.index("execution_evidence") < order.index("execution_attempts")
    assert order.index("execution_attempts") < order.index("execution_intents")
    assert order.index("approval_decisions") < order.index("action_proposal_revisions")
    assert order.index("action_proposal_revisions") < order.index("action_proposals")
    assert order.index("publication_requests") < order.index("project_delegations")
    assert order.index("job_attempts") < order.index("jobs")


def test_the_database_address_is_taken_from_the_container_or_the_flag(seed, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WENEXT_TEST_DSN", raising=False)
    with pytest.raises(seed.Refusal):
        seed._dsn(None)
    assert seed._dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://a/b")
    assert seed._dsn(None) == "postgresql://a/b"


def test_the_ids_command_prints_what_the_project_config_needs(seed, capsys) -> None:
    assert seed.ids() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["organization"] == str(demo.DEMO_ORGANIZATION_ID)
    assert printed["project_name"] == "tallymark"


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_there_are_four_conversations_on_both_platforms_each_with_a_reply(seed) -> None:
    items = seed.conversations()
    assert len(items) == 4
    assert sorted(i["mark"]["platform"] for i in items) == ["REDDIT", "REDDIT", "X", "X"]
    for item in items:
        assert item["draft"].strip() and item["mark"]["dlaczego"].strip() and item["mark"]["kat"].strip()


def test_the_replies_name_the_product_at_most_once_and_only_where_it_helps(seed) -> None:
    mentions = [i["draft"].lower().count("tallymark") for i in seed.conversations()]
    assert max(mentions) <= 1, mentions
    # Two of the four people ask for a tool or a simpler way; the other two ask a question the
    # reply fully answers, and naming the product there would be a pitch.
    assert sorted(mentions) == [0, 0, 1, 1], mentions
    for item in seed.conversations():
        if "No product mention" in item["mark"]["kat"]:
            assert "tallymark" not in item["draft"].lower()
        if "tallymark" in item["draft"].lower():
            assert re.search(r"\bI('m building| wrote| made| build| built)\b", item["draft"]), "as the author, said so"


def test_nothing_the_script_says_or_writes_has_a_long_dash(seed) -> None:
    assert not LONG_DASHES.search(SCRIPT.read_text(encoding="utf-8"))
    for item in seed.conversations():
        for value in (*item["mark"].values(), item["draft"]):
            assert not LONG_DASHES.search(str(value)), value


def test_x_replies_fit_a_post_and_say_nothing_a_reader_would_find_odd(seed) -> None:
    for item in seed.conversations():
        text = item["draft"]
        if item["mark"]["platform"] == "X":
            assert len(text) <= 280, len(text)
            assert "#" not in text and "http" not in text
        else:
            assert 400 <= len(text) <= 1800
        assert "dm me" not in text.lower() and "check out" not in text.lower()


def test_the_conversations_are_fresh_and_their_urls_are_unmistakably_fictional(seed) -> None:
    for item in seed.conversations():
        url = item["mark"]["url"]
        assert url.startswith(("https://www.reddit.com/r/", "https://x.com/"))
    ids = [i["mark"]["url"] for i in seed.conversations()]
    assert len(set(ids)) == 4
    # The invented people are neither the sandbox's own accounts nor the placeholder handles the
    # test suites use for an operator's accounts, and the sandbox's accounts are not those either.
    placeholders = {"demo_maintainer", "demo_founder"}
    own = {seed.REDDIT_HANDLE.lower(), seed.X_HANDLE.lower()}
    authors = {i["mark"]["author"].lower() for i in seed.conversations()}
    assert not authors & (placeholders | own)
    assert not own & placeholders


def test_the_sandbox_has_a_project_goal_in_english(seed) -> None:
    assert seed.PROJECT_NAME == "tallymark"
    assert "MRR" in seed.PROJECT_GOAL and "Stripe" in seed.PROJECT_GOAL
    assert not LONG_DASHES.search(seed.PROJECT_GOAL)
