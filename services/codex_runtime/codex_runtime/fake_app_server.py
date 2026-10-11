"""A stand-in for `codex app-server`, for testing sessions without a ChatGPT account (P2).

    CODEX_HOME=/tmp/home WENEXT_FAKE_APP_SERVER_SCRIPT=/tmp/script.json \\
        python -m codex_runtime.fake_app_server

WHAT IT IS AND, MORE IMPORTANTLY, WHAT IT IS NOT. It speaks the app-server protocol well enough
to run a session end to end: the handshake, `thread/start`, `thread/resume`, `turn/start`,
`turn/interrupt`, and the server notifications a session records. It proves the MECHANICS around
the model. It proves NOTHING about the model: no judgment, no quality, no wording. Every test
that uses it has to say so, and the report has to say so.

THE ONE THING IT DOES NOT FAKE IS THE TOOLS. It reads `config.toml` from `CODEX_HOME`, starts
every `[mcp_servers.*]` the same way Codex would, performs the MCP handshake and makes REAL
`tools/call` requests with the arguments from its script. So an end-to-end test exercises the
session, the job, the client, the tool server, its capability file and the database, and the
only thing standing in for reality is the choice of which tool to call. A fake that merely
emitted tool-call notifications would leave the entire tool server untested while looking
identical in the trace.

THE SCRIPT decides what the "model" does. A turn is a list of steps, each one of:

    {"tool": "catalog.list", "arguments": {}}   call a tool, stream its call and its result
    {"message": "tekst"}                        stream an assistant message
    {"delay_ms": 500}                           wait, so a test can restart or interrupt mid-turn
    {"fail": "powód"}                           end the turn as failed

Turns are consumed in order; when they run out, `default_turn` is used, and without one the
turn ends immediately with an empty message. That "ran out" behaviour is deliberate: a fake that
keeps inventing answers makes a runaway loop look like progress (`FakeTurnRunner` in
`observe/turns.py` refuses for the same reason, and this one records it as a failed turn rather
than silently continuing).

THE NOTIFICATION SHAPES come from `contract/app-server-notifications.json`, generated from the
pinned `openai-codex` package. They are not invented here, and a version bump that changes them
fails `tests/test_notification_contract.py` rather than quietly teaching the session to parse
something the real server never sends.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

ENV_SCRIPT = "WENEXT_FAKE_APP_SERVER_SCRIPT"
ENV_HOME = "CODEX_HOME"

MCP_PROTOCOL_VERSION = "2025-06-18"

#: How long one `tools/call` may take before the fake gives up on it. Generous: the real tool
#: server opens a connection pool on first use.
TOOL_TIMEOUT_S = 60.0


class FakeServerError(Exception):
    """The fake cannot do its job, and saying so loudly beats pretending to be a model."""


@dataclass
class _McpClient:
    """One MCP server, started from `config.toml` and spoken to over stdio."""

    name: str
    process: subprocess.Popen[str]
    _next_id: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            frame = {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            }
            self._write(frame)
            deadline = time.monotonic() + TOOL_TIMEOUT_S
            while time.monotonic() < deadline:
                line = self.process.stdout.readline() if self.process.stdout else ""
                if not line:
                    raise FakeServerError(
                        f"serwer MCP {self.name} zamknął wyjście; kod {self.process.poll()}"
                    )
                try:
                    answer = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if answer.get("id") == frame["id"]:
                    if "error" in answer:
                        raise FakeServerError(
                            f"{self.name}.{method}: {answer['error'].get('message')}"
                        )
                    return answer.get("result") or {}
            raise FakeServerError(f"serwer MCP {self.name} nie odpowiedział na {method}")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _write(self, frame: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise FakeServerError(f"serwer MCP {self.name} nie ma wejścia")
        self.process.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class _Answer:
    """Jedna odpowiedź klienta, na którą czeka wątek tury.

    NIE `queue.Queue`, choć pasowałaby: `test_no_bypass_static.py` skanuje repozytorium w
    poszukiwaniu `.put(` jako zapisu poza `execution/adapter.py`, a atrapa app-servera nie jest
    wyjątkiem od tej reguły i nie powinna nim być. Rozszerzanie słownika skanera, żeby przepuścił
    ten jeden przypadek, osłabiłoby kontrolę, która istnieje po to, żeby żaden zapis nie
    przeszedł niezauważony. Dwie metody o nazwach mówiących, co robią, kosztują mniej.
    """

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._value: Any = None

    def deliver(self, value: Any) -> None:
        self._value = value
        self._ready.set()

    def await_value(self, timeout_s: float) -> Any:
        if not self._ready.wait(timeout=timeout_s):
            raise TimeoutError
        return self._value


def effective_config(codex_home: Path, overrides: tuple[str, ...] = ()) -> dict[str, Any]:
    """`config.toml` domu z nałożonymi `--config`, tak jak składa to prawdziwa binarka.

    POWSTAŁO DLA E9 (21.09). Od tej zmiany tura NIE PISZE wpisu serwera narzędzi do wspólnego
    `config.toml`, tylko podaje go procesowi w `--config mcp_servers.wenext={...}`. Atrapa, która
    czytałaby dalej sam plik, nie zobaczyłaby żadnego serwera i testy tur wyglądałyby na zielone
    przy modelu bez narzędzi. Atrapa ma odwzorowywać binarkę, a binarka te argumenty przyjmuje:
    zmierzone na 0.147.0, `config_probe --mcp-overrides` (wartość jest parsowana jako TOML,
    dotted path nakłada się na konfigurację z pliku).
    """
    config: dict[str, Any] = {}
    config_path = codex_home / "config.toml"
    if config_path.exists():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    for override in overrides:
        try:
            parsed = tomllib.loads(override)
        except tomllib.TOMLDecodeError as exc:
            raise FakeServerError(f"--config {override!r} nie jest TOML-em: {exc}") from exc
        for key, value in parsed.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    return config


def start_mcp_servers(codex_home: Path, overrides: tuple[str, ...] = ()) -> dict[str, _McpClient]:
    """Start every `[mcp_servers.*]` from the effective configuration, exactly as Codex would.

    A server that fails to start is an ERROR, not a warning: `required = true` in the generated
    configuration means the real runtime would end the turn, and a fake that carried on would
    make a broken tool server look like a model that chose not to use it.
    """
    config = effective_config(codex_home, overrides)
    clients: dict[str, _McpClient] = {}
    for name, entry in (config.get("mcp_servers") or {}).items():
        if not entry.get("enabled", True):
            continue
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (entry.get("env") or {}).items()})
        command = [str(entry["command"]), *[str(a) for a in entry.get("args", [])]]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        client = _McpClient(name=name, process=process)
        try:
            client.request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "fake-app-server", "version": "0.1.0"},
                },
            )
            client.notify("notifications/initialized")
        except FakeServerError as exc:
            stderr = ""
            if process.stderr is not None and process.poll() is not None:
                stderr = process.stderr.read()[:500]
            client.close()
            raise FakeServerError(
                f"serwer MCP {name} nie wykonał handshake'u: {exc}. stderr: {stderr}"
            ) from exc
        clients[name] = client
    return clients


@dataclass
class FakeAppServer:
    """The protocol loop. One thread reads requests; each turn runs on its own thread."""

    script: dict[str, Any]
    mcp: dict[str, _McpClient]
    #: Where the turn counter lives between processes. See `_next_turn_script`.
    state_path: Path = field(default_factory=lambda: Path("fake-app-server.state"))
    stream_in: TextIO = field(default_factory=lambda: sys.stdin)
    stream_out: TextIO = field(default_factory=lambda: sys.stdout)

    _write_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _turn_index: int = field(default=0, init=False)
    _interrupted: set[str] = field(default_factory=set, init=False)
    #: Tura, która TERAZ trwa. Potrzebna, bo `turn/steer` ma warunek wstępny na jej identyfikator.
    _active_turn: str = field(default="", init=False)
    #: Zdania dopowiedziane w trakcie tury, w kolejności przyjęcia.
    _steered: list[str] = field(default_factory=list, init=False)
    _threads: list[threading.Thread] = field(default_factory=list, init=False)
    initialized: bool = field(default=False, init=False)
    #: State for requests the SERVER makes of the client (`_ask_client`). Separate lock from the
    #: write lock: one guards the wire, this guards the table of who is waiting for what.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _request_id: int = field(default=0, init=False)
    _client_waiters: dict[int, Any] = field(default_factory=dict, init=False)
    _answers: list[dict[str, Any]] = field(default_factory=list, init=False)

    # -- wire ---------------------------------------------------------------------------

    def _send(self, frame: dict[str, Any]) -> None:
        with self._write_lock:
            self.stream_out.write(json.dumps(frame, ensure_ascii=False) + "\n")
            self.stream_out.flush()

    def _respond(self, ident: Any, result: dict[str, Any]) -> None:
        # No "jsonrpc" field on the wire: the app-server protocol omits it, and `protocol.py`
        # documents that a client validating its presence would reject every frame.
        self._send({"id": ident, "result": result})

    def _error(self, ident: Any, code: int, message: str) -> None:
        self._send({"id": ident, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    # -- the protocol -------------------------------------------------------------------

    def handle(self, frame: dict[str, Any]) -> None:
        # A RESPONSE to a request WE made: an id and no method. Routed to whoever is waiting,
        # before anything else looks at the frame.
        if frame.get("id") is not None and "method" not in frame:
            with self._lock:
                waiter = self._client_waiters.get(int(frame["id"]))
            if waiter is not None:
                waiter.deliver(frame.get("result", frame.get("error")))
            return
        method = frame.get("method")
        ident = frame.get("id")
        params = frame.get("params") or {}

        if method == "initialize":
            if self.initialized:
                # Prawdziwy app-server odpowiada na drugi handshake błędem -32600
                # („Already initialized"), zmierzone 20.09 w żywym kontenerze. Atrapa, która
                # przyjmuje initialize dowolnie wiele razy, zieleni kod padający na starcie.
                self._error(ident, -32600, "Already initialized")
                return
            self.initialized = True
            self._respond(
                ident,
                {
                    "userAgent": {"name": "fake-app-server", "version": "0.1.0"},
                    "capabilities": {},
                },
            )
            return
        if method == "initialized":
            return
        if ident is None:
            return

        if method == "thread/start":
            if params.get("ephemeral"):
                self._error(ident, -32602, "atrapa nie obsługuje wątków ephemeral w sesji")
                return
            thread_id = str(self.script.get("thread_id") or f"th_{uuid.uuid4().hex[:12]}")
            # Which model the caller asked for, beside the scenario, so a test can read it after
            # the process ends (27.09: sessions asked for none and the engine chose one the
            # server then refused for a ChatGPT account).
            _append_line(
                self.state_path.with_suffix(".model"), f"start {params.get('model') or '-'}"
            )
            self._respond(ident, {"thread": {"id": thread_id}})
            self._notify("thread/started", {"thread": {"id": thread_id}})
            return

        if method == "thread/resume":
            thread_id = str(params.get("threadId") or "")
            if not thread_id:
                self._error(ident, -32602, "thread/resume wymaga threadId")
                return
            known = str(self.script.get("thread_id") or thread_id)
            if self.script.get("refuse_resume"):
                # A connection changed under the session, or the thread is gone. The session has
                # to survive this by starting a new engine thread and keeping its own history.
                self._error(ident, -32000, f"wątek {thread_id} nie istnieje w tym połączeniu")
                return
            _append_line(
                self.state_path.with_suffix(".model"), f"resume {params.get('model') or '-'}"
            )
            self._respond(ident, {"thread": {"id": known}})
            self._notify("thread/started", {"thread": {"id": known}})
            return

        if method == "turn/start":
            thread_id = str(params.get("threadId") or "")
            turn_id = f"turn_{uuid.uuid4().hex[:12]}"
            self._active_turn = turn_id
            self._respond(ident, {"turn": {"id": turn_id, "status": "inProgress"}})
            worker = threading.Thread(
                target=self._run_turn,
                args=(thread_id, turn_id, params),
                name=f"fake-turn-{turn_id}",
                daemon=True,
            )
            self._threads.append(worker)
            worker.start()
            return

        if method == "turn/interrupt":
            turn_id = str(params.get("turnId") or "")
            self._interrupted.add(turn_id)
            self._respond(ident, {"turnId": turn_id})
            return

        if method == "turn/steer":
            # `expectedTurnId` jest warunkiem wstępnym także tutaj. Atrapa, która przyjmuje
            # dopowiedzenie do tury już zakończonej, zieleniłaby kod, który na żywo traci zdanie
            # człowieka: prawdziwy app-server odpowiada wtedy błędem.
            expected = str(params.get("expectedTurnId") or "")
            if expected != self._active_turn:
                self._error(
                    ident,
                    -32602,
                    f"expectedTurnId={expected!r} nie jest aktywną turą ({self._active_turn!r})",
                )
                return
            text = _input_text(params)
            self._steered.append(text)
            _append_line(self.state_path.with_suffix(".steer"), text)
            self._respond(ident, {"turnId": expected})
            return

        if method == "config/read":
            try:
                self._respond(ident, {"config": _effective_config()})
            except FakeServerError as exc:
                self._error(ident, -32603, str(exc))
            return

        self._error(ident, -32601, f"atrapa nie obsługuje metody {method!r}")

    # -- the turn -----------------------------------------------------------------------

    def _next_turn_script(self) -> dict[str, Any]:
        """The next turn of the script, counting ACROSS PROCESSES.

        One fake process handles one turn, because the session client starts an app-server per
        turn exactly as it will against the real binary. An index kept in memory would therefore
        reset to zero every time, and a two-turn script would answer its first turn twice. The
        count lives in a file next to the script, which is also what makes a restarted turn
        replay the same step: that is the behaviour the restart test needs.
        """
        turns = self.script.get("turns") or []
        self._turn_index = _read_counter(self.state_path)
        if self._turn_index < len(turns):
            turn = turns[self._turn_index]
        else:
            turn = self.script.get("default_turn")
        _write_counter(self.state_path, self._turn_index + 1)
        self._turn_index += 1
        if turn is None:
            # Out of script. Recorded as a FAILED turn rather than an empty success, for the
            # reason the module docstring gives.
            return {
                "steps": [],
                "status": "failed",
                "error": (
                    f"atrapa app-servera: {self._turn_index}. tura, a scenariusz ma {len(turns)}"
                ),
            }
        return turn

    def _ask_client(self, method: str, params: dict[str, Any], *, timeout_s: float) -> Any:
        """Send a request TO the client and wait for its answer, like the real app-server does.

        Blocking on purpose. The whole value of this step is that a client which treats the
        request as a notification gets caught: the turn stops here and the test fails on a
        timeout, which is exactly what the live session did for seven minutes.
        """
        with self._lock:
            self._request_id += 1
            ident = self._request_id
            waiter = _Answer()
            self._client_waiters[ident] = waiter
        self._send({"id": ident, "method": method, "params": params})
        try:
            return waiter.await_value(timeout_s)
        except TimeoutError:
            raise FakeServerError(
                f"klient nie odpowiedział na {method} w {timeout_s} s. Tak wyglądała awaria "
                "z 19.09: żądanie serwer do klienta potraktowane jak notyfikacja"
            ) from None
        finally:
            with self._lock:
                self._client_waiters.pop(ident, None)

    def _run_turn(self, thread_id: str, turn_id: str, params: dict[str, Any]) -> None:
        turn = self._next_turn_script()
        items: list[dict[str, Any]] = []
        user_text = _input_text(params)
        if user_text:
            items.append(
                {"type": "userMessage", "id": f"it_{uuid.uuid4().hex[:8]}", "content": user_text}
            )
        status = str(turn.get("status") or "completed")
        error: dict[str, Any] | None = None
        try:
            for step in turn.get("steps") or []:
                if turn_id in self._interrupted:
                    status = "interrupted"
                    break
                item = self._run_step(thread_id, turn_id, step)
                if item is not None:
                    items.append(item)
                if step.get("fail"):
                    status = "failed"
                    error = {"message": str(step["fail"])}
                    break
        except FakeServerError as exc:
            status = "failed"
            error = {"message": str(exc)}
        if turn_id in self._interrupted and status != "failed":
            status = "interrupted"
        if turn.get("error") and error is None:
            error = {"message": str(turn["error"])}
        payload: dict[str, Any] = {
            "threadId": thread_id,
            "turn": {"id": turn_id, "status": status, "items": items},
        }
        if error is not None:
            payload["turn"]["error"] = error
        self._notify("turn/completed", payload)

    def _run_step(
        self, thread_id: str, turn_id: str, step: dict[str, Any]
    ) -> dict[str, Any] | None:
        if "delay_ms" in step:
            time.sleep(float(step["delay_ms"]) / 1000.0)
            return None
        if "elicit" in step:
            # A SERVER TO CLIENT REQUEST in the middle of a turn, which is what hung the first
            # real session (live, 2026-09-19 18:38:46 UTC): the app-server asked, our client
            # logged the question as a notification, and both sides waited. The fake reproduces
            # it exactly: it BLOCKS on the answer, so a client that does not reply hangs here and
            # the test times out instead of passing by accident.
            ask = step["elicit"] if isinstance(step["elicit"], dict) else {}
            method = str(ask.get("method") or "mcpServer/elicitation/request")
            params = {
                "server": ask.get("server", "wenext"),
                "message": ask.get("message", "czy mogę?"),
            }
            answer = self._ask_client(method, params, timeout_s=float(ask.get("timeout_s", 10)))
            self._answers.append({"method": method, "answer": answer})
            return {
                "type": "agentMessage",
                "id": f"it_{uuid.uuid4().hex[:8]}",
                "text": f"odpowiedź klienta na {method}: {json.dumps(answer, ensure_ascii=False)}",
            }
        if "message" in step:
            item = {
                "type": "agentMessage",
                "id": f"it_{uuid.uuid4().hex[:8]}",
                "text": str(step["message"]),
            }
            self._emit_item(thread_id, turn_id, item)
            return item
        if "tool" in step:
            return self._call_tool(thread_id, turn_id, step)
        if "fail" in step:
            return None
        raise FakeServerError(
            f"krok scenariusza bez `tool`, `message`, `delay_ms` ani `fail`: {step}"
        )

    def _call_tool(self, thread_id: str, turn_id: str, step: dict[str, Any]) -> dict[str, Any]:
        name = str(step["tool"])
        server_name = str(step.get("server") or _server_for(name, self.mcp))
        arguments = step.get("arguments") or {}
        item_id = f"it_{uuid.uuid4().hex[:8]}"
        started = {
            "type": "mcpToolCall",
            "id": item_id,
            "server": server_name,
            "tool": name,
            "arguments": arguments,
            "status": "inProgress",
        }
        self._notify(
            "item/started",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "startedAtMs": int(time.time() * 1000),
                "item": started,
            },
        )
        client = self.mcp.get(server_name)
        if client is None:
            finished = {
                **started,
                "status": "failed",
                "error": f"nie ma serwera MCP {server_name!r}; skonfigurowane: {sorted(self.mcp)}",
            }
        else:
            began = time.monotonic()
            try:
                result = client.request("tools/call", {"name": name, "arguments": arguments})
                finished = {
                    **started,
                    "status": "failed" if result.get("isError") else "completed",
                    "result": result,
                    "durationMs": int((time.monotonic() - began) * 1000),
                }
                if result.get("isError"):
                    finished["error"] = _first_text(result)
            except FakeServerError as exc:
                finished = {**started, "status": "failed", "error": str(exc)}
        self._notify(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": int(time.time() * 1000),
                "item": finished,
            },
        )
        return finished

    def _emit_item(self, thread_id: str, turn_id: str, item: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        self._notify(
            "item/started",
            {"threadId": thread_id, "turnId": turn_id, "startedAtMs": now_ms, "item": item},
        )
        self._notify(
            "item/completed",
            {"threadId": thread_id, "turnId": turn_id, "completedAtMs": now_ms, "item": item},
        )

    def serve(self) -> None:
        for line in self.stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                self.handle(frame)
        for worker in self._threads:
            worker.join(timeout=5)


def _read_counter(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_counter(path: Path, value: int) -> None:
    with contextlib.suppress(OSError):
        path.write_text(str(value), encoding="utf-8")


def _append_line(path: Path, line: str) -> None:
    """Dopisz jedną linię obok scenariusza, żeby test mógł to przeczytać po zakończeniu procesu.

    Atrapa żyje jedną turę, więc stan w pamięci ginie razem z nią. Plik obok scenariusza jest tym
    samym sposobem, którym liczony jest numer tury.
    """
    with contextlib.suppress(OSError), path.open("a", encoding="utf-8") as handle:
        handle.write(line.replace("\n", " ") + "\n")


#: Wartości `web_search`, które przyjmuje PRZYPIĘTA binarka. Zmierzone 20.09 odczytem z żywego
#: runtime'u: profil z `"enabled"` dostał `unknown variant `enabled`, expected one of `disabled`,
#: `cached`, `indexed`, `live``. Ta lista jest tu DRUGĄ kopią prawdy i to jest celowe: pierwsza
#: (`hardening.WEB_SEARCH_MODES`) mówi, co wpisujemy, ta mówi, co silnik przyjmie. Gdyby obie
#: pochodziły z jednego miejsca, atrapa potwierdzałaby każdą pomyłkę w tamtym miejscu.
WEB_SEARCH_VARIANTS = ("disabled", "cached", "indexed", "live")


def _effective_config() -> dict[str, Any]:
    """Konfiguracja EFEKTYWNA: `config.toml` tego CODEX_HOME plus `--config` z wiersza poleceń.

    Atrapa, która odpowiada stałą, potwierdzałaby każdy hardening, także ten, którego nikt nie
    zapisał. Odczyt z pliku sprawia, że sesja sprawdzająca konfigurację po starcie (D12, D-046)
    mierzy tu to samo, co zmierzy na żywo: czy to, o co poprosiliśmy, doszło do runtime'u.

    NADPISANIA Z WIERSZA POLECEŃ SĄ CZĘŚCIĄ TEJ ODPOWIEDZI OD E9 (21.09), bo od tej zmiany tura
    podaje tędy CAŁĄ swoją konfigurację: wpis serwera narzędzi, `web_search` z profilu i
    wyłączone flagi. Prawdziwa binarka zwraca wtedy wartości z argumentów (zmierzone na 0.147.0,
    `config_probe --mcp-overrides`: plik miał `disabled`, odczyt oddał `live`), więc atrapa,
    która pokazywałaby sam plik, kazałaby sesji zapisać niezgodność, której na żywo nie ma.
    """
    home = Path(os.environ.get(ENV_HOME) or ".")
    text = ""
    with contextlib.suppress(OSError):
        text = (home / "config.toml").read_text(encoding="utf-8")
    config: dict[str, Any] = {"features": {}}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"')
        if section == "features":
            config["features"][key] = value == "true"
        elif not section:
            config[key] = value
    for key, value in effective_config(home, _config_overrides(None)).items():
        if key == "features" and isinstance(value, dict):
            config["features"].update(value)
        elif key != "features":
            config[key] = value
    mode = config.get("web_search")
    if mode is not None and mode not in WEB_SEARCH_VARIANTS:
        raise FakeServerError(
            f"invalid configuration: unknown variant `{mode}`, expected one of "
            + ", ".join(f"`{one}`" for one in WEB_SEARCH_VARIANTS)
            + " in `web_search`"
        )
    return config


def _input_text(params: dict[str, Any]) -> str:
    items = params.get("input") or []
    return "\n".join(
        str(item.get("text") or "") for item in items if isinstance(item, dict)
    ).strip()


def _server_for(tool: str, servers: dict[str, _McpClient]) -> str:
    """Which server owns a tool, when the script does not say. One server is the usual case."""
    if len(servers) == 1:
        return next(iter(servers))
    return "wenext"


def _first_text(result: dict[str, Any]) -> str:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")[:500]
    return ""


def _config_overrides(argv: list[str] | None) -> tuple[str, ...]:
    """Pary `--config klucz=wartość` z wiersza poleceń, w kolejności podania.

    Prawdziwa binarka przyjmuje je PRZED podkomendą (`codex --config … app-server`), ale atrapa
    nie udaje parsera: bierze każdą parę, gdziekolwiek stoi, bo interesuje ją treść, nie składnia.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    found: list[str] = []
    index = 0
    while index < len(arguments):
        if arguments[index] in ("--config", "-c") and index + 1 < len(arguments):
            found.append(arguments[index + 1])
            index += 2
            continue
        index += 1
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    script_path = (os.environ.get(ENV_SCRIPT) or "").strip()
    if not script_path:
        print(
            f"atrapa app-servera: {ENV_SCRIPT} nie jest ustawione, nie ma czego odgrywać",
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"atrapa app-servera: scenariusz nieczytelny: {exc}", file=sys.stderr, flush=True)
        return 2
    codex_home = Path(os.environ.get(ENV_HOME) or ".")
    try:
        mcp = start_mcp_servers(codex_home, _config_overrides(argv))
    except FakeServerError as exc:
        print(f"atrapa app-servera: {exc}", file=sys.stderr, flush=True)
        return 3
    try:
        FakeAppServer(script=script, mcp=mcp, state_path=Path(f"{script_path}.state")).serve()
    finally:
        for client in mcp.values():
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
