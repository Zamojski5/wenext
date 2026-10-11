"""Reddit through the capsule's Chrome — the founder's logged-in session, behind the same port.

Why (D-025, `docs/OUTCOMES.md` §6 point 6): Reddit refused to register a Data API application
and the founder decided the observer should read Reddit "like me on Chrome — I type the password,
the agent reads". So this adapter does not hold credentials, tokens or cookies. It asks the
capsule's reader (`services/browser_supervisor/browser_supervisor/reader.py`) for the body of one
URL; the reader navigates the capsule's own Chrome — with whatever session that Chrome's profile
holds — and returns what the browser received. If a human logged the profile into Reddit, every
read here is a read by that account. If nobody did, the read is anonymous, and the adapter says so
instead of pretending (`require_login`).

The payload shape is the Data API's (`Listing → data.children[] → data`), because Reddit serves
the same JSON to a browser at `www.reddit.com/r/<sub>/new.json`. Parsing is therefore shared with
`RedditSource` through `reddit_payload.py`; only the transport differs.

THE ONE RULE ABOUT ERRORS holds here too (`source.py`): a read that did not happen raises, never
returns `()`. A reader that is down, a token that does not match, a 403 from Reddit — each is a
`SourceUnavailable` the operator sees as a failed attempt, not a quiet day. A profile that is not
logged in (when login is required) is `CredentialsMissing`: waiting does not help, a human must act.

What this module never does: `.post()`, clicking, typing, evaluating JavaScript. The reader's
allowlist is the second lock on the same door — an adapter bug cannot make the capsule fetch
anything but the three URL shapes named there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx
from community_os_contracts import AccessMode, ObservationPlatform

from .models import CommunityRef, ObservedThread, ThreadSnapshot
from .reddit_payload import (
    parse_listing_threads,
    parse_thread_pair,
    require_post_id,
    subreddit_name,
)
from .source import (
    MAX_LISTING,
    CredentialsMissing,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)

#: The header the capsule's reader expects. Spelled once here and once in `reader.py`; the two
#: are pinned together by `tests/test_reddit_browser.py::test_the_token_header_matches_the_reader`.
READER_TOKEN_HEADER = "X-Wenext-Reader-Token"

#: Environment variable names, spelled the way `.env.example` and `docker-compose.yml` use them.
ENV_READER_URL = "WENEXT_BROWSER_READER_URL"
ENV_READER_TOKEN = "WENEXT_BROWSER_READER_TOKEN"
ENV_REQUIRE_LOGIN = "WENEXT_REDDIT_REQUIRE_LOGIN"

WWW = "https://www.reddit.com"
ME_URL = f"{WWW}/api/me.json?raw_json=1"
#: The account's inbox. Served by the capsule's reader only while it runs with
#: `WENEXT_READER_ALLOW_INBOX=1`; without that switch every read of this URL comes back 400 and
#: this adapter reports it as unavailable rather than as an empty inbox.
INBOX_URL = f"{WWW}/message/inbox.json?raw_json=1&limit=100"

#: `viewer` of a read, as the capsule's reader spells it. `session` is the profile's login (the
#: default everywhere); `anonymous` is a fresh context with no session, and the only read that can
#: tell us whether a published comment is visible to anyone but its author (STAN-PRAC §8.22).
VIEWER_SESSION = "session"
VIEWER_ANONYMOUS = "anonymous"

#: Who AutoModerator is, and the phrases its removal notice uses. Deliberately narrow: a wider
#: match would let any AutoModerator reply (a sticky welcome, a rule reminder) be recorded as
#: "the platform removed our comment", and a removal report is not a thing to guess at.
AUTOMODERATOR = "automoderator"
REMOVAL_PHRASES = ("automatically removed", "has been removed", "was removed")

#: The comment tree parameters — identical to the OAuth adapter so both produce the same snapshot.
COMMENTS_LIMIT = 500
COMMENTS_DEPTH = 10

#: How long a positive login check is trusted before `/api/me.json` is read again. A session that
#: expires mid-window shows up as the next read's redirect or `{}` anyway.
LOGIN_CHECK_TTL = timedelta(minutes=10)

#: How long "the capsule is headful" is trusted before `/state` is asked again (D-026). A capsule
#: that restarted into HEADLESS in between shows up as a 403 and resets the mark anyway.
PREPARE_TTL = timedelta(minutes=10)

#: Waiting for the reader includes the reader's own spacing between fetches and Chrome's
#: navigation, so this is generous on purpose. The dispatcher's heartbeat keeps the job's lease.
READER_TIMEOUT = httpx.Timeout(90.0, connect=10.0)

#: The compose service names the reader client may address (D-027). The one `.post()` in this
#: file goes to `{self._base_url}/mode`; the static scanner in `control_plane` proves the address
#: is rooted in `_base_url`, and this check proves `_base_url` is our capsule — so
#: `WENEXT_BROWSER_READER_URL` pointing anywhere else fails at construction with its reason.
INTERNAL_CONTROL_HOSTS: frozenset[str] = frozenset({"capsule", "gateway", "capsule-x", "gateway-x"})


def internal_reader_url(base_url: str) -> str:
    """`base_url` without a trailing slash, or `ValueError` when its host is not one of ours."""
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "http" or parsed.hostname not in INTERNAL_CONTROL_HOSTS:
        raise ValueError(
            f"czytnik kapsuły: adres bazowy {base_url!r} nie wskazuje usługi compose z listy "
            f"{sorted(INTERNAL_CONTROL_HOSTS)} (D-027) — klient odmawia startu"
        )
    return base_url.strip().rstrip("/")


#: Reddit's `reason` values for a subreddit the current account may not read. Not transient —
#: a human has to join, be invited, or accept the quarantine; the job parks.
_ACCESS_REASONS = frozenset({"private", "quarantined", "gold_only", "gated", "banned"})

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PageRead:
    """What the capsule's Chrome received for one URL: status, where it ended up, and the body."""

    status: int
    final_url: str
    content_type: str | None
    body: str


