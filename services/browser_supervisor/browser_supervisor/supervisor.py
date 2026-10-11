"""BrowserSupervisor — starts, watches over and stops a single capsule.

One capsule = one account = one profile = one Chrome process (L2302-2305, L5163).

The `HEADLESS` mode is the default and handles all of the automation's work. The `INTERACTIVE`
mode additionally starts a virtual display and a window manager and is **the exception path** —
only for LOGIN, MFA, CHALLENGE and HUMAN_TAKEOVER (L2437, L5161).

Switching the mode stops the Chrome process and starts it again ON THE SAME profile
(L2444-2448). The profile, the session and the cookies stay — this is exactly the profile
handoff test required in L5162 and L5176.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from community_os_contracts import CapsuleMode, InteractiveReason
from community_os_contracts.enums import E1_ALLOWED_INTERACTIVE_REASONS

from . import devtools_port, profile, vncpasswd
from .args import DISPLAY_ONLY_GEOMETRY, HUMAN_GEOMETRY, build_args, build_human_args
from .devtools_port import DevToolsEndpoint


class ForbiddenInteractiveReason(Exception):
    """INTERACTIVE mode started for a reason outside the closed E1 list.

    "the list of allowed headful reasons (`LOGIN`, `MFA`, `CHALLENGE`, `HUMAN_TAKEOVER`)
     is closed; any other use raises an alert and requires a decision, not a silent
     exception" (L5518)
    """


class CapsuleStartFailed(Exception):
    """The process did not reach the state in which CDP answers."""


@dataclass
class RunningCapsule:
    """The state of a single launch."""

    mode: CapsuleMode
    process: subprocess.Popen
    #: None while a PERSON signs in (INTERACTIVE with a reason): that launch has no DevTools
    #: endpoint at all, because Google refuses a sign-in in a browser that runs with one
    #: (`args.HUMAN_ARGS`). Every reader of this field must handle None.
    endpoint: DevToolsEndpoint | None
    user_data_dir: Path
    started_at: float
    #: An identifier resistant to PID reuse (L5584).
    process_instance_id: str
    display_process: subprocess.Popen | None = None
    wm_process: subprocess.Popen | None = None
    reason: InteractiveReason | None = None
    #: D-026: INTERACTIVE started for rendering only — no human, no gateway credentials.
    display_only: bool = False

    def is_alive(self) -> bool:
        return self.process.poll() is None


@dataclass
class BrowserSupervisor:
    """The supervisor of one capsule."""

    binary: Path
    profiles_dir: Path
    capsule_id: str
    expected_cdp_browser: str
    cdp_host: str = "127.0.0.1"
    display_number: int = 99
    vnc_socket: Path | None = None
    _capsule: RunningCapsule | None = field(default=None, init=False, repr=False)

    # -- profile --------------------------------------------------------------

    @property
    def user_data_dir(self) -> Path:
        return self.profiles_dir / self.capsule_id

    @property
    def current(self) -> RunningCapsule | None:
        """The launch that is running now — `None` between `stop()` and the next `start()`.

        Callers that keep their own reference to a `RunningCapsule` hold the process from
        BEFORE a mode switch; the reader and the service loop ask here instead (D-025, plan B
        level 2).
        """
        return self._capsule

    def _image_build(self) -> profile.ProfileBuild:
        return profile.read_image_build(self.binary.parent)

    # -- start ----------------------------------------------------------------

    def start(
        self,
        mode: CapsuleMode = CapsuleMode.HEADLESS,
        *,
        reason: InteractiveReason | None = None,
        display_only: bool = False,
        timeout_s: float = 90.0,
        start_url: str | None = None,
    ) -> RunningCapsule:
        """Starts the capsule in the given mode and returns a verified endpoint.

        INTERACTIVE has two legitimate shapes since D-026 (2026-09-04, founder's decision after the
        measurement in `docs/OUTCOMES.md` §8 "Werdykt poziomu 2"):

        * with a `reason` from the closed E1 list — a human is at the screen (login, MFA,
          challenge, takeover); the VNC credentials are shared with the Stream Gateway;
        * `display_only=True` — headful Chrome on the virtual display because the platform refuses
          headless (measured: Reddit answers 403 to `HeadlessChrome` regardless of the account),
          with NO human steer: the gateway credentials file is not written, so nothing can attach
          to the screen. Exactly one of the two, never both, never neither.

        A launch for a person (the first shape) runs WITHOUT a DevTools endpoint and with
        `start_url` as its first tab (`args.build_human_args`, measured 2026-09-27: Google refuses
        a sign-in in a browser launched with the reading set). Its `endpoint` is None; the agent
        gets one back when the capsule returns to display-only.
        """
        if self._capsule is not None and self._capsule.is_alive():
            raise RuntimeError(
                "Kapsuła już działa. Dwa procesy na jednym profilu są zakazane "
                "(L2305, L5581) — najpierw stop()."
            )

        if mode is CapsuleMode.INTERACTIVE:
            if display_only and reason is not None:
                raise ForbiddenInteractiveReason(
                    "display_only wyklucza powód: albo człowiek przy ekranie (powód), albo sam "
                    "display do renderowania bez steru (D-026) — nie oba naraz."
                )
            if not display_only and reason is None:
                raise ForbiddenInteractiveReason(
                    "Tryb INTERACTIVE wymaga jawnego powodu z zamkniętej listy (L5518) albo "
                    "display_only=True (odczyt headful bez steru człowieka, D-026)."
                )
            if reason is not None and reason not in E1_ALLOWED_INTERACTIVE_REASONS:
                raise ForbiddenInteractiveReason(
                    f"Powód {reason} nie jest dopuszczony w E1. Dozwolone: "
                    f"{sorted(r.value for r in E1_ALLOWED_INTERACTIVE_REASONS)}. "
                    "Każde inne użycie wymaga decyzji człowieka, nie cichego wyjątku (L5518)."
                )
        elif display_only:
            raise ForbiddenInteractiveReason("display_only dotyczy tylko trybu INTERACTIVE")

        # Profile build match — before we launch anything.
        profile.ensure_build_match(self.user_data_dir, self._image_build())

        # We delete the old DevToolsActivePort so as not to read it as a fresh one.
        # We are allowed to do this because we hold the lease on the profile.
        devtools_port.remove_if_exists(self.user_data_dir)
        # Same lease, same reasoning: Chrome's SingletonLock of another container (a hard stop
        # of Docker leaves it in the profile volume) would make Chrome refuse the profile and
        # the capsule loop on CapsuleStartFailed. A live lock of THIS host is left alone.
        profile.remove_stale_singleton_lock(self.user_data_dir, hostname=socket.gethostname())

        srodowisko = os.environ.copy()
        display_proc: subprocess.Popen | None = None
        wm_proc: subprocess.Popen | None = None

        if mode is CapsuleMode.HEADLESS:
            # A gateway credentials file can outlive the display it belonged to: the runtime
            # volume survives a container recreate, and a container built before D-026 did not
            # remove it on stop (measured 2026-09-04 02:58 UTC — `human_steer_open: true` on a
            # HEADLESS capsule with no screen). Headless has no screen to steer, so no file.
            self._close_gateway_steer()

        if mode is CapsuleMode.INTERACTIVE:
            display_proc = self._start_display(
                share_with_gateway=not display_only,
                geometry=DISPLAY_ONLY_GEOMETRY if display_only else HUMAN_GEOMETRY,
            )
            srodowisko["DISPLAY"] = f":{self.display_number}"
            wm_proc = self._start_window_manager(srodowisko)

        for_person = mode is CapsuleMode.INTERACTIVE and reason is not None
        if for_person:
            args = build_human_args(user_data_dir=self.user_data_dir, start_url=start_url)
        else:
            args = build_args(
                mode=mode,
                user_data_dir=self.user_data_dir,
                cdp_host=self.cdp_host,
                cdp_port=0,
                display_only=display_only,
            )
        proces = subprocess.Popen(
            [str(self.binary), *args],
            env=srodowisko,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        if for_person:
            self._capsule = self._started_for_person(
                proces, display_proc, wm_proc, reason=reason, timeout_s=min(timeout_s, 10.0)
            )
            return self._capsule

        try:
            endpoint = devtools_port.wait_for_endpoint(
                self.user_data_dir,
                expected_browser=self.expected_cdp_browser,
                host=self.cdp_host,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            stderr = b""
            with contextlib.suppress(Exception):
                proces.kill()
                _, stderr = proces.communicate(timeout=5)
            for helper_process in (wm_proc, display_proc):
                self._kill(helper_process)
            raise CapsuleStartFailed(
                f"Kapsuła {self.capsule_id} nie wystartowała w trybie {mode.value}: {exc}\n"
                f"stderr Chrome: {stderr.decode('utf-8', 'replace')[:800]}"
            ) from exc

        # Tabs a restart brought back are closed at once (27.09: ten `about:blank` tabs after
        # twenty-seven minutes of a capsule that restarts with every mode switch).
        devtools_port.close_restored_pages(endpoint)

        self._capsule = RunningCapsule(
            mode=mode,
            process=proces,
            endpoint=endpoint,
            user_data_dir=self.user_data_dir,
            started_at=time.time(),
            process_instance_id=self._process_identity(proces.pid),
            display_process=display_proc,
            wm_process=wm_proc,
            reason=reason,
            display_only=display_only,
        )
        return self._capsule

    def _started_for_person(
        self,
        proces: subprocess.Popen,
        display_proc: subprocess.Popen | None,
        wm_proc: subprocess.Popen | None,
        *,
        reason: InteractiveReason | None,
        timeout_s: float,
    ) -> RunningCapsule:
        """A launch for a person has no endpoint to wait for, so "started" means: the process is
        still alive after a short settle. A browser that exits at once (a locked profile, a bad
        flag) is a failed start with its stderr, exactly like a missing endpoint."""
        deadline = time.monotonic() + min(timeout_s, 2.0)
        while time.monotonic() < deadline:
            if proces.poll() is not None:
                break
            time.sleep(0.1)
        if proces.poll() is not None:
            stderr = b""
            with contextlib.suppress(Exception):
                _, stderr = proces.communicate(timeout=5)
            for helper_process in (wm_proc, display_proc):
                self._kill(helper_process)
            raise CapsuleStartFailed(
                f"Kapsuła {self.capsule_id} nie wystartowała dla człowieka przy ekranie: Chrome "
                f"zakończył się z kodem {proces.returncode}\n"
                f"stderr Chrome: {stderr.decode('utf-8', 'replace')[:800]}"
            )
        return RunningCapsule(
            mode=CapsuleMode.INTERACTIVE,
            process=proces,
            endpoint=None,
            user_data_dir=self.user_data_dir,
            started_at=time.time(),
            process_instance_id=self._process_identity(proces.pid),
            display_process=display_proc,
            wm_process=wm_proc,
            reason=reason,
            display_only=False,
        )

    # -- attaching ------------------------------------------------------------

    def attach(self) -> DevToolsEndpoint:
        """Attaches to an already running process after a Gateway restart.

        "Gateway reattach after a restart" (L5159) and
        "a Gateway restart with a live Chrome does not start a second process" (L5583).

        The verification is the full one — we do not trust the mere existence of the file. If
        the file is stale, we raise `StaleDevToolsPort` and the caller MUST take a decision
        (reconciliation) rather than quietly start a second capsule.
        """
        return devtools_port.verify(
            self.user_data_dir,
            expected_browser=self.expected_cdp_browser,
            host=self.cdp_host,
        )

    # -- stopping -------------------------------------------------------------

    def stop(self, *, timeout_s: float = 20.0) -> None:
        """Stops the whole capsule: Chrome, the window manager, the display.

        Recovery covers the WHOLE capsule, not just the Chrome process (L2431).
        """
        capsule = self._capsule
        if capsule is None:
            return

        proces = capsule.process
        if proces.poll() is None:
            # First a graceful close over CDP, only then the signals.
            #
            # This is NOT cosmetics. Measured in gate no. 2: when stopping with SIGTERM
            # alone, Chrome does not manage to flush the cookie database to disk and the
            # profile loses the session between modes — a login performed in headful
            # disappears after switching to headless. The L5162 requirement ("without a
            # second login") therefore requires a controlled close, not killing the process.
            #
            # Note: Playwright's `browser.close()` does NOT close the browser on a CDP
            # connection — it closes the connection only. The `Browser.close` command is needed.
            #
            # A launch for a person has no endpoint, so it is closed the way a person closes it:
            # the window manager asks every window to close, and Chrome exits on its own after the
            # last one (measured 27.09: closing the window from the screen ended Chrome with 0 and
            # its session survived the next start).
            if capsule.endpoint is None:
                self._graceful_close_windows(capsule)
            else:
                self._graceful_close_via_cdp(capsule)

            if proces.poll() is None:
                proces.terminate()
            try:
                proces.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proces.kill()
                proces.wait(timeout=5)

        for helper_process in (capsule.wm_process, capsule.display_process):
            self._kill(helper_process)

        # After a clean close Chrome deletes the file itself; after kill -9 it does not.
        # We delete it ourselves so as not to leave a stale port for the next launch.
        devtools_port.remove_if_exists(self.user_data_dir)
        # The gateway's VNC credentials belong to the display that just died. Left behind, they
        # would make `human_steer_open` true for a capsule with no screen at all (D-026).
        self._close_gateway_steer()
        self._capsule = None

    # -- mode switching -------------------------------------------------------

    def switch_mode(
        self,
        target_mode: CapsuleMode,
        *,
        reason: InteractiveReason | None = None,
        display_only: bool = False,
        safe_stop_done: bool = False,
        start_url: str | None = None,
    ) -> RunningCapsule:
        """Switches the mode on THE SAME profile.

        Requires confirmation that the run in progress has reached `SAFE_STOP` (L2296, L2380,
        L5533). The supervisor cannot check that itself — it is knowledge of the execution
        layer — so it demands an explicit declaration from the caller instead of silently
        assuming it is safe.
        """
        if self._capsule is not None and not safe_stop_done:
            raise RuntimeError(
                "Przełączenie trybu wymaga wcześniejszego SAFE_STOP trwającego runa "
                "(L2380, L5533). Nierozstrzygnięty SUBMIT_STARTED musi najpierw przejść "
                "reconciliation."
            )
        self.stop()
        return self.start(
            target_mode, reason=reason, display_only=display_only, start_url=start_url
        )

    # -- helper parts ---------------------------------------------------------

    def _runtime_dir(self) -> Path:
        """Where the display's socket and password files live (the `capsule-run` volume)."""
        return (
            self.vnc_socket.parent if self.vnc_socket is not None else Path("/run/wenext/capsule")
        )

    def gateway_credentials_path(self) -> Path:
        """The one file the Stream Gateway reads to authenticate a human's stream (L2470)."""
        return self._runtime_dir() / "vnc-credentials"

    def _close_gateway_steer(self) -> None:
        """Removes the gateway's credentials file — the human steer is closed (D-026).

        Called from three places on purpose, each of them a moment when no human may be at the
        screen: a HEADLESS start (no screen), a display-only start (screen, no human), `stop()`
        (the screen is gone). The VNC passwords themselves keep existing in `vncpasswd` while a
        display runs — Xtigervnc needs them to refuse anonymous clients — only the copy the gateway
        reads is withheld. `tests/test_supervisor.py` executes all three paths for real.
        """
        with contextlib.suppress(FileNotFoundError):
            self.gateway_credentials_path().unlink()

    def _start_display(
        self,
        *,
        share_with_gateway: bool = True,
        geometry: tuple[int, int] = HUMAN_GEOMETRY,
    ) -> subprocess.Popen:
        """Xtigervnc on a unix socket — without a TCP port.

        The VNC port deliberately does NOT exist as a TCP port: the stream goes over a unix
        socket to the Stream Gateway, so it cannot be exposed either on the network or on the
        host (L2353, L5600).

        `share_with_gateway=False` (D-026, display-only reading): the passwords still exist —
        Xtigervnc needs them to refuse anonymous clients — but the file the Stream Gateway reads
        is NOT written (and a stale one is removed), so the human steer stays closed until a
        launch with a human reason writes it again.
        """
        # Separate passwords for control and view-only. Xtigervnc then enforces view-only
        # on ITS side — a client logged in with the view-only password will not send input,
        # no matter what the noVNC client does (L2470, L5594).
        # The Stream Gateway reads the plaintext passwords from a file in the runtime volume;
        # the browser never sees them.
        full_password, view_only = vncpasswd.generate_passwords()
        runtime_dir = (
            self.vnc_socket.parent if self.vnc_socket is not None else Path("/run/wenext/capsule")
        )
        password_file = runtime_dir / "vncpasswd"
        vncpasswd.write_password_file(password_file, full_password, view_only)
        if share_with_gateway:
            vncpasswd.write_for_gateway(runtime_dir / "vnc-credentials", full_password, view_only)
        else:
            self._close_gateway_steer()

        command = [
            "Xtigervnc",
            f":{self.display_number}",
            "-geometry",
            f"{geometry[0]}x{geometry[1]}",
            "-depth",
            "24",
            "-SecurityTypes",
            "VncAuth",
            "-PasswordFile",
            str(password_file),
            # -rfbport -1 disables TCP listening. The stream goes over the unix socket only,
            # so the VNC port exists neither on the container network nor on the host (L5600).
            "-rfbport",
            "-1",
        ]
        if self.vnc_socket is not None:
            self.vnc_socket.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                self.vnc_socket.unlink()
            command += ["-rfbunixpath", str(self.vnc_socket)]

        return subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True
        )

    def _start_window_manager(self, srodowisko: dict[str, str]) -> subprocess.Popen:
        # Chrome without a window manager will not handle popups and modal windows correctly,
        # and those are exactly what shows up at MFA and challenge.
        wait_for_display(self.display_number)
        return subprocess.Popen(
            ["openbox"],
            env=srodowisko,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _graceful_close_windows(self, capsule: RunningCapsule, *, timeout_s: float = 15.0) -> None:
        """Asks the window manager to close every window on the capsule's display, then waits for
        Chrome to exit on its own. `wmctrl -c` is the EWMH close request, the same thing the close
        button sends; Chrome treats it as a person closing the window and flushes its profile.

        A failure here is not critical: the caller moves on to SIGTERM anyway.
        """
        environment = dict(os.environ, DISPLAY=f":{self.display_number}")
        try:
            listing = subprocess.run(
                ["wmctrl", "-l"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for line in listing.splitlines():
            window_id = line.split(maxsplit=1)[0] if line.strip() else ""
            if not window_id.startswith("0x"):
                continue
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["wmctrl", "-i", "-c", window_id],
                    env=environment,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
        end_pos = time.monotonic() + timeout_s
        while time.monotonic() < end_pos:
            if capsule.process.poll() is not None:
                return
            time.sleep(0.1)

    @staticmethod
    def _graceful_close_via_cdp(capsule: RunningCapsule, *, timeout_s: float = 15.0) -> None:
        """Sends `Browser.close` and waits for the process to finish on its own.

        Thanks to that Chrome closes the profile databases in a controlled way and saves the
        session state. Without this step the profile loses cookies on a mode switch.

        A failure on this path is not critical — the caller moves on to SIGTERM anyway.
        """
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(capsule.endpoint.http_base)
                session = browser.new_browser_cdp_session()
                with contextlib.suppress(Exception):
                    session.send("Browser.close")
                with contextlib.suppress(Exception):
                    browser.close()
        except Exception:
            return

        end_pos = time.monotonic() + timeout_s
        while time.monotonic() < end_pos:
            if capsule.process.poll() is not None:
                return
            time.sleep(0.1)

    @staticmethod
    def _kill(proces: subprocess.Popen | None) -> None:
        if proces is None or proces.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proces.pid), signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proces.wait(timeout=5)
        if proces.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                proces.kill()

    @staticmethod
    def _process_identity(pid: int) -> str:
        """An identifier resistant to PID reuse.

        "PID reuse does not confuse the old and the new process" (L5584). The PID alone is
        not enough — we combine it with the process start time read from /proc.
        """
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as file:
                fields = file.read().rsplit(")", 1)[1].split()
            starttime = fields[19]
        except (OSError, IndexError):
            starttime = "nieznany"
        return f"{pid}:{starttime}"


def wait_for_display(number: int, *, timeout_s: float = 15.0) -> None:
    """Waits until the X socket for the given display shows up."""
    sock = Path(f"/tmp/.X11-unix/X{number}")
    end_pos = time.monotonic() + timeout_s
    while time.monotonic() < end_pos:
        if sock.exists():
            return
        time.sleep(0.1)
    raise CapsuleStartFailed(f"Display :{number} nie wystartował w {timeout_s:.0f} s")
