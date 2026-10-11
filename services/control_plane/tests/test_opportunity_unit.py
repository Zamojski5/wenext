"""Okazje bez bazy: normalizacja, stan z danych, kształty, narzędzia z atrapą Reddita (D-055).

CO TE TESTY TRZYMAJĄ. Okazja jest wskazówką dla człowieka, więc każdy przypadek to jeden sposób,
w jaki mogłaby go wprowadzić w błąd:

  * adres w Pokoju prowadzący gdzie indziej niż platforma (link podstawiony przez autora posta);
  * identyfikator jednego posta z adresem drugiego;
  * stan "SZKIC" dla szkicu, który wygasł, albo "NOWA" dla pominiętej;
  * licznik, którego nie odczytano, pokazany jako zero;
  * wynik wyszukiwania, który nie mógł się odbyć, podany jako "nic nie znaleziono";
  * długa pauza w czymkolwiek, co czyta założyciel.

Testy z bazą są w `test_opportunities_db.py`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest
from control_plane.opportunities import room
from control_plane.opportunities import store as opportunity_store
from control_plane.opportunities.store import (
    OpportunityRefused,
    OpportunityView,
    new_opportunity,
    reddit_post_id,
    room_path,
    status_of,
    x_status_id,
)
from control_plane.tools import BY_NAME, PLUGIN_TOOLS, ToolRegistry, issue, manifest
from control_plane.tools import opportunity_tools as tools_module
from control_plane.tools.registry import ToolError

LONG_DASHES = (chr(0x2014), chr(0x2013))
NEW_TOOLS = (
    "reddit.search",
    "reddit.read",
    "opportunities.mark",
    "opportunities.list",
    "room.show",
)
POST_URL = "https://www.reddit.com/r/SaaS/comments/abc123/how_do_you_handle_x/"
WHEN = datetime(2026, 10, 10, 8, 15, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


def _view(**changes) -> OpportunityView:
    base = {
        "id": uuid4(),
        "platform": "REDDIT",
        "external_id": "t3_abc123",
        "url": POST_URL,
        "community": "r/SaaS",
        "title": "How do you handle X?",
        "excerpt": "We keep losing track of X.",
        "author": "someone",
        "created_at_source": WHEN,
        "score": 12,
        "num_comments": 5,
        "reason": "prosi dokładnie o to, co robi projekt",
        "angle": "pokaż, jak to rozwiązałeś u siebie",
        "marked_at": WHEN,
        "stan": "NOWA",
        "draft_revision_id": None,
    }
    return OpportunityView(**{**base, **changes})


# ---------------------------------------------------------------------------
# Normalization: what may become an opportunity
# ---------------------------------------------------------------------------


def _reddit(**changes):
    base = {
        "platform": "REDDIT",
        "url": POST_URL,
        "reason": "ktoś pyta o to, co robi projekt",
        "title": "How do you handle X?",
        "created_at": WHEN,
    }
    return new_opportunity(**{**base, **changes})


def test_a_reddit_post_is_keyed_by_the_target_of_a_reply() -> None:
    """The key is `t3_<id>`, the spelling of `publication_requests.target_external_id`."""
    one = _reddit()
    assert one.external_id == "t3_abc123"
    assert one.community == "r/SaaS", "z adresu, gdy nie podano"
    assert _reddit(external_id="T3_ABC123").external_id == "t3_abc123"
    assert _reddit(external_id="abc123", community="saas").community == "r/saas"
    assert _reddit(
        url="https://redd.it/abc123", external_id="", community="r/SaaS"
    ).external_id == ("t3_abc123")


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"external_id": "t3_zzz999"}, "wskazuje post abc123"),
        ({"external_id": "t1_abc123"}, "nie jest identyfikatorem posta Reddita"),
        ({"url": "https://evil.example/r/SaaS/comments/abc123/x/"}, "musi być https://www.reddit"),
        ({"url": "http://www.reddit.com/r/SaaS/comments/abc123/x/"}, "musi być https://www.reddit"),
        ({"url": "https://www.reddit.com.evil.example/r/SaaS/comments/abc123/"}, "musi być"),
        ({"url": "https://www.reddit.com/r/SaaS/"}, "podaj adres posta Reddita"),
        ({"created_at": None}, "created_at"),
        ({"title": "  "}, "title"),
        ({"reason": "   "}, "bez uzasadnienia"),
        ({"community": "r/a"}, "nie jest nazwą subreddita"),
        ({"created_at": datetime(2026, 10, 10, 8, 15)}, "strefę czasową"),
        ({"score": True}, "score"),
        ({"num_comments": -1}, "ujemne"),
    ],
)
def test_input_that_cannot_be_a_reddit_opportunity_is_refused_with_a_sentence(
    changes, fragment
) -> None:
    with pytest.raises(OpportunityRefused) as refused:
        _reddit(**changes)
    assert fragment in str(refused.value)
    assert not any(dash in str(refused.value) for dash in LONG_DASHES)


def test_an_x_post_is_keyed_by_its_status_id_and_has_no_title() -> None:
    url = "https://x.com/jack/status/2103587050346250341"
    one = new_opportunity(
        platform="x", url=url, reason="pyta o narzędzie", excerpt="Anyone using agents for this?"
    )
    assert one.external_id == "x:2103587050346250341"
    assert one.community == "X"
    assert one.title == ""
    assert x_status_id(external_id="x:2103587050346250341", url=url) == "2103587050346250341"
    with pytest.raises(OpportunityRefused, match="wskazuje post"):
        x_status_id(external_id="x:1", url=url)
    for bad in (
        "https://twitter.com/jack/status/20",
        "https://x.com/jack",
        "https://x.com.evil.example/a/status/1",
    ):
        with pytest.raises(OpportunityRefused):
            new_opportunity(platform="X", url=bad, reason="powód", excerpt="tekst")
    with pytest.raises(OpportunityRefused, match="excerpt"):
        new_opportunity(platform="X", url=url, reason="powód")


def _zone(hours: int) -> timezone:
    return timezone(timedelta(hours=hours))


def test_text_is_clipped_with_a_marker_and_the_time_is_utc() -> None:
    one = _reddit(
        title="t" * 900,
        excerpt="e" * 5000,
        reason="r" * 900,
        created_at=datetime(2026, 10, 10, 10, 15, tzinfo=_zone(2)),
    )
    assert len(one.title) <= opportunity_store.TITLE_CHARS and one.title.endswith("...")
    assert len(one.excerpt) <= opportunity_store.EXCERPT_CHARS
    assert len(one.reason) <= opportunity_store.REASON_CHARS
    assert one.created_at == datetime(2026, 10, 10, 8, 15, tzinfo=UTC)


def test_the_post_id_comes_from_the_address_or_the_id_and_they_must_agree() -> None:
    assert reddit_post_id(external_id="", url=POST_URL) == "abc123"
    assert reddit_post_id(external_id="t3_abc123", url="") == "abc123"
    assert reddit_post_id(external_id="abc123", url="https://redd.it/abc123") == "abc123"


# ---------------------------------------------------------------------------
# The state is one rule over what the database knows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dismissed", "answered", "live_draft", "expected"),
    [
        (False, False, False, "NOWA"),
        (False, False, True, "SZKIC"),
        (False, True, False, "ODPOWIEDZIANO"),
        (False, True, True, "ODPOWIEDZIANO"),
        (True, False, False, "POMINIETA"),
        (True, True, True, "POMINIETA"),
        (True, False, True, "POMINIETA"),
    ],
)
def test_the_state_of_an_opportunity(dismissed, answered, live_draft, expected) -> None:
    assert status_of(dismissed=dismissed, answered=answered, has_live_draft=live_draft) == expected


def test_the_four_states_are_exactly_the_ones_the_brief_names() -> None:
    assert opportunity_store.STATUSES == ("NOWA", "SZKIC", "ODPOWIEDZIANO", "POMINIETA")
    assert opportunity_store.OPEN_STATUSES == ("NOWA", "SZKIC")


# ---------------------------------------------------------------------------
# The shapes the page reads
# ---------------------------------------------------------------------------

OPPORTUNITY_KEYS = {
    "id", "platforma", "spolecznosc", "tytul", "fragment", "autor", "url", "utworzono", "wynik",
    "komentarze", "dlaczego", "kat", "stan", "szkic",
}  # fmt: skip
DRAFT_KEYS = {
    "revision_id", "okazja_id", "platforma", "konto", "spolecznosc", "rodzaj", "tytul", "tekst",
    "w_odpowiedzi_na", "stan", "wygasa", "odcisk",
}  # fmt: skip


def test_an_opportunity_has_exactly_the_keys_of_the_contract() -> None:
    shown = _view().to_json()
    assert set(shown) == OPPORTUNITY_KEYS
    assert shown["wynik"] == 12 and shown["komentarze"] == 5
    assert shown["utworzono"] == "2026-10-10T08:15:00+00:00"
    assert shown["dlaczego"].startswith("prosi") and shown["kat"].startswith("pokaż")
    assert shown["szkic"] is None


def test_an_unread_counter_is_null_and_an_x_post_shows_the_start_of_its_text() -> None:
    shown = _view(
        platform="X",
        external_id="x:1",
        community="X",
        title="",
        excerpt="Anyone using agents for this? We tried three tools and none stuck at all.",
        score=None,
        num_comments=None,
        created_at_source=None,
        author=None,
    ).to_json()
    assert shown["wynik"] is None and shown["komentarze"] is None
    assert shown["utworzono"] is None and shown["autor"] is None
    assert shown["tytul"].startswith("Anyone using agents")
    assert len(shown["tytul"]) <= 90


def test_a_zero_is_a_measurement_and_stays_zero() -> None:
    shown = _view(score=0, num_comments=0).to_json()
    assert shown["wynik"] == 0 and shown["komentarze"] == 0


def _revision_row(
    *, action: str, target: str, text: str, target_url: str, platform="REDDIT", handle="maker"
):
    return {
        "revision_id": uuid4(),
        "expires_at": WHEN + timedelta(hours=72),
        "platform": platform,
        "account_handle": handle,
        "payload_hash": "a" * 64,
        "payload": {
            "action_type": action,
            "target_external_id": target,
            "target_url": target_url,
            "text_and_media": text,
        },
    }


def test_a_reply_draft_names_the_conversation_it_answers() -> None:
    row = _revision_row(
        action="SUBMIT_COMMENT", target="t3_abc123", text="Moja odpowiedź.", target_url=POST_URL
    )
    one = _view()
    shown = room.draft_json(row, opportunity=one, thread=None)

    assert set(shown) == DRAFT_KEYS
    assert shown["rodzaj"] == "REPLY" and shown["platforma"] == "REDDIT"
    assert shown["okazja_id"] == str(one.id)
    assert shown["spolecznosc"] == "r/SaaS" and shown["tekst"] == "Moja odpowiedź."
    assert shown["w_odpowiedzi_na"] == {
        "url": POST_URL,
        "tytul": "How do you handle X?",
        "autor": "someone",
    }
    assert shown["stan"] == "czeka_na_zgode"
    assert shown["wygasa"] == (WHEN + timedelta(hours=72)).isoformat()


def test_a_draft_names_the_account_it_goes_out_from_and_never_guesses_one() -> None:
    row = _revision_row(
        action="SUBMIT_COMMENT",
        target="t3_abc123",
        text="Odpowiedź.",
        target_url=POST_URL,
        handle="Demo_Maintainer",
    )
    assert room.draft_json(row, opportunity=None, thread=None)["konto"] == "Demo_Maintainer"
    del row["account_handle"]
    assert room.draft_json(row, opportunity=None, thread=None)["konto"] is None


def test_a_reply_to_an_unrecorded_conversation_makes_no_title_up() -> None:
    row = _revision_row(
        action="SUBMIT_COMMENT", target="t3_zzz999", text="Odpowiedź.", target_url=POST_URL
    )
    shown = room.draft_json(row, opportunity=None, thread=None)
    assert shown["okazja_id"] is None
    assert shown["w_odpowiedzi_na"] == {"url": POST_URL, "tytul": "", "autor": None}
    assert shown["spolecznosc"] == ""


def test_an_observed_thread_supplies_what_an_opportunity_does_not() -> None:
    row = _revision_row(
        action="SUBMIT_COMMENT", target="t3_abc123", text="Odpowiedź.", target_url=POST_URL
    )
    thread = {"community_key": "r/devops", "title": "Zebrany wątek", "author": "ktoś"}
    shown = room.draft_json(row, opportunity=None, thread=thread)
    assert shown["spolecznosc"] == "r/devops"
    assert shown["w_odpowiedzi_na"]["tytul"] == "Zebrany wątek"
    assert shown["w_odpowiedzi_na"]["autor"] == "ktoś"


def test_an_own_post_splits_its_title_and_answers_nothing() -> None:
    row = _revision_row(
        action="SUBMIT_POST",
        target="r/SaaS",
        text="Tytuł posta\n\nTreść posta.",
        target_url="https://www.reddit.com/r/SaaS/submit",
    )
    shown = room.draft_json(row, opportunity=None, thread=None)
    assert set(shown) == DRAFT_KEYS
    assert shown["rodzaj"] == "OWN_POST"
    assert shown["tytul"] == "Tytuł posta" and shown["tekst"] == "Treść posta."
    assert shown["w_odpowiedzi_na"] is None
    assert shown["spolecznosc"] == "r/SaaS"


def test_an_x_own_post_is_not_split_into_a_title() -> None:
    row = _revision_row(
        action="SUBMIT_POST",
        target="x:@maker",
        text="Pierwszy akapit.\n\nDrugi akapit.",
        target_url="https://x.com/maker",
        platform="X",
    )
    shown = room.draft_json(row, opportunity=None, thread=None)
    assert shown["platforma"] == "X" and shown["spolecznosc"] == "X"
    assert shown["tytul"] == "" and shown["tekst"] == "Pierwszy akapit.\n\nDrugi akapit."


def _row(*, score, replies, visibility="UNVERIFIED") -> dict:
    return {"glosy": score, "odpowiedzi": replies, "widocznosc": visibility}


def test_totals_with_nothing_measured_are_null_and_the_count_is_a_count() -> None:
    empty = room.totals([])
    assert empty["opublikowane"] == 0 and empty["okno_dni"] == 7
    assert empty["odpowiedzi"] is None and empty["glosy"] is None and empty["usuniete"] is None

    unread = room.totals([_row(score=None, replies=None), _row(score=None, replies=None)])
    assert unread["opublikowane"] == 2
    assert unread["glosy"] is None and unread["odpowiedzi"] is None
    assert unread["usuniete"] is None, "UNVERIFIED nie jest sprawdzeniem: brak werdyktów to null"


def test_totals_sum_only_what_was_measured() -> None:
    mixed = room.totals(
        [
            _row(score=7, replies=2, visibility="CONFIRMED"),
            _row(score=None, replies=None),
            _row(score=0, replies=0, visibility="REMOVED"),
        ]
    )
    assert mixed["opublikowane"] == 3
    assert mixed["glosy"] == 7 and mixed["odpowiedzi"] == 2
    assert mixed["usuniete"] == 1

    clean = room.totals([_row(score=1, replies=0, visibility="CONFIRMED")])
    assert clean["usuniete"] == 0, "jest werdykt i nic nie usunięto: to prawdziwe zero"


def test_the_room_path_is_the_one_the_dashboard_serves() -> None:
    workspace, project = uuid4(), uuid4()
    assert room_path(workspace, project) == f"/w/{workspace}/p/{project}/room"


# ---------------------------------------------------------------------------
# The tools, with a fake Reddit and a fake store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeHit:
    post_id: str
    subreddit: str
    title: str
    excerpt: str
    author: str
    url: str
    created_at: datetime
    score: int
    num_comments: int
    locked: bool = False
    archived: bool = False


@dataclass(frozen=True)
class FakeComment:
    comment_id: str
    author: str | None
    excerpt: str
    score: int | None
    created_at: datetime
    depth: int
    is_op: bool


@dataclass(frozen=True)
class FakeThread:
    post_id: str
    subreddit: str
    title: str
    body: str
    author: str | None
    url: str
    created_at: datetime
    score: int
    num_comments: int
    locked: bool
    archived: bool
    comments: tuple
    comments_truncated: bool


class FakeUnavailable(Exception):
    def __init__(self, reason: str, *, kind: str = "unavailable") -> None:
        super().__init__(reason)
        self.kind = kind


class FakeStore:
    known: ClassVar[dict] = {}

    def __init__(self, pool) -> None:
        self.pool = pool

    async def by_external_ids(self, *, organization_id, project_id, external_ids):
        return {key: value for key, value in self.known.items() if key in external_ids}


def _registry(host="codex") -> ToolRegistry:
    capability = issue(
        session_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        dsn="postgresql://atrapa/nieuzywane",
        host=host,
    )
    return ToolRegistry(capability=capability, pool=None)  # type: ignore[arg-type]


@pytest.fixture
def fake_reddit(monkeypatch):
    """The real module with its two functions and its exception replaced by fakes."""
    from control_plane.observe import reddit_search

    calls: list[dict] = []
    hits = [
        FakeHit(
            post_id="abc123",
            subreddit="SaaS",
            title="How do you handle X?",
            excerpt="We keep losing track of X.",
            author="someone",
            url=POST_URL,
            created_at=datetime.now(UTC) - timedelta(hours=3),
            score=12,
            num_comments=5,
        ),
        FakeHit(
            post_id="old999",
            subreddit="SaaS",
            title="Old thread",
            excerpt="",
            author="other",
            url="https://www.reddit.com/r/SaaS/comments/old999/old/",
            created_at=datetime.now(UTC) - timedelta(days=200),
            score=1,
            num_comments=0,
            archived=True,
        ),
    ]

    async def search_reddit(**kwargs):
        calls.append(kwargs)
        if kwargs["query"] == "boom":
            raise ValueError("Zapytanie nie może być puste.")
        if kwargs["query"] == "down":
            raise FakeUnavailable("Czytnik kapsuły nie odpowiada.", kind="capsule_down")
        return [] if kwargs["query"] == "nothing" else hits

    async def read_reddit_thread(url_or_id, **kwargs):
        calls.append({"read": url_or_id, **kwargs})
        return FakeThread(
            post_id="abc123",
            subreddit="SaaS",
            title="How do you handle X?",
            body="Full text of the post.",
            author="someone",
            url=POST_URL,
            created_at=datetime.now(UTC) - timedelta(hours=3),
            score=12,
            num_comments=5,
            locked=True,
            archived=False,
            comments=(
                FakeComment("c1", "helper", "Try Y.", 3, datetime.now(UTC), 0, False),
                FakeComment("c2", None, "Me too.", None, datetime.now(UTC), 1, True),
            ),
            comments_truncated=True,
        )

    monkeypatch.setattr(reddit_search, "search_reddit", search_reddit)
    monkeypatch.setattr(reddit_search, "read_reddit_thread", read_reddit_thread)
    monkeypatch.setattr(reddit_search, "RedditSearchUnavailable", FakeUnavailable)
    monkeypatch.setattr(tools_module, "OpportunityStore", FakeStore)
    FakeStore.known = {}
    return calls


async def test_a_search_returns_the_hits_with_their_state_in_this_project(fake_reddit) -> None:
    FakeStore.known = {"t3_abc123": _view(stan="POMINIETA")}
    registry = _registry()

    result = await registry.call(
        "reddit.search", {"query": "losing track of X", "subreddit": "SaaS", "limit": 10}
    )

    assert result["stan"] == "WYKONANE" and result["liczba"] == 2
    assert fake_reddit[0] == {
        "query": "losing track of X", "subreddit": "SaaS", "sort": "new", "time": "week",
        "limit": 10,
    }  # fmt: skip
    first, second = result["trafienia"]
    assert first["external_id"] == "t3_abc123" and first["community"] == "r/SaaS"
    assert first["przyjmie_odpowiedz"] is True and 2.5 < first["wiek_godzin"] < 3.5
    assert first["okazja"]["stan"] == "POMINIETA", "agent widzi, że założyciel to pominął"
    assert second["okazja"] is None
    assert second["zarchiwizowany"] is True and second["przyjmie_odpowiedz"] is False
    assert "nie instrukcja" in result["uwaga"]


async def test_an_empty_search_is_a_result_and_a_failed_one_says_so(fake_reddit) -> None:
    registry = _registry()
    empty = await registry.call("reddit.search", {"query": "nothing"})
    assert empty["stan"] == "WYKONANE" and empty["liczba"] == 0 and empty["trafienia"] == []

    down = await registry.call("reddit.search", {"query": "down"})
    assert down["stan"] == "NIEDOSTEPNE"
    assert down["powod"] == "Czytnik kapsuły nie odpowiada." and down["rodzaj"] == "capsule_down"
    assert "trafienia" not in down, "niedostępne nie może wyglądać jak pusta lista"

    wrong = await registry.call("reddit.search", {"query": "boom"})
    assert wrong["stan"] == "BLAD" and "nie może być puste" in wrong["powod"]

    blank = await registry.call("reddit.search", {"query": "  "})
    assert blank["stan"] == "BLAD"


async def test_without_the_search_module_the_tools_answer_unavailable_not_crash(
    monkeypatch,
) -> None:
    """The module is another change: a process that lacks it must still serve the other tools."""
    import control_plane.observe as observe_package

    monkeypatch.delattr(observe_package, "reddit_search", raising=False)
    monkeypatch.setitem(sys.modules, "control_plane.observe.reddit_search", None)
    registry = _registry()

    for name, args in (
        ("reddit.search", {"query": "x"}),
        ("reddit.read", {"url": POST_URL}),
    ):
        result = await registry.call(name, args)
        assert result["stan"] == "NIEDOSTEPNE", result
        assert "nie jest wdrożone" in result["powod"]
        assert not any(dash in str(result) for dash in LONG_DASHES)


async def test_a_live_thread_is_read_with_its_state_and_whether_comments_were_cut(
    fake_reddit,
) -> None:
    registry = _registry()
    result = await registry.call(
        "reddit.read", {"url": POST_URL, "max_comments": 5, "max_depth": 1}
    )

    assert result["stan"] == "WYKONANE"
    assert fake_reddit[0] == {"read": POST_URL, "max_comments": 5, "max_depth": 1}
    thread = result["watek"]
    assert thread["external_id"] == "t3_abc123" and thread["tresc"] == "Full text of the post."
    assert thread["zablokowany"] is True and thread["przyjmie_odpowiedz"] is False
    assert [one["external_id"] for one in result["komentarze"]] == ["t1_c1", "t1_c2"]
    assert result["komentarze"][1]["punkty"] is None and result["komentarze"][1]["autor"] is None
    assert result["przyciete"] is True and result["komentarzy_zwrocono"] == 2


async def test_reading_a_thread_that_reddit_does_not_know_is_unavailable(
    fake_reddit, monkeypatch
) -> None:
    from control_plane.observe import reddit_search

    async def missing(url_or_id, **kwargs):
        raise FakeUnavailable("Reddit nie zna tego wątku.", kind="not_found")

    monkeypatch.setattr(reddit_search, "read_reddit_thread", missing)
    result = await _registry().call("reddit.read", {"url": POST_URL})
    assert result["stan"] == "NIEDOSTEPNE" and result["rodzaj"] == "not_found"


async def test_a_failed_read_after_the_draft_does_not_turn_the_answer_into_a_failure(
    monkeypatch,
) -> None:
    """The draft is already written when this runs: the answer must still say so."""

    class Broken(FakeStore):
        async def for_target(self, **kwargs):
            raise RuntimeError("baza mrugnęła")

    monkeypatch.setattr(tools_module, "OpportunityStore", Broken)
    registry = _registry()

    fields = await registry._draft_opportunity_fields(_view(), "uwaga o wątku")
    assert fields["okazja"]["stan"] == "SZKIC" and "nie z odczytu" in fields["okazja"]["uwaga"]
    assert fields["ostrzezenie"] == "uwaga o wątku" and fields["pokoj"].endswith("/room")

    skipped = await registry._draft_opportunity_fields(_view(stan="POMINIETA"), None)
    assert skipped["okazja"]["stan"] == "POMINIETA", "decyzja człowieka wygrywa ze szkicem"
    assert "ostrzezenie" not in skipped


async def test_marking_refuses_input_before_it_touches_the_database() -> None:
    """The registry here has no pool at all: a refusal must come first."""
    registry = _registry()
    for args, fragment in (
        ({"platform": "REDDIT", "url": POST_URL, "dlaczego": ""}, "bez uzasadnienia"),
        ({"platform": "HN", "url": POST_URL, "dlaczego": "x"}, "dozwolone są REDDIT i X"),
        (
            {"platform": "REDDIT", "url": POST_URL, "dlaczego": "x", "created_at": "wczoraj"},
            "ISO 8601",
        ),
        (
            {"platform": "REDDIT", "url": POST_URL, "dlaczego": "x", "pomin": "tak"},
            "wartością logiczną",
        ),
        (
            {"platform": "REDDIT", "url": "https://evil.example/x", "dlaczego": "x",
             "created_at": "2026-10-10T08:15:00Z", "title": "t"},
            "musi być https://www.reddit",
        ),
    ):  # fmt: skip
        with pytest.raises(ToolError) as refused:
            await registry._opportunities_mark(args)
        assert fragment in str(refused.value), args


# ---------------------------------------------------------------------------
# The catalog: where the new tools stand
# ---------------------------------------------------------------------------


def test_the_new_tools_are_in_the_catalog_and_on_the_plugin_allowlist() -> None:
    for name in NEW_TOOLS:
        assert name in BY_NAME and name in PLUGIN_TOOLS, name
    assert BY_NAME["opportunities.mark"].writes is True
    for name in ("reddit.search", "reddit.read", "opportunities.list", "room.show"):
        assert BY_NAME[name].writes is False, f"{name} tylko czyta"


def test_read_tools_are_annotated_read_only_and_the_writer_is_not() -> None:
    annotations = {entry["name"]: entry["annotations"] for entry in manifest(host="codex")}
    for name in ("reddit.search", "reddit.read", "opportunities.list", "room.show"):
        assert annotations[name] == {"readOnlyHint": True}, name
    assert annotations["opportunities.mark"] == {"readOnlyHint": False}


def test_a_catalog_listing_never_hides_the_new_tools_when_the_module_is_missing() -> None:
    """State is a call-time answer (NIEDOSTEPNE), not a hidden entry: a tool that vanishes looks
    like a tool the product does not have."""
    for name in NEW_TOOLS:
        assert BY_NAME[name].state == "DOSTEPNE"


def test_no_text_of_the_new_tools_carries_a_long_dash() -> None:
    for spec in tools_module.OPPORTUNITY_TOOL_SPECS:
        assert not any(dash in repr(spec) for dash in LONG_DASHES), spec["name"]
    for path in (
        "services/control_plane/control_plane/opportunities/store.py",
        "services/control_plane/control_plane/opportunities/ingest.py",
        "services/control_plane/control_plane/opportunities/room.py",
        "services/control_plane/control_plane/opportunities/__init__.py",
        "services/control_plane/control_plane/tools/opportunity_tools.py",
        "services/control_plane/control_plane/api/room.py",
        "db/migrations/0036_project_opportunities.sql",
    ):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert not any(dash in text for dash in LONG_DASHES), f"{path}: długa pauza"


def test_every_new_tool_has_a_polish_verb_for_the_conversation() -> None:
    from control_plane.sessions.trace_payloads import TOOL_VERBS

    for name in NEW_TOOLS:
        assert name in TOOL_VERBS, name
