"""The card draws the post a reply answers the way the platform draws it, with our reply nested under it (v11, 13.10).

THE FOUNDER (13.10), twice: "I cannot tell whose post it is, what it says, or whether it is trending", and then "render it like real
X or Reddit". The server sends `cel` with every opportunity that has a reply under it (author, avatar, text, age, counters, Trending,
the verification mark and the first picture; `opportunities/store.py::target_json`), `konto_awatar` with every draft, and `cytat` with an
own post on X that ends with the address of a post. Held here, in jsdom with a fake host:

  * AN X POST: the author's photo in a column with a thread line down to our reply, name, verified mark, @handle and age, the text ONCE
    (no bold first line over a grey excerpt), the first picture, and the counters with X's icons (a counter nobody read is not drawn,
    a read zero is); a Reddit post: the alien, the community and age, its title and text, and a pill of points and one of comments;
  * OUR REPLY is the second row of the same block, with the founder's own picture;
  * PICTURES: an `img` has no source until the server gives one (`room.pictures`, asked once for the addresses the card shows); only the
    platforms' image hosts are ever asked for or drawn, the answer must be an inline image, a tool the host cannot call leaves the
    initials (or the address, where the host's policy lets it load), and a picture that fails to load is removed;
  * A POST WE ONLY QUOTE: our words without the address they end with, the quoted post in a bordered box (author, text, picture,
    counters), the label "Quote on X"; the editor still holds the whole text;
  * QUEUED AND POSTED are one quiet line under the post, with no reply above it;
  * THE PAGES are in the server's order (the best drafts first), and what the server left out is a note in the Skipped drawer;
  * THE POST'S TEXT is somebody else's and is drawn as text, never as markup; a server that sends no `cel` gets the old blocks.

NOT HELD HERE: how it looks (the PNGs `r3-*` of the task report).
"""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from control_plane.opportunities.avatars import PICTURE_HOSTS
from control_plane.tools import apps_ui
from tests.test_card_pager_unit import (
    JSDOM,
    OPP_A,
    OPP_B,
    REV_A,
    REV_B,
    REV_C,
    TOKEN_OK,
    _iso,
    fixture,
)
from tests.test_card_pager_unit import _run as _base_run

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nie jest zainstalowany")
NEEDS_JSDOM = pytest.mark.skipif(not JSDOM.is_dir(), reason="potrzebny jsdom (apps/dashboard)")

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")

AVATAR = "https://pbs.twimg.com/profile_images/1/a_normal.jpg"
OURS = "https://pbs.twimg.com/profile_images/2/founder_normal.jpg"
SHOT = "https://pbs.twimg.com/media/demo1.jpg"
INLINE = "data:image/png;base64,iVBORw0KGgo="


def _cel(**over: Any) -> dict[str, Any]:
    base = {
        "autor": "jack",
        "nazwa": "Jack Dorsey",
        "awatar": AVATAR,
        "tekst": "Anyone using agents for distribution? Tried three tools, none stuck.",
        "utworzono": _iso(hours=-2),
        "pomiar": _iso(minutes=-1),
        "metryki": {
            "wyswietlenia": 48_000,
            "polubienia": 312,
            "odpowiedzi": 12,
            "udostepnienia": 40,
        },
        "trending": True,
        "zniknal": False,
        "zweryfikowany": True,
        "media": {"typ": "photo", "url": SHOT, "szer": 1200, "wys": 675},
    }
    base.update(over)
    return base


def _data(**cel_over: Any) -> dict[str, Any]:
    """The pager fixture with `cel` on the X opportunity (page 2) and a Reddit one (page 1), and our picture on the X draft."""
    data = fixture()
    for one in data["okazje"]:
        if one["id"] == OPP_B:
            one["cel"] = _cel(**cel_over)
            one["autor"] = "jack"
        if one["id"] == OPP_A:
            one["cel"] = _cel(
                autor="demo_kestrel",
                nazwa=None,
                awatar=None,
                tekst=None,
                zweryfikowany=None,
                media=None,
                metryki={
                    "wyswietlenia": None,
                    "polubienia": 120,
                    "odpowiedzi": 40,
                    "udostepnienia": None,
                },
            )
    for draft in data["szkice"]:
        if draft["revision_id"] == REV_B:
            draft["konto_awatar"] = OURS
    return data


