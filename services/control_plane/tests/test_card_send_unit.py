"""Sending a draft from the card: ONLY THE HUMAN'S CLICK CAN DO IT. The half that needs no database.

The founder agreed to "Wyślij" on the card in Codex on one condition: the model can never press
it. The design is layered (`tools/card_send.py` has the threat model), and each test here holds one
layer, from the outside in:

  * the tools are the card's: hidden from the model for EVERY host, not listed at all for a host
    that is not an MCP Apps host, not callable by a Wenext-run session;
  * the server refuses a call that did not come from a click: no token, a wrong one, an expired one,
    a spent one, one minted for another draft;
  * the token never travels as text and never reaches the trace;
  * what a click will do about the scope is decided by one pure function, and a pause is a stop.

What is NOT measured here, and is stated so the file does not promise more: that Codex really
filters `visibility: ["app"]`, proxies a view's `tools/call` and hands the card `_meta`. The first
is in the host's source (openai/codex PR 24700, read, not run); the other two are unverified live.
"""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.presence import card_consent
from control_plane.presence.card_consent import (
    CONSENT_SEND,
    CONSENT_SIGN,
    CONSENT_WIDEN,
    REFUSED_MODE,
    REFUSED_PAUSED,
    CardConsentRefused,
    ConsentAccount,
    eligible_accounts,
    plan_consent,
)
from control_plane.presence.human_approval import (
    REFUSED_HASH,
    REFUSED_STATE,
    REFUSED_WINDOW,
    HumanApprovalRefused,
    ensure_waiting,
)
from control_plane.presence.scope import Budget, Scope
from control_plane.presence.store import DelegationRow
from control_plane.sessions.trace_payloads import TOOL_VERBS, args_digest
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
from control_plane.tools.card_send import (
    CARD_SEND_TOOL_SPECS,
    MAX_OUTSTANDING,
    META_TOKEN,
    TOKEN_TTL,
    SendTokens,
    TokenRefused,
    redacted_args,
)
from control_plane.tools.catalog import (
    APP_ONLY_BY_NAME,
    APP_ONLY_HOSTS,
    allowed_for_host,
    app_manifest,
)
from control_plane.tools.mcp_server import MCPToolServer, _Loop

CONTROL_PLANE = Path(__file__).resolve().parents[1]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
HASH = "a" * 64


# ---------------------------------------------------------------------------
# The tools are the card's: who sees them, who can call them
# ---------------------------------------------------------------------------


def _model_visible(tools: list[dict]) -> set[str]:
    """What a host that follows MCP Apps (SEP-1865) shows the agent: it MUST leave out a tool
    whose `_meta.ui.visibility` does not include "model"."""
    return {
        tool["name"]
        for tool in tools
        if "model" in tool.get("_meta", {}).get("ui", {}).get("visibility", ["model", "app"])
    }


def _capability(**changes):
    base = {
        "session_id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "dsn": "postgresql://nieuzywane-w-tescie",
    }
    return issue(**{**base, **changes})


def test_the_card_tools_are_not_in_the_catalog_the_model_reads() -> None:
    assert {"room.send_token", "room.send_draft", "room.send_status"} == APP_ONLY_TOOLS
    assert not APP_ONLY_TOOLS & set(BY_NAME), "narzędzie karty stoi w katalogu modelu"
    assert not APP_ONLY_TOOLS & PLUGIN_TOOLS
    assert not APP_ONLY_TOOLS & set(PLUGIN_EXCLUDED)
    for host in (None, "codex", "claude-code"):
        assert not {e["name"] for e in manifest(host=host)} & APP_ONLY_TOOLS, host
        assert not {e["name"] for e in listing(host=host)} & APP_ONLY_TOOLS, host
    # `catalog.list` counts the catalog: it did not grow.
    assert len(manifest()) == 54


