"""Reading X through the capsule's Chrome, because this platform leaves no other way in.

WHY A SEPARATE MODULE AND NOT ANOTHER PATH IN `reader.py`. Reddit serves JSON under `.json`, so
that reader takes the navigation's response body and parses it without rendering. X has no such
entrance without paid keys: its pages are an application that fetches the content after the
navigation, so the raw response body is an empty shell. What remains is to render the page in
Chrome and read it off the DOM. Different transport, different result shape, different allowlist,
so the two live apart. `public_research.py` is anonymous by design and cannot see a logged-in X.

WHAT NEEDS A SESSION, measured on 2026-09-26 on the live capsule (the P6 measurement of 19.09,
"X answers 403 to everything anonymously", STAN-PRAC §8.37, is OUT OF DATE):

  * `x.com/OpenAI` without a session: HTTP 200 and five real posts with text, permalinks and
    counters. A public profile reads without logging in.
  * `x.com/home` without a session redirects to `/i/jf/onboarding/web?...&mode=login`. The feed,
    notifications and everything tied to the account need the session.

THE LOGGED-IN AND THE LOGGED-OUT VIEW HAVE DIFFERENT DOM, and that is the trap of this module. The
logged-in application marks posts with `data-testid="tweet"` and `tweetText`; the logged-out page
renders plain `article` elements with neither (measured: 5 `article`, 0 `data-testid="tweet"`, no
`tweetText`, no `time[datetime]`). A selector fitted to the logged-in view alone would say "no
posts" about a page on which every human sees posts. So posts are found as `article`, and a post
whose body element is missing carries the whole card text, SAYING so (`text_source = "card"`).

READS ONLY. This module never calls `click`, `fill`, `press`, `type`, `evaluate` nor any scroll or
pointer input: `control_plane/execution/bypass_scan.py` counts such calls repository-wide and
`tests/test_x_reader.py` applies a stricter vocabulary to this file alone. Navigation is a read
(the same judgement as in `reader.py`); the content comes off the page through `inner_text` and
`get_attribute`. Writing on X belongs to `x_writer.py`, behind the actuator gate.

THE ALLOWLIST REFUSES EVERYTHING THAT CHANGES STATE. It is not only about HTTP methods: on X a mere
visit to `/logout` or `/intent/...` is an action, not a read. That is why the first path segment
has a closed list of exceptions (`RESERVED_FIRST_SEGMENTS`) and a profile is admitted only when it
is none of them. Same pattern and reason as `reader.ALLOWED_PATHS`: a value outside the list is a
REFUSAL, never a new capability.

EVERY SELECTOR LIVES IN ONE BLOCK BELOW (`SEL_*`), shared with `x_writer.py`. Those strings, not the
code around them, break when X rebuilds its frontend, so live tuning touches one place. When one
stops matching, the read comes back EXPLICIT (`None` fields and a sentence in `limitations`), never
invented: a counter that was not on the page is absent, not zero; a relation that cannot be
established is empty, not guessed.

WHAT IS NOT HERE. No login: a human puts the session into the profile once, through the capsule's
INTERACTIVE screen (`POST /mode` with `start_url`). No working around blocks, no CAPTCHA. A
platform refusal is a RESULT and travels as one (`XPlatformRefused` keeps the status code).
"""

from __future__ import annotations

import contextlib
import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: X moved from twitter.com to x.com and only the latter origin is served. One origin, as with
#: Reddit: two would make "which session did we read with" depend on a redirect.
X_HOST = "x.com"
X_ORIGIN = f"https://{X_HOST}"

#: An X handle: 1 to 15 letters, digits and underscores, the rule the platform enforces at signup.
HANDLE = r"[A-Za-z0-9_]{1,15}"
_HANDLE_RE = re.compile(rf"^{HANDLE}$")

#: First path segments that are NEVER a profile. Some are actions (`logout`, `intent`, `compose`),
#: some lead to the account's private pages (`settings`, `messages`, `bookmarks`), some are internal
#: routes of the application. Closed list, checked BEFORE a path is read as a handle, so a route
#: that happens to fit the handle pattern stays refused.
RESERVED_FIRST_SEGMENTS: frozenset[str] = frozenset(
    {
        "account",
        "bookmarks",
        "communities",
        "compose",
        "deactivate",
        "explore",
        "hashtag",
        "home",
        "i",
        "intent",
        "jobs",
        "lists",
        "login",
        "logout",
        "messages",
        "notifications",
        "oauth",
        "premium",
        "privacy",
        "search",
        "session",
        "settings",
        "share",
        "signup",
        "tos",
    }
)

#: Capability names, in English, as the control plane reads them.
CAPABILITY_HOME = "home"
CAPABILITY_EXPLORE = "explore"
CAPABILITY_SEARCH = "search"
CAPABILITY_NOTIFICATIONS = "notifications"
CAPABILITY_POST = "post"
CAPABILITY_PROFILE_REPLIES = "profile_replies"
CAPABILITY_PROFILE = "profile"
#: Shapes whose first path segment is a handle, so the reserved list applies to them.
_HANDLE_CAPABILITIES = frozenset({CAPABILITY_POST, CAPABILITY_PROFILE_REPLIES, CAPABILITY_PROFILE})
_PROFILE_PAGES = frozenset({CAPABILITY_PROFILE_REPLIES, CAPABILITY_PROFILE})

#: The paths that may be read, first match wins. The exact routes come before the handle shapes,
#: so `/home` is the feed and `/home/status/1` is refused by the reserved list.
ALLOWED_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (CAPABILITY_HOME, re.compile(r"^/home$")),
    (CAPABILITY_EXPLORE, re.compile(r"^/explore$")),
    (CAPABILITY_SEARCH, re.compile(r"^/search$")),
    (CAPABILITY_NOTIFICATIONS, re.compile(r"^/notifications$")),
    (CAPABILITY_POST, re.compile(rf"^/(?P<handle>{HANDLE})/status/(?P<id>\d{{1,25}})$")),
    (CAPABILITY_PROFILE_REPLIES, re.compile(rf"^/(?P<handle>{HANDLE})/with_replies$")),
    (CAPABILITY_PROFILE, re.compile(rf"^/(?P<handle>{HANDLE})$")),
)

