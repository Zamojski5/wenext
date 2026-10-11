"""The capsule's X actuator: a reply under a post and a post of our own, typed into x.com like a
person at the keyboard. The pattern of `writer.py` (D-028), on a second platform.

Reached only through `POST /act/x/reply` and `POST /act/x/post` on the reader's server, behind the
writer's token and the SAME `_act_allowed` gate as `/act/comment` (INTERACTIVE + display_only, the
human steer closed, Chrome alive). Those endpoints are called only by the X ports inside
`control_plane.execution.adapter`, which run only under `SideEffectAdapter.perform` with a consumed
`ExecutionIntent` (D-027).

HOW. The capsule's own Chrome, in the persistent profile where a human logged the account in,
opens the post (or the home timeline), checks that X's navigation shows the EXPECTED account, types
the consented text into X's composer and clicks its button. Then it waits for the platform to show
the new post and reads the post's id and permalink OFF THE PAGE: the "View" link of X's toast, or a
post by our account that was not on the page before the click. What was sent is never the
evidence; what the page shows afterwards is.

THREE OUTCOMES, the classes of `writer.py`, because the queue treats them differently:

  * refused (`WriteRefused`): nothing was submitted. EVERY failure before the click lands here.
  * blocked (`WriteBlocked`): X said no in a way a retry will not change (another account in the
    session, a locked or suspended account, a rate limit). The queue must STOP.
  * unknown (`WriteUnknown`): the button was clicked and no evidence could be read back in time.
    The post may exist. NEVER retried here; the control plane reconciles by reading X.

`submit_started` is set immediately BEFORE the click, from the actuator's own state, so a failure
between the click and the next line cannot be read as "nothing sent", and no refusal can leave this
module after the click (the wrappers turn one into an unknown).

WHAT IS UNVERIFIED, said here and not in a footnote: the capsule's profile has no X session yet,
so every selector of the logged-in application (the composer, its button, the toast) comes from
the well-known X DOM, not from a measured page. They live in ONE block (`x_reader.SEL_*`) and every
miss comes back as an explicit refusal or an unknown, never as an invented success.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from .writer import WriteBlocked, WriteRefused, WriteUnknown
from .x_reader import (
    CAPABILITY_POST,
    HANDLE,
    MAX_POSTS,
    MAX_STATUS_LINKS_PER_POST,
    READ_TIMEOUT_MS,
    SEL_ACCOUNT_SWITCHER,
    SEL_ARTICLE,
    SEL_ATTACHMENTS,
    SEL_BODY,
    SEL_COMPOSER_TEXTBOX,
    SEL_FILE_INPUT,
    SEL_NAV_PROFILE_LINK,
    SEL_POST_TEXT,
    SEL_POST_USER_NAME,
    SEL_STATUS_LINK,
    SEL_SUBMIT_INLINE,
    SEL_TOAST,
    SEL_TOAST_STATUS_LINK,
    X_HOST,
    X_ORIGIN,
    canonical_permalink,
    detect_unavailable,
    handle_from_profile_href,
    handle_from_switcher_text,
    is_account_locked,
    is_login_wall,
    normalize_page_text,
    normalized_for_comparison,
    own_permalink,
    parse_status_path,
    safe_all_texts,
    safe_attribute,
    safe_attributes,
    safe_count,
    safe_text,
    text_outside_posts,
)

#: X's limit, counted the way X counts it (twitter-text v3): a URL weighs `URL_WEIGHT` whatever
#: its length, code points in `LIGHT_RANGES` weigh 1 and every other one weighs 2 (CJK, emoji).
MAX_POST_CHARS = 280
URL_WEIGHT = 23
LIGHT_RANGES: tuple[tuple[int, int], ...] = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))

HOME_URL = f"{X_ORIGIN}/home"

#: Time budget. Everything up to the click shares ONE deadline (`timeout_s` of the request); after
#: the click the evidence gets at least `MIN_EVIDENCE_S`, so a slow page before the click cannot
#: turn a published post into an unknown. Worst case stays under the adapter's HTTP timeout (75 s)
#: with the reader's default act timeout (45 s).
NAV_TIMEOUT_S = 20.0
STEP_TIMEOUT_S = 10.0
CLICK_TIMEOUT_S = 5.0
ENABLE_WAIT_S = 3.0
MIN_EVIDENCE_S = 20.0
PROFILE_FALLBACK_S = 15.0
POLL_S = 1.0
PRE_CLICK_POLL_S = 0.5
TYPE_DELAY_MS = 15
#: One image under an own post (27.09: the founder wants news shown with a screenshot). X takes
#: bigger files, the bound is ours: a screenshot of a page is well under it.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: The only types accepted, each with the first bytes its file must start with. The type named by
#: the caller is checked against the bytes, so a mislabelled file is refused, not uploaded.
IMAGE_SIGNATURES: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}
#: How long X may take to take the image in (preview shown, button live again).
UPLOAD_TIMEOUT_S = 60.0
#: A post counts as ours-and-new only if its id was minted after the click, minus this skew
#: between the capsule's clock and X's.
RECENCY_SKEW_MS = 120_000
#: X's ids are snowflakes: `(id >> 22) + SNOWFLAKE_EPOCH_MS` is the moment the id was minted.
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657

#: Page text meaning the ACCOUNT is in trouble, matched outside the posts (lower case, apostrophes
#: normalised). UNVERIFIED wording; tune live together with the selectors.
ACCOUNT_BLOCKED_MARKERS: tuple[str, ...] = (
    "your account is suspended",
    "your account has been suspended",
    "your account has been locked",
    "your account is locked",
    "rate limit exceeded",
    "you are over the daily limit",
)
#: A toast after the click that means "stop the queue".
BLOCKED_TOAST_MARKERS: tuple[str, ...] = (
    "daily limit",
    "rate limit",
    "suspended",
    "locked",
    "might be automated",
    "can't complete this action right now",
)
#: A toast after the click that means "X showed an error": the outcome is unknown, reported at
#: once instead of after the whole evidence window.
FAILED_TOAST_MARKERS: tuple[str, ...] = (
    "something went wrong",
    "you already said that",
    "couldn't",
    "could not",
    "not sent",
    "try again",
)

NOT_LOGGED_IN = "strona nie pokazuje zalogowanego konta"
_HANDLE_RE = re.compile(rf"^{HANDLE}$")
_POST_ID_RE = re.compile(r"^\d{1,25}$")
#: Anything that looks like a URL or a bare domain, deliberately wider than X's own detector: a
#: token counted as a URL when X would not count it overestimates the length (a refusal too many),
#: while the opposite would let an over-limit text through to a composer that rejects it.
_URL_LIKE = re.compile(
    r"(?:https?://)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
    r"(?::\d{1,5})?(?:/\S*)?"
)
#: X truncates the visible text of long links with it.
_ELLIPSIS = chr(0x2026)


# -- pure rules ------------------------------------------------------------------------------------


def x_weighted_length(text: str) -> int:
    """The length X's counter shows for `text`, or more, never less (see `_URL_LIKE`)."""
    total = 0
    position = 0
    for match in _URL_LIKE.finditer(text):
        total += _weight(text[position : match.start()]) + URL_WEIGHT
        position = match.end()
    return total + _weight(text[position:])


