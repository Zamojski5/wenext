"""Hardened runtime: CODEX_HOME isolation and the effective configuration (L2191-2239, DoD D12)."""

from __future__ import annotations

import os
import stat

import pytest
from codex_runtime import (
    REQUIRED_FEATURES,
    REQUIRED_TOP_LEVEL,
    AppServerClient,
    HardeningConfig,
    effective_config_mismatches,
    extra_mcp_servers,
    prepare_codex_home,
    render_config_toml,
    verify_permissions,
)

# ---------------------------------------------------------------------------
# The configuration file
# ---------------------------------------------------------------------------


def test_web_search_is_a_top_level_key_not_a_features_entry():
    """Errata to L2226: `[features].web_search` is a legacy alias, not the canonical key."""
    text = render_config_toml()
    before_features, after_features = text.split("\n[features]\n", 1)
    assert 'web_search = "disabled"' in before_features
    assert "web_search" not in after_features


def test_experimental_api_is_not_a_config_toml_key():
    """Errata to L2237: this is a handshake capability, not a file setting."""
    text = render_config_toml().lower()
    assert "experimental_api" not in text
    assert "experimentalapi" not in text


def test_all_five_flags_from_l2228_are_explicitly_disabled():
    text = render_config_toml()
    for flag in (
        "shell_tool",
        "unified_exec",
        "apps",
        "multi_agent",
        "skill_mcp_dependency_install",
    ):
        assert f"{flag} = false" in text


def test_multi_agent_v2_is_a_separate_key_and_also_disabled():
    """`multi_agent` and `multi_agent_v2` are two different keys with different defaults."""
    assert "multi_agent_v2 = false" in render_config_toml()
    assert "multi_agent_v2" in REQUIRED_FEATURES


def test_credential_store_auto_is_forbidden():
    with pytest.raises(ValueError, match="auto"):
        HardeningConfig(cli_auth_credentials_store="auto")


def test_forced_login_method_is_chatgpt():
    assert f'forced_login_method = "{REQUIRED_TOP_LEVEL["forced_login_method"]}"' in (
        render_config_toml()
    )


def test_mcp_section_requires_explicit_enabled_tools():
    with pytest.raises(ValueError, match="enabled_tools"):
        render_config_toml(HardeningConfig(mcp_server_name="wenext_browser", mcp_command="python"))


def test_mcp_section_has_required_true_and_exact_enabled_tools():
    text = render_config_toml(
        HardeningConfig(
            mcp_server_name="wenext_browser",
            mcp_command="/usr/bin/python3",
            mcp_args=("-m", "mcp_sidecar"),
            mcp_enabled_tools=("snapshot", "screenshot"),
        )
    )
    assert "[mcp_servers.wenext_browser]" in text
    assert "required = true" in text
    assert 'enabled_tools = ["snapshot", "screenshot"]' in text


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_codex_home_is_0700_and_config_is_0600(tmp_path):
    home = prepare_codex_home(tmp_path / "dom")
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "config.toml").stat().st_mode) == 0o600
    assert verify_permissions(home) == []


def test_loosened_permissions_are_detected(tmp_path):
    home = prepare_codex_home(tmp_path / "dom")
    os.chmod(home, 0o755)
    violations = verify_permissions(home)
    assert any("0700" in n for n in violations)


def test_auth_json_gets_0600(tmp_path):
    home = tmp_path / "dom"
    home.mkdir()
    (home / "auth.json").write_text("{}", encoding="utf-8")
    os.chmod(home / "auth.json", 0o644)
    prepare_codex_home(home)
    assert verify_permissions(home) == []


# ---------------------------------------------------------------------------
# The effective configuration read from the runtime — not assumed
# ---------------------------------------------------------------------------


def test_effective_config_of_a_live_runtime_is_hardened(client: AppServerClient):
    """DoD D12: "verified by reading, not by assumption"."""
    config_read = client.request("config/read", {})
    assert effective_config_mismatches(config_read) == []
    cfg = config_read["config"]
    assert cfg["web_search"] == "disabled"
    assert all(cfg["features"][f] is False for f in REQUIRED_FEATURES)


def test_missing_features_entry_counts_as_enabled():
    """All five flags have default_enabled=true — silence is NOT a disabling."""
    mismatches = effective_config_mismatches(
        {
            "config": {
                "web_search": "disabled",
                "forced_login_method": "chatgpt",
                "cli_auth_credentials_store": "file",
                "features": {},
            }
        }
    )
    assert len(mismatches) == len(REQUIRED_FEATURES)
    assert all("domyślnie WŁĄCZONE" in r for r in mismatches)


def test_web_search_other_than_disabled_is_a_mismatch():
    mismatches = effective_config_mismatches(
        {
            "config": {
                "web_search": "cached",
                "forced_login_method": "chatgpt",
                "cli_auth_credentials_store": "file",
                "features": dict.fromkeys(REQUIRED_FEATURES, False),
            }
        }
    )
    assert mismatches == ["web_search='cached', oczekiwano 'disabled'"]


def test_runtime_has_no_mcp_server_beyond_the_allowed_one(client: AppServerClient):
    """DoD L5179 — verified with the list of ACTIVE servers, not with the declaration in config."""
    active_servers = client.request("mcpServerStatus/list", {})
    assert extra_mcp_servers(active_servers, frozenset({"wenext_browser"})) == []


def test_extra_mcp_server_is_detected():
    assert extra_mcp_servers(
        {"data": [{"name": "wenext_browser"}, {"name": "cudzy_serwer"}]},
        frozenset({"wenext_browser"}),
    ) == ["cudzy_serwer"]