#: Query parameters, allowed on `/search` ONLY. `q` is the search text, so a wide set of characters
#: passes, with a length cap: a query without a bound is not a search, it is a free channel into
#: the address.
SEARCH_QUERY: dict[str, re.Pattern[str]] = {
    "q": re.compile(r"^.{1,200}$", re.DOTALL),
    "f": re.compile(r"^(live|top|user|media)$"),
    "src": re.compile(r"^[A-Za-z0-9_]{1,40}$"),
    "lang": re.compile(r"^[a-z]{2}$"),
}
MAX_URL_CHARS = 2000

#: How many posts one page may yield. This is reading context, not harvesting; the bound is hard
#: for the same reason as `read_probe.MAX_TARGETS`.
MAX_POSTS = 40
#: Cells walked on a post page before giving up (posts, headings, "show more" rows).
MAX_CELLS = 160
MAX_STATUS_LINKS_PER_POST = 6
MAX_TEXT_CHARS = 200_000

#: Time budget. The reader's HTTP server is single-threaded and a slow X read blocks Reddit reads
#: queued behind it, so every wait is short and bounded by a clock, not by a count of loop turns.
X_NAV_TIMEOUT_S = 20.0
RENDER_WAIT_S = 10.0
POLL_S = 0.25
#: The post count must hold still this long before the page counts as rendered.
SETTLE_S = 0.75
VIEWER_WAIT_MS = 2_000
READ_TIMEOUT_MS = 1_500

# -- selectors: the ONE place live tuning touches --------------------------------------------------
#
# Built against the well-known logged-in X DOM. Only `SEL_ARTICLE` and the logged-out facts in the
# module docstring were measured on a live page (2026-09-26, logged out); everything tied to the
# logged-in application (`tweetText`, `User-Name`, the action bar, the composer, the toast) is
# UNVERIFIED until the capsule's profile holds an X session. Every reader of these strings treats a
# miss as "not on the page" and says so.

#: A post container in BOTH views. Not `article[data-testid="tweet"]`: that attribute belongs to the
#: logged-in application only (measured, see the module docstring).
SEL_ARTICLE = "article"
#: A row of a virtualised timeline. On a post page the rows also carry headings, which is how the
#: end of the conversation ("Discover more") is found.
SEL_CELL = '[data-testid="cellInnerDiv"]'
SEL_POST_TEXT = '[data-testid="tweetText"]'
SEL_POST_USER_NAME = '[data-testid="User-Name"]'
#: Every link to a status inside a post. The post's own permalink is the first one whose path is
#: exactly `/<handle>/status/<id>` (the timestamp link); `/photo/1`, `/analytics` do not qualify.
SEL_STATUS_LINK = 'a[href*="/status/"]'
SEL_TIME = "time"
#: An X ARTICLE (long-form post), measured logged in on 2026-09-27: the post is still
#: `article[data-testid="tweet"]`, but its body is a nested read view with a title and rich text,
#: it has no `tweetText` of its own, and its timestamp link comes AFTER the body and after any post
#: embedded in it. Reading "the first status link, the first time, the first tweetText" gave the
#: embedded post's author, date and text, and the article itself was "not on the page".
SEL_ARTICLE_TITLE = '[data-testid="twitter-article-title"]'
SEL_ARTICLE_BODY = '[data-testid="longformRichTextComponent"]'
#: "12 replies, 3 reposts, 45 likes, 2 bookmarks, 6789 views" in one aria-label.
SEL_ACTION_BAR = 'div[role="group"][aria-label]'
#: Per-button labels ("12 Replies. Reply"), read only when the action bar has no label at all.
SEL_REPLY_BUTTON = '[data-testid="reply"]'
SEL_REPOST_BUTTON = '[data-testid="retweet"], [data-testid="unretweet"]'
SEL_LIKE_BUTTON = '[data-testid="like"], [data-testid="unlike"]'
SEL_BOOKMARK_BUTTON = '[data-testid="bookmark"], [data-testid="removeBookmark"]'
SEL_ANALYTICS_LINK = 'a[href$="/analytics"]'
#: Who is logged in: the "Profile" link of the side navigation carries `/<handle>` as its href.
SEL_NAV_PROFILE_LINK = 'a[data-testid="AppTabBar_Profile_Link"]'
#: Fallback for the same fact: the account switcher's text holds "Display Name @handle".
SEL_ACCOUNT_SWITCHER = '[data-testid="SideNav_AccountSwitcher_Button"]'
#: A sign of the logged-out view (UNVERIFIED testid); only ever adds a sentence to `limitations`.
SEL_LOGGED_OUT_HINT = '[data-testid="loginButton"]'
SEL_PROFILE_NAME = '[data-testid="UserName"]'
SEL_PROFILE_BIO = '[data-testid="UserDescription"]'
SEL_FOLLOWING_LINK = 'a[href$="/following"]'
SEL_FOLLOWERS_LINK = 'a[href$="/followers"], a[href$="/verified_followers"]'
#: An empty timeline or an error panel: ends the render wait early (UNVERIFIED testids; a miss only
#: means the wait runs to its deadline).
SEL_PAGE_STATE = '[data-testid="emptyState"], [data-testid="error-detail"]'
SEL_BODY = "body"
#: The composer and what follows a submission, used by `x_writer.py` only.
SEL_COMPOSER_TEXTBOX = '[data-testid="tweetTextarea_0"]'
SEL_SUBMIT_INLINE = '[data-testid="tweetButtonInline"]'
SEL_TOAST = '[data-testid="toast"]'
SEL_TOAST_STATUS_LINK = '[data-testid="toast"] a[href*="/status/"]'
#: The composer's hidden media input and the preview of what it took (27.09, `x_writer.py` only).
#: UNVERIFIED testids until the first post with an image: a miss is a refusal before any click.
SEL_FILE_INPUT = 'input[data-testid="fileInput"]'
SEL_ATTACHMENTS = '[data-testid="attachments"]'

# -- page text markers (lower case, apostrophes and ellipses normalised) ---------------------------

