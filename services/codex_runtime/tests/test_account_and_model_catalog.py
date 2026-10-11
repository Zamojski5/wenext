"""The connection lifecycle, device code login and `model/list` pagination (13.1-13.2)."""

from __future__ import annotations

import pytest
from codex_runtime import (
    AppServerClient,
    CodexAccountAdapter,
    IllegalTransition,
    RemoteError,
    SnapshotIncomplete,
)
from codex_runtime.account_adapter import TRANSITIONS
from community_os_contracts import CodexConnectionState as ConnectionState
from conftest import advance_to_authenticated

# ---------------------------------------------------------------------------
# The state machine from L2134-2141
# ---------------------------------------------------------------------------


def test_transition_table_is_exactly_the_one_from_l2134():
    expected = {
        ConnectionState.DISCONNECTED: {ConnectionState.AUTH_PENDING},
        ConnectionState.AUTH_PENDING: {
            ConnectionState.AUTHENTICATED,
            ConnectionState.DISABLED,
            ConnectionState.DISCONNECTED,
        },
        ConnectionState.AUTHENTICATED: {ConnectionState.CAPABILITIES_SYNCING},
        ConnectionState.CAPABILITIES_SYNCING: {
            ConnectionState.READY,
            ConnectionState.REAUTH_REQUIRED,
            ConnectionState.SYNC_RETRY,
            ConnectionState.DISABLED,
        },
        ConnectionState.READY: {
            ConnectionState.CAPABILITIES_SYNCING,
            ConnectionState.RATE_LIMITED,
            ConnectionState.REAUTH_REQUIRED,
            ConnectionState.DISABLED,
        },
        ConnectionState.SYNC_RETRY: {
            ConnectionState.CAPABILITIES_SYNCING,
            ConnectionState.DISABLED,
        },
        ConnectionState.RATE_LIMITED: {
            ConnectionState.READY,
            ConnectionState.CAPABILITIES_SYNCING,
            ConnectionState.REAUTH_REQUIRED,
        },
        ConnectionState.REAUTH_REQUIRED: {ConnectionState.AUTH_PENDING},
        ConnectionState.DISABLED: {ConnectionState.DISCONNECTED},
    }
    assert {k: set(v) for k, v in TRANSITIONS.items()} == expected


def test_transition_outside_the_table_is_rejected(adapter: CodexAccountAdapter):
    with pytest.raises(IllegalTransition):
        adapter.transition_to(ConnectionState.READY)


def test_return_from_rate_limited_to_ready_only_with_a_fresh_complete_snapshot(
    adapter: CodexAccountAdapter,
):
    """L2145 — otherwise the connection has to synchronize the catalog."""
    advance_to_authenticated(adapter)
    adapter.sync_model_catalog()
    assert adapter.state is ConnectionState.READY
    adapter.mark_rate_limited()
    assert adapter.after_rate_limit_clears(snapshot_fresh=True) is ConnectionState.READY

    adapter.mark_rate_limited()
    assert (
        adapter.after_rate_limit_clears(snapshot_fresh=False)
        is ConnectionState.CAPABILITIES_SYNCING
    )


# ---------------------------------------------------------------------------
# The account
# ---------------------------------------------------------------------------


def test_account_read_without_login_returns_no_account(adapter: CodexAccountAdapter):
    """Measured: `{"account": null, "requiresOpenaiAuth": true}` — this is not an error."""
    state = adapter.read_account()
    assert state.logged_in is False
    assert state.requires_openai_auth is True
    assert state.email is None and state.plan_type is None
    assert len(state.fingerprint) == 64


def test_fingerprint_is_stable_for_the_same_connection(adapter: CodexAccountAdapter):
    assert adapter.read_account().fingerprint == adapter.read_account().fingerprint


def test_device_code_login_starts_without_an_account_and_returns_url_and_code(
    adapter: CodexAccountAdapter,
):
    """`account/login/start(chatgptDeviceCode)` works offline — completing it requires a human."""
    login_attempt = adapter.start_device_code_login()
    assert adapter.state is ConnectionState.AUTH_PENDING
    assert login_attempt.verification_url.startswith("https://")
    assert login_attempt.user_code and len(login_attempt.user_code) >= 6
    assert login_attempt.method == "chatgptDeviceCode"
    assert not login_attempt.is_expired()
    adapter.cancel_login()
    assert adapter.state is ConnectionState.DISCONNECTED


def test_rate_limits_without_an_account_fail_with_an_explicit_error(adapter: CodexAccountAdapter):
    """We do not fake the limits — no account gives an error, and not zero usage."""
    with pytest.raises(RemoteError) as exc:
        adapter.read_rate_limits()
    assert "authentication required" in exc.value.message


