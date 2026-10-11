"""The control plane's view of the capsule and the Stream Gateway — for the "agent's browser" card.

`docs/OUTCOMES.md` §8 „poziom 2" and D-026. Two thin HTTP clients, both fail-closed:

* `CapsuleClient` talks to the reader's control surface inside the capsule (`GET /state`,
  `POST /mode`) with the same shared token the Reddit adapter uses. It never touches `/read` —
  reading is the adapter's job; the card only needs to know the mode and to change it.
* `GatewayClient` asks the Stream Gateway for a one-time `CONTROL` ticket (`POST
  /browser-sessions`) with the issuer token only `api` holds. The ticket comes back in the body
  and is handed to the dashboard exactly once, inside the viewer URL's fragment (L2343: never in
  a query string, never in a log).

Both raise `CapsuleUnavailable` — the route turns it into 503 with the reason — instead of
returning a half-state the card would render as "everything fine".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

READER_TOKEN_HEADER = "X-Wenext-Reader-Token"
ISSUER_TOKEN_HEADER = "X-Wenext-Issuer-Token"

#: The compose service names these clients may address, and nothing else (D-027). The static half
#: of the same rule is `execution/bypass_scan.INTERNAL_CONTROL_HOSTS`: the scanner proves every
#: `.post()` here is rooted in `self._base_url`, and this check proves `self._base_url` is one of
#: our containers — so an environment variable pointing at a platform fails at construction, not
#: at the first request.
INTERNAL_CONTROL_HOSTS: frozenset[str] = frozenset({"capsule", "gateway", "capsule-x", "gateway-x"})

ENV_READER_URL = "WENEXT_BROWSER_READER_URL"
ENV_READER_TOKEN = "WENEXT_BROWSER_READER_TOKEN"
ENV_GATEWAY_URL = "WENEXT_GATEWAY_URL"
ENV_GATEWAY_ISSUER_TOKEN = "WENEXT_GATEWAY_ISSUER_TOKEN"
ENV_STREAM_PUBLIC_ORIGIN = "WENEXT_STREAM_PUBLIC_ORIGIN"
#: D-051: the X account's own capsule and screen. Unset reader URL = X shares the capsule above.
ENV_X_READER_URL = "WENEXT_X_BROWSER_READER_URL"
ENV_X_GATEWAY_URL = "WENEXT_X_GATEWAY_URL"
ENV_X_STREAM_PUBLIC_ORIGIN = "WENEXT_X_STREAM_PUBLIC_ORIGIN"

#: The audit subject of each capsule, `capsule:<profile id>`: the ids its compose service gives
#: the profile (`WENEXT_CAPSULE_ID`, `WENEXT_X_CAPSULE_ID`).
CAPSULE_SUBJECT = "capsule:capsule-01"
X_CAPSULE_SUBJECT = "capsule:x-01"


def capsule_subject(env: Mapping[str, str], platform: str | None) -> str:
    """The audit subject of the capsule that holds `platform`'s account, by the same rule as
    `CapsuleConfig.for_platform`: X names its own capsule once it has one (D-051)."""
    if platform == "X" and (env.get(ENV_X_READER_URL) or "").strip():
        return X_CAPSULE_SUBJECT
    return CAPSULE_SUBJECT


#: The founder's browser opens this page on the gateway; the ticket rides in the fragment.
VIEWER_PATH = "/login-capsule"

#: The control plane does not own `ProfileLease`/`InteractionLease` yet (F1 DEFER); the gateway's
#: ticket registry requires both generations. Constant `1` on both sides is written down here as
#: the debt it is — when leases arrive, these come from the lease store.
PROFILE_LEASE_GENERATION_STUB = 1
INTERACTION_LEASE_GENERATION_STUB = 1

#: Per call. A mode switch measured 0.3-0.7 s; 20 s is a wide margin, and it keeps the card's
#: `confirm` (one switch + two session reads at 25 s each, see `api/capsule.py`) under the
#: dashboard's 90 s: 20 + 2 x 25 + database ≈ 70 s worst case.
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class CapsuleUnavailable(Exception):
    """The capsule or the gateway did not answer usefully. The route says 503 and why."""


def internal_base_url(base_url: str, *, service: str) -> str:
    """`base_url` with its trailing slash removed — or `CapsuleUnavailable` when its host is not one
    of `INTERNAL_CONTROL_HOSTS` or its scheme is not plain HTTP inside the compose network."""
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "http" or parsed.hostname not in INTERNAL_CONTROL_HOSTS:
        raise CapsuleUnavailable(
            f"{service}: adres bazowy {base_url!r} nie wskazuje usługi compose z listy "
            f"{sorted(INTERNAL_CONTROL_HOSTS)} (D-027) — klient odmawia startu"
        )
    return base_url.strip().rstrip("/")


@dataclass(frozen=True)
class CapsuleState:
    """What `/state` said. `viewer_ready` is the dashboard's name for the display being up."""

    mode: str | None
    reason: str | None
    display_only: bool
    alive: bool
    viewer_ready: bool
    human_steer_open: bool
    since: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CapsuleState:
        mode = payload.get("mode")
        return cls(
            mode=mode if isinstance(mode, str) else None,
            reason=payload.get("reason") if isinstance(payload.get("reason"), str) else None,
            display_only=bool(payload.get("display_only")),
            alive=bool(payload.get("alive")),
            viewer_ready=bool(payload.get("vnc_ready")),
            human_steer_open=bool(payload.get("human_steer_open")),
            since=payload.get("since") if isinstance(payload.get("since"), str) else None,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "display_only": self.display_only,
            "alive": self.alive,
            "viewer_ready": self.viewer_ready,
            "human_steer_open": self.human_steer_open,
            "since": self.since,
        }


