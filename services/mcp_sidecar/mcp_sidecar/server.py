"""The sidecar's MCP server — stdio, JSON-RPC 2.0, exactly six tools.

How to run it:

    WENEXT_MCP_CAPABILITY_FILE=/run/wenext/cap.json python -m mcp_sidecar

Fail-closed is literal here: if the capability cannot be loaded, cannot be validated or has
already expired, the process EXITS with a non-zero code before it answers `initialize`.
Paired with `required = true` in the Codex configuration this gives the behaviour from
L2503 — a missing sidecar ends the turn instead of letting the model work without tools.

We implement the subset of MCP that suffices for this role: `initialize`,
`notifications/initialized`, `tools/list`, `tools/call`, `ping`. There is deliberately no
`resources/*`, `prompts/*` nor anything that would allow adding a tool at run time —
the boundary must not rest on the experimental `dynamicTools` (L2500).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

from .browser import BrowserBackend, NoBackend
from .capability import (
    CapabilityExpired,
    InvalidCapability,
    TaskScopedCapability,
    load_from_file,
)
from .tools import NoSuchTool, ToolError, ToolRegistry, manifest

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "wenext-task-scoped-browser"
SERVER_VERSION = "0.1.0"

CODE_METHOD_NOT_FOUND = -32601
CODE_INVALID_PARAMS = -32602
CODE_INTERNAL_ERROR = -32603


@dataclass
class MCPServer:
    """The protocol loop. Knows neither the browser nor Codex — only capability and registry."""

    capability: TaskScopedCapability
    registry: ToolRegistry
    wejscie: TextIO = field(default_factory=lambda: sys.stdin)
    output_dir: TextIO = field(default_factory=lambda: sys.stdout)
    initialized: bool = field(default=False, init=False)

    def _respond(self, ident: Any, result: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": ident, "result": result})

    def _error(self, ident: Any, kod: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": ident, "error": {"code": kod, "message": message}})

    def _write(self, frame: dict[str, Any]) -> None:
        self.output_dir.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self.output_dir.flush()

    def handle(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        ident = frame.get("id")
        params = frame.get("params") or {}

        if method == "initialize":
            self.initialized = True
            self._respond(
                ident,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    # We declare ONLY tools. The absence of `resources` and `prompts` is
                    # deliberate: a smaller surface is fewer things to overlook.
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
            return

        if method in {"notifications/initialized", "initialized"}:
            return

        if ident is None:
            return  # unknown notification — we stay silent, notifications are not answered

        if not self.initialized:
            self._error(ident, CODE_INTERNAL_ERROR, "handshake `initialize` nie został wykonany")
            return

        if method == "ping":
            self._respond(ident, {})
            return

        if method == "tools/list":
            available = set(self.registry.available())
            self._respond(ident, {"tools": [t for t in manifest() if t["name"] in available]})
            return

        if method == "tools/call":
            self._call_tool(ident, params)
            return

        self._error(
            ident,
            CODE_METHOD_NOT_FOUND,
            f"metoda {method!r} nie jest obsługiwana przez ten sidecar",
        )

    def _call_tool(self, ident: Any, params: dict[str, Any]) -> None:
        nazwa = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(nazwa, str):
            self._error(ident, CODE_INVALID_PARAMS, "tools/call wymaga pola `name`")
            return
        try:
            result = self.registry.call(nazwa, args)
        except (NoSuchTool, CapabilityExpired) as exc:
            # A nonexistent tool and an expired capability are PROTOCOL errors, not content.
            # The model is not to "handle" them — the turn is to stop on them.
            self._error(ident, CODE_METHOD_NOT_FOUND, str(exc))
            return
        except ToolError as exc:
            # An execution error comes back as `isError` in the content, per MCP — the model
            # is to see that the attempt failed, and not pretend it received a result.
            self._respond(
                ident,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
            return
        self._respond(
            ident,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                ],
                "isError": False,
            },
        )

    def serve(self) -> int:
        for line in self.wejscie:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                self._error(None, CODE_INVALID_PARAMS, "nieparsowalna ramka JSON")
                continue
            self.handle(frame)
        return 0


def build_server(
    backend: BrowserBackend | None = None,
    capability: TaskScopedCapability | None = None,
) -> MCPServer:
    """Builds the server, loading the capability ONLY from the Control Plane (L2495)."""
    cap = capability or load_from_file()
    cap.require_valid()
    registry = ToolRegistry(capability=cap, backend=backend or NoBackend())
    return MCPServer(capability=cap, registry=registry)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Any problem with the capability = exit code 2, before the handshake."""
    del argv
    try:
        serwer = build_server()
    except (InvalidCapability, CapabilityExpired) as exc:
        print(f"[mcp-sidecar] fail-closed: {exc}", file=sys.stderr)
        return 2
    return serwer.serve()


if __name__ == "__main__":
    sys.exit(main())
