"""`SupervisorModeController` — the closed list of reasons first, the switch second.

The supervisor is a fake with the surface the controller touches (`current`, `switch_mode`,
`capsule_id`, `vnc_socket`); no Chrome, no Xtigervnc. What is measured: which requests are
refused before anything is stopped, that a switch declares `safe_stop_done=True` and passes the
reason through, that the same mode is a no-op, and that a failed start is reported, not hidden.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from browser_supervisor.control import SupervisorModeController
from browser_supervisor.reader import FetchFailed, ModeSwitchFailed, ModeSwitchRefused
from browser_supervisor.supervisor import CapsuleStartFailed
from community_os_contracts import CapsuleMode, InteractiveReason


@dataclass
class _Endpoint:
    http_base: str = "http://127.0.0.1:41000"


@dataclass
class _Process:
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


@dataclass
class _Capsule:
    mode: CapsuleMode
    reason: InteractiveReason | None = None
    display_only: bool = False
    endpoint: _Endpoint | None = field(default_factory=_Endpoint)
    process: _Process = field(default_factory=_Process)
    started_at: float = field(default_factory=time.time)

    def is_alive(self) -> bool:
        return self.process.returncode is None


class FakeSupervisor:
    capsule_id = "capsule-test"

    def __init__(self, tmp_path: Path, mode: CapsuleMode = CapsuleMode.HEADLESS) -> None:
        self.vnc_socket = tmp_path / "vnc.sock"
        self.gateway_credentials = tmp_path / "vnc-credentials"
        self.current: _Capsule | None = _Capsule(mode)
        self.switches: list[tuple[CapsuleMode, InteractiveReason | None, bool, bool]] = []
        self.start_urls: list[str | None] = []
        self.fail_with: Exception | None = None

    def switch_mode(
        self, target, *, reason=None, display_only=False, safe_stop_done=False, start_url=None
    ):
        """Mirrors the real supervisor: the gateway credentials exist only for a HUMAN launch, and
        a human launch has no DevTools endpoint (27.09, `args.HUMAN_ARGS`)."""
        self.switches.append((target, reason, display_only, safe_stop_done))
        self.start_urls.append(start_url)
        self.current = None
        self.gateway_credentials.unlink(missing_ok=True)
        if self.fail_with is not None:
            raise self.fail_with
        for_person = target is CapsuleMode.INTERACTIVE and reason is not None
        self.current = _Capsule(
            target,
            reason=reason,
            display_only=display_only,
            endpoint=None if for_person else _Endpoint("http://127.0.0.1:42000"),
        )
        if target is CapsuleMode.INTERACTIVE:
            self.vnc_socket.touch()
            if not display_only:
                self.gateway_credentials.write_text("full=x\nview_only=y\n")
        else:
            self.vnc_socket.unlink(missing_ok=True)
        return self.current


@pytest.fixture
def supervisor(tmp_path: Path) -> FakeSupervisor:
    return FakeSupervisor(tmp_path)


def test_state_describes_the_running_capsule(supervisor: FakeSupervisor) -> None:
    state = SupervisorModeController(supervisor).state()
    assert state["capsule_id"] == "capsule-test"
    assert state["mode"] == "HEADLESS" and state["reason"] is None
    assert state["alive"] is True and state["cdp"] == "http://127.0.0.1:41000"
    assert state["vnc_ready"] is False
    assert state["since"] is not None and state["uptime_s"] >= 0


def test_state_without_a_process_says_so(supervisor: FakeSupervisor) -> None:
    supervisor.current = None
    state = SupervisorModeController(supervisor).state()
    assert state["mode"] is None and state["alive"] is False and state["cdp"] is None


def test_the_endpoint_follows_the_current_launch(supervisor: FakeSupervisor) -> None:
    controller = SupervisorModeController(supervisor)
    assert controller.endpoint_http() == "http://127.0.0.1:41000"
    controller.switch("INTERACTIVE", None, display_only=True)
    assert controller.endpoint_http() == "http://127.0.0.1:42000", "po przełączeniu nowy port"
    supervisor.current = None
    with pytest.raises(FetchFailed):
        controller.endpoint_http()


def test_a_person_signing_in_leaves_nothing_to_read_through(supervisor: FakeSupervisor) -> None:
    """27.09: Google refused a sign-in in a browser launched with a DevTools endpoint. While a
    person signs in there is none, and every read says why instead of attaching."""
    controller = SupervisorModeController(supervisor)
    state = controller.switch("INTERACTIVE", "FIRST_LOGIN", start_url="https://x.com/i/flow/login")
    assert supervisor.start_urls == ["https://x.com/i/flow/login"]
    assert state["cdp"] is None and state["human_steer_open"] is True
    with pytest.raises(FetchFailed, match="człowiek loguje się"):
        controller.endpoint_http()


@pytest.mark.parametrize(
    ("mode", "reason", "fragment"),
    [
        ("HEADFUL", None, "nieznany tryb"),
        ("INTERACTIVE", None, "wymaga powodu"),
        ("INTERACTIVE", "BECAUSE", "nieznany powód"),
        ("INTERACTIVE", "SCHEDULED_PUBLISH", "nieznany powód"),
        ("HEADLESS", "FIRST_LOGIN", "nie przyjmuje powodu"),
    ],
)
def test_requests_outside_the_closed_list_are_refused_before_anything_stops(
    supervisor: FakeSupervisor, mode: str, reason: str | None, fragment: str
) -> None:
    with pytest.raises(ModeSwitchRefused, match=fragment):
        SupervisorModeController(supervisor).switch(mode, reason)
    assert supervisor.switches == [], "odmowa nie dotyka Chrome"


@pytest.mark.parametrize("reason", ["FIRST_LOGIN", "REAUTH", "MFA", "CHALLENGE", "HUMAN_TAKEOVER"])
def test_every_e1_reason_switches_to_interactive_with_safe_stop_declared(
    supervisor: FakeSupervisor, reason: str
) -> None:
    state = SupervisorModeController(supervisor).switch("INTERACTIVE", reason)
    assert supervisor.switches == [
        (CapsuleMode.INTERACTIVE, InteractiveReason(reason), False, True)
    ]
    assert state["mode"] == "INTERACTIVE" and state["reason"] == reason
    assert state["vnc_ready"] is True and state["changed"] is True
    assert state["display_only"] is False and state["human_steer_open"] is True


def test_switching_back_to_headless_takes_no_reason(supervisor: FakeSupervisor) -> None:
    controller = SupervisorModeController(supervisor)
    controller.switch("INTERACTIVE", "FIRST_LOGIN")
    state = controller.switch("HEADLESS", None)
    assert supervisor.switches[-1] == (CapsuleMode.HEADLESS, None, False, True)
    assert state["mode"] == "HEADLESS" and state["vnc_ready"] is False and state["changed"]
    assert state["human_steer_open"] is False, "po zejściu z ekranu poświadczenia gatewaya znikają"


def test_the_same_mode_is_a_no_op(supervisor: FakeSupervisor) -> None:
    state = SupervisorModeController(supervisor).switch("HEADLESS", None)
    assert supervisor.switches == []
    assert state["mode"] == "HEADLESS" and state["changed"] is False


def test_a_failed_start_is_reported_not_hidden(supervisor: FakeSupervisor) -> None:
    supervisor.fail_with = CapsuleStartFailed("Xtigervnc nie wystartował")
    with pytest.raises(ModeSwitchFailed, match="Xtigervnc"):
        SupervisorModeController(supervisor).switch("INTERACTIVE", "FIRST_LOGIN")
    assert supervisor.current is None, "po nieudanym starcie nie ma procesu — i widać to w stanie"
    assert SupervisorModeController(supervisor).state()["alive"] is False


# -- D-026: display without a human ---------------------------------------------------


def test_display_only_is_interactive_with_the_human_steer_closed(
    supervisor: FakeSupervisor,
) -> None:
    state = SupervisorModeController(supervisor).switch("INTERACTIVE", None, display_only=True)
    assert supervisor.switches == [(CapsuleMode.INTERACTIVE, None, True, True)]
    assert state["mode"] == "INTERACTIVE" and state["reason"] is None
    assert state["display_only"] is True
    assert state["vnc_ready"] is True, "display istnieje — Chrome ma gdzie renderować"
    assert state["human_steer_open"] is False, "ale gateway nie ma poświadczeń — nikt nie wejdzie"


@pytest.mark.parametrize(
    ("mode", "reason", "fragment"),
    [
        ("INTERACTIVE", "FIRST_LOGIN", "wyklucza powód"),
        ("HEADLESS", None, "tylko trybu INTERACTIVE"),
    ],
)
def test_display_only_is_exclusive_with_a_reason_and_only_for_interactive(
    supervisor: FakeSupervisor, mode: str, reason: str | None, fragment: str
) -> None:
    with pytest.raises(ModeSwitchRefused, match=fragment):
        SupervisorModeController(supervisor).switch(mode, reason, display_only=True)
    assert supervisor.switches == []


def test_opening_the_human_steer_on_a_display_only_capsule_is_a_switch_not_a_no_op(
    supervisor: FakeSupervisor,
) -> None:
    controller = SupervisorModeController(supervisor)
    controller.switch("INTERACTIVE", None, display_only=True)
    assert controller.state()["human_steer_open"] is False
    state = controller.switch("INTERACTIVE", "FIRST_LOGIN")
    assert state["changed"] is True and state["human_steer_open"] is True
    assert len(supervisor.switches) == 2, "ten sam tryb, inny ster — Chrome startuje na nowo"
    back = controller.switch("INTERACTIVE", None, display_only=True)
    assert back["changed"] is True and back["human_steer_open"] is False
    again = controller.switch("INTERACTIVE", None, display_only=True)
    assert again["changed"] is False and len(supervisor.switches) == 3


def test_a_credentials_file_without_a_display_is_not_an_open_steer(
    supervisor: FakeSupervisor,
) -> None:
    """Measured 2026-09-04 02:58 UTC: the runtime volume kept `vnc-credentials` across a container
    recreate and a HEADLESS capsule reported `human_steer_open: true` with no screen at all."""
    supervisor.gateway_credentials.write_text("full=stary\nview_only=stary\n")
    assert not supervisor.vnc_socket.exists()
    state = SupervisorModeController(supervisor).state()
    assert state["mode"] == "HEADLESS" and state["vnc_ready"] is False
    assert state["human_steer_open"] is False, "plik bez gniazda to pozostałość, nie ster"