def _weight(chars: str) -> int:
    return sum(1 if any(low <= ord(c) <= high for low, high in LIGHT_RANGES) else 2 for c in chars)


def check_text(text: str) -> None:
    """Refuse a text X would not take, BEFORE anything is typed.

    Two rules, both applied: the plain length and X's weighted length. The weighted one catches a
    short link counted as 23 and a text in a non-Latin script counted double; the plain one bounds
    whatever the weighted estimate gets wrong. A composer over the limit keeps its button disabled,
    and a click on a disabled button would be an unknown for something that was never sent.
    """
    if not isinstance(text, str) or not text.strip():
        raise WriteRefused("pusta treść posta; nic nie wysłano")
    if len(text) > MAX_POST_CHARS:
        raise WriteRefused(
            f"treść ma {len(text)} znaków, a limit X to {MAX_POST_CHARS}; nic nie wysłano"
        )
    weighted = x_weighted_length(text)
    if weighted > MAX_POST_CHARS:
        raise WriteRefused(
            f"treść liczona po zasadach X (adres {URL_WEIGHT} znaki, znak spoza łaciny 2) ma "
            f"{weighted} z {MAX_POST_CHARS}; nic nie wysłano"
        )


def check_image(image: bytes, image_type: str) -> None:
    """The image is one this actuator will upload: a known type whose bytes say the same, and
    within the size bound. Anything else is a refusal before the browser is touched."""
    signature = IMAGE_SIGNATURES.get(image_type)
    if signature is None:
        raise WriteRefused(f"typ obrazu {image_type!r} spoza listy {sorted(IMAGE_SIGNATURES)}")
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise WriteRefused(f"obraz ma {len(image)} bajtów; dozwolone od 1 do {MAX_IMAGE_BYTES}")
    if not image.startswith(signature):
        raise WriteRefused(f"bajty obrazu nie są {image_type}; nic nie wysłano")


def expected_handle(account: str) -> str:
    """The handle the page must show, from the account named in the consent ("@" optional)."""
    handle = account.strip().lstrip("@") if isinstance(account, str) else ""
    if not _HANDLE_RE.match(handle):
        raise WriteRefused(f"konto nie jest uchwytem X: {account!r}")
    return handle


