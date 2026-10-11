"""Codex runtime for phase E1.F0 — section 13 of the document (L2105-2267).

The module split follows the split of responsibilities from L2107-2115:

    protocol           narrow JSON-RPC client with a method allowlist
    hardening          isolated CODEX_HOME 0700 + hardened config.toml
    supervisor         exclusive AUTH_CONTROL / EXECUTION modes, singleton lease
    account_adapter    ChatGPT login, account/read, model/list, rate limits
    routing            fixed model/effort routes per role (NOT the Model Router — that is E2)
    threads            four isolated roles, ThreadSecurityBinding, fail-closed resume
    execution_adapter  turns on the Python SDK, per-turn sidecar MCP, BoundedRunEnvelope
    jobs               WAITING_AUTH / WAITING_QUOTA / WAITING_CAPABILITY

The errata against the document (confirmed by running it) is in NOTES.md in this directory.
"""

from .account_adapter import (
    AccountState,
    CodexAccountAdapter,
    IllegalTransition,
    LoginAttempt,
    LoginUnavailable,
    ModelCatalogSnapshot,
    ModelInfo,
    SnapshotIncomplete,
    find_codex_bin,
)
from .execution_adapter import (
    BindingMismatch,
    BoundedRunEnvelope,
    CodexExecutionAdapter,
    EnvelopeExceeded,
    EnvelopeUsage,
    Sidecar,
    SidecarMissing,
    TurnFailClosed,
)
from .hardening import (
    REQUIRED_FEATURES,
    REQUIRED_TOP_LEVEL,
    HardeningConfig,
    McpServerConfig,
    effective_config_mismatches,
    extra_mcp_servers,
    prepare_codex_home,
    render_config_toml,
    verify_permissions,
)
from .jobs import DispatchDecision, evaluate_dispatch
from .protocol import (
    ALLOWED_METHODS,
    EXPLICITLY_FORBIDDEN_METHODS,
    AppServerClient,
    MethodNotAllowed,
    ProtocolError,
    RemoteError,
    load_contract,
    methods_outside_contract,
)
from .routing import (
    ROUTER_POLICY_VERSION,
    ROUTES,
    Route,
    RouteMismatch,
    RouteUnavailable,
    check_reroute,
    route,
    validate_all_routes,
    validate_route,
)
from .supervisor import CodexConnectionSupervisor, LeaseTaken, ModeBusy
from .threads import (
    VERIFIER_CONTEXT_WHITELIST,
    RoleMismatch,
    RoleThread,
    ThreadRegistry,
    threads_are_disjoint,
    verifier_context,
)

__all__ = [
    "ALLOWED_METHODS",
    "EXPLICITLY_FORBIDDEN_METHODS",
    "REQUIRED_FEATURES",
    "REQUIRED_TOP_LEVEL",
    "ROUTER_POLICY_VERSION",
    "ROUTES",
    "VERIFIER_CONTEXT_WHITELIST",
    "AccountState",
    "AppServerClient",
    "BindingMismatch",
    "BoundedRunEnvelope",
    "CodexAccountAdapter",
    "CodexConnectionSupervisor",
    "CodexExecutionAdapter",
    "DispatchDecision",
    "EnvelopeExceeded",
    "EnvelopeUsage",
    "HardeningConfig",
    "IllegalTransition",
    "LeaseTaken",
    "LoginAttempt",
    "LoginUnavailable",
    "McpServerConfig",
    "MethodNotAllowed",
    "ModeBusy",
    "ModelCatalogSnapshot",
    "ModelInfo",
    "ProtocolError",
    "RemoteError",
    "RoleMismatch",
    "RoleThread",
    "Route",
    "RouteMismatch",
    "RouteUnavailable",
    "Sidecar",
    "SidecarMissing",
    "SnapshotIncomplete",
    "ThreadRegistry",
    "TurnFailClosed",
    "check_reroute",
    "effective_config_mismatches",
    "evaluate_dispatch",
    "extra_mcp_servers",
    "find_codex_bin",
    "load_contract",
    "methods_outside_contract",
    "prepare_codex_home",
    "render_config_toml",
    "route",
    "threads_are_disjoint",
    "validate_all_routes",
    "validate_route",
    "verifier_context",
    "verify_permissions",
]
