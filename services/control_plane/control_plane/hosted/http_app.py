"""The hosted MCP endpoint: JSON-RPC over `POST`, one project per person, two doors.

WHAT RUNS HERE AND WHAT DOES NOT. The tool server is `MCPToolServer.dispatch`, the same protocol
loop the plugin runs over stdio, awaited on this app's own event loop. Behind it is the ordinary
`ToolRegistry` over ONE shared asyncpg pool. There is no capsule, no gateway and no browser
reader in this process or in its container (`mcp-edge` in the compose files, and a test that
reads that file): a stranger's agent reaches the product's objects and a SERP API, nothing else.

TWO DOORS, ONE ROOM.

  * `POST /mcp`, THE PRODUCT'S DOOR. A Cloudflare Worker (`infra/cloudflare/mcp-edge/`) is the
    public face and the OAuth authorization server (GitHub sign-in); it forwards each request with
    a signed identity header and THIS process trusts only that (`edge.py`). An unknown person gets
    an organization, workspace and project created on their first request
    (`provision.ExternalIdentityStore`), idempotently under parallel first calls. No OAuth token
    ever reaches this side.
  * `POST /mcp/{token}`, THE INTERNAL DOOR (administrator, tests, hand-picked invitations). The
    personal token is in the path:
      - the token is looked up by SHA-256 on EVERY request (`HostedTokenStore.lookup`), never
        cached in memory: a revoked token is refused on its next request, not at the next restart;
      - an unknown, revoked or malformed token is a bare 404, the same body for all three, so the
        response teaches a scanner nothing;
      - the token is never echoed, never logged and never put into an exception message. The
        access log of uvicorn prints the path, so the service runs with `--no-access-log` and a
        log filter here scrubs `/mcp/<token>` anyway, in case someone starts it without that flag.

STATELESS ON THE WIRE, CACHED IN MEMORY. Streamable HTTP lets a server answer each POST with a
plain JSON body and keep no session, which is what this does: no `Mcp-Session-Id`, a notification
gets 202, `GET` gets 405 (no server-initiated stream). What IS cached is one `MCPToolServer` and
`ToolRegistry` per (person, agent), because building them opens the project's host session. The
agent (`codex` or `claude-code`) is read from `initialize`'s `clientInfo` and remembered for the
person, since later POSTs carry no `clientInfo`; an agent that never initialized, or one we do not
recognise, is served as the text-only host `claude-code` (no card tools).

THE CAPABILITY IS BUILT PER REQUEST (`hosted=True`, `host=<agent>`), with a short window, and put
on the cached server and registry before the frame is dispatched. `registry.call` checks expiry on
every call, so a cached capability would simply expire under a long-lived entry. Entries are
dropped when idle and when old (the project's host session rotates after a day, and a cached one
must not outlive a session that was closed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_plane.auth import DSN_VARIABLE
from control_plane.sessions.store import SessionStore
from control_plane.tools.capability import HOST_CLAUDE_CODE, HOST_CODEX, issue
from control_plane.tools.mcp_server import MCPToolServer
from control_plane.tools.plugin_server import HOST_LABELS, PLUGIN_GOAL
from control_plane.tools.registry import ToolRegistry

from .edge import EDGE_HEADER, EdgeRejected, EdgeVerifier
from .provision import ExternalIdentityStore, IdentityDisabled
from .tokens import HostedAccess, HostedTokenStore

#: Served when `initialize` named no agent we recognise. `claude-code` because it is the host
#: that gets NO card tools, which is the safe reading of "an agent we know nothing about".
DEFAULT_HOST = HOST_CLAUDE_CODE

#: A request body above this is refused before it is parsed. A JSON-RPC frame is a few kilobytes.
MAX_BODY_BYTES = 1024 * 1024

#: An entry untouched for this long is dropped; one older than `ENTRY_MAX_AGE_S` is rebuilt even
#: when busy, so the host session it holds is looked up again (it rotates after a day or 600
#: events, and a closed one cannot take a trace row).
ENTRY_IDLE_S = 10 * 60.0
ENTRY_MAX_AGE_S = 30 * 60.0
MAX_ENTRIES = 500

#: The window of a per-request capability. Long enough for the slowest tool call
#: (`mcp_server.TOOL_TIMEOUT_S`) with room to spare, short enough that it is not a credential.
CAPABILITY_TTL_MINUTES = 15

#: The capability needs a DSN string by contract, and this process has no business putting the
#: real one into an object that can be serialised.
_NO_DSN = "postgresql://hosted-edge-holds-no-dsn-in-a-capability"

#: `tools/call` per token per hour. A leaked token must not be able to drain the SERP quota. Env
#: `WENEXT_HOSTED_CALLS_PER_HOUR` overrides; `0` switches the limit off.
DEFAULT_CALLS_PER_HOUR = 600

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_INTERNAL_ERROR = -32603

_TOKEN_IN_PATH = re.compile(r"/mcp/[^\s/?#\"']+")


def _say(message: str) -> None:
    """Diagnostics go to stderr and NEVER carry a token: callers pass a digest prefix at most."""
    print(f"wenext-hosted: {message}", file=sys.stderr, flush=True)


class _ScrubTokens(logging.Filter):
    """Replace the token part of any `/mcp/...` path in a log record with a placeholder."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_PATH.sub("/mcp/<redacted>", item) if isinstance(item, str) else item
                for item in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_PATH.sub("/mcp/<redacted>", record.msg)
        return True


