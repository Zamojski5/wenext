"""`observe/turns.py` — the SDK call shape is pinned against a fake, since no live model runs here.

`CodexTurnRunner` has never met a live model as of 2026-09-03 (its module docstring says so).
What CAN be proven without one: which SDK methods it calls with which arguments, how it maps the
SDK's failures onto `ModelUnavailable` versus `TurnFailed`, that it refuses to run without a
login file, and that it always closes the client. The fake SDK below has the same method
signatures as `openai_codex` 0.147.0 (`AsyncCodex.thread_start`, `AsyncThread.run`,
`TurnResult.final_response`), read from the installed package, not remembered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from community_os_contracts import ThreadRole
from control_plane.observe.turns import (
    DEFAULT_ROUTES,
    CodexTurnRunner,
    FakeTurnRunner,
    ModelUnavailable,
    TurnFailed,
    parse_structured_output,
)

SCHEMA = {"type": "object", "properties": {"decision": {"type": "string"}}}


@dataclass
class _Result:
    final_response: str | None
    status: str = "completed"
    error: Any = None


@dataclass
class _FakeThread:
    run_calls: list[dict[str, Any]]
    result: _Result | Exception

    async def run(self, input, *, model=None, effort=None, output_schema=None, **kwargs):
        self.run_calls.append(
            {
                "input": input,
                "model": model,
                "effort": effort,
                "output_schema": output_schema,
                **kwargs,
            }
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class _FakeCodex:
    config: Any
    thread_start_calls: list[dict[str, Any]] = field(default_factory=list)
    run_calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    result: _Result | Exception = field(
        default_factory=lambda: _Result(final_response='{"decision": "IGNORE"}')
    )
    start_error: Exception | None = None

    async def thread_start(self, **kwargs):
        self.thread_start_calls.append(kwargs)
        if self.start_error is not None:
            raise self.start_error
        return _FakeThread(self.run_calls, self.result)

    async def close(self):
        self.closed = True


@dataclass
class _FakeConfig:
    kwargs: dict[str, Any]


class _Sdk:
    """Builds the fake pair the runner asks `sdk_factory` for, and remembers the instance."""

    def __init__(
        self, result: _Result | Exception | None = None, start_error: Exception | None = None
    ):
        self.instances: list[_FakeCodex] = []
        self.result = result
        self.start_error = start_error

    def factory(self):
        def codex(config):
            instance = _FakeCodex(config=config, start_error=self.start_error)
            if self.result is not None:
                instance.result = self.result
            self.instances.append(instance)
            return instance

        def config(**kwargs):
            return _FakeConfig(kwargs)

        return codex, config


def runner(tmp_path: Path, sdk: _Sdk, *, logged_in: bool = True) -> CodexTurnRunner:
    if logged_in:
        (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    return CodexTurnRunner(
        codex_home=tmp_path, codex_bin=Path("/opt/codex"), sdk_factory=sdk.factory
    )


async def test_a_turn_calls_the_sdk_the_way_the_installed_sdk_is_shaped(tmp_path: Path) -> None:
    sdk = _Sdk()
    outcome = await runner(tmp_path, sdk).run(
        role=ThreadRole.WORKER, instructions="INSTR", user_input="{}", output_schema=SCHEMA
    )
    assert outcome.structured_output == {"decision": "IGNORE"}
    assert (outcome.model, outcome.effort) == DEFAULT_ROUTES[ThreadRole.WORKER]

    (instance,) = sdk.instances
    assert instance.config.kwargs["experimental_api"] is False, "errata 1 z codex_runtime/NOTES.md"
    assert instance.config.kwargs["env"] == {"CODEX_HOME": str(tmp_path), "OPENAI_API_KEY": ""}
    assert instance.config.kwargs["codex_bin"] == "/opt/codex"
    model, effort = DEFAULT_ROUTES[ThreadRole.WORKER]
    (start,) = instance.thread_start_calls
    assert start == {"model": model, "ephemeral": True, "base_instructions": "INSTR"}
    (run,) = instance.run_calls
    assert run["input"] == "{}"
    assert run["model"] == model and run["effort"] == effort
    assert run["output_schema"] is SCHEMA
    assert instance.closed, "klient SDK ma być zamknięty także po udanej turze"


async def test_planner_role_uses_the_planner_route(tmp_path: Path) -> None:
    sdk = _Sdk()
    outcome = await runner(tmp_path, sdk).run(
        role=ThreadRole.PLANNER, instructions="I", user_input="{}", output_schema=SCHEMA
    )
    assert (outcome.model, outcome.effort) == DEFAULT_ROUTES[ThreadRole.PLANNER]
    assert DEFAULT_ROUTES[ThreadRole.PLANNER] != DEFAULT_ROUTES[ThreadRole.WORKER]


async def test_without_a_login_file_the_sdk_is_never_touched(tmp_path: Path) -> None:
    sdk = _Sdk()
    with pytest.raises(ModelUnavailable, match=r"auth\.json"):
        await runner(tmp_path, sdk, logged_in=False).run(
            role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema=SCHEMA
        )
    assert sdk.instances == []


@pytest.mark.parametrize(
    "message", ["401 Unauthorized", "not logged in", "usage limit reached", "rate limit"]
)
async def test_auth_and_quota_failures_are_model_unavailable(tmp_path: Path, message: str) -> None:
    sdk = _Sdk(result=RuntimeError(message))
    with pytest.raises(ModelUnavailable):
        await runner(tmp_path, sdk).run(
            role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema=SCHEMA
        )
    assert sdk.instances[0].closed


async def test_other_sdk_failures_are_turn_failed_and_the_client_is_closed(tmp_path: Path) -> None:
    sdk = _Sdk(start_error=RuntimeError("model exploded"))
    with pytest.raises(TurnFailed, match="exploded"):
        await runner(tmp_path, sdk).run(
            role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema=SCHEMA
        )
    assert sdk.instances[0].closed


async def test_a_non_json_or_non_object_answer_is_turn_failed(tmp_path: Path) -> None:
    for bad in ("not json", "[1, 2]", "", None):
        sdk = _Sdk(result=_Result(final_response=bad))
        with pytest.raises(TurnFailed):
            await runner(tmp_path, sdk).run(
                role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema=SCHEMA
            )


async def test_a_turn_that_did_not_complete_is_turn_failed(tmp_path: Path) -> None:
    sdk = _Sdk(
        result=_Result(final_response='{"decision": "IGNORE"}', status="interrupted", error="cut")
    )
    with pytest.raises(TurnFailed, match="interrupted"):
        await runner(tmp_path, sdk).run(
            role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema=SCHEMA
        )


async def test_an_empty_schema_is_refused_before_anything_runs(tmp_path: Path) -> None:
    sdk = _Sdk()
    with pytest.raises(TurnFailed, match="output_schema"):
        await runner(tmp_path, sdk).run(
            role=ThreadRole.WORKER, instructions="I", user_input="{}", output_schema={}
        )
    assert sdk.instances == []


def test_parse_structured_output_distinguishes_three_failures() -> None:
    assert parse_structured_output(json.dumps({"a": 1}), what="x") == {"a": 1}
    with pytest.raises(TurnFailed, match="żadnej treści"):
        parse_structured_output("   ", what="x")
    with pytest.raises(TurnFailed, match="nie jest JSON"):
        parse_structured_output("{oops", what="x")
    with pytest.raises(TurnFailed, match="obiektu"):
        parse_structured_output("[]", what="x")


async def test_the_fake_runner_records_calls_and_refuses_to_answer_past_its_script() -> None:
    fake = FakeTurnRunner(outputs=[{"decision": "IGNORE"}])
    outcome = await fake.run(
        role=ThreadRole.WORKER, instructions="I", user_input="U", output_schema=SCHEMA
    )
    assert outcome.structured_output == {"decision": "IGNORE"}
    assert fake.calls[0].user_input == "U"
    with pytest.raises(AssertionError, match="zaplanowano 1"):
        await fake.run(
            role=ThreadRole.WORKER, instructions="I", user_input="U", output_schema=SCHEMA
        )


def test_default_routes_equal_the_runtime_route_table() -> None:
    """The copy in `turns.py` must not drift from `codex_runtime.routing.ROUTES`."""
    from codex_runtime.routing import ROUTES  # installed with the SDK pin in this venv

    for role, (model, effort) in DEFAULT_ROUTES.items():
        assert (ROUTES[role].model, ROUTES[role].effort) == (model, effort), role


async def test_the_judge_turn_runs_with_no_mcp_servers_at_all(tmp_path: Path) -> None:
    """ZMIERZONE NA LIVE 21.09, po restarcie kontenera: `judge.thread` padał na
    „required MCP servers failed to initialize: wenext".

    Dom Codexa jest WSPÓLNY dla pilota i rozmów. Rozmowa zostawia w jego `config.toml` wpis
    `[mcp_servers.wenext]` z `required = true` i ze ścieżką do pliku zdolności w `/tmp`; po
    restarcie pliku nie ma, serwer kończy proces przed odpowiedzią na `initialize`, a app-server
    odmawia CAŁEJ tury. Osąd wątku padał więc przez ustawienie, które należało do cudzej rozmowy.

    Gdyby plik ISTNIAŁ i był ważny, byłoby gorzej niż awaria: tura osądu dostałaby narzędzia z
    zakresem cudzej sesji. Dlatego nadpisanie jest tu twardym warunkiem, a nie optymalizacją, i
    dlatego nie piszemy pliku w domu: pisanie do wspólnego katalogu ścigałoby się z rozmowami.
    """
    sdk = _Sdk()

    await runner(tmp_path, sdk).run(
        role=ThreadRole.WORKER, instructions="INSTR", user_input="{}", output_schema=SCHEMA
    )

    (instance,) = sdk.instances
    overrides = instance.config.kwargs["config_overrides"]
    assert "mcp_servers={}" in overrides, overrides