class CapsuleClient:
    def __init__(self, base_url: str, *, token: str, client: httpx.AsyncClient | None = None):
        self._base_url = internal_base_url(base_url, service="kapsuła")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def state(self) -> CapsuleState:
        return CapsuleState.from_payload(await self._call("GET", "/state"))

    async def open_human_session(
        self, reason: str = "FIRST_LOGIN", *, start_url: str | None = None
    ) -> CapsuleState:
        """INTERACTIVE with a human reason: the display is up AND the gateway gets credentials.

        `start_url` (26.09) opens a platform's login page in the visible browser, so the one thing
        the human has to do is log in. The capsule accepts only its own closed list of login
        pages (`browser_supervisor.reader`); anything else is refused there, not here.
        """
        body: dict[str, Any] = {"mode": "INTERACTIVE", "reason": reason}
        if start_url is not None:
            body["start_url"] = start_url
        return CapsuleState.from_payload(await self._call("POST", "/mode", body))

    async def close_human_session(self) -> CapsuleState:
        """Back to display-only (D-026): same profile, same session, steer closed. Not HEADLESS —
        Reddit refuses headless, so the reading mode after a login is headful without a human."""
        return CapsuleState.from_payload(
            await self._call("POST", "/mode", {"mode": "INTERACTIVE", "display_only": True})
        )

    async def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        headers = {READER_TOKEN_HEADER: self._token}
        url = f"{self._base_url}{path}"
        try:
            if method == "POST":
                # Spelled `.post()` so the bypass scanner SEES it; the file is named with its exact
                # count in `execution/bypass_scan.py::PERMITTED_INTERNAL_CONTROL` (D-026).
                response = await self._client.post(url, json=body, headers=headers)
            else:
                response = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise CapsuleUnavailable(
                f"kapsuła nieosiągalna ({type(exc).__name__}) pod {self._base_url}{path}"
            ) from exc
        if response.status_code == 401:
            raise CapsuleUnavailable(
                "kapsuła odrzuciła token — WENEXT_BROWSER_READER_TOKEN różni się między api "
                "i kapsułą"
            )
        if response.status_code == 404:
            raise CapsuleUnavailable("kapsuła nie ma kontrolera trybu — obraz sprzed D-026")
        if response.status_code != 200:
            raise CapsuleUnavailable(
                f"kapsuła odpowiedziała {response.status_code} na {method} {path}: "
                f"{_detail(response)}"
            )
        payload = _json_object(response, path)
        return payload