#: What a page says instead of content, by category, in priority order. Matched against the page
#: text OUTSIDE the posts, so a post that quotes "account suspended" does not suspend anybody.
UNAVAILABLE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("suspended", ("account suspended", "this post is from a suspended account")),
    ("protected", ("these posts are protected", "limits who can view their posts")),
    ("deleted", ("this post was deleted",)),
    (
        "not_found",
        ("hmm...this page doesn't exist", "this page doesn't exist", "this account doesn't exist"),
    ),
)
ERROR_MARKERS: tuple[str, ...] = ("something went wrong. try reloading",)
#: The heading X puts under a conversation before unrelated posts. Posts after it are not replies.
CONVERSATION_END_MARKERS: tuple[str, ...] = ("discover more",)

#: Where X sends a visitor without a session. `/i/jf/` was added FROM A MEASUREMENT on 26.09: the
#: first real read of `/home` landed on
#: `/i/jf/onboarding/web?redirect_after_login=%2Fhome&mode=login`, and a list without that prefix
#: called a login wall "a session whose handle was not read".
#: Exact paths match themselves and their subpaths; `LOGIN_WALL_PREFIXES` are directories.
LOGIN_WALL_PATHS: tuple[str, ...] = ("/i/flow/login", "/i/flow/signup", "/login", "/account/access")
LOGIN_WALL_PREFIXES: tuple[str, ...] = ("/i/jf/",)
#: The locked-account screen: a login wall for the reader, a block for the writer.
ACCOUNT_LOCKED_PATH = "/account/access"
#: Statuses of the HTML navigation that mean "the platform refuses this client", not "empty page".
REFUSING_STATUSES = frozenset({401, 403, 429})

_STATUS_PATH = re.compile(rf"^/(?P<handle>{HANDLE})/status/(?P<id>\d{{1,25}})/?$")
_METRIC = re.compile(
    r"(?P<count>\d[\d,]*(?:\.\d+)?\s?[KMB]?)\s+"
    r"(?P<word>replies|reply|reposts|repost|retweets|retweet|likes|like|bookmarks|bookmark"
    r"|views|view)\b",
    re.IGNORECASE,
)
_METRIC_KEYS = {
    "reply": "replies",
    "replies": "replies",
    "repost": "reposts",
    "reposts": "reposts",
    "retweet": "reposts",
    "retweets": "reposts",
    "like": "likes",
    "likes": "likes",
    "bookmark": "bookmarks",
    "bookmarks": "bookmarks",
    "view": "views",
    "views": "views",
}
_PLAIN_COUNT = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)$")
_SUFFIXED_COUNT = re.compile(r"^(?P<number>\d+(?:\.\d+)?)(?P<suffix>[KMB])$", re.IGNORECASE)
_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_LEADING_COUNT = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?\s?[KMB]?)(?![A-Za-z0-9])", re.IGNORECASE)
_SWITCHER_HANDLE = re.compile(rf"(?<![A-Za-z0-9_])@({HANDLE})(?![A-Za-z0-9_])")


class XReadRefused(Exception):
    """The URL is outside the allowlist. Chrome was not asked."""


class XReadFailed(Exception):
    """Chrome could not deliver the page (no CDP, no context, the navigation failed)."""


class XReadTimedOut(XReadFailed):
    """The navigation did not finish in time."""


class XPlatformRefused(Exception):
    """X answered the navigation with a refusing status code.

    It carries the NUMBER because the caller's decision depends on it: 429 means "slow down", 403
    means "not this way", 5xx means "try later". One message without the code would make it guess.
    """

    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        super().__init__(f"X odpowiedział {status} na {url}")


# -- the allowlist ---------------------------------------------------------------------------------


def authorize_x_url(url: str) -> tuple[str, str]:
    """The canonical URL and the CAPABILITY NAME, or `XReadRefused`.

    The capability comes back with the address because the caller has to know what to expect of
    the page (a profile has a header, a post has a conversation), and deriving it a second time
    from the URL would be a second definition of the same rule.

    Canonical means: scheme and host lower-cased, no trailing slash, no fragment, the query
    re-encoded from the parsed pairs. Chrome receives exactly what was checked, not a differently
    parsed twin; the same rule as `reader.authorize_url`, copied on purpose: two allowlists may
    differ in paths, never in what canonical means.
    """
    if not isinstance(url, str) or not url:
        raise XReadRefused("brak URL")
    if len(url) > MAX_URL_CHARS:
        raise XReadRefused(f"URL dłuższy niż {MAX_URL_CHARS} znaków")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise XReadRefused(f"tylko https, nie {parts.scheme!r}")
    # `netloc`, not `hostname`: user-info and an explicit port are refused too, because
    # `https://x.com@evil.example/...` has the hostname `evil.example`.
    if parts.netloc.lower() != X_HOST:
        raise XReadRefused(f"tylko origin {X_ORIGIN}, nie {parts.netloc!r}")
    if parts.fragment:
        raise XReadRefused("fragment w URL nie jest dozwolony")

    path = parts.path.rstrip("/") or "/"
    if path == "/":
        raise XReadRefused("strona główna bez ścieżki nie jest odczytem niczego konkretnego")
    first_segment = path.split("/")[1].lower()
    capability: str | None = None
    for name, pattern in ALLOWED_PATHS:
        if pattern.match(path) is None:
            continue
        # A reserved first segment blocks ONLY the handle shapes. `/home` and `/notifications`
        # have exact patterns of their own and never reach this line.
        if name in _HANDLE_CAPABILITIES and first_segment in RESERVED_FIRST_SEGMENTS:
            raise XReadRefused(f"{first_segment!r} nie jest profilem, tylko trasą aplikacji")
        capability = name
        break
    if capability is None:
        raise XReadRefused(f"ścieżka poza allowlistą: {parts.path!r}")

    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query))
    except ValueError as exc:
        raise XReadRefused(f"niepoprawne zapytanie: {parts.query!r}") from exc
    if pairs and capability != CAPABILITY_SEARCH:
        raise XReadRefused("parametry zapytania są dozwolone tylko przy wyszukiwaniu")
    seen: set[str] = set()
    for key, value in pairs:
        if key in seen:
            raise XReadRefused(f"powtórzony parametr {key!r}")
        seen.add(key)
        allowed = SEARCH_QUERY.get(key)
        if allowed is None or allowed.match(value) is None:
            raise XReadRefused(f"parametr poza allowlistą: {key}={value!r}")
    if capability == CAPABILITY_SEARCH and "q" not in seen:
        raise XReadRefused("wyszukiwanie bez `q` nie jest wyszukiwaniem")
    return urlunsplit(("https", X_HOST, path, urlencode(pairs), "")), capability


