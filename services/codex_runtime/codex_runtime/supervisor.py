"""`CodexConnectionSupervisor` — exclusive modes and a singleton runtime lease (L2149-2161).

"The Account Adapter and the SDK never run two App Servers at the same time on the same
`CODEX_HOME`. They use the same exact `codex_bin` and runtime version." (L2159-2161)

We enforce this with two things at once, because each on its own has a hole:

* the mode machine `STOPPED → AUTH_CONTROL → STOPPED → EXECUTION → STOPPED`,
* a lease file in `CODEX_HOME` carrying the PID — because the mode machine lives in the process
  and does not see a second process that would reach for the same directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from community_os_contracts import CodexSupervisorMode as SupervisorMode

LEASE_FILE_NAME = "wenext-runtime.lease"

#: L2152-2157: from every working mode you come back through STOPPED. There is no
#: AUTH_CONTROL → EXECUTION transition and that is the whole point of exclusivity.
TRANSITIONS: dict[SupervisorMode, frozenset[SupervisorMode]] = {
    SupervisorMode.STOPPED: frozenset({SupervisorMode.AUTH_CONTROL, SupervisorMode.EXECUTION}),
    SupervisorMode.AUTH_CONTROL: frozenset({SupervisorMode.STOPPED}),
    SupervisorMode.EXECUTION: frozenset({SupervisorMode.STOPPED}),
}


class ModeBusy(Exception):
    """An attempt to enter a working mode without passing through STOPPED."""


class LeaseTaken(Exception):
    """Another live process holds the runtime lease on this `CODEX_HOME` (L2205)."""


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class CodexConnectionSupervisor:
    """One supervisor per one connection and one `CODEX_HOME`."""

    codex_home: Path
    _execution_mode: SupervisorMode = field(default=SupervisorMode.STOPPED, init=False)
    _lease_held: bool = field(default=False, init=False)

    @property
    def mode(self) -> SupervisorMode:
        return self._execution_mode

    @property
    def lease_path(self) -> Path:
        return self.codex_home / LEASE_FILE_NAME

    def _acquire_lease(self, mode: SupervisorMode) -> None:
        path = self.lease_path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            pid = data.get("pid")
            if isinstance(pid, int) and pid != os.getpid() and _process_is_alive(pid):
                raise LeaseTaken(
                    f"runtime lease na {self.codex_home} trzyma żywy proces pid={pid} "
                    f"w trybie {data.get('tryb')}. Dwa App Servery na jednym CODEX_HOME "
                    "są zakazane (L2159)."
                )
        path.write_text(json.dumps({"pid": os.getpid(), "tryb": str(mode)}), encoding="utf-8")
        os.chmod(path, 0o600)
        self._lease_held = True

    def _release_lease(self) -> None:
        if self._lease_held:
            self.lease_path.unlink(missing_ok=True)
            self._lease_held = False

    def transition_to(self, target: SupervisorMode) -> None:
        allowed = TRANSITIONS[self._execution_mode]
        if target not in allowed:
            raise ModeBusy(
                f"{self._execution_mode} → {target} nie istnieje w L2152-2157. "
                "Tryby są ekskluzywne: "
                f"z {self._execution_mode} wolno tylko do {', '.join(sorted(allowed))}."
            )
        if target is SupervisorMode.STOPPED:
            self._release_lease()
        else:
            self._acquire_lease(target)
        self._execution_mode = target

    def stop(self) -> None:
        if self._execution_mode is not SupervisorMode.STOPPED:
            self.transition_to(SupervisorMode.STOPPED)
        else:
            self._release_lease()
