"""The adapter against what the capsule's HEADFUL Chrome received from Reddit on 2026-09-04.

Recorded through the whole path (`api` → capsule reader → Chrome for Testing 153 in INTERACTIVE
mode → `https://www.reddit.com`) by `reddit_browser_probe`, with the founder's account logged in:

* `reddit_browser_me.json` — `/api/me.json`, 200, a `t2` account object (trimmed to the fields
  the parser touches; see `_nagrano` in the file),
* `reddit_browser_new_kubernetes.json` — `/r/kubernetes/new.json?raw_json=1&limit=25`, 200,
  25 posts,
* `reddit_browser_comments_1w6ln9w.json` — one thread with its comment tree, 200, 33 comments.

Two facts these files pin down: the browser-facing `.json` shape IS the Data API shape (the shared
parser needs no special case), and the account name sits under `data`, not at the top level — the
first probe read the top level and called a logged-in capsule anonymous.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest
from community_observer import PageRead, RedditBrowserSource
from tests.conftest import FIXTURES, FROZEN_NOW, frozen_clock

RECORDINGS = {
    "/api/me.json": "reddit_browser_me.json",
    "/r/kubernetes/new.json": "reddit_browser_new_kubernetes.json",
    "/r/kubernetes/comments/1w6ln9w.json": "reddit_browser_comments_1w6ln9w.json",
}


class _RecordedReader:
    """Answers each allowlisted path with the body the capsule's Chrome really received."""

    def __init__(self) -> None:
        self.pages = {
            path: json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            for path, name in RECORDINGS.items()
        }
        self.requests: list[str] = []

    async def read(self, url: str) -> PageRead:
        self.requests.append(url)
        page = self.pages[urlsplit(url).path]
        return PageRead(page["status"], page["final_url"], page["content_type"], page["body"])


@pytest.fixture
def reader() -> _RecordedReader:
    return _RecordedReader()


def test_the_recordings_say_how_they_were_made(reader: _RecordedReader) -> None:
    for page in reader.pages.values():
        assert page["status"] == 200
        assert page["content_type"].startswith("application/json")
        assert "INTERACTIVE" in page["_nagrano"] and "headful" in page["_nagrano"]
    assert "ZALOGOWANA" in reader.pages["/api/me.json"]["_nagrano"]


async def test_a_logged_in_capsule_is_recognised_from_the_t2_object(
    reader: _RecordedReader,
) -> None:
    source = RedditBrowserSource(reader, clock=frozen_clock)
    state = await source.session_state()
    assert state.logged_in is True
    assert state.username == "Demo_Maintainer"
    assert state.checked_at == FROZEN_NOW


async def test_the_listing_parses_all_twenty_five_recorded_posts(reader: _RecordedReader) -> None:
    source = RedditBrowserSource(reader, clock=frozen_clock)  # require_login=True: session is real
    threads = await source.list_recent_threads("r/kubernetes", since=None, limit=25)
    assert len(threads) == 25
    first = threads[0]
    assert first.external_id == "1w6ln9w"
    assert first.ref == "REDDIT:r/kubernetes:1w6ln9w"
    assert first.url.startswith("https://www.reddit.com/r/kubernetes/comments/1w6ln9w/")
    assert first.title.startswith("Kubernetes documentation assumes")
    assert first.metrics.score.require() == 42
    assert first.metrics.comment_count.require() == 33
    assert first.link_url is None, "post tekstowy"
    assert all(t.created_at.tzinfo is not None for t in threads)
    assert all(len(t.raw_sha256) == 64 for t in threads)
    created = [t.created_at for t in threads]
    assert created == sorted(created, reverse=True), "/new jest od najnowszego"
    assert [urlsplit(u).path for u in reader.requests] == [
        "/api/me.json",
        "/r/kubernetes/new.json",
    ]


async def test_the_recorded_thread_has_its_full_comment_tree(reader: _RecordedReader) -> None:
    source = RedditBrowserSource(reader, clock=frozen_clock)
    snapshot = await source.fetch_thread("r/kubernetes", "1w6ln9w")
    assert snapshot.thread.external_id == "1w6ln9w"
    assert len(snapshot.comments) == 33
    assert snapshot.thread.metrics.comment_count.require() == len(snapshot.comments)
    assert snapshot.comments_truncated is False
    assert snapshot.unique_participants.is_known
    assert max(c.depth for c in snapshot.comments) >= 1, "nagranie ma odpowiedzi na odpowiedzi"
    top_level = [c for c in snapshot.comments if c.parent_external_id is None]
    assert len(top_level) == 20 and all(c.depth == 0 for c in top_level)
    ids = {c.external_id for c in snapshot.comments}
    for comment in snapshot.comments:
        if comment.parent_external_id is not None:
            assert comment.parent_external_id in ids, "każdy rodzic jest w snapshocie"
