"""The execution adapter, the envelope, the per-turn sidecar and `WAITING_*` states (L2144, L2499-2505)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from codex_runtime import (
    BindingMismatch,
    BoundedRunEnvelope,
    CodexConnectionSupervisor,
    CodexExecutionAdapter,
    EnvelopeExceeded,
    EnvelopeUsage,
    HardeningConfig,
    LeaseTaken,
    ModeBusy,
    SidecarMissing,
    TurnFailClosed,
    evaluate_dispatch,
    route,
)
from community_os_contracts import CodexConnectionState as ConnectionState
from community_os_contracts import CodexSupervisorMode as SupervisorMode
from community_os_contracts import JobWaitingState, ThreadRole, TurnSpec
from conftest import model, snapshot_with

BINDING_FINGERPRINT = ("org", "ws", "task", "run", "assign", "acct-ref", 3, "sha256:binding")


class SidecarStub:
    """A minimal implementation of the `Sidecar` protocol — no process, for turn logic tests."""

    def __init__(
        self, *, fingerprint=BINDING_FINGERPRINT, starts_ok=True, alive_after_start=True
    ) -> None:
        self._fingerprint = fingerprint
        self._starts_ok = starts_ok
        self._alive_after_start = alive_after_start
        self.started_sidecar = False
        self.stopped = False

    nazwa = "wenext_browser"
    pid = 4242
    command = ("python", "-m", "mcp_sidecar")
    enabled_tools = ("snapshot",)

    @property
    def is_alive(self) -> bool:
        return self.started_sidecar and self._alive_after_start and not self.stopped

    def binding_fingerprint(self):
        return self._fingerprint

    def start(self) -> None:
        if not self._starts_ok:
            raise RuntimeError("sidecar padł na starcie")
        self.started_sidecar = True

    def stop(self) -> None:
        self.stopped = True


def _adapter(tmp_path: Path, sidecar) -> CodexExecutionAdapter:
    return CodexExecutionAdapter(
        codex_home=tmp_path / "codex-home",
        codex_bin=Path("/nieistotne/dla/tego/testu"),
        sidecar_factory=lambda _fingerprint: sidecar,
        hardening=HardeningConfig(),
    )


def _spec(role: ThreadRole = ThreadRole.WORKER) -> TurnSpec:
    t = route(role)
    return TurnSpec(
        role=role,
        model=t.model,
        effort=t.effort,
        output_schema={"type": "object", "properties": {"wynik": {"type": "string"}}},
    )


def _snapshot(role: ThreadRole = ThreadRole.WORKER):
    t = route(role)
    return snapshot_with(model(t.model, efforts=("low", "medium", "high", "xhigh")))


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def test_envelope_requires_positive_limits():
    with pytest.raises(ValueError, match="max_turns"):
        BoundedRunEnvelope(
            deadline=datetime.now(UTC) + timedelta(minutes=1),
            max_turns=0,
            max_tool_calls=1,
            max_output_bytes=1,
            heartbeat_s=1,
        )


def test_envelope_requires_a_timezone_aware_deadline():
    with pytest.raises(ValueError, match="strefy"):
        BoundedRunEnvelope(
            deadline=datetime.now(),  # that is the point of this test
            max_turns=1,
            max_tool_calls=1,
            max_output_bytes=1,
            heartbeat_s=1,
        )


def test_passed_deadline_stops_the_turn():
    envelope = BoundedRunEnvelope(
        deadline=datetime.now(UTC) - timedelta(seconds=1),
        max_turns=1,
        max_tool_calls=1,
        max_output_bytes=1,
        heartbeat_s=1,
    )
    with pytest.raises(EnvelopeExceeded, match="deadline"):
        EnvelopeUsage().enforce(envelope)


def test_exceeded_tool_call_limit_stops_the_turn():
    envelope = BoundedRunEnvelope.default()
    with pytest.raises(EnvelopeExceeded, match="max_tool_calls"):
        EnvelopeUsage(tool_calls=envelope.max_tool_calls + 1).enforce(envelope)


# ---------------------------------------------------------------------------
# Preparing the turn
# ---------------------------------------------------------------------------


def test_codex_home_must_be_prepared_before_the_turn(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub())
    with pytest.raises(TurnFailClosed, match="przygotuj"):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=_spec(),
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )


def test_successful_turn_starts_the_sidecar_before_the_runtime_and_closes_it_after(tmp_path):
    sidecar = SidecarStub()
    adapter = _adapter(tmp_path, sidecar)
    adapter.prepare()
    started_sidecar, t = adapter.prepare_turn(
        role=ThreadRole.WORKER,
        spec=_spec(),
        snapshot=_snapshot(),
        binding_fingerprint=BINDING_FINGERPRINT,
        envelope=BoundedRunEnvelope.default(),
    )
    assert started_sidecar.started_sidecar is True
    assert t == route(ThreadRole.WORKER)
    adapter.close_turn(started_sidecar)
    assert sidecar.stopped is True


def test_missing_sidecar_ends_the_turn_fail_closed(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub(starts_ok=False))
    adapter.prepare()
    with pytest.raises(SidecarMissing, match="L2503"):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=_spec(),
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )


def test_sidecar_that_died_after_start_ends_the_turn_fail_closed(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub(alive_after_start=False))
    adapter.prepare()
    with pytest.raises(SidecarMissing, match="natychmiast"):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=_spec(),
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )


def test_binding_mismatch_ends_the_turn_and_closes_the_sidecar(tmp_path):
    sidecar = SidecarStub(fingerprint=("zupelnie", "inny"))
    adapter = _adapter(tmp_path, sidecar)
    adapter.prepare()
    with pytest.raises(BindingMismatch, match="L2503"):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=_spec(),
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )
    assert sidecar.stopped is True


def test_spec_with_a_role_other_than_the_threads_is_a_mismatch(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub())
    adapter.prepare()
    with pytest.raises(BindingMismatch):
        adapter.prepare_turn(
            role=ThreadRole.VERIFIER,
            spec=_spec(ThreadRole.WORKER),
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )


def test_spec_requesting_a_model_other_than_the_fixed_route_is_rejected(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub())
    adapter.prepare()
    t = route(ThreadRole.WORKER)
    spec = TurnSpec(
        role=ThreadRole.WORKER,
        model=t.model,
        effort="xhigh",
        output_schema={"type": "object"},
    )
    with pytest.raises(BindingMismatch, match="zamrożone"):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=spec,
            snapshot=_snapshot(),
            binding_fingerprint=BINDING_FINGERPRINT,
            envelope=BoundedRunEnvelope.default(),
        )


def test_non_hardened_config_ends_the_turn_fail_closed(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub())
    adapter.prepare()
    with pytest.raises(TurnFailClosed, match="nie jest hardened"):
        adapter.verify_hardening({"config": {"web_search": "live", "features": {}}})


def test_sdk_config_disables_experimental_api_explicitly(tmp_path):
    """SDK 0.147.0 sets `experimental_api=True` BY DEFAULT — we have to override it."""
    from openai_codex import CodexConfig

    assert CodexConfig().experimental_api is True

    adapter = _adapter(tmp_path, SidecarStub())
    config = adapter.build_sdk_config()
    assert config.experimental_api is False
    assert config.env["CODEX_HOME"] == str(adapter.codex_home)


def test_sdk_config_neutralizes_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-cos")
    adapter = _adapter(tmp_path, SidecarStub())
    assert adapter.build_sdk_config().env["OPENAI_API_KEY"] == ""


def test_run_thread_never_fakes_the_effort(tmp_path):
    adapter = _adapter(tmp_path, SidecarStub())
    with pytest.raises(ValueError, match="udawanie wartości"):
        adapter.build_run_thread(
            binding_id=uuid4(),
            automation_run_id=uuid4(),
            job_attempt_id=uuid4(),
            attempt=1,
            prompt_version_id=uuid4(),
            router_policy_version="f0-stala-tabela-1",
            route=route(ThreadRole.WORKER),
            effective_model=None,
            effort_observable=False,
            effective_effort="medium",
            started_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# The WAITING_* states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ConnectionState.DISCONNECTED, JobWaitingState.WAITING_AUTH),
        (ConnectionState.AUTH_PENDING, JobWaitingState.WAITING_AUTH),
        (ConnectionState.REAUTH_REQUIRED, JobWaitingState.WAITING_AUTH),
        (ConnectionState.DISABLED, JobWaitingState.WAITING_AUTH),
        (ConnectionState.RATE_LIMITED, JobWaitingState.WAITING_QUOTA),
        (ConnectionState.CAPABILITIES_SYNCING, JobWaitingState.WAITING_CAPABILITY),
        (ConnectionState.SYNC_RETRY, JobWaitingState.WAITING_CAPABILITY),
        (ConnectionState.AUTHENTICATED, JobWaitingState.WAITING_CAPABILITY),
    ],
)
def test_connection_state_maps_to_a_waiting_state(state, expected):
    decision = evaluate_dispatch(state, snapshot_complete=True, sidecar_available=True)
    assert decision.can_dispatch is False
    assert decision.waiting_state is expected
    assert decision.reason


def test_ready_with_complete_snapshot_and_sidecar_may_dispatch():
    decision = evaluate_dispatch(
        ConnectionState.READY, snapshot_complete=True, sidecar_available=True
    )
    assert decision.can_dispatch is True
    assert decision.waiting_state is None


def test_missing_sidecar_gives_waiting_capability():
    decision = evaluate_dispatch(
        ConnectionState.READY, snapshot_complete=True, sidecar_available=False
    )
    assert decision.waiting_state is JobWaitingState.WAITING_CAPABILITY
    assert "L2503" in decision.reason


def test_incomplete_snapshot_gives_waiting_capability():
    decision = evaluate_dispatch(
        ConnectionState.READY, snapshot_complete=False, sidecar_available=True
    )
    assert decision.waiting_state is JobWaitingState.WAITING_CAPABILITY


# ---------------------------------------------------------------------------
# Supervisor: exclusive modes and a singleton lease
# ---------------------------------------------------------------------------


def test_modes_are_exclusive(codex_home):
    supervisor = CodexConnectionSupervisor(codex_home=codex_home)
    supervisor.transition_to(SupervisorMode.AUTH_CONTROL)
    with pytest.raises(ModeBusy):
        supervisor.transition_to(SupervisorMode.EXECUTION)
    supervisor.transition_to(SupervisorMode.STOPPED)
    supervisor.transition_to(SupervisorMode.EXECUTION)
    supervisor.stop()
    assert supervisor.mode is SupervisorMode.STOPPED


def test_a_second_live_process_cannot_take_the_lease_on_the_same_codex_home(codex_home):
    import json
    import os

    first = CodexConnectionSupervisor(codex_home=codex_home)
    first.transition_to(SupervisorMode.EXECUTION)
    # We swap the PID for another, live process — otherwise the supervisor would recognize
    # its own PID.
    first.lease_path.write_text(
        json.dumps({"pid": os.getppid(), "tryb": "EXECUTION"}), encoding="utf-8"
    )
    drugi = CodexConnectionSupervisor(codex_home=codex_home)
    with pytest.raises(LeaseTaken, match="L2159"):
        drugi.transition_to(SupervisorMode.AUTH_CONTROL)


def test_lease_of_a_dead_process_can_be_taken_over(codex_home):
    import json

    (codex_home / "wenext-runtime.lease").write_text(
        json.dumps({"pid": 2**22, "tryb": "EXECUTION"}), encoding="utf-8"
    )
    supervisor = CodexConnectionSupervisor(codex_home=codex_home)
    supervisor.transition_to(SupervisorMode.EXECUTION)
    supervisor.stop()
    assert not supervisor.lease_path.exists()
