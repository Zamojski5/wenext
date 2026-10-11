"""The card shows ONE person at a time, and every state of a send is drawn inside that person's panel (v7).

THE FOUNDER'S VERDICT ON THE FIRST RECORDING (11.10): "the card is too tall and runs away, you can't see
all of it, we must show it whole". v6 laid every person out as a column of their own; v7 has a pager.
What is held here, from the outside in:

  * THE PAGER: every entry has a page, all of them are in the document, and exactly one is active; the
    others are hidden, `inert` and `aria-hidden`, so a person (or a screen reader, or the Tab key) only
    ever meets the one on screen; "next", "previous", the dots and the arrow keys reach every draft, and
    stop at the ends instead of wrapping;
  * SEND ACTS ON THE VISIBLE DRAFT ONLY: pressing "Wyślij" asks the server about the revision on screen
    and no other, and a button of a page that is not active does nothing even when something clicks it;
  * THE STATES LIVE IN THE PANEL: the consent, waiting, queued, posted and refused boxes are drawn inside
    the active page, one at a time, in the place of the buttons, and never anywhere else in the card;
  * THE HEIGHT is told to the host as the card's own (`ui/notifications/size-changed` and
    `window.openai.notifyIntrinsicHeight`), and not as the height of a document that is never shorter
    than the frame the host gave it;
  * the new words are in both languages with the same placeholders.

The card is run in jsdom, in node, with a fake host that answers its `tools/call` messages (the page's
`parent` is replaced, or the card would answer itself) and a clock a hundred times faster. jsdom has no
layout, so HOW the card looks is not held here: it is rendered in a headless Chromium by hand (the PNGs
the task report names), and the height budget (about 560 px at 720 to 820 px) is measured there.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from control_plane.tools import apps_ui

CONTROL_PLANE = Path(__file__).resolve().parents[1]
JSDOM = CONTROL_PLANE.parents[1] / "apps" / "dashboard" / "node_modules" / "jsdom"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not JSDOM.is_dir(),
    reason="potrzebny node i jsdom (apps/dashboard/node_modules)",
)

# ---------------------------------------------------------------------------
# Fictional data: two drafts of people, an own post, a new person, a reply that went out
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)
DIGEST = "a" * 64
REV_A, REV_B, REV_C = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)
OPP_A, OPP_B, OPP_C, OPP_D = (f"0000000{n}-0000-4000-8000-00000000000{n}" for n in (1, 2, 3, 4))
ROOM = "http://127.0.0.1:3000/w/00000000-0000-4000-8000-000000000001/p/00000000-0000-4000-8000-000000000002/room"
LONG_REPLY = (
    "First users rarely come from one place. What worked for me was answering threads where someone "
    "already had the problem.\n\nSpecifically: I look for threads from the last three days, read the "
    "comments so I do not repeat what was said, and answer only where I have something to add. If nobody "
    "asks about the tool, I do not mention it at all."
)


def _iso(**delta: float) -> str:
    return (NOW + timedelta(**delta)).isoformat()


def _opportunity(oid: str, **over: Any) -> dict[str, Any]:
    base = {
        "id": oid,
        "platforma": "REDDIT",
        "spolecznosc": "r/SideProject",
        "tytul": f"How do you find your first ten users? ({oid[-1]})",
        "fragment": "I built a small tool for tracking invoices and nobody knows it exists.",
        "autor": "demo_kestrel",
        "url": f"https://www.reddit.com/r/SideProject/comments/zz{oid[-1]}/first_users/",
        "utworzono": _iso(hours=-3),
        "wynik": 12,
        "komentarze": 5,
        "dlaczego": "Asks exactly what your project does.",
        "kat": "Tell how you found your own first users.",
        "stan": "NOWA",
        "szkic": None,
    }
    base.update(over)
    return base


def _draft(rev: str, opp: str | None, **over: Any) -> dict[str, Any]:
    base = {
        "revision_id": rev,
        "okazja_id": opp,
        "platforma": "REDDIT",
        "konto": "demo_founder",
        "spolecznosc": "r/SideProject",
        "rodzaj": "REPLY",
        "tytul": "",
        "tekst": LONG_REPLY,
        "w_odpowiedzi_na": {
            "url": "https://www.reddit.com/r/SideProject/comments/zz1/x/",
            "tytul": "x",
            "autor": "demo_kestrel",
        },
        "stan": "czeka_na_zgode",
        "wygasa": _iso(hours=4),
        "odcisk": DIGEST,
    }
    base.update(over)
    return base


def fixture() -> dict[str, Any]:
    """Pages, in the card's order: draft A, draft B (X), the own post, the new person, the one that went out."""
    return {
        "stan": "WYKONANE",
        "okazje": [
            _opportunity(OPP_A, stan="SZKIC", szkic=REV_A),
            _opportunity(
                OPP_B,
                platforma="X",
                spolecznosc="X",
                autor="demo_builder_x",
                stan="SZKIC",
                szkic=REV_B,
                url="https://x.com/demo_builder_x/status/1000000000000000001",
            ),
            _opportunity(OPP_C),
            _opportunity(OPP_D, stan="ODPOWIEDZIANO", utworzono=_iso(days=-3)),
        ],
        "szkice": [
            _draft(REV_A, OPP_A),
            _draft(
                REV_B,
                OPP_B,
                platforma="X",
                konto="demo_founder_x",
                tekst="Tools alone did not stick for me either.",
            ),
            _draft(
                REV_C,
                None,
                rodzaj="OWN_POST",
                tytul="I shipped a tiny tool",
                tekst="The first ten users came from replies.",
                w_odpowiedzi_na=None,
            ),
        ],
        "wyniki": {
            "okno_dni": 7,
            "opublikowane": 1,
            "odpowiedzi": 3,
            "glosy": 41,
            "usuniete": 0,
            "pozycje": [
                {
                    "url": "https://www.reddit.com/r/SideProject/comments/zz4/x/yy1/",
                    "okazja_id": OPP_D,
                    "platforma": "REDDIT",
                    "tekst": "Start with one thread a day.",
                    "widocznosc": "CONFIRMED",
                    "glosy": 41,
                    "odpowiedzi": 3,
                    "opublikowano": _iso(days=-2),
                }
            ],
        },
        "pokoj": "/w/x/p/y/room",
        "pokoj_adres": ROOM,
    }