def authorize_reply_target(post_url: str, post_id: str) -> str:
    """The canonical address of the post to reply under, or `WriteRefused`.

    The address must be EXACTLY `https://x.com/<handle>/status/<post_id>` (one trailing slash
    tolerated): no other host, no query (`?s=20` share tails included), no fragment, and the id in
    the path must be the id the consent names. A reply lands under whatever post the page shows,
    so the address is checked as strictly as the target itself.
    """
    if not isinstance(post_id, str) or not _POST_ID_RE.match(post_id):
        raise WriteRefused(f"identyfikator posta nie jest liczbą: {post_id!r}")
    if not isinstance(post_url, str) or not post_url:
        raise WriteRefused("brak adresu posta")
    parts = urlsplit(post_url)
    if parts.scheme.lower() != "https" or parts.netloc.lower() != X_HOST:
        raise WriteRefused(f"tylko {X_ORIGIN}, nie {post_url!r}")
    if parts.query or parts.fragment:
        raise WriteRefused(
            "adres posta ma być dokładnie https://x.com/<konto>/status/<id>, bez zapytania i "
            "fragmentu"
        )
    parsed = parse_status_path(parts.path)
    if parsed is None:
        raise WriteRefused(f"ścieżka nie jest adresem posta X: {parts.path!r}")
    handle, path_id = parsed
    if path_id != post_id:
        raise WriteRefused(f"adres wskazuje post {path_id}, a zgoda dotyczy posta {post_id}")
    return canonical_permalink(handle, post_id)


def snowflake_ms(post_id: str) -> int | None:
    """When X minted `post_id`, in epoch milliseconds, or `None` for something that is not one."""
    try:
        stamp = int(post_id) >> 22
    except (TypeError, ValueError):
        return None
    return stamp + SNOWFLAKE_EPOCH_MS if stamp > 0 else None


def composer_holds(shown: str | None, text: str) -> bool:
    """Whether the composer shows the consented text. Compared through `normalized_for_comparison`,
    so an emoji X draws as an image does not fail it, while a word a typeahead changed does."""
    wanted = normalized_for_comparison(text)
    return bool(wanted) and normalized_for_comparison(shown) == wanted


def posted_text_matches(shown: str | None, text: str) -> bool:
    """Whether a rendered post carries our text. Links are taken out of both sides first: X shows
    a link shortened and truncated (`example.com/pa...`), so the rendered words never equal the
    typed ones there, and the author and the id's age carry that part of the proof."""

    def comparable(value: str | None) -> str:
        return normalized_for_comparison(_URL_LIKE.sub(" ", value or "").replace(_ELLIPSIS, " "))

    wanted = comparable(text)
    return bool(wanted) and comparable(shown) == wanted


@dataclass(frozen=True)
class XPostResult:
    """What the page showed after the click: the new post, by our account."""

    post_id: str
    permalink: str
    account: str
    observed_at: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "permalink": self.permalink,
            "account": self.account,
            "observed_at": self.observed_at,
            "submit_started": True,
            "raw": self.raw,
        }


class XWriteActuator(Protocol):
    """The two operations `reader.py` calls. Named `reply` and `own_post`, never `post`: `post`
    is in the bypass scanner's writing vocabulary and the reader must stay free of it; the typing
    and the clicking live below, in this file, where the scanner counts them."""

    def reply(
        self, *, post_url: str, post_id: str, text: str, account: str, timeout_s: float
    ) -> XPostResult: ...

    def own_post(
        self,
        *,
        text: str,
        account: str,
        timeout_s: float,
        image: bytes | None = None,
        image_type: str | None = None,
    ) -> XPostResult: ...


@dataclass
class _Budget:
    """The one deadline everything before the click shares."""

    deadline: float
    clock: Callable[[], float]

    def ms(self, cap_s: float) -> int:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise WriteRefused(
                "czas na przygotowanie publikacji minął przed wysłaniem; nic nie wysłano"
            )
        return max(1, int(min(cap_s, remaining) * 1000))

    def seconds(self, cap_s: float) -> float:
        return self.ms(cap_s) / 1000


@dataclass(frozen=True)
class _Seen:
    """One post on the page: who wrote it (as the permalink spells it) and its id."""

    handle: str
    post_id: str
    href: str
    article: Any


