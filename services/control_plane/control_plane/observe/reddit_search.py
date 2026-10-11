"""Live Reddit search and thread reading for the agent: who describes this problem right now.

`threads.search` only searches what the observer already collected; this module asks Reddit
itself, through the SAME door the observer uses: the capsule's reader (`GET /read?url=...` on the
capsule's Chrome, the founder's session, D-025). Nothing here holds a credential, opens a socket of
its own or writes anywhere. Two operations, both read-only:

* `search_reddit` - `/search.json` or `/r/<sub>/search.json`, parsed into `RedditHit`;
* `read_reddit_thread` - `/r/<sub>/comments/<id>.json`, so a live hit can be read (post, state and
  the best comments) before anyone decides to answer it.

THE TRANSPORT IS NOT NEW. `capsule_session.probe_from_env()` already builds the Reddit adapter from
`WENEXT_BROWSER_READER_URL` and `WENEXT_BROWSER_READER_TOKEN` (the pair `capsule_env()` forwards to
the tool process), and `RedditBrowserSource` already owns the ladder for what Reddit can answer:
headless Chrome refused with 403, 429, a redirect to the login page, 404, a body that is not JSON.
`RedditBrowserSource._json_of` says in its own docstring that two copies of that ladder are the
bug, so this module does not carry one. It reaches the ladder through ONE seam (`_CapsuleReader`),
which is also the only line that touches a private method of the adapter; when `community_observer`
grows a public "read this allowlisted listing" method, only that line changes.

THE SECOND LOCK IS THE CAPSULE'S. Whatever URL this module builds, the reader refuses everything
outside `browser_supervisor.reader.authorize_url`. The query is therefore built from closed sets
that mirror that allowlist, and `tests/test_reddit_search.py` runs every URL built here through the
reader's own `authorize_url`, so the two cannot drift apart without a red test.

TWO KINDS OF FAILURE, kept apart for the caller:

* `ValueError` - the CALL is wrong (empty query, `sort="hot"`, `limit=500`, a bare post id with no
  community). Retrying the same call cannot help; the message says what to change.
* `RedditSearchUnavailable` - the call was fine and the ENVIRONMENT did not deliver (capsule not
  configured, down, an older image that refuses the search URL, Reddit's 429 or 403). Its text is
  one Polish sentence for the founder's agent, `kind` is a closed value for code, `detail` keeps the
  adapter's full message for the log.

What this module never does: log in, post, vote, join, follow a link off Reddit, or search for a
person. Posts by deleted accounts, removed posts, NSFW and stickied posts are not returned at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from community_observer import (
    CredentialsMissing,
    SourceError,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)
from community_observer.reddit_payload import (
    PERMALINK_BASE,
    POST_ID,
    author,
    body,
    created_at,
    listing_children,
)

from .capsule_session import probe_from_env

#: The closed sets of the search query, spelled the way the capsule's reader allows them
#: (`browser_supervisor.reader.SEARCH_QUERY`). The contract test pins the two together.
SORTS: tuple[str, ...] = ("new", "relevance", "top", "comments")
TIME_SPANS: tuple[str, ...] = ("hour", "day", "week", "month", "year", "all")
MIN_LIMIT = 1
MAX_LIMIT = 100
MAX_QUERY_CHARS = 256

#: What `q` may contain: the reader's character class (`SEARCH_QUERY["q"]`), without the lookahead
#: that refuses a blank query (checked here on its own, with its own message).
_QUERY_CHARS = re.compile(r"[\w \"'\-.,:;!?()+/#@&]+")

#: Typographic characters a model writes by habit (curly quotes, en and em dash, ellipsis, no-break
#: space), by code point and mapped to the plain ones the reader allows. Code points on purpose:
#: this file must not carry a long dash itself, and the formatter rewrites a `u2014` escape in a
#: string back into the literal character.
_QUERY_FOLDING = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
    0x2026: "...",
    0x00A0: " ",
}

_SUBREDDIT = re.compile(r"[A-Za-z0-9_]{2,21}")
_PERMALINK = re.compile(r"^/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{1,12})(?:/|$)")
_REDDIT_HOSTS = frozenset({"www.reddit.com", "reddit.com", "old.reddit.com"})

EXCERPT_CHARS = 300
COMMENT_EXCERPT_CHARS = 400
THREAD_BODY_CHARS = 8000
DEFAULT_COMMENTS = 30
MAX_COMMENTS = 100
MAX_COMMENT_DEPTH = 3
_REMOVED_BODIES = frozenset({"[removed]", "[deleted]"})

#: Search is pace plus navigation (the reader spaces fetches by 2 s and a navigation takes seconds)
#: plus a possible switch of the capsule to a headful Chrome, so the observer's 25 s is too tight.
SEARCH_TIMEOUT = httpx.Timeout(75.0, connect=5.0)

# -- why the environment did not deliver (the closed values of `RedditSearchUnavailable.kind`) ----
NOT_CONFIGURED = "not_configured"
REFUSED = "refused"
CAPSULE_DOWN = "capsule_down"
MISCONFIGURED = "misconfigured"
RATE_LIMITED = "rate_limited"
BLOCKED = "blocked"
LOGIN_REQUIRED = "login_required"
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"
UNAVAILABLE_KINDS: frozenset[str] = frozenset(
    {
        NOT_CONFIGURED,
        REFUSED,
        CAPSULE_DOWN,
        MISCONFIGURED,
        RATE_LIMITED,
        BLOCKED,
        LOGIN_REQUIRED,
        NOT_FOUND,
        UNAVAILABLE,
    }
)

_REASONS: Mapping[str, str] = {
    # "w tym procesie" is deliberate, as in `capsule_session.NOT_CONFIGURED_HERE`: the tool process
    # and the api process have separate environments, and a sentence without those words once sent
    # two people looking for a broken capsule that was running.
    NOT_CONFIGURED: (
        "Wyszukiwanie na Reddicie jest niedostępne: kapsuła nie jest skonfigurowana w tym "
        "procesie, więc nie ma czym czytać Reddita."
    ),
    REFUSED: (
        "Czytnik kapsuły nie przyjmuje jeszcze wyszukiwania Reddita, bo działa na starszym "
        "obrazie niż ten kod, więc trzeba odświeżyć kapsułę."
    ),
    CAPSULE_DOWN: "Czytnik kapsuły nie odpowiada, więc nie da się teraz przeszukać Reddita.",
    # The capsule IS answering here: the sentence must not send anyone to look for a dead capsule.
    MISCONFIGURED: (
        "Czytnik kapsuły odrzucił token tego procesu, więc trzeba wyrównać "
        "WENEXT_BROWSER_READER_TOKEN w api i w kapsule."
    ),
    RATE_LIMITED: (
        "Reddit ogranicza zapytania z konta kapsuły (429), więc wyszukiwanie wznowi się dopiero "
        "za kilka minut."
    ),
    BLOCKED: (
        "Reddit odmówił odczytu (403), możliwa blokada anty-botowa, więc nie da się teraz "
        "przeszukać Reddita."
    ),
    LOGIN_REQUIRED: (
        "Reddit wymaga zalogowania albo nie wpuszcza konta kapsuły do tej społeczności, więc "
        "człowiek musi zalogować konto albo dołączyć do niej."
    ),
    UNAVAILABLE: "Reddit nie odpowiedział poprawnie; spróbuj ponownie za chwilę.",
}


class RedditSearchUnavailable(Exception):
    """The call was valid and the environment did not deliver. `str(exc)` is the Polish sentence;
    `kind` is one of `UNAVAILABLE_KINDS`; `detail` is the adapter's own message, for logs."""

    def __init__(self, reason: str, *, kind: str = UNAVAILABLE, detail: str = "") -> None:
        if kind not in UNAVAILABLE_KINDS:
            raise ValueError(f"nieznany rodzaj niedostępności: {kind!r}")
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class RedditHit:
    """One post that matched. Never a removed, deleted, NSFW or stickied one."""

    post_id: str
    subreddit: str
    title: str
    #: The first ~300 characters of the post's text, whitespace collapsed; `""` for a link post.
    excerpt: str
    author: str
    #: `https://www.reddit.com/r/<sub>/comments/<id>/<slug>/`, the canonical permalink.
    url: str
    created_at: datetime  # timezone-aware, UTC
    score: int
    num_comments: int
    #: A locked post takes no new comments and an archived one (about six months old) takes no
    #: replies at all: both matter to anyone who wants to answer, neither to someone doing research.
    locked: bool = False
    archived: bool = False


