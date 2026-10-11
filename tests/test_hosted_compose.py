"""The hosted MCP service in the compose files: what it holds and what it must NOT hold.

`mcp-edge` is reachable from the internet (through the Cloudflare Worker and Caddy) and serves
strangers' agents, so its container is the one place where a leaked capsule token, a browser
reader URL or a Reddit credential would matter most. The rule is an ALLOWLIST of environment
variables, not a blocklist: a new variable in that service is a deliberate edit of this file,
visible in a diff, and never a copy-paste from `api`.

Read from the YAML, no Docker needed (same approach as `test_prod_overlay.py`).
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.yml"
OVERLAY = ROOT / "compose.prod.yml"

#: Everything the hosted service may be told. `DATABASE_URL` and `POSTGRES_*` for the database,
#: `WENEXT_EDGE_SECRET` for the edge's signed header, `SERPER_API_KEY` for the SERP read, the
#: rest is process hygiene or a limit.
ALLOWED_ENV = frozenset(
    {
        "DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "WENEXT_ENV",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "TZ",
        "WENEXT_PUBLIC_DOMAIN",
        "WENEXT_EDGE_SECRET",
        "SERPER_API_KEY",
        "WENEXT_HOSTED_CALLS_PER_HOUR",
    }
)

#: What must never reach it, whatever the allowlist above says one day: spelled out so the failure
#: message names the secret that leaked.
FORBIDDEN = re.compile(
    r"CAPSULE|GATEWAY|BROWSER|READER|WRITER|STREAM|CODEX|OPENAI|REDDIT_|X_|LOCAL_AUTOLOGIN|"
    r"OPERATOR|ISSUER",
    re.IGNORECASE,
)


class _Loader(yaml.SafeLoader):
    """SafeLoader that understands compose's `!reset` (see `test_prod_overlay.py`)."""


def _reset(loader: yaml.SafeLoader, node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_Loader.add_constructor("!reset", _reset)


def _load(path: pathlib.Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


@pytest.fixture(scope="module")
def base() -> dict:
    return _load(BASE)


@pytest.fixture(scope="module")
def overlay() -> dict:
    return _load(OVERLAY)


def test_the_service_exists_only_under_its_own_profile(base) -> None:
    service = base["services"]["mcp-edge"]
    assert service["profiles"] == ["hosted"], "it must never start with a plain `docker compose up`"
    assert "ports" not in service, "nothing is published: the proxy reaches it by name"
    assert "volumes" not in service, "no profiles, no CODEX_HOME, no artifacts"
    assert service["user"] == "1000:1000" and service["cap_drop"] == ["ALL"]


def test_its_environment_is_exactly_the_allowlist(base) -> None:
    environment = base["services"]["mcp-edge"]["environment"]
    names = set(environment)
    assert names == ALLOWED_ENV, (
        f"unexpected: {sorted(names - ALLOWED_ENV)}, missing: {sorted(ALLOWED_ENV - names)}"
    )
    leaked = sorted(name for name in names if FORBIDDEN.search(name))
    assert not leaked, f"capsule, gateway, browser or credential variables in mcp-edge: {leaked}"
    # The Reddit client the founder's own stack uses is not in this container either.
    assert not any("REDDIT" in name for name in names)


def test_it_depends_on_the_database_and_on_nothing_with_a_browser(base) -> None:
    depends = base["services"]["mcp-edge"]["depends_on"]
    assert set(depends) == {"db"}
    assert depends["db"]["condition"] == "service_healthy"


def test_it_runs_the_hosted_app_without_an_access_log(base) -> None:
    """The uvicorn access log prints the request path, and for the token door the path is the
    secret."""
    command = base["services"]["mcp-edge"]["command"]
    assert "control_plane.hosted.http_app:create_app" in command
    assert "--factory" in command
    assert "--no-access-log" in command


def test_it_shares_the_networks_the_database_and_the_proxy_are_on(base) -> None:
    service = base["services"]["mcp-edge"]
    assert set(service["networks"]) == {"capsulenet", "edgenet"}


def test_the_overlay_gives_it_restart_limits_a_patient_healthcheck_and_rotated_logs(
    overlay,
) -> None:
    service = overlay["services"]["mcp-edge"]
    assert service["restart"] == "unless-stopped"
    limits = service["deploy"]["resources"]["limits"]
    assert float(limits["cpus"]) > 0 and limits["memory"]
    timeout = str(service["healthcheck"]["timeout"])
    assert timeout.endswith("s") and int(timeout[:-1]) > 5, "the 5 s that lied under load"
    options = service["logging"]["options"]
    assert options["max-size"] and options["max-file"]


def test_the_proxy_routes_both_doors_to_it_and_does_not_wait_for_it(overlay) -> None:
    caddyfile = overlay["configs"]["caddyfile"]["content"]
    assert "@mcp path /mcp /mcp/*" in caddyfile
    block = caddyfile[caddyfile.index("handle @mcp") :]
    block = block[: block.index("handle /healthz")]
    assert "reverse_proxy mcp-edge:8780" in block
    assert "request_body" in block and "max_size" in block
    assert not re.search(r"^\s*log\b", block, re.MULTILINE), "the path is a secret: no access log"
    # A profile-gated dependency would break `docker compose config` when the profile is off.
    assert "mcp-edge" not in overlay["services"]["caddy"].get("depends_on", {})


def test_the_caddyfile_placeholders_survive_compose_interpolation(overlay) -> None:
    """Compose interpolates `$VAR` inside `configs.content`, so a Caddy placeholder written as
    `{$VAR}` reaches Caddy as `{value}`, a name in braces that Caddy refuses. `$$` is how a
    literal `$` is written (measured with `caddy validate`, 11.10)."""
    caddyfile = overlay["configs"]["caddyfile"]["content"]
    assert "{$$WENEXT_PUBLIC_DOMAIN} {" in caddyfile
    assert "email {$$WENEXT_TLS_EMAIL}" in caddyfile
    assert not re.search(r"(?<!\$)\{\$[A-Z]", caddyfile), "a bare `{$VAR}` would be interpolated"


def test_no_long_dash_in_what_this_change_added(base, overlay) -> None:
    for name, document in (("base", base), ("overlay", overlay)):
        service = yaml.dump(document["services"]["mcp-edge"], allow_unicode=True)
        assert "\u2014" not in service and "\u2013" not in service, name