def test_only_a_codex_host_is_given_the_card_tools_and_they_say_app_only() -> None:
    assert {"codex"} == APP_ONLY_HOSTS
    assert {e["name"] for e in app_manifest("codex")} == APP_ONLY_TOOLS
    # Claude Code is not an MCP Apps host: it would list these to the model, so it gets none.
    assert app_manifest("claude-code") == []
    assert app_manifest(None) == []
    for name in APP_ONLY_TOOLS:
        assert allowed_for_host(name, "codex") is True
        assert allowed_for_host(name, "claude-code") is False
        assert allowed_for_host(name, None) is False, "sesja Wenext nie ma karty, w którą klika"


class _StubRegistry:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result if result is not None else {"stan": "WYKONANE"}
        self.called: list[tuple[str, dict]] = []

    async def platform_capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, args: dict) -> dict:
        self.called.append((name, args))
        return self._result


class _Sessions:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def append_event(self, *, organization_id, session_id, kind, payload) -> None:
        self.events.append((kind, payload))


def _serve(host, registry, frames, sessions=None) -> list[dict]:
    runner = _Loop()
    runner.start()
    out = io.StringIO()
    try:
        server = MCPToolServer(
            capability=_capability(host=host),
            registry=registry,
            runner=runner,
            sessions=(sessions or _Sessions()) if host else None,
            stream_in=io.StringIO(),
            stream_out=out,
        )
        for frame in frames:
            server.handle(frame)
    finally:
        runner.stop()
    return [json.loads(line) for line in out.getvalue().splitlines()]


_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.mark.parametrize("host", [None, "codex", "claude-code"])
def test_the_model_visible_tools_never_include_a_card_tool_for_any_host(host) -> None:
    listed = _serve(host, _StubRegistry(), [_INIT, _LIST])[1]["result"]["tools"]
    assert not _model_visible(listed) & APP_ONLY_TOOLS, host
    names = {tool["name"] for tool in listed}
    if host == "codex":
        # Listed, so the host can proxy the card's call, and each marked app-only.
        assert names & APP_ONLY_TOOLS == APP_ONLY_TOOLS
        for tool in listed:
            if tool["name"] in APP_ONLY_TOOLS:
                assert tool["_meta"] == {"ui": {"visibility": ["app"]}}
                assert "resourceUri" not in tool["_meta"]["ui"], (
                    "narzędzie karty niczego nie rysuje"
                )
    else:
        # Not even listed: Claude Code would show them to the model, a Wenext-run session has no
        # card, and "hidden by the host" is a promise only an Apps host makes.
        assert not names & APP_ONLY_TOOLS, host


@pytest.mark.parametrize("host", [None, "claude-code"])
@pytest.mark.parametrize("name", sorted(APP_ONLY_TOOLS))
async def test_a_card_tool_is_no_such_tool_for_a_session_that_has_no_card(host, name) -> None:
    """Refused before any database access (no pool): the same answer as a name that is not there."""
    registry = ToolRegistry(capability=_capability(host=host), pool=None)  # type: ignore[arg-type]
    with pytest.raises(NoSuchTool):
        await registry.call(name, {"revision_id": str(uuid4()), "token": "x", "request_id": "x"})


async def test_catalog_describe_does_not_tell_the_model_how_the_send_works() -> None:
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    hidden = await registry.call("catalog.describe", {"tool": "room.send_draft"})
    missing = await registry.call("catalog.describe", {"tool": "no.such.tool"})
    assert hidden["stan"] == "BLAD"
    assert hidden["powod"].replace("room.send_draft", "X") == missing["powod"].replace(
        "no.such.tool", "X"
    )


