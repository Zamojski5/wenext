"""Connecting an account from the card, and the health of each account in its footer (v10, 12.10).

The founder's X session in the agent's browser (capsule-x) was lost overnight, and the only way to
fix it was a conversation card on the dashboard. He asked for everything to happen in the chat: the
card says which account lost its session, he presses "Reconnect @demo_founder", the browser window
opens, he signs in himself, and the dot turns green. This module is the server half, and it is built
the way `card_send` and `card_actions` are: APP-ONLY tools (`_meta.ui.visibility: ["app"]`, listed
only for a Codex host, never for Claude Code, never for a hosted session, never in `catalog.list`).

FOUR TOOLS:

  * `room.connect_token`   the question before the open: checks the account and the browser, mints a
                           one-time confirmation;
  * `room.connect_account` the act, behind that confirmation: opens the agent's browser on the
                           platform's login page and answers with the viewer address, in
                           `_meta` only;
  * `room.connect_done`    "I have signed in": closes the window, reads the session once and
                           records it;
  * `room.account_status`  read only: for each assigned account, connected / session lost / never
                           connected, and whether the window is open.

THREAT MODEL, AS AN ADDITION TO `card_send.py` (1 to 5) AND `card_actions.py` (6 to 10); all of
those still hold:

 11. THE TICKET IS THE CAPABILITY. The address `room.connect_account` returns carries a one-time
     CONTROL ticket for the agent's browser: whoever opens it steers a browser that may hold the
     person's logged-in accounts. So it travels only in the result's `_meta` (the spec: "not
     intended for model context"), under `META_LOGIN_URL`; never in `content`, never in
     `structuredContent`, never in the session trace (`render_content` drops `_mcp_meta`, and the
     trace row of the result is built from the content). Checked: the address is built from the
     configured public origin and the ticket, never from a response, and a result whose origin is
     not the configured one is refused before it leaves.
 12. WHY A TOKEN, THOUGH THERE IS NOTHING TO SHOW THE PERSON. `card_actions` #8 decided that even
     the SAFE direction of a scope change needs the click, because a denial of service is still a
     harm; opening the login window is the same class: the capsule leaves its reading mode for the
     human one, and until the window is closed the publisher waits and the observer cannot read.
     `room.connect_account` therefore takes a token minted by `room.connect_token`, bound to the
     platform and the account, three minutes, once. It does NOT add a second click: the card asks
     and acts in the same handler of the one press on "Reconnect". What it buys is the invariant
     of the other tools: no single call that a prompt injection could name does anything.
 13. WHOSE TICKET. The gateway issues a CONTROL ticket to an identity, and the process that serves
     the card has no operator session and must not get one. As for a signature (`card_consent`),
     the ticket is filed under the organization's single operator (`PublicationStore
     .sole_operator_id`, D-016: two operators are a refusal, not a guess) and the audit row says it
     came through the plugin (`actor_ref` `plugin:<host>`, kind `CAPSULE_SESSION_OPENED`). This
     replaces the sentence of `accounts.connect` that no session may issue one: that tool, which the
     model calls, still never does; this one is behind the click.
 14. CLOSING THE WINDOW NEEDS NO TOKEN, and that is a decision: `room.connect_done` only returns the
     capsule to its normal mode (the one every other path wants it in), reads the session once and
     records what it saw. It opens nothing, signs nothing, and a call nobody meant costs a person
     their half-finished sign-in at worst. It does what the dashboard's "Gotowe" does
     (`/api/x/session/confirm`), in the same order and from the same primitives: close, revoke every
     stream, and ONLY THEN read, because the person's browser has no DevTools endpoint while they
     are at it (`args.HUMAN_ARGS`, 27.09).
 15. THE STATUS DOES NOT READ THE PLATFORM WHILE THE WINDOW IS OPEN, and could not: the same
     missing endpoint. It asks the capsule for its state (`human_steer_open`) and answers "waiting
     for you"; only when the window is closed does it read the session, at most once in
     `MIN_READ_GAP` seconds, whatever the polling rate. A card that asks every 4 seconds for three
     minutes makes no platform request while the person types their password.
 16. A DETECTED LOGIN IS NOT TAKEN FOR THE RIGHT ONE. A session that belongs to another account is
     `inne_konto`, never `polaczone`: it is the state that looks like success and means publishing
     as somebody else (`api/x_platform.py`). The same rule decides the footer's dots.
 17. RESIDUAL, as in `card_send`: an agent that ignores the host's filter, learns the names and
     calls the two steps in order can open the window, and a model with a shell can reach the
     gateway by other means. It cannot open it by accident, by a prompt injection that names one
     tool, or from a session the host does not run; it cannot read the address (`_meta`), and it
     cannot sign in: what the person types stays in the person's browser. NOT VERIFIED LIVE:
     that the capsule's environment in the plugin's tool server holds the gateway's issuer token
     (read from `docker-compose.yml`: the plugin server is `compose exec api`, which inherits
     it), and that a Codex host lets the card open a link without a user gesture.

THE FOOTER'S DOTS (`room.show` -> `konta_stan`): connected (the browser is signed in as this very
account), session lost (it was connected and the browser is not signed in), never connected, waiting
for you (the window is open), signed in as somebody else, held (paused or quarantined by a person's
decision: no button, a sign-in would not lift it), and unknown (the browser could not be asked, so
nothing is claimed). READING FIRST, THEN THE ROW (D-046): the row of `social_accounts` only says
what somebody once wrote, so a connected-looking row next to a lost session is exactly the case this
exists for. `room.show` reads the session when it has no reading of its own (bounded, a few seconds)
and reuses the ones the `x.*` tools made.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from community_os_contracts.session_verdict import (
    WRITER_PROOF_MAX_AGE_S,
    Observation,
    SessionVerdict,
    resolve,
)

from control_plane.audit import AuditWriter
from control_plane.observe.capsule_client import (
    CapsuleClient,
    CapsuleConfig,
    CapsuleUnavailable,
    GatewayClient,
    capsule_subject,
)
from control_plane.observe.capsule_session import (
    HUMAN_SESSION_REASON,
    LOGIN_PAGES,
    NOT_CONFIGURED_HERE,
    SessionReading,
)
from control_plane.pilot.publication import ConsentError, PublicationStore
from control_plane.workspace.account_state import note_capsule_login, writer_proof_age_s

from .card_send import CODE_CONFIRMATION, META_TOKEN, TOKEN_TTL, TokenRefused

#: The `_meta` key that carries the address of the login window back to the card, and nowhere else.
META_LOGIN_URL = "wenext/adres_logowania"

#: Platforms whose sign-in the agent's browser holds: the closed set of `/api/capsule/session`.
CONNECTABLE_PLATFORMS = ("X", "REDDIT")

#: Outstanding confirmations kept at once: one per platform is all a person has open.
MAX_OUTSTANDING = 4
#: The least time between two platform reads made for the status of one platform.
MIN_READ_GAP = 10.0
#: How long one live read may take, per caller. `room.show` is drawn at the end of the agent's
#: work and must not hang on a slow page; the status polls on a rhythm of seconds; the person who
#: pressed "I have signed in" is waiting for the answer and the browser has just restarted.
TIMEOUT_ROOM = 10.0
TIMEOUT_STATUS = 25.0
TIMEOUT_DONE = 90.0
TIMEOUT_SCREEN = 6.0

# What a footer dot can say (`stan` of an item of `konta_stan` and of `room.account_status`).
HEALTH_CONNECTED = "polaczone"
HEALTH_LOST = "utracona"
HEALTH_NEVER = "nigdy"
HEALTH_AWAITING = "czeka_na_ciebie"
HEALTH_OTHER = "inne_konto"
HEALTH_HELD = "wstrzymane"
HEALTH_UNKNOWN = "nieznana"
HEALTH_STATES = (
    HEALTH_CONNECTED,
    HEALTH_LOST,
    HEALTH_NEVER,
    HEALTH_AWAITING,
    HEALTH_OTHER,
    HEALTH_HELD,
    HEALTH_UNKNOWN,
)
#: The states a click on "Reconnect" can improve.
HEALTH_CONNECTABLE = (HEALTH_LOST, HEALTH_NEVER, HEALTH_OTHER)

#: Whether the agent's browser window for the person is open now.
SCREEN_OPEN = "otwarty"
SCREEN_CLOSED = "zamkniety"
SCREEN_NONE = "brak"

CODE_CAPSULE = "KAPSULA"
CODE_NO_ACCOUNT = "BRAK_KONTA"
CODE_OPERATOR = "OPERATOR"
CODE_ARGUMENT = "ARGUMENT"

# What `room.connect_done` says about the session it read: connected, or one of three codes, each
# with a sentence of the card's own (`ref.<code>`), like a refusal.
VERDICT_CONNECTED = "POLACZONE"
CODE_SIGN_IN = "ZALOGUJ"
CODE_OTHER_ACCOUNT = "INNE_KONTO"
CODE_UNREADABLE = "NIECZYTELNE"

#: Account states a sign-in does not lift: a person put them there.
_HELD_STATES = ("PAUSED", "QUARANTINED")
#: Account states of an account the browser never held.
_NEVER_STATES = ("PROVISIONED", "VERIFYING")


class ConnectRefused(Exception):
    """A refusal with a code for the card and a sentence for the person."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra


