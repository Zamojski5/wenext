"""What one session's MCP server is allowed to touch, and for how long (P2).

THE SHAPE IS BORROWED ON PURPOSE from `mcp_sidecar.capability`: a task-scoped capability in a
0600 file, whose path arrives in an environment variable, loaded and validated BEFORE the server
answers `initialize`, and fail-closed if anything about it is wrong. That sidecar had the same
problem (a tool server that must not be talked into widening its own scope) and the answer has
already been paid for once.

WHY THE SCOPE IS NOT IN THE PROMPT, AND NOT IN THE TOOL ARGUMENTS. The model can write anything
into a tool call. If `project_id` were an argument, "read project X" would be a thing the model
could ask for, and a model that has read a web page asking it to do so would ask for it. The
scope is handed to the process by the control plane and is not reachable from the conversation:
every query in `registry.py` filters by these ids and none of them takes an organization or
workspace from the caller.

WHY THE DATABASE URL IS IN THIS FILE RATHER THAN THE ENVIRONMENT. The hardened Codex runtime is
described as having no database (13.3), and that description is about the MODEL's runtime, not
about our own tool server, which runs in the `api` container precisely so it can serve product
objects. Keeping the DSN here rather than in `config.toml` means one file to protect and one
file to delete when the turn ends, instead of a secret living in a configuration file that
outlives the session.

EXPIRY IS CHECKED ON EVERY CALL, not only at startup. A turn can run for minutes and the process
lives as long as the turn; a capability checked once at boot would be a capability with no end.

`host` SAYS WHO IS ON THE OTHER END. `None` is a Wenext-run session: Codex is started by the
control plane, the person talks to it through the dashboard, and the whole catalog is its to use.
A name from `HOSTS` is a session driven by the person's OWN coding agent through the plugin
(`plugin_server.py`): a weaker trust domain, because that agent has a shell and its own
instructions, so the tool server narrows the catalog to `catalog.PLUGIN_TOOLS` and refuses what
only the person may state. The value travels in the capability, and never in a tool argument, for
the reason `project_id` does not: it is not something the conversation may argue about.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

#: The variable carrying the PATH to the capability file. The capability itself never travels in
#: an environment variable: `/proc/<pid>/environ` is readable by the same user, and the model's
#: runtime runs as that user.
ENV_CAPABILITY_FILE = "WENEXT_SESSION_CAPABILITY_FILE"

#: How long a freshly issued capability is good for, unless the caller says otherwise. Longer
#: than any single turn we expect and short enough that a leaked file stops working the same day.
DEFAULT_TTL_MINUTES = 60

#: The ceiling of any capability, in minutes. A plugin session lives as long as the person's
#: agent window does, which is hours and not one turn, so the Wenext-run default (one turn plus
#: five minutes) does not fit; a day is the longest a leaked file may stay good. `issue` refuses
#: more instead of clamping: a caller asking for a week should hear that it cannot have one.
MAX_TTL_MINUTES = 24 * 60

#: The agents that may drive a session from outside (see the module docstring). A closed list
#: because the name selects a trust profile: a host nobody reviewed would get the profile of
#: whichever branch the code happened to fall into, and an unknown name fails closed.
HOST_CODEX = "codex"
HOST_CLAUDE_CODE = "claude-code"
HOSTS: frozenset[str] = frozenset({HOST_CODEX, HOST_CLAUDE_CODE})


class CapabilityError(Exception):
    """The capability cannot be trusted. The server exits instead of serving a narrower guess."""


class CapabilityExpired(CapabilityError):
    """The capability was valid and its window has closed."""


@dataclass(frozen=True)
class SessionCapability:
    """The scope of one session's tool server."""

    session_id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    dsn: str
    issued_at: datetime
    expires_at: datetime
    #: The connection the session runs on, when it has one. Tools do not use it; it is here so
    #: `catalog.describe` can say WHY something needs a connection rather than guessing.
    codex_connection_id: UUID | None = None
    #: `None` for a session the control plane runs itself, otherwise one of `HOSTS`. Validated in
    #: `__post_init__`, so a capability built in code and one read from a file obey one rule.
    host: str | None = None
    #: `True` only for a session served by the hosted MCP endpoint (`control_plane.hosted`): a
    #: stranger's agent reaching us over HTTPS with a personal token, with NO capsule, no accounts
    #: and no browser behind it. It narrows the catalog further than `host` does
    #: (`catalog.manifest(..., hosted=True)`) and switches the product to manual sending. It rides
    #: on a `host` (the agent on the other end), so `hosted` without one is refused.
    hosted: bool = False

    def __post_init__(self) -> None:
        if self.host is not None and self.host not in HOSTS:
            raise CapabilityError(
                f"nieznany agent {self.host!r} w zdolności; dozwolone: {', '.join(sorted(HOSTS))}. "
                "Nazwa wybiera zakres narzędzi, więc nieznana nie dostaje żadnego"
            )
        if self.hosted and self.host is None:
            raise CapabilityError(
                "zdolność hostowana bez agenta: tryb hostowany zawsze ma po drugiej stronie "
                f"agenta z listy {', '.join(sorted(HOSTS))}"
            )

    def check_live(self, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        if moment >= self.expires_at:
            raise CapabilityExpired(
                f"zdolność sesji {self.session_id} wygasła o "
                f"{self.expires_at.isoformat(timespec='seconds')}; tura nie ma już prawa czytać"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "organization_id": str(self.organization_id),
            "workspace_id": str(self.workspace_id),
            "project_id": str(self.project_id),
            "codex_connection_id": (
                str(self.codex_connection_id) if self.codex_connection_id else None
            ),
            # ALWAYS SERIALIZED, `null` included: a reader that finds no key cannot tell "a
            # Wenext-run session" from "a file written before this field existed".
            "host": self.host,
            "hosted": self.hosted,
            "dsn": self.dsn,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


def parse_capability(payload: Any) -> SessionCapability:
    """A capability from an already-parsed object, with every field required.

    A missing field is an error and never a default. A default here would be a scope nobody
    chose, and the most likely default (None, "all") is the widest one.
    """
    if not isinstance(payload, dict):
        raise CapabilityError("plik zdolności nie jest obiektem JSON")
    required = (
        "session_id",
        "organization_id",
        "workspace_id",
        "project_id",
        "dsn",
        "issued_at",
        "expires_at",
    )
    missing = [field for field in required if not payload.get(field)]
    # `host` is required as a KEY but its value may be null, so it cannot go through the falsy
    # test above: absent means an older writer, and an older writer's scope is not one to guess.
    if "host" not in payload:
        missing.append("host")
    if missing:
        raise CapabilityError(f"plik zdolności nie ma pól: {', '.join(missing)}")
    host = payload["host"]
    if host is not None and (not isinstance(host, str) or host not in HOSTS):
        raise CapabilityError(
            f"nieznany agent {host!r} w pliku zdolności; dozwolone: {', '.join(sorted(HOSTS))}"
        )
    try:
        issued_at = datetime.fromisoformat(str(payload["issued_at"]))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except ValueError as exc:
        raise CapabilityError(f"daty w pliku zdolności nie są ISO 8601: {exc}") from exc
    if issued_at.tzinfo is None or expires_at.tzinfo is None:
        raise CapabilityError(
            "daty w pliku zdolności muszą mieć strefę czasową; bez niej okno ważności zależy "
            "od tego, gdzie stoi serwer"
        )
    if expires_at <= issued_at:
        raise CapabilityError(
            f"zdolność wygasa ({expires_at.isoformat()}) nie później niż została wydana "
            f"({issued_at.isoformat()})"
        )
    try:
        connection = payload.get("codex_connection_id")
        return SessionCapability(
            session_id=UUID(str(payload["session_id"])),
            organization_id=UUID(str(payload["organization_id"])),
            workspace_id=UUID(str(payload["workspace_id"])),
            project_id=UUID(str(payload["project_id"])),
            codex_connection_id=UUID(str(connection)) if connection else None,
            host=host,
            # Absent means a file written before the field existed, and that is never hosted.
            hosted=payload.get("hosted") is True,
            dsn=str(payload["dsn"]),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise CapabilityError(f"identyfikator w pliku zdolności nie jest UUID: {exc}") from exc


def load_from_file(path: str | Path) -> SessionCapability:
    """Read, validate and return the capability. Raises rather than returning a narrower one."""
    file_path = Path(path)
    if not file_path.exists():
        raise CapabilityError(f"nie ma pliku zdolności {file_path}")
    mode = stat.S_IMODE(file_path.stat().st_mode)
    if mode & 0o077:
        # Group- or world-readable means the DSN and the scope are readable by anything running
        # as another user on this host. Refusing is louder than narrowing.
        raise CapabilityError(
            f"plik zdolności {file_path} ma prawa {mode:04o}; wymagane 0600, bo niesie DSN"
        )
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityError(f"plik zdolności {file_path} nieczytelny: {exc}") from exc
    return parse_capability(payload)


def load_from_env(env: dict[str, str] | None = None) -> SessionCapability:
    source = os.environ if env is None else env
    path = (source.get(ENV_CAPABILITY_FILE) or "").strip()
    if not path:
        raise CapabilityError(
            f"{ENV_CAPABILITY_FILE} nie jest ustawione. Serwer narzędzi bez zdolności nie "
            "startuje: nie ma zakresu, w którym wolno mu czytać"
        )
    return load_from_file(path)


def write_capability(path: str | Path, capability: SessionCapability) -> Path:
    """Write the file with 0600, creating the parent directory. Used by the turn handler.

    The permissions are SET, not assumed, and the file is written then chmod-ed in that order so
    a reader cannot catch it world-readable between the two steps.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch(mode=0o600, exist_ok=True)
    os.chmod(file_path, 0o600)
    file_path.write_text(
        json.dumps(capability.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(file_path, 0o600)
    return file_path


def issue(
    *,
    session_id: UUID,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    dsn: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    codex_connection_id: UUID | None = None,
    host: str | None = None,
    hosted: bool = False,
    now: datetime | None = None,
) -> SessionCapability:
    from datetime import timedelta

    moment = now or datetime.now(UTC)
    if ttl_minutes <= 0:
        raise CapabilityError("okno ważności zdolności musi być dodatnie")
    if ttl_minutes > MAX_TTL_MINUTES:
        raise CapabilityError(
            f"okno ważności {ttl_minutes} min przekracza sufit {MAX_TTL_MINUTES} min; plik z DSN "
            "nie żyje dłużej niż doba"
        )
    return SessionCapability(
        session_id=session_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        codex_connection_id=codex_connection_id,
        host=host,
        hosted=hosted,
        dsn=dsn,
        issued_at=moment,
        expires_at=moment + timedelta(minutes=ttl_minutes),
    )
