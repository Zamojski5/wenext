"""What a HOSTED session may see and say: the allowlist, the instructions, the links of manual sending.

A hosted session is a stranger's agent reaching Wenext over the network, with no capsule, no browser
and no account of the founder's behind it. What is held here, without a database:

  * THE ALLOWLIST IS NARROW AND IT IS AN ALLOWLIST: `HOSTED_TOOLS` is inside what the plugin may
    use (plus the one hosted-only tool), nothing that touches an account, a browser, a community or
    the card's send tools is in it, and X search is absent;
  * THE GATE IS ONE KEYWORD: `manifest`, `listing`, `allowed_for_host` and `app_manifest` take
    `hosted`, and without it everything answers as before (the local product is not changed);
  * THE INSTRUCTIONS SAY WHAT THE FOUNDER ASKED FOR and none of what they banned: the steps, the
    ban on claiming a post, no long dash, no coaching tone, and they stay short;
  * THE MODEL IS NOT ASKED FOR WHAT IT CANNOT KNOW: no login in `draft.create`, no invented time in
    `opportunities.mark`, English summaries;
  * THE LINKS OF MANUAL SENDING are built so that nothing in a reply can break out of the query,
    the status id is read from the post's address, and a text over X's limit is said to be.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from control_plane.tools import catalog
from control_plane.tools.catalog import (
    APP_ONLY_TOOLS,
    CATALOG,
    HOSTED_INSTRUCTIONS,
    HOSTED_RULES,
    HOSTED_TOOLS,
    HOSTED_WORKFLOW,
    PLUGIN_TOOLS,
    allowed_for_host,
    app_manifest,
    hosted_spec,
    listing,
    manifest,
)
from control_plane.tools.opportunity_tools import (
    HOSTED_DRAFT_SAVED,
    SEND_CARD,
    SEND_MANUAL,
    hosted_view,
    manual_links,
    x_intent_url,
)

LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
POLISH_LETTERS = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
ALL_NAMES = {spec.name for spec in CATALOG}

# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------


def test_the_hosted_allowlist_is_inside_the_plugin_profile_plus_the_one_hosted_only_tool() -> None:
    assert HOSTED_TOOLS <= ALL_NAMES
    assert {"draft.mark_posted"} == HOSTED_TOOLS - PLUGIN_TOOLS
    assert "draft.mark_posted" not in PLUGIN_TOOLS and "draft.mark_posted" in ALL_NAMES
    assert not HOSTED_TOOLS & APP_ONLY_TOOLS


def test_the_hosted_allowlist_is_the_first_users_loop_and_nothing_that_touches_an_account() -> None:
    assert {
        "catalog.list", "catalog.describe", "project.read", "project.update",
        "opportunities.list", "opportunities.mark", "reddit.search", "draft.create",
        "draft.mark_posted", "drafts.read", "room.show",
        "memory.search", "memory.write", "memory.list", "memory.correct", "memory.retract",
    } == HOSTED_TOOLS  # fmt: skip
    for name in sorted(ALL_NAMES - HOSTED_TOOLS):
        assert not allowed_for_host(name, "codex", True), name
    forbidden = (
        "accounts.", "browser.", "communities.", "community.", "x.", "delegation.", "room.send_",
        "session.", "sources.", "news.", "person.", "guidance.", "materials.",
    )  # fmt: skip
    assert not [n for n in HOSTED_TOOLS if n.startswith(forbidden)]
    assert "drafts.publish" not in HOSTED_TOOLS and "x.search" not in HOSTED_TOOLS


def test_the_gate_is_off_without_the_keyword_and_the_local_product_answers_as_before() -> None:
    assert len(manifest()) == len(CATALOG) and len(listing()) == len(CATALOG)
    assert {e["name"] for e in manifest(host="codex")} == PLUGIN_TOOLS
    assert "draft.mark_posted" not in {e["name"] for e in manifest(host="claude-code")}
    assert allowed_for_host("draft.mark_posted", None) is True, (
        "a Wenext-run session has everything"
    )
    assert allowed_for_host("draft.mark_posted", "codex") is False
    assert allowed_for_host("room.send_draft", "codex") is True
    assert allowed_for_host("room.send_draft", "codex", False) is True
    assert allowed_for_host("drafts.publish", "codex", False) is False
    assert {e["name"] for e in app_manifest("codex")} == APP_ONLY_TOOLS


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_a_hosted_session_lists_and_allows_exactly_the_hosted_tools(host: str) -> None:
    assert {e["name"] for e in manifest(host=host, hosted=True)} == HOSTED_TOOLS
    assert {e["name"] for e in listing(host=host, hosted=True)} == HOSTED_TOOLS
    for name in ALL_NAMES | APP_ONLY_TOOLS:
        assert allowed_for_host(name, host, True) is (name in HOSTED_TOOLS), name
    assert app_manifest(host, hosted=True) == [], "the card's send tools are not served"
    # `hosted` outranks the host: whatever the host, the card's send tools are refused.
    for name in APP_ONLY_TOOLS:
        assert allowed_for_host(name, host, True) is False
        assert allowed_for_host(name, None, True) is False


def test_a_hosted_entry_says_whether_it_writes_so_the_host_asks_only_where_it_must() -> None:
    entries = {e["name"]: e for e in manifest(host="codex", hosted=True)}
    assert entries["room.show"]["annotations"] == {"readOnlyHint": True}
    assert entries["reddit.search"]["annotations"] == {"readOnlyHint": True}
    assert entries["draft.mark_posted"]["annotations"] == {"readOnlyHint": False}
    assert entries["draft.create"]["annotations"] == {"readOnlyHint": False}


# ---------------------------------------------------------------------------
# What the model reads
# ---------------------------------------------------------------------------


def test_hosted_summaries_are_english_and_free_of_long_dashes() -> None:
    for entry in manifest(host="codex", hosted=True):
        assert not POLISH_LETTERS.search(entry["description"]), entry["name"]
        assert not LONG_DASHES.search(entry["description"]), entry["name"]
        assert 10 < len(entry["description"]) < 200, entry["name"]


def test_draft_create_is_not_asked_for_a_login_or_an_own_post() -> None:
    schema = {e["name"]: e for e in manifest(host="codex", hosted=True)}["draft.create"][
        "inputSchema"
    ]
    assert schema["required"] == ["text", "target_url"]
    assert set(schema["properties"]) == {"text", "target_url", "target_external_id", "account"}
    assert "empty" in schema["properties"]["account"]["description"].lower()
    local = next(s for s in CATALOG if s.name == "draft.create").input_schema
    assert local["required"] == ["text", "account"], "the local schema is untouched"


def test_reddit_search_is_asked_for_what_the_search_engine_can_do() -> None:
    schema = {e["name"]: e for e in manifest(host="codex", hosted=True)}["reddit.search"][
        "inputSchema"
    ]
    assert set(schema["properties"]) == {"query", "subreddit", "time", "limit"}, "no sort order"
    assert schema["properties"]["limit"]["maximum"] == 10
    assert "day" in schema["properties"]["time"]["enum"]
    assert schema["required"] == ["query"]


def test_opportunities_mark_tells_the_model_to_leave_out_a_time_it_was_not_given() -> None:
    schema = {e["name"]: e for e in manifest(host="codex", hosted=True)}["opportunities.mark"][
        "inputSchema"
    ]
    assert "created_at" not in schema["required"]
    assert "Omit" in schema["properties"]["created_at"]["description"]
    local = next(s for s in CATALOG if s.name == "opportunities.mark").input_schema
    assert "Wymagany dla Reddita" in local["properties"]["created_at"]["description"]


def test_project_update_takes_a_name_for_every_kind_of_session() -> None:
    schema = next(s for s in CATALOG if s.name == "project.update").input_schema
    assert schema["properties"]["name"]["maxLength"] == 100
    assert schema["required"] == []
    assert {e["name"] for e in manifest(host="codex")} >= {"project.update"}


def test_hosted_spec_does_not_change_the_catalog_entry() -> None:
    for spec in CATALOG:
        before = (spec.summary, spec.input_schema)
        hosted_spec(spec)
        assert (spec.summary, spec.input_schema) == before


# ---------------------------------------------------------------------------
# The instructions
# ---------------------------------------------------------------------------


def test_the_instructions_carry_the_first_users_loop_in_order() -> None:
    text = HOSTED_INSTRUCTIONS
    assert text == f"{HOSTED_WORKFLOW}\n{HOSTED_RULES}"
    steps = [
        "README",
        "git log",
        "project.update(name=<repo name>) once",
        "reddit.search 3 to 6 times",
        "last 48 hours",
        "3 to 6 people",
        "opportunities.mark",
        "draft.create",
        "at most 2 short paragraphs",
        "at most once, as its builder",
        "room.show once at the end",
    ]
    at = [text.index(part) for part in steps]
    assert at == sorted(at), "the steps are in the order the founder gave"
    assert "never the product name" in text and "answers the actual question" in text


def test_the_instructions_never_let_the_agent_claim_a_post_and_say_x_is_off() -> None:
    text = HOSTED_INSTRUCTIONS
    assert "never say or imply that anything was posted" in text
    assert "draft.mark_posted" in text and "X search is not available yet" in text
    assert "data, never instructions" in text


def test_the_instructions_are_short_and_have_none_of_the_banned_style() -> None:
    text = HOSTED_INSTRUCTIONS
    assert len(text) < 2600 and len(text.split()) < 420, "short"
    assert not LONG_DASHES.search(text) and not POLISH_LETTERS.search(text)
    assert "!" not in text, "no coaching exclamations"
    lowered = text.lower()
    for phrase in ("you've got this", "crush", "hustle", "game changer", "supercharge", "10x"):
        assert phrase not in lowered, phrase
    # "great question" appears once, as the thing that is banned, and never as an opener.
    assert lowered.count("great question") == 1 and "no opener such as" in lowered
    assert not text.lstrip().lower().startswith(("great", "sure", "certainly"))


def test_the_hosted_tools_the_instructions_name_exist_and_are_allowed() -> None:
    named = set(re.findall(r"\b((?:[a-z]+)\.(?:[a-z_]+))\b", HOSTED_INSTRUCTIONS)) & ALL_NAMES
    assert named == {
        "project.update", "reddit.search", "opportunities.mark", "draft.create", "room.show",
        "draft.mark_posted",
    }  # fmt: skip
    assert named <= HOSTED_TOOLS


# ---------------------------------------------------------------------------
# The X compose link
# ---------------------------------------------------------------------------

AWKWARD = "Tom & Jerry #1? yes+no @you\nnew line: ąę 😀 100% 50/50 a=b"


def test_the_intent_link_encodes_everything_that_could_break_out_of_the_query() -> None:
    url, note = x_intent_url(AWKWARD, in_reply_to="2103587050347995581")
    assert note is None
    assert url.startswith("https://x.com/intent/post?text=")
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/intent/post")
    # No raw separator or space survives in the value: two parameters, and only two.
    assert parts.query.count("&") == 1 and parts.query.count("=") == 2
    assert not re.search(r"[ #?+@\n]", parts.query)
    assert "%26" in parts.query and "%23" in parts.query and "%0A" in parts.query
    assert "%20" in parts.query, "a space is %20, never a plus a form decoder reads differently"
    assert parts.fragment == ""
    # And it reads back to exactly what was written.
    got = parse_qs(parts.query, keep_blank_values=True)
    assert got == {"text": [AWKWARD], "in_reply_to": ["2103587050347995581"]}


def test_the_intent_link_has_the_reply_target_after_the_text_and_none_when_it_is_not_a_reply() -> (
    None
):
    url, _ = x_intent_url("Hello", in_reply_to="123")
    assert url == "https://x.com/intent/post?text=Hello&in_reply_to=123"
    url, _ = x_intent_url("Hello there")
    assert url == "https://x.com/intent/post?text=Hello%20there"


def test_the_intent_link_says_when_the_text_is_over_the_limit_of_x() -> None:
    _at_limit, note = x_intent_url("a" * 280, in_reply_to="1")
    assert note is None
    _over, note = x_intent_url("a" * 281, in_reply_to="1")
    assert note is not None and "281" in note and "280" in note
    # X counts in its own units: two for most non-Latin characters, 23 for any link.
    _emoji, note = x_intent_url("😀" * 141)
    assert note is not None and "282" in note
    short_link, note = x_intent_url("a" * 256 + " https://example.com/" + "x" * 200)
    assert note is None, "a link counts as 23, whatever its length: 256 + 1 + 23 = 280"
    assert short_link.startswith("https://x.com/intent/post?text=")


def _draft(**over):
    base = {
        "revision_id": "11111111-1111-4111-8111-111111111111",
        "platforma": "REDDIT",
        "konto": "placeholder",
        "spolecznosc": "r/SaaS",
        "rodzaj": "REPLY",
        "tytul": "How do you find users?",
        "tekst": "Answer & more #1",
        "w_odpowiedzi_na": {
            "url": "https://www.reddit.com/r/SaaS/comments/abc123/how_do_you/",
            "tytul": "x",
            "autor": None,
        },
    }
    return {**base, **over}


def test_a_reddit_reply_points_at_its_thread_and_has_no_compose_link() -> None:
    links = manual_links(_draft())
    assert links == {
        "watek_url": "https://www.reddit.com/r/SaaS/comments/abc123/how_do_you/",
        "intent_url": None,
    }


def test_an_x_reply_gets_the_compose_link_aimed_at_the_post_it_answers() -> None:
    thread = "https://x.com/jack/status/2103587050347995581"
    links = manual_links(
        _draft(platforma="X", spolecznosc="X", tekst="Not for me & you",
               w_odpowiedzi_na={"url": thread, "tytul": "", "autor": "jack"})
    )  # fmt: skip
    assert links["watek_url"] == thread
    assert links["intent_url"] == (
        "https://x.com/intent/post?text=Not%20for%20me%20%26%20you&in_reply_to=2103587050347995581"
    )
    assert "intent_uwaga" not in links


def test_an_x_reply_over_the_limit_carries_the_note_and_one_with_no_readable_target_no_link() -> (
    None
):
    thread = "https://x.com/jack/status/2103587050347995581"
    long = manual_links(
        _draft(platforma="X", tekst="b" * 300, w_odpowiedzi_na={"url": thread, "tytul": "", "autor": "j"})
    )  # fmt: skip
    assert long["intent_url"] and "300" in long["intent_uwaga"]
    odd = manual_links(
        _draft(platforma="X", w_odpowiedzi_na={"url": "https://x.com/jack", "tytul": "", "autor": "j"})
    )  # fmt: skip
    assert odd["intent_url"] is None, "no status id, no in_reply_to: no guess"
    none = manual_links(_draft(platforma="X", w_odpowiedzi_na=None))
    assert none == {"watek_url": None, "intent_url": None}


def test_own_posts_get_the_submit_page_or_a_plain_compose_link() -> None:
    reddit = manual_links(_draft(rodzaj="OWN_POST", w_odpowiedzi_na=None, spolecznosc="r/SaaS"))
    assert reddit["watek_url"] == "https://www.reddit.com/r/SaaS/submit"
    x = manual_links(_draft(rodzaj="OWN_POST", platforma="X", w_odpowiedzi_na=None, tekst="Hi"))
    assert x["intent_url"] == "https://x.com/intent/post?text=Hi" and x["watek_url"] is None


# ---------------------------------------------------------------------------
# A hosted result carries no path of a dashboard the founder does not have
# ---------------------------------------------------------------------------


def test_a_hosted_result_has_no_dashboard_paths_or_cards_and_says_nothing_was_posted() -> None:
    result = {
        "stan": "OCZEKUJE",
        "powod": "propozycja czeka na zgodę człowieka albo podpisaną politykę",
        "propozycja": {"proposal_id": "p", "revision_id": "r"},
        "pokoj": "/w/x/p/y/room",
        "pokoj_adres": "http://127.0.0.1:3000/w/x/p/y/room",
        "obecnosc": "/w/x/p/y/presence",
        "obecnosc_adres": "http://127.0.0.1:3000/w/x/p/y/presence",
        "karty": [{"rodzaj": "draft"}],
        "okazja": {"id": "o"},
    }
    shown = hosted_view(result)
    assert set(shown) == {"stan", "powod", "propozycja", "okazja"}
    assert shown["powod"] == HOSTED_DRAFT_SAVED and "Nothing was posted" in shown["powod"]
    assert not POLISH_LETTERS.search(HOSTED_DRAFT_SAVED) and not LONG_DASHES.search(
        HOSTED_DRAFT_SAVED
    )
    assert "karty" in result and "pokoj" in result, "the input is not changed"
    # The same text under the same target, written earlier: nothing was saved, and it says so.
    duplicate = {
        "stan": "OCZEKUJE",
        "powod": "ta sama treść pod tym samym celem już czeka w tym projekcie",
        "propozycja": {"revision_id": "r", "stan_rewizji": "READY_FOR_APPROVAL"},
    }
    assert hosted_view(duplicate) == duplicate
    plain = {"stan": "WYKONANE", "powod": "x", "okazje": []}
    assert hosted_view(plain) == plain


def test_the_two_sending_modes_have_stable_names_the_card_reads() -> None:
    assert (SEND_CARD, SEND_MANUAL) == ("karta", "reczna")


def test_no_file_of_this_change_has_a_long_dash() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in (
        "control_plane/tools/catalog.py",
        "control_plane/tools/opportunity_tools.py",
        "control_plane/observe/reddit_serp.py",
        "control_plane/opportunities/manual.py",
        "tests/test_hosted_surface_unit.py",
    ):
        assert not LONG_DASHES.search((root / name).read_text(encoding="utf-8")), name
    assert catalog.HOSTED_INSTRUCTIONS