# ---------------------------------------------------------------------------
# What the browser said, and what the dot says about it: PURE
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One look at the agent's browser for a platform, whatever the platform is.

    `logged_in` is `True` (signed in as `handle`), `False` (the platform showed a login wall or
    refused for lack of credentials) or `None` (nobody could tell: the browser did not answer, a
    page did not load, the handle was not on it). `None` is never a lost login: sending the person
    to sign in because OUR read failed asks for work in answer to our own bug (`x_platform.py`).
    """

    logged_in: bool | None
    handle: str | None = None
    error: str | None = None
    at: float = 0.0


def reading_of_x(state: Any, at: float) -> Reading:
    """`XSessionState` as a `Reading`, by the ONE rule of `session_verdict`: the account seen is
    logged in, a login wall is logged out, and anything else (a timeout, a handle that did not
    show up) is unknown. The `x.*` tools, the publisher and the trends collector read the same
    state by the same function (`x_browser.state_verdict`)."""
    from community_observer.x_browser import state_verdict

    verdict = state_verdict(state)
    if verdict is SessionVerdict.LOGGED_IN:
        return Reading(True, state.handle, None, at)
    if verdict is SessionVerdict.LOGGED_OUT:
        return Reading(False, None, state.error, at)
    return Reading(None, None, state.error, at)


def reading_of_reddit(reading: SessionReading, at: float) -> Reading:
    """`SessionReading` as a `Reading`."""
    if reading.logged_in is True:
        return Reading(True, reading.username, None, at)
    if reading.logged_in is False:
        return Reading(False, None, reading.error, at)
    return Reading(None, None, reading.error, at)


def health_of(
    account_state: str, handle: str, reading: Reading | None, *, screen_open: bool = False
) -> tuple[str, str | None]:
    """The state of one account's dot and, when it helps, one sentence about it.

    The order is the whole rule. A window that is open wins (the person is doing it right now);
    then a person's decision to hold the account; then what the browser said; only then what the
    row remembers, and only to tell a session that was lost from one that never was.
    """
    if screen_open:
        return HEALTH_AWAITING, None
    if account_state in _HELD_STATES:
        return HEALTH_HELD, None
    if reading is not None and reading.logged_in:
        seen = str(reading.handle or "").lower().lstrip("@")
        if seen and seen == handle.lower().lstrip("@"):
            return HEALTH_CONNECTED, None
        return HEALTH_OTHER, reading.error
    never = account_state in _NEVER_STATES
    if reading is not None and reading.logged_in is False:
        return (HEALTH_NEVER if never else HEALTH_LOST), reading.error
    # The browser could not be asked: claim nothing the row does not already say.
    return (HEALTH_NEVER if never else HEALTH_UNKNOWN), (reading.error if reading else None)


def can_connect(platform: str, health: str) -> bool:
    return platform in CONNECTABLE_PLATFORMS and health in HEALTH_CONNECTABLE


# ---------------------------------------------------------------------------
# The one-time confirmation of an open: a copy of the shape of `card_send.SendTokens`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectGrant:
    """What one confirmation was minted for. Nothing in it is chosen by the caller of the act."""

    platform: str
    account_id: UUID
    handle: str
    expires_at: datetime


class ConnectTokens:
    """One-time confirmations for opening the login window, in memory, hashed.

    In memory and hashed for the reasons `card_send.SendTokens` gives. One live confirmation per
    platform: asking again replaces the earlier one. Any presentation of a known token burns it.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = TOKEN_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._grants: dict[str, ConnectGrant] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, grant in self._grants.items() if grant.expires_at <= now]:
            del self._grants[key]

    def mint(self, *, platform: str, account_id: UUID, handle: str) -> tuple[str, datetime]:
        self._prune()
        for key in [k for k, g in self._grants.items() if g.platform == platform]:
            del self._grants[key]
        while len(self._grants) >= MAX_OUTSTANDING:
            del self._grants[next(iter(self._grants))]
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        self._grants[self._key(token)] = ConnectGrant(
            platform=platform, account_id=account_id, handle=handle, expires_at=expires_at
        )
        return token, expires_at

    def redeem(self, token: str, *, platform: str) -> ConnectGrant:
        grant = self._grants.pop(self._key(token), None)
        if grant is None:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "Brak ważnego potwierdzenia kliknięciem: nieznane albo już zużyte. Okno logowania "
                "otwiera człowiek w karcie, agent tego nie robi.",
            )
        if grant.expires_at <= self._clock():
            raise TokenRefused(CODE_CONFIRMATION, "Potwierdzenie wygasło. Spróbuj ponownie.")
        if grant.platform != platform:
            raise TokenRefused(
                CODE_CONFIRMATION,
                "To potwierdzenie dotyczy innej platformy. Spróbuj ponownie.",
            )
        return grant


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

