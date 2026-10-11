"""Hardened Codex runtime — an isolated `CODEX_HOME` and `config.toml`, section 13.3 (L2191-2239).

The document lists the hardened runtime artifact (L2224-2233) and says outright what the runtime
does not have: Chromium profile mounts, the database, secrets, the Docker socket, a shell, unified
exec, web search and superfluous MCP. This module covers the part that is a configuration file;
the rest is enforced by docker-compose (`codex` has no `profiles` volume, no `capsulenet`, no DSN).

Two factual corrections against the document, confirmed by running it — the full errata is
in NOTES.md:

* `experimentalApi` is NOT a `config.toml` key (L2237). It is a capability negotiated
  in the `initialize` handshake. It lives in `protocol.AppServerClient.experimental_api`.
* `web_search` is a TOP-LEVEL key, and not an entry in `[features]` (L2226). `[features]`
  accepts `web_search` as a legacy alias, but the canonical form is at the top level.

All five `[features]` flags from L2228-2232 have `default_enabled = true`, so a MISSING entry
means ENABLED. Hardening has to disable them explicitly.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Top-level keys that must have exactly these values in the effective configuration.
REQUIRED_TOP_LEVEL: dict[str, Any] = {
    "web_search": "disabled",
    "forced_login_method": "chatgpt",
}

#: Values `web_search` may take. ALL FOUR ARE MEASURED, on the pinned 0.147.0, 20.09: a config
#: with `web_search = "enabled"` made `config/read` answer
#: `[-32603] invalid configuration: unknown variant `enabled`, expected one of `disabled`,
#: `cached`, `indexed`, `live``. The guessed token did not exist, and nothing but a live read
#: would have said so. `codex_runtime.config_probe` is that read.
WEB_SEARCH_MODES: frozenset[str] = frozenset({"disabled", "cached", "indexed", "live"})

#: The hardened value. A profile that does not say otherwise gets this one, so the pilot keeps
#: exactly the configuration D12 was measured against.
WEB_SEARCH_OFF = "disabled"

#: The `[features]` flags from L2228-2232 plus `multi_agent_v2`, because `multi_agent` and
#: `multi_agent_v2` are TWO different keys and disabling the first does not disable the second.
REQUIRED_FEATURES: dict[str, bool] = {
    "shell_tool": False,
    "unified_exec": False,
    "apps": False,
    "multi_agent": False,
    "multi_agent_v2": False,
    "skill_mcp_dependency_install": False,
}


@dataclass(frozen=True)
class McpServerConfig:
    """One entry of `[mcp_servers]`: what to start, and exactly which tools it may expose.

    Added for P2, where a session needs the product's own tool server (`wenext`) and a runtime
    may already carry the browser sidecar. Before this, `HardeningConfig` could describe exactly
    one server, and "add a second one" meant either dropping the first or writing a second
    `config.toml` from another module. Two writers of one file is how two definitions of the
    boundary start.

    `enabled_tools` is REQUIRED and may not be empty, for the reason L2502 gives: an empty list
    reads as "whatever the server offers", which is a boundary that moves when the server does.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("serwer MCP bez nazwy nie ma jak trafić do [mcp_servers.<nazwa>]")
        if not self.command:
            raise ValueError(
                f"mcp_servers.{self.name} bez `command` — sidecar nie miałby jak wystartować"
            )
        if not self.enabled_tools:
            raise ValueError(
                f"mcp_servers.{self.name}: `enabled_tools` jest puste. L2502 wymaga exact "
                "`enabled_tools`; pusta lista byłaby cichym dopuszczeniem wszystkiego, "
                "co serwer wystawi."
            )


