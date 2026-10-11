"""The "agent's browser" card — what the operator sees and the two things they can click.

`docs/OUTCOMES.md` §8 „poziom 2", contract agreed with the dashboard (wenext-68):

* `card_state()` — SIGNED_OUT / AWAITING_HUMAN / SIGNED_IN plus the capsule's mode;
* `open_session()` — the capsule goes INTERACTIVE with a human reason, the gateway mints a
  one-time CONTROL ticket, the dashboard opens the viewer URL in a new tab;
* `confirm_session()` — the human says "done": measure the Reddit session while the screen is
  still up, return the capsule to display-only (D-026: headful without a steer, because Reddit
  refuses headless), measure again, and resume the jobs a missing login had parked.

Pure orchestration over injected ports so it is testable without Postgres, the capsule or the
gateway; the route module (`api/capsule.py`) supplies the real ones and the audit writer.

Nothing here reads a platform except through the Reddit adapter's `session_state()` — the same
call the observer makes — and nothing here writes to a platform at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from community_observer import CredentialsMissing, SourceUnavailable

from .capsule_client import CapsuleClient, CapsuleState, CapsuleUnavailable, GatewayClient

#: The card's three pills. A fourth value is a failed read on the dashboard side, by contract.
SIGNED_OUT = "SIGNED_OUT"
AWAITING_HUMAN = "AWAITING_HUMAN"
SIGNED_IN = "SIGNED_IN"

#: Powód, gdy sondy NIE MA. Mówi „w tym procesie" celowo: ten sam produkt potrafi jednocześnie
#: czytać Reddita z procesu api i nie mieć czym zapytać z procesu serwera narzędzi, bo to dwa
#: procesy z dwiema listami zmiennych. Zdanie bez tych trzech słów wysłało 20.09 dwie osoby
#: szukać awarii kapsuły, która działała.
NOT_CONFIGURED_HERE = "kapsuła nie jest skonfigurowana w tym procesie, więc nie ma czym sprawdzić"

#: The reason the capsule gets when a human opens the screen from the card. `REAUTH` would be
#: more precise once a session exists; the closed list allows both and the difference is not
#: measurable from here, so one word, always.
HUMAN_SESSION_REASON = "FIRST_LOGIN"


class SessionProbe(Protocol):
    """`RedditBrowserSource.session_state()` — who the capsule's Chrome is logged in as."""

    async def session_state(self) -> Any: ...


@dataclass(frozen=True)
class SessionReading:
    """One look at the Reddit session: logged in or not, or why the look failed."""

    logged_in: bool | None
    username: str | None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"logged_in": self.logged_in, "username": self.username, "error": self.error}


@dataclass
class CardMemory:
    """What the card remembers between requests: the last session reading. Process-local, like the
    operator session store — one operator, one process (D-016)."""

    last_session: SessionReading | None = None
    last_state: CapsuleState | None = None
    events: list[str] = field(default_factory=list)


def probe_from_env(env: Any | None = None, *, timeout: Any | None = None) -> SessionProbe | None:
    """Adapter Reddita przez kapsułę, albo `None`, gdy kapsuła nie jest skonfigurowana.

    JEDNO MIEJSCE, w którym ta sonda powstaje, bo mają jej używać trzy strony: karta kapsuły,
    potwierdzenie sesji i narzędzie `accounts.status`. Trzy kopie tej samej konstrukcji
    rozjechałyby się na pierwszej zmianie nazwy zmiennej środowiskowej, a rozjazd widać dopiero
    wtedy, gdy jedna ze stron zaczyna mówić „nie skonfigurowano" przy działającej kapsule.
    """
    import os

    import httpx
    from community_observer import HttpBrowserReader, RedditBrowserSource
    from community_observer.reddit_browser import require_login_from_env

    from control_plane.observe.capsule_client import CapsuleConfig

    environment = os.environ if env is None else env
    config = CapsuleConfig.from_env(environment)
    if config is None:
        return None
    reader = HttpBrowserReader(
        config.reader_url,
        token=config.reader_token,
        client=httpx.AsyncClient(timeout=timeout or httpx.Timeout(25.0, connect=5.0)),
    )
    return RedditBrowserSource(reader, require_login=require_login_from_env(environment))


