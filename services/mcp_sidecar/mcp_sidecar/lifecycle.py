"""Per-turn lifecycle of the sidecar (L2499-2505).

    1. The Control Plane creates the binding on the server side.
    2. It starts a separate per-turn MCP sidecar BEFORE the Codex runtime.
    3. The capability does not reach the prompt; the sidecar gets it directly.
    4. The Codex configuration names only this sidecar, required=true and exact enabled_tools.
    5. A missing sidecar or a binding mismatch ends the turn fail-closed.
    6. The sidecar is closed after the turn.

Point 6 is realized here by two paths, because one is not enough: `stop()` in a `finally`
block handles normal completion, and the `sidecar_for_turn` context manager handles an
exception. Cleanup "just in case" at process exit would be a third path, but it would rely
on the parent process actually getting a chance to terminate — and that is not guaranteed
under SIGKILL, so that is not how we release leases.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .capability import CAPABILITY_FILE_ENV_VAR, TaskScopedCapability, save_to_file
from .tools import TOOL_NAMES


class SidecarDidNotStart(Exception):
    """The sidecar process did not come up or died immediately. Turn fail-closed (L2503)."""


@dataclass
class SidecarProcess:
    """Per-turn sidecar process. Satisfies the `Sidecar` protocol from `codex_runtime`."""

    capability: TaskScopedCapability
    python_bin: str = sys.executable
    #: Directory for the capability file. By default a private temporary dir per instance.
    work_dir: Path | None = None
    start_timeout_s: float = 3.0
    _proc: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _tmp: tempfile.TemporaryDirectory | None = field(default=None, init=False, repr=False)
    _capability_file: Path | None = field(default=None, init=False, repr=False)

    # -- the `Sidecar` protocol -----------------------------------------------

    @property
    def nazwa(self) -> str:
        return "wenext_browser"

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def command(self) -> tuple[str, ...]:
        return (self.python_bin, "-m", "mcp_sidecar")

    @property
    def enabled_tools(self) -> tuple[str, ...]:
        """Intersection of the manifest and `allowed_tools` — never more than six names."""
        return tuple(n for n in TOOL_NAMES if n in self.capability.allowed_tools)

    def binding_fingerprint(self) -> tuple:
        return self.capability.binding_fingerprint()

    def start(self) -> None:
        if self.is_alive:
            return
        self.capability.require_valid()

        if self.work_dir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="wenext-mcp-")
            directory = Path(self._tmp.name)
        else:
            directory = self.work_dir
            directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self._capability_file = save_to_file(self.capability, directory / "capability.json")

        srodowisko = os.environ.copy()
        srodowisko[CAPABILITY_FILE_ENV_VAR] = str(self._capability_file)
        self._proc = subprocess.Popen(
            list(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=srodowisko,
        )
        # Fail-closed on immediate death: a process that died on capability validation
        # must not look like "it started, it just is not answering yet".
        # We wait a short moment, not the full `start_timeout_s` — if it is alive after
        # that, it means it entered the protocol loop and waiting longer checks nothing.
        try:
            kod = self._proc.wait(timeout=min(0.4, self.start_timeout_s))
        except subprocess.TimeoutExpired:
            return
        stderr = (self._proc.stderr.read() if self._proc.stderr else "") or ""
        self._proc = None
        raise SidecarDidNotStart(
            f"sidecar zakończył się kodem {kod} zaraz po starcie: {stderr.strip()[:500]}"
        )

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Closes the sidecar after the turn (L2505). Idempotent."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_s)
        for stream in (
            proc.stdin if proc else None,
            proc.stdout if proc else None,
            proc.stderr if proc else None,
        ):
            with contextlib.suppress(OSError):
                if stream:
                    stream.close()
        if self._capability_file is not None:
            self._capability_file.unlink(missing_ok=True)
            self._capability_file = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    # -- transport ------------------------------------------------------------

    def send(self, frame: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise SidecarDidNotStart("sidecar nie jest uruchomiony")
        self._proc.stdin.write(frame + "\n")
        self._proc.stdin.flush()

    def read(self) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise SidecarDidNotStart("sidecar nie jest uruchomiony")
        return self._proc.stdout.readline()


@contextlib.contextmanager
def sidecar_for_turn(
    capability: TaskScopedCapability, *, expected_fingerprint: tuple | None = None
) -> Iterator[SidecarProcess]:
    """A sidecar living exactly one turn. Closed also when the turn raised."""
    proces = SidecarProcess(capability=capability)
    proces.start()
    try:
        if (
            expected_fingerprint is not None
            and proces.binding_fingerprint() != expected_fingerprint
        ):
            raise SidecarDidNotStart(
                "mismatch bindingu: capability sidecara nie odpowiada bindingowi wątku "
                "— turn fail-closed (L2503)"
            )
        yield proces
    finally:
        proces.stop()


def config_toml_entry(sidecar: SidecarProcess, *, capability_file: Path) -> str:
    """The fragment of Codex's `config.toml` naming ONLY this sidecar (L2502).

    `required = true` and an explicit `enabled_tools`. `disabled_tools` is applied AFTER
    the allowlist, so putting a tool in both places ends with it being removed — which is
    why it is not here, so that the allowlist is the single place of truth.
    """
    tools = ", ".join(f'"{n}"' for n in sidecar.enabled_tools)
    command = sidecar.command
    args = ", ".join(f'"{a}"' for a in command[1:])
    return "\n".join(
        [
            f"[mcp_servers.{sidecar.nazwa}]",
            f'command = "{command[0]}"',
            f"args = [{args}]",
            "enabled = true",
            "required = true",
            f"enabled_tools = [{tools}]",
            f'env = {{ "{CAPABILITY_FILE_ENV_VAR}" = "{capability_file}" }}',
            "",
        ]
    )