@dataclass(frozen=True)
class HardeningConfig:
    """The input for generating `config.toml`.

    `cli_auth_credentials_store` defaults to `"file"`, because L2200-2203 requires outright
    `cli_auth_credentials_store = "file"` and `auth.json` with 0600. The `"ephemeral"` variant
    (credentials only in the process memory) exists in the enum and is better wherever
    persistence is not needed — but in F0 the login is a one-off human act and it has to
    survive a runtime restart, so `"file"` is the choice consistent with the document.
    """

    cli_auth_credentials_store: str = "file"
    #: Whether THIS runtime may use the engine's own web search. Default `"disabled"`, so every
    #: caller that predates the product profile keeps the hardened configuration unchanged; only
    #: a caller that says otherwise gets search, and it says so for one session kind at a time
    #: (D-046). The five `[features]` flags are NOT part of this choice: they stay off in every
    #: profile, because they are what gives the model a shell.
    web_search: str = WEB_SEARCH_OFF
    forced_chatgpt_workspace_id: str | None = None
    #: The name of the sidecar MCP server. `None` = no `[mcp_servers]` section from these
    #: fields. KEPT as the single-server form every caller before P2 uses; `servers()` folds it
    #: into the list so there is one code path rendering the file.
    mcp_server_name: str | None = None
    mcp_command: str | None = None
    mcp_args: tuple[str, ...] = ()
    mcp_enabled_tools: tuple[str, ...] = ()
    mcp_env: dict[str, str] | None = None
    #: Additional servers, for a runtime that needs more than one (P2: the session's `wenext`
    #: tool server alongside whatever else the deployment configures).
    mcp_servers: tuple[McpServerConfig, ...] = ()

    def servers(self) -> tuple[McpServerConfig, ...]:
        """Every configured server, the legacy single-server fields first.

        One list, one renderer. The alternative, rendering the old fields in one place and the
        new tuple in another, would let the two disagree about `required` or `enabled_tools`.
        """
        entries: list[McpServerConfig] = []
        if self.mcp_server_name:
            entries.append(
                McpServerConfig(
                    name=self.mcp_server_name,
                    command=self.mcp_command or "",
                    args=self.mcp_args,
                    enabled_tools=self.mcp_enabled_tools,
                    env=self.mcp_env,
                )
            )
        entries.extend(self.mcp_servers)
        names = [entry.name for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError(
                f"dwa serwery MCP o tej samej nazwie: {sorted(names)}. TOML zostawiłby tylko "
                "jedną sekcję, a druga zniknęłaby po cichu razem ze swoją listą narzędzi"
            )
        return tuple(entries)

    def __post_init__(self) -> None:
        if self.web_search not in WEB_SEARCH_MODES:
            raise ValueError(
                f"web_search={self.web_search!r} nie jest wartością WebSearchMode; "
                f"dozwolone: {sorted(WEB_SEARCH_MODES)}"
            )
        if self.cli_auth_credentials_store not in {"file", "keyring", "auto", "ephemeral"}:
            raise ValueError(
                f"cli_auth_credentials_store={self.cli_auth_credentials_store!r} nie jest "
                "wartością enuma AuthCredentialsStoreMode"
            )
        if self.cli_auth_credentials_store == "auto":
            raise ValueError(
                "cli_auth_credentials_store='auto' jest zakazane: L2205 mówi wprost, że "
                "CODEX_HOME nie daje izolacji, jeżeli credential store zostaje na `auto` "
                "albo na wspólnym keyringu."
            )


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config_toml(config: HardeningConfig | None = None) -> str:
    """Returns the contents of the hardened `config.toml`."""
    config = config or HardeningConfig()
    lines = [
        "# Wygenerowane przez codex_runtime.hardening — nie edytuj ręcznie.",
        "# Artefakt hardened runtime z sekcji 13.3 (L2224-2233).",
        "",
        "# `web_search` jest kluczem TOP-LEVEL (enum WebSearchMode), a nie wpisem w [features].",
        f"web_search = {_toml_string(config.web_search)}",
        "",
        "# L2200-2203: dedykowany CODEX_HOME 0700 nie izoluje, jeżeli poświadczenia idą",
        "# do wspólnego keyringu. Dlatego store jest jawny, nigdy `auto`.",
        f"cli_auth_credentials_store = {_toml_string(config.cli_auth_credentials_store)}",
        f"forced_login_method = {_toml_string(REQUIRED_TOP_LEVEL['forced_login_method'])}",
    ]
    if config.forced_chatgpt_workspace_id:
        lines.append(
            f"forced_chatgpt_workspace_id = {_toml_string(config.forced_chatgpt_workspace_id)}"
        )
    lines += [
        "",
        "# Wszystkie poniższe flagi mają default_enabled = true. Brak wpisu = WŁĄCZONE.",
        "[features]",
    ]
    lines += [f"{key} = {str(value).lower()}" for key, value in REQUIRED_FEATURES.items()]

    if config.mcp_server_name and not config.mcp_command:
        raise ValueError("mcp_server_name bez mcp_command — sidecar nie miałby jak wystartować")
    if config.mcp_server_name and not config.mcp_enabled_tools:
        raise ValueError(
            "mcp_enabled_tools jest puste. L2502 wymaga exact `enabled_tools`; pusta lista "
            "byłaby cichym dopuszczeniem wszystkiego, co serwer wystawi."
        )
    for server in config.servers():
        args = ", ".join(_toml_string(a) for a in server.args)
        tools = ", ".join(_toml_string(t) for t in server.enabled_tools)
        lines += [
            "",
            "# L2502: konfiguracja Codexa wskazuje TYLKO zadeklarowane sidecary, required=true",
            "# i dokładny enabled_tools. Brak sidecara kończy turn fail-closed (L2503).",
            f"[mcp_servers.{server.name}]",
            f"command = {_toml_string(server.command)}",
            f"args = [{args}]",
            "enabled = true",
            "required = true",
            f"enabled_tools = [{tools}]",
        ]
        if server.env:
            pairs = ", ".join(
                f"{_toml_string(k)} = {_toml_string(v)}" for k, v in sorted(server.env.items())
            )
            lines.append(f"env = {{ {pairs} }}")
    lines.append("")
    return "\n".join(lines)


def mcp_server_override(server: McpServerConfig) -> str:
    """Jeden wpis `[mcp_servers.<nazwa>]` jako wartość do `--config`, w jednej linii.

    PO CO (E9, 21.09). Wpis serwera narzędzi jest RÓŻNY DLA KAŻDEJ TURY, bo niesie ścieżkę do jej
    pliku zdolności, a `config.toml` jest WSPÓLNY dla wszystkich rozmów na jednym połączeniu i dla
    pilota. Pisanie go do pliku znaczyło, że dwie rozmowy jednej organizacji nadpisują sobie
    zdolność nawzajem; podanie go przy starcie procesu znaczy, że każda tura mówi tylko za siebie.

    Renderer jest TUTAJ, obok tego, który pisze plik, bo obie postacie opisują to samo i mają się
    zmieniać razem. Test porównuje je pole po polu.

    Zmierzone na przypiętej binarce 0.147.0: `--config mcp_servers.wenext={...}` z mapą `env`
    w jednej wartości jest przyjmowane, wraca z `config/read` co do pola, a serwer naprawdę wstaje
    i widzi swoje środowisko (`config_probe --mcp-overrides`, przypadek z żywą atrapą).
    """
    args = ", ".join(_toml_string(a) for a in server.args)
    tools = ", ".join(_toml_string(t) for t in server.enabled_tools)
    fields = [
        f"command={_toml_string(server.command)}",
        f"args=[{args}]",
        "enabled=true",
        # `required=true` zostaje z tego samego powodu, co w pliku: brak serwera narzędzi ma
        # zatrzymać turę (fail-closed, L2503), a nie dać modelowi pracować bez narzędzi.
        "required=true",
        f"enabled_tools=[{tools}]",
    ]
    if server.env:
        pairs = ", ".join(
            f"{_toml_string(k)}={_toml_string(v)}" for k, v in sorted(server.env.items())
        )
        fields.append(f"env={{{pairs}}}")
    return f"mcp_servers.{server.name}={{{', '.join(fields)}}}"


def turn_config_overrides(config: HardeningConfig | None = None) -> tuple[str, ...]:
    """Cała konfiguracja tury jako argumenty `--config`, bez dotykania `config.toml`.

    Niesie to samo, co pisał plik, poza rzeczami, które należą do DOMU, a nie do tury
    (`cli_auth_credentials_store`, `forced_login_method`): tamte ustawia się raz, przy zakładaniu
    katalogu połączenia, i zmiana ich co turę nic by nie znaczyła, bo logowanie już istnieje.

    Flagi `[features]` jadą tu MIMO że stoją w pliku, i to jest świadome: dotąd każda tura
    przepisywała plik, więc przy okazji ZERUJE drift; po przeniesieniu wpisu na `--config` nikt
    by tego nie robił, a to są flagi, które dają modelowi powłokę. Taniej jest powtórzyć je
    w argumentach, niż liczyć na to, że nikt pliku nie tknął.
    """
    config = config or HardeningConfig()
    overrides = [f"web_search={_toml_string(config.web_search)}"]
    overrides += [
        f"features.{key}={str(value).lower()}" for key, value in REQUIRED_FEATURES.items()
    ]
    overrides += [mcp_server_override(server) for server in config.servers()]
    return tuple(overrides)


def prepare_codex_home(path: Path, config: HardeningConfig | None = None) -> Path:
    """Creates a dedicated `CODEX_HOME` with 0700 permissions and writes the hardened config.

    L2199-2203: "dedicated CODEX_HOME with 0700", "auth.json with 0600".
    We do not assume the permissions — we set them and verify with `zweryfikuj_uprawnienia`.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)

    file_path = path / "config.toml"
    file_path.write_text(render_config_toml(config), encoding="utf-8")
    os.chmod(file_path, 0o600)

    auth = path / "auth.json"
    if auth.exists():
        os.chmod(auth, 0o600)
    return path


def verify_permissions(codex_home: Path) -> list[str]:
    """Returns the list of permission violations. An empty list = compliant with L2199-2201."""
    violations: list[str] = []
    dir_mode = stat.S_IMODE(codex_home.stat().st_mode)
    if dir_mode != 0o700:
        violations.append(f"CODEX_HOME ma {dir_mode:04o}, wymagane 0700")
    for nazwa, required_mode in (("config.toml", 0o600), ("auth.json", 0o600)):
        file_path = codex_home / nazwa
        if not file_path.exists():
            continue
        mode = stat.S_IMODE(file_path.stat().st_mode)
        if mode != required_mode:
            violations.append(f"{nazwa} ma {mode:04o}, wymagane {required_mode:04o}")
    return violations


def effective_config_mismatches(
    config_read: dict[str, Any], *, web_search: str | None = None
) -> list[str]:
    """Compares the EFFECTIVE configuration from `config/read` against the hardening requirements.

    DoD D12: "the runtime's effective configuration has web_search=\"disabled\" and all five
    feature flags at false — verified by READING, not by assumption". This function is that
    read; the input is the raw `result` of the `config/read` method.

    `web_search` says which value THIS runtime was configured with. Left out, the comparison is
    against the hardened default, so the pilot's gate keeps the meaning it was measured with.
    A session that asked for search passes its own value here: otherwise the read would report
    a mismatch for a difference somebody chose on purpose, and a real mismatch would drown in it.
    """
    cfg = config_read.get("config", config_read)
    mismatches: list[str] = []

    expected_top = dict(REQUIRED_TOP_LEVEL)
    if web_search is not None:
        if web_search not in WEB_SEARCH_MODES:
            raise ValueError(
                f"web_search={web_search!r} nie jest wartością WebSearchMode; "
                f"dozwolone: {sorted(WEB_SEARCH_MODES)}"
            )
        expected_top["web_search"] = web_search
    for key, expected in expected_top.items():
        actual = cfg.get(key)
        if actual != expected:
            mismatches.append(f"{key}={actual!r}, oczekiwano {expected!r}")

    features = cfg.get("features") or {}
    for key in REQUIRED_FEATURES:
        # All the required flags are to be `False`; the value from `WYMAGANE_FEATURES` is
        # documentation here, and the condition is identity with `False`, because `None` and `0`
        # do not count.
        actual_flag = features.get(key)
        if actual_flag is not False:
            # `None` means "not set", and these flags have default_enabled=true,
            # so a missing entry is NOT the same thing as disabled.
            mismatches.append(
                f"features.{key}={actual_flag!r}, oczekiwano False "
                f"({'brak wpisu = domyślnie WŁĄCZONE' if actual_flag is None else 'włączone'})"
            )

    store = cfg.get("cli_auth_credentials_store")
    if store in (None, "auto"):
        mismatches.append(
            f"cli_auth_credentials_store={store!r} — L2205 zabrania `auto` i wspólnego keyringu"
        )
    return mismatches


def extra_mcp_servers(mcp_server_status_list: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    """MCP servers active in the runtime that are not on the allowlist (DoD L5179).

    We check the list of ACTIVE servers from `mcpServerStatus/list`, and not the declaration
    in `config.toml` — because the former is state and the latter is intent.
    """
    entries = mcp_server_status_list.get("data") or []
    names = [
        outcome.get("name") or outcome.get("serverName") or outcome.get("id") for outcome in entries
    ]
    return sorted(n for n in names if n and n not in allowed)