@dataclass(frozen=True)
class RemovalReport:
    """The platform telling us one of our comments is gone, in a message we did not have to ask
    a thread for. Today: an AutoModerator reply in the account's inbox.

    `comment_external_id` is the `t1_…` of OUR comment (the reply's `parent_id`), which is the id
    `execution_evidence` recorded at publication, so a report can be matched to a request without
    guessing. Defined here rather than in the control plane because it describes what the platform
    said, and one definition of that is enough.
    """

    comment_external_id: str
    reported_at: datetime
    note: str


@runtime_checkable
class BrowserReader(Protocol):
    """One operation: navigate the capsule's Chrome to `url` and return the response. Read-only."""

    async def read(self, url: str) -> PageRead: ...


class HttpBrowserReader:
    """`BrowserReader` over the reader's HTTP endpoint inside the capsule (`GET /read?url=…`)."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = internal_reader_url(base_url)
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=READER_TIMEOUT)

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        await self._client.aclose()

    async def read(self, url: str) -> PageRead:
        return await self._read(url, viewer=VIEWER_SESSION)

    async def read_anonymously(self, url: str) -> PageRead:
        """The same read, through a fresh browser context with no session.

        A separate method rather than a flag on `read`, so that a caller cannot get an anonymous
        page while believing it asked for the account's view, or the other way round. Discovered
        by `getattr` at the call site, like `prepare()`: a fake reader in a test that does not
        implement it makes the anonymous path unavailable, not silently logged-in.
        """
        return await self._read(url, viewer=VIEWER_ANONYMOUS)

    async def inspect_public_comment(self, url: str, external_id: str) -> Mapping[str, Any]:
        """Rendered public permalink, without the account's cookies or a write capability."""
        try:
            response = await self._client.get(
                f"{self._base_url}/research",
                params={"url": url, "javascript": "true", "target_comment_id": external_id},
                headers={READER_TOKEN_HEADER: self._token},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable("Publiczny widok komentarza jest niedostępny.") from exc
        if response.status_code != 200:
            raise SourceUnavailable(
                f"Publiczny widok komentarza zwrócił {response.status_code}: "
                f"{_reader_detail(response)}"
            )
        try:
            page = response.json()
        except ValueError as exc:
            raise SourceUnavailable("Publiczny widok komentarza nie jest JSON-em.") from exc
        if (
            page.get("viewer") != VIEWER_ANONYMOUS
            or page.get("requested_url") != url
            or page.get("status") != 200
        ):
            raise SourceUnavailable("Brak potwierdzenia anonimowego odczytu permalinku.")
        page.pop("screenshot", None)
        return page

    async def _read(self, url: str, *, viewer: str) -> PageRead:
        try:
            response = await self._client.get(
                f"{self._base_url}/read",
                params={"url": url, "viewer": viewer},
                headers={READER_TOKEN_HEADER: self._token},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"czytnik kapsuły nieosiągalny ({type(exc).__name__}) pod {self._base_url} — "
                "czy kapsuła chodzi i ma ustawiony WENEXT_READER_PORT?"
            ) from exc
        detail = _reader_detail(response)
        if response.status_code == 401:
            raise SourceUnavailable(
                "czytnik kapsuły odrzucił token (401) — WENEXT_BROWSER_READER_TOKEN różni się "
                "między `api` i kapsułą"
            )
        if response.status_code == 400:
            raise SourceUnavailable(f"czytnik kapsuły odrzucił URL {url!r}: {detail}")
        if response.status_code != 200:
            raise SourceUnavailable(
                f"czytnik kapsuły odpowiedział {response.status_code} dla {url!r}: {detail}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceUnavailable(
                "czytnik kapsuły odpowiedział czymś, co nie jest JSON-em"
            ) from exc
        if viewer == VIEWER_ANONYMOUS and _viewer_of(payload) != VIEWER_ANONYMOUS:
            # A capsule image from before this parameter existed ignores it and reads through the
            # account's session. Accepting that answer would record the author's own view as a
            # public-visibility verdict, which is the mistake of 17.09 with a new label on it.
            # The read is reported as unavailable instead, and the request stays UNVERIFIED.
            raise SourceUnavailable(
                "czytnik kapsuły nie potwierdził odczytu anonimowego (brak `viewer=anonymous` "
                "w odpowiedzi): obraz kapsuły sprzed tej zmiany albo wyłączony przełącznik "
                "WENEXT_READER_ALLOW_ANONYMOUS"
            )
        return _page_read(payload)

    async def state(self) -> Mapping[str, Any]:
        """The capsule's mode surface as it is NOW — read-only, for probes and the operator's
        card."""
        return await self._control("GET", "/state")

    async def prepare(self) -> Mapping[str, Any]:
        """Makes sure the capsule's Chrome is headful before a read (D-026).

        Reddit answers 403 to `HeadlessChrome` whatever the account (measured 2026-09-03/04), so
        this platform's adapter asks the capsule for INTERACTIVE with `display_only=true` — a
        display to render on, no human steer — whenever `/state` says HEADLESS. A capsule already
        in INTERACTIVE (display-only or with a human at the screen) is left alone: it reads fine.
        Returns the capsule state after the check.
        """
        state = await self._control("GET", "/state")
        if state.get("mode") == CAPSULE_MODE_INTERACTIVE:
            return state
        return await self._control(
            "POST", "/mode", {"mode": CAPSULE_MODE_INTERACTIVE, "display_only": True}
        )

    async def _control(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        headers = {READER_TOKEN_HEADER: self._token}
        url = f"{self._base_url}{path}"
        try:
            if method == "POST":
                # `.post()` on purpose, not `request("POST")`: the bypass scanner's vocabulary sees
                # the former, and this file is named with its exact count of such calls in
                # `control_plane/execution/bypass_scan.py::PERMITTED_INTERNAL_CONTROL` (D-026).
                response = await self._client.post(url, json=body, headers=headers)
            else:
                response = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"czytnik kapsuły nieosiągalny ({type(exc).__name__}) pod {self._base_url}{path}"
            ) from exc
        if response.status_code == 401:
            raise SourceUnavailable(
                f"czytnik kapsuły odrzucił token (401) na {path} — WENEXT_BROWSER_READER_TOKEN "
                "różni się między `api` i kapsułą"
            )
        if response.status_code == 404:
            raise SourceUnavailable(
                f"kapsuła nie ma kontrolera trybu ({path} → 404) — obraz kapsuły sprzed D-026?"
            )
        if response.status_code != 200:
            raise SourceUnavailable(
                f"kapsuła odpowiedziała {response.status_code} na {method} {path}: "
                f"{_reader_detail(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceUnavailable(f"kapsuła: odpowiedź na {path} nie jest JSON-em") from exc
        if not isinstance(payload, Mapping):
            raise SourceUnavailable(f"kapsuła: odpowiedź na {path} nie jest obiektem")
        return payload


#: The capsule's mode names, as `browser_supervisor` spells them (`CapsuleMode` in contracts).
CAPSULE_MODE_INTERACTIVE = "INTERACTIVE"


def _reader_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, Mapping):
        return str(payload.get("detail") or payload.get("error") or payload)[:300]
    return str(payload)[:300]


def _viewer_of(payload: Any) -> str | None:
    """The `viewer` the reader says it used, or `None` when it did not say."""
    if isinstance(payload, Mapping):
        viewer = payload.get("viewer")
        if isinstance(viewer, str):
            return viewer.strip().lower()
    return None


def _page_read(payload: Any) -> PageRead:
    if not isinstance(payload, Mapping):
        raise SourceUnavailable("czytnik kapsuły: odpowiedź nie jest obiektem")
    status = payload.get("status")
    final_url = payload.get("final_url")
    body = payload.get("body")
    if not isinstance(status, int) or isinstance(status, bool):
        raise SourceUnavailable("czytnik kapsuły: brak liczbowego `status` w odpowiedzi")
    if not isinstance(final_url, str) or not isinstance(body, str):
        raise SourceUnavailable("czytnik kapsuły: brak `final_url` albo `body` w odpowiedzi")
    content_type = payload.get("content_type")
    return PageRead(
        status=status,
        final_url=final_url,
        content_type=content_type if isinstance(content_type, str) else None,
        body=body,
    )


@dataclass(frozen=True)
class SessionState:
    """What `/api/me.json` said about the capsule's Reddit session at `checked_at`."""

    logged_in: bool
    username: str | None
    checked_at: datetime


class RedditBrowserSource:
    """`CommunitySource` for Reddit through the capsule's Chrome. Read-only by construction."""

    platform = ObservationPlatform.REDDIT
    access_mode = AccessMode.ACCOUNT_REQUIRED

    def __init__(
        self,
        reader: BrowserReader,
        *,
        require_login: bool = True,
        clock: Clock = _utc_now,
        login_check_ttl: timedelta = LOGIN_CHECK_TTL,
    ) -> None:
        self._reader = reader
        self._require_login = require_login
        self._clock = clock
        self._login_check_ttl = login_check_ttl
        self._session: SessionState | None = None
        #: When the capsule was last confirmed headful (D-026); `None` = not yet this process.
        self._prepared_at: datetime | None = None

    @property
    def require_login(self) -> bool:
        return self._require_login

    async def aclose(self) -> None:
        aclose = getattr(self._reader, "aclose", None)
        if aclose is not None:
            await aclose()

    # -- port -----------------------------------------------------------------

    def describe(self, community_key: str) -> CommunityRef:
        name = subreddit_name(community_key)
        return CommunityRef(
            platform=self.platform,
            key=f"r/{name}",
            display_name=f"Reddit · r/{name}",
            access_mode=self.access_mode,
        )

    async def list_recent_threads(
        self, community_key: str, *, since: datetime | None, limit: int = MAX_LISTING
    ) -> tuple[ObservedThread, ...]:
        name = subreddit_name(community_key)
        if not 1 <= limit <= MAX_LISTING:
            raise ValueError(f"limit={limit} poza zakresem 1..{MAX_LISTING}")
        if since is not None and since.tzinfo is None:
            raise ValueError("since musi mieć strefę czasową")
        await self._ensure_logged_in()
        where = f"/r/{name}/new.json"
        payload = await self._read_json(f"{WWW}{where}?raw_json=1&limit={limit}", where=where)
        return parse_listing_threads(
            payload,
            community_key=f"r/{name}",
            observed_at=self._clock(),
            since=since,
            where=where,
        )

    async def fetch_thread(self, community_key: str, external_id: str) -> ThreadSnapshot:
        name = subreddit_name(community_key)
        require_post_id(external_id)
        await self._ensure_logged_in()
        where = f"/r/{name}/comments/{external_id}.json"
        payload = await self._read_json(
            f"{WWW}{where}?raw_json=1&limit={COMMENTS_LIMIT}&depth={COMMENTS_DEPTH}&sort=old",
            where=where,
            not_found=external_id,
        )
        return parse_thread_pair(
            payload,
            community_key=f"r/{name}",
            external_id=external_id,
            observed_at=self._clock(),
            where=where,
        )

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        """The thread's raw JSON pair as Reddit returns it, for callers that need fields the
        `ThreadSnapshot` does not carry (`locked`, `archived`, the score of one comment, the
        author of each comment). Same read, same allowlisted URL as `fetch_thread`; read-only."""
        name = subreddit_name(community_key)
        require_post_id(external_id)
        await self._ensure_logged_in()
        where = f"/r/{name}/comments/{external_id}.json"
        return await self._read_json(
            f"{WWW}{where}?raw_json=1&limit={COMMENTS_LIMIT}&depth={COMMENTS_DEPTH}&sort=old",
            where=where,
            not_found=external_id,
        )

    # -- is it visible to anyone but us (P0, STAN-PRAC §8.22) ------------------

    async def read_thread_payload_anonymously(self, community_key: str, external_id: str) -> Any:
        """The same thread JSON as `read_thread_payload`, read WITHOUT the account's session.

        Deliberately does NOT call `_ensure_logged_in`: being logged out is the entire point.
        It does call `_ensure_prepared`, because Reddit answers 403 to a headless Chrome whatever
        the session (D-026), and a 403 here must stay "we could not look", never "it is gone".

        Raises `SourceUnavailable` when the capsule's reader has no anonymous path, rather than
        falling back to the logged-in read: a fallback would answer a different question than the
        caller asked, and the caller would record the answer as a public-visibility verdict.
        """
        read_anonymously = getattr(self._reader, "read_anonymously", None)
        if read_anonymously is None:
            raise SourceUnavailable(
                "czytnik kapsuły nie umie odczytu anonimowego (brak `read_anonymously`) — "
                "widoczność publiczna pozostaje NIESPRAWDZONA"
            )
        name = subreddit_name(community_key)
        require_post_id(external_id)
        await self._ensure_prepared()
        where = f"/r/{name}/comments/{external_id}.json"
        url = f"{WWW}{where}?raw_json=1&limit={COMMENTS_LIMIT}&depth={COMMENTS_DEPTH}&sort=old"
        page = await read_anonymously(url)
        return self._json_of(page, where=where, not_found=external_id)

    async def read_comment_publicly(
        self, community_key: str, thread_external_id: str, comment_external_id: str
    ) -> Mapping[str, Any]:
        """Find one exact comment element in the rendered, logged-out permalink."""
        inspect_comment = getattr(self._reader, "inspect_public_comment", None)
        if inspect_comment is None:
            raise SourceUnavailable("Czytnik nie ma publicznego widoku pojedynczego komentarza.")
        name = subreddit_name(community_key)
        require_post_id(thread_external_id)
        if re.fullmatch(r"t1_[a-z0-9]{1,12}", comment_external_id) is None:
            raise ValueError("Nieprawidłowy identyfikator komentarza.")
        url = (
            f"{WWW}/r/{name}/comments/{thread_external_id}/comment/"
            f"{comment_external_id[3:]}/"
        )
        return await inspect_comment(url, comment_external_id)

    async def read_removal_reports(self) -> tuple[RemovalReport, ...]:
        """AutoModerator's removal notices from the account's inbox, newest first.

        Read AS THE ACCOUNT (the inbox is the account's), so `_ensure_logged_in` applies. Every
        other message in the inbox is ignored: a reply from a person, a mod note that does not say
        the comment was removed, a message with no `parent_id` we could attribute anything to.
        """
        await self._ensure_logged_in()
        payload = await self._read_json(INBOX_URL, where="/message/inbox.json")
        return parse_removal_reports(payload)

    # -- session --------------------------------------------------------------

    async def session_state(self) -> SessionState:
        """Reads `/api/me.json` NOW: an account object with a `name` means logged in.

        The browser-facing shape, RECORDED 2026-09-04 through the capsule (fixture
        `reddit_browser_me.json`), is `{"kind": "t2", "data": {"name": …, "id": …, …}, "loid": …}`
        — the name sits under `data`, not at the top level. The first version of this method read
        the top level only and reported a logged-in capsule as anonymous; the probe artifact said
        `zalogowany: false` while the profile held a `reddit_session` cookie. Both shapes are
        accepted now, and `{}` (no account) stays anonymous.

        Public so a probe can report the session honestly next to its read — "through the
        capsule's Chrome" does not by itself mean "as the founder's account".
        """
        payload = await self._read_json(ME_URL, where="/api/me.json")
        username = _account_name(payload)
        logged_in = username is not None
        state = SessionState(
            logged_in=logged_in, username=username if logged_in else None, checked_at=self._clock()
        )
        self._session = state
        return state

    async def _ensure_logged_in(self) -> None:
        if not self._require_login:
            return
        cached = self._session
        if (
            cached is not None
            and cached.logged_in
            and self._clock() - cached.checked_at < self._login_check_ttl
        ):
            return
        state = await self.session_state()
        if not state.logged_in:
            raise CredentialsMissing(
                "Chrome kapsuły nie jest zalogowany do Reddita (`/api/me.json` bez nazwy konta). "
                "Zaloguj konto w kapsule (ścieżka logowania — docs/OUTCOMES.md §8) albo ustaw "
                f"{ENV_REQUIRE_LOGIN}=false, żeby czytać anonimowo. Bez tego adapter nie czyta — "
                "i nie udaje, że community jest puste."
            )

    async def _ensure_prepared(self) -> None:
        """Before any read: the capsule must be headful, because Reddit refuses headless (D-026).

        Asks the reader's `prepare()` when it has one (`HttpBrowserReader`; a fake in tests may
        not) — once per `PREPARE_TTL`, and again after a 403, because a 403 is what a capsule that
        fell back to HEADLESS looks like from here.
        """
        prepare = getattr(self._reader, "prepare", None)
        if prepare is None:
            return
        now = self._clock()
        if self._prepared_at is not None and now - self._prepared_at < PREPARE_TTL:
            return
        await prepare()
        self._prepared_at = now

    # -- transport ------------------------------------------------------------

    async def _read_json(self, url: str, *, where: str, not_found: str | None = None) -> Any:
        await self._ensure_prepared()
        page = await self._reader.read(url)
        return self._json_of(page, where=where, not_found=not_found)

    def _json_of(self, page: PageRead, *, where: str, not_found: str | None = None) -> Any:
        """One page's body as JSON, or the right exception. Split out of `_read_json` so the
        anonymous read answers 403, 429 and a login redirect EXACTLY as the logged-in one does:
        two copies of this ladder would be two opinions about what a 403 means, and the anonymous
        copy is the one whose wrong answer becomes "the comment was removed"."""
        if page.status == 403:
            # Forget the "headful confirmed" mark: if the capsule restarted into HEADLESS, the
            # next read asks `prepare()` again instead of failing until the TTL runs out.
            self._prepared_at = None
        if urlsplit(page.final_url).path.rstrip("/").endswith("/login"):
            raise CredentialsMissing(
                f"Reddit przekierował {where} na stronę logowania — sesja w kapsule wygasła albo "
                "nigdy nie istniała"
            )
        if page.status == 404:
            if not_found is not None:
                raise ThreadNotFound(f"Reddit nie zna posta {not_found}")
            raise UnknownCommunity(f"Reddit odpowiedział 404 na {where} — subreddit nie istnieje")
        if page.status == 403:
            reason = _json_reason(page.body)
            if reason in _ACCESS_REASONS:
                raise CredentialsMissing(
                    f"Reddit odmawia dostępu do {where} (reason={reason}) — konto w kapsule nie ma "
                    "wstępu do tego community; to nie minie samo"
                )
            raise SourceUnavailable(
                f"Reddit odpowiedział 403 na {where} bez powodu w JSON — możliwa blokada "
                f"anty-bot; ciało: {page.body[:120]!r}"
            )
        if page.status == 429 or page.status >= 500:
            raise SourceUnavailable(f"Reddit odpowiedział {page.status} na {where}")
        if page.status != 200:
            raise SourceUnavailable(
                f"Reddit odpowiedział {page.status} na {where}: {page.body[:200]!r}"
            )
        try:
            return json.loads(page.body)
        except ValueError as exc:
            hint = (
                " — ciało wygląda na HTML (przekierowanie albo strona blokady)"
                if page.body.lstrip().startswith("<")
                else ""
            )
            raise SourceUnavailable(f"Reddit: odpowiedź na {where} nie jest JSON-em{hint}") from exc


def parse_removal_reports(payload: Any) -> tuple[RemovalReport, ...]:
    """AutoModerator removal notices out of an inbox listing, newest first.

    The shape is Reddit's usual listing: `{"data": {"children": [{"kind": "t1", "data": {…}}]}}`,
    each message carrying `author`, `body`, `parent_id` and `created_utc`. A message counts only
    when ALL of it holds: the author is AutoModerator, the body says the comment was removed, and
    there is a `t1_…` parent to attribute it to. Anything else is skipped in silence, because the
    inbox is full of ordinary mail and a removal report is not a thing to infer.
    """
    if not isinstance(payload, Mapping):
        raise SourceUnavailable("skrzynka: odpowiedź nie jest obiektem")
    data = payload.get("data")
    children = data.get("children") if isinstance(data, Mapping) else None
    if not isinstance(children, list):
        raise SourceUnavailable("skrzynka: brak `data.children` w odpowiedzi")
    reports: list[RemovalReport] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        data = child.get("data")
        if not isinstance(data, Mapping):
            continue
        if str(data.get("author") or "").strip().lower() != AUTOMODERATOR:
            continue
        body = str(data.get("body") or "")
        if not any(phrase in body.lower() for phrase in REMOVAL_PHRASES):
            continue
        parent = str(data.get("parent_id") or "")
        if not parent.startswith("t1_"):
            continue
        created = data.get("created_utc")
        if not isinstance(created, (int, float)) or isinstance(created, bool):
            continue
        reports.append(
            RemovalReport(
                comment_external_id=parent,
                reported_at=datetime.fromtimestamp(float(created), tz=UTC),
                note=" ".join(body.split())[:500],
            )
        )
    reports.sort(key=lambda r: r.reported_at, reverse=True)
    return tuple(reports)


def _account_name(payload: Any) -> str | None:
    """The account name in a `/api/me.json` body, or `None` for an anonymous session.

    Browser shape: `{"kind": "t2", "data": {"name": …}}` (recorded). Data API shape: `{"name": …}`
    (documented). Anything else — `{}`, a list, a `data` without `name` — is anonymous.
    """
    if not isinstance(payload, Mapping):
        return None
    candidates: list[Any] = [payload.get("name")]
    data = payload.get("data")
    if isinstance(data, Mapping):
        candidates.insert(0, data.get("name"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _json_reason(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    reason = payload.get("reason") if isinstance(payload, Mapping) else None
    return reason if isinstance(reason, str) else None


def require_login_from_env(env: Mapping[str, str]) -> bool:
    """`WENEXT_REDDIT_REQUIRE_LOGIN`: anything but an explicit `false`/`0`/`no` requires login."""
    value = (env.get(ENV_REQUIRE_LOGIN) or "true").strip().lower()
    return value not in {"false", "0", "no"}


def build_from_env(env: Mapping[str, str]) -> RedditBrowserSource | None:
    """The browser adapter when `WENEXT_BROWSER_READER_URL` is set; `None` otherwise.

    An empty token is not rejected here on purpose: the reader answers 401 and the adapter turns
    that into a visible `SourceUnavailable` naming the variable — a silent fallback to the OAuth
    adapter would hide a misconfigured capsule behind `CredentialsMissing` for the wrong reason.
    """
    url = (env.get(ENV_READER_URL) or "").strip()
    if not url:
        return None
    reader = HttpBrowserReader(url, token=(env.get(ENV_READER_TOKEN) or "").strip())
    return RedditBrowserSource(reader, require_login=require_login_from_env(env))