# -- pure parsers: everything that decides, testable without Chrome --------------------------------


def parse_status_path(href: str | None) -> tuple[str, str] | None:
    """`(handle, status id)` of a link that is EXACTLY a status permalink, or `None`.

    Accepts the relative href X renders (`/jack/status/20`) and the absolute form on x.com. A
    subpath (`/photo/1`, `/analytics`) is not the post's own permalink, and a reserved first
    segment (`/i/status/20`) names no author, so both are `None`.
    """
    if not href:
        return None
    parts = urlsplit(href)
    if (parts.scheme or parts.netloc) and (
        parts.scheme.lower() != "https" or parts.netloc.lower() != X_HOST
    ):
        return None
    match = _STATUS_PATH.match(parts.path)
    if match is None or match["handle"].lower() in RESERVED_FIRST_SEGMENTS:
        return None
    return match["handle"], match["id"]


def canonical_permalink(handle: str, post_id: str) -> str:
    return f"{X_ORIGIN}/{handle}/status/{post_id}"


def handle_from_profile_href(href: str | None) -> str | None:
    """The logged-in handle from the "Profile" link of X's navigation, or `None`.

    Guessing the handle from anything else would let the product claim who it is without having
    read it, and the whole point of this read is to CHECK the account's identity.
    """
    if not href:
        return None
    path = urlsplit(href).path.strip("/")
    if not path or "/" in path or path.lower() in RESERVED_FIRST_SEGMENTS:
        return None
    return path if _HANDLE_RE.match(path) else None


def handle_from_switcher_text(text: str | None) -> str | None:
    """The first `@handle` in the account switcher's text ("Display Name\\n@handle"), or `None`."""
    match = _SWITCHER_HANDLE.search(text or "")
    if match is None or match.group(1).lower() in RESERVED_FIRST_SEGMENTS:
        return None
    return match.group(1)


def _path_is(path: str, exact: str) -> bool:
    return path == exact or path.startswith(exact + "/")


def is_login_wall(final_url: str) -> bool:
    """Whether X sent us to a login screen. An answer about the SESSION, not a failed read.

    The query is parsed, not searched: `/search?q=mode%3Dlogin` is a search, not a wall. The
    return parameters stay as a second condition because X has changed the path before and those
    parameters survived the change.
    """
    parts = urlsplit(final_url or "")
    if parts.netloc and parts.netloc.lower() != X_HOST:
        return False
    if any(_path_is(parts.path, one) for one in LOGIN_WALL_PATHS):
        return True
    if any(parts.path.startswith(prefix) for prefix in LOGIN_WALL_PREFIXES):
        return True
    pairs = parse_qsl(parts.query or "", keep_blank_values=True)
    return any(key == "redirect_after_login" for key, _ in pairs) or ("mode", "login") in pairs


def is_account_locked(final_url: str) -> bool:
    parts = urlsplit(final_url or "")
    return (not parts.netloc or parts.netloc.lower() == X_HOST) and _path_is(
        parts.path, ACCOUNT_LOCKED_PATH
    )


def parse_count(raw: str | None) -> int | None:
    """An integer from X's count spelling: "1,234", "1.2K", "3M", "12". `None` for anything else.

    "1.2" without a suffix is refused rather than read: in the locale the capsule renders it is not
    an integer count, and a guess would be a number nobody measured.
    """
    if not raw:
        return None
    token = re.sub(r"\s+", "", raw)
    if _PLAIN_COUNT.match(token):
        return int(token.replace(",", ""))
    match = _SUFFIXED_COUNT.match(token)
    if match is None:
        return None
    return int(Decimal(match["number"]) * _MULTIPLIERS[match["suffix"].upper()])


def parse_metrics(label: str | None) -> dict[str, int]:
    """Counters named in one aria-label. A counter the label does not name stays ABSENT, never 0:
    zero is a measurement, absence is the lack of one."""
    found: dict[str, int] = {}
    for match in _METRIC.finditer(label or ""):
        key = _METRIC_KEYS[match["word"].lower()]
        value = parse_count(match["count"])
        if value is not None and key not in found:
            found[key] = value
    return found


def leading_count(text: str | None) -> int | None:
    """The count a profile link starts with ("4.5M Followers" gives 4500000), or `None`."""
    match = _LEADING_COUNT.match(text or "")
    return parse_count(match.group(1)) if match else None


