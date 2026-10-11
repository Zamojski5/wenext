"""Reading real communities — Outcome A (`docs/OUTCOMES.md` §3).

One port (`CommunitySource`), one adapter per platform and transport, one shape for what comes
back. Nothing in this package writes anywhere or knows about the database. Nothing here drives a
browser either: `RedditBrowserSource` (D-025) asks the capsule's reader for a page body over HTTP,
and the reader — not this package — holds the CDP connection.

`__all__` is checked by `tests/test_repo_consistency.py::test_every_all_entry_is_actually_bound`.
"""

from .hackernews import COMMUNITIES as HACKER_NEWS_COMMUNITIES
from .hackernews import HackerNewsSource
from .models import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    ThreadSnapshot,
    raw_digest,
)
from .reddit import REQUIRED_ENV as REDDIT_REQUIRED_ENV
from .reddit import RedditCredentials, RedditSource
from .reddit_browser import ENV_READER_TOKEN as BROWSER_READER_TOKEN_ENV
from .reddit_browser import ENV_READER_URL as BROWSER_READER_URL_ENV
from .reddit_browser import ENV_REQUIRE_LOGIN as REDDIT_REQUIRE_LOGIN_ENV
from .reddit_browser import (
    READER_TOKEN_HEADER,
    VIEWER_ANONYMOUS,
    VIEWER_SESSION,
    BrowserReader,
    HttpBrowserReader,
    PageRead,
    RedditBrowserSource,
    RemovalReport,
    SessionState,
    parse_removal_reports,
)
from .reddit_browser import build_from_env as build_reddit_browser_source
from .source import (
    COMMUNITY_SOURCE_METHODS,
    MAX_LISTING,
    CommunitySource,
    CredentialsMissing,
    SourceError,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)
from .text import html_to_text

__all__ = [
    "BROWSER_READER_TOKEN_ENV",
    "BROWSER_READER_URL_ENV",
    "COMMUNITY_SOURCE_METHODS",
    "HACKER_NEWS_COMMUNITIES",
    "MAX_LISTING",
    "READER_TOKEN_HEADER",
    "REDDIT_REQUIRED_ENV",
    "REDDIT_REQUIRE_LOGIN_ENV",
    "VIEWER_ANONYMOUS",
    "VIEWER_SESSION",
    "BrowserReader",
    "CommunityRef",
    "CommunitySource",
    "CredentialsMissing",
    "HackerNewsSource",
    "HttpBrowserReader",
    "ObservedComment",
    "ObservedThread",
    "PageRead",
    "RedditBrowserSource",
    "RedditCredentials",
    "RedditSource",
    "RemovalReport",
    "SessionState",
    "SourceError",
    "SourceUnavailable",
    "ThreadMetrics",
    "ThreadNotFound",
    "ThreadSnapshot",
    "UnknownCommunity",
    "build_reddit_browser_source",
    "html_to_text",
    "parse_removal_reports",
    "raw_digest",
]