_PLATFORM_SCHEMA = {
    "type": "string",
    "enum": list(CONNECTABLE_PLATFORMS),
    "description": "Platforma konta: X albo REDDIT.",
}

CARD_CONNECT_TOOL_SPECS = (
    {
        "name": "room.connect_token",
        "summary": "TYLKO DLA KARTY: potwierdzenie otwarcia okna logowania, wydane po kliknięciu.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Sprawdza, że projekt ma konto tej platformy, że przeglądarka agenta jest "
            "skonfigurowana w tym procesie i że organizacja ma jednego operatora, i wydaje "
            "potwierdzenie: jednorazowe, trzy minuty, związane z platformą i kontem. Potwierdzenie "
            "jedzie tylko w `_meta` wyniku. Niczego nie otwiera; nie ma `writes`, żeby powtórne "
            "wywołanie wydało nowe potwierdzenie, a nie wynik poprzedniego."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {"platforma": _PLATFORM_SCHEMA},
            "required": ["platforma"],
            "additionalProperties": False,
        },
        "tags": ("karta", "konta"),
    },
    {
        "name": "room.connect_account",
        "summary": "TYLKO DLA KARTY: otwórz okno logowania agenta, po kliknięciu człowieka.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Wymaga potwierdzenia z room.connect_token, jednorazowego i związanego z platformą. "
            "Przełącza przeglądarkę agenta w tryb dla człowieka na stronie logowania tej platformy "
            "i wydaje jednorazowy bilet do jej ekranu, tą samą drogą co dashboard "
            "(`/api/capsule/session`), na tożsamość jedynego operatora organizacji. Adres okna "
            "jedzie tylko w `_meta` wyniku. Człowiek loguje się SAM; agent nie widzi ani nie "
            "wpisuje danych logowania. Gdy okno już jest otwarte, wydaje tylko nowy bilet."
        ),
        "state": "DOSTEPNE",
        "writes": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "platforma": _PLATFORM_SCHEMA,
                "token": {"type": "string", "description": "Potwierdzenie z room.connect_token."},
            },
            "required": ["platforma", "token"],
            "additionalProperties": False,
        },
        "tags": ("karta", "konta", "działanie"),
    },
    {
        "name": "room.connect_done",
        "summary": "TYLKO DLA KARTY: człowiek się zalogował; zamknij okno i sprawdź sesję.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Robi to, co „Gotowe, sprawdź połączenie” w dashboardzie: zamyka okno człowieka "
            "(przeglądarka wraca do trybu odczytu), odwołuje strumienie ekranu i DOPIERO WTEDY "
            "czyta sesję jeden raz, bo w oknie człowieka nie ma jak jej odczytać. Gdy to konto "
            "projektu, zapisuje je jako połączone; sesję INNEGO konta zgłasza i niczego nie "
            "zapisuje. Nie otwiera niczego, więc nie wymaga potwierdzenia; powtórzone jest "
            "nieszkodliwe."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {"platforma": _PLATFORM_SCHEMA},
            "required": ["platforma"],
            "additionalProperties": False,
        },
        "tags": ("karta", "konta"),
    },
    {
        "name": "room.account_status",
        "summary": "TYLKO DLA KARTY: czy konta projektu mają sesję w przeglądarce agenta.",
        "detail": (
            "Narzędzie dla karty Pokoju w agencie, niewidoczne dla modelu (visibility: app). "
            "Tylko czyta. Dla każdego przypisanego konta X albo Reddita mówi: połączone, sesja "
            "utracona, nigdy niepołączone, czeka na człowieka (okno logowania jest otwarte), "
            "zalogowane inne konto, wstrzymane albo nieznane. Gdy okno jest otwarte, nie pyta "
            "platformy wcale (w oknie człowieka nie ma jak); gdy jest zamknięte, czyta sesję "
            "najwyżej raz na kilka sekund, więc można pytać co cztery."
        ),
        "state": "DOSTEPNE",
        "input_schema": {
            "type": "object",
            "properties": {"platforma": _PLATFORM_SCHEMA},
            "additionalProperties": False,
        },
        "tags": ("karta", "konta"),
    },
)