# ---------------------------------------------------------------------------
# The model catalog
# ---------------------------------------------------------------------------


def test_model_list_iterates_until_next_cursor_null(adapter: CodexAccountAdapter):
    advance_to_authenticated(adapter)
    snapshot = adapter.sync_model_catalog()
    assert snapshot.complete is True
    assert snapshot.page_count >= 1
    assert snapshot.content_hash.startswith("sha256:")
    assert len(snapshot.models) > 0
    assert adapter.state is ConnectionState.READY


def test_catalog_carries_supported_reasoning_efforts_per_model(adapter: CodexAccountAdapter):
    advance_to_authenticated(adapter)
    snapshot = adapter.sync_model_catalog()
    for model in snapshot.models:
        assert model.supported_reasoning_efforts, f"{model.id} bez listy effortów"
        assert model.default_reasoning_effort in model.supported_reasoning_efforts


def test_adapter_hardcodes_no_model_name():
    """The catalog comes from the backend; the code must not know the slugs up front."""
    import inspect

    from codex_runtime import account_adapter

    source = inspect.getsource(account_adapter)
    assert "gpt-5" not in source


def test_pagination_collects_every_page(adapter: CodexAccountAdapter, monkeypatch):
    """Synthetic pagination: checks that the loop walks `nextCursor`, instead of taking page 1."""
    strony = [
        {"data": [_model_json("a")], "nextCursor": "c1"},
        {"data": [_model_json("b")], "nextCursor": "c2"},
        {"data": [_model_json("c")], "nextCursor": None},
    ]
    cursors: list[str | None] = []

    def fake_request(method, params=None):
        assert method == "model/list"
        cursors.append((params or {}).get("cursor"))
        return strony.pop(0)

    monkeypatch.setattr(adapter.client, "request", fake_request)
    advance_to_authenticated(adapter)
    snapshot = adapter.sync_model_catalog()
    assert [m.id for m in snapshot.models] == ["a", "b", "c"]
    assert cursors == [None, "c1", "c2"]
    assert snapshot.page_count == 3 and snapshot.complete is True


def test_truncated_pagination_yields_an_incomplete_snapshot(
    adapter: CodexAccountAdapter, monkeypatch
):
    monkeypatch.setattr(
        adapter.client,
        "request",
        lambda *_a, **_k: {"data": [_model_json("a")], "nextCursor": "zawsze-jest-nastepna"},
    )
    advance_to_authenticated(adapter)
    snapshot = adapter.sync_model_catalog(max_pages=3)
    assert snapshot.complete is False
    assert adapter.state is ConnectionState.SYNC_RETRY
    with pytest.raises(SnapshotIncomplete):
        adapter.require_complete_snapshot()


def test_include_hidden_defaults_to_false(adapter: CodexAccountAdapter):
    assert adapter.include_hidden is False


def test_content_hash_changes_when_the_catalog_changes(adapter: CodexAccountAdapter, monkeypatch):
    monkeypatch.setattr(
        adapter.client,
        "request",
        lambda *_a, **_k: {"data": [_model_json("a")], "nextCursor": None},
    )
    advance_to_authenticated(adapter)
    first = adapter.sync_model_catalog()
    monkeypatch.setattr(
        adapter.client,
        "request",
        lambda *_a, **_k: {
            "data": [_model_json("a", efforts=("low",))],
            "nextCursor": None,
        },
    )
    drugi = adapter.sync_model_catalog()
    assert first.content_hash != drugi.content_hash


def _model_json(model_id: str, efforts: tuple[str, ...] = ("low", "medium")) -> dict:
    return {
        "id": model_id,
        "model": model_id,
        "displayName": model_id,
        "description": "",
        "hidden": False,
        "isDefault": False,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": e, "description": ""} for e in efforts],
    }


@pytest.mark.live
def test_full_chatgpt_login(adapter: CodexAccountAdapter):
    """Requires a human with a ChatGPT account and device code login enabled (D20)."""
    login_attempt = adapter.start_device_code_login()
    print(f"Otwórz {login_attempt.verification_url} i wpisz kod {login_attempt.user_code}")
    konto = adapter.wait_for_login()
    assert konto.logged_in and konto.auth_type == "chatgpt"


@pytest.mark.live
def test_rate_limits_on_a_logged_in_account(adapter: CodexAccountAdapter, client: AppServerClient):
    """The adapter MUST poll the limits, instead of falling into a retry loop."""
    del client
    limits = adapter.read_rate_limits()
    assert "rateLimits" in limits
