"""The card's acts on the scope and on a draft, the half that needs no database (v9).

`tools/card_actions.py` holds the threat model. Each test here holds one layer of it:

  * the five tools are the card's: listed (with `visibility: ["app"]`) for a Codex host only, never
    for Claude Code, never for a hosted session, no such tool for any session that has no card;
  * a scope change is two steps and a token: the token is one-time, three minutes, burned by the
    first presentation (also by the wrong tool), bound to the purpose, and holds the WHOLE plan;
  * what the person may ask for is decided by pure functions of rows the server read: an empty
    scope is refused, an account outside the project is refused, limits stay inside their bounds,
    a paused scope is not replaced, only a paused one is resumed;
  * the token never reaches the trace, every refusal carries a `kod`, and a refusal that a limit
    would fix tells the model to call `room.show` instead of trying to fix it.
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
    CARD_ACTIONS,
    CARD_LIMIT_BOUNDS,
    CARD_LIMIT_DEFAULTS,
    CONSENT_PAUSE,
    CONSENT_RESUME,
    CONSENT_SEND,
    CONSENT_SET,
    CONSENT_SIGN,
    CONSENT_WIDEN,
    SIGNING_KINDS,
    CardConsentRefused,
    ConsentAccount,
    plan_limits,
    plan_pause,
    plan_resume,
    scope_state,
)
from control_plane.presence.scope import Budget, Scope
from control_plane.presence.store import DelegationRow
from control_plane.sessions.trace_payloads import TOOL_VERBS
from control_plane.tools import APP_ONLY_TOOLS, NoSuchTool, ToolRegistry, issue
from control_plane.tools.card_actions import (
    CARD_ACTION_TOOL_SPECS,
    MAX_OUTSTANDING,
    PURPOSE_PAUSE,
    PURPOSE_RESUME,
    PURPOSE_SET,
    SCOPE_HINT,
    ScopeTokens,
    scope_plan_json,
    with_scope_hint,
)
from control_plane.tools.card_send import (
    META_TOKEN,
    SECRET_ARGS,
    TOKEN_TTL,
    TokenRefused,
    redacted_args,
)
from control_plane.tools.catalog import allowed_for_host, app_manifest
from control_plane.tools.mcp_server import MCPToolServer, _Loop

CONTROL_PLANE = Path(__file__).resolve().parents[1]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
ACTION_TOOLS = {spec["name"] for spec in CARD_ACTION_TOOL_SPECS}
SCOPE_TOOLS = {"room.scope_token", "room.pause", "room.resume", "room.sign_scope"}
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The tools are the card's
# ---------------------------------------------------------------------------


def test_the_five_card_actions_are_app_only_tools_and_nothing_else() -> None:
    assert {
        "room.revise_draft",
        "room.scope_token",
        "room.pause",
        "room.resume",
        "room.sign_scope",
    } == ACTION_TOOLS
    assert ACTION_TOOLS <= APP_ONLY_TOOLS
    for name in ACTION_TOOLS:
        assert allowed_for_host(name, "codex") is True
        assert allowed_for_host(name, "codex", hosted=True) is False
        assert allowed_for_host(name, "claude-code") is False
        assert allowed_for_host(name, None) is False
        assert name in TOOL_VERBS, "zapis w śladzie ma zdanie, nie nazwę techniczną"
    assert {e["name"] for e in app_manifest("codex")} >= ACTION_TOOLS
    assert app_manifest("claude-code") == [] and app_manifest(None) == []
    assert app_manifest("codex", hosted=True) == []


def test_the_specs_are_honest_strict_and_dash_free() -> None:
    by_name = {spec["name"]: spec for spec in CARD_ACTION_TOOL_SPECS}
    # Asking writes nothing and must not be replayed from the trace (an old, dead token).
    assert "writes" not in by_name["room.scope_token"]
    for name in ("room.revise_draft", "room.pause", "room.resume", "room.sign_scope"):
        assert by_name[name]["writes"] is True
    for spec in CARD_ACTION_TOOL_SPECS:
        assert spec["state"] == "DOSTEPNE"
        assert spec["input_schema"]["additionalProperties"] is False
        assert "TYLKO DLA KARTY" in spec["summary"]
        assert not LONG_DASHES.search(spec["summary"] + spec["detail"])
    # The acting tools take a token and NOTHING else: what is done is what the token holds.
    for name in ("room.pause", "room.resume", "room.sign_scope"):
        assert set(by_name[name]["input_schema"]["properties"]) == {"token"}
        assert by_name[name]["input_schema"]["required"] == ["token"]
    assert set(by_name["room.revise_draft"]["input_schema"]["properties"]) == {
        "revision_id",
        "text",
    }


def test_the_files_have_no_long_dash() -> None:
    for name in (
        "control_plane/tools/card_actions.py",
        "control_plane/presence/human_revision.py",
        "control_plane/presence/card_consent.py",
        "tests/test_card_actions_unit.py",
        "tests/test_card_actions_db.py",
    ):
        path = CONTROL_PLANE / name
        if path.exists():
            assert not LONG_DASHES.search(path.read_text(encoding="utf-8")), name


class _StubRegistry:
    async def platform_capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, args: dict) -> dict:
        return {"stan": "WYKONANE"}


class _Sessions:
    async def append_event(self, *, organization_id, session_id, kind, payload) -> None:
        return None


def _serve(host, frames, *, hosted: bool = False) -> list[dict]:
    runner = _Loop()
    runner.start()
    out = io.StringIO()
    capability = issue(
        session_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        dsn="postgresql://nieuzywane-w-tescie",
        host=host,
        hosted=hosted,
    )
    try:
        server = MCPToolServer(
            capability=capability,
            registry=_StubRegistry(),
            runner=runner,
            sessions=_Sessions() if host else None,
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


@pytest.mark.parametrize(
    ("host", "hosted"),
    [
        (None, False),
        ("codex", False),
        ("claude-code", False),
        ("codex", True),
        ("claude-code", True),
    ],
)
def test_listed_with_visibility_app_for_codex_only(host, hosted) -> None:
    listed = _serve(host, [_INIT, _LIST], hosted=hosted)[1]["result"]["tools"]
    names = {tool["name"] for tool in listed}
    if host == "codex" and not hosted:
        for tool in listed:
            if tool["name"] in ACTION_TOOLS:
                assert tool["_meta"] == {"ui": {"visibility": ["app"]}}
        assert names >= ACTION_TOOLS
    else:
        assert not names & ACTION_TOOLS, (host, hosted)
    # And never visible to the model, whatever the host: a host that follows SEP-1865 drops them.
    model_sees = {
        tool["name"]
        for tool in listed
        if "model" in tool.get("_meta", {}).get("ui", {}).get("visibility", ["model", "app"])
    }
    assert not model_sees & ACTION_TOOLS


@pytest.mark.parametrize("host", [None, "claude-code"])
@pytest.mark.parametrize("name", sorted(ACTION_TOOLS))
async def test_no_such_tool_for_a_session_that_has_no_card(host, name) -> None:
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://nieuzywane-w-tescie",
            host=host,
        ),
        pool=None,  # type: ignore[arg-type]
    )
    with pytest.raises(NoSuchTool):
        await registry.call(name, {"revision_id": str(uuid4()), "text": "x", "token": "x"})


@pytest.mark.parametrize("name", sorted(ACTION_TOOLS))
async def test_no_such_tool_for_a_hosted_session_even_with_a_codex_host(name) -> None:
    registry = ToolRegistry(
        capability=issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn="postgresql://nieuzywane-w-tescie",
            host="codex",
            hosted=True,
        ),
        pool=None,  # type: ignore[arg-type]
    )
    with pytest.raises(NoSuchTool):
        await registry.call(name, {"revision_id": str(uuid4()), "text": "x", "token": "x"})


# ---------------------------------------------------------------------------
# The token: one-time, bound to its purpose, holds the whole plan, never in the trace
# ---------------------------------------------------------------------------


def _clock():
    state = [NOW]

    def read() -> datetime:
        return state[0]

    read.state = state  # type: ignore[attr-defined]
    return read


def _plan(**changes):
    delegation = changes.pop("delegation", None)
    account = ConsentAccount(uuid4(), "REDDIT", "u_a", "READY")
    args = {
        "assigned": [account],
        "chosen": [account.account_id],
        "actions": ["REPLY"],
        "limits": None,
        "days": None,
        "now": NOW,
    }
    return plan_limits(delegation, **{**args, **changes}), account


def test_a_token_works_once_for_its_purpose_and_holds_the_whole_plan() -> None:
    tokens = ScopeTokens(clock=_clock())
    plan, _ = _plan()
    token, expires = tokens.mint(
        purpose=PURPOSE_SET, plan=plan, args={"akcja": PURPOSE_SET}, minted_at=NOW
    )
    assert len(token) >= 40 and expires == NOW + TOKEN_TTL
    grant = tokens.redeem(token, purpose=PURPOSE_SET)
    assert grant.plan == plan and grant.minted_at == NOW
    with pytest.raises(TokenRefused) as spent:
        tokens.redeem(token, purpose=PURPOSE_SET)
    assert spent.value.code == "POTWIERDZENIE"


@pytest.mark.parametrize("wrong", [PURPOSE_PAUSE, PURPOSE_RESUME])
def test_a_token_shown_to_the_wrong_tool_is_refused_and_burned(wrong) -> None:
    tokens = ScopeTokens(clock=_clock())
    plan, _ = _plan()
    token, _ = tokens.mint(purpose=PURPOSE_SET, plan=plan, args={}, minted_at=NOW)
    with pytest.raises(TokenRefused) as refused:
        tokens.redeem(token, purpose=wrong)
    assert refused.value.code == "POTWIERDZENIE"
    with pytest.raises(TokenRefused):  # the mistake spent it
        tokens.redeem(token, purpose=PURPOSE_SET)


def test_a_wrong_an_expired_and_a_replaced_token_are_refused() -> None:
    clock = _clock()
    tokens = ScopeTokens(clock=clock)
    plan, _ = _plan()
    with pytest.raises(TokenRefused):
        tokens.redeem("not-a-token", purpose=PURPOSE_SET)
    old, _ = tokens.mint(purpose=PURPOSE_SET, plan=plan, args={}, minted_at=NOW)
    new, _ = tokens.mint(purpose=PURPOSE_SET, plan=plan, args={}, minted_at=NOW)
    with pytest.raises(TokenRefused):
        tokens.redeem(old, purpose=PURPOSE_SET)  # a new confirmation kills the old one
    clock.state[0] = NOW + TOKEN_TTL + timedelta(seconds=1)
    with pytest.raises(TokenRefused) as late:
        tokens.redeem(new, purpose=PURPOSE_SET)
    assert "wygasło" in late.value.detail


def test_only_the_latest_of_each_purpose_lives_and_the_store_is_bounded() -> None:
    tokens = ScopeTokens(clock=_clock())
    plan, _ = _plan()
    first_pause, _ = tokens.mint(purpose=PURPOSE_PAUSE, plan=plan, args={}, minted_at=NOW)
    sign, _ = tokens.mint(purpose=PURPOSE_SET, plan=plan, args={}, minted_at=NOW)
    # Different purposes do not kill each other.
    assert tokens.redeem(sign, purpose=PURPOSE_SET).purpose == PURPOSE_SET
    assert tokens.redeem(first_pause, purpose=PURPOSE_PAUSE).purpose == PURPOSE_PAUSE
    assert len((PURPOSE_PAUSE, PURPOSE_RESUME, PURPOSE_SET)) <= MAX_OUTSTANDING


def test_the_token_is_redacted_from_the_trace_for_every_acting_tool() -> None:
    for name in ("room.pause", "room.resume", "room.sign_scope", "room.send_draft"):
        assert SECRET_ARGS[name] == ("token",)
        shown = redacted_args(name, {"token": "sekret-sekret", "inne": 1})
        assert "sekret" not in json.dumps(shown) and shown["inne"] == 1
    # The question mints it, so it carries none; the revise has no secret argument.
    assert "room.scope_token" not in SECRET_ARGS and "room.revise_draft" not in SECRET_ARGS
    assert META_TOKEN == "wenext/potwierdzenie"


# ---------------------------------------------------------------------------
# What a person may ask for: pure plans over rows the server read
# ---------------------------------------------------------------------------


def _delegation(**changes) -> DelegationRow:
    base = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "mode": "REVIEW",
        "scope": Scope(
            accounts=(uuid4(),), communities=("r/SaaS", "r/startups"), actions=("REPLY",)
        ),
        "budget": Budget(publications_per_hour=3, publications_per_day=9),
        "scope_sha256": "c" * 64,
        "signed_at": NOW - timedelta(days=1),
        "signed_by": uuid4(),
        "paused_at": None,
        "revoked_at": None,
        "expires_at": NOW + timedelta(days=5),
    }
    return DelegationRow(**{**base, **changes})


def test_the_signing_kinds_are_exactly_the_ones_that_write_a_new_signature() -> None:
    assert {CONSENT_SIGN, CONSENT_WIDEN, CONSENT_SET} == SIGNING_KINDS
    assert CONSENT_SEND not in SIGNING_KINDS
    assert CONSENT_PAUSE not in SIGNING_KINDS and CONSENT_RESUME not in SIGNING_KINDS
    assert (CONSENT_PAUSE, CONSENT_RESUME, CONSENT_SET) == ("WSTRZYMAJ", "WZNOW", "USTAW")


def test_the_panel_scope_is_always_review_and_exactly_what_was_ticked() -> None:
    reddit = ConsentAccount(uuid4(), "REDDIT", "u_a", "READY")
    x = ConsentAccount(uuid4(), "X", "demo_x", "READY")
    other = ConsentAccount(uuid4(), "REDDIT", "u_other", "READY")
    old = _delegation(mode="AUTONOMOUS")
    plan = plan_limits(
        old,
        assigned=[reddit, x, other],
        chosen=[x.account_id, reddit.account_id, reddit.account_id],
        actions=["RESEARCH", "OWN_POST", "REPLY"],
        limits={"godzina": 4, "doba": 20, "wlasne_doba": 2},
        days=14,
        now=NOW,
    )
    assert plan.kind == CONSENT_SET and plan.signs and plan.mode == "REVIEW"
    assert set(plan.scope.accounts) == {reddit.account_id, x.account_id}, (
        "nie to, czego nie zaznaczono"
    )
    assert plan.scope.actions == ("OWN_POST", "REPLY", "RESEARCH")
    assert plan.scope.communities == ("r/SaaS", "r/startups"), (
        "społeczności starego podpisu zostają"
    )
    assert plan.budget == Budget(
        publications_per_day=20, publications_per_hour=4, own_posts_per_day=2
    )
    assert plan.expires_at == NOW + timedelta(days=14)
    assert plan.replaces == old.id and plan.delegation_id == old.id
    assert [a.handle for a in plan.accounts] == ["u_a", "demo_x"], "kolejność: platforma, login"
    assert plan.basis().startswith(f"USTAW:{old.id}:")
    shown = scope_plan_json(plan, previous_mode="AUTONOMOUS")
    assert shown["rodzaj"] == "USTAW" and shown["tryb"] == "REVIEW"
    assert shown["poprzedni_tryb"] == "AUTONOMOUS"
    assert shown["limity"] == {"godzina": 4, "doba": 20, "wlasne_doba": 2}
    assert shown["dzialania"] == ["OWN_POST", "REPLY", "RESEARCH"]
    assert shown["konta"] == [
        {"platforma": "REDDIT", "konto": "u_a"},
        {"platforma": "X", "konto": "demo_x"},
    ]


def test_a_first_signature_has_no_communities_and_the_default_term_and_limits() -> None:
    plan, account = _plan()
    assert plan.replaces is None and plan.delegation_id is None and plan.scope.communities == ()
    assert plan.expires_at == NOW + timedelta(days=CARD_LIMIT_DEFAULTS["dni"])
    assert plan.budget.publications_per_hour == CARD_LIMIT_DEFAULTS["godzina"]
    assert plan.budget.publications_per_day == CARD_LIMIT_DEFAULTS["doba"]
    assert plan.budget.own_posts_per_day == CARD_LIMIT_DEFAULTS["wlasne_doba"]
    assert plan.scope.accounts == (account.account_id,)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"chosen": []}, "ZAKRES_PUSTY"),
        ({"actions": []}, "ZAKRES_PUSTY"),
        ({"actions": ["WSZYSTKO"]}, "ZAKRES_PUSTY"),
        ({"limits": {"godzina": 0}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"godzina": CARD_LIMIT_BOUNDS["godzina"] + 1}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"doba": CARD_LIMIT_BOUNDS["doba"] + 1}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"wlasne_doba": 99}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"godzina": 5, "doba": 4}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"godzina": True}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"godzina": "4"}}, "LIMIT_POZA_GRANICA"),
        ({"limits": {"tygodniowo": 4}}, "LIMIT_POZA_GRANICA"),
        ({"days": 3}, "LIMIT_POZA_GRANICA"),
        ({"days": 90}, "LIMIT_POZA_GRANICA"),
    ],
)
def test_an_empty_or_out_of_bounds_request_is_refused_with_a_code(changes, code) -> None:
    with pytest.raises(CardConsentRefused) as refused:
        _plan(**changes)
    assert refused.value.code == code and refused.value.detail


def test_an_empty_scope_is_never_signed_and_the_sentence_says_what_to_tick() -> None:
    with pytest.raises(CardConsentRefused) as accounts:
        _plan(chosen=[])
    assert "konto" in accounts.value.detail
    with pytest.raises(CardConsentRefused) as actions:
        _plan(actions=[])
    assert "działanie" in actions.value.detail


def test_an_account_the_server_does_not_know_a_stopped_one_and_an_unable_one_are_refused() -> None:
    assigned = ConsentAccount(uuid4(), "REDDIT", "u_a", "READY")
    stopped = ConsentAccount(uuid4(), "X", "demo_x", "PAUSED")
    strange = ConsentAccount(uuid4(), "MASTODON", "m", "READY")
    for chosen in (uuid4(), stopped.account_id, strange.account_id):
        with pytest.raises(CardConsentRefused) as refused:
            plan_limits(
                None,
                assigned=[assigned, stopped, strange],
                chosen=[assigned.account_id, chosen],
                actions=["REPLY"],
                limits=None,
                days=None,
                now=NOW,
            )
        assert refused.value.code == "KONTO_NIEZNANE", chosen
    with pytest.raises(ValueError):  # not an id at all: the tool turns this into `ARGUMENT`
        plan_limits(
            None, assigned=[assigned], chosen=["nie-id"], actions=["REPLY"], limits=None, days=None
        )


def test_a_paused_scope_is_not_replaced_by_signing_but_an_expired_or_dead_one_is() -> None:
    paused = _delegation(paused_at=NOW - timedelta(hours=1))
    with pytest.raises(CardConsentRefused) as refused:
        _plan(delegation=paused)
    assert refused.value.code == card_consent.REFUSED_PAUSED
    assert "Wznów" in refused.value.detail, "mówi, jak wznowić"
    # Expired (even paused) and a signature whose signer is gone govern nothing: signing replaces.
    for dead in (
        _delegation(expires_at=NOW - timedelta(days=1), expired=True),
        _delegation(
            paused_at=NOW - timedelta(days=2), expires_at=NOW - timedelta(days=1), expired=True
        ),
        _delegation(signer_active=False),
    ):
        plan, _ = _plan(delegation=dead)
        assert plan.kind == CONSENT_SET and plan.replaces == dead.id


def test_a_pause_needs_a_live_scope_and_a_resume_a_paused_one() -> None:
    live = _delegation()
    assert plan_pause(live).kind == CONSENT_PAUSE and not plan_pause(live).signs
    assert plan_pause(live).delegation_id == live.id
    for gone, code in (
        (None, "BRAK_ZAKRESU"),
        (_delegation(paused_at=NOW), "NIE_AKTYWNY"),
        (_delegation(expires_at=NOW - timedelta(days=1), expired=True), "NIE_AKTYWNY"),
    ):
        with pytest.raises(CardConsentRefused) as refused:
            plan_pause(gone)
        assert refused.value.code == code

    paused = _delegation(paused_at=NOW - timedelta(hours=1), mode="AUTONOMOUS")
    named = (ConsentAccount(uuid4(), "REDDIT", "u_a", "READY"),)
    plan = plan_resume(paused, named=named)
    assert plan.kind == CONSENT_RESUME and not plan.signs
    # It comes back as it was: same signature, same mode, limits and deadline (nothing is re-signed).
    assert (plan.delegation_id, plan.mode, plan.budget, plan.expires_at, plan.replaces) == (
        paused.id,
        "AUTONOMOUS",
        paused.budget,
        paused.expires_at,
        None,
    )
    assert scope_plan_json(plan, previous_mode="AUTONOMOUS")["tryb"] == "AUTONOMOUS"
    for gone, code in (
        (None, "BRAK_ZAKRESU"),
        (live, "NIE_WSTRZYMANY"),
        (
            _delegation(paused_at=NOW, expires_at=NOW - timedelta(days=1), expired=True),
            "ZAKRES_WYGASL",
        ),
    ):
        with pytest.raises(CardConsentRefused) as refused:
            plan_resume(gone, named=())
        assert refused.value.code == code


def test_the_send_click_still_refuses_a_paused_scope_and_never_resumes_it() -> None:
    """Resuming is not reachable from `plan_consent`: the only kinds it returns are the old three."""
    paused = _delegation(paused_at=NOW - timedelta(hours=1))
    account = ConsentAccount(uuid4(), "REDDIT", "u_a", "READY")
    with pytest.raises(CardConsentRefused) as refused:
        card_consent.plan_consent(
            paused, account=account, community_key="r/SaaS", action="REPLY", now=NOW
        )
    assert refused.value.code == "ZAKRES_WSTRZYMANY"
    assert "Wznów" in refused.value.detail and "karcie" in refused.value.detail
    for kind in (CONSENT_PAUSE, CONSENT_RESUME, CONSENT_SET):
        assert kind not in {CONSENT_SEND, CONSENT_SIGN, CONSENT_WIDEN}


def test_the_scope_state_is_one_word() -> None:
    assert scope_state(None) == "BRAK"
    assert scope_state(_delegation()) == "AKTYWNY"
    assert scope_state(_delegation(paused_at=NOW)) == "WSTRZYMANY"
    assert scope_state(_delegation(expires_at=NOW - timedelta(days=1), expired=True)) == "WYGASL"
    assert (
        scope_state(_delegation(paused_at=NOW, expires_at=NOW - timedelta(days=1), expired=True))
        == "WYGASL"
    ), "wygasłego nie da się wznowić, więc wstrzymany też jest wygasły"
    assert scope_state(_delegation(signer_active=False)) == "BRAK"
    assert CARD_ACTIONS == ("REPLY", "OWN_POST", "RESEARCH")


def test_the_bounds_are_the_ones_the_contract_with_the_card_names() -> None:
    assert CARD_LIMIT_DEFAULTS == {"godzina": 2, "doba": 10, "wlasne_doba": 1, "dni": 7}
    assert CARD_LIMIT_BOUNDS == {"godzina": 10, "doba": 50, "wlasne_doba": 10}
    for key, bound in CARD_LIMIT_BOUNDS.items():
        assert 1 <= CARD_LIMIT_DEFAULTS[key] <= bound


# ---------------------------------------------------------------------------
# The agent side: a refusal that a limit would fix points the model at the card
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        {"stan": "NIEDOSTEPNE", "kod": "BUDZET", "powod": "x"},
        {"stan": "NIEDOSTEPNE", "kod": "ZAKRES_WSTRZYMANY", "powod": "x"},
        {"stan": "NIEDOSTEPNE", "kod": "ZAKRES_INNY_TRYB", "powod": "x"},
    ],
)
def test_a_scope_or_budget_refusal_tells_the_model_to_call_room_show(result) -> None:
    hinted = with_scope_hint("whatever.tool", result)
    assert "room.show" in hinted["wskazowka"] and SCOPE_HINT in hinted["wskazowka"]
    assert "nie podpisujesz" in SCOPE_HINT, "model nie ma jak podpisać i nie próbuje"
    assert hinted["kod"] == result["kod"]
    assert with_scope_hint("whatever.tool", hinted) == hinted, "nie dopisuje dwa razy"


def test_the_hint_is_added_after_a_hint_the_tool_gave_and_only_where_it_fits() -> None:
    own = {"stan": "OCZEKUJE", "powod": "x", "wskazowka": "Powiedz, co zrobisz."}
    hinted = with_scope_hint("delegation.propose", own)
    assert (
        hinted["wskazowka"].startswith("Powiedz, co zrobisz.")
        and "room.show" in hinted["wskazowka"]
    )
    # A success, an unrelated refusal and an unrelated tool are left alone.
    assert with_scope_hint("delegation.propose", {"stan": "WYKONANE"}) == {"stan": "WYKONANE"}
    plain = {"stan": "NIEDOSTEPNE", "powod": "x", "kod": "NIE_ZNALEZIONO"}
    assert with_scope_hint("draft.create", plain) == plain
    assert with_scope_hint("draft.create", {"stan": "BLAD", "powod": "x"}) == {
        "stan": "BLAD",
        "powod": "x",
    }


def test_the_instructions_to_the_agent_name_the_card_and_forbid_signing() -> None:
    from control_plane.tools.mcp_server import HOST_INSTRUCTIONS

    assert "room.show" in HOST_INSTRUCTIONS and "never sign" in HOST_INSTRUCTIONS
    assert "pomin=true" in HOST_INSTRUCTIONS and "powod" in HOST_INSTRUCTIONS
    assert not LONG_DASHES.search(HOST_INSTRUCTIONS)