READ = """
const target = (page) => {
  const p = $$("[data-panel]")[page];
  const t = $(".tp", p);
  if (!t) return null;
  const av = $(".avc .av40", t), img = $("img.pic", av);
  return {
    name: $(".ph b", t) ? $(".ph b", t).textContent : null,
    handle: $(".ph .hd", t) ? $(".ph .hd", t).textContent : null,
    when: $(".ph .dt", t) ? $(".ph .dt", t).textContent : null,
    verified: !!$(".ph .vf", t),
    text: $(".pt", t) ? $(".pt", t).textContent : null,
    title: $(".rt", t) ? $(".rt", t).textContent : null,
    heads: $$("h2.title, p.excerpt", t).length,
    metrics: $$(".mx .mi", t).map((m) => m.textContent.trim()),
    icons: $$(".mx .mi svg.fi", t).length,
    pills: $$(".mx .rpill", t).map((m) => m.textContent.trim()),
    trending: $$(".tg-trend", t).map((n) => n.textContent.trim()),
    initials: av ? av.textContent.replace(/\\s+/g, " ").trim() : null,
    snoo: !!$(".av40.sn", t),
    img: img ? img.getAttribute("data-pic") : null,
    src: img ? img.getAttribute("src") : null,
    media: $(".xm .pic", t) ? $(".xm .pic", t).getAttribute("data-pic") : null,
    line: !!$(".tl", t),
    open: t.classList.contains("open"),
    gone: t.getAttribute("data-gone") === "1",
    html: t.innerHTML,
  };
};
const pictureCalls = () => calls().filter((c) => c.name === "room.pictures").map((c) => c.args.adresy);
"""


def _run(
    tmp_path: Path, scenario: str, data: dict[str, Any], tools: dict[str, Any] | None = None
) -> Any:
    return _base_run(tmp_path, READ + scenario, data=data, tools=tools)


def _pictures(answer: dict[str, str]) -> dict[str, Any]:
    return {"room.pictures": {"structured": {"stan": "WYKONANE", "obrazy": answer}}}


# ---------------------------------------------------------------------------
# An X post, as X draws it
# ---------------------------------------------------------------------------


