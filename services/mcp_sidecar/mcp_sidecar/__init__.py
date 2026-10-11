"""Task-scoped read-only MCP sidecar — section 14.6 of the document (L2472-2521).

    capability   TaskScopedCapability from the Control Plane, never from the prompt (L2495)
    origins      exact-origin allowlist; eTLD+1 NEVER authorizes (L2486)
    browser      narrow read-only browser backend — no click/fill/press (L2521)
    tools        exactly the six tools from L2504-2511
    server       MCP server over stdio, fail-closed when capability is missing (L2503)
    lifecycle    per-turn start before the Codex runtime; closed after the turn (L2501, L2505)

The integration status and what could not be done in F0 are in NOTES.md in this directory.
"""

from .browser import BackendError, BackendHttpReadOnly, BrowserBackend, NoBackend
from .capability import (
    CAPABILITY_FILE_ENV_VAR,
    CapabilityExpired,
    InvalidCapability,
    TaskScopedCapability,
    forbidden_field_violations,
    load_from_file,
    save_to_file,
)
from .lifecycle import (
    SidecarDidNotStart,
    SidecarProcess,
    config_toml_entry,
    sidecar_for_turn,
)
from .origins import FIXTURE_SITE_ORIGIN, OriginRejected, canonical_origin, is_allowed
from .server import MCPServer, build_server, main
from .tools import (
    FORBIDDEN_TOOLS,
    TOOL_NAMES,
    TOOLS,
    NoSuchTool,
    ToolError,
    ToolRegistry,
    manifest,
)

__all__ = [
    "CAPABILITY_FILE_ENV_VAR",
    "FIXTURE_SITE_ORIGIN",
    "FORBIDDEN_TOOLS",
    "TOOLS",
    "TOOL_NAMES",
    "BackendError",
    "BackendHttpReadOnly",
    "BrowserBackend",
    "CapabilityExpired",
    "InvalidCapability",
    "MCPServer",
    "NoBackend",
    "NoSuchTool",
    "OriginRejected",
    "SidecarDidNotStart",
    "SidecarProcess",
    "TaskScopedCapability",
    "ToolError",
    "ToolRegistry",
    "build_server",
    "canonical_origin",
    "config_toml_entry",
    "forbidden_field_violations",
    "is_allowed",
    "load_from_file",
    "main",
    "manifest",
    "save_to_file",
    "sidecar_for_turn",
]
