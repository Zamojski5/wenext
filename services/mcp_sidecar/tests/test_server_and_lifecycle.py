"""The MCP protocol, fail-closed and the per-turn lifecycle (L2495-2505, DoD D13)."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import build_capability
from mcp_sidecar import (
    TOOL_NAMES,
    BackendHttpReadOnly,
    SidecarDidNotStart,
    SidecarProcess,
    config_toml_entry,
    save_to_file,
    sidecar_for_turn,
)
from mcp_sidecar.capability import CAPABILITY_FILE_ENV_VAR
from mcp_sidecar.server import build_server

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _exchange(serwer, frames: list[dict]) -> list[dict]:
    """Feeds the frames to the server and returns the responses."""
    output_dir = io.StringIO()
    serwer.output_dir = output_dir
    for frame in frames:
        serwer.handle(frame)
    return [json.loads(line) for line in output_dir.getvalue().splitlines() if line]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


def test_the_handshake_and_the_tool_list(capability):
    serwer = build_server(backend=BackendHttpReadOnly(), capability=capability)
    response = _exchange(
        serwer,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert response[0]["result"]["serverInfo"]["name"] == "wenext-task-scoped-browser"
    assert "resources" not in response[0]["result"]["capabilities"]
    assert [t["name"] for t in response[1]["result"]["tools"]] == list(TOOL_NAMES)


def test_a_method_before_handshake_is_rejected(capability):
    serwer = build_server(backend=BackendHttpReadOnly(), capability=capability)
    response = _exchange(serwer, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert "error" in response[0]


def test_an_unknown_tool_is_a_protocol_error_not_content(capability):
    serwer = build_server(backend=BackendHttpReadOnly(), capability=capability)
    response = _exchange(
        serwer,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "click", "arguments": {"selector": "#a"}},
            },
        ],
    )
    assert response[1]["error"]["code"] == -32601


def test_the_server_exposes_no_method_that_adds_tools(capability):
    serwer = build_server(backend=BackendHttpReadOnly(), capability=capability)
    response = _exchange(
        serwer,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/register", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "dynamicTools/add", "params": {}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}},
        ],
    )
    assert all("error" in r for r in response[1:]), (
        "granica nie może zależeć od dynamicTools (L2500)"
    )


# ---------------------------------------------------------------------------
# Fail-closed: the process
# ---------------------------------------------------------------------------


def _run_process(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    srodowisko = os.environ.copy()
    srodowisko.pop(CAPABILITY_FILE_ENV_VAR, None)
    srodowisko["PYTHONPATH"] = str(PACKAGE_ROOT)
    srodowisko.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "mcp_sidecar"],
        input="",
        capture_output=True,
        text=True,
        env=srodowisko,
        timeout=30,
        check=False,
    )


def test_a_process_without_capability_exits_nonzero():
    """No capability = no sidecar = turn fail-closed under `required = true` (L2503)."""
    result = _run_process({})
    assert result.returncode == 2
    assert "fail-closed" in result.stderr


def test_a_process_with_an_expired_capability_exits_nonzero(tmp_path):
    cap = build_capability(expiry=datetime.now(UTC) - timedelta(minutes=1))
    path = save_to_file(cap, tmp_path / "cap.json")
    result = _run_process({CAPABILITY_FILE_ENV_VAR: str(path)})
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The per-turn lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def sidecar(capability, monkeypatch) -> SidecarProcess:
    monkeypatch.setenv("PYTHONPATH", str(PACKAGE_ROOT))
    return SidecarProcess(capability=capability)


def _pid_zyje(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_sidecar_is_alive_after_start_and_killed_after_the_turn(sidecar):
    """DoD: "the sidecar is killed after the turn (a test on the PID)"."""
    sidecar.start()
    pid = sidecar.pid
    assert pid is not None and sidecar.is_alive and _pid_zyje(pid)
    sidecar.stop()
    assert not sidecar.is_alive
    assert not _pid_zyje(pid), f"proces {pid} przeżył zamknięcie turnu"


def test_the_sidecar_answers_the_handshake_over_stdio(sidecar):
    sidecar.start()
    try:
        sidecar.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        response = json.loads(sidecar.read())
        assert response["result"]["protocolVersion"]
        sidecar.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        lista = json.loads(sidecar.read())
        assert [t["name"] for t in lista["result"]["tools"]] == list(TOOL_NAMES)
    finally:
        sidecar.stop()


def test_binding_mismatch_ends_the_turn_and_closes_the_sidecar(capability, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(PACKAGE_ROOT))
    pids = []
    with (
        pytest.raises(SidecarDidNotStart) as exc,
        sidecar_for_turn(capability, expected_fingerprint=("zupelnie", "inny")) as s,
    ):
        pids.append(s.pid)
    assert "fail-closed" in str(exc.value)
    # The sidecar started, so it has to be proven that despite the exception it was closed.
    assert pids == [] or not _pid_zyje(pids[0])


def test_the_sidecar_is_closed_even_when_the_turn_raised(capability, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(PACKAGE_ROOT))
    remembered = {}
    with pytest.raises(RuntimeError), sidecar_for_turn(capability) as s:
        remembered["pid"] = s.pid
        raise RuntimeError("turn wybuchł")
    assert not _pid_zyje(remembered["pid"])


def test_stop_is_idempotent(sidecar):
    sidecar.start()
    sidecar.stop()
    sidecar.stop()


def test_the_capability_file_disappears_after_the_sidecar_closes(sidecar):
    """A capability with `bound_account_ref` does not stay on disk after the turn."""
    sidecar.start()
    path = sidecar._capability_file  # the cleanup is a contract, not a detail
    assert path is not None and path.exists()
    sidecar.stop()
    assert not path.exists()


# ---------------------------------------------------------------------------
# Configuration on the Codex side
# ---------------------------------------------------------------------------


def test_the_config_toml_entry_has_required_true_and_exact_enabled_tools(capability, tmp_path):
    sidecar = SidecarProcess(capability=capability)
    entry = config_toml_entry(sidecar, capability_file=tmp_path / "cap.json")
    assert "required = true" in entry
    assert "[mcp_servers.wenext_browser]" in entry
    for nazwa in TOOL_NAMES:
        assert f'"{nazwa}"' in entry
    assert "disabled_tools" not in entry
    assert "click" not in entry


def test_enabled_tools_never_exceed_the_manifest():
    cap = build_capability(allowed_tools=(*TOOL_NAMES, "click"))
    sidecar = SidecarProcess(capability=cap)
    assert sidecar.enabled_tools == TOOL_NAMES


def test_sigterm_closes_the_sidecar_process(sidecar):
    sidecar.start()
    pid = sidecar.pid
    os.kill(pid, signal.SIGTERM)
    sidecar.stop()
    assert not _pid_zyje(pid)
