"""X through the capsule: what the product can really read on this platform, and nothing more.

THE FIRST SENTENCE IS THE BOUNDARY OF THIS MODULE. An entry in the tool catalog is not an
integration (task of 26.09, result 2), so every method here leads to a real read through the
capsule's `/x/read`, or raises an exception that names what is missing. A method returning an
empty list "because it is not done yet" would be a green gate without a measurement (D-007).

WHY A BROWSER AND NOT AN API. X has no entry that returns this data without paid keys, and the
26.09 task forbids buying access. What remains is rendering the page in the capsule's Chrome, in
the profile where the human logged in once.

WHAT NEEDS A SESSION, measured 26.09 through the capsule (the P6 probe of 19.09, "X answers 403
to everything anonymously", STAN-PRAC §8.37, is out of date):

  * `x.com/OpenAI` without a session: HTTP 200 and five real posts. A public profile reads
    without logging in, as a card of text (no separate post body in that view).
  * `x.com/home` without a session: redirect to `/i/jf/onboarding/web?...&mode=login`, the login
    wall. The feed, notifications, search and anything tied to an account need a session.
  * After several reads in a row X began answering 403 to everything, including the public
    profile read a moment earlier. The capsule paces X page loads for that reason; a refusal is
    a RESULT to report and wait on, never a signal to retry.

WHAT THIS MODULE DOES NOT DO. It does not log in or create accounts. It does not write: a reply
and an own post belong to the actuator and go through the action adapter and the delegation
(`control_plane.execution.adapter`), exactly like Reddit. It does not work around platform limits.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from .reddit_browser import READER_TOKEN_HEADER, HttpBrowserReader
from .source import CredentialsMissing, SourceUnavailable

X_ORIGIN = "https://x.com"

#: The capsule's X read route. The literal IS the contract: `browser_supervisor.reader` holds the
#: same value on its side and the two packages do not import each other.
X_READ_PATH = "/x/read"

#: A handle on X: 1 to 15 letters, digits and underscores (the platform's own rule).
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
#: `https://x.com/<handle>/status/<id>`, the only shape of a post address this product accepts.
STATUS_URL = re.compile(r"^https://x\.com/([A-Za-z0-9_]{1,15})/status/(\d{1,25})/?$")


class XPlatformRefused(SourceUnavailable):
    """X refused, and we know with which status code.

    A subclass of `SourceUnavailable`, so every existing caller handles it as before, and a
    caller that wants to behave differently on 403 than on 503 has the number to do it.
    """

    def __init__(self, status: int, url: str, detail: str = "") -> None:
        self.status = status
        self.url = url
        readable = {
            403: "X odmawia odczytu tej przeglądarce (403). To nie jest brak treści ani "
            "wylogowanie: platforma odmawia teraz. Nie obchodzimy tego, czekamy.",
            429: "X ogranicza tempo (429). Trzeba zwolnić i wrócić później.",
        }.get(status, f"X odpowiedział {status}.")
        super().__init__(f"{readable} ({url})" + (f" {detail}" if detail else ""))


def canonical_handle(value: str) -> str:
    """`@Name` or `Name` to `Name`, or `ValueError` when it is not a handle X would accept."""
    handle = (value or "").strip().lstrip("@")
    if not HANDLE.match(handle):
        raise ValueError(f"{value!r} nie jest uchwytem konta X")
    return handle


def parse_status_url(url: str) -> tuple[str, str]:
    """`(handle, status_id)` of an exact post address, or `ValueError`.

    Query strings (`?s=46` from the share sheet) and fragments are dropped before matching,
    because they do not name a different post; anything else is a refusal.
    """
    parts = urlsplit((url or "").strip())
    bare = f"{parts.scheme}://{parts.netloc.lower()}{parts.path}"
    match = STATUS_URL.match(bare)
    if match is None:
        raise ValueError(f"{url!r} nie jest adresem posta X (https://x.com/<konto>/status/<id>)")
    return match.group(1), match.group(2)


def status_url(handle: str, status_id: str) -> str:
    return f"{X_ORIGIN}/{canonical_handle(handle)}/status/{status_id}"


@dataclass(frozen=True)
class XSessionState:
    """Who the capsule is on X RIGHT NOW."""

    logged_in: bool
    handle: str | None
    checked_at: datetime
    #: What got in the way when it could not be established. `None` on a clean read.
    error: str | None = None
    #: True only when X itself showed the login screen: the one case where "sign in again" is the
    #: right sentence. A failed read or an unread handle is not the person's job to fix.
    login_wall: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "logged_in": self.logged_in,
            "handle": self.handle,
            "checked_at": self.checked_at.isoformat(),
            "error": self.error,
            "login_wall": self.login_wall,
        }


@dataclass(frozen=True)
class XPost:
    """One post as the capsule read it. A field that was not on the page stays `None`."""

    id: str | None
    author: str | None
    author_name: str | None
    text: str | None
    #: `post` is the body of the post; `card` is the whole card (name, handle, date, text and
    #: counters), which is all the logged-out view offers. The model must know which it reads.
    text_source: str | None
    permalink: str | None
    published_at: str | None
    #: `focal`, `ancestor` or `reply` on a post page; `None` elsewhere.
    relation: str | None
    #: Counters read from the page: absent means NOT READ, never zero.
    metrics: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> XPost:
        metrics = payload.get("metrics") or {}
        return cls(
            id=_text_or_none(payload.get("id")),
            author=_text_or_none(payload.get("author")),
            author_name=_text_or_none(payload.get("author_name")),
            text=_text_or_none(payload.get("text")),
            text_source=_text_or_none(payload.get("text_source")),
            permalink=_text_or_none(payload.get("permalink")),
            published_at=_text_or_none(payload.get("published_at")),
            relation=_text_or_none(payload.get("relation")),
            metrics={
                str(key): int(value)
                for key, value in metrics.items()
                if isinstance(value, int) and not isinstance(value, bool)
            },
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "author_name": self.author_name,
            "text": self.text,
            "text_source": self.text_source,
            "permalink": self.permalink,
            "published_at": self.published_at,
            "relation": self.relation,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class XPage:
    """One X page, as the capsule returned it."""

    requested_url: str
    final_url: str
    capability: str
    anonymous: bool
    viewer: str | None
    #: X sent us to the login screen. This is an answer about the SESSION.
    login_wall: bool
    #: Session confirmed by a handle. Narrower than "no login wall".
    logged_in: bool
    #: `not_found`, `deleted`, `suspended`, `protected`, `error` or `None`.
    unavailable: str | None
    posts: tuple[XPost, ...]
    profile: dict[str, Any] | None
    text: str
    limitations: tuple[str, ...]

    @property
    def focal(self) -> XPost | None:
        """The post the page is about, on a post page."""
        return next((one for one in self.posts if one.relation == "focal"), None)

    def by_id(self, status_id: str) -> XPost | None:
        return next((one for one in self.posts if one.id == status_id), None)

    def by_author(self, handle: str) -> tuple[XPost, ...]:
        wanted = handle.lstrip("@").lower()
        return tuple(one for one in self.posts if (one.author or "").lower() == wanted)


class XBrowserSource:
    """Reads of X through the capsule's reader.

    Every method builds its address from parts whose shape the platform imposes and hands it to
    the capsule's allowlist. Building it here does not replace that check: the capsule opens the
    browser, so the capsule has the last word and refuses.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
        clock: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        self._clock = clock or (lambda: datetime.now(UTC))
        # HEADFUL BEFORE THE FIRST READ, the way Reddit's adapter does it (D-026). A capsule
        # that restarted boots HEADLESS, and a headless Chrome is the one X is most likely to
        # refuse; the switch to INTERACTIVE + display_only is the capsule's own control call,
        # made through the Reddit reader that already owns it (one counted `.post()`, one place).
        self._prepared = False
        self._control = HttpBrowserReader(base_url, token=token, client=self._client)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- reads -------------------------------------------------------------------------------

    async def home(self) -> XPage:
        """The account's feed. Needs a session; without one X shows the login wall."""
        return await self.read(f"{X_ORIGIN}/home")

    async def notifications(self) -> XPage:
        return await self.read(f"{X_ORIGIN}/notifications")

    async def profile(self, handle: str) -> XPage:
        return await self.read(f"{X_ORIGIN}/{quote(canonical_handle(handle), safe='')}")

    async def profile_replies(self, handle: str) -> XPage:
        return await self.read(
            f"{X_ORIGIN}/{quote(canonical_handle(handle), safe='')}/with_replies"
        )

    async def read_post(self, url: str, *, anonymous: bool = False) -> XPage:
        """One post WITH the conversation around it: X renders ancestors and replies on the
        same page, and the capsule marks which post is focal."""
        handle, status_id = parse_status_url(url)
        return await self.read(status_url(handle, status_id), anonymous=anonymous)

    async def search(self, query: str, *, mode: str = "live") -> XPage:
        """Search. `live` gives the newest posts, `top` the most engaged ones."""
        if mode not in ("live", "top"):
            raise ValueError("tryb wyszukiwania X to live albo top")
        text = (query or "").strip()
        if not text or len(text) > 200:
            raise ValueError("zapytanie X ma od 1 do 200 znaków")
        return await self.read(f"{X_ORIGIN}/search?{urlencode({'q': text, 'f': mode})}")

    # -- identity ----------------------------------------------------------------------------

    async def session_state(self) -> XSessionState:
        """Whether the capsule is logged into X, and as whom. NEVER guesses.

        The handle comes from the "Profile" link in the logged-in navigation, the same place a
        human looks. A failed read comes back as `logged_in=False` with a reason, not as silence:
        result 2 requires a readable handling of a lost login, and silence is not handling.
        """
        now = self._clock()
        try:
            page = await self.home()
        except CredentialsMissing as exc:
            return XSessionState(False, None, now, error=str(exc)[:300], login_wall=True)
        except SourceUnavailable as exc:
            return XSessionState(False, None, now, error=str(exc)[:300])
        if page.logged_in and page.viewer:
            return XSessionState(True, page.viewer, now, error=None)
        # No login wall (that would have raised above) and no handle read. That is NOT a lost
        # login and must not be called one, or the product sends a human to log in every time X
        # changes its navigation.
        return XSessionState(
            logged_in=False,
            handle=None,
            checked_at=now,
            error=(
                "Sesja jest, ale nie odczytano uchwytu konta z nawigacji X"
                + (f" ({'; '.join(page.limitations)})" if page.limitations else "")
                + "."
            ),
        )

    async def verify_identity(self, expected_handle: str) -> XSessionState:
        """Whether the logged-in account is THE account a project expects.

        Separate from `session_state`, because "somebody is logged in" and "the right one is"
        are two questions, and confusing them means publishing from somebody else's account.
        Case-insensitive, because X does not distinguish case in handles.
        """
        state = await self.session_state()
        if not state.logged_in or state.handle is None:
            return state
        expected = expected_handle.lstrip("@")
        if state.handle.lower() != expected.lower():
            return XSessionState(
                logged_in=True,
                handle=state.handle,
                checked_at=state.checked_at,
                error=(
                    f"Przeglądarka agenta jest zalogowana jako @{state.handle}, "
                    f"a projekt oczekuje @{expected}."
                ),
            )
        return state

    # -- transport ---------------------------------------------------------------------------

    async def prepare(self) -> None:
        """Switch the capsule to headful once, before its first use by this source.

        Public because the publisher checks the capsule's mode BEFORE it reads anything: a capsule
        that restarted boots HEADLESS, and a publication waiting for a mode nobody switches would
        wait forever. A failed switch is not reported here: the next read meets the same capsule
        failure and reports it in its own words.
        """
        if self._prepared:
            return
        with contextlib.suppress(SourceUnavailable):
            await self._control.prepare()
        self._prepared = True

    async def read(self, url: str, *, anonymous: bool = False) -> XPage:
        await self.prepare()
        params = {"url": url}
        if anonymous:
            params["viewer"] = "anonymous"
        try:
            response = await self._client.get(
                f"{self._base_url}{X_READ_PATH}",
                params=params,
                headers={READER_TOKEN_HEADER: self._token},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"czytnik kapsuły nieosiągalny ({type(exc).__name__}) pod {self._base_url}"
            ) from exc
        if response.status_code == 401:
            raise SourceUnavailable("czytnik kapsuły odrzucił token (401)")
        if response.status_code == 400:
            raise SourceUnavailable(f"czytnik kapsuły odmówił adresu {url!r}: {_detail(response)}")
        if response.status_code == 503:
            raise SourceUnavailable("przeglądarka kapsuły nie jest dostępna")
        if response.status_code != 200:
            # THE PLATFORM'S CODE WHEN WE HAVE IT. The difference is the agent's decision: 429
            # and 403 mean "stop now", 5xx means "try later", and a capsule failure says nothing
            # about X at all.
            status = _platform_status(response)
            if status is not None:
                raise XPlatformRefused(status, url, _detail(response))
            raise SourceUnavailable(
                f"czytnik kapsuły odpowiedział {response.status_code} dla {url!r}: "
                f"{_detail(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceUnavailable(
                "czytnik kapsuły odpowiedział czymś, co nie jest JSON-em"
            ) from exc
        page = page_from_json(payload, requested=url, anonymous=anonymous)
        if page.login_wall and not anonymous:
            # The login wall is NOT a transport failure: it is an answer about the session.
            # `CredentialsMissing` is how Reddit signals a lost login, so the caller has one
            # pattern for both platforms. The condition is the WALL, not `logged_in`: an unread
            # handle (a slow page, a changed selector) also gives `logged_in=False`, and sending
            # a human to log in for that would be asking for work in answer to our own defect.
            raise CredentialsMissing(
                "Przeglądarka agenta nie ma sesji X: platforma pokazała ekran logowania. "
                "Potrzebne jednorazowe zalogowanie przez kartę „Połącz X”."
            )
        return page


def page_from_json(payload: dict[str, Any], *, requested: str, anonymous: bool) -> XPage:
    """The capsule's JSON as an `XPage`. Pure, so the shape is tested without a network."""
    return XPage(
        requested_url=str(payload.get("requested_url") or requested),
        final_url=str(payload.get("final_url") or ""),
        capability=str(payload.get("capability") or ""),
        anonymous=bool(payload.get("anonymous", anonymous)),
        viewer=_text_or_none(payload.get("viewer")),
        login_wall=bool(payload.get("login_wall")),
        logged_in=bool(payload.get("logged_in")),
        unavailable=_text_or_none(payload.get("unavailable")),
        posts=tuple(
            XPost.from_json(one) for one in (payload.get("posts") or ()) if isinstance(one, dict)
        ),
        profile=payload.get("profile") if isinstance(payload.get("profile"), dict) else None,
        text=str(payload.get("text") or ""),
        limitations=tuple(str(one) for one in (payload.get("limitations") or ())),
    )


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _platform_status(response: httpx.Response) -> int | None:
    """The code the PLATFORM answered with, if the capsule passed it on."""
    try:
        value = response.json().get("platform_status")
    except ValueError:
        return None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", ""))[:300]
    except ValueError:
        return response.text[:300]
