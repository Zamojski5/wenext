"""The two small pure pieces behind publishing a reply under a thread nobody recorded (11.10.2026).

`_subreddit_of` says which community a post belongs to FROM WHAT REDDIT RETURNED, not from the
spelling of the address the agent wrote; `parse_thread_reference` names the thread in that address.
The database half (the reply goes out, every check still runs) is `test_publish_unrecorded_thread`.
"""

from __future__ import annotations

import pytest
from control_plane.observe.reddit_search import parse_thread_reference
from control_plane.pilot.publisher import _subreddit_of
from test_pilot_publisher import thread_json


def test_the_community_is_the_one_reddit_reports() -> None:
    payload = thread_json("abc123")
    payload[0]["data"]["children"][0]["data"]["subreddit"] = "Kubernetes"
    assert _subreddit_of(payload, fallback="kubernetes") == "Kubernetes"


@pytest.mark.parametrize("reported", ["", None, "a", "x" * 22, "no spaces", "r/kubernetes", "ż"])
def test_a_value_that_is_not_a_subreddit_name_falls_back_to_the_address(reported) -> None:
    payload = thread_json("abc123")
    payload[0]["data"]["children"][0]["data"]["subreddit"] = reported
    assert _subreddit_of(payload, fallback="kubernetes") == "kubernetes"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.reddit.com/r/micro_saas/comments/1abc2de/some_slug/?utm=1", "1abc2de"),
        ("https://old.reddit.com/r/SaaS/comments/1abc2de/some_slug/xyz9/", "1abc2de"),
        ("https://reddit.com/r/SaaS/comments/1abc2de", "1abc2de"),
    ],
)
def test_the_address_of_a_post_or_of_one_of_its_comments_names_the_post(
    url: str, expected: str
) -> None:
    assert parse_thread_reference(url)[1] == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/r/SaaS/comments/1abc2de/x/",
        "https://www.reddit.com/r/SaaS/",
        "https://redd.it/1abc2de",
        "https://www.reddit.com/r/SaaS/s/abcdef",
        "",
    ],
)
def test_an_address_that_names_no_thread_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        parse_thread_reference(url)