@dataclass(frozen=True)
class RedditComment:
    comment_id: str
    #: `None` for a comment whose author deleted the account.
    author: str | None
    excerpt: str
    #: `None` while Reddit hides the score (`score_hidden`).
    score: int | None
    created_at: datetime
    #: 0 = a reply to the post itself.
    depth: int
    #: Written by the post's author (Reddit's `is_submitter`).
    is_op: bool


@dataclass(frozen=True)
class RedditThread:
    post_id: str
    subreddit: str
    title: str
    #: The post's text (at most `THREAD_BODY_CHARS`, then `...`); `""` for a link post or a post
    #: whose text was removed.
    body: str
    author: str | None
    url: str
    created_at: datetime
    score: int
    num_comments: int
    locked: bool
    archived: bool
    #: Chronological, `depth <= max_depth`; the best-scored ones when there are more than asked.
    comments: tuple[RedditComment, ...]
    #: `True` when `comments` is not all of them AMONG THE DEPTHS ASKED FOR: Reddit left a "more"
    #: stub there, or there were more than `max_comments` and we kept the best. Replies deeper than
    #: `max_depth` are not counted: the caller did not ask for them.
    comments_truncated: bool


class RedditReader(Protocol):
    """What `search_reddit` and `read_reddit_thread` need from a reader. Tests inject a fake."""

    async def read_listing(self, url: str, *, where: str) -> Any:
        """The JSON of one allowlisted listing URL, or `community_observer`'s `SourceError`."""
        ...

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        """The raw `[post listing, comment listing]` pair of `r/<sub>` thread `<id>`."""
        ...


