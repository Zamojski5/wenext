"""The capsule's page reader — the ONE process allowed to steer the capsule's Chrome (D-025).

`docs/OUTCOMES.md` §6 point 6: the founder wants Reddit read "like me on Chrome". The observer
runs in the `api` container, which has no Playwright, no profile and no way to reach CDP (bound to
the capsule's loopback, measured in `contract.py`). So the capsule itself offers a narrow HTTP
endpoint on the compose network: `GET /read?url=…` navigates the capsule's Chrome to `url` and
returns what the browser received — status, final URL, content type, body. Nothing else.

Three locks, each measured by `tests/test_reader.py`:

1. **Allowlist of exact URL shapes.** Only `https://www.reddit.com` and only these paths:
   `/r/<sub>/new.json`, `/r/<sub>/comments/<id>.json`, `/r/<sub>/about.json`, `/api/me.json`,
   and the two search listings `/search.json` and `/r/<sub>/search.json`, with a closed set of
   query parameters PER PATH (a search key on a listing, or the other way round, is refused).
   Exact origin, not eTLD+1 (roadmap 19.4, L3935).
   Anything else is 400 and Chrome is never asked. This replaces the F0 invariant "the capsule
   has no route to the world" — since D-025 it has one, and this list is the boundary.
   `about.json` joined the list on 21.09 by the coordinator's decision: it carries
   `user_is_subscriber` for the signed-in account, which is the only readback that can turn
   "dołączono" into a statement about an EFFECT rather than about a request sent (E3b).
   The two search paths joined on 10.10 (live Reddit search for the plugin: "who describes this
   problem right now"). They return PUBLIC posts only, so unlike the inbox they have no switch;
   what they carry outwards is the query text, which is why `q` is a short closed character
   class and not free text (see `SEARCH_QUERY`).
   A FIFTH path, `/message/inbox.json`, exists behind `WENEXT_READER_ALLOW_INBOX` and is OFF by
   default (P0): it is the only source that knew AutoModerator had removed our comments on
   17.09, and it is also the first path carrying the account's private messages, so switching it
   on is an operator's decision.
2. **Shared token**, compared in constant time. Known to the capsule and to `api`, not to `codex`
   (which sits on `edgenet` too). Without a token the reader refuses to START (`ReaderConfigError`):
   an unauthenticated steering endpoint is not a degraded mode, it is a different product.
3. **Read-only by vocabulary.** The fetch is `page.goto` plus the navigation response body. No
   `Runtime.evaluate`, no `Input.*`, no click/fill/type — the repository's bypass scanner
   (`control_plane/execution/bypass_scan.py`) treats those as writes, and this module must stay
   clean under it. `Page.navigate` is explicitly not a write there.

Pace: one fetch at a time (the HTTP server is single-threaded on purpose) and a minimum interval
between fetches, so a burst of snapshot jobs cannot turn into a burst against Reddit.

What is NOT here: login. Whatever session the profile holds is what Chrome uses; putting one there
is a human's job through the INTERACTIVE path, which is a separate task (`docs/OUTCOMES.md` §8).

The one exception, added for P0 and also OFF by default (`WENEXT_READER_ALLOW_ANONYMOUS`):
`GET /read?url=…&viewer=anonymous` reads through a FRESH browser context instead of the profile,
so the page comes back as a logged-out visitor sees it. This exists because reading as the
account cannot answer "is this comment visible to anybody else": Reddit shows an author their own
removed comments, and on 17.09 a read-back an hour after two removals still reported them present
(STAN-PRAC §8.22). The context is created for one page and destroyed after it; the profile's
session is neither used nor touched.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hmac
import http.server
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .writer import (
    ENV_WRITER_TOKEN,
    WRITER_TOKEN_HEADER,
    CommentActuator,
    CommentBlocked,
    CommentRefused,
    CommentUnknown,
)

if TYPE_CHECKING:
    from .x_writer import XPostResult, XWriteActuator

#: The header carrying the shared token. Pinned to the adapter's spelling by a test on both sides.
READER_TOKEN_HEADER = "X-Wenext-Reader-Token"

#: `POST /act/comment` (R4 minimal, D-028) is served by this same single-threaded server, behind a
#: SEPARATE token (`writer.py`), and only while the capsule is INTERACTIVE + display_only with the
#: human steer closed: an act never races a read or a human, because all three share this thread.
ACT_COMMENT_PATH = "/act/comment"
#: `POST /act/post` (0023): ten sam serwer, ten sam token, te same warunki kapsuły. Osobna
#: ścieżka, a nie pole w ciele `/act/comment`, bo to jest inny rodzaj zapisu: komentarz wchodzi
#: w cudzy wątek, własny post zakłada nowy. Osobny adres da się osobno wyłączyć i osobno policzyć.
ACT_POST_PATH = "/act/post"
DEFAULT_ACT_TIMEOUT_S = 45.0

#: `GET /x/read` renders one X page (`x_reader.py`). A constant because the client
#: (`community_observer.x_browser`) spells it on its side and the two packages do not import each
#: other: the literal IS the contract, like the token header.
X_READ_PATH = "/x/read"
#: The two writes on X (`x_writer.py`): a reply under a post and a post of our own. Same server,
#: same writer token, same gate and the same outcome map as `/act/comment`. Separate paths rather
#: than a field in one body, for the reason `ACT_POST_PATH` gives: each can be switched off and
#: counted on its own.
ACT_X_REPLY_PATH = "/act/x/reply"
ACT_X_POST_PATH = "/act/x/post"
#: The base64 of the largest image an own X post may carry (`x_writer.MAX_IMAGE_BYTES`, 4/3 of it).
MAX_IMAGE_B64_CHARS = 7 * 1024 * 1024

#: The minimum interval between two X page loads (reads and writes alike), ON TOP of the generic
#: `min_interval_s`. Measured on 26.09 by the first X reads through the capsule: after several
#: consecutive reads X began answering 403 to everything, including the public profile that had
#: read fine twenty minutes earlier. The generic 2 s pace was set for Reddit's JSON and is not a
#: pace X tolerates, so a burst of agent reads must not reach X as a burst. One place, one clock.
ENV_X_MIN_INTERVAL = "WENEXT_X_MIN_INTERVAL_SECONDS"
DEFAULT_X_MIN_INTERVAL_S = 8.0

#: The ONLY pages `POST /mode` may open for the human after a switch (the "Połącz X" button lands
#: the human on X's login form instead of a blank tab). A closed list of exact strings, because
#: this navigation happens in the account's own profile: a free-form URL here would be a way to
#: make the capsule's Chrome visit anything.
LOGIN_START_URLS: frozenset[str] = frozenset(
    {"https://x.com/i/flow/login", "https://www.reddit.com/login/"}
)

ALLOWED_HOST = "www.reddit.com"
ALLOWED_ORIGIN = f"https://{ALLOWED_HOST}"

#: The two search listings (10.10). `/search.json` searches all of Reddit, `/r/<sub>/search.json`
#: one community (with `restrict_sr`). Kept apart from the listing paths because their query
#: parameters are a different, closed set: `authorize_url` picks the set from the matched path.
GLOBAL_SEARCH_PATH: re.Pattern[str] = re.compile(r"^/search\.json$")
SUBREDDIT_SEARCH_PATH: re.Pattern[str] = re.compile(r"^/r/[A-Za-z0-9_]{2,21}/search\.json$")
SEARCH_PATHS: tuple[re.Pattern[str], ...] = (GLOBAL_SEARCH_PATH, SUBREDDIT_SEARCH_PATH)

#: The URL paths the capsule will fetch. Subreddit names: 2-21 word characters (the same
#: rule as `community_observer.reddit_payload.COMMUNITY_KEY`); post ids: base36, at most 12.
ALLOWED_PATHS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/new\.json$"),
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{1,12}\.json$"),
    # CZWARTA ŚCIEŻKA, dopisana 21.09 decyzją koordynatora po pomiarze sondy społeczności.
    # `about.json` niesie `user_is_subscriber` dla ZALOGOWANEGO konta, czyli gotowy ODCZYT
    # ZWROTNY członkostwa: jedyny sposób, żeby po dołączeniu powiedzieć „dołączono" na podstawie
    # skutku, a nie na podstawie wysłanego żądania (E3b). Alternatywą było czytanie przycisku ze
    # strony społeczności, a ta na `old.reddit.com` oddaje dziś 403 i stronę blokady.
    # Jest to ścieżka ODCZYTOWA, tylko GET, o JEDNEJ społeczności, i nie niesie cudzych treści
    # prywatnych, czym różni się od skrzynki trzymanej poza tą listą.
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/about\.json$"),
    re.compile(r"^/api/me\.json$"),
    *SEARCH_PATHS,
)

#: A FOURTH path, off unless `WENEXT_READER_ALLOW_INBOX` says otherwise (P0, STAN-PRAC §8.22).
#: The account's inbox is the only place that knew AutoModerator had removed our two comments on
#: 17.09; our own read-back could not see it, because Reddit shows an author their own removed
#: comments. It is kept out of `ALLOWED_PATHS` rather than added to it because it is the first
#: path that carries the account's private messages, and the allowlist is the boundary this
#: module exists to be: widening it is a decision an operator makes, not a default.
INBOX_PATH: re.Pattern[str] = re.compile(r"^/message/inbox\.json$")

#: Query parameters and the values each may take. Anything else — including a repeated key —
#: is refused. `limit` up to 500 covers the comment tree read; listings ask for at most 100.
ALLOWED_QUERY: Mapping[str, re.Pattern[str]] = {
    "raw_json": re.compile(r"^1$"),
    "limit": re.compile(r"^([1-9][0-9]{0,2})$"),
    "depth": re.compile(r"^([1-9]|10)$"),
    "sort": re.compile(r"^(old|new|top|confidence|controversial|qa)$"),
}
_MAX_LIMIT = 500

#: The query parameters of the two search paths (10.10), a SEPARATE closed set so that a search key
#: on a listing path (`/r/x/new.json?q=a`) and a listing key on a search path (`sort=old`) are both
#: refused: one mapping for everything would quietly widen each path to the union of the two.
#:
#: `q` is the only free text the reader ever lets out, so it is the one parameter that is a
#: character class and not a keyword: 1-256 characters of letters, digits, `_`, space and the
#: punctuation Reddit's search syntax needs (`" ' - . , : ; ! ? ( ) + / # @ &`, for `"phrase"`,
#: `title:`, `-excluded`, `(a OR b)`, `C++`, `CI/CD`). No control characters, no brackets and no
#: percent signs: the query is built by a model that has read a private repository, and what
#: leaves through this door should be a search, not a payload. The caller's mirror of this class
#: is `control_plane.observe.reddit_search`; a test there runs every URL it builds through
#: `authorize_url`, so the two cannot drift apart unnoticed.
#: `restrict_sr` exists only on `/r/<sub>/search.json` (it means "this community only").
SEARCH_QUERY: Mapping[str, re.Pattern[str]] = {
    "q": re.compile(r"^(?=.*\S)[\w \"'\-.,:;!?()+/#@&]{1,256}$"),
    "sort": re.compile(r"^(new|relevance|top|comments)$"),
    "t": re.compile(r"^(hour|day|week|month|year|all)$"),
    "limit": re.compile(r"^([1-9][0-9]?|100)$"),
    "type": re.compile(r"^link$"),
    "raw_json": re.compile(r"^1$"),
}
SUBREDDIT_SEARCH_QUERY: Mapping[str, re.Pattern[str]] = {
    **SEARCH_QUERY,
    "restrict_sr": re.compile(r"^(on|1|true)$"),
}

ENV_PORT = "WENEXT_READER_PORT"
ENV_BIND = "WENEXT_READER_BIND"
ENV_TOKEN = "WENEXT_READER_TOKEN"
ENV_MIN_INTERVAL = "WENEXT_READER_MIN_INTERVAL_SECONDS"
#: After Reddit answers 429 the capsule sends no Reddit read for this long, unless the response
#: said how long itself (`retry-after`, Reddit's `x-ratelimit-reset`). Measured 27.09 01:10 CEST:
#: after an api restart the observer read about 150 threads in 15 minutes at the 2 s pace, Reddit
#: answered 429 to a dozen of them, and every next job asked again. A limit the account keeps
#: hitting is a limit that gets worse: this is the brake, the 2 s interval is only a pace.
ENV_PLATFORM_COOLDOWN = "WENEXT_READER_429_COOLDOWN_SECONDS"
DEFAULT_PLATFORM_COOLDOWN_S = 300.0
MIN_PLATFORM_COOLDOWN_S = 30.0
MAX_PLATFORM_COOLDOWN_S = 900.0
ENV_TIMEOUT = "WENEXT_READER_TIMEOUT_SECONDS"
#: The two switches added for P0. Both OFF unless set to one of `TRUTHY`: each widens what this
#: reader may do, and a widened boundary that arrives as a default is a boundary nobody decided.
ENV_ALLOW_INBOX = "WENEXT_READER_ALLOW_INBOX"
ENV_ALLOW_ANONYMOUS = "WENEXT_READER_ALLOW_ANONYMOUS"

#: Who the read is made as. `session` is the capsule's persistent profile (the human's login) and
#: stays the default, so every caller written before P0 keeps reading exactly as it did.
VIEWER_SESSION = "session"
VIEWER_ANONYMOUS = "anonymous"
TRUTHY = frozenset({"1", "true", "yes", "on"})


def switch_is_on(env: Mapping[str, str], name: str) -> bool:
    return (env.get(name) or "").strip().lower() in TRUTHY


DEFAULT_BIND = "0.0.0.0"
DEFAULT_MIN_INTERVAL_S = 2.0
DEFAULT_TIMEOUT_S = 30.0


class ReadRefused(Exception):
    """The URL is outside the allowlist. Chrome was not asked."""


class ReaderConfigError(Exception):
    """The environment asks for a reader but not in a way that is safe to start."""


class FetchFailed(Exception):
    """Chrome could not deliver the page (no context, no response, body unreadable)."""


class FetchTimedOut(FetchFailed):
    """The navigation did not finish in time."""


class ModeSwitchRefused(Exception):
    """The mode request is malformed or outside the closed list of reasons. Nothing changed."""


class ModeSwitchFailed(Exception):
    """The switch was legitimate but the capsule could not complete it."""


class ModeController(Protocol):
    """The capsule's mode, as the control plane may see and change it (plan B, level 2).

    Implemented by `control.SupervisorModeController`; a fake in tests. Both methods run on the
    reader's single thread, so a switch never races a fetch.
    """

    def state(self) -> Mapping[str, Any]: ...

    def switch(
        self,
        mode: str,
        reason: str | None,
        display_only: bool = False,
        start_url: str | None = None,
    ) -> Mapping[str, Any]: ...


def authorize_url(url: str, *, allow_inbox: bool = False) -> str:
    """The canonical form of `url` if it is one of the allowed shapes; `ReadRefused` otherwise.

    `allow_inbox` adds the inbox path (`/message/inbox.json`) and nothing else. It defaults to
    False so that every existing caller keeps the boundary it had.

    Canonical means: scheme and host lower-cased, no fragment, query re-encoded from the parsed
    pairs — so what Chrome fetches is exactly what was checked, not a differently-parsed twin.
    The query rules are chosen by the matched PATH: the search paths have their own set
    (`SEARCH_QUERY`) and require `q`; every other path keeps `ALLOWED_QUERY`.
    """
    if not isinstance(url, str) or not url:
        raise ReadRefused("brak URL")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ReadRefused(f"tylko https, nie {parts.scheme!r}")
    # `netloc` (not `hostname`) so user-info and an explicit port are refused too:
    # `https://www.reddit.com@evil.example/…` has hostname `evil.example` and must not pass.
    if parts.netloc.lower() != ALLOWED_HOST:
        raise ReadRefused(f"tylko origin {ALLOWED_ORIGIN}, nie {parts.netloc!r}")
    if parts.fragment:
        raise ReadRefused("fragment w URL nie jest dozwolony")
    allowed = ALLOWED_PATHS + ((INBOX_PATH,) if allow_inbox else ())
    if not any(pattern.match(parts.path) for pattern in allowed):
        if INBOX_PATH.match(parts.path):
            raise ReadRefused(
                "skrzynka konta jest poza allowlistą, dopóki czytnik nie dostanie "
                f"{ENV_ALLOW_INBOX}=1 — to jest decyzja operatora, nie domyślna"
            )
        raise ReadRefused(f"ścieżka poza allowlistą: {parts.path!r}")
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query))
    except ValueError as exc:
        raise ReadRefused(f"niepoprawne zapytanie: {parts.query!r}") from exc
    query_rules: Mapping[str, re.Pattern[str]] = ALLOWED_QUERY
    is_search = any(pattern.match(parts.path) for pattern in SEARCH_PATHS)
    if is_search:
        query_rules = (
            SUBREDDIT_SEARCH_QUERY if SUBREDDIT_SEARCH_PATH.match(parts.path) else SEARCH_QUERY
        )
    seen: set[str] = set()
    for key, value in pairs:
        if key in seen:
            raise ReadRefused(f"powtórzony parametr {key!r}")
        seen.add(key)
        rule = query_rules.get(key)
        # `fullmatch`, not `match`: the patterns end in `$`, which also matches BEFORE a trailing
        # newline, so `limit=5%0A` used to pass as `5`. The canonical query would then have carried
        # the newline to Reddit, a value nobody checked.
        if rule is None or not rule.fullmatch(value):
            raise ReadRefused(f"parametr poza allowlistą: {key}={value!r}")
        if key == "limit" and int(value) > _MAX_LIMIT:
            raise ReadRefused(f"limit={value} ponad {_MAX_LIMIT}")
    if is_search and "q" not in seen:
        raise ReadRefused("wyszukiwanie wymaga parametru q")
    return urlunsplit(("https", ALLOWED_HOST, parts.path, urlencode(pairs), ""))


@dataclass(frozen=True)
class FetchResult:
    """What the browser received for one navigation."""

    status: int
    final_url: str
    content_type: str | None
    body: str
    #: How long the platform asked us to wait (seconds), when it said so. Not on the wire: the
    #: reader acts on it, the caller gets the status.
    retry_after_s: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "body": self.body,
        }


class PageFetcher(Protocol):
    """Navigate to an already-authorized URL and return the response. One call, one page.

    `anonymous=True` asks for the page WITHOUT the profile's session. It is a separate argument
    rather than a separate method because everything else about the read is identical, and the
    caller must not be able to get one while believing it asked for the other.
    """

    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult: ...


class _NoPlaywrightError(Exception):
    """Stands in for Playwright's `Error` on a host without Playwright (unit tests). Never raised
    by the reader itself; a test double raises it where Chrome would raise."""


class _NoPlaywrightTimeout(_NoPlaywrightError):
    """Stands in for Playwright's `TimeoutError`, which is a subclass of its `Error` there too:
    one class for both would let `except Timeout` swallow every navigation refusal."""


def playwright_errors() -> tuple[type[BaseException], type[BaseException]]:
    """`(Error, TimeoutError)` of Playwright, or stand-ins when it is not installed.

    The same shape as `writer.py`: the capsule image has Playwright, the host venv used for unit
    tests does not, and a fetch that imported it unconditionally could not be tested with a
    double at all (the 26.09 status tests failed there for that reason alone).
    """
    try:
        from playwright.sync_api import Error, TimeoutError
    except ImportError:
        return _NoPlaywrightError, _NoPlaywrightTimeout
    return Error, TimeoutError


def retry_after_seconds(headers: Mapping[str, Any] | None) -> float | None:
    """How long the platform asked us to wait: `retry-after` in seconds or Reddit's
    `x-ratelimit-reset`. A date form of `retry-after` is not parsed; the default brake applies."""
    if not headers:
        return None
    for name in ("retry-after", "x-ratelimit-reset"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except ValueError:
            continue
        if value > 0:
            return value
    return None


class CdpPageFetcher:
    """`PageFetcher` over the capsule's own Chrome, attached through CDP on the loopback.

    The Playwright driver is started lazily and kept for the life of the reader thread; the CDP
    connection is opened per fetch and closed after it — `Browser.close` on a `connect_over_cdp`
    browser only disconnects (measured in `supervisor.py`), Chrome keeps running. The page is
    opened in `contexts[0]`, the persistent profile: that is where a human's login lives.
    """

    def __init__(self, endpoint_http: str | Callable[[], str]) -> None:
        # A callable, not a string, whenever the capsule can switch modes: every switch is a new
        # Chrome process with a new DevTools port, and a fetcher holding the old address would
        # report every read as "browser unavailable" from the first switch on.
        self._endpoint_http = endpoint_http
        self._playwright: Any = None

    def driver(self) -> Any:
        """The one Playwright sync driver of the reader thread, started lazily and shared with
        the comment actuator (D-028): the sync API refuses a second driver on a thread that
        already runs one."""
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        return self._playwright

    def _endpoint(self) -> str:
        if callable(self._endpoint_http):
            return self._endpoint_http()
        return self._endpoint_http

    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult:
        PlaywrightError, PlaywrightTimeout = playwright_errors()

        driver = self.driver()
        try:
            browser = driver.chromium.connect_over_cdp(
                self._endpoint(), timeout=int(timeout_s * 1000)
            )
        except PlaywrightError as exc:
            raise FetchFailed(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc
        anonymous_context = None
        try:
            if not browser.contexts:
                raise FetchFailed("Chrome kapsuły nie ma kontekstu profilu — brak sesji do użycia")
            if anonymous:
                # A FRESH context, not `contexts[0]`: the persistent profile is where the human's
                # Reddit login lives, and a read through it is a read BY the account. That is the
                # read that reported a removed comment as present on 17.09. This context starts
                # with no cookies and no storage, is used for exactly one page and is destroyed
                # below, so nothing it picks up can leak into the account's session either way.
                try:
                    anonymous_context = browser.new_context()
                except PlaywrightError as exc:
                    raise FetchFailed(f"Chrome kapsuły nie dał kontekstu bez sesji: {exc}") from exc
                page = anonymous_context.new_page()
            else:
                page = browser.contexts[0].new_page()
            # THE PLATFORM'S STATUS MUST SURVIVE A REFUSED NAVIGATION. Chrome raises
            # ERR_HTTP_RESPONSE_CODE_FAILURE when a response has a 4xx/5xx code and an empty body:
            # `page.goto` then exits with an exception and `response` never exists. Until 26.09
            # that ended as `FetchFailed` without a number, i.e. a capsule 502 for EVERY one of
            # those causes. Measured that day on `/api/me.json`: the product could not tell a rate
            # limit (429) from a suspended account (403) or a Reddit outage (5xx), and result 1 of
            # the task requires exactly that distinction. The response IS received, only the
            # navigation does not commit it, so it is caught by a listener and its status kept.
            # Only the main frame and this address count: subresources (images, scripts) have
            # codes of their own and would replace the result of the read.
            captured: dict[str, Any] = {}

            def _remember_response(response: Any) -> None:
                if captured or response.url != url:
                    return
                if response.frame != page.main_frame:
                    return
                captured["status"] = response.status
                captured["content_type"] = response.headers.get("content-type")
                captured["retry_after_s"] = retry_after_seconds(response.headers)
                with contextlib.suppress(Exception):
                    captured["body"] = response.text()

            page.on("response", _remember_response)
            try:
                try:
                    response = page.goto(url, wait_until="load", timeout=int(timeout_s * 1000))
                except PlaywrightTimeout as exc:
                    raise FetchTimedOut(
                        f"nawigacja do {url} nie skończyła się w {timeout_s:g} s"
                    ) from exc
                except PlaywrightError as exc:
                    if "status" in captured:
                        # The platform's code instead of "the navigation failed": the caller
                        # gets the real status and decides whether it is a refusal or a pace.
                        return FetchResult(
                            status=int(captured["status"]),
                            final_url=url,
                            content_type=captured.get("content_type"),
                            body=captured.get("body", ""),
                            retry_after_s=captured.get("retry_after_s"),
                        )
                    raise FetchFailed(f"nawigacja do {url} nie powiodła się: {exc}") from exc
                if response is None:
                    raise FetchFailed(f"nawigacja do {url} nie dała odpowiedzi")
                try:
                    body = response.text()
                except PlaywrightError as exc:
                    raise FetchFailed(f"ciało odpowiedzi {url} nieczytelne: {exc}") from exc
                return FetchResult(
                    status=response.status,
                    final_url=page.url,
                    content_type=response.headers.get("content-type"),
                    body=body,
                    retry_after_s=retry_after_seconds(response.headers),
                )
            finally:
                page.close()
        finally:
            if anonymous_context is not None:
                with contextlib.suppress(Exception):
                    anonymous_context.close()
            browser.close()


class _ReaderHTTPServer(http.server.HTTPServer):
    """Single-threaded on purpose: one fetch at a time is the concurrency limit."""

    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        token: str,
        fetcher: PageFetcher,
        min_interval_s: float,
        timeout_s: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        controller: ModeController | None = None,
        writer_token: str | None = None,
        actuator: CommentActuator | None = None,
        act_timeout_s: float = DEFAULT_ACT_TIMEOUT_S,
        allow_inbox: bool = False,
        allow_anonymous: bool = False,
        x_actuator: XWriteActuator | None = None,
        x_min_interval_s: float = DEFAULT_X_MIN_INTERVAL_S,
        platform_cooldown_s: float = DEFAULT_PLATFORM_COOLDOWN_S,
    ) -> None:
        if not token:
            raise ReaderConfigError("czytnik bez tokenu nie startuje")
        self.allow_inbox = allow_inbox
        self.allow_anonymous = allow_anonymous
        self.act_timeout_s = act_timeout_s
        if (actuator is None) != (not writer_token):
            raise ReaderConfigError(
                "aktuator komentarzy wymaga osobnego tokenu (WENEXT_WRITER_TOKEN) i odwrotnie: "
                "token bez aktuatora albo aktuator bez tokenu to błąd konfiguracji, nie tryb"
            )
        if x_actuator is not None and not writer_token:
            raise ReaderConfigError(
                "aktuator X wymaga tokenu aktuatora (WENEXT_WRITER_TOKEN): bez niego nie ma "
                "bramki, przez którą wolno pisać"
            )
        if writer_token and hmac.compare_digest(writer_token.encode(), token.encode()):
            raise ReaderConfigError("token aktuatora musi różnić się od tokenu czytnika")
        super().__init__(address, _Handler)
        self.token = token
        self.writer_token = writer_token
        self.actuator = actuator
        self.x_actuator = x_actuator
        self.x_min_interval_s = x_min_interval_s
        self._last_x_load_at: float | None = None
        self.acts_ok = 0
        self.acts_refused = 0
        self.fetcher = fetcher
        self.controller = controller
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.clock = clock
        self.sleep = sleep
        self.reads_ok = 0
        self.reads_failed = 0
        self.reads_refused = 0
        self.reads_paced = 0
        self.platform_cooldown_s = platform_cooldown_s
        self._reddit_paced_until: float | None = None
        self._last_fetch_at: float | None = None

    def reddit_pace_left(self) -> float | None:
        """Seconds left of the brake after Reddit's 429, or None when a read may go out."""
        if self._reddit_paced_until is None:
            return None
        left = self._reddit_paced_until - self.clock()
        if left <= 0:
            self._reddit_paced_until = None
            return None
        return left

    def note_reddit_429(self, retry_after_s: float | None) -> float:
        """Start the brake; its length is the platform's own word when it gave one, bounded."""
        wait = retry_after_s if retry_after_s is not None else self.platform_cooldown_s
        wait = min(max(wait, MIN_PLATFORM_COOLDOWN_S), MAX_PLATFORM_COOLDOWN_S)
        self._reddit_paced_until = self.clock() + wait
        return wait

    def throttle(self) -> None:
        """Waits until `min_interval_s` has passed since the previous fetch started."""
        now = self.clock()
        if self._last_fetch_at is not None:
            wait = self.min_interval_s - (now - self._last_fetch_at)
            if wait > 0:
                self.sleep(wait)
                now = self.clock()
        self._last_fetch_at = now

    def x_throttle(self) -> None:
        """Waits until `x_min_interval_s` has passed since the previous X page load started.

        Called by `/x/read` and both `/act/x/...` right before the page is opened, after
        `throttle()`, so an X load waits for BOTH paces. Reads and writes share one clock on
        purpose: X limits the client, not the kind of request (see `DEFAULT_X_MIN_INTERVAL_S`).
        """
        now = self.clock()
        if self._last_x_load_at is not None:
            wait = self.x_min_interval_s - (now - self._last_x_load_at)
            if wait > 0:
                self.sleep(wait)
                now = self.clock()
        self._last_x_load_at = now


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _ReaderHTTPServer  # type: ignore[assignment]

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/healthz":
            self._send(
                200,
                {
                    "ok": True,
                    "reads_ok": self.server.reads_ok,
                    "reads_failed": self.server.reads_failed,
                    "reads_refused": self.server.reads_refused,
                    "reads_paced": self.server.reads_paced,
                    "reddit_paced_for_s": self.server.reddit_pace_left(),
                },
            )
            return
        if parts.path == "/read":
            self._read(parts.query)
            return
        if parts.path == "/research":
            self._research(parts.query)
            return
        if parts.path == X_READ_PATH:
            self._x_read(parts.query)
            return
        if parts.path == "/state":
            self._state()
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/mode":
            self._mode()
            return
        if parts.path == ACT_COMMENT_PATH:
            self._act_comment()
            return
        if parts.path == ACT_POST_PATH:
            self._act_own_post()
            return
        if parts.path == ACT_X_REPLY_PATH:
            self._act_x_reply()
            return
        if parts.path == ACT_X_POST_PATH:
            self._act_x_own_post()
            return
        self._send(404, {"error": "not_found"})

    def _authorized(self) -> bool:
        presented = self.headers.get(READER_TOKEN_HEADER, "")
        if hmac.compare_digest(presented.encode("utf-8"), self.server.token.encode("utf-8")):
            return True
        self.server.reads_refused += 1
        self._send(401, {"error": "token_rejected"})
        return False

    def _state(self) -> None:
        if not self._authorized():
            return
        if self.server.controller is None:
            self._send(404, {"error": "no_mode_controller"})
            return
        self._send(200, dict(self.server.controller.state()))

    def _mode(self) -> None:
        """`POST /mode {"mode": "INTERACTIVE", "reason": "FIRST_LOGIN"}` — level 2 of plan B.

        Runs on the same single thread as `/read`, so no fetch can be mid-flight while Chrome is
        stopped and started again; that is what makes the controller's `safe_stop_done=True`
        true (see `control.py`).
        """
        if not self._authorized():
            return
        if self.server.controller is None:
            self._send(404, {"error": "no_mode_controller"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": "bad_request", "detail": f"ciało nie jest JSON-em: {exc}"})
            return
        if not isinstance(body, Mapping) or not isinstance(body.get("mode"), str):
            self._send(400, {"error": "bad_request", "detail": "wymagane pole `mode` (string)"})
            return
        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            self._send(400, {"error": "bad_request", "detail": "`reason` ma być stringiem"})
            return
        display_only = body.get("display_only", False)
        if not isinstance(display_only, bool):
            self._send(400, {"error": "bad_request", "detail": "`display_only` ma być bool"})
            return
        # `start_url` is checked BEFORE the switch, so a refused one changes nothing. It only
        # makes sense when a human takes the screen: with HEADLESS or display_only there is nobody
        # to show the page to, and silently ignoring it would tell the caller a page was opened.
        start_url = body.get("start_url")
        if start_url is not None:
            if not isinstance(start_url, str) or start_url not in LOGIN_START_URLS:
                self._send(
                    400,
                    {
                        "error": "bad_request",
                        "detail": f"`start_url` spoza zamkniętej listy: {sorted(LOGIN_START_URLS)}",
                    },
                )
                return
            if body["mode"] != "INTERACTIVE" or not reason or display_only:
                self._send(
                    400,
                    {
                        "error": "bad_request",
                        "detail": "`start_url` tylko przy INTERACTIVE z powodem (człowiek przy "
                        "ekranie), nie przy HEADLESS ani display_only",
                    },
                )
                return
        try:
            if start_url is not None:
                # The page is handed to the browser at launch, as its first tab: a person's
                # launch has no DevTools endpoint to open it through (`args.HUMAN_ARGS`).
                state = self.server.controller.switch(
                    body["mode"], reason, display_only, start_url=start_url
                )
            else:
                state = self.server.controller.switch(body["mode"], reason, display_only)
        except ModeSwitchRefused as exc:
            self._send(400, {"error": "mode_refused", "detail": str(exc)[:400]})
            return
        except ModeSwitchFailed as exc:
            self._send(502, {"error": "mode_switch_failed", "detail": str(exc)[:400]})
            return
        except Exception as exc:
            self._send(
                502,
                {"error": "mode_switch_failed", "detail": f"{type(exc).__name__}: {exc}"[:400]},
            )
            return
        payload = dict(state)
        if start_url is not None:
            payload["start_url"] = self._open_start_url(start_url, state)
        self._send(200, payload)

    def _open_start_url(self, url: str, state: Mapping[str, Any]) -> dict[str, Any]:
        """What became of the page for the human after a SUCCESSFUL switch.

        Since 27.09 the page is not opened through CDP: a person's launch has none, and the page
        is the browser's first tab from the launch itself. A switch that did not happen
        (`changed: false`, the person's window was already up) leaves the window as the person
        has it, and says so, instead of claiming a page it did not open.
        """
        if state.get("mode") != "INTERACTIVE" or state.get("display_only"):
            return {
                "url": url,
                "opened": False,
                "detail": "kapsuła nie jest w trybie z człowiekiem przy ekranie; karty nie otwarto",
            }
        if state.get("changed") is False:
            return {
                "url": url,
                "opened": False,
                "detail": "okno logowania było już otwarte; zostaje tak, jak zostawił je człowiek",
            }
        print(f"kapsuła: przeglądarka dla człowieka otwarta na {url}, bez CDP", flush=True)
        return {"url": url, "opened": True, "final_url": url}

    def _act_allowed(self) -> bool:
        """The three refusals in front of EVERY act, in this order: no actuator configured (404),
        wrong writer token (401), the capsule not in INTERACTIVE + display_only with the steer
        closed (409 `capsule_not_display_only`: a human at the screen and an automation writing
        into the same profile is the one thing D-026 forbids).

        One gate for both writes, on purpose: an endpoint added later must not be able to get a
        gentler gate by accident, and a gate tightened here tightens for everything at once.
        """
        if self.server.actuator is None or not self.server.writer_token:
            self._send(404, {"error": "no_actuator"})
            return
        presented = self.headers.get(WRITER_TOKEN_HEADER, "")
        if not hmac.compare_digest(
            presented.encode("utf-8"), self.server.writer_token.encode("utf-8")
        ):
            self.server.acts_refused += 1
            self._send(401, {"error": "token_rejected"})
            return
        if self.server.controller is None:
            self._send(409, {"error": "no_mode_controller"})
            return
        state = dict(self.server.controller.state())
        if (
            state.get("mode") != "INTERACTIVE"
            or not state.get("display_only")
            or state.get("human_steer_open")
            or not state.get("alive")
        ):
            self.server.acts_refused += 1
            self._send(
                409,
                {
                    "error": "capsule_not_display_only",
                    "detail": "publikacja wymaga INTERACTIVE + display_only z zamkniętym sterem "
                    "człowieka i żywym Chrome (D-026)",
                    "state": state,
                },
            )
            return False
        return True

    def _act_body(self, fields: tuple[str, ...]) -> Mapping[str, str] | None:
        """The request body, or `None` after sending 400. Every field must be a non-empty string:
        an act with a field the caller forgot is an act on content nobody consented to."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": "bad_request", "detail": f"ciało nie jest JSON-em: {exc}"})
            return None
        if not isinstance(body, Mapping) or not all(
            isinstance(body.get(f), str) and body.get(f) for f in fields
        ):
            self._send(400, {"error": "bad_request", "detail": f"wymagane pola tekstowe {fields}"})
            return None
        return body

    def _act_comment(self) -> None:
        """`POST /act/comment {"thread_url", "parent_fullname", "text", "account"}` (D-028).

        The actuator's three outcomes map to 200 (result), 409 `refused` (nothing submitted), 423
        `blocked` (stop the queue) and 504 `unknown` (submitted or maybe; the caller reconciles,
        never retries).
        """
        if not self._act_allowed():
            return
        body = self._act_body(("thread_url", "parent_fullname", "text", "account"))
        if body is None:
            return
        self.server.throttle()
        try:
            result = self.server.actuator.comment(
                thread_url=body["thread_url"],
                parent_fullname=body["parent_fullname"],
                text=body["text"],
                account=body["account"],
                timeout_s=self.server.act_timeout_s,
            )
        except CommentRefused as exc:
            self.server.acts_refused += 1
            self._send(409, {"error": "refused", "detail": str(exc)[:400], "submit_started": False})
            return
        except CommentBlocked as exc:
            self.server.acts_refused += 1
            self._send(
                423,
                {
                    "error": "blocked",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except CommentUnknown as exc:
            self._send(
                504,
                {
                    "error": "unknown",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except Exception as exc:
            # The actuator wraps everything after the click into CommentUnknown, so another class
            # escaped BEFORE it ran (a bug here, not on the page). The flag is unknown, so it is
            # reported as such, never as false: the caller must not requeue on a guess.
            self._send(
                502,
                {
                    "error": "browser_unavailable",
                    "detail": f"{type(exc).__name__}: {exc}"[:400],
                    "submit_started": None,
                },
            )
            return
        self.server.acts_ok += 1
        print(
            f"aktuator: komentarz {result.fullname} konta {result.account} pod "
            f"{body['parent_fullname']}",
            flush=True,
        )
        self._send(200, result.to_json())

    def _act_own_post(self) -> None:
        """`POST /act/post {"community", "title", "text", "account"}` (0023).

        Ten sam token, te same bramki i ta sama mapa wyników co przy komentarzu; inne są tylko
        pola, bo własny post celuje w SPOŁECZNOŚĆ, a nie w wątek. Ciało nie niesie adresu:
        aktuator składa go sam z nazwy społeczności, więc nie ma czego przemycić w ścieżce.
        """
        if not self._act_allowed():
            return
        body = self._act_body(("community", "title", "text", "account"))
        if body is None:
            return
        self.server.throttle()
        try:
            result = self.server.actuator.own_post(
                community=body["community"],
                title=body["title"],
                text=body["text"],
                account=body["account"],
                timeout_s=self.server.act_timeout_s,
            )
        except CommentRefused as exc:
            self.server.acts_refused += 1
            self._send(409, {"error": "refused", "detail": str(exc)[:400], "submit_started": False})
            return
        except CommentBlocked as exc:
            self.server.acts_refused += 1
            self._send(
                423,
                {
                    "error": "blocked",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except CommentUnknown as exc:
            self._send(
                504,
                {
                    "error": "unknown",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except Exception as exc:
            self._send(
                502,
                {
                    "error": "browser_unavailable",
                    "detail": f"{type(exc).__name__}: {exc}"[:400],
                    "submit_started": None,
                },
            )
            return
        self.server.acts_ok += 1
        print(
            f"aktuator: wątek {result.fullname} konta {result.account} w {body['community']}",
            flush=True,
        )
        self._send(200, result.to_json())

    def _x_act_allowed(self) -> bool:
        """`_act_allowed`, preceded by the one check it cannot make: is an X actuator configured.
        Everything else (writer token, INTERACTIVE + display_only, steer closed, Chrome alive) is
        the SAME gate as for Reddit, on purpose: a second platform must not bring a gentler gate."""
        if self.server.x_actuator is None:
            self._send(404, {"error": "no_actuator"})
            return False
        return bool(self._act_allowed())

    def _act_x_reply(self) -> None:
        """`POST /act/x/reply {"post_url", "post_id", "text", "account"}`: a reply under one post.

        The outcome map is the one of `/act/comment`: 200 result, 409 refused (nothing submitted),
        423 blocked (stop the queue), 504 unknown (submitted or maybe; reconcile, never retry),
        502 when the actuator itself broke (the click is unknown, so the flag is `null`).
        """
        if not self._x_act_allowed():
            return
        body = self._act_body(("post_url", "post_id", "text", "account"))
        if body is None:
            return
        self.server.throttle()
        self.server.x_throttle()
        self._x_act_outcome(
            lambda actuator: actuator.reply(
                post_url=body["post_url"],
                post_id=body["post_id"],
                text=body["text"],
                account=body["account"],
                timeout_s=self.server.act_timeout_s,
            ),
            describe=lambda result: (
                f"aktuator X: odpowiedź {result.post_id} konta {result.account} pod "
                f"{body['post_id']}"
            ),
        )

    def _act_x_own_post(self) -> None:
        """`POST /act/x/post {"text", "account"}`: a post of our own, from the home timeline.

        No address in the body: the actuator opens `https://x.com/home` itself, so there is
        nothing to smuggle in a path. Same token, gate and outcome map as the reply.
        """
        if not self._x_act_allowed():
            return
        body = self._act_body(("text", "account"))
        if body is None:
            return
        # 27.09: optionally ONE image, as base64 with its type. Both or neither; the bytes are
        # checked by the actuator (type, signature, size) before the browser is touched.
        encoded, image_type = body.get("image_b64"), body.get("image_type")
        image: bytes | None = None
        if encoded is not None or image_type is not None:
            if not isinstance(encoded, str) or not isinstance(image_type, str):
                self._send(
                    400, {"error": "bad_request", "detail": "obraz wymaga image_b64 i image_type"}
                )
                return
            if len(encoded) > MAX_IMAGE_B64_CHARS:
                self._send(400, {"error": "bad_request", "detail": "obraz za duży"})
                return
            try:
                image = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                self._send(400, {"error": "bad_request", "detail": f"obraz nie jest base64: {exc}"})
                return
        self.server.throttle()
        self.server.x_throttle()
        self._x_act_outcome(
            lambda actuator: actuator.own_post(
                text=body["text"],
                account=body["account"],
                timeout_s=self.server.act_timeout_s,
                image=image,
                image_type=image_type if image is not None else None,
            ),
            describe=lambda result: f"aktuator X: post {result.post_id} konta {result.account}",
        )

    def _x_act_outcome(
        self,
        act: Callable[[XWriteActuator], XPostResult],
        *,
        describe: Callable[[XPostResult], str],
    ) -> None:
        """ONE map from the actuator's outcome classes to HTTP for both X writes; two copies would
        drift apart at the first change of a status, and the status decides what the queue does."""
        try:
            result = act(self.server.x_actuator)
        except CommentRefused as exc:
            self.server.acts_refused += 1
            self._send(409, {"error": "refused", "detail": str(exc)[:400], "submit_started": False})
            return
        except CommentBlocked as exc:
            self.server.acts_refused += 1
            self._send(
                423,
                {
                    "error": "blocked",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except CommentUnknown as exc:
            self._send(
                504,
                {
                    "error": "unknown",
                    "detail": str(exc)[:400],
                    "submit_started": exc.submit_started,
                },
            )
            return
        except Exception as exc:
            # The actuator classifies everything it raises, so another class escaped from OUTSIDE
            # its classification. Whether the button was clicked is unknown, so the flag says so.
            self._send(
                502,
                {
                    "error": "browser_unavailable",
                    "detail": f"{type(exc).__name__}: {exc}"[:400],
                    "submit_started": None,
                },
            )
            return
        self.server.acts_ok += 1
        print(describe(result), flush=True)
        self._send(200, result.to_json())

    def _research(self, query: str) -> None:
        if not self._authorized():
            return
        from .public_research import ResearchRefused, inspect_public_page

        params = dict(parse_qsl(query, keep_blank_values=True))
        if (
            "url" not in params
            or set(params) - {"url", "javascript", "target_comment_id"}
            or params.get("javascript", "false") not in ("true", "false")
        ):
            self._send(400, {"detail": "Odczyt strony wymaga jednego adresu URL."})
            return
        if not isinstance(self.server.fetcher, CdpPageFetcher):
            self._send(503, {"detail": "Przeglądarka badawcza nie jest dostępna."})
            return
        self.server.throttle()
        try:
            result = inspect_public_page(
                self.server.fetcher,
                params["url"],
                javascript=params.get("javascript") == "true",
                target_comment_id=params.get("target_comment_id"),
            )
        except ResearchRefused as exc:
            self._send(400, {"detail": str(exc)})
            return
        except Exception as exc:
            self._send(502, {"detail": f"Nie udało się obejrzeć strony ({type(exc).__name__})."})
            return
        self._send(200, result)

    def _x_read(self, query: str) -> None:
        """`GET /x/read?url=...[&viewer=anonymous]`: one rendered X page (`x_reader.py`).

        A route of its own rather than a parameter of `/read`: another transport and another
        result shape. `/read` returns the response body (Reddit serves JSON); here Chrome has to
        RENDER the page, because X has no content without it. One name for two guarantees would
        hide which one a caller got, and with reads the guarantee is the product.

        `viewer` means what it means on `/read`: a present key must name one of the two contexts,
        and `anonymous` needs the same operator switch, so the two routes cannot disagree about
        which session a read used. `platform_status` is `null` on every 200 and a number on a
        refused navigation (502): the caller branches on that field, not on the wording.
        """
        if not self._authorized():
            return
        from .x_reader import (
            X_NAV_TIMEOUT_S,
            XPlatformRefused,
            XReadRefused,
            XReadTimedOut,
            authorize_x_url,
            read_x_page,
        )

        pairs = parse_qsl(query, keep_blank_values=True)
        keys = [key for key, _ in pairs]
        if keys.count("url") != 1 or len(keys) != len(set(keys)) or set(keys) - {"url", "viewer"}:
            self.server.reads_refused += 1
            self._send(
                400,
                {
                    "error": "bad_request",
                    "detail": "Odczyt X wymaga jednego adresu URL (i opcjonalnie `viewer`).",
                },
            )
            return
        params = dict(pairs)
        viewer = params["viewer"].strip().lower() if "viewer" in params else VIEWER_SESSION
        if viewer not in (VIEWER_SESSION, VIEWER_ANONYMOUS):
            self.server.reads_refused += 1
            self._send(
                400,
                {
                    "error": "viewer_refused",
                    "detail": f"`viewer` przyjmuje {VIEWER_SESSION} albo {VIEWER_ANONYMOUS}, "
                    f"nie {viewer!r}",
                },
            )
            return
        anonymous = viewer == VIEWER_ANONYMOUS
        if anonymous and not self.server.allow_anonymous:
            self.server.reads_refused += 1
            self._send(
                400,
                {
                    "error": "viewer_refused",
                    "detail": "odczyt anonimowy jest wyłączony, dopóki czytnik nie dostanie "
                    f"{ENV_ALLOW_ANONYMOUS}=1",
                },
            )
            return
        # The allowlist BEFORE the throttles: a refused address costs no wait and no navigation.
        try:
            canonical, capability = authorize_x_url(params["url"])
        except XReadRefused as exc:
            self.server.reads_refused += 1
            self._send(400, {"error": "url_refused", "detail": str(exc)})
            return
        fetcher = self.server.fetcher
        if not isinstance(fetcher, CdpPageFetcher):
            self._send(
                503,
                {
                    "error": "browser_unavailable",
                    "detail": "Przeglądarka kapsuły nie jest dostępna.",
                },
            )
            return
        self.server.throttle()
        self.server.x_throttle()
        try:
            result = read_x_page(
                canonical,
                driver=fetcher.driver,
                endpoint=fetcher._endpoint,
                anonymous=anonymous,
                timeout_s=min(self.server.timeout_s, X_NAV_TIMEOUT_S),
                clock=self.server.clock,
                sleep=self.server.sleep,
            )
        except XReadRefused as exc:
            self.server.reads_refused += 1
            self._send(400, {"error": "url_refused", "detail": str(exc)})
            return
        except XPlatformRefused as exc:
            # The PLATFORM's code travels as a number, not as a sentence: 429 and 403 mean
            # different things and lead to different decisions of the agent.
            self.server.reads_failed += 1
            self._send(
                502,
                {
                    "error": "platform_refused",
                    "detail": str(exc),
                    "platform_status": exc.status,
                    "url": exc.url,
                },
            )
            return
        except XReadTimedOut as exc:
            self.server.reads_failed += 1
            self._send(
                504,
                {"error": "browser_timeout", "detail": str(exc)[:300], "platform_status": None},
            )
            return
        except Exception as exc:
            self.server.reads_failed += 1
            self._send(
                502,
                {
                    "error": "browser_unavailable",
                    "detail": f"Nie udało się odczytać X ({type(exc).__name__}): {exc}"[:400],
                    "platform_status": None,
                },
            )
            return
        self.server.reads_ok += 1
        print(
            f"czytnik X[{viewer}]: {capability} {canonical} postów={len(result.posts)}"
            + (" (ściana logowania)" if result.login_wall else ""),
            flush=True,
        )
        self._send(200, {**result.to_json(), "platform_status": None})

    def _read(self, query: str) -> None:
        if not self._authorized():
            return
        params = dict(parse_qsl(query, keep_blank_values=True))
        url = params.get("url", "")
        # `viewer` is a parameter of OUR endpoint, never of the Reddit URL: it says which Chrome
        # context to read through, and the allowlist above still decides what may be read at all.
        # A `viewer` key that is PRESENT must name one of the two contexts, even when empty.
        # Falling back to the default for an unrecognised value would serve the account's view to
        # a caller that asked for the anonymous one, and that caller records what it gets as a
        # public-visibility verdict.
        viewer = params["viewer"].strip().lower() if "viewer" in params else VIEWER_SESSION
        if viewer not in (VIEWER_SESSION, VIEWER_ANONYMOUS):
            self.server.reads_refused += 1
            self._send(
                400,
                {
                    "error": "viewer_refused",
                    "detail": f"`viewer` przyjmuje {VIEWER_SESSION} albo {VIEWER_ANONYMOUS}, "
                    f"nie {viewer!r}",
                },
            )
            return
        anonymous = viewer == VIEWER_ANONYMOUS
        if anonymous and not self.server.allow_anonymous:
            self.server.reads_refused += 1
            self._send(
                400,
                {
                    "error": "viewer_refused",
                    "detail": "odczyt anonimowy jest wyłączony, dopóki czytnik nie dostanie "
                    f"{ENV_ALLOW_ANONYMOUS}=1",
                },
            )
            return
        try:
            canonical = authorize_url(url, allow_inbox=self.server.allow_inbox)
        except ReadRefused as exc:
            self.server.reads_refused += 1
            self._send(400, {"error": "url_refused", "detail": str(exc)})
            return
        left = self.server.reddit_pace_left()
        if left is not None:
            # The same answer a 429 gets, so every caller keeps its handling, and nothing reaches
            # Reddit: both viewers share the account's address, so the brake covers both.
            self.server.reads_paced += 1
            print(f"czytnik[{viewer}]: wstrzymane po 429, {left:.0f} s, {canonical}", flush=True)
            self._send(
                200,
                {
                    "status": 429,
                    "final_url": canonical,
                    "content_type": None,
                    "body": "",
                    "viewer": viewer,
                    "paced": True,
                    "detail": f"Reddit odpowiedział 429; kapsuła nie wysyła odczytów Reddita "
                    f"jeszcze przez {left:.0f} s, żeby nie pogłębiać limitu konta.",
                },
            )
            return
        self.server.throttle()
        try:
            result = self.server.fetcher.fetch(
                canonical, timeout_s=self.server.timeout_s, anonymous=anonymous
            )
        except FetchTimedOut as exc:
            self.server.reads_failed += 1
            self._send(504, {"error": "browser_timeout", "detail": str(exc)[:300]})
            return
        except Exception as exc:
            self.server.reads_failed += 1
            self._send(
                502,
                {"error": "browser_unavailable", "detail": f"{type(exc).__name__}: {exc}"[:300]},
            )
            return
        self.server.reads_ok += 1
        print(
            f"czytnik[{viewer}]: {result.status} {canonical} ({len(result.body)} B)",
            flush=True,
        )
        if result.status == 429:
            wait = self.server.note_reddit_429(result.retry_after_s)
            print(f"czytnik: Reddit 429, odczyty Reddita wstrzymane na {wait:.0f} s", flush=True)
        self._send(200, {**result.to_json(), "viewer": viewer})

    def _send(self, status: int, payload: Mapping[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args: object) -> None:  # quiet: one line per read is printed above
        return


class ReaderServer:
    """Owns the HTTP server and the thread it runs on."""

    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        token: str,
        fetcher: PageFetcher,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        controller: ModeController | None = None,
        writer_token: str | None = None,
        actuator: CommentActuator | None = None,
        act_timeout_s: float = DEFAULT_ACT_TIMEOUT_S,
        allow_inbox: bool = False,
        allow_anonymous: bool = False,
        x_actuator: XWriteActuator | None = None,
        x_min_interval_s: float = DEFAULT_X_MIN_INTERVAL_S,
        platform_cooldown_s: float = DEFAULT_PLATFORM_COOLDOWN_S,
    ) -> None:
        self._server = _ReaderHTTPServer(
            (bind_host, port),
            token=token,
            fetcher=fetcher,
            min_interval_s=min_interval_s,
            timeout_s=timeout_s,
            clock=clock,
            sleep=sleep,
            controller=controller,
            writer_token=writer_token,
            actuator=actuator,
            act_timeout_s=act_timeout_s,
            allow_inbox=allow_inbox,
            allow_anonymous=allow_anonymous,
            x_actuator=x_actuator,
            x_min_interval_s=x_min_interval_s,
            platform_cooldown_s=platform_cooldown_s,
        )
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def bind_host(self) -> str:
        return str(self._server.server_address[0])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="wenext-reader", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def build_reader_from_env(
    env: Mapping[str, str],
    *,
    fetcher: PageFetcher,
    controller: ModeController | None = None,
    actuator: CommentActuator | None = None,
    x_actuator: XWriteActuator | None = None,
) -> ReaderServer | None:
    """A reader when `WENEXT_READER_PORT` is set; `None` when it is not (F0 behaviour).

    Raises `ReaderConfigError` for a port without a token — the caller must refuse to run the
    capsule rather than run it with an open steering endpoint. The comment actuator (D-028) is
    served only when BOTH an actuator object and `WENEXT_WRITER_TOKEN` are present; without the
    token the endpoint answers 404, so a capsule deployed without the secret cannot write. The X
    actuator follows the same rule with the same token, and `WENEXT_X_MIN_INTERVAL_SECONDS` sets
    the pace of X page loads (default `DEFAULT_X_MIN_INTERVAL_S`).
    """
    writer_token = (env.get(ENV_WRITER_TOKEN) or "").strip() or None
    raw_port = (env.get(ENV_PORT) or "").strip()
    if not raw_port:
        return None
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ReaderConfigError(f"{ENV_PORT}={raw_port!r} nie jest liczbą") from exc
    if not 1 <= port <= 65535:
        raise ReaderConfigError(f"{ENV_PORT}={port} poza zakresem portów")
    token = (env.get(ENV_TOKEN) or "").strip()
    if not token:
        raise ReaderConfigError(
            f"{ENV_PORT} jest ustawiony, a {ENV_TOKEN} pusty — czytnik bez tokenu nie startuje, "
            "a kapsuła z czytnikiem bez tokenu nie startuje razem z nim"
        )
    return ReaderServer(
        bind_host=(env.get(ENV_BIND) or DEFAULT_BIND).strip(),
        port=port,
        token=token,
        fetcher=fetcher,
        min_interval_s=_positive_float(env, ENV_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_S),
        timeout_s=_positive_float(env, ENV_TIMEOUT, DEFAULT_TIMEOUT_S),
        controller=controller,
        writer_token=writer_token,
        actuator=actuator if writer_token else None,
        allow_inbox=switch_is_on(env, ENV_ALLOW_INBOX),
        allow_anonymous=switch_is_on(env, ENV_ALLOW_ANONYMOUS),
        # The X actuator rides on the SAME writer token: no token, no X writes either.
        x_actuator=x_actuator if writer_token else None,
        x_min_interval_s=_positive_float(env, ENV_X_MIN_INTERVAL, DEFAULT_X_MIN_INTERVAL_S),
        platform_cooldown_s=_positive_float(
            env, ENV_PLATFORM_COOLDOWN, DEFAULT_PLATFORM_COOLDOWN_S
        ),
    )


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ReaderConfigError(f"{name}={raw!r} nie jest liczbą") from exc
    if value < 0:
        raise ReaderConfigError(f"{name}={value} nie może być ujemne")
    return value


def reader_environment() -> Mapping[str, str]:
    """`os.environ` behind a name, so `main.py` reads the environment in one obvious place."""
    return os.environ
