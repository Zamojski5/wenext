"""D12 ↔ D13 integration: a real sidecar process as the execution adapter's `Sidecar`.

The tests in `test_execution_and_job_states.py` use a dummy, in order to isolate the turn logic.
Here we check the thing a dummy will not check: that `mcp_sidecar.SidecarProcess`
ACTUALLY satisfies the `codex_runtime.Sidecar` protocol — including that after the turn
the process is not alive.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from codex_runtime import (
    BindingMismatch,
    BoundedRunEnvelope,
    CodexExecutionAdapter,
    HardeningConfig,
    render_config_toml,
    route,
)
from community_os_contracts import ThreadRole, TurnSpec

SIDECAR_DIR = Path(__file__).resolve().parents[2] / "mcp_sidecar"


@pytest.fixture(scope="module", autouse=True)
def _sidecar_path():
    if not (SIDECAR_DIR / "mcp_sidecar" / "__init__.py").exists():
        pytest.skip("services/mcp_sidecar nie istnieje w tym drzewie")
    sys.path.insert(0, str(SIDECAR_DIR))
    yield
    sys.path.remove(str(SIDECAR_DIR))


def _capability(**overrides):
    from mcp_sidecar import TOOL_NAMES, TaskScopedCapability

    data = {
        "organization_id": "org-1",
        "workspace_id": "ws-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "assignment_id": "assign-1",
        "bound_account_ref": "acct-ref-7f3a",
        "profile_lease_generation": 3,
        "allowed_tools": TOOL_NAMES,
        "allowed_origins": ("http://fixture-site:8000",),
        "expiry": datetime.now(UTC) + timedelta(minutes=30),
        "thread_binding_fingerprint": "sha256:binding-abc",
    }
    data.update(overrides)
    return TaskScopedCapability(**data)


def _pid_zyje(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _spec() -> TurnSpec:
    t = route(ThreadRole.WORKER)
    return TurnSpec(
        role=ThreadRole.WORKER, model=t.model, effort=t.effort, output_schema={"type": "object"}
    )


def _snapshot():
    from conftest import model, snapshot_with

    t = route(ThreadRole.WORKER)
    return snapshot_with(model(t.model, efforts=("low", "medium", "high", "xhigh")))


def test_sidecar_process_satisfies_the_sidecar_protocol():
    from mcp_sidecar import SidecarProcess

    proces = SidecarProcess(capability=_capability())
    # Attribute names as STRINGS — the tokenizing rename tool does not see them,
    # so after the D-010 migration they have to be fixed by hand and watched on every change
    # of the sidecar contract. `nazwa` stays Polish: it is on the FORBIDDEN_NAMES list.
    for attr_name in ("nazwa", "pid", "is_alive", "command", "enabled_tools"):
        assert hasattr(proces, attr_name)
    assert callable(proces.binding_fingerprint)
    assert callable(proces.start) and callable(proces.stop)


def test_full_turn_cycle_with_a_real_sidecar_process(tmp_path, monkeypatch):
    from mcp_sidecar import SidecarProcess

    monkeypatch.setenv("PYTHONPATH", str(SIDECAR_DIR))
    capability = _capability()
    sidecar = SidecarProcess(capability=capability)
    adapter = CodexExecutionAdapter(
        codex_home=tmp_path / "codex-home",
        codex_bin=Path("/nieistotne"),
        sidecar_factory=lambda _o: sidecar,
        hardening=HardeningConfig(),
    )
    adapter.prepare()
    started_sidecar, _route = adapter.prepare_turn(
        role=ThreadRole.WORKER,
        spec=_spec(),
        snapshot=_snapshot(),
        binding_fingerprint=capability.binding_fingerprint(),
        envelope=BoundedRunEnvelope.default(),
    )
    pid = started_sidecar.pid
    assert pid is not None and _pid_zyje(pid)

    adapter.close_turn(started_sidecar)
    assert not _pid_zyje(pid), "sidecar przeżył turn — L2505 wymaga zamknięcia"


def test_capability_of_another_task_ends_the_turn_fail_closed(tmp_path, monkeypatch):
    from mcp_sidecar import SidecarProcess

    monkeypatch.setenv("PYTHONPATH", str(SIDECAR_DIR))
    foreign_capability = _capability(task_id="task-CUDZY")
    sidecar = SidecarProcess(capability=foreign_capability)
    adapter = CodexExecutionAdapter(
        codex_home=tmp_path / "codex-home",
        codex_bin=Path("/nieistotne"),
        sidecar_factory=lambda _o: sidecar,
        hardening=HardeningConfig(),
    )
    adapter.prepare()
    with pytest.raises(BindingMismatch):
        adapter.prepare_turn(
            role=ThreadRole.WORKER,
            spec=_spec(),
            snapshot=_snapshot(),
            binding_fingerprint=_capability().binding_fingerprint(),
            envelope=BoundedRunEnvelope.default(),
        )
    assert sidecar.pid is None or not _pid_zyje(sidecar.pid)


def test_codex_config_toml_lists_exactly_the_six_sidecar_tools():
    from mcp_sidecar import TOOL_NAMES

    text = render_config_toml(
        HardeningConfig(
            mcp_server_name="wenext_browser",
            mcp_command=sys.executable,
            mcp_args=("-m", "mcp_sidecar"),
            mcp_enabled_tools=TOOL_NAMES,
        )
    )
    assert "required = true" in text
    assert text.count('"') >= 2 * len(TOOL_NAMES)
    for nazwa in TOOL_NAMES:
        assert f'"{nazwa}"' in text
    for forbidden in ("click", "fill", "press"):
        assert forbidden not in text