def test_the_card_tool_specs_are_honest_about_what_they_write() -> None:
    by_name = {spec.name: spec for spec in APP_ONLY_BY_NAME.values()}
    # Minting writes nothing and must not be replayed from the trace (an old, dead token).
    assert by_name["room.send_token"].writes is False
    assert by_name["room.send_status"].writes is False
    assert by_name["room.send_draft"].writes is True
    for spec in CARD_SEND_TOOL_SPECS:
        assert spec["state"] == "DOSTEPNE"
        assert spec["input_schema"]["additionalProperties"] is False
        assert not LONG_DASHES.search(spec["summary"] + spec["detail"])
        assert "TYLKO DLA KARTY" in spec["summary"]
    for name in APP_ONLY_TOOLS:
        assert name in TOOL_VERBS, "zapis w śladzie ma zdanie, nie nazwę techniczną"


# ---------------------------------------------------------------------------
# The server refuses a call that did not come from a click
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _mint(tokens: SendTokens, revision=None, **changes):
    revision = revision or uuid4()
    return revision, *tokens.mint(
        revision_id=revision,
        payload_hash=HASH,
        account_id=changes.pop("account_id", uuid4()),
        basis=changes.pop("basis", "WYSLIJ:-"),
    )


def test_a_token_works_once_for_the_draft_it_was_minted_for() -> None:
    tokens = SendTokens()
    revision, token, _ = _mint(tokens)
    grant = tokens.redeem(token, revision_id=revision)
    assert grant.revision_id == revision and grant.payload_hash == HASH
    with pytest.raises(TokenRefused) as spent:
        tokens.redeem(token, revision_id=revision)
    assert "zużyte" in spent.value.detail


def test_a_call_without_a_token_or_with_a_guessed_one_is_refused() -> None:
    tokens = SendTokens()
    revision, _real, _ = _mint(tokens)
    for guess in ("", "zgadnięte", "A" * 43, _real[::-1]):
        with pytest.raises(TokenRefused):
            tokens.redeem(guess, revision_id=revision)
    # Guessing did not spend the real one.
    assert tokens.redeem(_real, revision_id=revision).revision_id == revision


def test_an_expired_token_is_refused_and_so_is_one_for_another_draft() -> None:
    clock = _Clock()
    tokens = SendTokens(clock=clock)
    revision, token, expires = _mint(tokens)
    assert expires == clock.now + TOKEN_TTL
    clock.now += TOKEN_TTL
    with pytest.raises(TokenRefused) as late:
        tokens.redeem(token, revision_id=revision)
    assert "wygasło" in late.value.detail

    other, token_b, _ = _mint(tokens)
    with pytest.raises(TokenRefused) as wrong:
        tokens.redeem(token_b, revision_id=uuid4())
    assert "innego szkicu" in wrong.value.detail
    # ...and a token shown for the wrong draft is burned: it cannot be retried on the right one.
    with pytest.raises(TokenRefused):
        tokens.redeem(token_b, revision_id=other)


def test_a_new_token_for_the_same_draft_replaces_the_old_one() -> None:
    tokens = SendTokens()
    revision, first, _ = _mint(tokens)
    second, _ = tokens.mint(
        revision_id=revision, payload_hash=HASH, account_id=uuid4(), basis="WYSLIJ:-"
    )
    with pytest.raises(TokenRefused):
        tokens.redeem(first, revision_id=revision)
    assert tokens.redeem(second, revision_id=revision).revision_id == revision


def test_only_the_digest_of_a_token_is_kept_and_the_number_outstanding_is_bounded() -> None:
    tokens = SendTokens()
    minted = [_mint(tokens)[1] for _ in range(MAX_OUTSTANDING + 5)]
    assert len(tokens._grants) == MAX_OUTSTANDING
    for token in minted:
        assert token not in tokens._grants and token not in json.dumps(list(tokens._grants))


def test_the_secret_argument_is_redacted_for_the_trace_and_nothing_else_is() -> None:
    args = {"revision_id": "r", "token": "tajne"}
    assert redacted_args("room.send_draft", args) == {
        "revision_id": "r",
        "token": "[pominięte w śladzie]",
    }
    assert redacted_args("room.send_token", {"revision_id": "r"}) == {"revision_id": "r"}
    assert redacted_args("draft.create", {"token": "x"}) == {"token": "x"}