@NEEDS_JSDOM
def test_an_x_post_is_drawn_like_x_with_our_reply_under_it_on_the_same_thread(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open(); await sleep(60);
        const p = $$("[data-panel]")[1];
        const reply = $(".pv", p), tp = $(".tp", p);
        return { t: target(1), above: !!(tp.compareDocumentPosition(reply) & 4), joined: reply.classList.contains("join"),
          ourAv: $(".pv .avc img.pic", p) ? $(".pv .avc img.pic", p).getAttribute("data-pic") : null,
          ourHead: $(".pv .pv-head", p).textContent.replace(/\\s+/g, " ").trim(), replying: $(".pv .re", p).textContent.trim(),
          ourLine: $(".pv .tl", p) !== null };
        """,
        _data(),
        _pictures({}),
    )
    t = got["t"]
    assert t["name"] == "Jack Dorsey" and t["handle"] == "@jack" and t["when"] == "2 h ago"
    assert t["verified"] is True and t["trending"] == ["Trending"]
    assert t["text"].startswith("Anyone using agents for distribution?")
    assert t["heads"] == 0, "the text is drawn once: no bold first line over a grey excerpt"
    assert t["media"] == SHOT and t["img"] == AVATAR
    assert t["metrics"] == ["12", "40", "312", "48K"] and t["icons"] == 4, (
        "X's icons: replies, reposts, likes, views"
    )
    assert t["line"] is True and t["open"] is True, "the thread line runs down to our reply"
    assert got["above"] is True and got["joined"] is True and got["ourLine"] is False
    assert got["ourAv"] == OURS, "our reply carries the picture of the account it leaves from"
    assert got["ourHead"].startswith("@demo_founder_x")
    assert got["replying"] == "Replying to @demo_kestrel", (
        "the post this reply answers, from the draft"
    )


@NEEDS_JSDOM
def test_a_reddit_post_keeps_its_title_and_text_and_gets_pills_of_points_and_comments(
    tmp_path: Path,
) -> None:
    got = _run(tmp_path, "await open(); return target(0);", _data())
    assert got["snoo"] is True and got["initials"] == "", "Reddit's alien, never a letter"
    assert got["title"].startswith("How do you find your first ten users?")
    assert got["text"].startswith("I built a small tool"), (
        "its text under its title, as Reddit has it"
    )
    assert got["pills"] == ["120", "40"] and got["metrics"] == [], (
        "points and comments are pills, not X's icons"
    )
    assert got["trending"] == ["Trending"] and got["line"] is True


@NEEDS_JSDOM
def test_a_counter_nobody_read_is_not_drawn_and_a_read_zero_is(tmp_path: Path) -> None:
    one = _run(
        tmp_path,
        "await open(); return target(1);",
        _data(
            trending=False,
            zweryfikowany=None,
            metryki={
                "wyswietlenia": None,
                "polubienia": 0,
                "odpowiedzi": None,
                "udostepnienia": None,
            },
        ),
    )
    assert one["metrics"] == ["0"] and one["trending"] == [] and one["verified"] is False
    none = _run(
        tmp_path,
        "await open(); return { t: target(1), mx: !!$$('[data-panel]')[1].querySelector('.mx') };",
        _data(trending=False, metryki={}, media=None),
    )
    assert none["mx"] is False and none["t"]["media"] is None, "nothing known, nothing drawn"


@NEEDS_JSDOM
def test_a_post_that_is_gone_says_so_and_draws_no_numbers_or_picture(tmp_path: Path) -> None:
    got = _run(tmp_path, "await open(); return target(1);", _data(zniknal=True, trending=False))
    assert got["gone"] is True and got["metrics"] == [] and got["media"] is None
    assert "This post is gone." in got["html"]


@NEEDS_JSDOM
def test_the_text_of_someone_elses_post_is_text_never_markup(tmp_path: Path) -> None:
    hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b> & "quotes"'
    got = _run(
        tmp_path,
        """
        await open();
        const p = $$("[data-panel]")[1];
        return { pwned: !!win.__pwned, kids: $$(".tp .pt *", p).length, text: $(".tp .pt", p).textContent,
          nameKids: $$(".tp .ph b > *", p).length, name: $(".tp .ph b", p).textContent };
        """,
        _data(tekst=hostile, nazwa="<script>alert(1)</script>Jack"),
    )
    assert got["pwned"] is False and got["kids"] == 0 and got["text"] == hostile
    assert got["nameKids"] == 0 and got["name"] == "<script>alert(1)</script>Jack"


@NEEDS_JSDOM
def test_a_server_that_sends_no_cel_gets_the_old_blocks(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const p = $$("[data-panel]")[1];
        return { tp: !!$(".tp", p), title: !!$(".title, .excerpt", p), reply: !!$(".pv", p), join: $(".pv", p).classList.contains("join") };
        """,
        fixture(),
    )
    assert got["tp"] is False and got["title"] is True and got["reply"] is True


# ---------------------------------------------------------------------------
# Pictures: asked for once, inline, and only from the platforms' hosts
# ---------------------------------------------------------------------------


