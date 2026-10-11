"""Fixed model/effort routes, four isolated roles and fail-closed resume (13.4, L5266)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from codex_runtime import (
    ROUTER_POLICY_VERSION,
    ROUTES,
    VERIFIER_CONTEXT_WHITELIST,
    AppServerClient,
    RoleMismatch,
    RouteMismatch,
    RouteUnavailable,
    ThreadRegistry,
    check_reroute,
    route,
    threads_are_disjoint,
    validate_all_routes,
    validate_route,
    verifier_context,
)
from codex_runtime.routing import WIRE_EFFORTS, env_mismatch
from community_os_contracts import ResumeRefused, ThreadRole, ThreadSecurityBinding
from conftest import advance_to_authenticated, model, snapshot_with

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_every_role_has_exactly_one_recorded_route():
    assert set(ROUTES) == set(ThreadRole)
    assert len(ROUTES) == 4


def test_efforts_are_wire_values_not_ui_names():
    for t in ROUTES.values():
        assert t.effort in WIRE_EFFORTS
    for ui_name in ("Light", "Extra High", "Medium"):
        assert ui_name not in {t.effort for t in ROUTES.values()}


def test_route_with_a_ui_effort_is_rejected_at_construction():
    from codex_runtime.routing import Route

    with pytest.raises(ValueError, match="wartością drutową"):
        Route(model="gpt-5.6-terra", effort="Extra High", uzasadnienie="test")


def test_unavailable_model_is_rejected_before_the_turn():
    snapshot = snapshot_with(model("zupelnie-inny-model"))
    with pytest.raises(RouteUnavailable, match="nie istnieje w katalogu"):
        validate_route(ThreadRole.WORKER, snapshot)


def test_unavailable_effort_is_rejected_before_the_turn():
    t = route(ThreadRole.PLANNER)
    snapshot = snapshot_with(model(t.model, efforts=("low", "medium")))
    with pytest.raises(RouteUnavailable, match="nie wspiera effortu"):
        validate_route(ThreadRole.PLANNER, snapshot)


def test_incomplete_snapshot_blocks_routing():
    snapshot = snapshot_with(model("x"), complete=False)
    with pytest.raises(RouteUnavailable, match="WAITING_CAPABILITY"):
        validate_route(ThreadRole.WORKER, snapshot)


def test_all_four_routes_validate_against_the_real_catalog(adapter):
    """The catalog from a live `model/list` — no account, because the model list needs no login."""
    advance_to_authenticated(adapter)
    snapshot = adapter.sync_model_catalog()
    problems = validate_all_routes(snapshot)
    assert problems == {}, f"trasy niewykonalne na przypiętej wersji: {problems}"


def test_reroute_to_another_model_blocks_the_commit():
    t = route(ThreadRole.WORKER)
    with pytest.raises(RouteMismatch, match="commit zablokowany"):
        check_reroute(t, effective_model="inny-model", effort_observable=True)


def test_unobservable_effort_blocks_the_commit():
    t = route(ThreadRole.WORKER)
    with pytest.raises(RouteMismatch, match="UNOBSERVED"):
        check_reroute(t, effective_model=t.model, effort_observable=False)


def test_matching_reroute_does_not_raise():
    t = route(ThreadRole.WORKER)
    check_reroute(t, effective_model=t.model, effort_observable=True)


def test_compose_env_vars_must_match_the_route_table(monkeypatch):
    default = ROUTES[ThreadRole.WORKER]
    monkeypatch.setenv("WENEXT_CODEX_MODEL", default.model)
    monkeypatch.setenv("WENEXT_CODEX_EFFORT", default.effort)
    assert env_mismatch() is None
    monkeypatch.setenv("WENEXT_CODEX_MODEL", "gpt-cos-innego")
    assert "WENEXT_CODEX_MODEL" in env_mismatch()


def test_router_policy_version_is_recorded():
    assert ROUTER_POLICY_VERSION


# ---------------------------------------------------------------------------
# Four isolated roles
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(client: AppServerClient) -> ThreadRegistry:
    return ThreadRegistry(
        client=client,
        codex_connection_id=uuid4(),
        workspace_id=uuid4(),
        tool_manifest_version="mcp-sidecar-1",
        security_policy_version="f0-1",
        principal_generation=1,
    )


def test_four_roles_have_separate_thread_ids_and_bindings(registry: ThreadRegistry):
    """DoD D12: "the four thread roles have separate thread_id and a separate binding"."""
    threads = registry.start_all_roles()
    assert threads_are_disjoint(threads)
    assert len({outcome.binding.id for outcome in threads.values()}) == 4
    assert {outcome.role for outcome in threads.values()} == set(ThreadRole)
    for outcome in threads.values():
        assert outcome.binding.thread_id == outcome.thread_id
        assert outcome.binding.session_id == outcome.session_id


def test_one_role_cannot_get_two_threads(registry: ThreadRegistry):
    registry.start(ThreadRole.WORKER)
    with pytest.raises(ValueError, match="L2265"):
        registry.start(ThreadRole.WORKER)


def test_resume_with_a_matching_binding_passes_authorization(registry: ThreadRegistry):
    """A matching binding is NOT refused by the authorization layer.

    The runtime refuses separately and for another reason: a fresh thread with no executed turn
    has no rollout yet, so `thread/resume` answers "no rollout found for thread
    id ...". This is a runtime fact measured on the pinned binary, not a failure of the security
    rule — and that is why the test distinguishes these two errors instead of catching `Exception`.
    """
    from codex_runtime import RemoteError

    thread = registry.start(ThreadRole.WORKER)
    requested = thread.binding.model_copy(update={"id": uuid4()})
    with pytest.raises(RemoteError) as exc:
        registry.resume(thread.binding, requested, active_principal_generation=1)
    assert "no rollout found" in exc.value.message
    assert not isinstance(exc.value, ResumeRefused)


@pytest.mark.live
def test_resume_after_a_completed_turn(registry: ThreadRegistry):
    """Requires a ChatGPT account: only an executed turn creates a rollout that can be resumed."""
    thread = registry.start(ThreadRole.WORKER)
    registry.client.request(
        "turn/start", {"threadId": thread.thread_id, "input": {"type": "text", "text": "ping"}}
    )
    requested = thread.binding.model_copy(update={"id": uuid4()})
    resumed = registry.resume(thread.binding, requested, active_principal_generation=1)
    assert resumed.thread_id == thread.thread_id


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": uuid4()},
        {"tool_manifest_version": "mcp-sidecar-2"},
        {"security_policy_version": "f0-2"},
        {"principal_generation": 2},
        {"social_account_id": uuid4()},
        {"codex_connection_id": uuid4()},
    ],
)
def test_any_binding_difference_ends_resume_fail_closed(registry: ThreadRegistry, change):
    thread = registry.start(ThreadRole.WORKER)
    requested = thread.binding.model_copy(update={"id": uuid4(), **change})
    with pytest.raises(ResumeRefused):
        registry.resume(
            thread.binding,
            requested,
            active_principal_generation=change.get("principal_generation", 1),
        )


def test_resume_across_roles_is_rejected(registry: ThreadRegistry):
    thread = registry.start(ThreadRole.WORKER)
    requested = thread.binding.model_copy(update={"id": uuid4(), "role": ThreadRole.VERIFIER})
    with pytest.raises(RoleMismatch):
        registry.resume(thread.binding, requested, active_principal_generation=1)


def test_inactive_principal_generation_ends_resume_fail_closed(registry: ThreadRegistry):
    thread = registry.start(ThreadRole.WORKER)
    requested = thread.binding.model_copy(update={"id": uuid4()})
    with pytest.raises(ResumeRefused, match="Principal generation"):
        registry.resume(thread.binding, requested, active_principal_generation=99)


def test_a_different_automation_run_id_is_not_a_conflict(registry: ThreadRegistry):
    """L2266: a new run may have a different `automation_run_id` without a false conflict.

    `automation_run_id` lives in `CodexRunThread`, not in the binding — so changing it has no
    way of affecting resume authorization. This test pins down that split.
    """
    registry.start(ThreadRole.PLANNER)
    assert "automation_run_id" not in ThreadSecurityBinding.model_fields
    assert "job_attempt_id" not in ThreadSecurityBinding.model_fields


# ---------------------------------------------------------------------------
# The verifier context
# ---------------------------------------------------------------------------


def test_verifier_never_sees_the_authors_private_reasoning():
    """DoD D12 / L1952 / L5313 — a test of the field whitelist."""
    author_context = {
        "wynik": "propozycja odpowiedzi",
        "zrodla": ["https://fixture-site:8000/watek/1"],
        "kryteria": ["zgodność z polityką"],
        "evidence": {"cytat": "fragment wątku"},
        "rozumowanie": "TAJNE: autor wahał się między dwiema wersjami",
        "notatki_autora": "TAJNE",
        "chain_of_thought": "TAJNE",
        "cokolwiek_nowego": "też nie powinno przejść",
    }
    trimmed = verifier_context(author_context)
    assert set(trimmed) == set(VERIFIER_CONTEXT_WHITELIST)
    assert "TAJNE" not in str(trimmed)
    assert "cokolwiek_nowego" not in trimmed


def test_whitelist_is_exactly_result_sources_criteria_evidence():
    assert frozenset({"wynik", "zrodla", "kryteria", "evidence"}) == VERIFIER_CONTEXT_WHITELIST