class _CapsuleReader:
    """`RedditReader` over `RedditBrowserSource`, i.e. over the capsule. The one seam."""

    def __init__(self, source: Any) -> None:
        self._source = source

    async def read_listing(self, url: str, *, where: str) -> Any:
        # The adapter's `_read_json` is the ladder (403, 429, login redirect, 404, non-JSON) and
        # also the headful check; it does not ask for a logged-in session, which a public search
        # does not need. It has no public name yet.
        return await self._source._read_json(url, where=where)

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        return await self._source.read_thread_payload(community_key, external_id)

    async def aclose(self) -> None:
        await self._source.aclose()


def reader_from_env(env: Mapping[str, str] | None = None) -> RedditReader | None:
    """The capsule's reader built from `WENEXT_BROWSER_READER_URL` / `..._TOKEN`, or `None` when the
    capsule is not configured in this process. The same construction as every other Reddit read
    (`probe_from_env`), with a timeout that fits a search."""
    source = probe_from_env(env, timeout=SEARCH_TIMEOUT)
    return None if source is None else _CapsuleReader(source)


# -- search --------------------------------------------------------------------------------------


def normalize_query(query: str) -> str:
    """`query` as the reader will accept it: typographic quotes and dashes made plain, all
    whitespace (newlines and tabs too) collapsed to single spaces. `ValueError` when it is empty,
    longer than `MAX_QUERY_CHARS`, or still holds a character the reader does not allow."""
    if not isinstance(query, str):
        raise ValueError("Zapytanie musi być tekstem.")
    cleaned = " ".join(query.translate(_QUERY_FOLDING).split())
    if not cleaned:
        raise ValueError("Zapytanie nie może być puste.")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise ValueError(
            f"Zapytanie ma {len(cleaned)} znaków, a wyszukiwarka przyjmuje najwyżej "
            f"{MAX_QUERY_CHARS}. Skróć je do kilku słów kluczowych."
        )
    refused = sorted({char for char in cleaned if _QUERY_CHARS.fullmatch(char) is None})
    if refused:
        raise ValueError(
            "Zapytanie zawiera znaki, których wyszukiwarka nie przyjmuje: "
            f"{' '.join(refused)}. Użyj liter, cyfr i prostej interpunkcji."
        )
    return cleaned


