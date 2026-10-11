"""`build_sources`: which Reddit transport the process gets, decided by the environment alone.

D-023 gave Reddit the OAuth adapter; D-025 adds the capsule's browser. Exactly one of them is
built per process — the operator must be able to say, from the environment, which account and
which road read a thread. No database, no network: this is about wiring.
"""

from __future__ import annotations

from community_observer import RedditBrowserSource, RedditSource
from community_os_contracts import ObservationPlatform
from control_plane.observe.wiring import build_sources

READER = {
    "WENEXT_BROWSER_READER_URL": "http://capsule:8765",
    "WENEXT_BROWSER_READER_TOKEN": "token-testowy",
}
OAUTH = {
    "WENEXT_REDDIT_CLIENT_ID": "cid",
    "WENEXT_REDDIT_CLIENT_SECRET": "sec",
    "WENEXT_REDDIT_USERNAME": "u",
    "WENEXT_REDDIT_PASSWORD": "p",
}


def test_every_platform_has_exactly_one_adapter_regardless_of_environment() -> None:
    for env in ({}, READER, OAUTH, {**READER, **OAUTH}):
        sources = build_sources(env)
        assert set(sources) == set(ObservationPlatform), "adapter na każdą platformę, bez dziur"


def test_without_a_reader_url_reddit_is_the_oauth_adapter_even_unconfigured() -> None:
    reddit = build_sources({})[ObservationPlatform.REDDIT]
    assert isinstance(reddit, RedditSource)
    assert not reddit.configured, "bez zmiennych adapter API mówi CredentialsMissing, nie znika"


def test_a_reader_url_switches_reddit_to_the_capsule_browser() -> None:
    reddit = build_sources(READER)[ObservationPlatform.REDDIT]
    assert isinstance(reddit, RedditBrowserSource)
    assert reddit.require_login is True, "domyślnie odczyt tylko jako zalogowane konto"


def test_the_browser_wins_over_oauth_credentials_when_both_are_present() -> None:
    reddit = build_sources({**READER, **OAUTH})[ObservationPlatform.REDDIT]
    assert isinstance(reddit, RedditBrowserSource), "dwa adaptery na jedną platformę to niejasność"


def test_require_login_is_read_from_the_environment() -> None:
    for value in ("false", "0", "no", " FALSE "):
        reddit = build_sources({**READER, "WENEXT_REDDIT_REQUIRE_LOGIN": value})[
            ObservationPlatform.REDDIT
        ]
        assert isinstance(reddit, RedditBrowserSource) and reddit.require_login is False, value
    reddit = build_sources({**READER, "WENEXT_REDDIT_REQUIRE_LOGIN": "yes"})[
        ObservationPlatform.REDDIT
    ]
    assert isinstance(reddit, RedditBrowserSource) and reddit.require_login is True


def test_a_blank_reader_url_is_the_same_as_none() -> None:
    reddit = build_sources({"WENEXT_BROWSER_READER_URL": "   "})[ObservationPlatform.REDDIT]
    assert isinstance(reddit, RedditSource)


def test_the_capsule_source_is_wired_as_the_anonymous_and_removal_reader() -> None:
    """P0: the publisher gets its visibility ports from the same capsule source, or nothing.

    Without these two lines in the wiring every request would stay UNVERIFIED forever, and the
    unit tests of the publisher would keep passing, because they assert that very default.
    """
    from uuid import uuid4

    from control_plane.observe.wiring import build_publish_handlers

    with_capsule = build_publish_handlers(
        object(), organization_id=uuid4(), env={"WENEXT_BROWSER_READER_URL": "http://capsule:8765"}
    )
    assert with_capsule._anonymous is not None
    assert with_capsule._anonymous is with_capsule._source
    assert with_capsule._removals is with_capsule._source

    without = build_publish_handlers(object(), organization_id=uuid4(), env={})
    assert without._anonymous is None
    assert without._removals is None