PLAN_SIGN = {
    "rodzaj": "PODPISZ",
    "tryb": "REVIEW",
    "wygasa": _iso(days=7),
    "limity": {"godzina": 1, "doba": 6},
    "konta": [
        {"platforma": "REDDIT", "konto": "demo_founder"},
        {"platforma": "X", "konto": "demo_founder_x"},
    ],
}
TOKEN = "tok-" + "x" * 20
TOKEN_OK = {
    "structured": {"stan": "WYKONANE", "zakres": PLAN_SIGN},
    "meta": {"wenext/potwierdzenie": TOKEN},
}
QUEUED = {"structured": {"stan": "OCZEKUJE", "powod": "x", "wniosek": "req-1", "podpisano": True}}
STATUS_QUEUED = {
    "structured": {
        "stan": "WYKONANE",
        "wniosek": {
            "id": "req-1",
            "status": "QUEUED",
            "widocznosc": None,
            "opublikowano": None,
            "adres": None,
            "powod": None,
        },
    }
}
STATUS_PUBLISHED = {
    "structured": {
        "stan": "WYKONANE",
        "wniosek": {
            "id": "req-1",
            "status": "PUBLISHED",
            "widocznosc": "CONFIRMED",
            "opublikowano": _iso(minutes=-1),
            "adres": "https://www.reddit.com/r/SideProject/comments/zz1/x/yy9/",
            "powod": None,
        },
    }
}


def _refusal(code: str) -> dict[str, Any]:
    return {
        "structured": {
            "stan": "NIEDOSTEPNE",
            "kod": code,
            "powod": f"powod {code}",
            "obecnosc_adres": "http://127.0.0.1:3000/w/x/p/y/presence",
        }
    }


# ---------------------------------------------------------------------------
# The jsdom runner
# ---------------------------------------------------------------------------

