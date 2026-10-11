"""The workspace domain of P1: projects, person profiles, platform accounts and assignments.

Modules, and a deliberate split between them:

  * `store` — everything that touches migration 0013, with the tenant filter in every query;
  * `platforms` — what the product can really do on each platform, kept in CODE because it is a
    fact about this repository, not about the user's data;
  * `connections` — migration 0014, the Codex connections (P1 etap 2);
  * `codex_home` — where one connection keeps its `CODEX_HOME` under the single volume;
  * `engine` — the port to one app-server, and the adapter behind it;
  * `handlers` — the two queue jobs: waiting out a device-code login, and re-reading a state.

The routers `control_plane.api.workspaces` and `control_plane.api.codex_connections` are the only
callers. Nothing here knows about HTTP: a refusal is a `WorkspaceRefusal`, and turning it into a
status code is the router's job.

`engine` and `codex_home` import `codex_runtime` LAZILY, inside the functions that need it, and
fail loudly when it is absent. That package is in the control plane's image on purpose — the
hardening rule that makes a separate `CODEX_HOME` isolate anything (L2205) lives there, and a
second copy of it here would drift into a connection storing credentials in a shared keyring.
"""

from control_plane.workspace.codex_home import (
    HOME_ROOT_VARIABLE,
    ROOT_REF,
    CodexHomeUnavailable,
    UnsafeHomeRef,
    home_facts,
    home_root,
    prepare_connection_home,
    resolve_home,
)
from control_plane.workspace.connections import (
    CONNECTION_STATUSES,
    CodexConnectionRow,
    CodexConnectionStore,
    HomeAlreadyLive,
)
from control_plane.workspace.engine import (
    AppServerEngine,
    CodexEnginePort,
    EngineSnapshot,
    EngineUnavailable,
    LoginCancelled,
    LoginRefused,
    LoginTicket,
    LoginTimedOut,
)
from control_plane.workspace.platforms import (
    NO_INTEGRATION_LABEL,
    PLATFORMS,
    SUPPORTED_PLATFORMS,
    PlatformSupport,
    platform_payload,
    support_for,
)
from control_plane.workspace.store import (
    AccountAlreadyAssigned,
    AccountAlreadyAttached,
    AccountRow,
    AssignmentRow,
    AttachmentRow,
    PersonProfileRow,
    ProjectRow,
    WorkspaceRefusal,
    WorkspaceRow,
    WorkspaceStore,
)

__all__ = [
    "CONNECTION_STATUSES",
    "HOME_ROOT_VARIABLE",
    "NO_INTEGRATION_LABEL",
    "PLATFORMS",
    "ROOT_REF",
    "SUPPORTED_PLATFORMS",
    "AccountAlreadyAssigned",
    "AccountAlreadyAttached",
    "AccountRow",
    "AppServerEngine",
    "AssignmentRow",
    "AttachmentRow",
    "CodexConnectionRow",
    "CodexConnectionStore",
    "CodexEnginePort",
    "CodexHomeUnavailable",
    "EngineSnapshot",
    "EngineUnavailable",
    "HomeAlreadyLive",
    "LoginCancelled",
    "LoginRefused",
    "LoginTicket",
    "LoginTimedOut",
    "PersonProfileRow",
    "PlatformSupport",
    "ProjectRow",
    "UnsafeHomeRef",
    "WorkspaceRefusal",
    "WorkspaceRow",
    "WorkspaceStore",
    "home_facts",
    "home_root",
    "platform_payload",
    "prepare_connection_home",
    "resolve_home",
    "support_for",
]
