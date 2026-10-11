"""The tools are read-only: none of them writes on the external site (DoD D13).

The evidence is behavioural, not declarative: the fixture site has a form with `POST /post`
that appends to the write counter. After going through ALL six tools the counter must still
be zero — and there must exist no tool with which it could be raised.
"""

from __future__ import annotations

import contextlib

import pytest
from conftest import build_capability
from mcp_sidecar import (
    TOOL_NAMES,
    BackendHttpReadOnly,
    NoSuchTool,
    ToolError,
    ToolRegistry,
)
from mcp_sidecar.origins import canonical_origin


@pytest.fixture
def registry(fixture_site):
    cap = build_capability(allowed_origins=(canonical_origin(fixture_site),))
    return ToolRegistry(capability=cap, backend=BackendHttpReadOnly())


def test_a_full_session_of_the_six_tools_writes_nothing(registry, fixture_site, records):
    opened = registry.call("open_allowed_url", {"url": f"{fixture_site}/"})
    assert opened["opened"] is True and opened["status"] == 200

    page_snapshot = registry.call("snapshot", {})
    assert "Community fixture" in page_snapshot["text"]

    extracted = registry.call(
        "extract", {"schema": {"type": "object", "properties": {"tytul": {"type": "string"}}}}
    )
    assert extracted["source"]["url"].startswith(fixture_site)

    wait_result = registry.call("wait_for", {"condition": "Community fixture"})
    assert wait_result["satisfied"] is True

    with pytest.raises(ToolError):
        # The HTTP backend does not render — screenshot must fail EXPLICITLY, not return blank.
        registry.call("screenshot", {})

    registry.call("request_human_takeover", {"reason": "CHALLENGE"})

    assert records == [], "któreś z narzędzi wykonało zapis na stronie zewnętrznej"


def test_no_tool_of_the_six_can_submit_the_form(registry, fixture_site, records):
    attempts = [
        ("open_allowed_url", {"url": f"{fixture_site}/post"}),
        ("wait_for", {"condition": "submit"}),
        ("extract", {"schema": {"type": "object"}}),
    ]
    for nazwa, args in attempts:
        with contextlib.suppress(ToolError):
            registry.call(nazwa, args)
    assert records == []

    for writing in ("click", "fill", "press", "submit", "evaluate"):
        with pytest.raises(NoSuchTool):
            registry.call(writing, {"selector": "button", "value": "x"})
    assert records == []


def test_open_allowed_url_on_another_host_never_reaches_the_network(registry, records):
    with pytest.raises(ToolError):
        registry.call("open_allowed_url", {"url": "http://127.0.0.1:1/"})
    assert records == []


def test_all_six_tools_are_marked_read_only():
    from mcp_sidecar.tools import TOOLS

    assert all(TOOLS[n].read_only for n in TOOL_NAMES)
