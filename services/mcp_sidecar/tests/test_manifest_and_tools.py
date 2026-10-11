"""Manifest: exactly six tools and no seventh one (DoD D13, L2504-2521)."""

from __future__ import annotations

import pytest
from conftest import build_capability
from mcp_sidecar import (
    FORBIDDEN_TOOLS,
    TOOL_NAMES,
    BackendHttpReadOnly,
    NoBackend,
    NoSuchTool,
    ToolError,
    ToolRegistry,
    manifest,
)

SIX_FROM_DOCUMENT = (
    "open_allowed_url",
    "snapshot",
    "extract",
    "screenshot",
    "wait_for",
    "request_human_takeover",
)


def test_the_manifest_has_exactly_the_six_tools_from_l2504():
    assert TOOL_NAMES == SIX_FROM_DOCUMENT
    assert tuple(t["name"] for t in manifest()) == SIX_FROM_DOCUMENT


def test_every_tool_has_an_input_schema_that_forbids_extra_fields():
    for tool in manifest():
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, (
            f"{tool['name']} przyjmuje dowolne pola — to jest kanał, którym model "
            "mógłby przemycić parametr spoza kontraktu"
        )


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_TOOLS))
def test_writing_tools_do_not_exist_in_the_manifest(forbidden):
    """“The model does not receive raw click/fill/press for writing operations” (L2521)."""
    assert forbidden not in {t["name"] for t in manifest()}


def test_calling_click_fails_as_a_nonexistent_tool(capability):
    registry = ToolRegistry(capability=capability, backend=NoBackend())
    with pytest.raises(NoSuchTool) as exc:
        registry.call("click", {"selector": "#submit"})
    assert "L2521" in str(exc.value)


def test_the_model_cannot_add_a_seventh_tool_through_capability():
    """Widening `allowed_tools` does NOT create a tool — the manifest is a code constant."""
    cap = build_capability(allowed_tools=(*TOOL_NAMES, "click", "fill"))
    registry = ToolRegistry(capability=cap, backend=NoBackend())
    assert registry.available() == SIX_FROM_DOCUMENT
    with pytest.raises(NoSuchTool):
        registry.call("fill", {"selector": "#a", "value": "b"})


def test_allowed_tools_narrowing_the_manifest_is_respected():
    cap = build_capability(allowed_tools=("snapshot", "screenshot"))
    registry = ToolRegistry(capability=cap, backend=NoBackend())
    assert registry.available() == ("snapshot", "screenshot")
    with pytest.raises(NoSuchTool):
        registry.call("open_allowed_url", {"url": "http://fixture-site:8000/"})


def test_request_human_takeover_rejects_a_reason_outside_the_closed_list(capability):
    registry = ToolRegistry(capability=capability, backend=NoBackend())
    with pytest.raises(ToolError):
        registry.call("request_human_takeover", {"reason": "BO_TAK"})
    with pytest.raises(ToolError) as exc:
        registry.call("request_human_takeover", {"reason": "IDENTITY_VERIFICATION"})
    assert "poza listą dopuszczoną w E1" in str(exc.value)


def test_request_human_takeover_passes_the_reason_to_control_plane(capability):
    reports = []
    registry = ToolRegistry(
        capability=capability,
        backend=NoBackend(),
        report_takeover=lambda reason, szczegoly: reports.append((reason, szczegoly)),
    )
    result = registry.call("request_human_takeover", {"reason": "MFA", "details": "kod SMS"})
    assert result["requested"] is True
    assert [(p.value, s) for p, s in reports] == [("MFA", "kod SMS")]


def test_a_missing_backend_fails_loudly_instead_of_returning_an_empty_result(capability):
    """An empty snapshot and a missing browser must be distinguishable."""
    registry = ToolRegistry(capability=capability, backend=NoBackend())
    with pytest.raises(ToolError):
        registry.call("snapshot", {})


def test_the_read_only_backend_has_no_writing_method():
    """The backend surface contains nothing with which a POST could be sent."""
    public_attrs = {n for n in dir(BackendHttpReadOnly) if not n.startswith("_")}
    writing = {"post", "submit", "click", "fill", "press", "type", "evaluate"}
    assert public_attrs & writing == set()