def x_probe_from_env(env: Any | None = None, *, timeout: Any | None = None) -> Any | None:
    """The X source through the capsule, or `None` when the capsule is not configured.

    A twin of `probe_from_env` for the same reason: several places use it (the "Połącz X" card,
    the `x.*` tools, the identity check before a publication), and three copies of this
    construction would drift apart on the first renamed environment variable.

    The X account's own capsule when `WENEXT_X_BROWSER_READER_URL` is set (D-051): Chromium,
    because Google refuses a person's sign-in in Chrome for Testing, with its own profile. Without
    it, the Reddit capsule and its profile, as before 27.09.
    """
    import os

    import httpx
    from community_observer.x_browser import XBrowserSource

    from control_plane.observe.capsule_client import CapsuleConfig

    environment = os.environ if env is None else env
    config = CapsuleConfig.for_platform(environment, "X")
    if config is None:
        return None
    return XBrowserSource(
        config.reader_url,
        token=config.reader_token,
        # Rendering X takes longer than fetching Reddit's JSON: the page loads its content after
        # the navigation, and the capsule paces X page loads on top of that.
        client=httpx.AsyncClient(timeout=timeout or httpx.Timeout(120.0, connect=10.0)),
    )


#: Zmienne, bez których proces POTOMNY nie ma czym zapytać kapsuły. Dokładnie te, których używa
#: `probe_from_env`: adres czytnika, jego token i flaga wymagania logowania. Bramy nie ma na tej
#: liście świadomie, bo bilet CONTROL wystawia się tożsamości człowieka, a nie sesji agenta.
CAPSULE_ENV_KEYS = (
    "WENEXT_BROWSER_READER_URL",
    "WENEXT_BROWSER_READER_TOKEN",
    "WENEXT_REDDIT_REQUIRE_LOGIN",
    # D-051: without it the `x.*` tools in the child process would read X through Reddit's
    # capsule, where nobody signed in to X, and report a lost login that never happened.
    "WENEXT_X_BROWSER_READER_URL",
)


def capsule_env(env: Any | None = None) -> dict[str, str]:
    """Te ze zmiennych kapsuły, które są USTAWIONE, do przekazania procesowi potomnemu.

    PO CO TO ISTNIEJE, zmierzone na live 20.09 (sesja 01ef065e). Narzędzia modelu biegną w
    OSOBNYM procesie: app-server startuje serwer MCP `wenext` z listą zmiennych, którą sami mu
    podajemy. Dostawał tylko ścieżkę pliku zdolności, więc `probe_from_env()` zwracało tam
    `None`, `accounts.status` nie miało czego zapytać i karta mówiła „brak połączenia" o koncie,
    na którym kapsuła w tej samej chwili czytała Reddita. Proces rodzic widział prawdę, proces
    dziecko nie, i nic tej różnicy nie nazywało.

    Zmienna nieustawiona NIE JEDZIE jako pusty napis: pusty adres czytnika znaczy w
    `CapsuleConfig.from_env` to samo co brak, ale pusty token znaczyłby „token jest, tylko
    pusty", a to jest 401 zamiast czytelnego „nieskonfigurowane".
    """
    import os

    source = os.environ if env is None else env
    return {key: value for key in CAPSULE_ENV_KEYS if (value := (source.get(key) or "").strip())}


async def read_session(probe: SessionProbe | None) -> SessionReading:
    """Never raises: the card must render whatever the platform did."""
    if probe is None:
        return SessionReading(None, None, NOT_CONFIGURED_HERE)
    try:
        state = await probe.session_state()
    except CredentialsMissing as exc:
        return SessionReading(False, None, str(exc)[:300])
    except SourceUnavailable as exc:
        return SessionReading(None, None, str(exc)[:300])
    return SessionReading(bool(state.logged_in), state.username, None)


def card_status(state: CapsuleState | None, session: SessionReading | None) -> str:
    """The pill. INTERACTIVE with an open human steer means "waiting for you"; otherwise the last
    known session decides; nothing known yet is SIGNED_OUT — the safe default, it prompts a login.
    """
    if state is not None and state.mode == "INTERACTIVE" and state.human_steer_open:
        return AWAITING_HUMAN
    if session is not None and session.logged_in:
        return SIGNED_IN
    return SIGNED_OUT