class XActuator:
    """`XWriteActuator` over the capsule's own Chrome, attached through CDP.

    `driver` is the Playwright sync driver of the reader thread (`CdpPageFetcher.driver`), shared
    for the reason `writer.CdpCommentActuator` documents: a SECOND `sync_playwright().start()` on
    that thread fails before touching any page (measured live 2026-09-09). Without a shared driver
    the actuator starts its own, which is right only when it is the sole Playwright user of its
    thread.

    `clock`, `sleep` and `now_ms` are injectable so the dumb fake of the tests runs the whole flow
    without waiting; `now_ms` is wall-clock time, compared with the time encoded in X's ids.
    """

    def __init__(
        self,
        endpoint_http: str | Callable[[], str],
        *,
        driver: Callable[[], Any] | None = None,
        reply_policy: Callable[[str, str], str] = authorize_reply_target,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        poll_s: float = POLL_S,
    ) -> None:
        self._endpoint_http = endpoint_http
        self._shared_driver = driver
        self._reply_policy = reply_policy
        self._clock = clock
        self._sleep = sleep
        self._now_ms = now_ms
        self._poll_s = poll_s
        self._playwright: Any = None

    def _driver(self) -> Any:
        if self._shared_driver is not None:
            return self._shared_driver()
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        return self._playwright

    def _endpoint(self) -> str:
        if callable(self._endpoint_http):
            return self._endpoint_http()
        return self._endpoint_http

    # -- the two operations, each classified by ONE fact: was the button clicked -------------------

    def reply(
        self, *, post_url: str, post_id: str, text: str, account: str, timeout_s: float
    ) -> XPostResult:
        state = {"submit_started": False}
        try:
            return self._reply(
                post_url=post_url,
                post_id=post_id,
                text=text,
                account=account,
                timeout_s=timeout_s,
                state=state,
            )
        except Exception as exc:
            classified = _classified(exc, state["submit_started"])
            if classified is exc:
                raise
            raise classified from exc

    def own_post(
        self,
        *,
        text: str,
        account: str,
        timeout_s: float,
        image: bytes | None = None,
        image_type: str | None = None,
    ) -> XPostResult:
        state = {"submit_started": False}
        try:
            return self._own_post(
                text=text,
                account=account,
                timeout_s=timeout_s,
                state=state,
                image=image,
                image_type=image_type,
            )
        except Exception as exc:
            classified = _classified(exc, state["submit_started"])
            if classified is exc:
                raise
            raise classified from exc

    # -- flows ------------------------------------------------------------------------------------

    def _reply(
        self,
        *,
        post_url: str,
        post_id: str,
        text: str,
        account: str,
        timeout_s: float,
        state: dict[str, bool],
    ) -> XPostResult:
        target = self._reply_policy(post_url, post_id)
        handle = expected_handle(account)
        check_text(text)
        budget = _Budget(self._clock() + timeout_s, self._clock)
        browser = self._connect(timeout_s)
        try:
            if not browser.contexts:
                raise WriteRefused("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            page = browser.contexts[0].new_page()
            try:
                self._open(page, target, budget)
                self._check_session(page, handle, budget)
                seen = self._await_posts(page, budget, until_id=post_id)
                self._check_page_blocks(page)
                if not any(one.post_id == post_id for one in seen):
                    raise WriteRefused(_unavailable_message(page, post_id))
                # What was ours BEFORE the click. Without it "a new reply" would mean "any reply
                # of ours in this thread", and we may have written there before.
                before = {one.post_id for one in seen if one.handle.lower() == handle.lower()}
                entry, clicked_at = self._compose_and_submit(page, text, budget, state)
                clicked_clock = self._clock()
                until = max(budget.deadline, clicked_clock + MIN_EVIDENCE_S)
                found = self._await_evidence(
                    page,
                    handle=handle,
                    exclude=before | {post_id},
                    clicked_at=clicked_at,
                    text=None,
                    until=until,
                )
                if found is None:
                    raise WriteUnknown(
                        f"po kliknięciu przez {until - clicked_clock:.0f} s nie widać odpowiedzi "
                        f"konta @{handle} pod {target}; skutek nieznany, sprawdź X, nie publikuj "
                        "ponownie",
                        submit_started=True,
                    )
                return _result(
                    found,
                    account=account,
                    page=page,
                    extra={"target": target, "entry": entry, "clicked_at_ms": clicked_at},
                )
            finally:
                with contextlib.suppress(Exception):
                    page.close()
        finally:
            with contextlib.suppress(Exception):
                browser.close()

    def _own_post(
        self,
        *,
        text: str,
        account: str,
        timeout_s: float,
        state: dict[str, bool],
        image: bytes | None = None,
        image_type: str | None = None,
    ) -> XPostResult:
        handle = expected_handle(account)
        check_text(text)
        if image is not None:
            check_image(image, image_type or "")
        budget = _Budget(self._clock() + timeout_s, self._clock)
        browser = self._connect(timeout_s)
        try:
            if not browser.contexts:
                raise WriteRefused("Chrome kapsuły nie ma kontekstu profilu, brak sesji")
            context = browser.contexts[0]
            page = context.new_page()
            try:
                self._open(page, HOME_URL, budget)
                self._check_session(page, handle, budget)
                seen = self._await_posts(page, budget, until_id=None)
                self._check_page_blocks(page)
                before = {one.post_id for one in seen if one.handle.lower() == handle.lower()}
                entry, clicked_at = self._compose_and_submit(
                    page, text, budget, state, image=image, image_type=image_type
                )
                until = max(budget.deadline, self._clock() + MIN_EVIDENCE_S)
                reserve = min(PROFILE_FALLBACK_S, max(0.0, (until - self._clock()) / 2))
                found = self._await_evidence(
                    page,
                    handle=handle,
                    exclude=before,
                    clicked_at=clicked_at,
                    text=text,
                    until=until - reserve,
                )
                extra = {
                    "target": HOME_URL,
                    "entry": entry,
                    "clicked_at_ms": clicked_at,
                    "image_bytes": len(image) if image is not None else 0,
                }
                if found is not None:
                    return _result(found, account=account, page=page, extra=extra)
                # FALLBACK: our profile, read in a SECOND tab. The compose tab stays untouched:
                # navigating it away while X may still be sending would be able to cancel the
                # very request whose outcome we are trying to learn.
                found = self._profile_fallback(
                    context,
                    handle=handle,
                    text=text,
                    exclude=before,
                    clicked_at=clicked_at,
                    until=until,
                )
                if found is None:
                    raise WriteUnknown(
                        f"po kliknięciu nie widać nowego posta konta @{handle} ani w "
                        "powiadomieniu, ani na osi czasu, ani na profilu; skutek nieznany, "
                        "sprawdź X, nie publikuj ponownie",
                        submit_started=True,
                    )
                return _result(found, account=account, page=page, extra=extra)
            finally:
                with contextlib.suppress(Exception):
                    page.close()
        finally:
            with contextlib.suppress(Exception):
                browser.close()

    # -- steps before the click (every failure is a refusal or a block) ---------------------------

    def _connect(self, timeout_s: float) -> Any:
        try:
            return self._driver().chromium.connect_over_cdp(
                self._endpoint(), timeout=int(min(timeout_s, NAV_TIMEOUT_S) * 1000)
            )
        except Exception as exc:  # no page was touched yet: any connection failure is a refusal
            raise WriteRefused(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc

    def _open(self, page: Any, url: str, budget: _Budget) -> None:
        try:
            response = page.goto(
                url, wait_until="domcontentloaded", timeout=budget.ms(NAV_TIMEOUT_S)
            )
        except WriteRefused:
            raise
        except Exception as exc:
            raise WriteRefused(
                f"nawigacja do {url} nie powiodła się: {type(exc).__name__}: {exc}"[:400]
            ) from exc
        status = getattr(response, "status", None)
        if status in (403, 429):
            raise WriteBlocked(
                f"X odpowiedział {status} już przy otwieraniu {url}: platforma odmawia tej "
                "przeglądarce albo ogranicza tempo, kolejka stoi"
            )
        if isinstance(status, int) and status >= 400:
            raise WriteRefused(f"strona {url} odpowiedziała {status}; nic nie wysłano")

    def _check_session(self, page: Any, handle: str, budget: _Budget) -> str:
        """THE HARD CONDITION: the page must show OUR account. Missing is a refusal (nobody is
        logged in, nothing happened); another account is a block (the session in the capsule
        belongs to someone else, and every next request would meet the same)."""
        if is_account_locked(str(page.url)):
            raise WriteBlocked(_LOCKED)
        if is_login_wall(str(page.url)):
            raise WriteRefused(f"{NOT_LOGGED_IN} (X odesłał na ekran logowania)")
        shown = self._shown_handle(page, budget)
        if shown is None:
            # The redirect to a login or lock screen may happen client-side, during the wait.
            if is_account_locked(str(page.url)):
                raise WriteBlocked(_LOCKED)
            wall = " (X odesłał na ekran logowania)" if is_login_wall(str(page.url)) else ""
            raise WriteRefused(NOT_LOGGED_IN + wall)
        if shown.lower() != handle.lower():
            raise WriteBlocked(
                f"strona pokazuje konto @{shown}, oczekiwano @{handle}: sesja w kapsule należy "
                "do innego konta, kolejka stoi"
            )
        return shown

    def _shown_handle(self, page: Any, budget: _Budget) -> str | None:
        link = page.locator(SEL_NAV_PROFILE_LINK).first
        href: str | None = None
        try:
            # Waits for the element instead of counting it: the navigation hydrates after
            # `domcontentloaded`, and an early count would call a logged-in page logged out (the
            # same trap `writer._first_attribute` documents for Reddit).
            link.wait_for(state="attached", timeout=budget.ms(STEP_TIMEOUT_S))
            href = link.get_attribute("href", timeout=READ_TIMEOUT_MS)
        except WriteRefused:
            raise
        except Exception:
            href = None
        return handle_from_profile_href(href) or handle_from_switcher_text(
            safe_text(page.locator(SEL_ACCOUNT_SWITCHER))
        )

    def _await_posts(self, page: Any, budget: _Budget, *, until_id: str | None) -> list[_Seen]:
        """Posts on the page once it rendered: until the post `until_id` shows up, or until any
        post shows up when there is no target, or until the step's time runs out."""
        until = self._clock() + budget.seconds(STEP_TIMEOUT_S)
        while True:
            seen = _scan_posts(page)
            if until_id is not None and any(one.post_id == until_id for one in seen):
                return seen
            if until_id is None and seen:
                return seen
            if self._clock() >= until:
                return seen
            self._sleep(PRE_CLICK_POLL_S)

    def _check_page_blocks(self, page: Any) -> None:
        body = safe_text(page.locator(SEL_BODY)) or ""
        if not any(marker in normalize_page_text(body) for marker in ACCOUNT_BLOCKED_MARKERS):
            return
        # Only now the posts' texts are read: a marker inside somebody's post ("your account is
        # suspended", in a complaint) must not stop our queue.
        outside = text_outside_posts(body, _card_texts(page))
        marker = next((one for one in ACCOUNT_BLOCKED_MARKERS if one in outside), None)
        if marker is not None:
            raise WriteBlocked(f"strona X zgłasza blokadę konta: {marker!r}; kolejka stoi")

    def _compose_and_submit(
        self,
        page: Any,
        text: str,
        budget: _Budget,
        state: dict[str, bool],
        *,
        image: bytes | None = None,
        image_type: str | None = None,
    ) -> tuple[str, int]:
        """Type the text, verify the composer holds it and its button is live, click ONCE.

        Returns how the text went in (`fill` or `keyboard`) and the wall-clock moment of the click.
        """
        textbox = page.locator(SEL_COMPOSER_TEXTBOX).first
        try:
            textbox.wait_for(state="visible", timeout=budget.ms(STEP_TIMEOUT_S))
        except WriteRefused:
            raise
        except Exception as exc:
            raise WriteRefused(
                "brak pola tekstu X na stronie (odpowiedzi ograniczone albo pole się nie "
                "wczytało); nic nie wysłano"
            ) from exc
        submit = page.locator(SEL_SUBMIT_INLINE).first

        # `fill` first: one programmatic insert (`Input.insertText` after selecting all), which X's
        # DraftJS editor handles as a `beforeinput`. It is not trusted blindly: the composer must
        # SHOW the consented text and the button must turn live, the one signal that the editor's
        # own state (not only its DOM) took the text.
        fill_error = ""
        try:
            textbox.fill(text, timeout=budget.ms(STEP_TIMEOUT_S))
            filled = self._composer_ready(textbox, submit, text)
        except WriteRefused:
            raise
        except Exception as exc:
            # Playwright refuses `fill` on an element it does not consider editable; the keyboard
            # below does not depend on that judgement.
            fill_error, filled = f" (fill: {type(exc).__name__}: {exc})"[:200], False
        entry = "fill"
        if not filled:
            # FALLBACK, documented: DraftJS can keep its state out of step with a programmatic
            # insert (text in the DOM, button still disabled). Clear with the keyboard and type
            # key by key, which is what a person does. A newline goes in as a plain Enter, a new
            # line in X's composer; no modifier is ever held, so nothing submits early. The
            # comparison below still guards the result: a typeahead that rewrote a mention would
            # make the composer differ from the consent, and that is a refusal before any click.
            textbox.press("ControlOrMeta+a", timeout=budget.ms(STEP_TIMEOUT_S))
            textbox.press("Backspace", timeout=budget.ms(STEP_TIMEOUT_S))
            typing_s = STEP_TIMEOUT_S + len(text) * TYPE_DELAY_MS / 1000
            textbox.press_sequentially(text, delay=TYPE_DELAY_MS, timeout=budget.ms(typing_s))
            entry = "keyboard"
            if not self._composer_ready(textbox, submit, text):
                raise WriteRefused(
                    "pole tekstu X nie pokazuje treści zgodnej ze zgodą albo przycisk wysyłki "
                    f"pozostał nieaktywny, po fill i po klawiaturze{fill_error}; nic nie wysłano"
                )
        if image is not None:
            self._attach_image(page, submit, image, image_type or "", budget)
            # The consent is the text AND the image: the upload re-renders the composer, so the
            # text is held against the consent once more before the one click.
            if not composer_holds(safe_text(textbox), text):
                raise WriteRefused(
                    "po wgraniu obrazu pole tekstu X nie pokazuje treści zgodnej ze zgodą; "
                    "nic nie wysłano"
                )
        if not _is_enabled(submit):
            raise WriteRefused("przycisk wysyłki X jest nieaktywny; nic nie wysłano")
        # From the next statement on the outcome is a platform fact: mark it BEFORE the click, so
        # a failure between the click and this line cannot be misread as "nothing sent".
        state["submit_started"] = True
        submit.click(timeout=int(CLICK_TIMEOUT_S * 1000))
        return entry, self._now_ms()

    def _attach_image(
        self, page: Any, submit: Any, image: bytes, image_type: str, budget: _Budget
    ) -> None:
        """Give the composer the image and wait until X took it: its preview shows and the button
        is live again (X disables it while the upload runs). All of this is BEFORE the click, so
        any failure is a refusal: nothing was sent, and the text alone is not posted instead."""
        suffix = "png" if image_type == "image/png" else "jpg"
        try:
            page.locator(SEL_FILE_INPUT).first.set_input_files(
                {"name": f"obraz.{suffix}", "mimeType": image_type, "buffer": image},
                timeout=budget.ms(STEP_TIMEOUT_S),
            )
            page.locator(SEL_ATTACHMENTS).first.wait_for(
                state="visible", timeout=budget.ms(UPLOAD_TIMEOUT_S)
            )
        except WriteRefused:
            raise
        except Exception as exc:
            raise WriteRefused(
                f"X nie przyjął obrazu w oknie pisania ({type(exc).__name__}); nic nie wysłano"
            ) from exc
        until = min(budget.deadline, self._clock() + UPLOAD_TIMEOUT_S)
        while not _is_enabled(submit):
            if self._clock() >= until:
                raise WriteRefused(
                    "obraz się wgrywał, a przycisk wysyłki nie odblokował się; nic nie wysłano"
                )
            self._sleep(PRE_CLICK_POLL_S)

    def _composer_ready(self, textbox: Any, submit: Any, text: str) -> bool:
        if not composer_holds(safe_text(textbox), text):
            return False
        until = self._clock() + ENABLE_WAIT_S
        while True:
            if _is_enabled(submit):
                return True
            if self._clock() >= until:
                return False
            self._sleep(PRE_CLICK_POLL_S)

    # -- after the click (a failure is an unknown or a block, never a refusal) ---------------------

    def _await_evidence(
        self,
        page: Any,
        *,
        handle: str,
        exclude: set[str],
        clicked_at: int,
        text: str | None,
        until: float,
    ) -> tuple[str, _Seen] | None:
        """`(source, post)` read off the page, or `None` when the window closed without one.

        Evidence, in order: (a) the toast's "View" link to a post of OUR handle; (b) a post of our
        handle that was not on the page before the click and whose id was minted after it (when
        `text` is given, it must also carry our text). An error toast ends the wait at once: a
        block stops the queue, anything else is an unknown with X's own words in it.
        """
        while True:
            toast = _toast_evidence(page, handle, exclude)
            if toast is not None:
                return "toast", toast
            fresh = _fresh_post(page, handle, exclude, clicked_at=clicked_at, text=text)
            if fresh is not None:
                return "dom", fresh
            failure = _toast_failure(page)
            if failure is not None:
                kind, message = failure
                if kind == "blocked":
                    raise WriteBlocked(
                        f"X odmówił po kliknięciu: {message[:200]}", submit_started=True
                    )
                raise WriteUnknown(
                    f"X pokazał błąd po kliknięciu: {message[:200]}; skutek nieznany, sprawdź X, "
                    "nie publikuj ponownie",
                    submit_started=True,
                )
            if self._clock() >= until:
                return None
            self._sleep(self._poll_s)

    def _profile_fallback(
        self,
        context: Any,
        *,
        handle: str,
        text: str,
        exclude: set[str],
        clicked_at: int,
        until: float,
    ) -> tuple[str, _Seen] | None:
        """The newest post of ours on our profile whose text equals ours and whose id was minted
        after the click. A READ in a second tab; any failure of it is "no evidence", which the
        caller turns into an unknown."""
        page = context.new_page()
        try:
            remaining = until - self._clock()
            if remaining <= 0:
                return None
            try:
                page.goto(
                    f"{X_ORIGIN}/{handle}",
                    wait_until="domcontentloaded",
                    timeout=max(1, int(remaining * 1000)),
                )
            except Exception:
                return None
            while True:
                matches = [
                    one
                    for one in _scan_posts(page)
                    if one.handle.lower() == handle.lower()
                    and one.post_id not in exclude
                    and _minted_after(one.post_id, clicked_at)
                    and posted_text_matches(safe_text(one.article.locator(SEL_POST_TEXT)), text)
                ]
                if matches:
                    return "profile", max(matches, key=lambda one: int(one.post_id))
                if self._clock() >= until:
                    return None
                self._sleep(self._poll_s)
        finally:
            with contextlib.suppress(Exception):
                page.close()


_LOCKED = (
    "X pokazuje ekran blokady konta (/account/access): konto wymaga człowieka przy ekranie, "
    "kolejka stoi"
)


def _classified(exc: Exception, submit_started: bool) -> Exception:
    """Every failure gets its class from ONE fact the actuator itself knows. Before the click any
    exception is a refusal (nothing sent); after it, an unknown (the post may exist). A refusal
    raised after the click would be a lie about the click, so it becomes an unknown as well."""
    if isinstance(exc, WriteBlocked | WriteUnknown):
        return exc
    if submit_started:
        return WriteUnknown(
            f"po kliknięciu wysyłki: {type(exc).__name__}: {exc}"[:400], submit_started=True
        )
    if isinstance(exc, WriteRefused):
        return exc
    return WriteRefused(f"przed wysłaniem: {type(exc).__name__}: {exc}"[:400])


def _scan_posts(page: Any) -> list[_Seen]:
    """Posts on the page with a readable permalink, in document order, bounded by `MAX_POSTS`.
    The author comes from the permalink and is compared IN PYTHON, case-insensitively: a selector
    with the handle in it would be case-sensitive and miss our own post (the 21.09 Reddit bug)."""
    articles = page.locator(SEL_ARTICLE)
    seen: list[_Seen] = []
    for index in range(min(safe_count(articles), MAX_POSTS)):
        article = articles.nth(index)
        hrefs = safe_attributes(
            article.locator(SEL_STATUS_LINK), "href", limit=MAX_STATUS_LINKS_PER_POST
        )
        # The post's own link is its AUTHOR's (`x_reader.own_permalink`): in an X article the
        # first status link belongs to a post embedded in it, and the actuator refused to reply
        # to the article because "the post is not on the page" (27.09, nothing was sent).
        names = safe_all_texts(article.locator(SEL_POST_USER_NAME))
        href, _embedded_first = own_permalink(hrefs, names[0] if names else None)
        parsed = parse_status_path(href)
        if href and parsed is not None:
            seen.append(_Seen(handle=parsed[0], post_id=parsed[1], href=href, article=article))
    return seen


def _card_texts(page: Any) -> list[str | None]:
    articles = page.locator(SEL_ARTICLE)
    return [safe_text(articles.nth(index)) for index in range(min(safe_count(articles), MAX_POSTS))]


def _unavailable_message(page: Any, post_id: str) -> str:
    category, _ = detect_unavailable(
        safe_text(page.locator(SEL_BODY)),
        post_texts=_card_texts(page),
        capability=CAPABILITY_POST,
        focal_found=False,
    )
    if category is not None:
        return f"post {post_id} jest niedostępny na X ({category}); nic nie wysłano"
    return f"nie widać posta {post_id} na stronie; nic nie wysłano"


def _is_enabled(locator: Any) -> bool:
    if safe_count(locator) == 0:
        return False
    try:
        enabled = bool(locator.first.is_enabled(timeout=READ_TIMEOUT_MS))
    except Exception:
        return False
    return enabled and safe_attribute(locator, "aria-disabled") != "true"


def _minted_after(post_id: str, clicked_at: int) -> bool:
    minted = snowflake_ms(post_id)
    return minted is not None and minted >= clicked_at - RECENCY_SKEW_MS


def _toast_evidence(page: Any, handle: str, exclude: set[str]) -> _Seen | None:
    links = page.locator(SEL_TOAST_STATUS_LINK)
    for href in safe_attributes(links, "href", limit=4):
        parsed = parse_status_path(href)
        if parsed is None or parsed[0].lower() != handle.lower() or parsed[1] in exclude:
            continue
        return _Seen(handle=parsed[0], post_id=parsed[1], href=href, article=None)
    return None


def _fresh_post(
    page: Any, handle: str, exclude: set[str], *, clicked_at: int, text: str | None
) -> _Seen | None:
    for one in _scan_posts(page):
        if one.handle.lower() != handle.lower() or one.post_id in exclude:
            continue
        # An OLD post of ours can appear after the click too: X loads a conversation in parts,
        # so a reply of ours from last week may render only now. The id's own timestamp tells it
        # apart from the post we just sent.
        if not _minted_after(one.post_id, clicked_at):
            continue
        if text is not None and not posted_text_matches(
            safe_text(one.article.locator(SEL_POST_TEXT)), text
        ):
            continue
        return one
    return None


def _toast_failure(page: Any) -> tuple[str, str] | None:
    message = safe_text(page.locator(SEL_TOAST))
    if not message or safe_count(page.locator(SEL_TOAST_STATUS_LINK)) > 0:
        return None
    normalized = normalize_page_text(message)
    if any(marker in normalized for marker in BLOCKED_TOAST_MARKERS):
        return "blocked", message.strip()
    if any(marker in normalized for marker in FAILED_TOAST_MARKERS):
        return "failed", message.strip()
    return None


def _result(
    found: tuple[str, _Seen], *, account: str, page: Any, extra: dict[str, Any]
) -> XPostResult:
    source, post = found
    minted = snowflake_ms(post.post_id)
    return XPostResult(
        post_id=post.post_id,
        # The handle as the PAGE spells it: that is what X links to.
        permalink=canonical_permalink(post.handle, post.post_id),
        # The account as the CONSENT names it: that is what a human sees under the evidence.
        account=account.strip(),
        observed_at=datetime.now(UTC).isoformat(),
        raw={
            "source": source,
            "href": post.href,
            "page_url": str(page.url),
            "minted_at": (
                datetime.fromtimestamp(minted / 1000, tz=UTC).isoformat() if minted else None
            ),
            **extra,
        },
    )
