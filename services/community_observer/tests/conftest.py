"""Fixtures: recorded payloads and a frozen clock.

The JSON files in `fixtures/` were RECORDED from the live Algolia API on 2026-09-03 with plain
`curl` — not hand-written — so the parser is tested against what the platform actually sends.
`hn_item_with_comments.json` is story 4102013 (44 comments, four levels deep); the two search
files are the newest Ask HN and Show HN hits at the time of recording.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

#: A fixed `observed_at`. Tests compare against it rather than against `now()`.
FROZEN_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)


def frozen_clock() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def hn_search_ask() -> dict:
    return json.loads((FIXTURES / "hn_search_ask.json").read_text(encoding="utf-8"))


@pytest.fixture
def hn_search_show() -> dict:
    return json.loads((FIXTURES / "hn_search_show.json").read_text(encoding="utf-8"))


@pytest.fixture
def hn_item_with_comments() -> dict:
    return json.loads((FIXTURES / "hn_item_with_comments.json").read_text(encoding="utf-8"))
