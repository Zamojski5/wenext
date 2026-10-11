"""Capability from the Control Plane and the exact-origin allowlist (L2474-2495, DoD D13)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from conftest import build_capability
from mcp_sidecar import (
    FIXTURE_SITE_ORIGIN,
    CapabilityExpired,
    InvalidCapability,
    NoBackend,
    OriginRejected,
    TaskScopedCapability,
    ToolError,
    ToolRegistry,
    canonical_origin,
    forbidden_field_violations,
    is_allowed,
    load_from_file,
    save_to_file,
)
from mcp_sidecar.capability import CAPABILITY_FILE_ENV_VAR

# ---------------------------------------------------------------------------
# bound_account_ref with no handles for switching the account (L2474)
# ---------------------------------------------------------------------------


def test_capability_carries_no_profile_path_or_cdp_port(capability):
    """DoD: "the test searches the capability payload for these fields"."""
    assert forbidden_field_violations(capability.to_json()) == []
    text = json.dumps(capability.to_json())
    for forbidden in ("user_data_dir", "cdp_port", "devtools", "ws://", "/var/wenext/profiles"):
        assert forbidden not in text


@pytest.mark.parametrize(
    "poisoned",
    [
        {"bound_account_ref": "acct-1?user_data_dir=/var/wenext/profiles/a"},
        {"bound_account_ref": "ws://127.0.0.1:9222/devtools/browser/abc"},
        {"assignment_id": "assign-1;cdp_port=9222"},
        {"task_id": "task-1 remote-debugging-port"},
    ],
)
def test_capability_with_an_account_switching_handle_is_rejected(poisoned):
    with pytest.raises(InvalidCapability) as exc:
        build_capability(**poisoned)
    assert "L2474" in str(exc.value)


def test_capability_without_allowed_origins_is_rejected():
    with pytest.raises(InvalidCapability):
        build_capability(allowed_origins=())


def test_capability_without_allowed_tools_is_rejected():
    with pytest.raises(InvalidCapability):
        build_capability(allowed_tools=())


# ---------------------------------------------------------------------------
# The capability comes from the Control Plane, never from the prompt (L2495)
# ---------------------------------------------------------------------------


def test_a_missing_env_var_ends_fail_closed(monkeypatch):
    monkeypatch.delenv(CAPABILITY_FILE_ENV_VAR, raising=False)
    with pytest.raises(InvalidCapability) as exc:
        load_from_file()
    assert "L2495" in str(exc.value)


def test_a_world_readable_capability_file_is_rejected(tmp_path, capability):
    path = save_to_file(capability, tmp_path / "cap.json")
    os.chmod(path, 0o644)
    with pytest.raises(InvalidCapability) as exc:
        load_from_file(path)
    assert "0600" in str(exc.value)


def test_save_and_load_preserve_the_binding_fingerprint(tmp_path, capability):
    path = save_to_file(capability, tmp_path / "cap.json")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    loaded = load_from_file(path)
    assert loaded.binding_fingerprint() == capability.binding_fingerprint()


def test_an_incomplete_capability_names_the_missing_fields():
    with pytest.raises(InvalidCapability) as exc:
        TaskScopedCapability.from_json({"organization_id": "org-1"})
    assert "workspace_id" in str(exc.value)


def test_an_expired_capability_ends_the_turn(tmp_path):
    cap = build_capability(expiry=datetime.now(UTC) - timedelta(seconds=1))
    assert cap.is_expired()
    with pytest.raises(CapabilityExpired):
        cap.require_valid()


def test_a_tool_does_not_accept_capability_in_its_arguments(capability):
    """The model has no channel through which it could substitute its own capability."""
    registry = ToolRegistry(capability=capability, backend=NoBackend())
    with pytest.raises(ToolError):
        # `allowed_origins` in the arguments is ignored — validation goes by the capability.
        registry.call(
            "open_allowed_url",
            {"url": "http://zly.example.com/", "allowed_origins": ["http://zly.example.com"]},
        )


# ---------------------------------------------------------------------------
# Exact-origin allowlist; eTLD+1 does not authorize (L2486, DoD D13)
# ---------------------------------------------------------------------------


def test_the_origin_is_canonicalized_before_comparison():
    assert canonical_origin("HTTP://Fixture-Site:8000/sciezka?a=1") == "http://fixture-site:8000"
    assert canonical_origin("http://example.com:80/") == "http://example.com"
    assert canonical_origin("https://example.com:443/") == "https://example.com"


def test_the_same_etld1_but_a_different_host_is_rejected():
    """DoD: "a host outside the exact-origin allowlist but in the same eTLD+1 is rejected"."""
    allowlist = ("https://fixture-site.example.com",)
    assert is_allowed("https://fixture-site.example.com/a", allowlist) is True
    assert is_allowed("https://zly.example.com/a", allowlist) is False
    assert is_allowed("https://zly.fixture-site.example.com/a", allowlist) is False
    assert is_allowed("https://fixture-site.example.com.zly.pl/a", allowlist) is False


def test_a_different_port_is_a_different_origin():
    assert is_allowed("http://fixture-site:8001/", (FIXTURE_SITE_ORIGIN,)) is False


def test_a_different_scheme_is_a_different_origin():
    assert is_allowed("https://fixture-site:8000/", (FIXTURE_SITE_ORIGIN,)) is False


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<h1>x</h1>",
        "javascript:alert(1)",
        "about:blank",
        "devtools://devtools/bundled/inspector.html",
    ],
)
def test_local_and_injecting_schemes_are_forbidden(url):
    with pytest.raises(OriginRejected):
        canonical_origin(url)


def test_in_f0_the_only_allowed_origin_is_the_fixture_site(capability):
    assert capability.allowed_origins == (FIXTURE_SITE_ORIGIN,)
    registry = ToolRegistry(capability=capability, backend=NoBackend())
    with pytest.raises(ToolError) as exc:
        registry.call("open_allowed_url", {"url": "https://www.linkedin.com/feed/"})
    assert "nie jest na allowliście" in str(exc.value)
