"""The adapter against what the capsule's Chrome REALLY received from Reddit on 2026-09-03.

The recording (`fixtures/reddit_browser_blocked_403.json`) was made through the whole path —
`api` container → capsule reader (`reader.py`) → Chrome for Testing 153, HEADLESS, anonymous →
`https://www.reddit.com` — by `reddit_browser_probe`. Reddit answered **403 with an HTML block
page** ("You've been blocked by network security") to both `/api/me.json` and
`/r/kubernetes/new.json`. No Listing was ever received, so there is no recorded listing fixture:
the listing tests in `test_reddit_browser.py` run on the Data API's documented shapes, and this
file pins the one shape the platform actually sent.

What this proves and what it does not (`docs/OUTCOMES.md` §8, plan B): the transport works end to
end and the adapter reports the refusal honestly; whether Reddit answers a logged-in or a headful
session is unmeasured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from community_observer import (
    CredentialsMissing,
    PageRead,
    RedditBrowserSource,
    SourceUnavailable,
)
from tests.conftest import FIXTURES, frozen_clock

RECORDING = FIXTURES / "reddit_browser_blocked_403.json"


@pytest.fixture
def blocked() -> dict:
    return json.loads(Path(RECORDING).read_text(encoding="utf-8"))


class _RecordedReader:
    """Answers every URL with the recorded block page — that is what Reddit did."""

    def __init__(self, recording: dict) -> None:
        self._page = PageRead(
            status=recording["status"],
            final_url=recording["final_url"],
            content_type=recording["content_type"],
            body=recording["body"],
        )
        self.requests: list[str] = []

    async def read(self, url: str) -> PageRead:
        self.requests.append(url)
        return PageRead(
            status=self._page.status,
            final_url=url,
            content_type=self._page.content_type,
            body=self._page.body,
        )


def test_the_recording_is_what_the_probe_saw(blocked: dict) -> None:
    assert blocked["status"] == 403
    assert blocked["content_type"] == "text/html"
    assert blocked["url"].startswith("https://www.reddit.com/api/me.json")
    assert "blocked by network security" in blocked["visible_text"]
    assert blocked["body_length_original"] > 100_000, "prawdziwa strona blokady, nie atrapa"
    assert "HeadlessChrome" in blocked["_nagrano"], "nagranie mówi, czym było czytane"


async def test_the_block_page_is_a_visible_transient_failure_not_an_empty_listing(
    blocked: dict,
) -> None:
    reader = _RecordedReader(blocked)
    source = RedditBrowserSource(reader, require_login=False, clock=frozen_clock)
    with pytest.raises(SourceUnavailable, match="403") as failure:
        await source.list_recent_threads("r/kubernetes", since=None, limit=25)
    assert "blokada" in str(failure.value), "komunikat nazywa możliwą blokadę anty-bot"
    with pytest.raises(SourceUnavailable, match="403"):
        await source.fetch_thread("r/kubernetes", "abc123")
    assert len(reader.requests) == 2


async def test_with_login_required_the_block_page_on_me_json_is_still_403_not_a_missing_login(
    blocked: dict,
) -> None:
    """A 403 block page is NOT `{}`: the adapter must not read it as "anonymous session"."""
    reader = _RecordedReader(blocked)
    source = RedditBrowserSource(reader, require_login=True, clock=frozen_clock)
    with pytest.raises(SourceUnavailable, match="403"):
        await source.list_recent_threads("r/kubernetes", since=None, limit=25)
    with pytest.raises(SourceUnavailable):
        await source.session_state()


async def test_a_block_page_never_becomes_credentials_missing(blocked: dict) -> None:
    reader = _RecordedReader(blocked)
    source = RedditBrowserSource(reader, require_login=True, clock=frozen_clock)
    try:
        await source.fetch_thread("r/kubernetes", "abc123")
    except CredentialsMissing:  # pragma: no cover — the wrong class would hide a platform block
        pytest.fail(
            "blokada anty-bot to nie brak poświadczeń — job ma paść widocznie, nie parkować"
        )
    except SourceUnavailable:
        pass