LIB = r"""
const fs = require("fs");
const { JSDOM } = require(process.env.JSDOM_PATH);
const html = fs.readFileSync(process.env.CARD_PATH, "utf8");
const FIX = JSON.parse(process.env.FIXTURE);
let TOOLS = JSON.parse(process.env.TOOLS || "{}");
const counts = {};
const log = [];            // everything the card posts to its host
let win = null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function toWin(data) { win.dispatchEvent(new win.MessageEvent("message", { data })); }
function reply(id, result) { setTimeout(() => toWin({ jsonrpc: "2.0", id, result }), 1); }
function fail(id) { setTimeout(() => toWin({ jsonrpc: "2.0", id, error: { code: -32601, message: "no tool" } }), 1); }
const host = {
  postMessage(m) {
    log.push(m);
    if (m.method === "ui/initialize") {
      return reply(m.id, { protocolVersion: "2026-01-26", hostCapabilities: { serverTools: {} }, hostContext: { locale: "en-US" } });
    }
    if (m.method === "tools/call") {
      let t = TOOLS[m.params.name];
      if (Array.isArray(t)) { const k = counts[m.params.name] || 0; counts[m.params.name] = k + 1; t = t[Math.min(k, t.length - 1)]; }
      if (!t) return fail(m.id);
      const result = { content: [{ type: "text", text: JSON.stringify(t.structured) }], structuredContent: t.structured };
      if (t.meta) result._meta = t.meta;
      return reply(m.id, result);
    }
    if (m.id != null) reply(m.id, {});
  },
};
const calls = () => log.filter((m) => m.method === "tools/call").map((m) => ({ name: m.params.name, args: m.params.arguments }));
function setTools(t) { TOOLS = t; for (const k of Object.keys(counts)) delete counts[k]; log.length = 0; }
// The card, in jsdom: `parent` is the fake host (else the card would answer its own messages) and the
// card's clock runs a hundred times faster (its waits are seconds).
async function open(hook) {
  const dom = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "https://card.test/",
    beforeParse(window) {
      win = window;
      Object.defineProperty(window, "parent", { value: host, configurable: true });
      const real = window.setTimeout.bind(window);
      window.setTimeout = (fn, ms) => real(fn, Math.max(1, Math.round((ms || 0) / 100)));
      if (hook) hook(window);
    },
  });
  await sleep(30);
  toWin({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: FIX } });
  await sleep(10);
  return dom;
}
const doc = () => win.document;
const $ = (s, root) => (root || doc()).querySelector(s);
const $$ = (s, root) => Array.from((root || doc()).querySelectorAll(s));
const active = () => $$("[data-panel][data-active]");
const idx = () => active().map((p) => p.getAttribute("data-i"));
const next = () => $('[data-nav="1"]').click();
const prev = () => $('[data-nav="-1"]').click();
function key(k, init) {
  doc().dispatchEvent(new win.KeyboardEvent("keydown", Object.assign({ key: k, bubbles: true, cancelable: true }, init || {})));
}
const box = () => $$(".send").map((s) => ({
  phase: s.getAttribute("data-p"),
  page: s.closest("[data-panel]") ? s.closest("[data-panel]").getAttribute("data-i") : null,
  inStage: !!s.closest(".stage"),
  text: s.textContent.replace(/\s+/g, " ").trim(),
  buttons: $$("button", s).map((b) => b.getAttribute("data-act")),
}));
const sendCalls = () => calls().filter((c) => c.name.indexOf("room.send") === 0);
async function main(body) {
  try { process.stdout.write("\n@@" + JSON.stringify(await body()) + "\n"); } catch (e) { process.stdout.write("\n@@" + JSON.stringify({ crashed: String(e && e.stack || e) }) + "\n"); }
  process.exit(0);
}
"""