class CardConnectTools:
    """Mixin of `ToolRegistry`: the four app-only tools behind the card's Reconnect."""

    # -- state held per tool server ------------------------------------------------------------

    @property
    def _connect_tokens(self) -> ConnectTokens:
        store = self.__dict__.get("_connect_tokens_value")
        if store is None:
            store = self.__dict__["_connect_tokens_value"] = ConnectTokens()
        return store  # type: ignore[no-any-return]

    @_connect_tokens.setter
    def _connect_tokens(self, value: ConnectTokens) -> None:
        self.__dict__["_connect_tokens_value"] = value

    @property
    def _health_reads(self) -> dict[str, Reading]:
        """The last live reading of each platform made for a status, to rate-limit the reads."""
        return self.__dict__.setdefault("_health_reads_value", {})  # type: ignore[no-any-return]

    def _health_lock(self, platform: str) -> asyncio.Lock:
        locks: dict[str, asyncio.Lock] = self.__dict__.setdefault("_health_locks_value", {})
        if platform not in locks:
            locks[platform] = asyncio.Lock()
        return locks[platform]

    # -- plumbing -----------------------------------------------------------------------------

    def _host_name(self) -> str:
        return str(self.capability.host)  # type: ignore[attr-defined]

    def _gone(self, reason: str, code: str, **extra: Any) -> dict[str, Any]:
        from .registry import unavailable

        return unavailable(reason, kod=code, **extra)

    @staticmethod
    def _platform_of(args: dict[str, Any], *, required: bool) -> str | None:
        from .registry import ToolError

        value = str(args.get("platforma") or "").strip().upper()
        if not value and not required:
            return None
        if value not in CONNECTABLE_PLATFORMS:
            raise ToolError("platforma to X albo REDDIT")
        return value

    async def _assigned_for_connect(self) -> list[dict[str, Any]]:
        """The accounts assigned to this project, X and Reddit only, as the server reads them."""
        capability = self.capability  # type: ignore[attr-defined]
        async with self.pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(
                """
                SELECT a.id, a.platform, a.external_account_id, a.state
                  FROM project_account_assignments x
                  JOIN social_accounts a
                    ON a.organization_id = x.organization_id AND a.id = x.social_account_id
                 WHERE x.organization_id = $1 AND x.workspace_id = $2 AND x.project_id = $3
                   AND x.unassigned_at IS NULL AND a.platform = ANY($4::text[])
                 ORDER BY a.platform, x.assigned_at, a.external_account_id
                """,
                capability.organization_id,
                capability.workspace_id,
                capability.project_id,
                list(CONNECTABLE_PLATFORMS),
            )
        return [
            {
                "id": row["id"],
                "platforma": str(row["platform"]),
                "konto": str(row["external_account_id"]),
                "stan_konta": str(row["state"]),
            }
            for row in rows
        ]

    def _config_for(self, platform: str, *, need_gateway: bool) -> CapsuleConfig:
        config = CapsuleConfig.for_platform(os.environ, platform)
        if config is None:
            raise ConnectRefused(CODE_CAPSULE, NOT_CONFIGURED_HERE)
        if need_gateway and not config.gateway_issuer_token:
            raise ConnectRefused(
                CODE_CAPSULE,
                "Przeglądarka agenta nie ma w tym procesie tokenu wystawcy biletów "
                "(WENEXT_GATEWAY_ISSUER_TOKEN), więc nie otworzę okna logowania.",
            )
        return config

    @staticmethod
    def _clients(config: CapsuleConfig) -> tuple[CapsuleClient, GatewayClient]:
        try:
            return (
                CapsuleClient(config.reader_url, token=config.reader_token),
                GatewayClient(config.gateway_url, issuer_token=config.gateway_issuer_token),
            )
        except CapsuleUnavailable as exc:
            raise ConnectRefused(CODE_CAPSULE, str(exc)) from exc

    async def _screen_of(self, platform: str) -> str:
        """Is the human window of this platform's browser open? Never raises; one cheap call."""
        config = CapsuleConfig.for_platform(os.environ, platform)
        if config is None:
            return SCREEN_NONE
        try:
            capsule = CapsuleClient(config.reader_url, token=config.reader_token)
        except CapsuleUnavailable:
            return SCREEN_NONE
        try:
            state = await asyncio.wait_for(capsule.state(), TIMEOUT_SCREEN)
        except (CapsuleUnavailable, TimeoutError):
            return SCREEN_NONE
        finally:
            await capsule.aclose()
        return SCREEN_OPEN if state.human_steer_open else SCREEN_CLOSED

    # -- reading the browser ------------------------------------------------------------------

    def _cached_reading(self, platform: str) -> Reading | None:
        """The reading the `x.*` or `accounts.*` tools made in this process, if still good."""
        now = time.monotonic()
        if platform == "X":
            state = self._x_identity_cache  # type: ignore[attr-defined]
            if state is None or not self._x_identity_usable():  # type: ignore[attr-defined]
                return None
            return reading_of_x(state, self._x_identity_at or now)  # type: ignore[attr-defined]
        reading = self._capsule_reading  # type: ignore[attr-defined]
        if reading is None:
            return None
        at = self._capsule_reading_at  # type: ignore[attr-defined]
        age = now - at if at else 0.0
        if age >= (600.0 if reading.logged_in else 20.0):
            return None
        return reading_of_reddit(reading, at or now)

    async def _read_live(self, platform: str, handle: str, *, timeout: float) -> Reading:
        """One read of the agent's browser for `platform`. Never raises: a failure is a reading
        that says it could not tell. Remembers the answer where the `x.*` tools look for it."""
        from control_plane.observe.capsule_session import (
            probe_from_env,
            read_session,
            x_probe_from_env,
        )

        started = time.monotonic()
        try:
            if platform == "X":
                source = x_probe_from_env()
                if source is None:
                    return Reading(None, None, NOT_CONFIGURED_HERE, started)
                # THE RETRY INSIDE THE SOURCE IS FOR THE CALLER WHO CAN AFFORD IT. A card that is
                # polled every few seconds has `timeout` seconds in all and `asyncio.wait_for`
                # would cancel a second look mid-flight (leaving the capsule to finish a read
                # nobody waits for); the next poll IS its second look. The person who pressed "I
                # have signed in" (`TIMEOUT_DONE`) is waiting for the answer, and gets the retry.
                retries = 1 if timeout >= TIMEOUT_DONE else 0
                try:
                    state = await asyncio.wait_for(
                        source.verify_identity(handle, retries=retries), timeout
                    )
                finally:
                    await source.aclose()
                self._x_identity_noted(state)  # type: ignore[attr-defined]
                return reading_of_x(state, time.monotonic())
            probe = probe_from_env()
            if probe is None:
                return Reading(None, None, NOT_CONFIGURED_HERE, started)
            try:
                seen = await asyncio.wait_for(read_session(probe), timeout)
            finally:
                close = getattr(probe, "aclose", None)
                if close is not None:
                    await close()
            self._capsule_reading = seen  # type: ignore[attr-defined]
            self._capsule_reading_at = time.monotonic()  # type: ignore[attr-defined]
            return reading_of_reddit(seen, time.monotonic())
        except TimeoutError:
            return Reading(None, None, "przeglądarka agenta nie odpowiedziała na czas", started)
        except Exception as exc:
            why = f"nie udało się sprawdzić sesji ({type(exc).__name__})"
            return Reading(None, None, why, started)

    async def _vouched_by_writer(self, platform: str, handle: str, reading: Reading) -> Reading:
        """A reader that could not tell does not contradict a writer that did (12 and 13.10.2026).

        The card showed X as `nieznana` ("the browser did not answer in time") in the very minutes
        in which the actuator published replies from the account. An actuator refuses to write
        without the account on the page, so a reply it published a few minutes ago is a definitive
        "logged in"; the reader's UNKNOWN carries no information. `session_verdict.resolve` picks
        the youngest definitive observation, and a definitive reading of the reader (a wall, the
        account seen) is always younger than the writer's proof and wins. Only UNKNOWN is replaced,
        and never on `room.connect_done`, which reads the session afresh on purpose.
        """
        if reading.logged_in is not None:
            return reading
        age = await writer_proof_age_s(
            self.pool,  # type: ignore[attr-defined]
            organization_id=self.capability.organization_id,  # type: ignore[attr-defined]
            platform=platform,
            handle=handle,
        )
        if age is None:
            return reading
        chosen = resolve(
            [
                Observation(SessionVerdict.UNKNOWN, 0.0, source="reader"),
                Observation(SessionVerdict.LOGGED_IN, age, handle, "writer"),
            ],
            max_age_s=WRITER_PROOF_MAX_AGE_S,
        )
        if chosen is None or chosen.verdict is not SessionVerdict.LOGGED_IN:
            return reading
        return Reading(True, handle, None, reading.at)

    # -- the health of the accounts -----------------------------------------------------------

    async def _health_of_accounts(
        self,
        accounts: list[dict[str, Any]],
        *,
        platforms: tuple[str, ...],
        mode: str,
    ) -> list[dict[str, Any]]:
        """The footer's items for `accounts` of `platforms`.

        `mode` decides how the browser is asked: `room` (what a reading in this process says, else
        one bounded live read), `status` (the window first: open means no platform request at all;
        else a read at most once in `MIN_READ_GAP`).
        """
        wanted = [a for a in accounts if a["platforma"] in platforms]
        by_platform: dict[str, list[dict[str, Any]]] = {}
        for account in wanted:
            by_platform.setdefault(account["platforma"], []).append(account)

        def recent(platform: str) -> Reading | None:
            last = self._health_reads.get(platform)
            if last is not None and time.monotonic() - last.at < MIN_READ_GAP:
                return last
            return None

        async def one(platform: str) -> tuple[str, str, Reading | None]:
            screen = await self._screen_of(platform)
            if screen == SCREEN_OPEN:
                return platform, screen, None
            reading = self._cached_reading(platform) if mode == "room" else None
            reading = reading or recent(platform)
            if reading is None:
                async with self._health_lock(platform):
                    # Another poll may have read while this one waited for the lock.
                    reading = recent(platform)
                    if reading is None:
                        reading = await self._read_live(
                            platform,
                            by_platform[platform][0]["konto"],
                            timeout=TIMEOUT_ROOM if mode == "room" else TIMEOUT_STATUS,
                        )
                        self._health_reads[platform] = reading
            reading = await self._vouched_by_writer(
                platform, by_platform[platform][0]["konto"], reading
            )
            return platform, screen, reading

        results = await asyncio.gather(*(one(p) for p in by_platform))
        seen = {platform: (screen, reading) for platform, screen, reading in results}
        items = []
        for account in wanted:
            screen, reading = seen[account["platforma"]]
            health, why = health_of(
                account["stan_konta"],
                account["konto"],
                reading,
                screen_open=screen == SCREEN_OPEN,
            )
            items.append(
                {
                    "id": str(account["id"]),
                    "platforma": account["platforma"],
                    "konto": account["konto"],
                    "stan": health,
                    "ekran": screen,
                    "powod": why,
                    "mozna_polaczyc": can_connect(account["platforma"], health)
                    and screen != SCREEN_NONE,
                }
            )
        return items

    async def _room_health(self) -> list[dict[str, Any]]:
        """`konta_stan` of `room.show`. Never raises: a card without its footer is worse than a
        card whose footer says it could not ask."""
        try:
            accounts = await self._assigned_for_connect()
            if not accounts:
                return []
            return await self._health_of_accounts(
                accounts, platforms=CONNECTABLE_PLATFORMS, mode="room"
            )
        except Exception:
            return []

    # -- room.connect_token -------------------------------------------------------------------

    async def _connect_target(self, platform: str) -> dict[str, Any]:
        """The account a click on Reconnect is about, or the refusal."""
        accounts = [a for a in await self._assigned_for_connect() if a["platforma"] == platform]
        if not accounts:
            raise ConnectRefused(
                CODE_NO_ACCOUNT,
                f"Ten projekt nie ma przypisanego konta {platform}, więc nie ma czego łączyć. "
                "Konto przypisuje się w Profilach i kontach.",
            )
        open_ones = [a for a in accounts if a["stan_konta"] not in _HELD_STATES]
        if not open_ones:
            raise ConnectRefused(
                CODE_NO_ACCOUNT,
                "To konto jest wstrzymane decyzją człowieka, a logowanie w przeglądarce tego nie "
                "zmienia.",
            )
        return open_ones[0]

    async def _sole_operator(self) -> UUID:
        capability = self.capability  # type: ignore[attr-defined]
        try:
            return await PublicationStore(self.pool).sole_operator_id(  # type: ignore[attr-defined]
                organization_id=capability.organization_id
            )
        except ConsentError as exc:
            raise ConnectRefused(
                CODE_OPERATOR,
                f"{exc}. Okno logowania otworzysz w dashboardzie.",
            ) from exc

    async def _room_connect_token(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        platform = self._platform_of(args, required=True)
        assert platform is not None
        try:
            target = await self._connect_target(platform)
            self._config_for(platform, need_gateway=True)
            await self._sole_operator()
        except ConnectRefused as exc:
            return self._gone(exc.detail, exc.code, **exc.extra)
        token, expires_at = self._connect_tokens.mint(
            platform=platform, account_id=target["id"], handle=target["konto"]
        )
        return {
            **done(
                platforma=platform,
                konto=target["konto"],
                ekran=await self._screen_of(platform),
                wygasa_potwierdzenie=expires_at.isoformat(),
            ),
            # ONLY the card reads this, and nothing that is written to a trace or shown as text.
            "_mcp_meta": {META_TOKEN: token},
        }

    # -- room.connect_account -----------------------------------------------------------------

    async def _room_connect_account(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        platform = self._platform_of(args, required=True)
        assert platform is not None
        token = str(args.get("token") or "")
        if not token:
            return self._gone(
                "Brak potwierdzenia kliknięciem. Okno logowania otwiera człowiek w karcie, agent "
                "tego nie robi.",
                CODE_CONFIRMATION,
            )
        # THE TOKEN IS CONSUMED FIRST: whatever else happens, this confirmation is spent.
        try:
            grant = self._connect_tokens.redeem(token, platform=platform)
        except TokenRefused as exc:
            return self._gone(exc.detail, exc.code)
        try:
            target = await self._connect_target(platform)
            if target["id"] != grant.account_id:
                return self._gone(
                    "Konto tego projektu zmieniło się od chwili, gdy kliknięto. Spróbuj ponownie.",
                    CODE_CONFIRMATION,
                )
            config = self._config_for(platform, need_gateway=True)
            operator_id = await self._sole_operator()
            capsule, gateway = self._clients(config)
            try:
                state = await capsule.state()
                if not state.human_steer_open:
                    # Order matters (`capsule_session.open_session`): the screen first, because a
                    # ticket for a screen that never came up is a dead link.
                    await capsule.open_human_session(
                        HUMAN_SESSION_REASON, start_url=LOGIN_PAGES.get(platform)
                    )
                ticket = await gateway.issue_control_ticket(user_id=operator_id)
            except CapsuleUnavailable as exc:
                raise ConnectRefused(CODE_CAPSULE, str(exc)) from exc
            finally:
                await capsule.aclose()
                await gateway.aclose()
            href = ticket.viewer_url(config.stream_public_origin)
            if not _same_origin(href, config.stream_public_origin):
                raise ConnectRefused(
                    CODE_CAPSULE,
                    "Adres okna logowania nie należy do skonfigurowanego ekranu przeglądarki "
                    "agenta, więc go nie podaję.",
                )
        except ConnectRefused as exc:
            return self._gone(exc.detail, exc.code, **exc.extra)
        capability = self.capability  # type: ignore[attr-defined]
        pool = self.pool  # type: ignore[attr-defined]
        async with pool.acquire() as connection, connection.transaction():
            await AuditWriter(connection).record(
                kind="CAPSULE_SESSION_OPENED",
                organization_id=capability.organization_id,
                subject_ref=capsule_subject(os.environ, platform),
                actor_ref=f"plugin:{self._host_name()}",
                payload={"reason_code": HUMAN_SESSION_REASON, "platform": platform},
            )
        # The window is open: what the browser said before is no longer what it says.
        self._health_reads.pop(platform, None)
        return {
            **done(
                platforma=platform,
                konto=grant.handle,
                ekran=SCREEN_OPEN,
                bilet_wygasa=ticket.expires_at,
                uwaga=(
                    "Okno logowania jest otwarte. Człowiek loguje się sam; potem prosi o "
                    "sprawdzenie (room.connect_done)."
                ),
            ),
            "_mcp_meta": {META_LOGIN_URL: href},
        }

    # -- room.connect_done --------------------------------------------------------------------

    async def _resume_parked_jobs(self) -> int:
        """Reddit only, as in `/api/capsule/session/confirm`: a missing login parked jobs."""
        from control_plane.api.waiting_jobs import resume_human_unblocked

        capability = self.capability  # type: ignore[attr-defined]
        try:
            operator = SimpleNamespace(
                organization_id=capability.organization_id,
                operator_id=await self._sole_operator(),
            )
            pool = self.pool  # type: ignore[attr-defined]
            resumed = await resume_human_unblocked(pool, operator)  # type: ignore[arg-type]
            return int(resumed["resumed"])
        except Exception:
            return 0

    async def _room_connect_done(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        platform = self._platform_of(args, required=True)
        assert platform is not None
        capability = self.capability  # type: ignore[attr-defined]
        try:
            accounts = [a for a in await self._assigned_for_connect() if a["platforma"] == platform]
            if not accounts:
                raise ConnectRefused(
                    CODE_NO_ACCOUNT, f"Ten projekt nie ma przypisanego konta {platform}."
                )
            config = self._config_for(platform, need_gateway=True)
            capsule, gateway = self._clients(config)
            closed = False
            try:
                state = await capsule.state()
                if state.human_steer_open:
                    # THE WINDOW CLOSES FIRST (27.09): the person's browser has no DevTools
                    # endpoint, so the session cannot be read while they are at it. Then every
                    # stream is cut, and only then is the session read, which also proves that it
                    # survived the restart of the browser on the same profile.
                    await capsule.close_human_session()
                    await gateway.revoke_sessions()
                    closed = True
            except CapsuleUnavailable as exc:
                raise ConnectRefused(CODE_CAPSULE, str(exc)) from exc
            finally:
                await capsule.aclose()
                await gateway.aclose()
        except ConnectRefused as exc:
            return self._gone(exc.detail, exc.code, **exc.extra)

        async with self._health_lock(platform):
            reading = await self._read_live(platform, accounts[0]["konto"], timeout=TIMEOUT_DONE)
            self._health_reads[platform] = reading

        verdict, handle, detail = _verdict_of(platform, reading, accounts, closed=closed)
        saved = False
        if verdict == VERDICT_CONNECTED and handle:
            noted = await note_capsule_login(
                self.pool,  # type: ignore[attr-defined]
                organization_id=capability.organization_id,
                platform=platform,
                logged_in=True,
                username=handle,
                actor_ref=f"plugin:{self._host_name()}",
            )
            saved = noted.changed or noted.reason is None
            if platform == "REDDIT":
                await self._resume_parked_jobs()
        pool = self.pool  # type: ignore[attr-defined]
        async with pool.acquire() as connection, connection.transaction():
            await AuditWriter(connection).record(
                kind="CAPSULE_SESSION_CONFIRMED",
                organization_id=capability.organization_id,
                subject_ref=capsule_subject(os.environ, platform),
                actor_ref=f"plugin:{self._host_name()}",
                payload={
                    "platform": platform,
                    "verdict": "SIGNED_IN" if verdict == VERDICT_CONNECTED else "SIGNED_OUT",
                },
            )
        return done(
            platforma=platform,
            polaczone=verdict == VERDICT_CONNECTED,
            kod_odczytu=verdict,
            konto=handle if verdict == VERDICT_CONNECTED else accounts[0]["konto"],
            zalogowano_jako=reading.handle,
            okno_zamkniete=closed,
            konto_gotowe=saved,
            detail=detail,
        )

    # -- room.account_status ------------------------------------------------------------------

    async def _room_account_status(self, args: dict[str, Any]) -> dict[str, Any]:
        from .registry import done

        platform = self._platform_of(args, required=False)
        accounts = await self._assigned_for_connect()
        platforms = (platform,) if platform else CONNECTABLE_PLATFORMS
        items = await self._health_of_accounts(accounts, platforms=platforms, mode="status")
        return done(
            konta=items,
            liczba=len(items),
            odczytano=datetime.now(UTC).isoformat(),
        )


def _same_origin(href: str, origin: str) -> bool:
    """Is `href` an http(s) address on exactly `origin`'s host and port, with no credentials?"""
    try:
        address, base = urlsplit(href), urlsplit(origin)
    except ValueError:
        return False
    if address.scheme not in ("http", "https") or address.username or address.password:
        return False
    return address.scheme == base.scheme and address.netloc == base.netloc


def _verdict_of(
    platform: str, reading: Reading, accounts: list[dict[str, Any]], *, closed: bool
) -> tuple[str, str | None, str]:
    """What `room.connect_done` tells the person about the session it just read. PURE.

    Four outcomes and none may pass for another: the project's account is signed in; somebody
    else's is; nobody is (the person must sign in, and if the window is closed already, open it
    again); or the read failed, which is OUR problem, never the person's.
    """
    shown = "X" if platform == "X" else "Reddit"
    names = {a["konto"].lower().lstrip("@"): a["konto"] for a in accounts}
    if reading.logged_in:
        key = str(reading.handle or "").lower().lstrip("@")
        if key in names:
            return VERDICT_CONNECTED, names[key], f"Połączono konto {shown} {names[key]}."
        return (
            CODE_OTHER_ACCOUNT,
            reading.handle,
            f"Przeglądarka agenta jest zalogowana jako {reading.handle}, a ten projekt działa "
            f"kontem {accounts[0]['konto']}. Wyloguj się w oknie i zaloguj właściwe konto.",
        )
    if reading.logged_in is False:
        return (
            CODE_SIGN_IN,
            None,
            f"Nie widzę jeszcze zalogowanej sesji {shown}. "
            + (
                "Okno logowania jest już zamknięte: otwórz je ponownie i dokończ logowanie."
                if closed
                else "Otwórz okno logowania i zaloguj się."
            ),
        )
    return (
        CODE_UNREADABLE,
        None,
        reading.error
        or f"Nie udało się teraz odczytać sesji {shown}. Spróbuj sprawdzić jeszcze raz za chwilę.",
    )


__all__ = [
    "CARD_CONNECT_TOOL_SPECS",
    "CONNECTABLE_PLATFORMS",
    "HEALTH_STATES",
    "META_LOGIN_URL",
    "CardConnectTools",
    "ConnectGrant",
    "ConnectRefused",
    "ConnectTokens",
    "Reading",
    "can_connect",
    "health_of",
    "reading_of_reddit",
    "reading_of_x",
]