def host_from_initialize(frame: dict[str, Any]) -> str:
    """`codex` or `claude-code` from `initialize.params.clientInfo.name`, else the default."""
    params = frame.get("params")
    info = params.get("clientInfo") if isinstance(params, dict) else None
    name = str(info.get("name") or "").lower() if isinstance(info, dict) else ""
    if "codex" in name:
        return HOST_CODEX
    if "claude" in name:
        return HOST_CLAUDE_CODE
    return DEFAULT_HOST


def _rpc_error(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


@dataclass
class _Entry:
    server: MCPToolServer
    registry: ToolRegistry
    session_id: UUID
    built_at: float
    last_used: float


@dataclass
class HostedService:
    """The shared state of the endpoint: the pool, the token table and the per-token servers."""

    pool: asyncpg.Pool
    calls_per_hour: int = DEFAULT_CALLS_PER_HOUR
    clock: Any = time.monotonic
    store: HostedTokenStore = field(init=False)
    identities: ExternalIdentityStore = field(init=False)
    _entries: dict[tuple[str, str], _Entry] = field(default_factory=dict, init=False)
    _locks: dict[tuple[str, str], asyncio.Lock] = field(default_factory=dict, init=False)
    _hosts: dict[str, str] = field(default_factory=dict, init=False)
    _calls: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.store = HostedTokenStore(self.pool)
        self.identities = ExternalIdentityStore(self.pool)

    # -- cache ---------------------------------------------------------------------

    def _evict(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if now - entry.last_used > ENTRY_IDLE_S:
                self._drop(key)
        while len(self._entries) > MAX_ENTRIES:
            oldest = min(self._entries, key=lambda key: self._entries[key].last_used)
            self._drop(oldest)

    def _drop(self, key: tuple[str, str]) -> None:
        self._entries.pop(key, None)
        # The lock stays out of the way: a waiter holding it still finishes, and a fresh request
        # makes a new one. Dropping it here keeps the dict from growing with every token ever seen.
        self._locks.pop(key, None)

    def cached(self) -> int:
        return len(self._entries)

    async def _entry(self, access: HostedAccess, host: str) -> _Entry:
        key = (access.digest, host)
        now = self.clock()
        self._evict(now)
        entry = self._entries.get(key)
        if entry is not None and now - entry.built_at <= ENTRY_MAX_AGE_S:
            entry.last_used = now
            return entry
        # ONE build per key: two parallel first calls must not both open the host session, and
        # the second must find the first one's entry.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self.clock()
            entry = self._entries.get(key)
            if entry is not None and now - entry.built_at <= ENTRY_MAX_AGE_S:
                entry.last_used = now
                return entry
            sessions = SessionStore(self.pool)
            session, _created = await sessions.open_host_session(
                organization_id=access.organization_id,
                workspace_id=access.workspace_id,
                project_id=access.project_id,
                host=host,
                goal=PLUGIN_GOAL,
            )
            capability = self._capability(access, host, session.id)
            registry = ToolRegistry(capability=capability, pool=self.pool)
            server = MCPToolServer(
                capability=capability,
                registry=registry,
                sessions=sessions,
                require_handshake=False,
            )
            entry = _Entry(
                server=server,
                registry=registry,
                session_id=session.id,
                built_at=now,
                last_used=now,
            )
            self._entries[key] = entry
            return entry

    @staticmethod
    def _capability(access: HostedAccess, host: str, session_id: UUID):
        return issue(
            session_id=session_id,
            organization_id=access.organization_id,
            workspace_id=access.workspace_id,
            project_id=access.project_id,
            dsn=_NO_DSN,
            ttl_minutes=CAPABILITY_TTL_MINUTES,
            host=host,
            hosted=True,
        )

    # -- limits --------------------------------------------------------------------

    def _over_limit(self, digest: str) -> bool:
        if self.calls_per_hour <= 0:
            return False
        now = self.clock()
        window = self._calls.setdefault(digest, deque())
        while window and now - window[0] > 3600.0:
            window.popleft()
        if len(window) >= self.calls_per_hour:
            return True
        window.append(now)
        return False

    # -- one frame -----------------------------------------------------------------

    async def handle_frame(self, access: HostedAccess, frame: Any) -> dict[str, Any] | None:
        if not isinstance(frame, dict):
            return _rpc_error(None, JSONRPC_INVALID_REQUEST, "invalid request")
        method = frame.get("method")
        if not isinstance(method, str):
            # A response or something that is not a request: we never send requests, so there is
            # nothing it could be answering.
            return None
        if method == "initialize":
            host = host_from_initialize(frame)
            self._hosts[access.digest] = host
        else:
            host = self._hosts.get(access.digest, DEFAULT_HOST)
        ident = frame.get("id")
        if method == "tools/call" and ident is not None and self._over_limit(access.digest):
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Too many Wenext calls this hour on this link. Wait a little "
                                "and ask again."
                            ),
                        }
                    ],
                    "isError": True,
                },
            }
        entry = await self._entry(access, host)
        # A fresh window for THIS request, on both objects that check it (see the module docs).
        capability = self._capability(access, host, entry.session_id)
        entry.server.capability = capability
        entry.registry.capability = capability
        started = self.clock()
        try:
            response = await entry.server.dispatch(frame)
        except Exception as exc:  # the endpoint must answer, whatever a handler did
            _say(f"{access.digest[:8]} {method} failed: {type(exc).__name__}: {exc}")
            if ident is None:
                return None
            return _rpc_error(ident, JSONRPC_INTERNAL_ERROR, "internal error")
        tool = ""
        params = frame.get("params")
        if method == "tools/call" and isinstance(params, dict):
            tool = " " + str(params.get("name") or "")[:60]
        elapsed = self.clock() - started
        _say(f"{access.digest[:8]} {HOST_LABELS[host]} {method}{tool} {elapsed:.2f}s")
        return response


