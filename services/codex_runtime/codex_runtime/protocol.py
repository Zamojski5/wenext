"""Narrow JSON-RPC client for `codex app-server` with an explicit method allowlist (L2222, L2237).

Three things this module enforces and which must not be loosened:

1. **Method allowlist.** The client sends only methods from `ALLOWED_METHODS`. Every other one
   ends with `MetodaNiedozwolona` ON THE CLIENT SIDE, before the frame reaches the wire.
2. **No `jsonrpc` field on the wire.** The app-server protocol is JSON-RPC 2.0, but the
   `jsonrpc` header is omitted. A client strictly validating its presence would reject every frame.
3. **`experimentalApi=false` in the handshake.** This is a capability negotiated in `initialize`,
   and NOT a `config.toml` key — see the errata in NOTES.md. The Python SDK defaults it to `True`.

Why the allowlist is the only boundary on the protocol side — the result of a run, not theory:
`command/exec` with the parameter `{"command": ["/bin/echo", "hi"]}` RETURNED `{"exitCode": 0,
"stdout": "hi\\n"}` with `features.shell_tool=false`, `features.unified_exec=false`
and `experimentalApi=false`. The `[features]` flags govern which tools the MODEL gets,
and not what the app-server CLIENT is allowed to call. The sentence from L2222 "cannot call
`thread/shellCommand`, `command/exec`" is achievable only here and in the OS sandbox.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).parent / "contract"

#: Explicit method allowlist from the D12 description. The adapter sends NOTHING outside it.
ALLOWED_METHODS: frozenset[str] = frozenset(
    {
        "initialize",
        "account/read",
        "account/login/start",
        "account/login/cancel",
        "account/logout",
        "account/rateLimits/read",
        "model/list",
        "thread/start",
        "thread/resume",
        "turn/start",
        "turn/interrupt",
        # Dopowiedzenie W TRAKCIE tury (D-046). Metoda jest stabilna w przypiętym 0.147.0
        # (`client_requests_stabilne`), a jej parametr `expectedTurnId` jest warunkiem wstępnym:
        # skierowanie zdania do tury, która już się skończyła, kończy się błędem, a nie cichym
        # zniknięciem wiadomości. To jedyny powód, dla którego wolno jej tu być: bez tego
        # warunku dopowiedzenie mogłoby trafić w pustkę i nikt by się nie dowiedział.
        "turn/steer",
        # Reading the effective configuration. Needed because hardening is to be VERIFIED
        # by reading it, not assumed (DoD D12). This is a purely read-only method.
        "config/read",
        # The list of actually active MCP servers — evidence for the `tylko_dozwolony_mcp` check
        # (DoD L5179: "Codex uses only the allowed MCP"), also read-only.
        "mcpServerStatus/list",
    }
)

#: The only notification the client sends. The handshake is two-stage and mandatory.
ALLOWED_NOTIFICATIONS: frozenset[str] = frozenset({"initialized"})

#: Methods listed in L2222 as forbidden. We keep them separately so that a test can show
#: they are known to the binary (that is, real) and yet unavailable to the adapter.
EXPLICITLY_FORBIDDEN_METHODS: frozenset[str] = frozenset(
    {
        "thread/shellCommand",
        "command/exec",
        "command/exec/write",
        "command/exec/resize",
        "command/exec/terminate",
        "process/spawn",
        "process/kill",
        "process/writeStdin",
        "process/resizePty",
        "config/value/write",
        "config/batchWrite",
        "config/mcpServer/reload",
        "externalAgentConfig/import",
        "fs/writeFile",
        "fs/remove",
        "fs/copy",
        "fs/createDirectory",
        "plugin/install",
        "marketplace/add",
    }
)


class ProtocolError(Exception):
    """Anything that went wrong at the transport or frame level."""


class MethodNotAllowed(ProtocolError):
    """An attempt to call a method outside the allowlist — stopped before sending (L2237)."""


class RemoteError(ProtocolError):
    """The server replied with `error`. Carries the code and message without interpreting them."""

    def __init__(self, method: str, kod: int, message: str) -> None:
        super().__init__(f"{method} → [{kod}] {message}")
        self.method = method
        self.kod = kod
        self.message = message


def load_contract() -> dict[str, Any]:
    """The protocol contract generated from the pinned binary (`generate-json-schema`)."""
    return json.loads((CONTRACT_DIR / "app-server-methods.json").read_text(encoding="utf-8"))


def methods_outside_contract(methods: frozenset[str]) -> list[str]:
    """Which of the given methods do not exist in the pinned protocol version.

    The online documentation describes `main`, not our version — which is why we validate the
    allowlist against the artifact generated from the binary, not against a web page.
    """
    contract = load_contract()
    known_methods = set(contract["client_requests_stabilne"]) | set(
        contract["client_requests_tylko_experimental"]
    )
    return sorted(m for m in methods if m not in known_methods)


@dataclass
class IncomingFrame:
    """A single frame received from the app-server."""

    raw: dict[str, Any]

    @property
    def is_response(self) -> bool:
        return "id" in self.raw and ("result" in self.raw or "error" in self.raw)

    @property
    def method(self) -> str | None:
        return self.raw.get("method")


@dataclass
class AppServerClient:
    """A stdio client for `codex app-server`, with a hard method allowlist.

    We do not use `openai_codex.client.CodexClient` for the control layer, because its
    `model_list()` does not accept a cursor (checked in the installed package 0.147.0),
    and `account/rateLimits/read` is not in the public SDK surface at all. Turn execution
    goes through the SDK — see `execution_adapter`.
    """

    codex_bin: Path
    codex_home: Path
    client_name: str = "wenext-codex-runtime"
    client_version: str = "0.1.0"
    #: `experimentalApi` in the handshake. Always False in production (L2237).
    experimental_api: bool = False
    timeout_s: float = 30.0
    #: `--config klucz=wartość` podawane binarce PRZED podkomendą, tak jak robi to SDK
    #: (`openai_codex/client.py`). Domyślnie puste, więc dotychczasowe wywołania biegną bez
    #: zmian. Istnieje dla sondy E9: mierzymy, czy wpis serwera narzędzi da się podać przy
    #: starcie procesu zamiast pisać go do wspólnego `config.toml`.
    config_overrides: tuple[str, ...] = ()

    _proc: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _id: int = field(default=0, init=False, repr=False)
    _stderr: deque[str] = field(default_factory=lambda: deque(maxlen=200), init=False, repr=False)
    _notifications: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    initialized: bool = field(default=False, init=False)
    initialize_response: dict[str, Any] | None = field(default=None, init=False)

    # -- process lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            return
        srodowisko = os.environ.copy()
        srodowisko["CODEX_HOME"] = str(self.codex_home)
        # An API key would change the auth mode under us. The runtime is to be on the ChatGPT
        # login (L2119).
        srodowisko.pop("OPENAI_API_KEY", None)
        arguments = [str(self.codex_bin)]
        for override in self.config_overrides:
            arguments += ["--config", override]
        arguments += ["app-server", "--listen", "stdio://"]
        self._proc = subprocess.Popen(  # the binary is pinned and known
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=srodowisko,
        )
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    @property
    def stderr(self) -> list[str]:
        return list(self._stderr)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def close(self) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        self.initialized = False

    def __enter__(self) -> AppServerClient:
        self.start()
        self.initialize()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- frame layer ----------------------------------------------------------

    def _send(self, frame: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ProtocolError("app-server nie jest uruchomiony")
        # Deliberately WITHOUT the "jsonrpc" field — the protocol omits it on the wire.
        self._proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _receive(self, expected_id: int) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise ProtocolError("app-server nie jest uruchomiony")
        koniec = time.monotonic() + self.timeout_s
        while time.monotonic() < koniec:
            line = self._proc.stdout.readline()
            if not line:
                raise ProtocolError(
                    f"app-server zamknął stdout; stderr: {' | '.join(self.stderr[-5:])}"
                )
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProtocolError(f"nieparsowalna ramka: {line[:200]!r}") from exc
            if frame.get("id") == expected_id:
                return frame
            if "method" in frame:
                self._notifications.append(frame)
        raise ProtocolError(f"brak odpowiedzi na żądanie {expected_id} w {self.timeout_s} s")

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications)

    # -- method layer ---------------------------------------------------------

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Calls a method from the allowlist. Anything outside ends with `MetodaNiedozwolona`."""
        if method not in ALLOWED_METHODS:
            raise MethodNotAllowed(
                f"Metoda {method!r} jest poza allowlistem klienta JSON-RPC (L2222, L2237). "
                f"Dozwolone: {', '.join(sorted(ALLOWED_METHODS))}"
            )
        if method != "initialize" and not self.initialized:
            raise ProtocolError(
                "handshake nie został zakończony — żadna metoda nie przejdzie przed "
                "`initialize` + notyfikacją `initialized`"
            )
        return self._request_bypassing_allowlist(method, params)

    def _request_bypassing_allowlist(
        self, method: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        """A back door ONLY for gate tests that have to show the server's behaviour.

        The name is ugly on purpose: production code calls `request`, not this.
        """
        self._id += 1
        current_id = self._id
        self._send({"id": current_id, "method": method, "params": params or {}})
        frame = self._receive(current_id)
        if "error" in frame:
            error = frame["error"]
            raise RemoteError(method, error.get("code", 0), error.get("message", ""))
        result = frame.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if method not in ALLOWED_NOTIFICATIONS:
            raise MethodNotAllowed(f"Notyfikacja {method!r} jest poza allowlistem")
        self._send({"method": method, "params": params})

    def initialize(self) -> dict[str, Any]:
        """The mandatory, two-stage handshake: an `initialize` request, then `initialized`."""
        response = self._request_bypassing_allowlist(
            "initialize",
            {
                "clientInfo": {
                    "name": self.client_name,
                    "title": "wenext Codex runtime",
                    "version": self.client_version,
                },
                "capabilities": {"experimentalApi": self.experimental_api},
            },
        )
        self.initialized = True
        self.notify("initialized", None)
        self.initialize_response = response
        return response


def run_with_allowlist(
    codex_bin: Path, codex_home: Path, work: Callable[[AppServerClient], Any]
) -> Any:
    """Starts the app-server, does the handshake, runs `praca`, always closes the process."""
    client = AppServerClient(codex_bin=codex_bin, codex_home=codex_home)
    with client:
        return work(client)
