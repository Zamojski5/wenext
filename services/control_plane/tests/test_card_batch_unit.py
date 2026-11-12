"""Sending a whole reviewed batch with one click: ONLY THE PERSON'S CLICK CAN DO IT (v11, no database).

`tools/card_batch.py` holds the threat model (lines 18 to 24). What is held here without a
database, from the outside in:

  * VISIBILITY: `room.batch_token` and `room.send_batch` are APP-ONLY tools like the other twelve:
    not in the catalog the model reads, not in `catalog.list` or `catalog.describe`, listed with
    `visibility: ["app"]` for a Codex host and for nobody else, and "no such tool" for Claude Code, a
    Wenext-run session and a hosted one, answered before any database access;
  * THE TOKEN: one outstanding at a time (every tick or untick is a new question), works once,
    is burned by the first presentation right or wrong, expires, only its digest is kept, travels
    in `_meta` and never in the trace, and the set it binds has one digest;
  * THE SHAPE of the calls: the question takes two parallel lists and nothing else, the act takes
    the token and nothing else (`additionalProperties: false`), and a malformed call is refused
    before anything is read;
  * THE SPECS are honest about what they write and carry no long dash.

The database half (every draft validated as `room.send_token` validates, an edited draft refuses the
whole batch, the schedule and the queue rows) is `tests/test_card_batch_db.py`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.presence.batch_plan import MAX_BATCH
from control_plane.sessions.trace_payloads import TOOL_VERBS
from control_plane.tools import (
    APP_ONLY_TOOLS,
    BY_NAME,
    PLUGIN_EXCLUDED,
    PLUGIN_TOOLS,
    NoSuchTool,
    ToolRegistry,
    issue,
    listing,
    manifest,
)
from control_plane.tools.card_batch import (
    BATCH_TTL,
    CARD_BATCH_TOOL_SPECS,
    CODE_EMPTY,
    CODE_TOO_BIG,
    BatchItem,
    BatchTokens,
    set_digest,
)
from control_plane.tools.card_send import META_TOKEN, TokenRefused, redacted_args
from control_plane.tools.catalog import (
    APP_ONLY_BY_NAME,
    APP_ONLY_HOSTS,
    CATALOG,
    allowed_for_host,
    app_manifest,
)
from tests.test_card_send_unit import _INIT, _LIST, _serve, _StubRegistry

BATCH_TOOLS = {"room.batch_token", "room.send_batch"}
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
HASH_A, HASH_B = "a" * 64, "b" * 64


def _capability(**changes):
    base = {
        "session_id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "dsn": "postgresql://nieuzywane-w-tescie",
    }
    return issue(**{**base, **changes})


# ---------------------------------------------------------------------------
# Visibility: the model never gets the tool
# ---------------------------------------------------------------------------


def test_the_two_batch_tools_are_app_only_and_nowhere_the_model_can_read() -> None:
    assert BATCH_TOOLS <= APP_ONLY_TOOLS
    assert not BATCH_TOOLS & set(BY_NAME), "narzędzie paczki stoi w katalogu modelu"
    assert not BATCH_TOOLS & PLUGIN_TOOLS and not BATCH_TOOLS & set(PLUGIN_EXCLUDED)
    for host in (None, "codex", "claude-code"):
        assert not {e["name"] for e in manifest(host=host)} & BATCH_TOOLS, host
        assert not {e["name"] for e in listing(host=host)} & BATCH_TOOLS, host
    assert len(manifest()) == len(CATALOG), "`catalog.list` did not grow"
    assert {"codex"} == APP_ONLY_HOSTS
    assert {e["name"] for e in app_manifest("codex")} >= BATCH_TOOLS
    assert app_manifest("claude-code") == [] and app_manifest(None) == []
    assert app_manifest("codex", hosted=True) == []
    for name in BATCH_TOOLS:
        assert allowed_for_host(name, "codex") is True
        assert allowed_for_host(name, "claude-code") is False
        assert allowed_for_host(name, None) is False
        assert allowed_for_host(name, "codex", hosted=True) is False


@pytest.mark.parametrize("host", [None, "claude-code"])
@pytest.mark.parametrize("name", sorted(BATCH_TOOLS))
async def test_a_host_that_serves_no_card_is_told_there_is_no_such_tool(host, name) -> None:
    """Refused before any database access (no pool): the same answer as a name that is not there."""
    registry = ToolRegistry(capability=_capability(host=host), pool=None)  # type: ignore[arg-type]
    with pytest.raises(NoSuchTool):
        await registry.call(
            name, {"revision_ids": [str(uuid4())], "odciski": [HASH_A], "token": "x"}
        )


async def test_a_hosted_session_cannot_call_them_either() -> None:
    registry = ToolRegistry(
        capability=_capability(host="codex", hosted=True),  # type: ignore[arg-type]
        pool=None,  # type: ignore[arg-type]
    )
    for name in BATCH_TOOLS:
        with pytest.raises(NoSuchTool):
            await registry.call(name, {"token": "x"})


async def test_catalog_describe_does_not_tell_the_model_how_the_batch_works() -> None:
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    missing = await registry.call("catalog.describe", {"tool": "no.such.tool"})
    for name in BATCH_TOOLS:
        hidden = await registry.call("catalog.describe", {"tool": name})
        assert hidden["stan"] == "BLAD"
        assert hidden["powod"].replace(name, "X") == missing["powod"].replace("no.such.tool", "X")


@pytest.mark.parametrize("host", [None, "codex", "claude-code"])
def test_no_host_lists_a_batch_tool_to_the_model_and_codex_lists_it_for_the_card(host) -> None:
    listed = _serve(host, _StubRegistry(), [_INIT, _LIST])[1]["result"]["tools"]
    names = {tool["name"] for tool in listed}
    if host == "codex":
        assert names >= BATCH_TOOLS
        for tool in listed:
            if tool["name"] in BATCH_TOOLS:
                assert tool["_meta"] == {"ui": {"visibility": ["app"]}}
    else:
        assert not names & BATCH_TOOLS, host


# ---------------------------------------------------------------------------
# The specs
# ---------------------------------------------------------------------------


def test_the_specs_are_honest_about_what_they_write_and_say_the_model_cannot_call_them() -> None:
    by_name = {spec["name"]: spec for spec in CARD_BATCH_TOOL_SPECS}
    assert set(by_name) == BATCH_TOOLS
    # The question writes nothing and must not be replayed from the trace (an old, dead token).
    assert not by_name["room.batch_token"].get("writes")
    assert by_name["room.send_batch"]["writes"] is True
    for name, spec in by_name.items():
        assert spec["state"] == "DOSTEPNE"
        assert spec["input_schema"]["additionalProperties"] is False
        assert "TYLKO DLA KARTY" in spec["summary"]
        assert not LONG_DASHES.search(spec["summary"] + spec["detail"]), name
        assert "visibility: app" in spec["detail"]
        assert name in TOOL_VERBS, "zapis w śladzie ma zdanie, nie nazwę techniczną"
        assert not LONG_DASHES.search(TOOL_VERBS[name])
        assert APP_ONLY_BY_NAME[name].name == name


def test_the_act_takes_the_token_and_nothing_else_so_the_card_cannot_change_the_set() -> None:
    act = {s["name"]: s for s in CARD_BATCH_TOOL_SPECS}["room.send_batch"]["input_schema"]
    assert act["required"] == ["token"] and set(act["properties"]) == {"token"}
    question = {s["name"]: s for s in CARD_BATCH_TOOL_SPECS}["room.batch_token"]["input_schema"]
    assert set(question["properties"]) == {"revision_ids", "odciski"}
    assert question["properties"]["revision_ids"]["maxItems"] == MAX_BATCH == 25
    assert question["required"] == ["revision_ids", "odciski"]


def test_the_secret_argument_of_the_act_is_redacted_for_the_trace() -> None:
    assert redacted_args("room.send_batch", {"token": "tajne"}) == {
        "token": "[pominięte w śladzie]"
    }
    args = {"revision_ids": ["r"], "odciski": [HASH_A]}
    assert redacted_args("room.batch_token", args) == args, "the question has no secret"


def test_the_token_of_a_batch_travels_in_meta_only() -> None:
    secret = "tajne-potwierdzenie-paczki"
    registry = _StubRegistry({"stan": "WYKONANE", "paczka": {}, "_mcp_meta": {META_TOKEN: secret}})
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "room.batch_token",
            "arguments": {"revision_ids": [str(uuid4())], "odciski": [HASH_A]},
        },
    }
    answer = _serve("codex", registry, [_INIT, call])[1]["result"]
    assert answer["_meta"] == {META_TOKEN: secret}
    for field in ("content", "structuredContent"):
        assert secret not in json.dumps(answer[field], ensure_ascii=False)


# ---------------------------------------------------------------------------
# A malformed call is refused before anything is read
# ---------------------------------------------------------------------------


async def _call(name: str, args: dict) -> dict:
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    return await registry.call(name, args)


async def test_a_question_with_lists_of_different_length_or_a_bad_digest_is_an_error() -> None:
    for args in (
        {"revision_ids": [str(uuid4())], "odciski": [HASH_A, HASH_B]},
        {"revision_ids": "x", "odciski": [HASH_A]},
        {"revision_ids": [str(uuid4())], "odciski": ["nie-odcisk"]},
        {"revision_ids": ["nie-uuid"], "odciski": [HASH_A]},
    ):
        result = await _call("room.batch_token", args)
        assert result["stan"] == "BLAD", args
    same = str(uuid4())
    result = await _call(
        "room.batch_token", {"revision_ids": [same, same], "odciski": [HASH_A, HASH_A]}
    )
    assert result["stan"] == "BLAD" and "różne" in result["powod"]


async def test_an_empty_batch_and_one_over_the_bound_are_refused_with_a_code() -> None:
    empty = await _call("room.batch_token", {"revision_ids": [], "odciski": []})
    assert empty["stan"] == "NIEDOSTEPNE" and empty["kod"] == CODE_EMPTY
    n = MAX_BATCH + 1
    big = await _call(
        "room.batch_token",
        {"revision_ids": [str(uuid4()) for _ in range(n)], "odciski": [HASH_A] * n},
    )
    assert big["stan"] == "NIEDOSTEPNE" and big["kod"] == CODE_TOO_BIG and big["limit"] == MAX_BATCH


async def test_sending_without_a_token_or_with_a_guessed_one_is_refused_before_any_read() -> None:
    # The handler itself (a write goes through `registry.call`, which asks the trace first, and that
    # needs the database; the handler refuses before it touches any).
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    for args in ({}, {"token": ""}, {"token": "zgadnięte"}, {"token": "A" * 43}):
        result = await registry._room_send_batch(args)  # type: ignore[attr-defined]
        assert result["stan"] == "NIEDOSTEPNE" and result["kod"] == "POTWIERDZENIE", args


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _items(*pairs: tuple[str, str]) -> list[BatchItem]:
    at = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    return [
        BatchItem(
            revision_id=uuid4(), payload_hash=h, account_id=uuid4(), at=at + timedelta(minutes=i)
        )
        for i, (_name, h) in enumerate(pairs)
    ]


def test_a_batch_token_works_once_and_carries_the_whole_validated_set() -> None:
    tokens = BatchTokens()
    items = _items(("a", HASH_A), ("b", HASH_B))
    token, _expires = tokens.mint(items=items, basis="WYSLIJ:abc")
    grant = tokens.redeem(token)
    assert grant.items == tuple(items) and grant.basis == "WYSLIJ:abc"
    assert [i.at for i in grant.items] == [i.at for i in items], "the moments shown are in it"
    with pytest.raises(TokenRefused) as spent:
        tokens.redeem(token)
    assert "zużyte" in spent.value.detail


def test_a_guessed_batch_token_is_refused_and_does_not_spend_the_real_one() -> None:
    tokens = BatchTokens()
    real, _ = tokens.mint(items=_items(("a", HASH_A)), basis="WYSLIJ:-")
    for guess in ("", "zgadnięte", "A" * 43, real[::-1]):
        with pytest.raises(TokenRefused):
            tokens.redeem(guess)
    assert tokens.redeem(real).basis == "WYSLIJ:-"


def test_a_new_question_kills_the_earlier_confirmation_one_outstanding_at_a_time() -> None:
    """Unticking a draft is a NEW question: the token for the bigger set is dead the moment it is asked."""
    tokens = BatchTokens()
    first, _ = tokens.mint(items=_items(("a", HASH_A), ("b", HASH_B)), basis="WYSLIJ:-")
    second, _ = tokens.mint(items=_items(("a", HASH_A)), basis="WYSLIJ:-")
    with pytest.raises(TokenRefused):
        tokens.redeem(first)
    assert len(tokens.redeem(second).items) == 1


def test_an_expired_batch_token_is_refused_and_lives_five_minutes() -> None:
    clock = _Clock()
    tokens = BatchTokens(clock=clock)
    token, expires = tokens.mint(items=_items(("a", HASH_A)), basis="WYSLIJ:-")
    assert expires == clock.now + BATCH_TTL and timedelta(minutes=5) == BATCH_TTL
    clock.now += BATCH_TTL
    with pytest.raises(TokenRefused) as late:
        tokens.redeem(token)
    assert "wygasło" in late.value.detail


def test_only_the_digest_of_a_batch_token_is_kept() -> None:
    tokens = BatchTokens()
    token, _ = tokens.mint(items=_items(("a", HASH_A)), basis="WYSLIJ:-")
    assert token not in tokens._grants and token not in json.dumps(list(tokens._grants))


def test_the_set_digest_depends_on_the_revisions_and_their_texts_and_not_on_the_order() -> None:
    r1, r2 = uuid4(), uuid4()
    base = set_digest([(r1, HASH_A), (r2, HASH_B)])
    assert base == set_digest([(r2, HASH_B), (r1, HASH_A)]), "a set has no order"
    assert base != set_digest([(r1, HASH_A)]), "a smaller set is another set"
    assert base != set_digest([(r1, HASH_B), (r2, HASH_B)]), "an edited draft is another set"
    assert base != set_digest([(r1, HASH_A), (uuid4(), HASH_B)]), "another draft is another set"
    assert re.fullmatch(r"[0-9a-f]{64}", base)
