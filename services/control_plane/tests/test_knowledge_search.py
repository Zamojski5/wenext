"""`control_plane.knowledge.search` - three keyless backends, and never inventing an answer.

WHAT THESE TESTS PROTECT. A search that fails has exactly one honest outcome: "nothing answered,
here is why". The failure mode being ruled out is an empty `results` list that reads, to a model
or to a person, as "the web has nothing on this". Every case below is about keeping those two
apart, plus the plumbing that makes the backends usable without a key.

NO EXTERNAL NETWORK. The fetcher is a stub that returns canned bytes and records which URLs were
asked for. Nothing here resolves a name or opens a socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from control_plane.knowledge.fetch import Fetched, FetchRefused
from control_plane.knowledge.search import (
    DUCKDUCKGO,
    HN_ALGOLIA,
    WIKIPEDIA,
    WebSearch,
    _Throttle,
)

HN_JSON = json.dumps(
    {
        "hits": [
            {
                "objectID": "111",
                "title": "Queues without ack lose messages",
                "url": "https://example.test/queues",
                "points": 42,
                "num_comments": 17,
            },
            {"objectID": "222", "title": "Bez url", "points": 3, "num_comments": 1},
        ]
    }
).encode()

WIKI_JSON = json.dumps(
    {"query": {"search": [{"title": "Kolejka komunikatów", "snippet": "<span>wzorzec</span>"}]}}
).encode()

DDG_HTML = b"""<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fddg">Wynik
jeden</a>
<a class="result__snippet">Fragment opisu</a>
</body></html>"""


def ok(content: bytes, content_type: str = "application/json") -> Fetched:
    return Fetched(
        ok=True,
        url="https://stub.test/",
        status=200,
        content=content,
        content_type=content_type,
        title="",
        text=content.decode("utf-8", errors="replace"),
        reason=None,
        truncated=False,
    )


def broken(reason: str) -> Fetched:
    return Fetched(
        ok=False,
        url="https://stub.test/",
        status=None,
        content=b"",
        content_type="",
        title="",
        text="",
        reason=reason,
        truncated=False,
    )


class StubFetcher:
    """Answers by substring of the URL, and remembers what it was asked for."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(self, url: str) -> Fetched:
        self.asked.append(url)
        for needle, answer in self.answers.items():
            if needle in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return broken("stub nie zna tego adresu")


def searcher(answers: dict[str, object], **kwargs) -> WebSearch:
    return WebSearch(
        fetcher=StubFetcher(answers),
        throttle=_Throttle(interval_s=0.0),
        **kwargs,
    )


def test_every_hit_says_which_backend_produced_it():
    """Provenance is not a footnote. A hit with no backend cannot be judged by anybody later."""
    engine = searcher(
        {
            "hn.algolia": ok(HN_JSON),
            "wikipedia": ok(WIKI_JSON),
            "duckduckgo": ok(DDG_HTML, "text/html"),
        }
    )
    answer = asyncio.run(engine.search("kolejki"))
    backends = {hit.backend for hit in answer.results}
    assert backends == {HN_ALGOLIA, WIKIPEDIA, DUCKDUCKGO}
    assert set(answer.checked) == backends
    assert answer.ok and answer.reason() == ""


def test_a_hit_without_its_own_address_falls_back_to_the_discussion_it_came_from():
    engine = searcher({"hn.algolia": ok(HN_JSON)}, backends=(HN_ALGOLIA,))
    answer = asyncio.run(engine.search("kolejki"))
    assert answer.results[0].url == "https://example.test/queues"
    assert answer.results[1].url == "https://news.ycombinator.com/item?id=222"


def test_a_wikipedia_snippet_arrives_without_its_markup():
    engine = searcher({"wikipedia": ok(WIKI_JSON)}, backends=(WIKIPEDIA,))
    answer = asyncio.run(engine.search("kolejka"))
    assert answer.results[0].snippet == "wzorzec"
    assert answer.results[0].url.endswith("Kolejka_komunikat%C3%B3w")


def test_the_scraped_backend_unwraps_the_redirect_so_the_locator_is_the_real_page():
    """A stored locator pointing back at the search engine is not provenance."""
    engine = searcher({"duckduckgo": ok(DDG_HTML, "text/html")}, backends=(DUCKDUCKGO,))
    answer = asyncio.run(engine.search("cokolwiek"))
    assert answer.results[0].url == "https://example.test/ddg"
    assert answer.results[0].snippet == "Fragment opisu"


def test_when_no_backend_answers_the_result_is_not_an_empty_list_but_a_stated_failure():
    """THE case this module exists for. `ok` is false and every backend says what went wrong."""
    engine = searcher(
        {
            "hn.algolia": broken("timeout"),
            "wikipedia": ok(b"to nie jest json", "text/plain"),
            "duckduckgo": FetchRefused("adres odrzucony"),
        }
    )
    answer = asyncio.run(engine.search("kolejki"))
    assert answer.results == ()
    assert not answer.ok
    assert answer.checked == ()
    named = dict(answer.unavailable)
    assert set(named) == {HN_ALGOLIA, WIKIPEDIA, DUCKDUCKGO}
    assert "adres odrzucony" in named[DUCKDUCKGO]
    assert HN_ALGOLIA in answer.reason() and WIKIPEDIA in answer.reason()


def test_one_broken_backend_does_not_remove_the_others():
    engine = searcher({"hn.algolia": ok(HN_JSON), "wikipedia": broken("500")})
    answer = asyncio.run(engine.search("kolejki"))
    assert answer.ok
    assert [hit.backend for hit in answer.results] == [HN_ALGOLIA, HN_ALGOLIA]
    assert dict(answer.unavailable)[WIKIPEDIA] == "500"


def test_html_that_changed_shape_is_unavailable_rather_than_zero_results():
    """The scraped backend has no contract. When the markup moves, it says so."""
    engine = searcher(
        {"duckduckgo": ok(b"<html>przebudowa</html>", "text/html")}, backends=(DUCKDUCKGO,)
    )
    answer = asyncio.run(engine.search("cokolwiek"))
    assert not answer.ok
    assert "nie dała się odczytać" in dict(answer.unavailable)[DUCKDUCKGO]


def test_an_empty_query_is_refused_without_asking_anybody():
    fetcher = StubFetcher({})
    engine = WebSearch(fetcher=fetcher, throttle=_Throttle(interval_s=0.0))
    answer = asyncio.run(engine.search("   "))
    assert not answer.ok and fetcher.asked == []


def test_the_rate_limit_spaces_requests_to_one_backend_and_does_not_delay_the_others():
    """A courtesy limit on somebody else's free endpoint, not a quota. Per backend."""
    throttle = _Throttle(interval_s=2.0)
    assert throttle.wait_for(HN_ALGOLIA, now=100.0) == 0.0
    assert throttle.wait_for(WIKIPEDIA, now=100.0) == 0.0
    assert throttle.wait_for(HN_ALGOLIA, now=100.0) == pytest.approx(2.0)
    assert throttle.wait_for(HN_ALGOLIA, now=110.0) == 0.0


def test_the_query_reaches_the_backend_encoded_and_not_pasted_into_the_path():
    fetcher = StubFetcher({"hn.algolia": ok(HN_JSON)})
    engine = WebSearch(fetcher=fetcher, throttle=_Throttle(interval_s=0.0), backends=(HN_ALGOLIA,))
    asyncio.run(engine.search("kolejki & bazy"))
    assert "kolejki%20%26%20bazy" in fetcher.asked[0]