def author_name_from_user_name(text: str | None) -> str | None:
    """The display name: the first line of a `User-Name` block, when it is not the `@handle`."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or lines[0].startswith("@"):
        return None
    return lines[0]


#: X ids are snowflakes; the same epoch as `x_writer.SNOWFLAKE_EPOCH_MS` (a test keeps them equal).
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
#: An article can run to thousands of words; the agent needs the argument, not the whole book.
MAX_ARTICLE_CHARS = 12_000


def snowflake_moment(post_id: str | None) -> str | None:
    """When an X id was minted, in the ISO form of X's own `time[datetime]`, or `None`."""
    if not post_id or not post_id.isdigit():
        return None
    minted_ms = (int(post_id) >> 22) + SNOWFLAKE_EPOCH_MS
    moment = time.gmtime(minted_ms // 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", moment) + f".{minted_ms % 1000:03d}Z"


def own_permalink(hrefs: Sequence[str], author_block: str | None) -> tuple[str | None, bool]:
    """The post's own status link, and whether an embedded post's link came before it.

    The post's permalink is its AUTHOR's status link. In an ordinary post that is also the first
    one (the timestamp sits in the header); in an X article the timestamp comes after the body and
    after any post embedded in it, so "the first link" named the embedded post (27.09). Without an
    author handle the first exact link stays the answer, as before.
    """
    exact = [href for href in hrefs if parse_status_path(href) is not None]
    author = handle_from_switcher_text(author_block)
    own = [
        href
        for href in exact
        if author is not None and parse_status_path(href)[0].lower() == author.lower()
    ]
    permalink = (own or exact or [None])[0]
    return permalink, bool(exact) and permalink != exact[0]


def choose_post_text(
    body_texts: Sequence[str], *, name_blocks: int, card_text: str | None
) -> tuple[str | None, str | None]:
    """`(text, text_source)` for one post, honest about where the text came from.

    A QUOTED POST is the trap here: it brings its own `User-Name` and `tweetText` into the same
    `article`. With two name blocks and ONE body text, nobody can tell whether that text is the
    post's or the quote's, and attributing a quote to the wrong author is inventing a sentence. So
    that case falls back to the whole card, labelled `card`. The logged-out view has no body element
    at all and lands in the same fallback.
    """
    if body_texts and (name_blocks <= 1 or len(body_texts) >= 2):
        return body_texts[0], "post"
    if card_text:
        return card_text, "card"
    return None, None


def assign_relations(
    post_ids: Sequence[str | None], focal_id: str, *, conversation_end: int | None = None
) -> list[str | None]:
    """Relation of every post on a post page: `ancestor` before the focal post, `focal`, `reply`
    after it up to the end of the conversation, and `None` beyond that end.

    Without the focal post on the page nothing can be established, so every relation is `None`:
    "the third post is a reply" is a claim only the focal post's position makes true.
    """
    try:
        focal = list(post_ids).index(focal_id)
    except ValueError:
        return [None] * len(post_ids)
    end = len(post_ids) if conversation_end is None else conversation_end
    relations: list[str | None] = []
    for index in range(len(post_ids)):
        if index < focal:
            relations.append("ancestor")
        elif index == focal:
            relations.append("focal")
        elif index < end:
            relations.append("reply")
        else:
            relations.append(None)
    return relations


def normalize_page_text(text: str | None) -> str:
    """Lower case, typographic apostrophes and ellipses made plain, whitespace collapsed. X writes
    a typographic apostrophe (U+2019) in "doesn't", and a marker spelled with a
    plain one would never match it."""
    plain = (text or "").replace("\u2019", "'").replace("\u2026", "...")
    return " ".join(plain.split()).lower()


def text_outside_posts(body_text: str | None, post_texts: Sequence[str | None]) -> str:
    """The normalised page text with each post's text taken out once, so page-level markers are
    matched against the page's own words and not against what somebody posted."""
    remaining = normalize_page_text(body_text)
    for one in post_texts:
        piece = normalize_page_text(one)
        if piece:
            remaining = remaining.replace(piece, " ", 1)
    return remaining


def detect_unavailable(
    body_text: str | None,
    *,
    post_texts: Sequence[str | None],
    capability: str,
    focal_found: bool,
) -> tuple[str | None, list[str]]:
    """`(unavailable, limitations)` from what the page says instead of content.

    On a post page a FOUND focal post settles it: the post is there, and a tombstone on the page
    ("This post was deleted") belongs to a parent or a reply, not to the post asked for. An error
    panel next to posts is a partial page, reported as a limitation; only a page with NO posts is
    `error`.
    """
    outside = text_outside_posts(body_text, post_texts)
    limitations: list[str] = []
    error_shown = any(marker in outside for marker in ERROR_MARKERS)
    if capability == CAPABILITY_POST and focal_found:
        if error_shown:
            limitations.append("Część strony nie wczytała się (X: „Something went wrong”).")
        return None, limitations
    for category, markers in UNAVAILABLE_MARKERS:
        if any(marker in outside for marker in markers):
            return category, limitations
    if error_shown:
        if any(one for one in post_texts):
            limitations.append("Część strony nie wczytała się (X: „Something went wrong”).")
            return None, limitations
        return "error", limitations
    return None, limitations


def normalized_for_comparison(text: str | None) -> str:
    """Text reduced to what survives X's rendering: NFC, no emoji or symbol characters (X draws
    them as images, which `inner_text` does not return), no variation selectors, whitespace
    collapsed. Case is kept: X does not change it, and a changed case is a different text."""
    composed = unicodedata.normalize("NFC", text or "")
    kept = "".join(
        char
        for char in composed
        if unicodedata.category(char) not in {"So", "Sk", "Cf", "Cs"}
        and char not in {"\ufe0e", "\ufe0f"}
    )
    return " ".join(kept.split())


# -- the result ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class XPost:
    """One post read off the page. A field that was not there stays `None` (or absent, for
    counters): `None` means "not read", never "zero"."""

    id: str | None
    author: str | None
    author_name: str | None
    text: str | None
    #: `post` is the post's own body (`tweetText`, logged-in view); `card` is the WHOLE article
    #: (name, handle, date, body, counters together), because the logged-out view has no separate
    #: body element. The model has to know which one it reads: a card served as the post's words
    #: would be a quote nobody wrote.
    text_source: str | None
    permalink: str | None
    published_at: str | None
    relation: str | None = None
    metrics: dict[str, int] = field(default_factory=dict)

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


def post_from_fields(
    *,
    permalink_href: str | None,
    text: str | None,
    text_source: str | None,
    author_name: str | None = None,
    published_at: str | None = None,
    relation: str | None = None,
    metrics: dict[str, int] | None = None,
) -> XPost:
    """Assemble a post from what came off the page. Author and id come from the permalink, the
    one place X states both unambiguously; a display name changes and is not an identity."""
    parsed = parse_status_path(permalink_href)
    author, post_id = parsed if parsed is not None else (None, None)
    return XPost(
        id=post_id,
        author=author,
        author_name=author_name,
        text=text,
        text_source=text_source if text is not None else None,
        permalink=canonical_permalink(author, post_id) if parsed is not None else None,
        published_at=published_at,
        relation=relation,
        metrics=dict(metrics or {}),
    )


@dataclass(frozen=True)
class XProfile:
    handle: str | None
    name: str | None
    bio: str | None
    followers: int | None = None
    following: int | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"handle": self.handle, "name": self.name, "bio": self.bio}
        if self.followers is not None:
            payload["followers"] = self.followers
        if self.following is not None:
            payload["following"] = self.following
        return payload


