"""The learning loop without a database: buckets, the n-guard, horizons, the hook rules (13.10).

WHAT THIS HOLDS DOWN. The founder asked for a model that is "deliberately simple and explainable".
That is a promise about what it will NOT do, and each case below is one way it could lie:

  * a claim made from two items per group, or from groups that mix different horizons;
  * a "clear" gap that is two views against none;
  * a median that treats an unread counter as zero;
  * a lesson without the n of both sides, or with a number nobody computed;
  * a snapshot assigned to a horizon it does not belong to;
  * the founder's rules for the tone drifting between the three places that say them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from control_plane.learning import diary, model
from control_plane.learning.model import (
    MIN_N,
    Item,
    aggregate,
    claim_for,
    classify_hook,
    due_horizons,
    find_claims,
    horizon_of,
    payload_of,
    statement_of,
)
from control_plane.learning.service import (
    HARVEST_GRACE,
    PUBLISHER_OBSERVES_FOR,
    post_from_manual,
    post_from_request,
    reading_from_observations,
)
from control_plane.learning.sources import Reading, parse_fx_reading, parse_fx_target
from control_plane.learning.store import ObservationRow, PostRow, item_from_url
from control_plane.learning.voice import HUMOR_NEVER_PITCHES, VOICE_RULES

LONG_DASHES = re.compile(f"[{chr(0x2013)}{chr(0x2014)}]")
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def item(
    n: int,
    *,
    kind: str = "REPLY",
    horizon: int = 24,
    age: int | None = None,
    followers: int | None = None,
    views: int | None = None,
    likes: int | None = None,
    hook: str | None = None,
    link: bool = False,
    wenext: bool = False,
    position: int | None = None,
    hour: int = 12,
    platform: str = "X",
) -> Item:
    return Item(
        key=f"x:{n}",
        platform=platform,
        kind=kind,
        horizon=horizon,
        published_at=T0.replace(hour=hour) - timedelta(minutes=n),
        hook=hook,
        target_followers=followers,
        target_age_minutes=age,
        position_replies=position,
        mentions_wenext=wenext,
        has_link=link,
        views=views,
        likes=likes,
    )


# --------------------------------------------------------------------------------------------
# Horizons
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "horizon"),
    [(44, None), (45, 1), (60, 1), (180, 1), (181, None), (270, 6), (720, 6), (721, None),
     (1080, 24), (1440, 24), (2880, 24), (2881, None), (3240, 72), (4320, 72), (7200, 72),
     (7201, None)],
)  # fmt: skip
def test_a_reading_belongs_to_the_horizon_whose_window_holds_its_age(age, horizon) -> None:
    assert horizon_of(age) == horizon


def test_the_windows_do_not_overlap_so_a_reading_never_belongs_to_two_horizons() -> None:
    spans = [(w.accept_minutes, w.expires_minutes) for w in model.WINDOWS]
    for (_, first_end), (second_start, _) in pairwise(spans):
        assert first_end < second_start
    assert model.HORIZONS == (1, 6, 24, 72)


def test_a_horizon_is_due_from_its_age_until_it_expires_and_never_twice() -> None:
    published = T0
    assert due_horizons(published, T0 + timedelta(minutes=59), ()) == []
    assert due_horizons(published, T0 + timedelta(minutes=60), ()) == [1]
    assert due_horizons(published, T0 + timedelta(minutes=60), {1}) == []
    # A sweep that was down for a day finds the 24 h horizon due, and the 1 and 6 h ones gone:
    # a "1 h" reading made at 25 h would be a different quantity.
    assert due_horizons(published, T0 + timedelta(hours=25), ()) == [24]
    assert due_horizons(published, T0 + timedelta(hours=100), ()) == [72]
    assert due_horizons(published, T0 + timedelta(hours=121), ()) == []


def test_the_publisher_window_the_sweep_waits_for_is_the_publishers_own() -> None:
    from control_plane.pilot.publisher import OBSERVE_WINDOW

    assert PUBLISHER_OBSERVES_FOR == OBSERVE_WINDOW
    assert timedelta(hours=1) > HARVEST_GRACE


# --------------------------------------------------------------------------------------------
# Properties from the text
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "hook"),
    [
        ("does it work on windows?", "PYTANIE"),
        ("my launch post got 107 views in three days", "LICZBA"),
        ("$2,000 to $5,000 and nobody warned me", "LICZBA"),
        ("software is dead, says everyone reading a pricing page", "PROWOKACJA"),
        ("i'm bad at getting users, not so much at building", "BOL"),
        ("just shipped the new billing page", "NEWS"),
        ("you can run it in a container, that fixes it", "POMOC"),
        ("lol the printer wins again", "ZART"),
        ("finishing is the product. the one-shot is the demo", "DEMO"),
        ("this is the plainest possible sentence", "OPINIA"),
    ],
)
def test_the_hook_is_the_first_rule_that_matches(text: str, hook: str) -> None:
    assert classify_hook(text) == hook


def test_a_picture_is_a_demo_and_a_link_to_a_demo_page_is_not() -> None:
    assert classify_hook("look at this", has_media=True) == "DEMO"
    assert classify_hook("see https://example.com/demo for the long version") == "OPINIA"


def test_a_link_is_an_address_with_or_without_a_scheme_and_a_mention_is_the_name() -> None:
    assert model.has_link("read https://x.com/a/status/1") and model.has_link("try getwenext.com")
    assert not model.has_link("a plain sentence. with two parts.")
    assert model.mentions_wenext("I build Wenext") and not model.mentions_wenext("I build things")


# --------------------------------------------------------------------------------------------
# Buckets
# --------------------------------------------------------------------------------------------


def test_target_age_is_cut_at_30_and_120_minutes_and_only_replies_have_one() -> None:
    cuts = {0: "<30 min", 29: "<30 min", 30: "30-120 min", 120: "30-120 min", 121: ">2 h"}
    for minutes, bucket in cuts.items():
        assert model.bucket_target_age(item(1, age=minutes)) == bucket
    assert model.bucket_target_age(item(1, age=None)) is None
    assert model.bucket_target_age(item(1, kind="OWN_POST", age=5)) is None


def test_followers_position_and_hour_buckets() -> None:
    sizes = {999: "<1k", 1000: "1k-10k", 9999: "1k-10k", 10_000: "10k-100k", 100_000: "100k+"}
    for followers, bucket in sizes.items():
        assert model.bucket_followers(item(1, followers=followers)) == bucket
    assert model.bucket_followers(item(1, followers=None)) is None
    assert [model.bucket_position(item(1, position=p)) for p in (0, 5, 6, 20, 21)] == [
        "0-5", "0-5", "6-20", "6-20", ">20",
    ]  # fmt: skip
    assert [model.bucket_hour(item(0, hour=h)) for h in (0, 5, 6, 11, 12, 18, 23)] == [
        "00-06 UTC", "00-06 UTC", "06-12 UTC", "06-12 UTC", "12-18 UTC", "18-24 UTC", "18-24 UTC",
    ]  # fmt: skip


def test_an_unclassified_hook_is_not_a_group() -> None:
    assert model.bucket_hook(item(1, hook=None)) is None
    assert model.bucket_hook(item(1, hook="INNE")) is None
    assert model.bucket_hook(item(1, hook="OPINIA")) == "OPINIA"


# --------------------------------------------------------------------------------------------
# Statistics and the n-guard
# --------------------------------------------------------------------------------------------


def test_a_median_is_over_the_counters_that_were_read_and_an_unread_one_is_not_a_zero() -> None:
    rows = [item(1, views=10), item(2, views=30), item(3, views=None), item(4, views=20)]
    stats = model.stats_of("g", rows)
    assert (stats.n, stats.n_views, stats.median_views) == (4, 3, 20.0)
    assert (stats.n_likes, stats.median_likes) == (0, None)
    even = model.stats_of("g", [item(1, views=10), item(2, views=21)])
    assert even.median_views == 15.5


def test_the_guard_two_items_per_group_make_no_claim_however_big_the_gap() -> None:
    items = [item(i, age=10, views=1000) for i in range(2)] + [
        item(10 + i, age=200, views=5) for i in range(3)
    ]
    buckets = aggregate(items, model.DIMENSION_OF["target_age"])
    assert buckets["<30 min"].n == 2 and buckets[">2 h"].n == 3
    assert claim_for("X", model.DIMENSION_OF["target_age"], 24, buckets) is None
    assert find_claims(items, platform="X") == []


def test_three_per_group_and_a_clear_gap_make_the_claim_with_both_ns() -> None:
    young = [item(i, age=10, views=v) for i, v in enumerate((120, 140, 160, 130, 150, 145))]
    old = [item(20 + i, age=200, views=v) for i, v in enumerate((20, 25, 30, 25, 25))]
    claims = find_claims([*young, *old], platform="X")
    assert [claim.dimension for claim in claims] == ["target_age"]
    claim = claims[0]
    assert (claim.winner.bucket, claim.winner.n, claim.winner.median_views) == ("<30 min", 6, 142.5)
    assert (claim.loser.bucket, claim.loser.n, claim.loser.median_views) == (">2 h", 5, 25.0)
    assert claim.metric == "views" and claim.horizon == 24 and claim.key == "X:target_age"
    sentence = statement_of(claim)
    assert sentence == (
        "On X, at about 24 h after posting: replies under posts younger than 30 min got a median "
        "of 142.5 views (n=6) versus 25 views for replies under posts older than 2 h (n=5)."
    )
    assert not LONG_DASHES.search(sentence)
    payload = payload_of(claim, measured_at=T0)
    # The database requires exactly these: the key and both sides with their n (migration 0040).
    assert payload["lesson_key"] == "X:target_age"
    assert payload["winner"]["n"] == 6 and payload["loser"]["n"] == 5
    assert payload["thresholds"] == {"min_n": MIN_N, "min_ratio": 2.0, "min_abs": 20.0}
    assert payload["winner"]["range_views"] == [120, 160] and payload["loser"]["range_views"] == [
        20,
        30,
    ]
    assert len(payload["winner"]["evidence"]) == 6 and payload["loser"]["evidence"][0] == "x:20"


@pytest.mark.parametrize(
    ("young", "old"),
    [
        ((10, 12, 11), (5, 6, 5)),   # 2x in ratio but under 20 views apart
        ((18, 46, 8), (7, 7, 7)),    # the first real run: 18 against 7 was noise
        ((0, 0, 0), (3, 4, 3)),      # a median of 0 against a handful is not a finding
        ((100, 110, 120), (70, 80, 75)),  # 50 views apart, but not twice as many
    ],
)  # fmt: skip
def test_a_gap_must_be_big_in_ratio_and_in_absolute_terms(young, old) -> None:
    rows = [item(i, age=10, views=v) for i, v in enumerate(young)] + [
        item(10 + i, age=200, views=v) for i, v in enumerate(old)
    ]
    assert find_claims(rows, platform="X") == []


def test_likes_decide_only_where_views_cannot_and_a_disappointing_views_gap_is_final() -> None:
    # Reddit shows points, not views: the same rule on likes (points), with its own floor.
    rows = [item(i, platform="REDDIT", age=10, likes=v) for i, v in enumerate((5, 6, 7))] + [
        item(10 + i, platform="REDDIT", age=200, likes=v) for i, v in enumerate((1, 0, 1))
    ]
    (claim,) = find_claims(rows, platform="REDDIT")
    assert claim.metric == "likes" and "points" in statement_of(claim)
    # X: views are plentiful and say there is no gap; likes are NOT consulted afterwards.
    rows = [
        item(i, age=10, views=v, likes=lk) for i, (v, lk) in enumerate(((50, 9), (52, 9), (51, 9)))
    ] + [item(10 + i, age=200, views=v, likes=0) for i, v in enumerate((50, 51, 52))]
    assert find_claims(rows, platform="X") == []


def test_a_claim_is_made_at_one_horizon_the_most_mature_one_that_can_be_compared() -> None:
    young_24 = [item(i, age=10, views=100, horizon=24) for i in range(3)]
    old_24 = [item(10 + i, age=200, views=90, horizon=24) for i in range(3)]
    # At 1 h the same items show a huge gap, but 24 h is comparable, so 1 h is never consulted.
    young_1 = [item(i, age=10, views=50, horizon=1) for i in range(3)]
    old_1 = [item(10 + i, age=200, views=5, horizon=1) for i in range(3)]
    assert find_claims([*young_24, *old_24, *young_1, *old_1], platform="X") == []
    # With nothing comparable at 24 h the 1 h gap speaks, and says so.
    (claim,) = find_claims([*young_1, *old_1], platform="X")
    assert claim.horizon == 1 and "at about 1 h" in statement_of(claim)


def test_snapshots_of_different_horizons_are_never_mixed_in_one_group() -> None:
    mixed = [
        item(0, age=10, views=1000, horizon=72),
        item(1, age=10, views=1000, horizon=72),
        item(2, age=10, views=1000, horizon=24),
    ]
    # Three items in the bucket, but 2 at 72 h and 1 at 24 h: neither horizon has n >= 3.
    assert find_claims([*mixed, *[item(10 + i, age=200, views=5, horizon=24) for i in range(3)]],
                       platform="X") == []  # fmt: skip


def test_a_group_with_other_groups_between_the_best_and_the_worst_keeps_them_in_the_payload() -> (
    None
):
    rows = (
        [item(i, followers=500, views=300) for i in range(3)]
        + [item(10 + i, followers=5_000, views=100) for i in range(3)]
        + [item(20 + i, followers=50_000, views=20) for i in range(3)]
    )
    (claim,) = find_claims(rows, platform="X")
    assert (claim.winner.bucket, claim.loser.bucket) == ("<1k", "10k-100k")
    assert [other.bucket for other in claim.others] == ["1k-10k"]
    assert claim.ratio == 15.0


def test_own_post_versus_reply_link_and_mention_are_properties_too() -> None:
    own = [item(i, kind="OWN_POST", views=v) for i, v in enumerate((10, 12, 11))]
    replies = [item(10 + i, views=v) for i, v in enumerate((60, 70, 65))]
    (claim,) = [c for c in find_claims([*own, *replies], platform="X") if c.dimension == "kind"]
    assert "replies got a median of 65 views (n=3) versus 11 views for own posts (n=3)" in (
        statement_of(claim)
    )
    with_link = [item(i, link=True, wenext=True, views=v) for i, v in enumerate((5, 6, 4))]
    without = [item(10 + i, views=v) for i, v in enumerate((60, 70, 65))]
    dims = {c.dimension for c in find_claims([*with_link, *without], platform="X")}
    assert {"link", "mention"} <= dims


# --------------------------------------------------------------------------------------------
# What a request knew when we replied
# --------------------------------------------------------------------------------------------


def request_row(**changes):
    row = {
        "request_id": "11111111-1111-1111-1111-111111111111",
        "organization_id": "22222222-2222-2222-2222-222222222222",
        "workspace_id": "33333333-3333-3333-3333-333333333333",
        "project_id": "44444444-4444-4444-4444-444444444444",
        "kind": "REPLY",
        "target_external_id": "x:2109997331097833513",
        "target_url": "https://x.com/alexcooldev/status/2109997331097833513",
        "published_at": datetime(2026, 10, 13, 13, 53, tzinfo=UTC),
        "our_id": "x:2110006135294136605",
        "permalink": "https://x.com/demo_founder/status/2110006135294136605",
        "account": "demo_founder",
        "payload": {"text_and_media": "$2,000 to $5,000 and the honest line is scaling isnt it"},
        "opp_author": None,
        "opp_created": None,
        "opp_replies": None,
        "opp_metrics_at": None,
        "xt_replies": None,
        "xt_at": None,
        "xt_author": None,
        "th_author": None,
        "th_created": None,
    }
    return row | changes


def test_a_reply_request_becomes_a_learning_row_with_what_was_known_before_it() -> None:
    row = request_row(
        opp_replies=7,
        opp_metrics_at=datetime(2026, 10, 13, 13, 40, tzinfo=UTC),
        opp_author="alexcooldev",
    )
    post = post_from_request(row)
    assert post is not None
    assert (post.platform, post.kind, post.external_id) == ("X", "REPLY", "x:2110006135294136605")
    assert post.target_author == "alexcooldev" and post.hook == "LICZBA"
    assert post.hook_source == "CLASSIFIER" and not post.has_link and not post.mentions_wenext
    # The target's age comes from the snowflake in its id, no read: 2109997331097833513 is
    # 13.10.2026 13:20 UTC, and we answered at 13:53.
    assert post.target_created_at is not None and post.target_age_minutes is not None
    assert 30 <= post.target_age_minutes <= 40
    assert post.position_replies == 7 and post.facts["position_source"] == "opportunity"
    assert post.target_followers is None  # never read yet: unknown, not zero


def test_a_reply_count_read_after_our_reply_is_not_our_position() -> None:
    row = request_row(
        opp_replies=40,
        opp_metrics_at=datetime(2026, 10, 13, 15, 0, tzinfo=UTC),  # after 13:53
    )
    post = post_from_request(row)
    assert post is not None and post.position_replies is None
    # The newest reading BEFORE the reply wins, whichever source it came from.
    row = request_row(
        opp_replies=3,
        opp_metrics_at=datetime(2026, 10, 13, 13, 0, tzinfo=UTC),
        xt_replies=9,
        xt_at=datetime(2026, 10, 13, 13, 30, tzinfo=UTC),
    )
    post = post_from_request(row)
    assert post is not None and post.position_replies == 9
    assert post.facts["position_source"] == "x_trends"


def test_an_own_post_has_no_target_and_a_link_and_a_mention_are_read_from_the_text() -> None:
    row = request_row(
        kind="OWN_POST",
        target_external_id="x:@demo_founder",
        target_url="https://x.com/demo_founder",
        our_id="x:2109976668630913530",
        permalink="https://x.com/demo_founder/status/2109976668630913530",
        payload={"text_and_media": "Wenext finds the people. https://getwenext.com"},
    )
    post = post_from_request(row)
    assert post is not None and post.kind == "OWN_POST" and post.target_external_id is None
    assert post.has_link and post.mentions_wenext and post.target_age_minutes is None


def test_a_request_whose_permalink_is_not_a_platform_is_not_tracked() -> None:
    row = request_row(permalink="https://fixture-site.test/c/x/t/y?posted=1")
    assert post_from_request(row) is None
    # A Reddit permalink under an X target is a mismatch, not a reply.
    assert (
        post_from_request(request_row(permalink="https://www.reddit.com/r/a/comments/b/c/d/"))
        is None
    )


def test_a_reddit_reply_takes_the_thread_age_from_the_observed_thread() -> None:
    row = request_row(
        target_external_id="t3_1w9a5wx",
        target_url="https://www.reddit.com/r/SaaS/comments/1w9a5wx/title/",
        our_id="t1_abc123",
        permalink="https://www.reddit.com/r/SaaS/comments/1w9a5wx/title/abc123/",
        th_created=datetime(2026, 10, 13, 13, 33, tzinfo=UTC),
        th_author="someone",
    )
    post = post_from_request(row)
    assert post is not None and post.platform == "REDDIT" and post.external_id == "t1_abc123"
    assert post.target_age_minutes == 20 and post.target_author == "someone"


def test_a_hand_posted_reply_takes_its_real_time_from_its_own_id() -> None:
    row = {
        "manual_id": "55555555-5555-5555-5555-555555555555",
        "organization_id": "22222222-2222-2222-2222-222222222222",
        "workspace_id": "33333333-3333-3333-3333-333333333333",
        "project_id": "44444444-4444-4444-4444-444444444444",
        "platform": "X",
        "target_external_id": "x:2109997331097833513",
        "target_url": "https://x.com/alexcooldev/status/2109997331097833513",
        "url": "https://x.com/demo_founder/status/2110006135294136605",
        "text": "a reply the founder typed",
        "posted_at": datetime(2026, 10, 14, 9, 0, tzinfo=UTC),  # marked a day later
        "opp_author": None,
        "opp_created": None,
        "opp_replies": None,
        "opp_metrics_at": None,
    }
    post = post_from_manual(row)
    assert (
        post is not None and post.origin == "MANUAL" and post.external_id == "x:2110006135294136605"
    )
    assert post.published_at.date().isoformat() == "2026-10-13"
    assert post.facts["published_at_source"] == "snowflake"
    assert post_from_manual(row | {"url": "https://example.com/post"}) is None


def test_an_address_names_the_item_and_its_platform() -> None:
    assert item_from_url("https://x.com/a/status/12") == ("X", "x:12")
    assert item_from_url(
        "https://www.reddit.com/r/SideProject/comments/1x2gigk/comment/pfb0k9f/"
    ) == (
        "REDDIT",
        "t1_pfb0k9f",
    )
    assert item_from_url("https://www.reddit.com/r/SaaS/comments/1w9a5wx/") == (
        "REDDIT",
        "t3_1w9a5wx",
    )
    assert item_from_url("https://fixture-site.test/a") is None


# --------------------------------------------------------------------------------------------
# Snapshots out of the observations the publisher already took
# --------------------------------------------------------------------------------------------


def post_row(published_at=T0, platform="X") -> PostRow:
    return PostRow(
        id="66666666-6666-6666-6666-666666666666",
        organization_id="22222222-2222-2222-2222-222222222222",
        workspace_id="33333333-3333-3333-3333-333333333333",
        project_id=None,
        platform=platform,
        kind="OWN_POST",
        origin="PUBLICATION",
        request_id="11111111-1111-1111-1111-111111111111",
        manual_publication_id=None,
        external_id="x:1",
        permalink="https://x.com/a/status/1",
        account="a",
        body="",
        published_at=published_at,
        target_external_id=None,
        target_url=None,
        target_author=None,
        target_followers=None,
        target_created_at=None,
        target_age_minutes=None,
        position_replies=None,
        hook=None,
        hook_source=None,
        mentions_wenext=False,
        has_link=False,
        facts={},
    )


def observation(minutes: int, *, score=None, count=0, **metrics) -> ObservationRow:
    return ObservationRow(
        request_id="11111111-1111-1111-1111-111111111111",
        observed_at=T0 + timedelta(minutes=minutes),
        score=score,
        reply_count=count,
        raw={"metrics": metrics},
    )


def test_counters_are_merged_across_the_window_each_from_the_earliest_that_showed_it() -> None:
    """The capsule reads X's analytics and counters in different moments: one observation has
    the views, the next the likes. The earlier ones (outside the window) are not used at all."""
    observations = [
        observation(20, views=3),  # before the 1 h window: belongs to nothing
        observation(62, views=7),
        observation(83, likes=1, replies=0, reposts=0),
        observation(104, views=9),
    ]
    found = reading_from_observations(post_row(), observations, 1)
    assert found is not None
    reading, measured_at = found
    assert (reading.views, reading.likes, reading.replies, reading.reposts) == (7, 1, 0, 0)
    assert reading.source == "OBSERVATION"
    assert measured_at == T0 + timedelta(minutes=62)


def test_nothing_in_the_window_is_no_snapshot_and_a_late_observation_is_not_moved_earlier() -> None:
    assert reading_from_observations(post_row(), [observation(30, views=3)], 1) is None
    assert reading_from_observations(post_row(), [observation(300, views=30)], 1) is None
    found = reading_from_observations(post_row(), [observation(400, views=30)], 6)
    assert found is not None and found[0].views == 30


def test_a_reddit_observation_gives_points_and_replies_and_may_be_negative() -> None:
    row = post_row(platform="REDDIT")
    found = reading_from_observations(row, [observation(70, score=-2, count=0)], 1)
    assert found is not None
    reading, _ = found
    assert (reading.likes, reading.replies, reading.views) == (-2, 0, None)


# --------------------------------------------------------------------------------------------
# Readings
# --------------------------------------------------------------------------------------------


def test_the_capsules_missing_counters_are_taken_from_the_mirror_and_the_reading_says_so() -> None:
    capsule = Reading(views=11, source="CAPSULE")
    mirror = Reading(views=12, likes=2, replies=1, reposts=0, source="FXTWITTER")
    merged = capsule.filled_from(mirror)
    assert (merged.views, merged.likes, merged.replies, merged.reposts) == (11, 2, 1, 0)
    assert merged.source == "CAPSULE" and merged.raw["filled_by"] == "FXTWITTER"
    assert merged.raw["filled"] == ["likes", "replies", "reposts"]
    assert Reading(source="CAPSULE").filled_from(mirror).source == "FXTWITTER"
    assert not Reading().has_any()


FX = {
    "code": 200,
    "message": "OK",
    "tweet": {
        "id": "2109976668630913530",
        "text": "software is dead",
        "replies": 0,
        "retweets": 1,
        "likes": 2,
        "views": 14,
        "created_timestamp": 1791892613,
        "author": {"screen_name": "demo_founder", "followers": 4},
    },
}


def test_the_mirrors_answer_gives_four_counters_and_the_authors_followers() -> None:
    reading = parse_fx_reading(FX, "2109976668630913530")
    assert reading is not None
    assert (reading.views, reading.likes, reading.replies, reading.reposts) == (14, 2, 0, 1)
    target = parse_fx_target(FX, "2109976668630913530", now=T0)
    assert target is not None and target.followers == 4 and target.handle == "demo_founder"
    assert target.created_at == datetime.fromtimestamp(1791892613, tz=UTC)
    # Another post's answer, a failure and a counter of the wrong type are no readings.
    assert parse_fx_reading(FX, "1") is None and parse_fx_reading({"code": 404}, "1") is None
    broken = {"code": 200, "tweet": {"id": "1", "views": "14", "likes": True, "replies": -1}}
    assert parse_fx_reading(broken, "1") is None


# --------------------------------------------------------------------------------------------
# The diary
# --------------------------------------------------------------------------------------------

DIARY = """\
| # | Data | Kanał | Format | Hook | Treść (początek) | Link | 1 h | 24 h | 72 h | Zapisy |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 11.10 21:36 | X @demo_founder | tekst + link | BOL | "I'm good at building things." | https://x.com/demo_founder/status/2109367577172689034 |  | 4 lajki, 3 odp., ok. 60 wyśw. | 107 wyśw., 4 lajki, 1 repost (wcześniejszy odczyt). Pomiar 13.10 ok. 15:50 (ok. 42 h): 145 wyśw., 4 lajki, 3 odp., 1 repost | ? |
| 3 | 11.10 21:32 | Reddit r/SideProject | komentarz | POMOC | Volpi | t1_pfb0k9f | | USUNIĘTY przez moderację | | |
| 4 | 12.10 21:05 | X reply | odpowiedź | POMOC | do @simonbs (19,8k) | x:2109722713741029594 |  | ok. 18 h: 69 wyśw., 0 lajków, 0 odp. (pomiar 13.10 ok. 15:50) |  |  |
| 6 | 12.10 ok. 21:30 | X @demo_founder | film v3 | DEMO z liczbą | "Everyone has a coding agent now..." | https://x.com/demo_founder/status/2109728567655571651 | 18 wyśw., 2 lajki, 1 repost, 2 odp. | ok. 18 h: 47 wyśw., 2 lajki, 1 repost, 2 odp. (pomiar). Odpowiedź z linkiem pod nim: 22 wyśw. |  |  |
| 22 | 13.10 ok. 15:50 | X reply | odpowiedź (szkic) | ZART | do @tibo_maker (212k): "congrats." | szkic, x:2109981296369807417 |  |  |  |  |
| 31 | 13.10 ok. 15:50 | X @demo_founder | własny post (szkic) | NEWS | "my timeline says codex..." | szkic |  |  |  |  |
"""


def test_the_diary_rows_are_read_by_position_with_what_was_skipped_and_why() -> None:
    rows = {row.number: row for row in diary.parse_diary(DIARY)}
    assert sorted(rows) == [1, 3, 4, 6, 22, 31]
    one = rows[1]
    assert (one.kind, one.platform, one.hook, one.own_id) == (
        "OWN_POST",
        "X",
        "BOL",
        "x:2109367577172689034",
    )
    # The 24 h cell has its nominal age (approximate); the 72 h cell's EARLIER reading has no
    # stated age and is skipped, and the 42 h reading keeps the age the cell states.
    assert [(r.age_minutes, r.views, r.approximate) for r in one.readings] == [
        (1440, 60, True),
        (2520, 145, False),
    ]
    assert "earlier reading with no stated age" in one.skipped
    assert rows[3].readings == () and "removed by moderation: no numbers" in rows[3].skipped[0]
    assert (rows[4].followers, rows[4].target_handle, rows[4].own_id) == (
        19_800, "simonbs", "x:2109722713741029594",
    )  # fmt: skip
    assert rows[4].readings[0].likes == 0 and rows[4].readings[0].replies == 0  # zero is a reading
    six = rows[6]
    assert six.hook == "DEMO" and [r.age_minutes for r in six.readings] == [60, 1080]
    assert any("another post" in why for why in six.skipped)
    draft = rows[22]
    assert draft.is_draft and draft.target_id == "x:2109981296369807417" and draft.own_id is None
    assert draft.followers == 212_000 and draft.readings == ()
    assert rows[31].is_draft and rows[31].own_id is None and rows[31].kind == "OWN_POST"


def test_a_seed_reading_counts_for_a_horizon_only_within_its_tolerance() -> None:
    assert [diary.strict_horizon_of(m) for m in (60, 102, 1080, 1440, 2520, 4320)] == [
        1, None, 24, 24, None, 72,
    ]  # fmt: skip
    assert diary.text_prefix('"I\'m good at building things..."') == "I'm good at building things"


# --------------------------------------------------------------------------------------------
# The founder's voice, said once and held equal everywhere
# --------------------------------------------------------------------------------------------


def test_the_voice_rules_carry_every_point_the_founder_listed_and_none_of_the_dashes() -> None:
    text = VOICE_RULES
    assert not LONG_DASHES.search(text) and "!" not in text
    for part in (
        "5 to 20 words", "lowercase", "never explain the joke", "funniest true angle", "reframe",
        "understate", "shared pain", "absurd", "never at the person", "Technical correctness",
        "a plain, correct answer", "Ah yes, the classic", "Nothing says X like",
        "the real X was Y all along", "POV:", "bro really", "this is peak",
        "never a joke followed by a pivot to the product or to distribution",
        "only funny", "no joke", "10 seconds", "50 unrelated posts",
    ):  # fmt: skip
        assert part in text, part
    assert len(text.split()) <= 200, "a short section, not the whole guide"


def test_the_seeded_rule_says_it_is_a_rule_and_where_it_is_from() -> None:
    assert HUMOR_NEVER_PITCHES.key.startswith("rule:")
    assert "never" in HUMOR_NEVER_PITCHES.statement.lower() and HUMOR_NEVER_PITCHES.source
    assert not LONG_DASHES.search(HUMOR_NEVER_PITCHES.statement + HUMOR_NEVER_PITCHES.source)


def test_the_voice_is_the_same_text_in_the_plugin_the_hosted_endpoint_and_the_playbook() -> None:
    from control_plane.tools import catalog, mcp_server

    assert VOICE_RULES in mcp_server.HOST_INSTRUCTIONS
    assert VOICE_RULES in catalog.HOSTED_RULES and VOICE_RULES in catalog.HOSTED_INSTRUCTIONS
    assert not LONG_DASHES.search(mcp_server.HOST_INSTRUCTIONS)
    assert not LONG_DASHES.search(catalog.HOSTED_INSTRUCTIONS)
    assert "playbook.read" in mcp_server.HOST_INSTRUCTIONS
    assert "playbook.read" in catalog.HOSTED_INSTRUCTIONS
    # And before drafting: the hosted loop calls it BEFORE draft.create.
    assert catalog.HOSTED_INSTRUCTIONS.index("playbook.read") < catalog.HOSTED_INSTRUCTIONS.index(
        "draft.create"
    )