def _run(
    tmp_path: Path,
    scenario: str,
    *,
    tools: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    """Run `scenario` (the body of an async function returning JSON) against the card in jsdom."""
    card = tmp_path / "card.html"
    card.write_text(apps_ui.OPPORTUNITIES_HTML, encoding="utf-8")
    program = tmp_path / "scenario.js"
    program.write_text(LIB + "\nmain(async () => {\n" + scenario + "\n});\n", encoding="utf-8")
    env = {
        "PATH": __import__("os").environ["PATH"],
        "JSDOM_PATH": str(JSDOM),
        "CARD_PATH": str(card),
        "FIXTURE": json.dumps(data if data is not None else fixture()),
        "TOOLS": json.dumps(tools or {}),
    }
    done = subprocess.run(
        ["node", str(program)], capture_output=True, text=True, timeout=60, check=False, env=env
    )
    assert done.returncode == 0, done.stderr
    line = [part for part in done.stdout.split("\n") if part.startswith("@@")]
    assert line, done.stdout + done.stderr
    got = json.loads(line[-1][2:])
    assert "crashed" not in got, got["crashed"]
    return got


# ---------------------------------------------------------------------------
# The pager: one panel at a time, every draft reachable
# ---------------------------------------------------------------------------


def test_the_pager_shows_exactly_one_panel_at_a_time(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        const pages = $$("[data-panel]");
        out.total = pages.length;
        out.active = idx();
        out.hiddenOnes = pages.filter((p) => !p.hasAttribute("data-active"))
          .map((p) => [p.getAttribute("aria-hidden"), p.hasAttribute("inert")]);
        out.activeAttrs = [active()[0].getAttribute("aria-hidden"), active()[0].hasAttribute("inert")];
        out.draftRegionsInActive = $$("[data-panel][data-active] [data-box]").length;
        out.draftRegionsInHidden = $$("[data-panel]:not([data-active]) [data-box]").filter((r) => !r.closest("[inert]")).length;
        out.count = $(".count").textContent;
        out.prevDisabled = $('[data-nav="-1"]').disabled;
        out.trail = [];
        for (let k = 0; k < pages.length + 2; k++) {
          out.trail.push([idx(), active().length, $(".count").textContent]);
          next();
        }
        out.nextDisabledAtEnd = $('[data-nav="1"]').disabled;
        out.dots = $$(".dot").length;
        out.dotCurrent = $$(".dot").map((d) => d.getAttribute("aria-current"));
        return out;
        """,
    )
    assert got["total"] == 5, "każdy wpis ma swoją stronę: trzy szkice, nowa osoba, opublikowana"
    assert got["active"] == ["0"]
    assert got["hiddenOnes"] == [["true", True]] * 4, "reszta jest ukryta, `inert` i poza czytaniem"
    assert got["activeAttrs"] == [None, False]
    assert got["draftRegionsInActive"] == 1, "na ekranie jest jeden szkic naraz"
    assert got["draftRegionsInHidden"] == 0, "szkice innych osób są `inert`"
    assert got["count"] == "1 of 5" and got["prevDisabled"] is True
    # One page at every step, and "next" stops at the last one instead of wrapping.
    assert [step[0] for step in got["trail"]] == [["0"], ["1"], ["2"], ["3"], ["4"], ["4"], ["4"]]
    assert all(step[1] == 1 for step in got["trail"])
    assert [step[2] for step in got["trail"]][:5] == [f"{n} of 5" for n in range(1, 6)]
    assert got["nextDisabledAtEnd"] is True
    assert (
        got["dots"] == 5 and got["dotCurrent"].count("true") == 1 and got["dotCurrent"][4] == "true"
    )


def test_every_draft_is_reachable_by_next_by_the_dots_and_by_the_arrow_keys(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = { byNext: [], byDots: {}, byKeys: [] };
        const drafts = () => $$("[data-panel][data-active] [data-box]").map((r) => r.getAttribute("data-box"));
        for (let k = 0; k < 5; k++) { out.byNext.push(drafts()); next(); }
        $$(".dot").forEach((d, k) => { d.click(); out.byDots[k] = [idx()[0], drafts()]; });
        $$(".dot")[0].click();
        key("ArrowLeft"); out.byKeys.push(idx()[0]);                       // already at the first: stays
        key("ArrowRight"); out.byKeys.push(idx()[0]);
        key("ArrowRight"); out.byKeys.push(idx()[0]);
        key("ArrowLeft"); out.byKeys.push(idx()[0]);
        key("ArrowRight", { shiftKey: true }); out.byKeys.push(idx()[0]);  // a chord is not paging
        key("ArrowRight", { metaKey: true }); out.byKeys.push(idx()[0]);
        for (let k = 0; k < 6; k++) key("ArrowRight");
        out.byKeys.push(idx()[0]);                                         // the last: stays
        prev(); out.byKeys.push(idx()[0]);
        out.dotsAfterKeys = $$(".dot").map((d) => d.getAttribute("aria-current"));
        return out;
        """,
    )
    assert got["byNext"] == [[REV_A], [REV_B], [REV_C], [], []]
    assert {tuple(v[1]) for v in got["byDots"].values()} == {(REV_A,), (REV_B,), (REV_C,), ()}
    assert [got["byDots"][str(k)][0] for k in range(5)] == ["0", "1", "2", "3", "4"]
    assert got["byKeys"] == ["0", "1", "2", "1", "1", "1", "4", "3"]
    assert got["dotsAfterKeys"].count("true") == 1 and got["dotsAfterKeys"][3] == "true"


def test_the_header_has_no_results_line_and_the_room_is_one_click_away(tmp_path: Path) -> None:
    """The founder's decision (11.10): the summary line says who is waiting and nothing about the last days."""
    got = _run(
        tmp_path,
        """
        await open();
        const sum = $(".sum");
        return {
          parts: $$(".sum > span").map((s) => s.textContent),
          text: sum.textContent.replace(/\\s+/g, " ").trim(),
          results: $$(".results, .sl").length,
          room: $$("[data-room]").map((b) => b.textContent),
          head: $("h1").textContent,
        };
        """,
    )
    assert len(got["parts"]) == 2 and got["parts"][0].startswith("newest 3 h ago")
    assert got["parts"][1] == "3 replies waiting for your OK"
    for word in ("days", "posted", "votes", "removed", "41"):
        assert word not in got["text"], word
    assert got["results"] == 0
    assert got["room"] == ["Open Room ↗"], "wyniki zostają w Pokoju, jedno kliknięcie"
    assert got["head"].startswith("3 people are writing right now"), "nagłówek zostaje"
    # No result line either on the card that has nobody on it: only the headline and its sentence.
    nobody = fixture()
    nobody["okazje"], nobody["szkice"] = [], []
    only = _run(
        tmp_path,
        "await open(); return { sum: $$('.sum').length, text: $('#app').textContent.replace(/\\s+/g, ' ').trim() };",
        data=nobody,
    )
    assert only["sum"] == 0 and "votes" not in only["text"] and "posted" not in only["text"].lower()


def test_the_results_of_one_reply_keep_the_rule_that_a_gap_is_a_gap_never_a_zero(
    tmp_path: Path,
) -> None:
    gaps = fixture()
    gaps["wyniki"]["pozycje"][0].update(
        {"glosy": None, "odpowiedzi": None, "widocznosc": "UNVERIFIED"}
    )
    got = _run(
        tmp_path,
        """
        await open();
        for (let k = 0; k < 4; k++) next();
        const page = active()[0];
        return { kind: page.getAttribute("data-kind"), figures: $$(".res dd", page).map((d) => d.textContent.trim()) };
        """,
        data=gaps,
    )
    assert got["kind"] == "posted"
    assert got["figures"] == ["not measured", "not measured", "not confirmed"], (
        "luka w pomiarze to luka, nie zero"
    )
    measured = _run(
        tmp_path,
        "await open(); for (let k = 0; k < 4; k++) next(); return $$('.res dd', active()[0]).map((d) => d.textContent.trim());",
    )
    assert measured == ["41", "3", "visible"]


def test_a_card_drawn_again_stays_on_the_same_person_and_a_single_person_has_no_pager(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        next(); next();
        const out = { before: [idx(), $(".count").textContent] };
        toWin({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { locale: "pl-PL" } });
        await sleep(10);
        out.after = [idx(), $(".count").textContent, doc().documentElement.lang];
        return out;
        """,
    )
    assert got["before"] == [["2"], "3 of 5"]
    assert got["after"] == [["2"], "3 z 5", "pl"], (
        "ten sam człowiek po przerysowaniu, już po polsku"
    )
    alone = fixture()
    alone["okazje"], alone["szkice"] = [], [alone["szkice"][0]]
    one = _run(
        tmp_path,
        "await open(); return { pages: $$('[data-panel]').length, pager: $$('.pager').length, active: idx() };",
        data=alone,
    )
    assert one == {"pages": 1, "pager": 0, "active": ["0"]}


def test_a_page_the_person_leaves_is_folded_again(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        const page = () => active()[0];
        $(".whyt", page()).click();
        out.whyOpen = [page().hasAttribute("data-why"), $(".whyt", page()).getAttribute("aria-expanded")];
        $(".more", page()).click();
        out.replyOpen = [!!$(".ptw[data-open]", page()), $(".more", page()).textContent];
        next(); prev();
        out.afterReturn = [page().hasAttribute("data-why"), $(".whyt", page()).getAttribute("aria-expanded"),
                           !!$(".ptw[data-open]", page()), $(".more", page()).textContent];
        // A person nobody has answered yet has no reply to draw, so their reasons start open (on a wide card)
        // and are open again when the person comes back to them.
        next(); next(); next();
        out.newStarts = [page().getAttribute("data-kind"), page().hasAttribute("data-why"), $(".whyt", page()).getAttribute("aria-expanded")];
        $(".whyt", page()).click();
        out.newClosed = page().hasAttribute("data-why");
        next(); prev();
        out.newBack = [page().hasAttribute("data-why"), $(".whyt", page()).getAttribute("aria-expanded")];
        return out;
        """,
    )
    assert got["whyOpen"] == [True, "true"]
    assert got["replyOpen"] == [True, "Show less"]
    assert got["afterReturn"] == [False, "false", False, "Show all"]
    assert got["newStarts"] == ["new", True, "true"]
    assert got["newClosed"] is False and got["newBack"] == [True, "true"]


# ---------------------------------------------------------------------------
# Send acts on the visible draft only
# ---------------------------------------------------------------------------


def test_send_acts_on_the_visible_draft_only(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        const send = (root) => $('button[data-act="send"]', root);
        const first = $$("[data-panel]")[0], second = $$("[data-panel]")[1];
        next();                                                 // the second draft is on screen
        send(first).click();                                    // a button of a page that is not active
        await sleep(30);
        out.hiddenPageSend = sendCalls();
        send(second).click();                                   // the one the person sees
        await sleep(30);
        out.visiblePageSend = sendCalls();
        out.boxes = box();
        return out;
        """,
        tools={"room.send_token": TOKEN_OK},
    )
    assert got["hiddenPageSend"] == [], "przycisk strony, której nie widać, nic nie robi"
    assert [(c["name"], c["args"]) for c in got["visiblePageSend"]] == [
        ("room.send_token", {"revision_id": REV_B, "odcisk": DIGEST})
    ], "pytanie dotyczy szkicu na ekranie i żadnego innego, a wysyłki nie ma"
    assert [(b["phase"], b["page"]) for b in got["boxes"]] == [("confirm", "1")]


def test_each_draft_asks_for_its_own_confirmation_and_nothing_goes_out_without_the_yes(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = { asked: [] };
        for (let k = 0; k < 3; k++) {
          $('[data-panel][data-active] button[data-act="send"]').click();
          await sleep(30);
          out.asked.push(sendCalls().map((c) => c.args.revision_id));
          next();
        }
        out.names = sendCalls().map((c) => c.name);
        out.boxes = box().map((b) => [b.phase, b.page]);
        return out;
        """,
        tools={"room.send_token": TOKEN_OK},
    )
    assert got["asked"] == [[REV_A], [REV_A, REV_B], [REV_A, REV_B, REV_C]]
    assert set(got["names"]) == {"room.send_token"}, "sama zgoda nie wysyła niczego"
    assert got["boxes"] == [["confirm", "0"], ["confirm", "1"], ["confirm", "2"]]


# ---------------------------------------------------------------------------
# The states are drawn in the active panel
# ---------------------------------------------------------------------------

STATE_SCENARIO = """
await open();
const out = {};
const page = () => active()[0];
const idle = () => $$('button[data-act="send"]', page()).length;
out.idleBefore = idle();
$('button[data-act="send"]', page()).click();
await sleep(%(wait)d);
out.first = box();
out.idleDuring = idle();
out.region = $("[data-box]", page()).getAttribute("data-phase");
out.replyStill = !!$(".pv", page());
%(then)s
return out;
"""


def _states(
    tmp_path: Path, tools: dict[str, Any], *, wait: int = 30, then: str = ""
) -> dict[str, Any]:
    return _run(tmp_path, STATE_SCENARIO % {"wait": wait, "then": then}, tools=tools)


def test_the_consent_is_drawn_inside_the_active_panel_in_place_of_the_buttons(
    tmp_path: Path,
) -> None:
    got = _states(
        tmp_path,
        {"room.send_token": TOKEN_OK},
        then="""
        $('button[data-act="cancel"]', page()).click();
        out.afterCancel = [box().length, idle(), $("[data-box]", page()).getAttribute("data-phase")];
        """,
    )
    assert got["idleBefore"] == 1 and got["idleDuring"] == 0, (
        "przyciski Wyślij i Popraw ustępują miejsca zgodzie"
    )
    [consent] = got["first"]
    assert (consent["phase"], consent["page"], consent["inStage"]) == ("confirm", "0", True)
    assert consent["buttons"] == ["go", "cancel"]
    assert "Wenext can write from" in consent["text"] and "demo_founder_x" in consent["text"]
    assert got["region"] == "confirm" and got["replyStill"], "odpowiedź zostaje nad zdaniem zgody"
    assert got["afterCancel"] == [0, 1, "idle"], "anulowanie oddaje przyciski"


def test_a_busy_queue_is_shown_as_waiting_inside_the_active_panel(tmp_path: Path) -> None:
    got = _states(tmp_path, {"room.send_token": _refusal("ZAKRES_KOLEJKA")}, wait=15)
    [waiting] = got["first"]
    assert (waiting["phase"], waiting["page"], waiting["inStage"]) == ("waiting", "0", True)
    assert (
        waiting["buttons"] == ["cancel"]
        and "Waiting for the previous reply to go out" in waiting["text"]
    )
    assert got["idleDuring"] == 0 and got["region"] == "waiting"


def test_queued_and_posted_are_drawn_inside_the_active_panel(tmp_path: Path) -> None:
    then = """
    $('button[data-act="go"]', page()).click();
    await sleep(12);
    out.queued = box();
    await sleep(120);
    out.later = box();
    """
    queued = _states(
        tmp_path,
        {"room.send_token": TOKEN_OK, "room.send_draft": QUEUED, "room.send_status": STATUS_QUEUED},
        then=then,
    )
    [box_q] = queued["queued"]
    assert (box_q["phase"], box_q["page"], box_q["inStage"]) == ("queued", "0", True)
    assert "Queued." in box_q["text"] and "Posted" not in box_q["text"], (
        "kolejka to jeszcze nie post"
    )
    posted = _states(
        tmp_path,
        {
            "room.send_token": TOKEN_OK,
            "room.send_draft": QUEUED,
            "room.send_status": STATUS_PUBLISHED,
        },
        then=then,
    )
    [box_p] = posted["later"]
    assert (box_p["phase"], box_p["page"], box_p["inStage"]) == ("published", "0", True)
    assert "Posted." in box_p["text"] and box_p["buttons"] == ["live", "close"]
    assert [c for c in (posted["later"])] == [box_p], "jedno pudełko w całej karcie"


def test_a_refusal_is_drawn_inside_the_active_panel_with_a_way_out(tmp_path: Path) -> None:
    got = _states(tmp_path, {"room.send_token": _refusal("BUDZET")})
    [refused] = got["first"]
    assert (refused["phase"], refused["page"], refused["inStage"]) == ("refused", "0", True)
    assert refused["buttons"] == ["presence", "retry", "close"], "odmowa nie jest ślepym zaułkiem"
    assert "posting limit is used up" in refused["text"]


def test_the_state_of_one_draft_stays_with_its_page_when_the_person_pages_away(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        $('[data-panel][data-active] button[data-act="send"]').click();
        await sleep(30);
        next();
        out.onSecond = [box().length, $$("[data-panel][data-active] .send").length,
                        $$('[data-panel][data-active] button[data-act="send"]').length];
        prev();
        out.backOnFirst = $$("[data-panel][data-active] .send").map((s) => s.getAttribute("data-p"));
        out.sends = sendCalls().map((c) => c.name);
        return out;
        """,
        tools={"room.send_token": TOKEN_OK},
    )
    assert got["onSecond"] == [1, 0, 1], (
        "drugi szkic ma swoje przyciski, zgoda pierwszego czeka na swojej stronie"
    )
    assert got["backOnFirst"] == ["confirm"]
    assert got["sends"] == ["room.send_token"]


# ---------------------------------------------------------------------------
# The height the host is told
# ---------------------------------------------------------------------------


def test_the_height_told_to_the_host_is_the_cards_own(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        const intrinsic = [];
        await open((window) => {
          window.openai = { notifyIntrinsicHeight: (h) => intrinsic.push(h) };
          const real = window.Element.prototype.getBoundingClientRect;
          window.Element.prototype.getBoundingClientRect = function () {
            return this.id === "app" ? { width: 719.4, height: 555.2, top: 0, left: 0, right: 719.4, bottom: 555.2 } : real.call(this);
          };
        });
        win.dispatchEvent(new win.Event("resize"));
        await sleep(80);
        const sizes = log.filter((m) => m.method === "ui/notifications/size-changed").map((m) => m.params);
        return { sizes, intrinsic };
        """,
    )
    assert got["sizes"] and all(s == {"width": 720, "height": 556} for s in got["sizes"])
    assert got["intrinsic"] and set(got["intrinsic"]) == {556}
    script = apps_ui.OPPORTUNITIES_HTML
    resize = script[script.index("function resize()") : script.index("function settle()")]
    assert "getBoundingClientRect" in resize and "scrollHeight" not in resize, (
        "wysokość karty, nie dokumentu"
    )
    assert "ResizeObserver" in script, "każda zmiana rozmiaru jest zgłaszana"
    assert "overflow: hidden" in script[script.index("html, body {") :][:140], (
        "karta nie ma paska przewijania"
    )


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def _script() -> str:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    return html[html.index("<script>") : html.index("</script>")]


def test_the_click_handler_ignores_a_button_of_a_page_that_is_not_active() -> None:
    script = _script()
    handler = script[script.index('document.addEventListener("click"') :]
    guard = handler.index('if (pg && !pg.hasAttribute("data-active")) return;')
    assert guard < handler.index('sendAction(b.getAttribute("data-act")'), (
        "straż jest przed każdym działaniem strony"
    )
    assert guard < handler.index('b.hasAttribute("data-fix")')
    # Hidden, inert and out of the reading order, in the markup the pager sets and in the style.
    assert 'p.setAttribute("aria-hidden", "true"); p.setAttribute("inert", "");' in script
    assert re.search(r"\.page \{[^}]*visibility: hidden", apps_ui.OPPORTUNITIES_HTML)


def test_the_card_still_names_every_state_of_a_send_in_one_region() -> None:
    script = _script()
    assert script.count('"room.send_draft"') == 1
    region = script[script.index("function regionInner(") : script.index("function region(")]
    assert (
        'st.phase === "idle" ? approval(d, i) : preview(d, false, true) + sendPanel(d, st)'
        in region
    )


# ---------------------------------------------------------------------------
# The new words
# ---------------------------------------------------------------------------

NEW_KEYS = ("why.ask", "nav.label", "nav.prev", "nav.next", "nav.of", "nav.go")


def test_the_new_strings_are_in_both_languages_with_the_same_placeholders() -> None:
    for key in NEW_KEYS:
        assert key in PL and key in EN, key
        assert isinstance(PL[key], str) and isinstance(EN[key], str), key
        assert PLACEHOLDER.findall(PL[key]) == PLACEHOLDER.findall(EN[key]), key
        assert not LONG_DASHES.search(PL[key]) and not LONG_DASHES.search(EN[key]), key
    assert PLACEHOLDER.findall(EN["nav.of"]) == ["i", "n"] and PLACEHOLDER.findall(
        EN["nav.go"]
    ) == ["i", "n"]
    assert (EN["why.ask"], PL["why.ask"]) == ("Why?", "Dlaczego?")
    assert (EN["nav.of"], PL["nav.of"]) == ("{i} of {n}", "{i} z {n}")
    assert set(EN) == set(PL), "klucze są takie same w obu językach"
    # The founder took the line of results off the card (11.10): its words went with it, the null rule stayed.
    assert not [key for key in PL if key.startswith("st.")] and not [
        key for key in EN if key.startswith("st.")
    ]
    assert "fig.none" in PL and "fig.none" in EN
    # English is English: none of the Polish letters.
    assert not any(re.search("[ąćęłńóśźż]", EN[key]) for key in NEW_KEYS)


def test_the_new_files_have_no_long_dash() -> None:
    for name in ("tests/test_card_pager_unit.py", "control_plane/tools/apps_ui.py"):
        assert not LONG_DASHES.search((CONTROL_PLANE / name).read_text(encoding="utf-8")), name
