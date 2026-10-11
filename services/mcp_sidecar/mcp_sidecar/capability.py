"""Task-scoped capability created by the Control Plane before the turn (14.6, L2476-2505).

The two sentences this entire module is built around:

* "Codex neither picks nor passes an arbitrary `account_id`. It receives an immutable
  `bound_account_ref` derived from the task-scoped context, with no profile path, CDP port
  or handle that would allow switching the account." (L2474)
* "The capability does not reach the prompt; the sidecar receives it directly from the
  Control Plane." (L2495)

The second sentence has a design consequence: the capability enters the sidecar through a
channel the model has no access to — a file with 0600 permissions pointed at by an
environment variable, not an argument, a prompt or an MCP request field. `load_from_file`
is the only entry point.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Environment variable pointing at the capability file. Set by the Control Plane, not by the model.
CAPABILITY_FILE_ENV_VAR = "WENEXT_MCP_CAPABILITY_FILE"

#: Patterns that neither `bound_account_ref` nor any other capability field may contain.
#: This is the list of things that would give the model a handle to switch accounts (L2474).
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)user[-_]?data[-_]?dir", "ścieżka profilu Chromium"),
    (r"(?i)\bprofile_path\b", "ścieżka profilu"),
    (r"(?i)\bcdp[-_]?(port|url|endpoint)\b", "uchwyt CDP"),
    (r"(?i)\bdevtools\b", "uchwyt DevTools"),
    (r"(?i)\bws://", "adres WebSocket debuggera"),
    (r"(?i)\bremote[-_]debugging", "flaga remote debugging"),
    (r"(?i)/var/wenext/profiles", "mount profili"),
    (r"(?i)\bauth\.json\b", "poświadczenia Codexa"),
)


class InvalidCapability(Exception):
    """The capability fails the requirements of L2474-2495 — the sidecar does not start."""


class CapabilityExpired(Exception):
    """`expiry` has passed. A capability is renewed by the Control Plane, never in the sidecar."""


@dataclass(frozen=True)
class TaskScopedCapability:
    """Exactly the fields from L2478-2490, with nothing added and nothing dropped.

    `bound_account_ref` is an opaque identifier — the sidecar uses it for logging and for
    the binding fingerprint, never to derive anything about the account.
    """

    organization_id: str
    workspace_id: str
    task_id: str
    run_id: str
    assignment_id: str
    bound_account_ref: str
    profile_lease_generation: int
    allowed_tools: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    expiry: datetime
    #: Fingerprint of the `ThreadSecurityBinding` of the thread this capability was created for.
    #: A mismatch ends the turn fail-closed (L2503).
    thread_binding_fingerprint: str = ""
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.allowed_tools:
            raise InvalidCapability(
                "allowed_tools jest puste — pusta lista byłaby cichym dopuszczeniem wszystkiego"
            )
        if not self.allowed_origins:
            raise InvalidCapability(
                "allowed_origins jest puste — sidecar bez allowlisty originów nie może "
                "otworzyć niczego, więc pusta lista to błąd konfiguracji, nie stan pracy"
            )
        if self.expiry.tzinfo is None:
            raise InvalidCapability("expiry musi być świadome strefy czasowej")
        if self.profile_lease_generation < 0:
            raise InvalidCapability("profile_lease_generation nie może być ujemna")
        violations = forbidden_field_violations(self.to_json())
        if violations:
            raise InvalidCapability(
                "capability niesie uchwyty zakazane przez L2474: " + "; ".join(violations)
            )

    def is_expired(self, teraz: datetime | None = None) -> bool:
        return (teraz or datetime.now(UTC)) >= self.expiry

    def require_valid(self, teraz: datetime | None = None) -> None:
        if self.is_expired(teraz):
            raise CapabilityExpired(
                f"capability wygasło o {self.expiry.isoformat()} — turn kończy się fail-closed"
            )

    def binding_fingerprint(self) -> tuple:
        """The tuple that must match the thread binding on the runtime side."""
        return (
            self.organization_id,
            self.workspace_id,
            self.task_id,
            self.run_id,
            self.assignment_id,
            self.bound_account_ref,
            self.profile_lease_generation,
            self.thread_binding_fingerprint,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "assignment_id": self.assignment_id,
            "bound_account_ref": self.bound_account_ref,
            "profile_lease_generation": self.profile_lease_generation,
            "allowed_tools": list(self.allowed_tools),
            "allowed_origins": list(self.allowed_origins),
            "expiry": self.expiry.isoformat(),
            "thread_binding_fingerprint": self.thread_binding_fingerprint,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TaskScopedCapability:
        missing = [
            k
            for k in (
                "organization_id",
                "workspace_id",
                "task_id",
                "run_id",
                "assignment_id",
                "bound_account_ref",
                "profile_lease_generation",
                "allowed_tools",
                "allowed_origins",
                "expiry",
            )
            if k not in data
        ]
        if missing:
            raise InvalidCapability(f"capability bez pól: {', '.join(missing)}")
        return cls(
            organization_id=str(data["organization_id"]),
            workspace_id=str(data["workspace_id"]),
            task_id=str(data["task_id"]),
            run_id=str(data["run_id"]),
            assignment_id=str(data["assignment_id"]),
            bound_account_ref=str(data["bound_account_ref"]),
            profile_lease_generation=int(data["profile_lease_generation"]),
            allowed_tools=tuple(data["allowed_tools"]),
            allowed_origins=tuple(data["allowed_origins"]),
            expiry=datetime.fromisoformat(data["expiry"]),
            thread_binding_fingerprint=str(data.get("thread_binding_fingerprint", "")),
            _raw=dict(data),
        )


def forbidden_field_violations(payload: dict[str, Any]) -> list[str]:
    """Scans the WHOLE capability payload for the handles forbidden in L2474.

    We scan the serialization, not a list of known keys, because the problem is not the
    field name but the fact that the value carries a profile path or a CDP port —
    regardless of what someone names that field.
    """
    text = json.dumps(payload, ensure_ascii=False)
    return [description for pattern, description in FORBIDDEN_PATTERNS if re.search(pattern, text)]


def load_from_file(path: Path | None = None) -> TaskScopedCapability:
    """The only entry point of a capability into the sidecar (L2495).

    The file must be 0600 — the capability carries `bound_account_ref` and the lease
    generation, so a world-readable file would defeat the point of task-scoping.
    """
    if path is None:
        env_path = os.environ.get(CAPABILITY_FILE_ENV_VAR)
        if not env_path:
            raise InvalidCapability(
                f"brak {CAPABILITY_FILE_ENV_VAR} — capability przychodzi od Control Plane, "
                "nigdy z promptu ani z argumentów narzędzia (L2495). Sidecar nie startuje."
            )
        path = Path(env_path)
    if not path.exists():
        raise InvalidCapability(f"plik capability {path} nie istnieje — fail-closed")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise InvalidCapability(
            f"plik capability {path} ma {mode:04o}; wymagane 0600 (albo ostrzejsze)"
        )
    return TaskScopedCapability.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_to_file(capability: TaskScopedCapability, path: Path) -> Path:
    """The Control Plane writes the capability with this function — 0600 permissions at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(capability.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.chmod(path, 0o600)
    return path