def parse_profile(
    name_block: str,
    *,
    bio: str | None,
    following_text: str | None,
    followers_text: str | None,
) -> XProfile:
    lines = [line.strip() for line in name_block.splitlines() if line.strip()]
    handle = next(
        (line[1:] for line in lines if line.startswith("@") and _HANDLE_RE.match(line[1:])), None
    )
    name = next((line for line in lines if not line.startswith("@")), None)
    return XProfile(
        handle=handle,
        name=name,
        bio=bio,
        followers=leading_count(followers_text),
        following=leading_count(following_text),
    )


@dataclass(frozen=True)
class XReadResult:
    """One read of one X page, in the shape `GET /x/read` answers with."""

    requested_url: str
    final_url: str
    capability: str
    anonymous: bool
    #: The handle we looked with. `None` when it could not be read from the navigation.
    viewer: str | None
    #: X sent us to a login screen. THIS, not a missing handle, means "no session": an unread
    #: handle (a slow page, a changed selector) sharing one field with a logout would send the
    #: human to log in for no reason.
    login_wall: bool
    #: A CONFIRMED session: no wall AND a handle read. Narrower than "not logged out".
    logged_in: bool
    unavailable: str | None
    posts: tuple[XPost, ...]
    profile: XProfile | None
    text: str
    #: What did not work although the read as a whole did. Empty means "no reservations".
    limitations: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "capability": self.capability,
            "anonymous": self.anonymous,
            "viewer": self.viewer,
            "login_wall": self.login_wall,
            "logged_in": self.logged_in,
            "unavailable": self.unavailable,
            "posts": [post.to_json() for post in self.posts],
            "profile": self.profile.to_json() if self.profile is not None else None,
            "text": self.text,
            "limitations": list(self.limitations),
        }


# -- the read --------------------------------------------------------------------------------------