def normalize_subreddit(subreddit: str) -> str:
    """`devops`, `r/devops` and `/r/devops/` all mean `devops`; anything else is a `ValueError`."""
    if not isinstance(subreddit, str):
        raise ValueError("Nazwa społeczności musi być tekstem.")
    name = subreddit.strip().strip("/")
    if name[:2].lower() == "r/":
        name = name[2:]
    if _SUBREDDIT.fullmatch(name) is None:
        raise ValueError(
            f"Nazwa społeczności {subreddit!r} jest niepoprawna: 2-21 liter, cyfr lub `_`, "
            "np. `devops` albo `r/devops`."
        )
    return name


def build_search_url(*, query: str, subreddit: str | None, sort: str, time: str, limit: int) -> str:
    """The search URL, already in the canonical form the capsule's reader re-derives, so that what
    is sent is exactly what the reader checks. Validates every argument."""
    cleaned = normalize_query(query)
    name = None if subreddit is None else normalize_subreddit(subreddit)
    if sort not in SORTS:
        raise ValueError(f"Sortowanie {sort!r} jest niedozwolone; dozwolone: {', '.join(SORTS)}.")
    if time not in TIME_SPANS:
        raise ValueError(
            f"Okno czasu {time!r} jest niedozwolone; dozwolone: {', '.join(TIME_SPANS)}."
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise ValueError(f"Limit musi być liczbą od {MIN_LIMIT} do {MAX_LIMIT}, nie {limit!r}.")
    params = [("q", cleaned)]
    if name is not None:
        params.append(("restrict_sr", "on"))
    params += [
        ("sort", sort),
        ("t", time),
        ("limit", str(limit)),
        ("type", "link"),
        ("raw_json", "1"),
    ]
    path = "/search.json" if name is None else f"/r/{name}/search.json"
    return f"{PERMALINK_BASE}{path}?{urlencode(params)}"


async def search_reddit(
    *,
    query: str,
    subreddit: str | None = None,
    sort: str = "new",
    time: str = "week",
    limit: int = 25,
    reader: RedditReader | None = None,
) -> list[RedditHit]:
    """Posts that match `query` on Reddit NOW, newest first by default, within `subreddit` when
    given (all of Reddit otherwise). Read-only.

    `ValueError` for a bad argument (see the module docstring), `RedditSearchUnavailable` when the
    capsule or Reddit did not deliver. An empty list means Reddit answered and nothing matched, or
    everything matching was removed, NSFW, stickied or from a deleted account.
    """
    url = build_search_url(query=query, subreddit=subreddit, sort=sort, time=time, limit=limit)
    where = urlsplit(url).path
    owned = reader is None
    reader = reader if reader is not None else _default_reader()
    try:
        try:
            payload = await reader.read_listing(url, where=where)
            children = listing_children(payload, where=where)
        except SourceError as exc:
            raise _unavailable(exc, subject=subreddit) from exc
    finally:
        if owned:
            await _close(reader)
    hits: list[RedditHit] = []
    for child in children:
        hit = hit_from_child(child)
        if hit is not None:
            hits.append(hit)
    return hits


def hit_from_child(child: Mapping[str, Any]) -> RedditHit | None:
    """One `children[]` entry of a search listing as a `RedditHit`, or `None` when it must not be
    shown: not a post, a profile post, removed, deleted (text or author), NSFW, stickied, an ad,
    or too malformed to trust."""
    if child.get("kind") != "t3":
        return None
    data = child.get("data")
    if not isinstance(data, Mapping):
        return None
    post_id = str(data.get("id") or "")
    subreddit = str(data.get("subreddit") or "")
    if POST_ID.match(post_id) is None or _SUBREDDIT.fullmatch(subreddit) is None:
        return None
    if data.get("subreddit_type") == "user":
        return None
    if data.get("over_18") or data.get("stickied") or data.get("promoted"):
        return None
    if data.get("removed_by_category"):
        return None
    selftext = data.get("selftext")
    if isinstance(selftext, str) and selftext.strip() in _REMOVED_BODIES:
        return None
    poster = author(data)
    title = _collapse(data.get("title"))
    if poster is None or not title:
        return None
    try:
        posted = created_at(data)
    except SourceUnavailable:
        return None
    return RedditHit(
        post_id=post_id,
        subreddit=subreddit,
        title=title,
        excerpt=_excerpt(body(data, "selftext"), EXCERPT_CHARS),
        author=poster,
        url=_permalink(data, subreddit, post_id),
        created_at=posted,
        score=_int(data.get("score")) or 0,
        num_comments=_int(data.get("num_comments")) or 0,
        locked=bool(data.get("locked")),
        archived=bool(data.get("archived")),
    )


# -- thread --------------------------------------------------------------------------------------


async def read_reddit_thread(
    url_or_id: str,
    reader: RedditReader | None = None,
    *,
    subreddit: str | None = None,
    max_comments: int = DEFAULT_COMMENTS,
    max_depth: int = 0,
) -> RedditThread:
    """One thread, read-only: the post, whether it is locked or archived, and its comments.

    `url_or_id` is a thread permalink (`https://www.reddit.com/r/<sub>/comments/<id>/...`) or a
    bare post id (`1w6ln9w` or `t3_1w6ln9w`) together with `subreddit=`. The capsule reads a
    thread by `/r/<sub>/comments/<id>.json`, so an id alone cannot be read. `max_depth=0` returns
    replies to the post only; up to `MAX_COMMENT_DEPTH` brings replies to replies.

    `ValueError` for an unreadable argument, `RedditSearchUnavailable` for the environment (and for
    a thread Reddit does not know, `kind="not_found"`).
    """
    name, post_id = parse_thread_reference(url_or_id, subreddit)
    if isinstance(max_comments, bool) or not isinstance(max_comments, int):
        raise ValueError("max_comments musi być liczbą całkowitą.")
    if not 1 <= max_comments <= MAX_COMMENTS:
        raise ValueError(f"max_comments musi być z zakresu 1-{MAX_COMMENTS}, nie {max_comments}.")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise ValueError("max_depth musi być liczbą całkowitą.")
    if not 0 <= max_depth <= MAX_COMMENT_DEPTH:
        raise ValueError(f"max_depth musi być z zakresu 0-{MAX_COMMENT_DEPTH}, nie {max_depth}.")
    owned = reader is None
    reader = reader if reader is not None else _default_reader()
    try:
        try:
            payload = await reader.read_thread_payload(f"r/{name}", post_id)
            return parse_thread(
                payload,
                subreddit=name,
                post_id=post_id,
                max_comments=max_comments,
                max_depth=max_depth,
            )
        except SourceError as exc:
            raise _unavailable(exc, subject=name, thread=True) from exc
    finally:
        if owned:
            await _close(reader)


def parse_thread_reference(url_or_id: str, subreddit: str | None = None) -> tuple[str, str]:
    """`(subreddit, post id)` from a permalink, or from a bare id plus `subreddit`."""
    if not isinstance(url_or_id, str) or not url_or_id.strip():
        raise ValueError("Podaj adres wątku Reddita albo identyfikator posta.")
    text = url_or_id.strip()
    if "/" in text or "." in text:
        parts = urlsplit(text if "//" in text else f"https://{text}")
        match = _PERMALINK.match(parts.path)
        if (
            parts.scheme not in {"http", "https"}
            or parts.netloc.lower() not in _REDDIT_HOSTS
            or match is None
        ):
            raise ValueError(
                f"{url_or_id!r} nie jest adresem wątku Reddita w postaci "
                "https://www.reddit.com/r/<społeczność>/comments/<id>/..."
            )
        return match.group(1), match.group(2)
    post_id = text[3:] if text.startswith("t3_") else text
    if POST_ID.match(post_id) is None:
        raise ValueError(
            f"Identyfikator posta Reddita to małe litery i cyfry (base36), nie {text!r}."
        )
    if subreddit is None:
        raise ValueError(
            "Sam identyfikator posta nie wystarcza, bo wątek czyta się po adresie "
            "/r/<społeczność>/comments/<id>: podaj pełny adres wątku albo parametr subreddit."
        )
    return normalize_subreddit(subreddit), post_id


def parse_thread(
    payload: Any, *, subreddit: str, post_id: str, max_comments: int, max_depth: int
) -> RedditThread:
    """Reddit's `[post listing, comment listing]` pair as a `RedditThread`."""
    where = f"/r/{subreddit}/comments/{post_id}.json"
    if not isinstance(payload, list) or len(payload) < 2:
        raise SourceUnavailable(f"Reddit: odpowiedź {where} nie jest parą listingów")
    posts = listing_children(payload[0], where=f"{where}[0]")
    data = posts[0].get("data") if posts and posts[0].get("kind") == "t3" else None
    if not isinstance(data, Mapping):
        raise ThreadNotFound(f"Reddit: brak posta {post_id} w odpowiedzi")
    comments, more_stub = _comments(listing_children(payload[1], where=f"{where}[1]"), max_depth)
    truncated = more_stub
    if len(comments) > max_comments:
        # The best-scored ones, shown in the order they were written (a hidden score ranks as 0).
        ranked = sorted(range(len(comments)), key=lambda i: (-(comments[i].score or 0), i))
        keep = sorted(ranked[:max_comments])
        comments = [comments[i] for i in keep]
        truncated = True
    text = body(data, "selftext") or ""
    if len(text) > THREAD_BODY_CHARS:
        text = text[:THREAD_BODY_CHARS].rstrip() + "..."
    posted = created_at(data)
    thread_sub = str(data.get("subreddit") or subreddit)
    return RedditThread(
        post_id=post_id,
        subreddit=thread_sub if _SUBREDDIT.fullmatch(thread_sub) else subreddit,
        title=_collapse(data.get("title")) or f"(bez tytułu) {post_id}",
        body=text,
        author=author(data),
        url=_permalink(data, subreddit, post_id),
        created_at=posted,
        score=_int(data.get("score")) or 0,
        num_comments=_int(data.get("num_comments")) or 0,
        locked=bool(data.get("locked")),
        archived=bool(data.get("archived")),
        comments=tuple(comments),
        comments_truncated=truncated,
    )


def _comments(
    children: list[Mapping[str, Any]], max_depth: int, depth: int = 0
) -> tuple[list[RedditComment], bool]:
    """Depth-first, chronological as Reddit sorted them (`sort=old`). The flag is `True` when a
    `more` stub was met at a depth we were asked for. Comments with no text left (removed or
    deleted) are skipped; their replies are still read."""
    found: list[RedditComment] = []
    more = False
    for child in children:
        kind = child.get("kind")
        data = child.get("data")
        if kind == "more":
            more = True
            continue
        if kind != "t1" or not isinstance(data, Mapping) or not data.get("id"):
            continue
        text = body(data, "body")
        try:
            posted = created_at(data)
        except SourceUnavailable:
            continue
        if text is not None:
            found.append(
                RedditComment(
                    comment_id=str(data["id"]),
                    author=author(data),
                    excerpt=_excerpt(text, COMMENT_EXCERPT_CHARS),
                    score=None if data.get("score_hidden") else _int(data.get("score")),
                    created_at=posted,
                    depth=depth,
                    is_op=bool(data.get("is_submitter")),
                )
            )
        replies = data.get("replies")
        if depth < max_depth and isinstance(replies, Mapping):
            inner = replies.get("data")
            nested = inner.get("children") if isinstance(inner, Mapping) else None
            if isinstance(nested, list):
                deeper, deeper_more = _comments(
                    [item for item in nested if isinstance(item, Mapping)], max_depth, depth + 1
                )
                found.extend(deeper)
                more = more or deeper_more
    return found, more


# -- helpers -------------------------------------------------------------------------------------


def _default_reader() -> RedditReader:
    reader = reader_from_env()
    if reader is None:
        raise RedditSearchUnavailable(
            _REASONS[NOT_CONFIGURED],
            kind=NOT_CONFIGURED,
            detail="WENEXT_BROWSER_READER_URL nie jest ustawiony w tym procesie",
        )
    return reader


async def _close(reader: RedditReader) -> None:
    aclose = getattr(reader, "aclose", None)
    if aclose is not None:
        await aclose()


def _unavailable(
    exc: SourceError, *, subject: str | None = None, thread: bool = False
) -> RedditSearchUnavailable:
    """`community_observer`'s error as the sentence and the kind the caller gets. The adapter's
    own wording decides the kind: its messages are the only place the status survives."""
    detail = str(exc)
    if isinstance(exc, UnknownCommunity):
        name = f" r/{subject.removeprefix('r/')}" if subject else ""
        return RedditSearchUnavailable(
            f"Reddit nie zna społeczności{name}; sprawdź jej nazwę.",
            kind=NOT_FOUND,
            detail=detail,
        )
    if isinstance(exc, ThreadNotFound):
        return RedditSearchUnavailable(
            "Reddit nie zna tego wątku; mógł zostać usunięty.", kind=NOT_FOUND, detail=detail
        )
    if isinstance(exc, CredentialsMissing):
        return RedditSearchUnavailable(_REASONS[LOGIN_REQUIRED], kind=LOGIN_REQUIRED, detail=detail)
    status = re.search(r"Reddit odpowiedział (\d{3})", detail)
    reddit_status = int(status.group(1)) if status else None
    if "odrzucił URL" in detail:
        kind = REFUSED
    elif "odrzucił token" in detail:
        kind = MISCONFIGURED
    elif reddit_status == 429:
        kind = RATE_LIMITED
    elif reddit_status == 403:
        kind = BLOCKED
    elif reddit_status is None and ("czytnik kapsuły" in detail or "kapsuła" in detail):
        kind = CAPSULE_DOWN
    else:
        kind = UNAVAILABLE
    return RedditSearchUnavailable(_REASONS[kind], kind=kind, detail=detail)


def _collapse(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _excerpt(text: str | None, limit: int) -> str:
    flat = _collapse(text)
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "..."


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    return None


def _permalink(data: Mapping[str, Any], subreddit: str, post_id: str) -> str:
    """The canonical thread address. Reddit's own `permalink` when it has the expected shape (it
    carries the slug), otherwise the slug-less form, which Reddit resolves just as well."""
    permalink = data.get("permalink")
    if isinstance(permalink, str) and _PERMALINK.match(permalink):
        return f"{PERMALINK_BASE}{permalink}"
    return f"{PERMALINK_BASE}/r/{subreddit}/comments/{post_id}/"


__all__ = [
    "BLOCKED",
    "CAPSULE_DOWN",
    "LOGIN_REQUIRED",
    "MAX_LIMIT",
    "MAX_QUERY_CHARS",
    "MISCONFIGURED",
    "NOT_CONFIGURED",
    "NOT_FOUND",
    "RATE_LIMITED",
    "REFUSED",
    "SORTS",
    "TIME_SPANS",
    "UNAVAILABLE",
    "UNAVAILABLE_KINDS",
    "RedditComment",
    "RedditHit",
    "RedditReader",
    "RedditSearchUnavailable",
    "RedditThread",
    "build_search_url",
    "hit_from_child",
    "normalize_query",
    "normalize_subreddit",
    "parse_thread",
    "parse_thread_reference",
    "read_reddit_thread",
    "reader_from_env",
    "search_reddit",
]