@dataclass(frozen=True)
class StreamTicket:
    ticket: str
    stream_session_id: str
    expires_at: str
    profile_lease_generation: int
    interaction_lease_generation: int

    def viewer_url(self, public_origin: str) -> str:
        """The URL the founder's browser opens. The ticket and both generations travel in the
        FRAGMENT: browsers never send it to the server, so it reaches neither access logs nor
        referrers (L2343); the page reads it client-side and exchanges it with a POST."""
        return (
            f"{public_origin.rstrip('/')}{VIEWER_PATH}#ticket={self.ticket}"
            f"&plg={self.profile_lease_generation}&ilg={self.interaction_lease_generation}"
        )


class GatewayClient:
    def __init__(
        self, base_url: str, *, issuer_token: str, client: httpx.AsyncClient | None = None
    ):
        self._base_url = internal_base_url(base_url, service="gateway")
        self._issuer_token = issuer_token
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def revoke_sessions(self) -> int:
        """Cut every open stream session on the gateway; returns how many there were.

        Called when the human says "done" (`confirm_session`): the capsule has already removed the
        gateway's credentials, but a WebSocket that was open before that removal would otherwise
        stay alive until the browser tab closed (review 2026-09-04, item 1). Failure raises — a
        steer that may still be open is not a state the card may report as closed.
        """
        try:
            # `.post()` so the bypass scanner counts it (`PERMITTED_INTERNAL_CONTROL`, D-026).
            response = await self._client.post(
                f"{self._base_url}/stream/revoke",
                headers={ISSUER_TOKEN_HEADER: self._issuer_token},
            )
        except httpx.HTTPError as exc:
            raise CapsuleUnavailable(
                f"gateway nieosiągalny ({type(exc).__name__}) pod {self._base_url} — sesje streamu "
                "nie zostały odwołane"
            ) from exc
        if response.status_code == 401:
            raise CapsuleUnavailable(
                "gateway odrzucił token wystawcy przy odwołaniu sesji — "
                "WENEXT_GATEWAY_ISSUER_TOKEN różni się między api i gatewayem"
            )
        if response.status_code != 200:
            raise CapsuleUnavailable(
                f"gateway odpowiedział {response.status_code} na /stream/revoke: "
                f"{_detail(response)}"
            )
        revoked = _json_object(response, "/stream/revoke").get("revoked")
        if not isinstance(revoked, int) or isinstance(revoked, bool) or revoked < 0:
            raise CapsuleUnavailable(
                "gateway: odpowiedź /stream/revoke bez liczby odwołanych sesji"
            )
        return revoked

    async def issue_control_ticket(self, *, user_id: UUID) -> StreamTicket:
        """A one-time CONTROL ticket bound to the operator. The interaction lease is a stub (see
        the module docstring); the browser session id is fresh per login."""
        body = {
            "browser_session_id": str(uuid4()),
            "user_id": str(user_id),
            "role": "CONTROL",
            "profile_lease_generation": PROFILE_LEASE_GENERATION_STUB,
            "interaction_lease_id": str(uuid4()),
            "interaction_lease_generation": INTERACTION_LEASE_GENERATION_STUB,
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/browser-sessions",
                json=body,
                headers={ISSUER_TOKEN_HEADER: self._issuer_token},
            )
        except httpx.HTTPError as exc:
            raise CapsuleUnavailable(
                f"gateway nieosiągalny ({type(exc).__name__}) pod {self._base_url}"
            ) from exc
        if response.status_code == 401:
            raise CapsuleUnavailable(
                "gateway odrzucił token wystawcy — WENEXT_GATEWAY_ISSUER_TOKEN różni się między "
                "api i gatewayem"
            )
        if response.status_code == 503:
            raise CapsuleUnavailable(
                "gateway nie wystawia ticketów (brak WENEXT_GATEWAY_ISSUER_TOKEN w jego środowisku)"
            )
        if response.status_code != 200:
            raise CapsuleUnavailable(
                f"gateway odpowiedział {response.status_code} na /browser-sessions: "
                f"{_detail(response)}"
            )
        payload = _json_object(response, "/browser-sessions")
        ticket = payload.get("ticket")
        expires = payload.get("ticket_expires_at")
        if not isinstance(ticket, str) or not ticket or not isinstance(expires, str):
            raise CapsuleUnavailable("gateway: odpowiedź bez ticketu albo bez terminu ważności")
        return StreamTicket(
            ticket=ticket,
            stream_session_id=str(payload.get("stream_session_id", "")),
            expires_at=expires,
            profile_lease_generation=PROFILE_LEASE_GENERATION_STUB,
            interaction_lease_generation=INTERACTION_LEASE_GENERATION_STUB,
        )


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, Mapping):
        return str(payload.get("detail") or payload.get("error") or payload)[:300]
    return str(payload)[:300]