def _scrub_logs() -> None:
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(one, _ScrubTokens) for one in logger.filters):
            logger.addFilter(_ScrubTokens())


async def _answer(service: HostedService, access: HostedAccess, request: Request) -> Response:
    """Read the body of an authenticated POST, run its frame(s) and shape the HTTP answer."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(_rpc_error(None, JSONRPC_PARSE_ERROR, "parse error"), status_code=400)

    try:
        if isinstance(payload, list):
            if not payload:
                return JSONResponse(
                    _rpc_error(None, JSONRPC_INVALID_REQUEST, "invalid request"),
                    status_code=400,
                )
            answers = [await service.handle_frame(access, frame) for frame in payload]
            answers = [one for one in answers if one is not None]
            if not answers:
                return Response(status_code=202)
            return JSONResponse(answers)
        answer = await service.handle_frame(access, payload)
    except Exception as exc:  # last line: a traceback would be logged with the request
        _say(f"{access.digest[:8]} unhandled: {type(exc).__name__}")
        return JSONResponse(
            _rpc_error(None, JSONRPC_INTERNAL_ERROR, "internal error"), status_code=500
        )
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


def create_app(*, pool: asyncpg.Pool | None = None, edge: EdgeVerifier | None = None) -> FastAPI:
    """The ASGI app. `pool` and `edge` are for tests; production builds both from the environment
    (`DATABASE_URL`, `WENEXT_EDGE_SECRET`)."""
    state: dict[str, Any] = {"edge": edge if edge is not None else EdgeVerifier.from_env()}
    if pool is not None:
        state["service"] = HostedService(pool=pool, calls_per_hour=_calls_per_hour())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        owned: asyncpg.Pool | None = None
        if "service" not in state:
            dsn = (os.environ.get(DSN_VARIABLE) or "").strip()
            if not dsn:
                raise RuntimeError(f"brak {DSN_VARIABLE}: mcp-edge nie ma bazy")
            owned = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
            state["service"] = HostedService(pool=owned, calls_per_hour=_calls_per_hour())
        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(
        title="wenext-hosted-mcp",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    _scrub_logs()

    @app.get("/healthz")
    async def healthz() -> Response:
        service = state.get("service")
        if service is None:
            return JSONResponse({"ok": False}, status_code=503)
        try:
            await asyncio.wait_for(service.pool.fetchval("SELECT 1"), timeout=3)
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True, "edge": state["edge"] is not None})

    # `GET` and `DELETE` on the MCP paths: a server with no server-initiated stream and no session
    # answers 405, as the Streamable HTTP transport specifies. No lookup, nothing echoed.
    @app.api_route("/mcp/{token}", methods=["GET", "DELETE", "PUT", "PATCH"])
    @app.api_route("/mcp", methods=["GET", "DELETE", "PUT", "PATCH"])
    async def not_allowed(token: str = "") -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    # THE INTERNAL DOOR: a personal token in the path (administrator, tests, invitations).
    @app.post("/mcp/{token}")
    async def mcp_token(token: str, request: Request) -> Response:
        service = state["service"]
        try:
            access = await service.store.lookup(token)
        except Exception as exc:
            _say(f"token lookup failed: {type(exc).__name__}")
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if access is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return await _answer(service, access, request)

    # THE PRODUCT'S DOOR: the edge (Cloudflare Worker) has signed the person in and forwards the
    # request with a signed identity header. No token here, no OAuth here (see `edge.py`).
    @app.post("/mcp")
    async def mcp_edge(request: Request) -> Response:
        service = state["service"]
        verifier: EdgeVerifier | None = state["edge"]
        if verifier is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            identity = verifier.verify(request.headers.get(EDGE_HEADER))
        except EdgeRejected as exc:
            _say(f"edge header refused: {exc.code}")
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            access = await service.identities.resolve_or_provision(identity)
        except IdentityDisabled:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except Exception as exc:
            _say(f"identity resolution failed: {type(exc).__name__}: {exc}")
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return await _answer(service, access, request)

    app.state.hosted = state
    return app


def _calls_per_hour() -> int:
    raw = (os.environ.get("WENEXT_HOSTED_CALLS_PER_HOUR") or "").strip()
    try:
        return int(raw) if raw else DEFAULT_CALLS_PER_HOUR
    except ValueError:
        return DEFAULT_CALLS_PER_HOUR
