"""Stream Gateway — the only service published on the host.

Implements the flow from section 14.3 (L2319-2345), quoted verbatim from the Polish source
document:

    [Połącz konto] / [Otwórz interaktywnie]
        ↓ POST /browser-sessions
    ProfileLease + start/reuse Session Capsule
        ↓ VIEW_ONLY: issue ticket
          CONTROL: atomically acquire HUMAN InteractionLease + issue ticket
        ↓ one-time StreamTicket (30-60 s, stored only as hash)
        ↓ POST exchange on isolated stream origin
    Secure HttpOnly session cookie
        ↓ dashboard iframe/panel + WSS

Four things that this module enforces and that are easy to miss:

1. **The ticket does not reach the query string or the logs** (L2343). It is returned once,
   in the POST response body, and it is exchanged with a POST as well.
2. **Exchange requires an exact `Origin`, a CSRF token and the POST method** (L2345).
3. **The VNC password never leaves the server.** The RFB handshake is performed by the
   gateway, with its own credential read from the capsule's runtime volume (L2470).
4. **`VIEW_ONLY` is enforced on the server side**, on every frame, after splitting
   concatenated RFB messages (L5594).

What is NOT here and why: there is no issuing of `ProfileLease` nor `InteractionLease`.
The gateway USES them, but their owner is the Control Plane (F1). In F0 the generations come
from outside as parameters, and `RejestrTicketow` checks that they match on exchange.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from community_os_contracts import StreamSessionRole
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .rfb import RFBError, SecurityType, may_forward, perform_handshake, split_messages
from .tickets import TicketRegistry, TicketRejected

#: File with the plaintext VNC passwords, written by the BrowserSupervisor when starting
#: INTERACTIVE mode. It lies in the capsule's shared runtime volume, permissions 0600.
VNC_CREDENTIALS_PATH = Path(
    os.environ.get("WENEXT_VNC_CREDENTIALS", "/run/wenext/capsule/vnc-credentials")
)
VNC_SOCKET_PATH = Path(os.environ.get("WENEXT_VNC_SOCKET", "/run/wenext/capsule/vnc.sock"))

#: Origin from which the ticket may be exchanged. The stream iframe sits on a separate
#: origin (L2345).
ALLOWED_ORIGIN = os.environ.get("WENEXT_STREAM_ORIGIN", "http://127.0.0.1:8760")

COOKIE_NAME = "wenext_stream"

app = FastAPI(title="wenext Stream Gateway", docs_url=None, redoc_url=None, openapi_url=None)

registry = TicketRegistry()

#: A stream session lives at most this long without activity — the length of a human's login,
#: not of the day. Review of level 2 (2026-09-04): the previous dict had no expiry and no caller
#: of `revoke_session`, so a captured cookie was CONTROL for as long as the process lived.
SESSION_IDLE_TTL = timedelta(minutes=15)

#: How many stream sessions may exist at once. One operator opens one screen; the limit is a
#: ceiling against a leak, not a quota — the oldest session is evicted when it is reached.
SESSION_LIMIT = 8


class StreamSessions:
    """Active stream sessions: cookie key → (ticket record, CSRF token, last activity).

    Every read goes through `get`, which expires idle sessions on the way; `touch` renews on
    activity (a WebSocket frame in either direction); `revoke_all` is what the control plane calls
    when the human steer closes — the cookie in the browser is then just a string.
    """

    def __init__(self, *, ttl: timedelta = SESSION_IDLE_TTL, limit: int = SESSION_LIMIT) -> None:
        self._entries: dict[str, tuple[object, str, datetime]] = {}
        self.ttl = ttl
        self.limit = limit

    def open(self, record: object, csrf: str, *, now: datetime) -> str:
        self._expire(now)
        while len(self._entries) >= self.limit:
            oldest = min(self._entries, key=lambda k: self._entries[k][2])
            del self._entries[oldest]
        key = secrets.token_urlsafe(32)
        self._entries[key] = (record, csrf, now)
        return key

    def get(self, key: str | None, *, now: datetime) -> tuple[object, str] | None:
        self._expire(now)
        if key is None:
            return None
        entry = self._entries.get(key)
        return (entry[0], entry[1]) if entry is not None else None

    def touch(self, key: str, *, now: datetime) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return False
        self._entries[key] = (entry[0], entry[1], now)
        return True

    def alive(self, key: str, *, now: datetime) -> bool:
        return self.get(key, now=now) is not None

    def revoke(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    def revoke_all(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        return count

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def _expire(self, now: datetime) -> None:
        stale = [k for k, (_, _, seen) in self._entries.items() if now - seen > self.ttl]
        for key in stale:
            del self._entries[key]


_sessions = StreamSessions()

# ---------------------------------------------------------------------------
# Level 2 of plan B (`docs/OUTCOMES.md` §8, D-026): the human login path.
# ---------------------------------------------------------------------------

#: Only the control plane may mint tickets. `POST /browser-sessions` used to be anonymous while the
#: gateway was the one published port — anyone on the host could have minted a CONTROL ticket for
#: a capsule in INTERACTIVE mode. Empty → issuing is DISABLED (503), not open.
ISSUER_TOKEN_HEADER = "X-Wenext-Issuer-Token"
ISSUER_TOKEN = os.environ.get("WENEXT_GATEWAY_ISSUER_TOKEN", "").strip()


def _ticket_ttl() -> timedelta:
    """`WENEXT_STREAM_TICKET_TTL_SECONDS` — set in compose and `.env` since F0, read by nobody until
    now (the registry used its own 45 s). The dashboard opens the viewer with a 303 right after
    minting, so a couple of minutes is plenty and a typo cannot make a ticket live for hours."""
    raw = os.environ.get("WENEXT_STREAM_TICKET_TTL_SECONDS", "").strip()
    try:
        seconds = int(raw) if raw else 45
    except ValueError:
        seconds = 45
    return timedelta(seconds=max(15, min(seconds, 600)))


#: noVNC v1.7.0 (pinned in `versions.lock.toml`, fetched at image build by
#: `infra/novnc/fetch_novnc.py`). Served under `/novnc/` on THIS origin so the viewer page and the
#: exchange share `Origin` (L2345). Absent directory (host tests) → nothing is mounted.
NOVNC_DIR = Path(os.environ.get("WENEXT_NOVNC_DIR", "/srv/novnc"))
NOVNC_URL_PATH = os.environ.get("WENEXT_NOVNC_ORIGIN_PATH", "/novnc")

VIEWER_PATH = "/login-capsule"

#: What a `Host` header may look like before it is written into the viewer's CSP: a hostname or
#: an IPv4 literal, an optional port, nothing else. Bracketed IPv6 is not on the list because the
#: gateway binds 127.0.0.1 and compose names — a shape nobody sends here does not get a rule.
HOST_HEADER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})(?::[0-9]{1,5})?$")

#: The one page a human opens. The ticket arrives in the FRAGMENT (`#ticket=…`), which the browser
#: never sends to the server (L2343: not in query strings, not in logs). The page exchanges it with
#: `POST /stream/exchange` (same origin, so `Origin` matches) and connects noVNC's RFB client to
#: `/stream/ws` — the cookie set by the exchange rides along on the WebSocket.
#: Everything inline, no third-party origin, CSP below pins that down.
VIEWER_HTML = """<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><title>Przeglądarka kapsuły — logowanie</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 html,body{margin:0;height:100%;background:#111;color:#eee;font:14px system-ui,sans-serif}
 #bar{padding:8px 12px;background:#1e1e1e;display:flex;gap:16px;align-items:center}
 #status{flex:1}
 #screen{height:calc(100% - 38px)}
 .warn{color:#f5c542}.err{color:#ff6b6b}.ok{color:#7bd88f}
</style></head>
<body>
<div id="bar"><strong>Chrome kapsuły</strong><span id="status">Łączenie…</span>
<span class="warn">Zaloguj się na platformie jak człowiek. Hasło wpisujesz TYLKO w oknie poniżej.</span></div>
<div id="screen"></div>
<script type="module">
import RFB from "__NOVNC__/core/rfb.js";
const status = document.getElementById("status");
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, "", location.pathname);  // the ticket leaves the address bar at once
const ticket = params.get("ticket");
const plg = Number(params.get("plg") || "1");
const ilg = params.get("ilg");
function say(text, cls) { status.textContent = text; status.className = cls || ""; }
if (!ticket) {
  say("Brak ticketu w adresie — otwórz ten ekran z panelu (przycisk „Otwórz przeglądarkę kapsuły”).", "err");
} else {
  const body = { ticket, current_profile_lease_generation: plg };
  if (ilg !== null) body.current_interaction_lease_generation = Number(ilg);
  fetch("/stream/exchange", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); })
  .then((session) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const rfb = new RFB(document.getElementById("screen"), `${proto}://${location.host}/stream/ws`,
      { shared: true, credentials: {} });
    rfb.scaleViewport = true; rfb.resizeSession = false; rfb.viewOnly = session.role !== "CONTROL";
    rfb.addEventListener("connect", () => say(`Połączono (${session.role}). Zaloguj się, potem wróć do panelu i kliknij „Gotowe”.`, "ok"));
    rfb.addEventListener("disconnect", (e) => say(e.detail.clean ? "Rozłączono." : "Połączenie zerwane — kapsuła nie jest w trybie z ekranem albo sesja wygasła.", "err"));
    rfb.addEventListener("securityfailure", (e) => say(`Odmowa: ${e.detail.reason}`, "err"));
  })
  .catch((e) => say(`Ticket odrzucony: ${e.message}. Wróć do panelu i otwórz ekran ponownie.`, "err"));
}
</script>
</body></html>
"""  # noqa: E501 — inline HTML/JS of the viewer; wrapping it would change what the browser runs

VIEWER_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' ws://{host} wss://{host}; img-src 'self' data: blob:; "
    "font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


def _load_password(role: StreamSessionRole) -> bytes | None:
    """Reads the server-side VNC password for the given role.

    Returns `None` if the file is absent — the RFB handshake then ends fail-closed
    instead of silently falling back to `SecurityType=None`.
    """
    if not VNC_CREDENTIALS_PATH.exists():
        return None
    credentials: dict[str, str] = {}
    for line in VNC_CREDENTIALS_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            credentials[key.strip()] = value.strip()
    key = "full" if role is StreamSessionRole.CONTROL else "view_only"
    password = credentials.get(key)
    return password.encode("utf-8") if password else None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "gniazdo_vnc": VNC_SOCKET_PATH.exists(),
            "poswiadczenia": VNC_CREDENTIALS_PATH.exists(),
            "novnc": NOVNC_DIR.is_dir(),
            "wystawianie_ticketow": bool(ISSUER_TOKEN),
        }
    )


# ---------------------------------------------------------------------------
# The human's screen: noVNC + the viewer page (level 2 of plan B)
# ---------------------------------------------------------------------------

if NOVNC_DIR.is_dir():
    app.mount(NOVNC_URL_PATH, StaticFiles(directory=str(NOVNC_DIR)), name="novnc")


@app.get(VIEWER_PATH, include_in_schema=False)
async def viewer(request: Request) -> Response:
    """The page the founder opens from the dashboard. Static HTML, strict CSP, no ticket in it —
    the ticket lives in the URL fragment the browser keeps to itself."""
    if not NOVNC_DIR.is_dir():
        raise HTTPException(
            status_code=503,
            detail="obraz gatewaya nie zawiera noVNC — brak katalogu "
            f"{NOVNC_DIR} (infra/novnc/fetch_novnc.py przy buildzie)",
        )
    host = request.headers.get("host", "")
    if not HOST_HEADER.fullmatch(host):
        # The value goes into the CSP header verbatim; a Host with `;`, spaces or a scheme in it
        # would let a client rewrite the policy that protects it (review 2026-09-04, item 5).
        raise HTTPException(status_code=400, detail="nagłówek Host ma niedozwolony kształt")
    return HTMLResponse(
        VIEWER_HTML.replace("__NOVNC__", NOVNC_URL_PATH.rstrip("/")),
        headers={
            "Content-Security-Policy": VIEWER_CSP.format(host=host),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Issuing the ticket
# ---------------------------------------------------------------------------


class SessionRequest(BaseModel):
    browser_session_id: UUID
    user_id: UUID
    role: StreamSessionRole
    profile_lease_generation: int
    interaction_lease_id: UUID | None = None
    interaction_lease_generation: int | None = None


@app.post("/browser-sessions")
async def create_browser_session(
    payload: SessionRequest,
    issuer: str | None = Header(default=None, alias=ISSUER_TOKEN_HEADER),
) -> JSONResponse:
    """Issues a one-time StreamTicket — to the control plane only.

    `CONTROL` without a bound HUMAN InteractionLease is rejected (L5590) — this is guarded
    by `RejestrTicketow.wydaj`, not by this endpoint, so that the rule has a single place.

    The issuer token (level 2 of plan B) closes the hole this endpoint used to be: the gateway is
    the one port published on the host, and a ticket is a live browser session. No token in the
    environment → 503 (issuing disabled), a wrong one → 401. Constant-time comparison.
    """
    if not ISSUER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="wystawianie ticketów wyłączone: brak WENEXT_GATEWAY_ISSUER_TOKEN w środowisku "
            "gatewaya (D-026, poziom 2)",
        )
    if issuer is None or not hmac.compare_digest(
        issuer.encode("utf-8"), ISSUER_TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="brak albo zły token wystawcy")
    try:
        ticket, record = registry.issue(
            browser_session_id=payload.browser_session_id,
            user_id=payload.user_id,
            role=payload.role,
            profile_lease_generation=payload.profile_lease_generation,
            interaction_lease_id=payload.interaction_lease_id,
            interaction_lease_generation=payload.interaction_lease_generation,
            teraz=datetime.now(UTC),
            ttl=_ticket_ttl(),
        )
    except TicketRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The ticket comes back in the response BODY. Never in the URL, never in a log (L2343).
    return JSONResponse(
        {
            "ticket": ticket,
            "stream_session_id": str(record.id),
            "role": record.role.value,
            "ticket_expires_at": record.ticket_expires_at.isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Exchanging the ticket for a cookie
# ---------------------------------------------------------------------------


class ExchangeRequest(BaseModel):
    ticket: str
    current_profile_lease_generation: int
    current_interaction_lease_generation: int | None = None


@app.post("/stream/exchange")
async def exchange_ticket(
    payload: ExchangeRequest,
    origin: str | None = Header(default=None),
) -> JSONResponse:
    """Consumes the ticket and establishes the stream session.

    Requires POST and an exact `Origin` (L2345). The ticket is one-time — replay is rejected
    (L5589). Exchange does NOT change the lease, it only consumes an existing ticket (L2343).
    """
    if origin != ALLOWED_ORIGIN:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Niedozwolony Origin: {origin!r}. Exchange działa wyłącznie z {ALLOWED_ORIGIN}."
            ),
        )

    try:
        record = registry.exchange(
            payload.ticket,
            teraz=datetime.now(UTC),
            current_profile_lease_generation=payload.current_profile_lease_generation,
            current_interaction_lease_generation=payload.current_interaction_lease_generation,
        )
    except TicketRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    csrf = secrets.token_urlsafe(24)
    stream_session_key = _sessions.open(record, csrf, now=datetime.now(UTC))

    # We set the cookie on the RETURNED response, not on the injected `Response`.
    # When returning our own `JSONResponse`, FastAPI does not merge the headers from that
    # object, so `set_cookie` on it was lost and the browser never got the session.
    # `max_age` = the idle TTL: the server forgets the session at the same moment the browser
    # drops the cookie; a longer cookie would only carry a key the server no longer knows.
    response = JSONResponse({"csrf": csrf, "role": record.role.value})
    response.set_cookie(
        COOKIE_NAME,
        stream_session_key,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=int(_sessions.ttl.total_seconds()),
        path="/stream",
    )
    return response


@app.post("/stream/revoke")
async def revoke_stream_sessions(
    issuer: str | None = Header(default=None, alias=ISSUER_TOKEN_HEADER),
) -> JSONResponse:
    """Revokes EVERY stream session — the control plane calls it when the human steer closes
    (the card's "done", or the capsule leaving a human reason). One capsule, one screen, so
    "all" is the right granularity today (see D-026 on N capsules). Issuer token, like minting."""
    if not ISSUER_TOKEN:
        raise HTTPException(status_code=503, detail="brak WENEXT_GATEWAY_ISSUER_TOKEN w gatewayu")
    presented = (issuer or "").encode("utf-8")
    if not issuer or not hmac.compare_digest(presented, ISSUER_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=401, detail="brak albo zły token wystawcy")
    return JSONResponse({"revoked": _sessions.revoke_all()})


# ---------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------


class BrowserHandshakeFailed(Exception):
    """The browser did not speak RFB 3.8 with SecurityType=None the way noVNC does."""


async def _browser_handshake(websocket: WebSocket) -> bytes:
    """The RFB handshake the BROWSER expects — replayed by the gateway after it has authenticated
    itself to Xtigervnc with the real password.

    Measured 2026-09-04 03:40 UTC, first real stream: after `perform_handshake` the gateway piped
    raw bytes, but Xtigervnc was already past its handshake and waiting for `ClientInit`, while
    noVNC was waiting for `RFB 003.008\\n` — both sides waited, noVNC gave up, "Połączenie
    zerwane". The compose comment had promised "a przeglądarce oferuje SecurityType=None" all
    along; this is where that promise is kept:

        gateway → browser: `RFB 003.008\\n`
        browser → gateway: its version (12 bytes, must start with `RFB `)
        gateway → browser: 1 security type, `NONE` — the browser never sees VncAuth
        browser → gateway: the chosen type (must be `NONE`)
        gateway → browser: SecurityResult OK (4 zero bytes)
        browser → gateway: `ClientInit` (1 byte, shared flag) — RETURNED to the caller, who forwards
                           it to Xtigervnc, which is exactly the byte it has been waiting for

    Anything else is `BrowserHandshakeFailed`, and the socket is closed with 4400. Returns the
    `ClientInit` byte plus any bytes the browser sent after it (fed into the message buffer).
    """
    inbox = bytearray()

    async def need(count: int) -> bytes:
        while len(inbox) < count:
            inbox.extend(await websocket.receive_bytes())
        chunk = bytes(inbox[:count])
        del inbox[:count]
        return chunk

    await websocket.send_bytes(b"RFB 003.008\n")
    version = await need(12)
    if not version.startswith(b"RFB "):
        raise BrowserHandshakeFailed(f"przeglądarka nie mówi RFB: {version!r}")
    await websocket.send_bytes(bytes([1, int(SecurityType.NONE)]))
    chosen = (await need(1))[0]
    if chosen != int(SecurityType.NONE):
        raise BrowserHandshakeFailed(f"przeglądarka wybrała typ bezpieczeństwa {chosen}, nie NONE")
    await websocket.send_bytes(b"\x00\x00\x00\x00")
    client_init = await need(1)
    return client_init + bytes(inbox)


@app.websocket("/stream/ws")
async def stream(
    websocket: WebSocket,
    wenext_stream: str | None = Cookie(default=None),
) -> None:
    """Forwards RFB frames between the browser and Xtigervnc.

    The handshake with the VNC server is performed by THIS code, with the password read from
    the capsule volume. The browser receives an already authenticated stream and never sees
    the credential.
    """
    entry = _sessions.get(wenext_stream, now=datetime.now(UTC))
    if entry is None or wenext_stream is None:
        await websocket.close(code=4401, reason="brak wazniej sesji streamu")
        return
    session_key: str = wenext_stream

    record, _ = entry
    role: StreamSessionRole = record.role  # type: ignore[attr-defined]

    password = _load_password(role)
    await websocket.accept()

    try:
        read, writer = await asyncio.open_unix_connection(str(VNC_SOCKET_PATH))
    except OSError as exc:
        await websocket.close(code=4503, reason="kapsula nie jest w trybie INTERACTIVE")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        await perform_handshake(read, writer, password=password)
    except RFBError as exc:
        writer.close()
        await websocket.close(code=4502, reason="handshake RFB odrzucony")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # The browser's side of the handshake (SecurityType=None, the password stays here), ending
    # with its `ClientInit`, which Xtigervnc is waiting for right now.
    try:
        client_init_and_rest = await _browser_handshake(websocket)
    except BrowserHandshakeFailed as exc:
        writer.close()
        await websocket.close(code=4400, reason="handshake RFB przegladarki odrzucony")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    writer.write(client_init_and_rest[:1])
    await writer.drain()

    buffer = bytearray(client_init_and_rest[1:])

    async def from_browser() -> None:
        """Client → VNC server, with the VIEW_ONLY filter on every message."""
        while True:
            frame = await websocket.receive_bytes()
            now = datetime.now(UTC)
            # Every frame from the human renews the session; a revoked or expired one ends here,
            # not at the next login — the browser's cookie is then just a string.
            if not _sessions.alive(session_key, now=now):
                await websocket.close(code=4408, reason="sesja streamu wygasla albo odwolana")
                return
            _sessions.touch(session_key, now=now)
            buffer.extend(frame)
            try:
                messages = split_messages(buffer)
            except RFBError:
                # Unknown message type: we close instead of guessing the length.
                await websocket.close(code=4400, reason="nieznany komunikat RFB")
                return
            for message in messages:
                allowed, _reason = may_forward(message, role)
                if not allowed:
                    # We reject silently — we do not tell the client what is not allowed.
                    continue
                writer.write(message)
            await writer.drain()

    async def to_browser() -> None:
        """VNC server → client. No filtering: both roles may watch the image — but not after
        the session is gone: the screen stops when the steer does."""
        while True:
            data = await read.read(65536)
            if not data:
                return
            if not _sessions.alive(session_key, now=datetime.now(UTC)):
                await websocket.close(code=4408, reason="sesja streamu wygasla albo odwolana")
                return
            await websocket.send_bytes(data)

    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for payload in tasks:
            payload.cancel()
        with contextlib.suppress(Exception):
            writer.close()
        with contextlib.suppress(Exception):
            await websocket.close()


def revoke_session(identyfikator: str) -> bool:
    """Revokes one stream session. Called on losing the InteractionLease (L2343)."""
    return _sessions.revoke(identyfikator)


def compare_csrf(identyfikator: str, presented: str) -> bool:
    entry = _sessions.get(identyfikator, now=datetime.now(UTC))
    if entry is None:
        return False
    return hmac.compare_digest(entry[1], presented)


def _new_id() -> str:
    return str(uuid4())