def _json_object(response: httpx.Response, path: str) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CapsuleUnavailable(f"odpowiedź na {path} nie jest JSON-em") from exc
    if not isinstance(payload, Mapping):
        raise CapsuleUnavailable(f"odpowiedź na {path} nie jest obiektem")
    return payload


@dataclass(frozen=True)
class CapsuleConfig:
    """Everything the card's routes need from the environment; `None` when not configured."""

    reader_url: str
    reader_token: str
    gateway_url: str
    gateway_issuer_token: str
    stream_public_origin: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> CapsuleConfig | None:
        reader_url = (env.get(ENV_READER_URL) or "").strip()
        if not reader_url:
            return None
        return cls(
            reader_url=reader_url,
            reader_token=(env.get(ENV_READER_TOKEN) or "").strip(),
            gateway_url=(env.get(ENV_GATEWAY_URL) or "http://gateway:8760").strip(),
            gateway_issuer_token=(env.get(ENV_GATEWAY_ISSUER_TOKEN) or "").strip(),
            stream_public_origin=(
                env.get(ENV_STREAM_PUBLIC_ORIGIN) or "http://127.0.0.1:8760"
            ).strip(),
        )

    @classmethod
    def for_platform(cls, env: Mapping[str, str], platform: str | None) -> CapsuleConfig | None:
        """The capsule that holds `platform`'s account (D-051).

        X has a capsule of its own once `WENEXT_X_BROWSER_READER_URL` is set: Chromium, because
        Google refuses a person's sign-in in Chrome for Testing (measured 2026-09-27). Without
        it X shares the one capsule, as it did before. Both capsules take the same reader and
        issuer tokens: one control plane addresses both.
        """
        if platform == "X":
            reader_url = (env.get(ENV_X_READER_URL) or "").strip()
            if reader_url:
                return cls(
                    reader_url=reader_url,
                    reader_token=(env.get(ENV_READER_TOKEN) or "").strip(),
                    gateway_url=(env.get(ENV_X_GATEWAY_URL) or "http://gateway-x:8760").strip(),
                    gateway_issuer_token=(env.get(ENV_GATEWAY_ISSUER_TOKEN) or "").strip(),
                    stream_public_origin=(
                        env.get(ENV_X_STREAM_PUBLIC_ORIGIN) or "http://127.0.0.1:8761"
                    ).strip(),
                )
        return cls.from_env(env)


def utc_iso(moment: datetime) -> str:
    return moment.isoformat()
