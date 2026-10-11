"""`BrowserSupervisor` — the human-steer invariants of D-026, executed on the REAL code paths.

`test_control.py` drives the controller through `FakeSupervisor`, whose `switch_mode` re-implements
"credentials only for a human launch" — so a regression that opened the steer in the real
supervisor would still pass there. This file runs the real `_start_display`, `start(HEADLESS)` and
`stop()` on a temporary runtime directory with the processes substituted: no Chrome, no Xtigervnc,
but the exact lines that decide whether `vnc-credentials` exists.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import ClassVar

import pytest
from browser_supervisor import supervisor as module
from browser_supervisor.devtools_port import DevToolsEndpoint
from browser_supervisor.supervisor import (
    BrowserSupervisor,
    ForbiddenInteractiveReason,
    RunningCapsule,
)
from community_os_contracts import CapsuleMode, InteractiveReason


class FakePopen:
    """Enough of `subprocess.Popen` for the supervisor: never really runs anything."""

    launched: ClassVar[list[list[str]]] = []

    def __init__(self, args, **_kwargs) -> None:
        self.args = list(args)
        self.pid = 4242
        self.returncode: int | None = None
        FakePopen.launched.append(self.args)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        return b"", b""


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[BrowserSupervisor, Path]:
    FakePopen.launched = []
    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    supervisor = BrowserSupervisor(
        binary=tmp_path / "chrome",
        profiles_dir=tmp_path / "profiles",
        capsule_id="capsule-test",
        expected_cdp_browser="Chrome/153.0.8010.12",
        vnc_socket=run_dir / "vnc.sock",
    )
    return supervisor, run_dir


def _credentials(run_dir: Path) -> Path:
    return run_dir / "vnc-credentials"


# -- _start_display: the only writer of the gateway credentials -----------------------------


def test_a_human_display_writes_the_gateway_credentials(runtime) -> None:
    supervisor, run_dir = runtime
    supervisor._start_display(share_with_gateway=True)
    assert _credentials(run_dir).is_file(), "człowiek przy ekranie → gateway ma czym uwierzytelnić"
    assert (run_dir / "vncpasswd").is_file(), "Xtigervnc też ma swój plik haseł"
    text = _credentials(run_dir).read_text(encoding="utf-8")
    assert "full=" in text and "view_only=" in text
    (xtigervnc,) = FakePopen.launched
    assert xtigervnc[0] == "Xtigervnc" and "-rfbport" in xtigervnc and "-1" in xtigervnc
    assert "-rfbunixpath" in xtigervnc and str(run_dir / "vnc.sock") in xtigervnc


def test_a_display_only_start_does_not_write_them_and_removes_a_stale_file(runtime) -> None:
    supervisor, run_dir = runtime
    _credentials(run_dir).write_text("full=stare\nview_only=stare\n", encoding="utf-8")
    supervisor._start_display(share_with_gateway=False)
    assert not _credentials(run_dir).exists(), "display bez człowieka — gateway nie ma poświadczeń"
    assert (run_dir / "vncpasswd").is_file(), (
        "hasła VNC nadal powstają: Xtigervnc odrzuca klientów bez hasła; wstrzymany jest tylko "
        "przekaz do gatewaya"
    )


# -- start(HEADLESS) and stop(): the two other moments the steer must be closed ----------------


@pytest.fixture
def headless_ready(runtime, monkeypatch: pytest.MonkeyPatch):
    """`start(HEADLESS)` without Chrome: profile check, port wait and PID identity substituted."""
    supervisor, run_dir = runtime
    monkeypatch.setattr(module.profile, "ensure_build_match", lambda *_a, **_k: None)
    monkeypatch.setattr(BrowserSupervisor, "_image_build", lambda self: None)
    monkeypatch.setattr(
        module.devtools_port,
        "wait_for_endpoint",
        lambda *_a, **_k: DevToolsEndpoint(port=45000, browser_path="/devtools/browser/x"),
    )
    monkeypatch.setattr(module.devtools_port, "close_restored_pages", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        BrowserSupervisor, "_process_identity", staticmethod(lambda pid: f"{pid}:1")
    )
    monkeypatch.setattr(
        BrowserSupervisor, "_graceful_close_via_cdp", staticmethod(lambda *_a, **_k: None)
    )
    return supervisor, run_dir


def test_a_headless_start_removes_a_leftover_credentials_file(headless_ready) -> None:
    supervisor, run_dir = headless_ready
    _credentials(run_dir).write_text("full=z-poprzedniego-kontenera\n", encoding="utf-8")
    capsule = supervisor.start(CapsuleMode.HEADLESS)
    assert capsule.mode is CapsuleMode.HEADLESS and capsule.display_only is False
    assert not _credentials(run_dir).exists(), "headless nie ma ekranu, więc nie ma steru"
    (chrome,) = FakePopen.launched
    assert "--headless" in chrome and f"--user-data-dir={supervisor.user_data_dir}" in chrome


def _singleton_links(user_data_dir: Path, *, host: str, pid: int) -> None:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    (user_data_dir / "SingletonLock").symlink_to(f"{host}-{pid}")
    (user_data_dir / "SingletonCookie").symlink_to("5804077806427502377")
    (user_data_dir / "SingletonSocket").symlink_to("/tmp/org.chromium.Chromium.x/SingletonSocket")


def test_a_start_removes_the_singleton_lock_of_another_container(headless_ready) -> None:
    """Measured 2026-09-12: after a hard Docker stop the profile volume kept
    `SingletonLock -> 21dc193847d8-379` of the previous container; Chrome refused the profile as
    "in use on another computer" and the capsule looped on CapsuleStartFailed (5 restarts)."""
    supervisor, _ = headless_ready
    _singleton_links(supervisor.user_data_dir, host="21dc193847d8", pid=379)
    supervisor.start(CapsuleMode.HEADLESS)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        assert not (supervisor.user_data_dir / name).is_symlink(), f"{name} z obcego hosta usunięty"
    (chrome,) = FakePopen.launched
    assert f"--user-data-dir={supervisor.user_data_dir}" in chrome


def test_a_start_keeps_the_singleton_lock_of_a_live_chrome_on_this_host(headless_ready) -> None:
    """The one lock that is NOT stale: this host, a pid that is alive. Two Chromes on one profile
    stay forbidden, so the lock stays and Chrome keeps refusing loudly."""
    supervisor, _ = headless_ready
    _singleton_links(supervisor.user_data_dir, host=socket.gethostname(), pid=os.getpid())
    supervisor.start(CapsuleMode.HEADLESS)
    assert (supervisor.user_data_dir / "SingletonLock").is_symlink(), "żywa blokada zostaje"
    assert (supervisor.user_data_dir / "SingletonCookie").is_symlink()


def test_a_dead_pid_of_this_host_is_a_stale_lock_too(tmp_path: Path) -> None:
    from browser_supervisor import profile

    dead = 2**22 - 1  # above pid_max defaults; nothing runs there
    _singleton_links(tmp_path, host=socket.gethostname(), pid=dead)
    removed = profile.remove_stale_singleton_lock(tmp_path, hostname=socket.gethostname())
    assert set(removed) == {"SingletonLock", "SingletonCookie", "SingletonSocket"}
    assert profile.remove_stale_singleton_lock(tmp_path, hostname=socket.gethostname()) == ()


def test_stop_removes_the_credentials_of_the_display_that_died(headless_ready) -> None:
    supervisor, run_dir = headless_ready
    supervisor.start(CapsuleMode.HEADLESS)
    _credentials(run_dir).write_text("full=x\nview_only=y\n", encoding="utf-8")
    supervisor.stop()
    assert supervisor.current is None
    assert not _credentials(run_dir).exists()


def test_display_only_and_a_reason_are_exclusive_in_the_real_supervisor(headless_ready) -> None:
    supervisor, _ = headless_ready
    with pytest.raises(ForbiddenInteractiveReason, match="wyklucza"):
        supervisor.start(
            CapsuleMode.INTERACTIVE, reason=InteractiveReason.FIRST_LOGIN, display_only=True
        )
    with pytest.raises(ForbiddenInteractiveReason, match="display_only=True"):
        supervisor.start(CapsuleMode.INTERACTIVE)
    with pytest.raises(ForbiddenInteractiveReason, match="tylko trybu INTERACTIVE"):
        supervisor.start(CapsuleMode.HEADLESS, display_only=True)
    assert FakePopen.launched == [], "odmowa zapada zanim cokolwiek startuje"


def test_stop_is_idempotent_and_safe_without_a_capsule(runtime) -> None:
    supervisor, _run_dir = runtime
    supervisor.stop()
    assert supervisor.current is None
    running = RunningCapsule(
        mode=CapsuleMode.HEADLESS,
        process=FakePopen(["chrome"]),
        endpoint=DevToolsEndpoint(port=1, browser_path="/x"),
        user_data_dir=supervisor.user_data_dir,
        started_at=time.time(),
        process_instance_id="1:1",
    )
    assert running.is_alive()
    running.process.terminate()
    assert not running.is_alive(), "atrapa procesu zachowuje się jak Popen: po terminate nie żyje"


# -- a person's launch (27.09): no DevTools endpoint, the login page as the first tab ------------


@pytest.fixture
def person_ready(headless_ready, monkeypatch: pytest.MonkeyPatch):
    supervisor, run_dir = headless_ready
    monkeypatch.setattr(module, "wait_for_display", lambda *_a, **_k: None)
    return supervisor, run_dir


def test_a_person_launch_has_no_devtools_endpoint_and_opens_the_login_page(person_ready) -> None:
    """Google refused a sign-in twice in a browser launched with the reading set and accepted it
    in one launched with two first-run flags and the page; this is that launch."""
    supervisor, run_dir = person_ready
    capsule = supervisor.start(
        CapsuleMode.INTERACTIVE,
        reason=InteractiveReason.FIRST_LOGIN,
        start_url="https://x.com/i/flow/login",
    )
    chrome = FakePopen.launched[-1]
    assert capsule.endpoint is None
    assert chrome[1:] == [
        f"--user-data-dir={supervisor.user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://x.com/i/flow/login",
    ]
    assert not any(one.startswith("--remote-debugging") for one in chrome)
    assert _credentials(run_dir).is_file(), "człowiek przy ekranie, gateway ma poświadczenia"


def test_a_person_launch_is_closed_by_the_window_manager_not_over_cdp(
    person_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor, _ = person_ready
    running: list[RunningCapsule] = []
    calls: list[list[str]] = []

    class Done:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if list(args[:2]) == ["wmctrl", "-l"]:
            return Done("0x01200003  0 capsule Sign in - Chromium\n0x01200010  0 capsule X\n")
        if list(args[:3]) == ["wmctrl", "-i", "-c"] and running:
            running[0].process.returncode = 0  # Chrome exits after its last window closes
        return Done()

    cdp: list[int] = []
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        BrowserSupervisor, "_graceful_close_via_cdp", staticmethod(lambda *_a, **_k: cdp.append(1))
    )
    running.append(supervisor.start(CapsuleMode.INTERACTIVE, reason=InteractiveReason.FIRST_LOGIN))
    supervisor.stop()
    assert ["wmctrl", "-i", "-c", "0x01200003"] in calls
    assert ["wmctrl", "-i", "-c", "0x01200010"] in calls
    assert cdp == [], "przeglądarka człowieka nie ma CDP, więc nie zamyka się przez CDP"


def test_a_person_browser_that_exits_at_once_is_a_failed_start(
    person_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor, _ = person_ready

    class ExitsAtOnce(FakePopen):
        def __init__(self, args, **kwargs) -> None:
            super().__init__(args, **kwargs)
            if "--no-first-run" in self.args:
                self.returncode = 21

    monkeypatch.setattr(module.subprocess, "Popen", ExitsAtOnce)
    with pytest.raises(module.CapsuleStartFailed, match="kodem 21"):
        supervisor.start(CapsuleMode.INTERACTIVE, reason=InteractiveReason.FIRST_LOGIN)


def test_the_healthcheck_speaks_the_readers_header_and_needs_a_reader_to_say_yes() -> None:
    from browser_supervisor import healthcheck, reader

    assert healthcheck.READER_TOKEN_HEADER == reader.READER_TOKEN_HEADER
    assert healthcheck.person_at_the_screen({}) is False


def test_the_agents_screen_is_portrait_and_the_persons_screen_is_unchanged(person_ready) -> None:
    supervisor, _ = person_ready
    supervisor.start(CapsuleMode.INTERACTIVE, display_only=True)
    agent_display = next(one for one in FakePopen.launched if one[0] == "Xtigervnc")
    supervisor.stop()
    FakePopen.launched = []
    supervisor.start(CapsuleMode.INTERACTIVE, reason=InteractiveReason.FIRST_LOGIN)
    person_display = next(one for one in FakePopen.launched if one[0] == "Xtigervnc")
    geometry = lambda command: command[command.index("-geometry") + 1]  # noqa: E731
    assert geometry(agent_display) == "1440x2560"
    assert geometry(person_display) == "1280x900"
