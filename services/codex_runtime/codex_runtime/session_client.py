"""An ASYNC, STREAMING client for `codex app-server`, for work that lasts (P2).

WHY THIS IS NOT `protocol.AppServerClient`, MEASURED RATHER THAN ASSUMED. That client is
deliberately narrow and it fits what it was built for, the account and catalog calls of D12.
Three of its properties make it unusable for a session:

  * it is SYNCHRONOUS, and the control plane's handlers are async. Calling it would mean a
    thread per session and the lease heartbeat sharing that thread's fate;
  * `timeout_s` defaults to 30 seconds and applies to every request. A turn takes minutes;
  * `_receive` collects notifications into a list that is readable only AFTER the request it was
    waiting on returns. A session has to write the assistant's message and every tool call to
    its trace WHILE the turn runs, because that trace is what the screen shows and what survives
    a restart. Events that arrive in a batch at the end are not a stream.

So this is a second client, not an edit of the first: `protocol.py` keeps its contract and its
tests. What is NOT duplicated is the security boundary. `ALLOWED_METHODS` is IMPORTED from
`protocol`, and this client refuses anything outside it exactly as that one does. A second copy
of that list is a second answer to "what may the client call", and the two would drift.

THE TURN IS TWO-PHASE, and this is the protocol's shape, not a choice made here (read out of
`openai_codex.client.turn_start` and `wait_for_turn_completed` in the pinned SDK): `turn/start`
RETURNS as soon as the turn is registered, carrying its id, and the turn's end arrives later as
a `turn/completed` notification. So `start_turn` gives back a handle and `wait_for_turn` awaits
the completion, with everything in between delivered to the caller's sink as it happens.

WHAT THIS MODULE DOES NOT DO: it does not know about sessions, the database, budgets or tools.
It speaks the protocol and streams what it hears. `control_plane/sessions/handlers.py` is where
that becomes a session.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import ALLOWED_METHODS, ALLOWED_NOTIFICATIONS, MethodNotAllowed, ProtocolError

#: How long the handshake and ordinary requests may take. NOT the turn: a turn is awaited
#: through `wait_for_turn`, whose deadline is the session's budget and is passed in by the
#: caller. One timeout for both would have to be the longer of the two, and then a hung
#: handshake would look like a long turn.
REQUEST_TIMEOUT_S = 30.0

# thread/resume returns stored turns and tool results in one JSON line. The asyncio
# default (64 KiB) kills the reader after ordinary research. This bounds transport
# memory; it does not truncate the model's history or allocate the whole limit up front.
MAX_FRAME_BYTES = 32 * 1024 * 1024

#: How long a turn may say NOTHING before it is called failed. Not the same clock as the session
#: budget: the budget bounds a turn that is working, this bounds one that has stopped talking.
#:
#: Four minutes, and the number comes from the incident of 2026-09-19 rather than from taste: the
#: live turn produced its last event at 18:38:46 and was still RUNNING at 18:46, while the job
#: lease had died at 18:42. Anything longer than the lease TTL is a turn nobody owns any more, so
#: the bound sits below it. A model thinking, or a tool reading a thread, emits events in seconds.
SILENCE_TIMEOUT_S = 240.0

#: The status values a finished turn can carry (`TurnStatus` in the pinned package).
TURN_COMPLETED = "completed"
TURN_INTERRUPTED = "interrupted"
TURN_FAILED = "failed"
TURN_IN_PROGRESS = "inProgress"

#: A sink is given every server notification, in arrival order, as `(method, params)`. It is
#: awaited, so a sink that writes to the database backpressures the reader rather than dropping.
NotificationSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class RemoteError(ProtocolError):
    """The server answered with an `error` frame."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"{method}: [{code}] {message}")
        self.method = method
        self.code = code
        self.message = message


class ServerGone(ProtocolError):
    """The app-server process died or closed its stdout. Nothing more will arrive."""


@dataclass(frozen=True)
class TurnHandle:
    """What `start_turn` gives back: enough to await the turn and enough to interrupt it."""

    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class TurnOutcome:
    """A finished turn, as `turn/completed` reported it."""

    turn_id: str
    status: str
    error: str | None
    items: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return self.status == TURN_COMPLETED

    def assistant_text(self) -> str:
        """Every assistant message of this turn, joined. Empty when the model said nothing.

        Reads `AgentMessageThreadItem` (`type: "agentMessage"`, field `text`) as the pinned
        contract records it in `contract/app-server-notifications.json`.
        """
        parts = [
            str(item.get("text") or "")
            for item in self.items
            if item.get("type") == "agentMessage" and item.get("text")
        ]
        return "\n\n".join(p for p in parts if p.strip())


