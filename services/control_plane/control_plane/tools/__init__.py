"""The `wenext` MCP server: the product's objects, scoped to one session (P2).

`capability.py` is the scope, `catalog.py` is what exists and what does not, `registry.py` does
the work, `mcp_server.py` is the process Codex starts. Nothing here knows about Codex beyond
speaking MCP over stdio.
"""

from .capability import (
    ENV_CAPABILITY_FILE,
    HOSTS,
    MAX_TTL_MINUTES,
    CapabilityError,
    CapabilityExpired,
    SessionCapability,
    issue,
    load_from_env,
    load_from_file,
    write_capability,
)
from .catalog import (
    APP_ONLY_TOOLS,
    BY_NAME,
    CATALOG,
    OUTCOME_DONE,
    OUTCOME_ERROR,
    OUTCOME_PENDING,
    OUTCOME_UNAVAILABLE,
    OUTCOMES,
    PLUGIN_EXCLUDED,
    PLUGIN_TOOLS,
    STATE_AVAILABLE,
    STATE_NEEDS_CONNECTION,
    STATE_NOT_IMPLEMENTED,
    STATES,
    ToolSpec,
    listing,
    manifest,
)
from .registry import NoSuchTool, ToolError, ToolRegistry, render_content

__all__ = [
    "APP_ONLY_TOOLS",
    "BY_NAME",
    "CATALOG",
    "ENV_CAPABILITY_FILE",
    "HOSTS",
    "MAX_TTL_MINUTES",
    "OUTCOMES",
    "OUTCOME_DONE",
    "OUTCOME_ERROR",
    "OUTCOME_PENDING",
    "OUTCOME_UNAVAILABLE",
    "PLUGIN_EXCLUDED",
    "PLUGIN_TOOLS",
    "STATES",
    "STATE_AVAILABLE",
    "STATE_NEEDS_CONNECTION",
    "STATE_NOT_IMPLEMENTED",
    "CapabilityError",
    "CapabilityExpired",
    "NoSuchTool",
    "SessionCapability",
    "ToolError",
    "ToolRegistry",
    "ToolSpec",
    "issue",
    "listing",
    "load_from_env",
    "load_from_file",
    "manifest",
    "render_content",
    "write_capability",
]