def card_json(
    *,
    configured: bool,
    state: CapsuleState | None,
    session: SessionReading | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "configured": configured,
        "status": card_status(state, session),
        "href": None,
        "account": session.username if session is not None and session.logged_in else None,
        "since": state.since if state is not None else None,
        "mode": state.mode if state is not None else None,
        "display_only": bool(state.display_only) if state is not None else False,
        "viewer_ready": bool(state.viewer_ready) if state is not None else False,
        "human_steer_open": bool(state.human_steer_open) if state is not None else False,
        "session": session.to_json() if session is not None else None,
        "error": error,
    }


async def card_state(
    capsule: CapsuleClient, memory: CardMemory, *, probe: SessionProbe | None
) -> dict[str, Any]:
    """`GET /api/capsule`: the capsule's mode now and the last session reading. Does NOT touch
    Reddit — a card refresh must not cost a platform request; `confirm_session` does the reading."""
    try:
        state = await capsule.state()
    except CapsuleUnavailable as exc:
        return card_json(configured=True, state=None, session=memory.last_session, error=str(exc))
    memory.last_state = state
    session = memory.last_session
    if session is None and probe is None:
        session = SessionReading(None, None, NOT_CONFIGURED_HERE)
    return card_json(configured=True, state=state, session=session, error=None)


#: The login page the capsule opens for the human, per platform. The capsule holds the same
#: closed list and refuses anything else; this map only chooses from it.
LOGIN_PAGES: dict[str, str] = {
    "X": "https://x.com/i/flow/login",
    "REDDIT": "https://www.reddit.com/login/",
}


async def open_session(
    capsule: CapsuleClient,
    gateway: GatewayClient,
    memory: CardMemory,
    *,
    operator_id: UUID,
    public_origin: str,
    platform: str | None = None,
) -> dict[str, Any]:
    """`POST /api/capsule/session`: screen up with a human steer, ticket minted, viewer URL out.

    Order matters: the capsule first (a ticket for a screen that never came up is a dead link),
    the gateway second. Both raise `CapsuleUnavailable` on failure; the route says 503. With a
    `platform`, the capsule opens that platform's login page in the visible browser (26.09).
    """
    state = await capsule.open_human_session(
        HUMAN_SESSION_REASON, start_url=LOGIN_PAGES.get(platform or "")
    )
    ticket = await gateway.issue_control_ticket(user_id=operator_id)
    memory.last_state = state
    memory.events.append("session_opened")
    return {
        "status": AWAITING_HUMAN,
        "href": ticket.viewer_url(public_origin),
        "ticket_expires_at": ticket.expires_at,
        "capsule": state.to_json(),
    }


async def confirm_session(
    capsule: CapsuleClient,
    gateway: GatewayClient,
    memory: CardMemory,
    *,
    probe: SessionProbe | None,
    resume_jobs: Callable[[], Awaitable[int]],
) -> dict[str, Any]:
    """`POST /api/capsule/session/confirm`: measure, close the steer, cut the streams, measure,
    resume.

    Two readings on purpose (the plan B verdict): the first while the human's screen is still up
    (headful with a steer), the second after the capsule returned to display-only headful. If the
    second says logged in, the jobs parked by a missing login are resumed — that is what makes the
    login matter to the observer.

    The gateway is told to revoke every stream session right after the capsule closed the steer:
    removing the credentials file stops NEW connections, and only the gateway can end the one the
    founder's tab still holds. If the gateway cannot be reached the call raises and the route says
    503 — a second click repeats every step, none of them minds.
    """
    with_human = await read_session(probe)
    state = await capsule.close_human_session()
    memory.last_state = state
    revoked = await gateway.revoke_sessions()
    display_only = await read_session(probe)
    memory.last_session = display_only
    resumed = 0
    if display_only.logged_in:
        resumed = await resume_jobs()
    memory.events.append("session_confirmed")
    return {
        "status": card_status(state, display_only),
        "account": display_only.username if display_only.logged_in else None,
        "with_human": with_human.to_json(),
        "display_only": display_only.to_json(),
        "resumed_jobs": resumed,
        "revoked_streams": revoked,
        "capsule": state.to_json(),
    }


def config_json(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the card may know about the configuration — never a token."""
    if config is None:
        return {"configured": False}
    return {"configured": True, "reader_url": config.get("reader_url")}
