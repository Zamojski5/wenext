"""Exactly the six read-only Browser MCP tools (L2504-2511).

    open_allowed_url()
    snapshot()
    extract(schema)
    screenshot()
    wait_for(condition)
    request_human_takeover(reason)

There is no seventh one and it cannot be added from the model's level: the manifest is built
from the `TOOLS` constant, dispatch walks the same constant, and `enabled_tools` in the Codex
configuration is a subset of it. There is no path by which prompt content influences this
list — including `dynamicTools`, which L2500 forbids resting the boundary on.

"The model does not receive raw `click/fill/press` for external writing operations"
(L2521) is realized here as simply as it can be: there are no such functions in this module,
and the browser backend (`browser.BrowserBackend`) exposes no method that would make them
possible. Writing actions go through the Action Adapter (L2513-2519), outside this process.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from community_os_contracts import E1_ALLOWED_INTERACTIVE_REASONS, InteractiveReason

from .browser import BackendError, BrowserBackend
from .capability import TaskScopedCapability
from .origins import OriginRejected, require_allowed

#: The names in the order from L2504-2511. The order is part of the manifest contract.
TOOL_NAMES: tuple[str, ...] = (
    "open_allowed_url",
    "snapshot",
    "extract",
    "screenshot",
    "wait_for",
    "request_human_takeover",
)

#: Tools the model WOULD get in a typical Browser MCP, and which are not here.
#: We keep them named so that a test can check their absence by name (L2521).
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        "click",
        "fill",
        "press",
        "type",
        "select_option",
        "upload_file",
        "submit",
        "evaluate",
        "execute_script",
        "set_cookie",
        "goto",
        "navigate",
    }
)


class ToolError(Exception):
    """The tool refused to execute. The content reaches the model as `isError`."""


class NoSuchTool(ToolError):
    """A call to a tool outside the manifest — including one that exists in another MCP."""


class HumanTakeoverRequested(ToolError):
    """`request_human_takeover` is not a model error, but a signal to the Control Plane."""


@dataclass(frozen=True)
class Tool:
    nazwa: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True


TOOLS: dict[str, Tool] = {
    "open_allowed_url": Tool(
        nazwa="open_allowed_url",
        description=(
            "Otwiera URL, którego origin jest DOKŁADNIE na allowliście task-scoped "
            "capability. Nie przyjmuje konta ani profilu — konto wynika z bindingu."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Pełny URL do otwarcia."},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    ),
    "snapshot": Tool(
        nazwa="snapshot",
        description="Zwraca ustrukturyzowany zrzut bieżącej strony. Nie modyfikuje strony.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "extract": Tool(
        nazwa="extract",
        description=(
            "Wyciąga dane z bieżącej strony wg podanego JSON Schema. Schema opisuje "
            "kształt wyniku, nie sposób nawigacji."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "schema": {"type": "object", "description": "JSON Schema oczekiwanego wyniku."},
            },
            "required": ["schema"],
            "additionalProperties": False,
        },
    ),
    "screenshot": Tool(
        nazwa="screenshot",
        description="Zrzut ekranu bieżącej strony jako PNG (base64). Nie modyfikuje strony.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "wait_for": Tool(
        nazwa="wait_for",
        description="Czeka na warunek na bieżącej stronie. Nie klika i nic nie wpisuje.",
        input_schema={
            "type": "object",
            "properties": {
                "condition": {"type": "string", "description": "Opis warunku do spełnienia."},
                "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 60000},
            },
            "required": ["condition"],
            "additionalProperties": False,
        },
    ),
    "request_human_takeover": Tool(
        nazwa="request_human_takeover",
        description=(
            "Zgłasza, że dalej potrzebny jest człowiek. Powód musi należeć do zamkniętej "
            "listy dopuszczonej w E1; powód spoza listy tworzy alert, a nie cichy wyjątek."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": sorted(r.value for r in E1_ALLOWED_INTERACTIVE_REASONS),
                },
                "details": {"type": "string"},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        read_only=True,
    ),
}

assert tuple(TOOLS) == TOOL_NAMES, "manifest musi mieć dokładnie sześć narzędzi z L2504"


def manifest() -> list[dict[str, Any]]:
    """The manifest in the MCP `tools/list` format."""
    return [
        {"name": n.nazwa, "description": n.description, "inputSchema": n.input_schema}
        for n in TOOLS.values()
    ]


@dataclass
class ToolRegistry:
    """Tool dispatch with a hard check of the capability and of the origin allowlist."""

    capability: TaskScopedCapability
    backend: BrowserBackend
    #: Callback to the Control Plane for `request_human_takeover`. The sidecar decides nothing.
    report_takeover: Callable[[InteractiveReason, str], None] | None = None

    def available(self) -> tuple[str, ...]:
        """Intersection of the manifest and the capability's `allowed_tools` — never a union."""
        return tuple(n for n in TOOL_NAMES if n in self.capability.allowed_tools)

    def call(self, nazwa: str, args: dict[str, Any]) -> dict[str, Any]:
        if nazwa in FORBIDDEN_TOOLS:
            raise NoSuchTool(
                f"narzędzie {nazwa!r} nie istnieje w tym sidecarze. Operacje zapisujące "
                "idą przez Action Adapter po zatwierdzeniu, nie przez surowe click/fill/press "
                "(L2521)."
            )
        if nazwa not in TOOLS:
            raise NoSuchTool(f"narzędzie {nazwa!r} nie istnieje w manifeście sidecara")
        if nazwa not in self.capability.allowed_tools:
            raise NoSuchTool(f"narzędzie {nazwa!r} nie jest w `allowed_tools` tego capability")
        self.capability.require_valid()

        method = getattr(self, f"_{nazwa}")
        return method(args)

    # -- implementations ------------------------------------------------------

    def _open_allowed_url(self, args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        if not isinstance(url, str) or not url:
            raise ToolError("open_allowed_url wymaga niepustego `url`")
        try:
            require_allowed(url, self.capability.allowed_origins)
        except OriginRejected as exc:
            raise ToolError(str(exc)) from exc
        try:
            result = self.backend.open_url(url)
        except BackendError as exc:
            raise ToolError(str(exc)) from exc
        return {"opened": True, **result}

    def _snapshot(self, _args: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.backend.snapshot()
        except BackendError as exc:
            raise ToolError(str(exc)) from exc

    def _extract(self, args: dict[str, Any]) -> dict[str, Any]:
        schema = args.get("schema")
        if not isinstance(schema, dict) or not schema:
            raise ToolError("extract wymaga niepustego `schema` (JSON Schema wyniku)")
        try:
            page_snapshot = self.backend.snapshot()
        except BackendError as exc:
            raise ToolError(str(exc)) from exc
        # The sidecar does not infer for the model — it returns the material and the schema,
        # the model fills it in. Guessing values here would introduce a second, invisible
        # extractor.
        return {"schema": schema, "source": page_snapshot}

    def _screenshot(self, _args: dict[str, Any]) -> dict[str, Any]:
        import base64

        try:
            png = self.backend.screenshot()
        except BackendError as exc:
            raise ToolError(str(exc)) from exc
        return {"mime_type": "image/png", "base64": base64.b64encode(png).decode("ascii")}

    def _wait_for(self, args: dict[str, Any]) -> dict[str, Any]:
        condition = args.get("condition")
        if not isinstance(condition, str) or not condition:
            raise ToolError("wait_for wymaga niepustego `condition`")
        timeout = int(args.get("timeout_ms", 5000))
        try:
            return self.backend.wait_for(condition, timeout)
        except BackendError as exc:
            raise ToolError(str(exc)) from exc

    def _request_human_takeover(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = args.get("reason")
        try:
            reason = InteractiveReason(raw)
        except ValueError as exc:
            raise ToolError(
                f"powód {raw!r} nie jest wartością InteractiveReason. Lista jest zamknięta."
            ) from exc
        if reason not in E1_ALLOWED_INTERACTIVE_REASONS:
            raise ToolError(
                f"powód {reason} jest poza listą dopuszczoną w E1 — wymaga decyzji operatora "
                "i alertu, a nie cichego przejścia (L5518)"
            )
        szczegoly = str(args.get("details", ""))
        if self.report_takeover is not None:
            self.report_takeover(reason, szczegoly)
        return {
            "requested": True,
            "reason": reason.value,
            "note": "Control Plane decyduje o trybie INTERACTIVE; sidecar nie otwiera kapsuły.",
        }
