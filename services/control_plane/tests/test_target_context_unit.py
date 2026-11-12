"""The post a reply answers, whether it went cold, and whether it is trending (v11, 13.10; no database).

THE FOUNDER (13.10), after a day of real use: drafts showed "Reply on X" and nothing else; stale ones
sat on page 1. Held here, pure:

  * THE ORDER AND THE BADGE (`opportunities/ranking.py`): fresh X first, a post that is growing fast
    beats one that is not, the badge needs a fresh post AND a measured rate over the line, a counter
    nobody read never makes a post trending, an unknown age scores zero;
  * THE FRESHNESS RULE (`opportunities/freshness.py::judge`): X 8 hours, Reddit 48, the age of an X
    post from its id when nothing else is known, a comment is never judged by its thread, an age
    nobody can establish is never called stale, and a post that is gone is stale at any age;
  * THE READ (`opportunities/snapshot.py`): the mirror's answer parsed defensively (another post's
    id, wrong types, control characters, an avatar on a host that is not the platform's), the
    browser's page and the Reddit thread turned into the same snapshot, a failing mirror is `None`;
  * THE AVATAR HOSTS: the server's list and the card's are one list.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from control_plane.opportunities import freshness, ranking, snapshot
from control_plane.opportunities.avatars import AVATAR_HOSTS, PICTURE_HOSTS, safe_avatar
from control_plane.opportunities.ranking import Metrics
from control_plane.pilot import send_checks
from control_plane.tools import apps_ui

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def _x_id(age: timedelta) -> str:
    """The id of an X post written `age` ago (its snowflake)."""
    return str((int((NOW - age).timestamp() * 1000) - send_checks._X_EPOCH_MS) << 22)


# ---------------------------------------------------------------------------
# Ranking and the badge
# ---------------------------------------------------------------------------


def test_the_badge_needs_a_fresh_post_and_a_measured_rate_over_the_line() -> None:
    fast = Metrics(views=10_000, likes=50)
    assert ranking.is_trending("X", NOW - 2 * H, fast, NOW) is True, "5000 views an hour"
    assert ranking.is_trending("X", NOW - 7 * H, fast, NOW) is False, "fresh means under six hours"
    assert ranking.is_trending("X", NOW - 2 * H, Metrics(views=1_000, likes=10), NOW) is False
    assert ranking.is_trending("X", NOW - 2 * H, Metrics(likes=80), NOW) is True, "40 likes an hour"
    assert ranking.is_trending("X", NOW - 2 * H, Metrics(replies=500, reposts=500), NOW) is False, (
        "replies and reposts are heat for the order, not a reason for the badge"
    )
    # Reddit: points and comments, fresh means a day
    assert ranking.is_trending("REDDIT", NOW - 10 * H, Metrics(likes=300), NOW) is True
    assert ranking.is_trending("REDDIT", NOW - 10 * H, Metrics(replies=100), NOW) is True
    assert ranking.is_trending("REDDIT", NOW - 30 * H, Metrics(likes=3000), NOW) is False
    assert ranking.is_trending("REDDIT", NOW - 10 * H, Metrics(likes=10, replies=2), NOW) is False


def test_a_counter_nobody_read_is_not_zero_and_never_makes_a_post_trending() -> None:
    assert ranking.is_trending("X", NOW - H, Metrics(), NOW) is False
    assert ranking.is_trending("X", NOW - H, None, NOW) is False
    assert ranking.is_trending("X", None, Metrics(views=10**6), NOW) is False, "no age, no badge"
    assert ranking.rank_score("X", NOW - H, None, NOW) > 0
    assert ranking.rank_score("X", None, Metrics(views=10**6), NOW) == 0.0, (
        "an unknown age goes last"
    )


def test_a_very_young_post_is_not_a_huge_rate() -> None:
    """Five minutes with 40 likes is not 480 an hour: the age is never less than half an hour."""
    assert ranking.is_trending("X", NOW - timedelta(minutes=5), Metrics(likes=10), NOW) is False
    assert ranking.is_trending("X", NOW - timedelta(minutes=5), Metrics(likes=15), NOW) is True
    assert ranking.age_of(NOW + H, NOW) == timedelta(0), "a clock a little ahead is not negative"


def test_the_order_prefers_fresh_then_fast_and_trending_gets_a_boost() -> None:
    fresh_quiet = ranking.rank_score("X", NOW - H, Metrics(views=100), NOW)
    old_quiet = ranking.rank_score("X", NOW - 9 * H, Metrics(views=100), NOW)
    old_viral = ranking.rank_score("X", NOW - 9 * H, Metrics(views=10**6), NOW)
    fresh_viral = ranking.rank_score("X", NOW - H, Metrics(views=100_000), NOW)
    assert fresh_quiet > old_quiet, "younger first"
    assert old_viral > old_quiet, "faster first, at the same age"
    assert fresh_viral > fresh_quiet, "and the trending one beats the quiet one of the same age"
    assert fresh_viral > old_viral
    # A day-old Reddit thread still outranks an X post of a day ago: they decay at their own rates.
    assert ranking.rank_score("REDDIT", NOW - 24 * H, None, NOW) > ranking.rank_score(
        "X", NOW - 24 * H, None, NOW
    )
    # Heat is a signal, not a veto: ten times over the line is about double, never ten.
    base = ranking.rank_score("X", NOW - H, Metrics(likes=1), NOW)
    ten = ranking.rank_score("X", NOW - H, Metrics(likes=300), NOW)
    assert ten / base < 4


def test_the_thresholds_are_the_documented_constants() -> None:
    assert ranking.X_VIEWS_PER_HOUR == 2000 and ranking.X_LIKES_PER_HOUR == 30
    assert ranking.REDDIT_POINTS_PER_HOUR == 25 and ranking.REDDIT_COMMENTS_PER_HOUR == 8
    assert ranking.FRESH["X"] == 6 * H and ranking.FRESH["REDDIT"] == 24 * H
    doc = Path(ranking.__file__).read_text(encoding="utf-8")
    for fragment in (
        "2000 views an hour",
        "30 likes an hour",
        "25 points an hour",
        "8 comments an hour",
    ):
        assert fragment in doc, fragment


# ---------------------------------------------------------------------------
# The freshness rule
# ---------------------------------------------------------------------------


def _row(**over):
    base = {
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": str(uuid4()),
        "revision_id": uuid4(),
        "target_external_id": f"x:{_x_id(2 * H)}",
        "target_url": "https://x.com/jack/status/1",
        "platform": "X",
        "opp_created": None,
        "opp_community": None,
        "opp_title": None,
        "opp_excerpt": None,
        "opp_author": None,
        "opp_gone": None,
        "thread_created": None,
        "thread_title": None,
        "thread_author": None,
        "thread_community": None,
    }
    base.update(over)
    return base


def test_an_x_reply_is_stale_after_eight_hours_by_the_age_in_the_post_id() -> None:
    fresh = _row(target_external_id=f"x:{_x_id(7 * H)}")
    assert freshness.judge(fresh, now=NOW, environ={}) is None
    stale = freshness.judge(_row(target_external_id=f"x:{_x_id(9 * H)}"), now=NOW, environ={})
    assert stale is not None and stale.code == "ZA_STARY" and stale.platform == "X"
    assert stale.limit == 8 * H and 8 * H < stale.age < 10 * H
    assert "post ma 9 h" in stale.reason and "najwyżej 8 h (X)" in stale.reason
    assert not re.search("[" + chr(0x2013) + chr(0x2014) + "]", stale.reason)


def test_a_reddit_post_is_stale_after_forty_eight_hours_by_what_the_project_knows() -> None:
    row = _row(
        target_external_id="t3_abc", platform="REDDIT", opp_created=NOW - 49 * H, opp_title="T"
    )
    stale = freshness.judge(row, now=NOW, environ={})
    assert stale is not None and stale.platform == "REDDIT" and stale.limit == 48 * H
    assert (
        freshness.judge(_row(**{**row, "opp_created": NOW - 47 * H}), now=NOW, environ={}) is None
    )
    # the observed thread is the second source
    viaThread = _row(target_external_id="t3_abc", platform="REDDIT", thread_created=NOW - 60 * H)
    assert freshness.judge(viaThread, now=NOW, environ={}) is not None


def test_an_age_nobody_can_establish_and_a_comment_target_are_not_judged() -> None:
    assert freshness.judge(_row(target_external_id="t3_abc", platform="REDDIT"), now=NOW) is None
    comment = _row(target_external_id="t1_abc", platform="REDDIT", opp_created=NOW - 500 * H)
    assert freshness.judge(comment, now=NOW) is None, "a fresh comment under an old post is live"
    assert freshness.judge(_row(target_external_id="x:not-a-number"), now=NOW) is None


def test_a_post_the_browser_said_is_gone_is_stale_at_any_age() -> None:
    gone = freshness.judge(_row(opp_gone=NOW - H, target_external_id=f"x:{_x_id(H)}"), now=NOW)
    assert gone is not None and gone.code == "ZNIKNAL"
    assert "już nie ma" in gone.reason and "Tekst się nie zmienił" in gone.reason
    assert (
        gone.skipped_json()["powod_kod"] == "ZNIKNAL" and gone.skipped_json()["przez"] == "system"
    )


def test_the_limits_are_the_publishers_and_configurable() -> None:
    env = {send_checks.ENV_MAX_AGE_X_HOURS: "2"}
    assert (
        freshness.judge(_row(target_external_id=f"x:{_x_id(3 * H)}"), now=NOW, environ=env)
        is not None
    )
    assert (
        freshness.judge(_row(target_external_id=f"x:{_x_id(3 * H)}"), now=NOW, environ={}) is None
    )


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def _fx(**over):
    tweet = {
        "id": "2103587050346250341",
        "text": "Anyone using agents for distribution?\x07",
        "author": {
            "name": "Jack",
            "screen_name": "jack",
            "avatar_url": "https://pbs.twimg.com/profile_images/1/a_normal.jpg",
        },
        "likes": 312,
        "retweets": 40,
        "replies": 12,
        "views": 48_000,
        "created_timestamp": int((NOW - 2 * H).timestamp()),
    }
    tweet.update(over)
    return {"code": 200, "message": "OK", "tweet": tweet}


URL = "https://x.com/jack/status/2103587050346250341"


def test_the_mirrors_answer_becomes_a_snapshot_with_every_counter_and_the_avatar() -> None:
    got = snapshot.parse_fxtwitter(_fx(), url=URL, status_id="2103587050346250341")
    assert got is not None
    assert (got.handle, got.name, got.external_id) == ("jack", "Jack", "x:2103587050346250341")
    assert got.text == "Anyone using agents for distribution?", "control characters are dropped"
    assert (got.views, got.likes, got.replies, got.reposts) == (48_000, 312, 12, 40)
    assert got.avatar_url == "https://pbs.twimg.com/profile_images/1/a_normal.jpg"
    assert got.created_at == NOW - 2 * H and got.source == "fxtwitter" and not got.gone


def test_the_mirrors_answer_is_data_a_stranger_wrote_and_is_held_to_its_shape() -> None:
    other = snapshot.parse_fxtwitter(_fx(id="999"), url=URL, status_id="2103587050346250341")
    assert other is None, "an answer about another post is not this post"
    for bad in (None, [], {}, {"code": 404, "tweet": None}, {"code": 200, "tweet": "x"}):
        assert snapshot.parse_fxtwitter(bad, url=URL, status_id="1") is None
    odd = snapshot.parse_fxtwitter(
        _fx(
            likes="many",
            views=-5,
            replies=True,
            retweets=None,
            author={
                "name": "x" * 500,
                "screen_name": "bad handle!",
                "avatar_url": "https://evil.example/a.png",
            },
            created_timestamp="yesterday",
        ),
        url=URL,
        status_id="2103587050346250341",
    )
    assert odd is not None
    assert (odd.likes, odd.views, odd.replies, odd.reposts) == (None, None, None, None), "not zero"
    assert odd.handle is None and len(odd.name or "") <= 100
    assert odd.avatar_url is None, "an avatar on a host that is not the platform's is dropped"
    assert odd.created_at == send_checks.x_created_at(None, "2103587050346250341"), "from the id"


def test_only_the_platforms_image_hosts_are_avatars() -> None:
    assert safe_avatar("https://pbs.twimg.com/profile_images/1/a.jpg") is not None
    assert safe_avatar("https://styles.redditmedia.com/t5_x/styles/a.png") is not None
    for bad in (
        None, "", "http://pbs.twimg.com/a.jpg", "https://evil.example/a.jpg",
        "https://pbs.twimg.com.evil.example/a.jpg", "https://user@pbs.twimg.com/a.jpg",
        "https://pbs.twimg.com:8443/a.jpg", "javascript:alert(1)", "https://pbs.twimg.com/" + "a" * 2000,
    ):  # fmt: skip
        assert safe_avatar(bad) is None, bad


def test_the_card_checks_the_same_hosts_the_server_does() -> None:
    html = apps_ui.OPPORTUNITIES_HTML
    listed = re.search(r"var PICTURE_HOSTS = \[(.*?)\];", html, re.S).group(1)
    assert tuple(re.findall(r'"([^"]+)"', listed)) == PICTURE_HOSTS
    assert set(AVATAR_HOSTS) <= set(PICTURE_HOSTS)


async def test_a_failing_mirror_is_none_never_an_exception() -> None:
    class Client:
        def __init__(self, response=None, error=None):
            self.response, self.error = response, error

        async def get(self, *_args, **_kwargs):
            if self.error:
                raise self.error
            return self.response

    ok = SimpleNamespace(status_code=200, content=b"{}", json=lambda: _fx())
    assert (await snapshot.fetch_fxtwitter(URL, client=Client(ok))).likes == 312
    assert (
        await snapshot.fetch_fxtwitter(
            URL, client=Client(SimpleNamespace(status_code=404, content=b"", json=lambda: {}))
        )
        is None
    )
    huge = SimpleNamespace(status_code=200, content=b"x" * 300_000, json=lambda: _fx())
    assert await snapshot.fetch_fxtwitter(URL, client=Client(huge)) is None
    assert await snapshot.fetch_fxtwitter(URL, client=Client(error=TimeoutError())) is None
    assert await snapshot.fetch_fxtwitter("https://example.com/x", client=Client(ok)) is None
    assert await snapshot.fetch_fxtwitter("not a url", client=Client(ok)) is None


def test_the_browsers_page_becomes_a_snapshot_and_a_gone_post_is_said_only_by_it() -> None:
    focal = SimpleNamespace(
        id="2103587050346250341", author="jack", author_name="Jack", text="hi",
        published_at=(NOW - H).isoformat(), metrics={"views": 5, "likes": 2},
    )  # fmt: skip
    page = SimpleNamespace(unavailable=None, by_id=lambda i: focal if i == focal.id else None)
    got = snapshot.from_x_page(page, url=URL, status_id=focal.id)
    assert got is not None and got.source == "x.read" and got.avatar_url is None
    assert (got.views, got.likes, got.replies, got.reposts) == (5, 2, None, None)
    gone = SimpleNamespace(unavailable="deleted", by_id=lambda i: None)
    deleted = snapshot.from_x_page(gone, url=URL, status_id=focal.id)
    assert deleted is not None and deleted.gone is True
    blocked = SimpleNamespace(unavailable="protected", by_id=lambda i: None)
    assert snapshot.from_x_page(blocked, url=URL, status_id=focal.id) is None, "not gone, not known"
    empty = SimpleNamespace(unavailable=None, by_id=lambda i: None)
    assert snapshot.from_x_page(empty, url=URL, status_id=focal.id) is None


def test_a_reddit_thread_becomes_a_snapshot_with_its_points_and_comments() -> None:
    thread = SimpleNamespace(
        post_id="abc123", subreddit="SaaS", title="How do you find users?", body="Body text",
        author="someone", url="https://www.reddit.com/r/SaaS/comments/abc123/x/",
        created_at=NOW - 3 * H, score=41, num_comments=7,
    )  # fmt: skip
    got = snapshot.from_reddit_thread(thread, url="u")
    assert got is not None and got.external_id == "t3_abc123" and got.community == "r/SaaS"
    assert (got.likes, got.replies, got.title, got.handle) == (
        41,
        7,
        "How do you find users?",
        "someone",
    )
    assert snapshot.from_reddit_thread(SimpleNamespace(post_id=""), url="u") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/jack/status/123",
        "https://twitter.com/jack/statuses/123/",
        "https://www.x.com/jack/status/123",
    ],
)
def test_the_post_address_names_the_handle_and_the_status(url: str) -> None:
    assert snapshot.x_target(url) == ("jack", "123")


def test_an_address_that_is_not_an_x_post_is_not_a_target() -> None:
    for url in (
        "http://x.com/jack/status/1",
        "https://evil.com/jack/status/1",
        "https://x.com/jack",
        "",
    ):
        assert snapshot.x_target(url) is None, url