def read_x_page(
    url: str,
    *,
    driver: Callable[[], Any],
    endpoint: Callable[[], str],
    anonymous: bool = False,
    timeout_s: float = X_NAV_TIMEOUT_S,
    render_wait_s: float = RENDER_WAIT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> XReadResult:
    """Render one X page in the capsule's Chrome and read it.

    `driver` returns the reader thread's ONE Playwright driver (`CdpPageFetcher.driver`): the sync
    API refuses a second driver on a thread that already runs one. `endpoint` returns the CDP
    address of the Chrome running NOW, which changes with every mode switch.

    `anonymous=False` reads in `contexts[0]`, the persistent profile, where the session a human
    put there lives. `anonymous=True` reads through a FRESH context (no cookies, no storage),
    created for this one page and destroyed after it, like `CdpPageFetcher.fetch(anonymous=True)`
    for Reddit: it answers "what does a visitor without our account see".

    Failures come out as exceptions, never as an empty result: "could not read" and "there are no
    posts" are two different answers and the product has to tell them apart.
    """
    canonical, capability = authorize_x_url(url)
    focal_id = _focal_id(canonical) if capability == CAPABILITY_POST else None
    try:
        browser = driver().chromium.connect_over_cdp(endpoint(), timeout=int(timeout_s * 1000))
    except Exception as exc:
        raise XReadFailed(f"brak połączenia CDP z Chrome kapsuły: {exc}") from exc
    fresh_context = None
    try:
        if not browser.contexts:
            raise XReadFailed("Chrome kapsuły nie ma kontekstu profilu, brak sesji do użycia")
        if anonymous:
            try:
                fresh_context = browser.new_context()
                page = fresh_context.new_page()
            except Exception as exc:
                raise XReadFailed(f"Chrome kapsuły nie dał kontekstu bez sesji: {exc}") from exc
        else:
            page = browser.contexts[0].new_page()
        try:
            return _read_open_page(
                page,
                canonical=canonical,
                capability=capability,
                focal_id=focal_id,
                anonymous=anonymous,
                timeout_s=timeout_s,
                render_wait_s=render_wait_s,
                clock=clock,
                sleep=sleep,
            )
        finally:
            # The page stays in the capsule's Chrome otherwise; a failed close must not replace
            # the answer (or the exception) the read produced.
            with contextlib.suppress(Exception):
                page.close()
    finally:
        if fresh_context is not None:
            with contextlib.suppress(Exception):
                fresh_context.close()
        with contextlib.suppress(Exception):
            browser.close()


def _focal_id(canonical: str) -> str | None:
    parsed = parse_status_path(urlsplit(canonical).path)
    return parsed[1] if parsed is not None else None


def _read_open_page(
    page: Any,
    *,
    canonical: str,
    capability: str,
    focal_id: str | None,
    anonymous: bool,
    timeout_s: float,
    render_wait_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> XReadResult:
    # THE PLATFORM'S STATUS MUST SURVIVE A REFUSED NAVIGATION. Measured on 26.09 in
    # `reader.CdpPageFetcher.fetch`: with a 4xx or 5xx and an empty body Chrome raises
    # ERR_HTTP_RESPONSE_CODE_FAILURE, `goto` leaves with an exception and the code is lost. Without
    # it "X rate-limits us" (429), "X refuses" (403) and "X is down" (5xx) are one message, and
    # they are three decisions: wait, stop, retry later. Only the main frame of this very address
    # counts: subresources carry codes of their own.
    captured: dict[str, int] = {}

    def remember(response: Any) -> None:
        if captured or getattr(response, "url", None) != canonical:
            return
        if getattr(response, "frame", None) != page.main_frame:
            return
        status = getattr(response, "status", None)
        if isinstance(status, int):
            captured["status"] = status

    page.on("response", remember)
    try:
        response = page.goto(
            canonical, wait_until="domcontentloaded", timeout=int(timeout_s * 1000)
        )
    except Exception as exc:
        status = captured.get("status")
        if status is not None and status >= 400:
            raise XPlatformRefused(status, canonical) from exc
        if "Timeout" in type(exc).__name__:
            raise XReadTimedOut(
                f"nawigacja do {canonical} nie skończyła się w {timeout_s:g} s"
            ) from exc
        raise XReadFailed(f"nawigacja do {canonical} nie powiodła się: {exc}") from exc
    status = getattr(response, "status", None)
    if isinstance(status, int) and (status in REFUSING_STATUSES or status >= 500):
        raise XPlatformRefused(status, canonical)

    limitations: list[str] = []
    if isinstance(status, int) and status >= 400:
        # A rendered 404 or 410 is read (it may say why), and `platform_status` stays `null` in
        # the answer because the navigation was not refused; the code itself is still a fact.
        limitations.append(f"X odpowiedział statusem {status}; stronę odczytano mimo to.")
    ended = _await_render(page, render_wait_s=render_wait_s, clock=clock, sleep=sleep)
    final_url = str(page.url)
    login_wall = is_login_wall(final_url)
    if is_account_locked(final_url):
        limitations.append(
            "X pokazuje ekran blokady konta (/account/access); potrzebny człowiek przy ekranie."
        )

    viewer: str | None = None
    if not anonymous and not login_wall:
        viewer = _read_viewer(page)
        if viewer is None:
            limitations.append("Nie odczytano uchwytu konta z nawigacji.")
    if (
        not anonymous
        and not login_wall
        and viewer is None
        and safe_count(page.locator(SEL_LOGGED_OUT_HINT)) > 0
    ):
        limitations.append("Strona pokazuje widok niezalogowany (przycisk logowania).")

    posts: list[XPost] = []
    card_texts: list[str | None] = []
    if not login_wall:
        posts, card_texts, post_limits = _read_posts(page, capability, focal_id)
        limitations.extend(post_limits)

    profile: XProfile | None = None
    if capability in _PROFILE_PAGES and not login_wall:
        profile = _read_profile(page)
        if profile is None:
            limitations.append("Nie odczytano nagłówka profilu.")

    body = safe_text(page.locator(SEL_BODY))
    if body is None:
        body = ""
        limitations.append("Nie odczytano tekstu strony.")

    unavailable: str | None = None
    if not login_wall:
        unavailable, marker_limits = detect_unavailable(
            body,
            post_texts=card_texts + [post.text for post in posts],
            capability=capability,
            focal_found=focal_id is not None and any(post.id == focal_id for post in posts),
        )
        limitations.extend(marker_limits)
        if unavailable is None and not posts and status in (404, 410):
            # The platform said "not found" in its status code; that is a measurement too.
            unavailable = "not_found"
    if ended == "timeout" and not posts and unavailable is None and not login_wall:
        limitations.append(f"Nie pojawił się żaden post w czasie odczytu ({render_wait_s:g} s).")

    return XReadResult(
        requested_url=canonical,
        final_url=final_url,
        capability=capability,
        anonymous=anonymous,
        viewer=viewer,
        login_wall=login_wall,
        logged_in=not login_wall and viewer is not None,
        unavailable=unavailable,
        posts=tuple(posts),
        profile=profile,
        text=body[:MAX_TEXT_CHARS],
        limitations=tuple(limitations),
    )


def _await_render(
    page: Any,
    *,
    render_wait_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> str:
    """Wait until the page has something to read, and say why the wait ended.

    X fetches the content after `domcontentloaded`, so a finished navigation does not mean there
    is anything to read. The wait ends on an EVENT (a login wall in the URL, posts whose count
    held still, an empty-state or error panel) or on a deadline measured by the clock, never on a
    number of loop turns: an empty profile and an empty search really exist, so running out of
    time is a result (`timeout`), not an error.
    """
    deadline = clock() + render_wait_s
    last_count = -1
    stable_since = clock()
    while True:
        if is_login_wall(str(page.url)):
            return "login_wall"
        count = safe_count(page.locator(SEL_ARTICLE))
        now = clock()
        if count > 0:
            if count != last_count:
                last_count, stable_since = count, now
            elif now - stable_since >= SETTLE_S:
                return "posts"
        elif safe_count(page.locator(SEL_PAGE_STATE)) > 0:
            return "page_state"
        if now >= deadline:
            return "posts" if count > 0 else "timeout"
        sleep(POLL_S)


def _read_viewer(page: Any) -> str | None:
    link = page.locator(SEL_NAV_PROFILE_LINK).first
    href: str | None = None
    try:
        link.wait_for(state="attached", timeout=VIEWER_WAIT_MS)
        href = link.get_attribute("href", timeout=READ_TIMEOUT_MS)
    except Exception:
        href = None
    handle = handle_from_profile_href(href)
    if handle is not None:
        return handle
    return handle_from_switcher_text(safe_text(page.locator(SEL_ACCOUNT_SWITCHER)))


@dataclass(frozen=True)
class _ArticleRead:
    permalink_href: str | None
    text: str | None
    text_source: str | None
    author_name: str | None
    published_at: str | None
    metrics: dict[str, int]
    card_text: str | None

    @property
    def post_id(self) -> str | None:
        parsed = parse_status_path(self.permalink_href)
        return parsed[1] if parsed is not None else None


def _read_posts(
    page: Any, capability: str, focal_id: str | None
) -> tuple[list[XPost], list[str | None], list[str]]:
    """Posts in document order, their card texts, and the limitations of the listing.

    On a post page the rows of the timeline are walked instead of the articles, because only the
    rows show where the conversation ends: X puts a "Discover more" heading row before unrelated
    posts, and calling those replies would be inventing a relation.
    """
    limitations: list[str] = []
    reads: list[_ArticleRead] = []
    seen = 0
    conversation_end: int | None = None
    if capability == CAPABILITY_POST:
        cells = page.locator(SEL_CELL)
        for index in range(min(safe_count(cells), MAX_CELLS)):
            cell = cells.nth(index)
            articles = cell.locator(SEL_ARTICLE)
            if safe_count(articles) == 0:
                if conversation_end is None and reads and _is_conversation_end(cell):
                    conversation_end = len(reads)
                continue
            seen += 1
            if len(reads) < MAX_POSTS:
                reads.append(_read_article(articles.first))
    if not reads:
        # Every other capability, and a post page whose rows did not match (the logged-out view
        # may render no rows at all).
        conversation_end = None
        articles = page.locator(SEL_ARTICLE)
        seen = safe_count(articles)
        for index in range(min(seen, MAX_POSTS)):
            reads.append(_read_article(articles.nth(index)))
    if seen > len(reads):
        limitations.append(
            f"Odczytano {len(reads)} postów z {seen} widocznych (limit {MAX_POSTS})."
        )

    relations: list[str | None] = [None] * len(reads)
    if capability == CAPABILITY_POST and focal_id is not None and reads:
        ids = [one.post_id for one in reads]
        if focal_id in ids:
            relations = assign_relations(ids, focal_id, conversation_end=conversation_end)
            if conversation_end is not None and conversation_end < len(reads):
                limitations.append(
                    f"{len(reads) - conversation_end} postów pod nagłówkiem „Discover more” nie "
                    "należy do rozmowy; ich relacja zostaje pusta."
                )
        else:
            limitations.append(
                f"Nie znaleziono na stronie posta {focal_id} z adresu; relacje nieustalone."
            )
    focal_article = any(
        relation == "focal" and one.text_source == "article"
        for one, relation in zip(reads, relations, strict=True)
    )
    if focal_article and "reply" not in relations:
        # Measured 27.09: an article's replies sit below its whole body, beyond even the agent's
        # portrait screen, and the reader never scrolls. The search operator reaches them.
        limitations.append(
            "To artykuł X: odpowiedzi są pod całą treścią i ten odczyt ich nie obejmuje. "
            f"Rozmowę pod nim zwraca wyszukiwanie conversation_id:{focal_id}."
        )
    posts = [
        post_from_fields(
            permalink_href=one.permalink_href,
            text=one.text,
            text_source=one.text_source,
            author_name=one.author_name,
            published_at=one.published_at,
            relation=relation,
            metrics=one.metrics,
        )
        for one, relation in zip(reads, relations, strict=True)
    ]
    return posts, [one.card_text for one in reads], limitations


def _read_article(article: Any) -> _ArticleRead:
    hrefs = safe_attributes(
        article.locator(SEL_STATUS_LINK), "href", limit=MAX_STATUS_LINKS_PER_POST
    )
    body_texts = safe_all_texts(article.locator(SEL_POST_TEXT))
    name_blocks = safe_all_texts(article.locator(SEL_POST_USER_NAME))
    permalink, embedded_first = own_permalink(hrefs, name_blocks[0] if name_blocks else None)
    card = safe_text(article)
    article_body = safe_text(article.locator(SEL_ARTICLE_BODY))
    if article_body:
        # An X article: the text is its title and body, not a `tweetText` (it has none of its own;
        # the one inside belongs to an embedded post).
        title = safe_text(article.locator(SEL_ARTICLE_TITLE))
        text = f"{title}\n\n{article_body}" if title else article_body
        text, source = text[:MAX_ARTICLE_CHARS], "article"
    else:
        text, source = choose_post_text(body_texts, name_blocks=len(name_blocks), card_text=card)
    # `time[datetime]` exists in the logged-in view only. The logged-out card says "23h" or
    # "Sep 3"; turning that into a timestamp would be precision nobody measured. When an embedded
    # post came first, the first `time` is ITS time; the post's own id then says when it was made.
    published_at = safe_attribute(article.locator(SEL_TIME), "datetime")
    if embedded_first:
        parsed = parse_status_path(permalink)
        published_at = snowflake_moment(parsed[1]) if parsed is not None else None
    return _ArticleRead(
        permalink_href=permalink,
        text=text,
        text_source=source,
        author_name=author_name_from_user_name(name_blocks[0]) if name_blocks else None,
        published_at=published_at,
        metrics=_read_metrics(article),
        card_text=card,
    )


def _read_metrics(article: Any) -> dict[str, int]:
    """Counters of one post. The action bar's label is the source; when it EXISTS, a counter it
    does not name stays absent. The per-button labels are read only when the bar has no label at
    all, so a zero X leaves out of the bar is not turned into a number by a second source."""
    bar_label = safe_attribute(article.locator(SEL_ACTION_BAR), "aria-label")
    if bar_label:
        return parse_metrics(bar_label)
    metrics: dict[str, int] = {}
    for key, selector in (
        ("replies", SEL_REPLY_BUTTON),
        ("reposts", SEL_REPOST_BUTTON),
        ("likes", SEL_LIKE_BUTTON),
        ("bookmarks", SEL_BOOKMARK_BUTTON),
        ("views", SEL_ANALYTICS_LINK),
    ):
        value = parse_metrics(safe_attribute(article.locator(selector), "aria-label")).get(key)
        if value is not None:
            metrics[key] = value
    return metrics


def _read_profile(page: Any) -> XProfile | None:
    name_block = safe_text(page.locator(SEL_PROFILE_NAME))
    if name_block is None:
        return None
    return parse_profile(
        name_block,
        bio=safe_text(page.locator(SEL_PROFILE_BIO)),
        following_text=safe_text(page.locator(SEL_FOLLOWING_LINK)),
        followers_text=safe_text(page.locator(SEL_FOLLOWERS_LINK)),
    )


def _is_conversation_end(cell: Any) -> bool:
    text = normalize_page_text(safe_text(cell))
    return any(marker in text for marker in CONVERSATION_END_MARKERS)


# -- small reads that never throw: a miss is `None`, and the caller says so ------------------------
#
# Each one counts BEFORE it reads: a Playwright read on a locator with no match waits for the
# element until its timeout, and a missing optional element would cost seconds on the reader's
# only thread. `.first` everywhere a single value is read, because locators are strict.


def safe_count(locator: Any) -> int:
    try:
        return int(locator.count())
    except Exception:
        return 0


def safe_text(locator: Any) -> str | None:
    if safe_count(locator) == 0:
        return None
    try:
        return locator.first.inner_text(timeout=READ_TIMEOUT_MS)
    except Exception:
        return None


def safe_all_texts(locator: Any) -> list[str]:
    try:
        return [str(one) for one in locator.all_inner_texts()]
    except Exception:
        return []


def safe_attribute(locator: Any, name: str) -> str | None:
    if safe_count(locator) == 0:
        return None
    try:
        return locator.first.get_attribute(name, timeout=READ_TIMEOUT_MS)
    except Exception:
        return None


def safe_attributes(locator: Any, name: str, *, limit: int) -> list[str]:
    values: list[str] = []
    for index in range(min(safe_count(locator), limit)):
        try:
            value = locator.nth(index).get_attribute(name, timeout=READ_TIMEOUT_MS)
        except Exception:
            value = None
        if value:
            values.append(value)
    return values
