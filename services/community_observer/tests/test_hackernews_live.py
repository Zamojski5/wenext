"""The adapter against the LIVE Algolia API. `-m live`, network required, no account.

Deselected by `tools/dev/run_all_tests.sh` (`-m "not live"`) and run by hand:

    services/community_observer/.venv/bin/python -m pytest tests -m live -q

A skip is not a pass. There is no `skipif(no network)`: without network these tests FAIL, and
that is the honest result — the recorded fixtures cover the parser, this file covers the
platform, and only a run says anything about the platform.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from community_observer import HackerNewsSource

pytestmark = pytest.mark.live


async def test_ask_hn_lists_real_threads_created_in_the_last_two_days() -> None:
    source = HackerNewsSource()
    try:
        since = datetime.now(UTC) - timedelta(days=2)
        threads = await source.list_recent_threads("ask", since=since, limit=5)
    finally:
        await source.aclose()
    assert 1 <= len(threads) <= 5
    for thread in threads:
        assert thread.created_at > since
        assert thread.url.startswith("https://news.ycombinator.com/item?id=")
        assert thread.observed_at.tzinfo is not None


async def test_a_real_thread_can_be_fetched_with_its_comments() -> None:
    source = HackerNewsSource()
    try:
        # A well-known, closed thread with a stable comment tree (recorded in tests/fixtures/).
        snapshot = await source.fetch_thread("ask", "4102013")
    finally:
        await source.aclose()
    assert snapshot.thread.external_id == "4102013"
    assert len(snapshot.comments) >= 40
    assert snapshot.thread.metrics.comment_count.require() == len(snapshot.comments)