def test_the_token_travels_in_meta_only_and_the_trace_never_carries_it() -> None:
    secret = "tajne-potwierdzenie-karty"
    registry = _StubRegistry({"stan": "WYKONANE", "konto": "u", "_mcp_meta": {META_TOKEN: secret}})
    sessions = _Sessions()
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "room.send_draft", "arguments": {"revision_id": "r", "token": secret}},
    }
    answer = _serve("codex", registry, [_INIT, call], sessions)[1]["result"]

    assert answer["_meta"] == {META_TOKEN: secret}, "karta nie dostanie potwierdzenia"
    for field in ("content", "structuredContent"):
        assert secret not in json.dumps(answer[field], ensure_ascii=False), (
            f"potwierdzenie w polu {field}, które czyta model albo host"
        )
    assert answer["structuredContent"]["stan"] == "WYKONANE"
    # The trace: the call row has a placeholder, the result row has nothing of the token, and the
    # digest `_already_done` matches on is still taken over the real arguments.
    assert secret not in json.dumps(sessions.events, ensure_ascii=False)
    (_, call_row), (_, result_row) = sessions.events
    assert call_row["arguments"]["token"] == "[pominięte w śladzie]"
    assert call_row["args_sha256"] == args_digest(
        "room.send_draft", {"revision_id": "r", "token": secret}
    )
    assert "_meta" not in result_row["result"]


def test_a_tool_without_a_secret_gets_no_meta_and_a_card_tool_gets_structured_content() -> None:
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "room.send_status", "arguments": {"request_id": "x"}},
    }
    answer = _serve("codex", _StubRegistry(), [_INIT, call])[1]["result"]
    assert "_meta" not in answer and answer["structuredContent"] == {"stan": "WYKONANE"}


# ---------------------------------------------------------------------------
# The checks the dashboard makes, in the order it makes them
# ---------------------------------------------------------------------------


def _row(**changes) -> dict:
    base = {
        "state": "READY_FOR_APPROVAL",
        "expires_at": datetime.now(UTC) + timedelta(hours=10),
        "payload_hash": HASH,
    }
    return {**base, **changes}


def test_a_proposal_must_wait_be_in_time_and_be_the_text_the_person_saw() -> None:
    ensure_waiting(_row(), HASH)
    ensure_waiting(_row())  # the route has no digest to compare with
    cases = [
        (_row(state="APPROVED"), HASH, REFUSED_STATE),
        (_row(state="REJECTED"), HASH, REFUSED_STATE),
        (_row(expires_at=datetime.now(UTC) - timedelta(seconds=1)), HASH, REFUSED_WINDOW),
        (_row(), "b" * 64, REFUSED_HASH),
    ]
    for row, shown, code in cases:
        with pytest.raises(HumanApprovalRefused) as refused:
            ensure_waiting(row, shown)
        assert refused.value.code == code and refused.value.status == 409
        assert not LONG_DASHES.search(refused.value.detail)