@NEEDS_JSDOM
def test_pictures_have_no_source_until_the_server_gives_one_and_are_asked_for_once(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        await sleep(80);
        const after = $$("img.pic").filter((i) => i.getAttribute("src")).map((i) => [i.getAttribute("data-pic"), i.getAttribute("src").slice(0, 22)]);
        return { after, asked: pictureCalls() };
        """,
        _data(),
        _pictures({AVATAR: INLINE, SHOT: INLINE, OURS: INLINE}),
    )
    # The markup of a picture carries no source until one is known: the card never points an image at the internet itself.
    assert (
        """'<img class="pic" data-pic="' + esc(url) + '" alt="" referrerpolicy="no-referrer"' + (pics[url] ? ' src="'"""
        in apps_ui.OPPORTUNITIES_HTML
    )
    assert {pair[0] for pair in got["after"]} == {AVATAR, SHOT, OURS}
    assert all(pair[1] == "data:image/png;base64," for pair in got["after"])
    assert len(got["asked"]) == 1, "one call for the whole set"
    assert sorted(got["asked"][0]) == sorted({AVATAR, SHOT, OURS}), "each address once"


@NEEDS_JSDOM
def test_only_an_inline_image_is_ever_put_in_a_source(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open(); await sleep(80);
        return $$("img.pic").map((i) => [i.getAttribute("data-pic"), i.getAttribute("src")]);
        """,
        _data(),
        _pictures(
            {
                AVATAR: "https://evil.example/x.png",
                SHOT: "data:image/svg+xml;base64,PHN2Zz4=",
                OURS: "javascript:alert(1)",
            }
        ),
    )
    assert got and all(src is None for _pic, src in got), (
        "an answer that is not a small inline image is dropped"
    )


@NEEDS_JSDOM
def test_an_address_off_the_platforms_hosts_is_never_drawn_or_asked_for(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "await open(); await sleep(60); return { asked: pictureCalls(), t: target(1) };",
        _data(
            awatar="https://evil.example/a.jpg",
            media={"typ": "photo", "url": "http://pbs.twimg.com/media/x.jpg", "szer": 1, "wys": 1},
        ),
        _pictures({}),
    )
    assert got["t"]["img"] is None and got["t"]["media"] is None
    assert got["asked"] == [[OURS]], "only the one address that is on a platform host"
    assert got["t"]["initials"] == "JA"


@NEEDS_JSDOM
def test_a_host_that_cannot_ask_leaves_the_initials_and_a_picture_that_fails_is_removed(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open(); await sleep(80);
        const sel = "img.pic[data-pic='__A__']";
        const before = !!$(sel);
        $(sel).dispatchEvent(new win.Event("error"));
        return { before, after: !!$(sel), initials: $(".tp .avc .av40", $$("[data-panel]")[1]).textContent.trim() };
        """.replace("__A__", AVATAR),
        _data(),
        {},  # no `room.pictures`: the host answers "no tool"
    )
    assert got["before"] is True, (
        "the address is still there to try where the host's policy allows it"
    )
    assert got["after"] is False and got["initials"] == "JA", (
        "a picture that does not load leaves the initials"
    )


@NEEDS_JSDOM
def test_a_picture_that_arrived_stays_across_a_redraw(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open(); await sleep(80);
        next(); prev();
        return { asked: pictureCalls().length, src: !!$("img.pic[data-pic='__A__']").getAttribute("src") };
        """.replace("__A__", AVATAR),
        _data(),
        _pictures({AVATAR: INLINE, SHOT: INLINE, OURS: INLINE}),
    )
    assert got == {"asked": 1, "src": True}


def test_the_cards_picture_hosts_are_the_servers() -> None:
    match = re.search(r"var PICTURE_HOSTS = \[(.*?)\];", apps_ui.OPPORTUNITIES_HTML, re.S)
    assert match is not None
    assert tuple(re.findall(r'"([^"]+)"', match.group(1))) == PICTURE_HOSTS


# ---------------------------------------------------------------------------
# A post we quote
# ---------------------------------------------------------------------------

QUOTED = "https://x.com/indie_maker/status/2103587050346250341"
WORDS = "This is the take most people miss: distribution beats features."


def _quote(**over: Any) -> dict[str, Any]:
    base = {
        "url": QUOTED,
        "autor": "indie_maker",
        "nazwa": "Indie Maker",
        "awatar": AVATAR,
        "tekst": "Unpopular opinion: nobody cares about your launch.",
        "utworzono": _iso(hours=-3),
        "pomiar": _iso(minutes=-1),
        "trending": False,
        "zniknal": False,
        "zweryfikowany": True,
        "metryki": {"wyswietlenia": 9200, "polubienia": 188, "odpowiedzi": 9, "udostepnienia": 21},
        "media": {"typ": "photo", "url": SHOT, "szer": 1200, "wys": 675},
    }
    base.update(over)
    return base


def _quote_data(quote: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pager fixture whose own post (page 3) is a quote on X."""
    data = fixture()
    for draft in data["szkice"]:
        if draft["revision_id"] == REV_C:
            draft.update(
                platforma="X",
                konto="demo_founder_x",
                spolecznosc="X",
                tytul="",
                tekst=f"{WORDS} {QUOTED}",
                konto_awatar=OURS,
                cytat=quote or _quote(),
            )
    return data


@NEEDS_JSDOM
def test_a_quote_draft_shows_our_words_without_the_address_and_the_post_in_a_box(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        next(); next();
        const p = $$("[data-panel]")[2];
        const q = $(".qbx", p);
        return { label: $(".where", p).textContent, text: $(".pv .bd > .ptw .pt", p).textContent, whole: $(".pv", p).textContent,
          quote: q ? { name: $(".ph b", q).textContent, handle: $(".ph .hd", q).textContent, verified: !!$(".ph .vf", q), text: $(".pt", q).textContent,
            media: $(".xm .pic", q) ? $(".xm .pic", q).getAttribute("data-pic") : null, metrics: $$(".mx .mi", q).map((m) => m.textContent.trim()),
            avatar: $(".ph .av40 .pic", q) ? $(".ph .av40 .pic", q).getAttribute("data-pic") : null } : null,
          card: $(".pv-label", p) ? $(".pv-label", p).textContent : null, joined: $(".pv", p).classList.contains("join") };
        """,
        _quote_data(),
        _pictures({}),
    )
    assert got["label"] == "Quote on X"
    assert got["text"] == WORDS and "x.com" not in got["whole"], "the address is not printed"
    assert got["quote"] == {
        "name": "Indie Maker",
        "handle": "@indie_maker",
        "verified": True,
        "text": "Unpopular opinion: nobody cares about your launch.",
        "media": SHOT,
        "metrics": ["9", "21", "188", "9.2K"],
        "avatar": AVATAR,
    }
    assert got["card"] == "Your quote, as it will appear" and got["joined"] is False


@NEEDS_JSDOM
def test_the_editor_of_a_quote_holds_the_whole_text_including_the_address(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        next(); next();
        $('[data-edit="2"]').click();
        await sleep(20);
        return $("textarea.edit").value;
        """,
        _quote_data(),
    )
    assert got == f"{WORDS} {QUOTED}", "saving must not drop the address that makes it a quote"


@NEEDS_JSDOM
def test_a_quote_whose_post_was_never_read_still_shows_a_box(tmp_path: Path) -> None:
    bare = _quote(
        nazwa=None,
        awatar=None,
        tekst=None,
        utworzono=None,
        pomiar=None,
        zweryfikowany=None,
        metryki={
            "wyswietlenia": None,
            "polubienia": None,
            "odpowiedzi": None,
            "udostepnienia": None,
        },
        media=None,
    )
    got = _run(
        tmp_path,
        'await open(); next(); next(); const q = $$("[data-panel]")[2].querySelector(".qbx"); return q ? q.textContent.replace(/\\s+/g, " ").trim() : null;',
        _quote_data(bare),
    )
    assert got is not None and "@indie_maker" in got and "Post on X" in got


@NEEDS_JSDOM
def test_an_own_post_without_a_quote_is_unchanged(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        'await open(); next(); next(); const p = $$("[data-panel]")[2]; return { box: !!$(".qbx", p), label: $(".where", p).textContent };',
        fixture(),
    )
    assert got["box"] is False and got["label"] == "Your post on Reddit"


# ---------------------------------------------------------------------------
# Queued and posted: one quiet line
# ---------------------------------------------------------------------------


@NEEDS_JSDOM
def test_queued_and_posted_are_one_quiet_line_with_no_reply_above_it(tmp_path: Path) -> None:
    queued = {
        "structured": {"stan": "OCZEKUJE", "powod": "x", "wniosek": "req-1", "podpisano": True}
    }
    status = {
        "structured": {
            "stan": "WYKONANE",
            "wniosek": {
                "id": "req-1",
                "status": "PUBLISHED",
                "widocznosc": "CONFIRMED",
                "opublikowano": _iso(minutes=-1),
                "adres": "https://x.com/a/status/9",
                "powod": None,
            },
        }
    }
    got = _run(
        tmp_path,
        """
        await open();
        next();
        const page = () => $$("[data-panel]")[1];
        const line = () => { const s = $(".send.cl", page()); return s ? s.textContent.replace(/\\s+/g, " ").trim() : null; };
        $('[data-act="send"]', page()).click(); await sleep(40);
        $('[data-act="go"]', page()).click(); await sleep(20);
        const q = { line: line(), reply: !!$(".pv", page()), joined: !$(".send.cl.solo", page()), phase: $(".send", page()).getAttribute("data-p") };
        await sleep(400);
        const p = { line: line(), reply: !!$(".pv", page()), phase: $(".send", page()).getAttribute("data-p"),
          buttons: $$(".send button", page()).map((b) => b.textContent.trim()) };
        return { q, p };
        """,
        _data(),
        {"room.send_token": TOKEN_OK, "room.send_draft": queued, "room.send_status": status},
    )
    assert got["q"]["phase"] == "queued" and got["q"]["reply"] is False
    assert got["q"]["joined"] is True and got["q"]["line"].startswith("Queued.")
    assert got["p"]["phase"] == "published" and got["p"]["reply"] is False
    assert got["p"]["line"].startswith("Posted.") and got["p"]["buttons"] == [
        "View reply ↗",
        "Done",
    ]


# ---------------------------------------------------------------------------
# The pages: the server's order, and what it left out
# ---------------------------------------------------------------------------


@NEEDS_JSDOM
def test_the_pages_follow_the_servers_order_of_drafts_and_the_pager_counts_what_it_sent(
    tmp_path: Path,
) -> None:
    data = _data()
    # The server ranked the X draft above the Reddit one, although its opportunity is listed after the Reddit one.
    data["szkice"] = sorted(
        (d for d in data["szkice"] if d["revision_id"] in (REV_A, REV_B)),
        key=lambda d: d["revision_id"] != REV_B,
    )
    data["okazje"] = [o for o in data["okazje"] if o["id"] in (OPP_A, OPP_B)]
    got = _run(
        tmp_path,
        'await open(); return { first: $$("[data-panel]")[0].getAttribute("data-kind") + ":" + $$("[data-panel]")[0].textContent.includes("Indie"), '
        'xFirst: $$("[data-panel]")[0].querySelector(".tp").getAttribute("data-p"), count: $(".count").textContent };',
        data,
    )
    assert got["xFirst"] == "X", "the best draft is page 1, whatever the order of the conversations"
    assert got["count"] == "1 of 2", "the pager says what is there: no 1 of 47"


@NEEDS_JSDOM
def test_what_the_server_left_out_is_a_note_in_the_skipped_drawer(tmp_path: Path) -> None:
    data = _data()
    data["ukryte"] = 12
    got = _run(
        tmp_path,
        """
        await open();
        const label = $$(".ql > *").map((e) => e.textContent.trim()).filter(Boolean).join(" ");
        $('[data-tog="skipped"]').click();
        return { label, note: $(".rnote") ? $(".rnote").textContent : null, open: $(".drawer").getAttribute("data-open") };
        """,
        data,
    )
    assert "12 more" in got["label"] and got["open"] == "skipped"
    assert (
        got["note"]
        == "12 more are waiting behind these. They come up as you send or skip one of these."
    )
    nothing = _run(
        tmp_path,
        'await open(); return { note: !!$(".rnote"), ql: $(".ql") ? $(".ql").textContent : "" };',
        _data(),
    )
    assert nothing["note"] is False and "more" not in nothing["ql"]


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


@NEEDS_JSDOM
def test_the_numbers_are_told_in_the_cards_language(tmp_path: Path) -> None:
    polish = copy.deepcopy(_data())
    polish["jezyk"] = "pl"
    got = _run(
        tmp_path,
        'await open(); return { t: target(1), title: $$("[data-panel]")[1].querySelector(".tp .mx .mi").getAttribute("title") };',
        polish,
    )
    assert got["t"]["trending"] == ["Na topie"]
    assert got["title"] == "12 odpowiedzi"
    english = _run(tmp_path, "await open(); return target(1).trending;", _data())
    assert english == ["Trending"]


def _new_keys(strings: dict[str, Any]) -> set[str]:
    named = {"pv.labelQuote", "pv.quotePost", "dc.quoteOn", "sc.sessionsLabel", "sc.nowLabel"}
    return {k for k in strings if k.startswith(("tg.", "tp.", "ql.", "hid.", "mt."))} | named


def test_the_new_words_are_in_both_languages_with_the_same_placeholders_and_no_long_dash() -> None:
    keys = _new_keys(PL)
    assert keys == _new_keys(EN) and len(keys) >= 20
    placeholder = re.compile(r"\{(\w+)\}")
    for key in keys:
        forms_pl = PL[key] if isinstance(PL[key], list) else [PL[key]]
        forms_en = EN[key] if isinstance(EN[key], list) else [EN[key]]
        for form in forms_pl + forms_en:
            assert not LONG_DASHES.search(form), key
        assert {n for f in forms_pl for n in placeholder.findall(f)} == {
            n for f in forms_en for n in placeholder.findall(f)
        }, key