#: Pola, które przypięty app-server PRZYJMUJE w trzech metodach, jakich używa sesja. Nazwy są
#: przepisane ze schematów `v2/*.json` z binarki codex-cli 0.147.0 (kopia w artefakcie audytu
#: z 19.09).
#:
#: PO CO TO JEST, I DLACZEGO NIE „PRZEPUŚĆ WSZYSTKO". Audyt zmierzył, że `thread/start` przyjmuje
#: NIEZNANE pola i je IGNORUJE: wysłał `base_instructions` z liczbą 123456789 i nie dostał błędu,
#: a przy `baseInstructions` walidacja zadziałała. Nasz kod wysyłał właśnie `base_instructions`,
#: więc PIERWSZA PRAWDZIWA SESJA BIEGŁA BEZ INSTRUKCJI BAZOWYCH i nikt się o tym nie dowiedział,
#: bo serwer nie protestuje. Milcząca akceptacja po tamtej stronie znaczy, że kontrolę musi mieć
#: ta strona.
THREAD_START_FIELDS = frozenset(
    {
        "approvalPolicy",
        "approvalsReviewer",
        "baseInstructions",
        "config",
        "cwd",
        "developerInstructions",
        "ephemeral",
        "model",
        "modelProvider",
        "personality",
        "sandbox",
        "serviceName",
        "serviceTier",
        "sessionStartSource",
        "threadSource",
    }
)
TURN_START_FIELDS = frozenset(
    {
        "approvalPolicy",
        "approvalsReviewer",
        "clientUserMessageId",
        "cwd",
        "effort",
        "input",
        "model",
        "outputSchema",
        "personality",
        "sandboxPolicy",
        "serviceTier",
        "summary",
        "threadId",
    }
)
THREAD_RESUME_FIELDS = frozenset(
    {
        "approvalPolicy",
        "approvalsReviewer",
        "baseInstructions",
        "config",
        "cwd",
        "developerInstructions",
        "model",
        "modelProvider",
        "personality",
        "sandbox",
        "serviceTier",
        "threadId",
    }
)