def test_the_refusal_sentences_are_the_ones_the_dashboard_tells_apart_by_substring() -> None:
    """`apps/dashboard/lib/room-actions.ts` matches these words in the route's 409 text."""
    with pytest.raises(HumanApprovalRefused) as window:
        ensure_waiting(_row(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert "okno zgody" in window.value.detail
    with pytest.raises(HumanApprovalRefused) as state:
        ensure_waiting(_row(state="APPROVED"))
    assert "czeka na zgodę" in state.value.detail


# ---------------------------------------------------------------------------
# What a click does about the scope: three cases, a pause is a stop
# ---------------------------------------------------------------------------

ACCOUNT = uuid4()
OTHER_ACCOUNT = uuid4()
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
#: The account of the draft being clicked: the project's Reddit account.
DRAFT = ConsentAccount(ACCOUNT, "REDDIT", "u_draft", "READY")


def _acct(platform: str, handle: str, state: str = "READY") -> ConsentAccount:
    return ConsentAccount(uuid4(), platform, handle, state)


def _delegation(**changes) -> DelegationRow:
    base = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "mode": "REVIEW",
        "scope": Scope(accounts=(OTHER_ACCOUNT,), communities=("r/Other",), actions=("REPLY",)),
        "budget": Budget(publications_per_hour=3, publications_per_day=9),
        "scope_sha256": "c" * 64,
        "signed_at": NOW - timedelta(days=1),
        "signed_by": uuid4(),
        "paused_at": None,
        "revoked_at": None,
        "expires_at": NOW + timedelta(days=5),
    }
    return DelegationRow(**{**base, **changes})


def _plan(delegation, **changes):
    args = {"account": DRAFT, "community_key": "r/SaaS", "action": "REPLY", "now": NOW}
    return plan_consent(delegation, **{**args, **changes})


def test_with_no_signed_scope_one_click_signs_the_smallest_review_scope() -> None:
    plan = _plan(None)
    assert plan.kind == CONSENT_SIGN and plan.signs and plan.replaces is None
    assert plan.mode == "REVIEW", "każdy tekst czeka na kliknięcie: nic nie wychodzi niewidziane"
    assert plan.scope.accounts == (ACCOUNT,), "tylko konto tego szkicu"
    assert plan.scope.communities == ("r/SaaS",) and plan.scope.actions == ("REPLY",)
    assert plan.budget.publications_per_hour == 1 and plan.budget.publications_per_day == 6
    assert plan.expires_at == NOW + timedelta(days=7), "domyślny termin, ten sam co formularz"
    assert plan.to_json()["limity"] == {"godzina": 1, "doba": 6}
    assert plan.basis().startswith("PODPISZ:-:"), "podpis wiąże listę kont, którą pokazano"


def test_an_own_post_draft_signs_its_own_action_beside_reply() -> None:
    plan = _plan(None, action="OWN_POST", community_key=None)
    assert plan.scope.actions == ("OWN_POST", "REPLY") and plan.scope.communities == ()


def test_one_click_signs_every_account_of_the_project_the_platform_matrix_lets_write() -> None:
    """The founder's first real use: an X and a Reddit account, one consent for both."""
    x = _acct("X", "demo_x")
    reddit_two = _acct("REDDIT", "u_second")
    hacker_news = _acct("HACKER_NEWS", "hn_reader")  # reads, does not comment
    linkedin = _acct("LINKEDIN", "li_person")  # no integration at all
    paused = _acct("REDDIT", "u_paused", "PAUSED")  # the person stopped it
    quarantined = _acct("X", "x_quarantined", "QUARANTINED")
    assigned = (DRAFT, x, reddit_two, hacker_news, linkedin, paused, quarantined)

    plan = _plan(None, assigned=assigned)

    assert plan.kind == CONSENT_SIGN and plan.mode == "REVIEW"
    assert set(plan.scope.accounts) == {ACCOUNT, x.account_id, reddit_two.account_id}
    assert {a.account_id for a in plan.accounts} == set(plan.scope.accounts)
    # Named to the person, in one fixed order whichever draft was clicked: platform, then login.
    assert plan.to_json()["konta"] == [
        {"platforma": "REDDIT", "konto": "u_draft"},
        {"platforma": "REDDIT", "konto": "u_second"},
        {"platforma": "X", "konto": "demo_x"},
    ]
    # The pace did not move: the limits bind the whole project, so more accounts is not more speed.
    assert plan.to_json()["limity"] == {"godzina": 1, "doba": 6}
    # The same plan from the X draft names the same accounts.
    from_x = _plan(None, account=x, assigned=assigned)
    assert from_x.scope.accounts == plan.scope.accounts and from_x.accounts == plan.accounts


def test_the_accounts_of_a_plan_come_from_the_assigned_rows_and_the_draft_and_nothing_else() -> (
    None
):
    stranger = uuid4()
    plan = _plan(None, assigned=(_acct("X", "demo_x"),))
    assert stranger not in plan.scope.accounts
    assert len(plan.scope.accounts) == 2
    # With no rows at all the click still covers the draft it was pressed for.
    assert _plan(None).scope.accounts == (ACCOUNT,)
    # A row listed twice is one account.
    x = _acct("X", "demo_x")
    assert len(_plan(None, assigned=(x, x, DRAFT)).scope.accounts) == 2


def test_an_own_post_covers_the_accounts_that_can_post_and_a_reply_those_that_can_reply(
    monkeypatch,
) -> None:
    from control_plane.workspace.platforms import PlatformSupport

    replies_only = PlatformSupport(
        platform="FAKE",
        label="Fake",
        read=True,
        comment=True,
        own_post=False,
        effect_readback=False,
        audience_metrics=False,
    )
    real = card_consent.support_for
    monkeypatch.setattr(
        card_consent, "support_for", lambda name: replies_only if name == "FAKE" else real(name)
    )
    fake = _acct("FAKE", "fake_login")
    both = _acct("X", "demo_x")
    assigned = (DRAFT, fake, both)

    reply = _plan(None, assigned=assigned, action="REPLY")
    assert fake.account_id in reply.scope.accounts and both.account_id in reply.scope.accounts
    post = _plan(None, assigned=assigned, action="OWN_POST", community_key=None)
    assert fake.account_id not in post.scope.accounts, "ta platforma nie umie własnego posta"
    assert set(post.scope.accounts) == {ACCOUNT, both.account_id}
    assert post.scope.actions == ("OWN_POST", "REPLY")


def test_the_draft_is_always_covered_and_a_stopped_account_is_left_alone() -> None:
    # The account the click is FOR stays in, whatever the matrix or its state say: the click can
    # always send what it was pressed for (the publisher still checks the account before writing).
    odd = _acct("HACKER_NEWS", "hn_draft", "PAUSED")
    covered = eligible_accounts((_acct("X", "demo_x"),), draft=odd, actions=("REPLY",))
    assert odd in covered and len(covered) == 2
    stopped = [_acct("X", "a", "PAUSED"), _acct("REDDIT", "b", "QUARANTINED")]
    assert eligible_accounts(stopped, draft=DRAFT, actions=("REPLY",)) == (DRAFT,)
    # READY, PROVISIONED and REAUTH_REQUIRED are not stops: the dashboard offers them all.
    others = [
        _acct("X", f"x{i}", state)
        for i, state in enumerate(("READY", "PROVISIONED", "REAUTH_REQUIRED", "VERIFYING"))
    ]
    assert len(eligible_accounts(others, draft=DRAFT, actions=("REPLY",))) == 5


def test_the_token_basis_binds_the_accounts_the_person_was_shown() -> None:
    """An account assigned between "Wyślij" and "Tak, wyślij" must not be signed in unseen: the
    basis changes, the send is refused as `ZAKRES_ZMIENIONY`, and the card asks again."""
    x = _acct("X", "demo_x")
    later = _acct("REDDIT", "u_later")
    before = _plan(None, assigned=(DRAFT, x))
    after = _plan(None, assigned=(DRAFT, x, later))
    assert before.basis() != after.basis()
    # Same accounts in another order, or a clock that moved: the same basis.
    assert before.basis() == _plan(None, assigned=(x, DRAFT)).basis()
    assert (
        before.basis() == _plan(None, assigned=(DRAFT, x), now=NOW + timedelta(minutes=2)).basis()
    )
    old = _delegation()
    assert _plan(old, assigned=(DRAFT, x)).basis() != _plan(old, assigned=(DRAFT, x, later)).basis()


@pytest.mark.parametrize(
    "delegation",
    [
        _delegation(expired=True, expires_at=NOW - timedelta(hours=1)),
        _delegation(signer_active=False),
    ],
    ids=["expired", "signer gone"],
)
def test_a_scope_that_governs_nothing_is_signed_again_and_closed(delegation) -> None:
    plan = _plan(delegation)
    assert plan.kind == CONSENT_SIGN and plan.replaces == delegation.id
    assert plan.scope.accounts == (ACCOUNT,), "nic nie dziedziczy się z wygasłego podpisu"


def test_a_live_scope_that_lists_the_account_just_sends() -> None:
    delegation = _delegation(scope=Scope(accounts=(ACCOUNT,), communities=(), actions=("REPLY",)))
    plan = _plan(delegation)
    assert plan.kind == CONSENT_SEND and not plan.signs and plan.replaces is None
    assert plan.basis() == f"WYSLIJ:{delegation.id}", "wysyłka nic nie podpisuje: bez listy kont"
    assert plan.accounts == () and plan.to_json()["konta"] == []
    # Dashboard parity: the approve route asks only whether the scope is live, not its mode.
    assert _plan(_delegation(mode="AUTONOMOUS", scope=delegation.scope)).kind == CONSENT_SEND
    assert _plan(_delegation(mode="RESEARCH", scope=delegation.scope)).kind == CONSENT_SEND


def test_a_live_review_scope_without_the_account_is_widened_by_a_new_signature() -> None:
    old = _delegation()
    plan = _plan(old)
    assert plan.kind == CONSENT_WIDEN and plan.signs and plan.replaces == old.id
    assert plan.mode == "REVIEW" and plan.budget == old.budget, "limity i tryb zostają"
    assert set(plan.scope.accounts) == {ACCOUNT, OTHER_ACCOUNT}
    assert set(plan.scope.communities) == {"r/Other", "r/SaaS"} and plan.scope.actions == ("REPLY",)
    assert plan.expires_at == old.expires_at, "poszerzenie nie przedłuża tego, co podpisano"
    assert plan.basis().startswith(f"ROZSZERZ:{old.id}:")


def test_widening_adds_every_eligible_account_the_old_scope_lacks_and_names_them() -> None:
    """A scope signed for one account (by an older card, or in Obecność) becomes the project's."""
    x = _acct("X", "demo_x")
    stopped = _acct("X", "x_paused", "PAUSED")
    unsupported = _acct("LINKEDIN", "li")
    known = _acct("REDDIT", "u_known")
    old = _delegation(scope=Scope(accounts=(known.account_id,), communities=(), actions=("REPLY",)))

    plan = _plan(old, assigned=(known, DRAFT, x, stopped, unsupported))

    assert plan.kind == CONSENT_WIDEN
    assert set(plan.scope.accounts) == {known.account_id, ACCOUNT, x.account_id}
    assert [a.handle for a in plan.accounts] == ["u_draft", "demo_x"], "tylko dodawane, nazwane"
    assert plan.to_json()["konta"] == [
        {"platforma": "REDDIT", "konto": "u_draft"},
        {"platforma": "X", "konto": "demo_x"},
    ]
    assert plan.budget == old.budget and plan.mode == "REVIEW"


def test_a_widened_scope_close_to_its_end_gets_the_default_term() -> None:
    plan = _plan(_delegation(expires_at=NOW + timedelta(minutes=20)))
    assert plan.expires_at == NOW + timedelta(days=7)


@pytest.mark.parametrize("mode", ["AUTONOMOUS", "RESEARCH"])
def test_a_click_never_adds_an_account_to_a_scope_that_is_not_review(mode) -> None:
    with pytest.raises(CardConsentRefused) as refused:
        _plan(_delegation(mode=mode))
    assert refused.value.code == REFUSED_MODE and mode in refused.value.detail


def test_a_pause_is_a_stop_and_a_click_does_not_lift_it() -> None:
    with pytest.raises(CardConsentRefused) as refused:
        _plan(_delegation(paused_at=NOW - timedelta(hours=1)))
    assert refused.value.code == REFUSED_PAUSED and "Wznów" in refused.value.detail
    # ...even for an account the scope lists: the person stopped the project, not an account.
    covered = _delegation(
        paused_at=NOW - timedelta(hours=1),
        scope=Scope(accounts=(ACCOUNT,), communities=(), actions=("REPLY",)),
    )
    with pytest.raises(CardConsentRefused):
        _plan(covered)


def test_a_paused_scope_stays_a_stop_even_when_its_signer_is_gone() -> None:
    with pytest.raises(CardConsentRefused) as refused:
        _plan(_delegation(paused_at=NOW - timedelta(hours=1), signer_active=False))
    assert refused.value.code == REFUSED_PAUSED
    # An EXPIRED pause governs nothing: the scope itself ended, and a fresh signature replaces it.
    expired = _delegation(paused_at=NOW - timedelta(days=2), expired=True)
    assert _plan(expired).kind == CONSENT_SIGN


def test_the_sentences_of_the_consent_have_no_long_dash() -> None:
    for delegation in (_delegation(mode="AUTONOMOUS"), _delegation(paused_at=NOW)):
        with pytest.raises(CardConsentRefused) as refused:
            _plan(delegation)
        assert not LONG_DASHES.search(refused.value.detail)


# ---------------------------------------------------------------------------
# The card, and the files of this feature, hold the founder's rules
# ---------------------------------------------------------------------------


def test_the_card_says_what_one_click_does_in_the_words_the_founder_chose() -> None:
    from control_plane.tools import apps_ui

    html = apps_ui.OPPORTUNITIES_HTML
    for words in (
        "Wyślij z <b>",  # the second step: "Wyślij z u/X?"
        "Tak, wyślij",
        "Wenext może pisać z ",  # the consent: "... z @a i u/b."
        "Każdą odpowiedź pokażę Ci przed wysłaniem.",
        "Każdą następną też pokażę Ci przed wysłaniem.",
        "Czekam, aż poprzednia odpowiedź wyjdzie...",
        "Spróbuj ponownie",
        "W kolejce publikatora",
        "Opublikowano.",
        "Zobacz odpowiedź",
        "Otwórz Obecność",
        "Zatwierdź w Pokoju",
    ):
        assert words in html, words
    # The same sentences in English, for the public launch (v5): the dictionary is in the card.
    for words in (
        "Send from <b>",
        "Yes, send",
        "Wenext can write from ",
        "I'll show you every reply before it goes out.",
        "I'll show you every next one before it goes out.",
        "Waiting for the previous reply to go out...",
        "Try again",
        "Queued.",
        "Posted.",
        "Open Presence",
        "Approve in Room",
    ):
        assert words in html, words
    assert apps_ui.OPPORTUNITIES_URI == "ui://wenext/okazje-v8.html"
    assert '"wenext/potwierdzenie"' in html, "karta czyta potwierdzenie z `_meta`, nie z tekstu"
    # "Opublikowano" is said only on the word of the publisher; a queued request is not a post.
    assert 'w.status === "PUBLISHED"' in html
    # The send is started by a button of the card and by nothing else: no timer, no message from
    # the host and no `ask()` calls `sendAction`, which only the click handler does.
    assert len(re.findall(r"sendAction\(", html)) == 2, "definicja i jedno wywołanie, z kliknięcia"
    assert 'document.addEventListener("click"' in html


@pytest.mark.parametrize(
    "name",
    [
        "control_plane/tools/card_send.py",
        "control_plane/presence/card_consent.py",
        "control_plane/presence/human_approval.py",
        "control_plane/tools/apps_ui.py",
        "tests/test_card_send_unit.py",
        "tests/test_card_send_db.py",
    ],
)
def test_the_files_of_this_feature_have_no_long_dash(name: str) -> None:
    text = (CONTROL_PLANE / name).read_text(encoding="utf-8")
    assert not LONG_DASHES.search(text), f"{name}: długa pauza"
