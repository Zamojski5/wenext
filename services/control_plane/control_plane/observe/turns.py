"""One model turn with structured output — the only way the observe module reaches a model.

Why a port of one method and not the `CodexExecutionAdapter` of `services/codex_runtime/`:
that adapter starts a browser MCP sidecar before every turn and fails closed without one
(L2503), because it was built for turns that READ THE WEB through the capsule. The turns here
read nothing — the control plane compiles everything the model sees into `user_input`
(`context.py`), so a sidecar would be a tool the model is not allowed to have. The port is
therefore narrower on purpose, and the founders' decision "no API key, Codex on the ChatGPT
account" (`STAN-PROJEKTU.md`, GATE-3) is kept: `CodexTurnRunner` goes through the same SDK,
the same `CODEX_HOME` volume and the same hardened defaults as the runtime.

VERIFIED AGAINST A LIVE MODEL since 2026-09-04 (`docs/OUTCOMES.md` §8, entries of 2026-09-04):
after the founder's Codex device login (`auth.json` in the `codex-home` volume, 2026-09-03 22:12)
this port produced the first community picture (`gpt-5.6-sol`, `community_picture` v1) and,
from ~05:55 UTC on 2026-09-04, hundreds of thread judgments (420 rows by 2026-09-06) and five
voice cards. The SDK surface below was read from the installed `openai-codex==0.147.0` —
`AsyncCodex.thread_start(...)`, `AsyncThread.run(input, *, model, effort, output_schema)`,
`TurnResult.final_response` — not remembered, and `tests/test_observe_turns.py` pins the exact
call shape against a fake SDK. A usage limit on the ChatGPT account surfaces as
`ModelUnavailable` and parks the job (`WAITING_AUTH`); it is not a failed turn.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from community_os_contracts import ThreadRole

_LOG = logging.getLogger(__name__)

#: Model/effort per role for the observe turns. `WORKER` judges threads (many turns a day);
#: `PLANNER` draws the daily picture (one turn a day, much more context). These are the routes
#: of `services/codex_runtime/codex_runtime/routing.py`, copied as VALUES and pinned equal to
#: them by `tests/test_observe_turns.py` rather than imported: importing would make the control
#: plane's route table depend on a package whose import pulls the Codex CLI wheel into every
#: test process.
#:
#: `docs/OUTCOMES.md` §6 point 5: this table is a HYPOTHESIS to be measured by the `judgment`
#: eval family, not a decision. In particular nobody has measured whether the judge needs
#: `terra/medium` or whether `luna/low` (the runtime's POST_VERIFIER route) judges as well for a
#: fraction of the quota. Changing it is a one-line, visible edit — here and in `routing.py`.
DEFAULT_ROUTES: Mapping[ThreadRole, tuple[str, str]] = {
    ThreadRole.WORKER: ("gpt-5.6-terra", "medium"),
    ThreadRole.PLANNER: ("gpt-5.6-sol", "high"),
}


@dataclass(frozen=True)
class TurnOutcome:
    """What one turn produced, with the identity every row derived from it must carry."""

    structured_output: dict[str, Any]
    model: str
    effort: str


class ModelUnavailable(Exception):
    """No usable model right now: not configured, not logged in, or out of quota.

    NOT a failed turn. The handler turns it into a parked job (`WAITING_AUTH`) so the operator
    sees "log in" on the queue screen instead of a terminal failure per thread.
    """


class TurnFailed(Exception):
    """The model answered, and the answer is unusable: not JSON, not the schema, or an error."""


class ModelTurnPort(Protocol):
    async def run(
        self, *, role: ThreadRole, instructions: str, user_input: str, output_schema: dict
    ) -> TurnOutcome:
        """One turn in a FRESH thread. Structured output, or an exception — never a partial."""
        ...


def parse_structured_output(final_response: str | None, *, what: str) -> dict[str, Any]:
    """The model's final message as a JSON object. Anything else is `TurnFailed`, with a sample.

    A `null` response, a string that is not JSON and a JSON array are three different failures
    and the sample in the message is what tells them apart in a log.
    """
    if not final_response or not final_response.strip():
        raise TurnFailed(f"{what}: model nie zwrócił żadnej treści")
    try:
        parsed = json.loads(final_response)
    except json.JSONDecodeError as exc:
        raise TurnFailed(
            f"{what}: odpowiedź modelu nie jest JSON-em: {final_response[:200]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise TurnFailed(f"{what}: oczekiwano obiektu JSON, jest {type(parsed).__name__}")
    return parsed


class CodexTurnRunner:
    """`ModelTurnPort` over the Codex Python SDK, on the founder's logged-in `CODEX_HOME`.

    `sdk_factory` exists for the test: it returns the `(AsyncCodex, CodexConfig)` pair, and the
    test hands in fakes with the same signatures so the exact call shape is pinned without a
    binary or an account.
    """

    #: Tura osądu NIE UŻYWA narzędzi produktu i nie wolno jej od nich uzależniać. Nadpisanie
    #: wyłącza serwery MCP na czas TEGO procesu, nie ruszając pliku w domu, i rozwiązuje dwie
    #: rzeczy naraz (zmierzone na live 21.09 po restarcie kontenera):
    #:
    #:   * DOM JEST WSPÓLNY. Rozmowa zostawia w jego `config.toml` wpis `[mcp_servers.wenext]`
    #:     z `required = true` i ze ścieżką do pliku zdolności. Po restarcie pliku nie ma (żył
    #:     w `/tmp`), serwer kończy proces przed odpowiedzią na `initialize`, a app-server
    #:     odmawia CAŁEJ tury: „required MCP servers failed to initialize". Osąd wątku padał
    #:     więc przez ustawienie, które należało do cudzej rozmowy;
    #:   * GRANICA. Gdyby plik ISTNIAŁ i był ważny, byłoby gorzej niż awaria: tura osądu
    #:     dostałaby narzędzia z zakresem CUDZEJ sesji. Wpis MCP jest własnością tury rozmowy i
    #:     żadna inna tura nie ma prawa go dziedziczyć.
    #:
    #: Nadpisanie zamiast pisania pliku, bo pisanie do wspólnego domu ścigałoby się z rozmowami,
    #: które piszą tam swoje wpisy przed każdą turą.
    CONFIG_OVERRIDES: tuple[str, ...] = ("mcp_servers={}",)

    #: Words in an SDK error that mean "a human has to log in or wait", not "this turn is bad".
    UNAVAILABLE_MARKERS: tuple[str, ...] = (
        "auth",
        "login",
        "logged",
        "unauthorized",
        "401",
        "quota",
        "rate limit",
        "rate_limit",
        "429",
        "usage limit",
    )

    def __init__(
        self,
        *,
        codex_home: Path,
        codex_bin: Path | None = None,
        routes: Mapping[ThreadRole, tuple[str, str]] = DEFAULT_ROUTES,
        sdk_factory: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self._codex_home = codex_home
        self._codex_bin = codex_bin
        self._routes = dict(routes)
        self._sdk_factory = sdk_factory or _import_sdk

    def route(self, role: ThreadRole) -> tuple[str, str]:
        try:
            return self._routes[role]
        except KeyError as exc:
            raise TurnFailed(
                f"brak trasy model/effort dla roli {role.value}; znane: "
                f"{sorted(r.value for r in self._routes)}"
            ) from exc

    async def run(
        self, *, role: ThreadRole, instructions: str, user_input: str, output_schema: dict
    ) -> TurnOutcome:
        if not output_schema:
            raise TurnFailed("output_schema nie może być pusty — każda tura ma structured output")
        if not (self._codex_home / "auth.json").exists():
            raise ModelUnavailable(
                f"brak {self._codex_home / 'auth.json'} — Codex nie jest zalogowany w tym "
                "CODEX_HOME. Logowanie to krok człowieka (runbook GATE-3, `make codex-gonogo`)"
            )
        model, effort = self.route(role)
        async_codex, codex_config = self._sdk_factory()
        env = {"CODEX_HOME": str(self._codex_home), "OPENAI_API_KEY": ""}
        config = codex_config(
            codex_bin=str(self._codex_bin) if self._codex_bin else None,
            env=env,
            config_overrides=self.CONFIG_OVERRIDES,
            client_name="wenext-observe",
            client_title="wenext observe",
            # SDK 0.147.0 defaults this to True — `services/codex_runtime/NOTES.md`, errata 1.
            experimental_api=False,
        )
        codex = async_codex(config)
        try:
            try:
                thread = await codex.thread_start(
                    model=model, ephemeral=True, base_instructions=instructions
                )
                result = await thread.run(
                    user_input, model=model, effort=effort, output_schema=output_schema
                )
            except Exception as exc:  # the SDK's own hierarchy is mapped by message, see below
                text = str(exc).lower()
                if any(marker in text for marker in self.UNAVAILABLE_MARKERS):
                    raise ModelUnavailable(f"Codex: {type(exc).__name__}: {exc}") from exc
                raise TurnFailed(f"Codex: {type(exc).__name__}: {exc}") from exc
        finally:
            close = getattr(codex, "close", None)
            if close is not None:
                await close()
        status = getattr(result, "status", None)
        status_value = getattr(status, "value", status)
        if status_value not in (None, "completed"):
            raise TurnFailed(
                f"tura zakończyła się stanem {status_value!r}: {getattr(result, 'error', None)!r}"
            )
        parsed = parse_structured_output(getattr(result, "final_response", None), what=role.value)
        return TurnOutcome(structured_output=parsed, model=model, effort=effort)


def _import_sdk() -> tuple[Any, Any]:
    try:
        from openai_codex import AsyncCodex, CodexConfig
    except ImportError as exc:
        raise ModelUnavailable(
            "pakiet `openai-codex` nie jest zainstalowany w tym procesie — obraz control plane "
            "musi go zawierać, żeby obraz community i osądy w ogóle powstawały"
        ) from exc
    return AsyncCodex, CodexConfig


@dataclass
class RecordedTurn:
    role: ThreadRole
    instructions: str
    user_input: str
    output_schema: dict


@dataclass
class FakeTurnRunner:
    """A scripted `ModelTurnPort` for tests: returns the next output, records every call.

    Running out of scripted outputs is an ERROR, not an empty dict — a fake that keeps answering
    would let a handler loop look like progress.
    """

    outputs: Sequence[dict[str, Any] | Exception]
    model: str = "fake-model"
    effort: str = "none"
    calls: list[RecordedTurn] = field(default_factory=list)
    on_call: Callable[[RecordedTurn], Awaitable[None]] | None = None

    async def run(
        self, *, role: ThreadRole, instructions: str, user_input: str, output_schema: dict
    ) -> TurnOutcome:
        recorded = RecordedTurn(role, instructions, user_input, output_schema)
        self.calls.append(recorded)
        if self.on_call is not None:
            await self.on_call(recorded)
        index = len(self.calls) - 1
        if index >= len(self.outputs):
            raise AssertionError(
                f"FakeTurnRunner: {len(self.calls)}. wywołanie, a zaplanowano {len(self.outputs)}"
            )
        scripted = self.outputs[index]
        if isinstance(scripted, Exception):
            raise scripted
        return TurnOutcome(structured_output=dict(scripted), model=self.model, effort=self.effort)