def _camel(name: str) -> str:
    """`base_instructions` na `baseInstructions`. Nazwa już w tej postaci przechodzi bez zmian."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def to_wire(params: dict[str, Any], allowed: frozenset[str], method: str) -> dict[str, Any]:
    """Zamień nazwy na kształt z kontraktu i ODRZUĆ te, których w nim nie ma.

    Odrzucenie jest wyjątkiem, nie cichym pominięciem: pole, którego serwer nie zna, to albo
    literówka, albo zmiana kontraktu, a obie rzeczy mają zatrzymać turę tutaj, a nie objawić się
    jako model pracujący bez instrukcji, których nikt nie zauważył, że nie dostał.
    """
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in params.items():
        wire = _camel(key)
        if wire not in allowed:
            unknown.append(key)
            continue
        out[wire] = value
    if unknown:
        raise ProtocolError(
            f"{method}: pola {sorted(unknown)} nie ma w przypiętym kontrakcie. App-server "
            f"przyjmuje nieznane pola i je IGNORUJE, więc taka literówka nie zgłosiłaby się "
            f"sama. Dozwolone: {', '.join(sorted(allowed))}"
        )
    return out


#: Where a server request names the server it came from. Several spellings, because this is a
#: request shape the pinned package does not model: `mcpServer/elicitation/request` carries no
#: generated type, so the key is read defensively rather than assumed. An unknown shape ends in
#: a refusal WITH a reason, never in silence.
_SERVER_KEYS = ("server", "serverName", "mcpServer", "mcp_server", "name")


def _server_name(params: dict[str, Any]) -> str:
    for key in _SERVER_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("name")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _answer_for(method: str, *, accept: bool) -> dict[str, Any]:
    """The result body for one server request.

    Two shapes, because the protocol has two. `*/requestApproval` answers with a `decision`, and
    that spelling is taken from the pinned client's own default handler
    (`openai_codex.client._default_approval_handler`), not guessed. Elicitation answers with an
    `action`, which is the MCP spelling. A method we do not recognise gets an empty object: the
    server then has its answer and can move on, which is the whole point.
    """
    if method.endswith("/requestApproval"):
        return {"decision": "accept" if accept else "denied"}
    if "elicitation" in method:
        return {"action": "accept" if accept else "decline"}
    return {}


@dataclass
class AsyncAppServerClient:
    """One `codex app-server` process, spoken to over stdio, with notifications streamed out."""

    codex_bin: Path
    codex_home: Path
    sink: NotificationSink
    client_name: str = "wenext-sessions"
    client_version: str = "0.1.0"
    #: Always False in production (L2237); it is a handshake capability, not a config key.
    experimental_api: bool = False
    #: `--config klucz=wartość`, podawane binarce PRZED podkomendą, tak jak robi to SDK.
    #: TĘDY JEDZIE KONFIGURACJA TURY (E9, 21.09): wpis serwera narzędzi niesie ścieżkę do pliku
    #: zdolności TEJ tury, a `config.toml` jest wspólny dla wszystkich rozmów na jednym
    #: połączeniu i dla pilota, więc zapis do niego znaczył, że dwie rozmowy nadpisują sobie
    #: zdolność nawzajem. Domyślnie puste, żeby wywołania bez konfiguracji biegły jak dotąd.
    config_overrides: tuple[str, ...] = ()
    #: The ONE MCP server whose requests this client accepts: ours. Named rather than hardcoded
    #: so a deployment that renames the tool server does not silently start refusing itself.
    trusted_mcp_server: str = "wenext"
    request_timeout_s: float = REQUEST_TIMEOUT_S

    _proc: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _reader: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _pending: dict[int, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _turn_waiters: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict, init=False, repr=False
    )
    #: Monotonic time of the last frame from the server, of ANY kind. The silence clock reads it.
    _last_event_at: float = field(default=0.0, init=False, repr=False)
    _stderr: list[str] = field(default_factory=list, init=False, repr=False)
    _stderr_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _next_id: int = field(default=0, init=False, repr=False)
    initialized: bool = field(default=False, init=False)

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        if self._proc is not None:
            return
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        # An API key would change the auth mode under us. The runtime is on the ChatGPT login.
        env.pop("OPENAI_API_KEY", None)
        arguments: list[str] = [str(self.codex_bin)]
        for override in self.config_overrides:
            arguments += ["--config", override]
        arguments += ["app-server", "--listen", "stdio://"]
        self._proc = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=MAX_FRAME_BYTES,
        )
        self._reader = asyncio.create_task(self._read_loop(), name="app-server-reader")
        # Held, not fired and forgotten: a task with no reference can be garbage collected
        # mid-run, and the stderr of a dying app-server is exactly what a failure report needs.
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="app-server-stderr")

    async def initialize(self) -> dict[str, Any]:
        """The mandatory two-stage handshake: an `initialize` request, then `initialized`."""
        response = await self._call(
            "initialize",
            {
                "clientInfo": {"name": self.client_name, "version": self.client_version},
                "experimentalApi": self.experimental_api,
            },
            allow_before_handshake=True,
        )
        self.initialized = True
        await self._notify("initialized", {})
        return response

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        self._fail_pending(ServerGone("klient zamknięty"))
        self.initialized = False
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()

    @property
    def stderr(self) -> list[str]:
        return list(self._stderr)

    # -- the protocol ---------------------------------------------------------------------

    async def start_thread(self, **params: Any) -> str:
        """A NEW, PERSISTENT thread; returns its id.

        `ephemeral` is not passed and must not be: an ephemeral thread is exactly what a session
        cannot use, because there would be nothing to resume on the next turn. The observe path
        does pass it, and that is the difference between one judgment and work that continues.
        """
        if params.get("ephemeral"):
            raise ValueError(
                "sesja nie może mieć wątku ephemeral: przy następnej turze nie byłoby czego "
                "wznowić, a rozmowa zaczynałaby się od zera"
            )
        response = await self._call(
            "thread/start", to_wire(params, THREAD_START_FIELDS, "thread/start")
        )
        thread_id = _thread_id_of(response)
        if not thread_id:
            raise ProtocolError(f"thread/start nie zwrócił identyfikatora wątku: {response}")
        return thread_id

    async def resume_thread(self, thread_id: str, **params: Any) -> str:
        """Reopen an existing thread. The returned id is compared with the one asked for by the
        caller: a server that silently opened a DIFFERENT thread would give the model somebody
        else's history, and nothing downstream could tell."""
        response = await self._call(
            "thread/resume",
            to_wire({"threadId": thread_id, **params}, THREAD_RESUME_FIELDS, "thread/resume"),
        )
        resumed = _thread_id_of(response) or thread_id
        return resumed

    async def start_turn(self, thread_id: str, text: str, **params: Any) -> TurnHandle:
        """Send the user's message. Returns as soon as the turn is registered, with its id.

        The input shape is the pinned SDK's: a list of items, each `{"type": "text", "text": …}`.
        """
        # The silence clock starts HERE, not at construction: `_last_event_at` left at zero would
        # make every turn look silent since the epoch and fail on its first check.
        self._last_event_at = asyncio.get_running_loop().time()
        response = await self._call(
            "turn/start",
            to_wire(
                {**params, "threadId": thread_id, "input": [{"type": "text", "text": text}]},
                TURN_START_FIELDS,
                "turn/start",
            ),
        )
        turn_id = _turn_id_of(response)
        if not turn_id:
            raise ProtocolError(f"turn/start nie zwrócił identyfikatora tury: {response}")
        # Registered BEFORE the caller awaits, so a turn that completes immediately is not lost
        # between the response arriving and the wait starting.
        if turn_id not in self._turn_waiters:
            self._turn_waiters[turn_id] = asyncio.get_running_loop().create_future()
        return TurnHandle(thread_id=thread_id, turn_id=turn_id)

    async def wait_for_turn(
        self, handle: TurnHandle, *, timeout_s: float, silence_s: float = SILENCE_TIMEOUT_S
    ) -> TurnOutcome:
        """Await `turn/completed` for this turn. `timeout_s` is the session's budget, not ours.

        TWO CLOCKS, AND THE SECOND ONE IS THE LESSON OF 2026-09-19. The budget bounds a turn
        that is WORKING; it says nothing about a turn that has stopped saying anything. On live
        the app-server asked us a question, got no answer, and both sides waited: the turn sat
        in RUNNING past its lease, and the only thing that ended it was somebody looking.
        `silence_s` bounds the gap BETWEEN events, so a turn that has gone quiet ends with a
        stated reason instead of occupying the session until the budget runs out.

        A long tool call is not silence: every notification resets the clock, and our tool
        server reports its calls as they happen.
        """
        waiter = self._turn_waiters.get(handle.turn_id)
        if waiter is None:
            waiter = asyncio.get_running_loop().create_future()
            self._turn_waiters[handle.turn_id] = waiter
        deadline = asyncio.get_running_loop().time() + timeout_s
        try:
            while True:
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    raise TimeoutError
                quiet_for = asyncio.get_running_loop().time() - self._last_event_at
                gap = max(0.0, silence_s - quiet_for)
                try:
                    payload = await asyncio.wait_for(
                        asyncio.shield(waiter), timeout=min(left, gap or silence_s)
                    )
                    break
                except TimeoutError:
                    if asyncio.get_running_loop().time() - self._last_event_at >= silence_s:
                        waiter.cancel()
                        return TurnOutcome(
                            turn_id=handle.turn_id,
                            status=TURN_FAILED,
                            error=(
                                f"cisza dłuższa niż {int(silence_s)} s: app-server nie przysłał "
                                "ani jednego zdarzenia. Najczęstsza przyczyna to żądanie "
                                "serwer do klienta, na które nikt nie odpowiedział"
                            ),
                            items=(),
                        )
                    continue
        finally:
            self._turn_waiters.pop(handle.turn_id, None)
        turn = payload.get("turn") or {}
        error = turn.get("error") or {}
        return TurnOutcome(
            turn_id=str(turn.get("id") or handle.turn_id),
            status=str(turn.get("status") or TURN_FAILED),
            error=str(error.get("message")) if error.get("message") else None,
            items=tuple(turn.get("items") or ()),
        )

    async def interrupt_turn(self, handle: TurnHandle) -> dict[str, Any]:
        """Ask the server to stop this turn. The turn still ends with `turn/completed`, with
        status `interrupted`, so the caller keeps awaiting it rather than assuming."""
        return await self._call(
            "turn/interrupt", {"threadId": handle.thread_id, "turnId": handle.turn_id}
        )

    async def steer_turn(self, handle: TurnHandle, text: str) -> dict[str, Any]:
        """Wpuść zdanie człowieka do TRWAJĄCEJ tury, zamiast kazać mu czekać na następną.

        `expectedTurnId` jest warunkiem wstępnym po stronie serwera: jeżeli ta tura już się
        skończyła, wywołanie kończy się błędem. Dlatego wołający ma prawo potraktować sukces
        jako „model to dostał", a błąd jako „nie dostał i trzeba podać w następnej turze".
        Kształt parametrów jest przepisany z przypiętego SDK (`TurnSteerParams`), nie zgadnięty.
        """
        return await self._call(
            "turn/steer",
            {
                "threadId": handle.thread_id,
                "expectedTurnId": handle.turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )

    async def read_config(self) -> dict[str, Any]:
        return await self._call("config/read", {})

    # -- frames ---------------------------------------------------------------------------

    async def read_rate_limits(self) -> dict[str, Any]:
        """`account/rateLimits/read`: ile konta zostało, wprost od silnika.

        POTRZEBNE PRZY LIMICIE, i po to jest publiczne: komunikat o wyczerpanym limicie przychodzi
        jako TEKST tury, a tekstowi nie wolno wierzyć na słowo (model potrafi zacytować takie
        zdanie). Sesja pyta więc silnik i dopiero jego odpowiedź rozstrzyga, czy czekać.

        Metoda jest na allowliście protokołu od początku; bez konta zalogowanego app-server
        odpowiada błędem i wtedy `RemoteError` leci wyżej, bo NIE zmyślamy limitów.
        """
        return await self._call("account/rateLimits/read", {})

    async def _call(
        self, method: str, params: dict[str, Any], *, allow_before_handshake: bool = False
    ) -> dict[str, Any]:
        if method not in ALLOWED_METHODS:
            raise MethodNotAllowed(
                f"Metoda {method!r} jest poza allowlistem klienta JSON-RPC (L2222, L2237). "
                f"Dozwolone: {', '.join(sorted(ALLOWED_METHODS))}"
            )
        if not self.initialized and not allow_before_handshake:
            raise ProtocolError(
                "handshake nie został zakończony: żadna metoda nie przejdzie przed "
                "`initialize` i notyfikacją `initialized`"
            )
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            frame = await asyncio.wait_for(future, timeout=self.request_timeout_s)
        except TimeoutError as exc:
            raise ProtocolError(
                f"brak odpowiedzi na {method} w {self.request_timeout_s:g} s; "
                f"stderr: {' | '.join(self._stderr[-5:])}"
            ) from exc
        finally:
            self._pending.pop(request_id, None)
        if "error" in frame:
            error = frame["error"] or {}
            raise RemoteError(method, int(error.get("code") or 0), str(error.get("message") or ""))
        result = frame.get("result")
        return result if isinstance(result, dict) else {"result": result}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if method not in ALLOWED_NOTIFICATIONS:
            raise MethodNotAllowed(f"Notyfikacja {method!r} jest poza allowlistem")
        await self._send({"method": method, "params": params})

    async def _send(self, frame: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ServerGone("app-server nie jest uruchomiony")
        # Deliberately WITHOUT the "jsonrpc" field: the protocol omits it on the wire.
        proc.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _answer_server_request(self, frame: dict[str, Any]) -> None:
        """Answer a request the app-server made of us, and tell the session what we answered.

        THE POLICY, AND WHY IT IS NOT "ACCEPT EVERYTHING". A request from OUR tool server is
        already inside what the session may do: its catalog is fixed, its capability sits in a
        0600 file with a TTL, and every tool checks the scope itself. Accepting there grants
        nothing that was not granted when the session opened.

        A request from ANY OTHER server is declined, because nobody decided anything about it.
        An approval that nobody has thought about is worth less than a refusal, and a refusal
        that is written down is something a person can react to. Both outcomes reach the trace
        with the method and the decision, so a turn that was refused something says so instead
        of merely being shorter.

        NOTHING HERE RAISES. A request we cannot classify still gets an answer, because the one
        thing the server cannot survive is silence: that is the bug this method exists to fix.
        """
        frame_id = frame.get("id")
        method = str(frame.get("method") or "")
        params = frame.get("params")
        params = params if isinstance(params, dict) else {}

        accept, server, why = self._decide(method, params)
        result = _answer_for(method, accept=accept)
        try:
            await self._send({"id": frame_id, "result": result})
        except Exception as exc:  # the turn is lost anyway; say why in the trace
            why = f"{why}; odpowiedź nie wyszła: {type(exc).__name__}: {exc}"

        if self.sink is not None:
            # The sink is how the session records what happened. The answer is namespaced so it
            # cannot be mistaken for a field the app-server sent.
            await self.sink(
                method,
                {
                    **params,
                    "wenext_zadanie_serwera": True,
                    "wenext_serwer": server,
                    "wenext_decyzja": "przyjete" if accept else "odmowa",
                    "wenext_powod": why,
                },
            )

    def _decide(self, method: str, params: dict[str, Any]) -> tuple[bool, str, str]:
        """`(accept, server, reason)`. Our own tool server is accepted; everything else is not."""
        server = _server_name(params)
        if server and server == self.trusted_mcp_server:
            return (
                True,
                server,
                f"żądanie z naszego serwera narzędzi ({server}); zgoda mieści się w zakresie, "
                "który sesja dostała przy otwarciu",
            )
        if not server:
            return (
                False,
                "(nieznany)",
                f"żądanie {method} nie mówi, od którego serwera pochodzi; bez tego nie da się "
                "powiedzieć, na co byłaby zgoda",
            )
        return (
            False,
            server,
            f"żądanie z serwera {server}, o którym nikt w tej sesji nie decydował",
        )

    async def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    self._fail_pending(
                        ServerGone(
                            f"app-server zamknął stdout; stderr: {' | '.join(self._stderr[-5:])}"
                        )
                    )
                    return
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    # One unreadable line is not a reason to tear the session down; the frames
                    # that matter carry ids and will be matched.
                    continue
                await self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the reader dying silently would hang every waiter
            self._fail_pending(ServerGone(f"czytnik ramek padł: {type(exc).__name__}: {exc}"))

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        # ANY frame counts as a sign of life, including a response to our own request and a
        # request from the server. Counting only notifications would call a long tool call
        # silence, and a long tool call is the opposite of it.
        self._last_event_at = asyncio.get_running_loop().time()
        frame_id = frame.get("id")
        if frame_id is not None and "method" not in frame:
            pending = self._pending.get(int(frame_id))
            if pending is not None and not pending.done():
                pending.set_result(frame)
            return
        if frame_id is not None and "method" in frame:
            # A SERVER TO CLIENT REQUEST, and it MUST be answered. Measured on live 2026-09-19:
            # the app-server sent `mcpServer/elicitation/request` at 18:38:46 UTC, this client
            # treated it as a notification, logged it and said nothing, and the turn hung in
            # RUNNING for over seven minutes until the lease expired. Codex was doing exactly
            # what the protocol says: waiting for the answer to a request it had made.
            #
            # The distinction is one line of the wire format: a frame with BOTH `method` and
            # `id` is a request; a frame with `method` and no `id` is a notification. The pinned
            # client (`openai_codex.client._reader_loop`) makes the same split, and this is a
            # copy of its decision, not an invention of ours.
            await self._answer_server_request(frame)
            return
        method = frame.get("method")
        if not isinstance(method, str):
            return
        params = frame.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "turn/completed":
            turn_id = str((params.get("turn") or {}).get("id") or "")
            waiter = self._turn_waiters.get(turn_id)
            if waiter is None:
                # The completion can outrun `start_turn`'s response on a very short turn. Park it
                # so the caller's wait finds it instead of hanging for the whole budget.
                waiter = asyncio.get_running_loop().create_future()
                self._turn_waiters[turn_id] = waiter
            if not waiter.done():
                waiter.set_result(params)
        # EVERY notification reaches the sink, including ones this client does not interpret.
        # A session records what it does not understand rather than dropping it: an unrecognised
        # event is evidence that the protocol moved, and silence is not.
        await self.sink(method, params)

    def _fail_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
        for waiter in list(self._turn_waiters.values()):
            if not waiter.done():
                waiter.set_exception(exc)
        self._turn_waiters.clear()

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                self._stderr.append(line.decode("utf-8", "replace").rstrip("\n"))
                del self._stderr[:-200]
        except asyncio.CancelledError:
            raise
        except Exception:
            return


def _thread_id_of(response: dict[str, Any]) -> str | None:
    thread = response.get("thread")
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    if response.get("threadId"):
        return str(response["threadId"])
    return None


def _turn_id_of(response: dict[str, Any]) -> str | None:
    turn = response.get("turn")
    if isinstance(turn, dict) and turn.get("id"):
        return str(turn["id"])
    if response.get("turnId"):
        return str(response["turnId"])
    return None
