"""The card sends a whole reviewed batch with ONE click, and only that click can (v11).

THE FOUNDER (13.10): "I do not want to click twenty times a day." The card's answer is a button in the pager
row, "Send all (N)", a panel that lists every draft with a tick for each (all ticked), the schedule the server
will use and the limits, and "Yes, send these N". What is held here, from the outside in:

  * THE SOURCE: `room.send_batch` is named in ONE place, inside `go`, and `go` is reached by `dispatch("go")`
    and by nothing else; it carries the token and NOTHING ELSE; `room.batch_token` is the question, named
    once, in `ask`; the machine (`makeBatcher`, cut out of the script by its BATCH markers) is PURE;
  * THE MACHINE, run in node with a fake host and a fake clock: the confirmation binds the exact ticked set
    (a tick or an untick asks again and the earlier token is dead), a stale confirmation or a batch that
    changed asks again ONCE and ends at a confirmation that waits for a click, every failure of the chain ends
    in a refusal with a way out and sends nothing, a stale answer after Cancel does nothing, and the follow-up
    asks `room.send_status` only, near requests often and far ones rarely, until the publisher says;
  * THE RANDOM WALK over every action the card has, against a host that answers arbitrarily: `room.send_batch`
    is called only after a press on "Yes", once per confirmation the server minted, never with a token that
    was spent, and always with the confirmation minted for the very set that was ticked at that moment;
  * THE CARD, in jsdom with a fake host that plans from what it is asked: the button and its count (and when
    it is NOT there: a paused scope, an account outside the scope, an expired or an edited draft, hosted
    sending), the panel (one row per draft with platform, who, first line and a tick, the schedule and the
    limits in both languages), untick, confirm, the "Scheduled" list with live status, the page of a
    scheduled draft, and the reasons the publisher gave.

NOT HELD HERE: how it looks (the PNGs the task report names), and that a Codex host carries the card's calls
the way the fake host does (unverified live).
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from control_plane.tools import apps_ui
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_pager_unit import JSDOM, REV_A, REV_B, REV_C, _draft, _iso
from tests.test_card_pager_unit import _run as _base_run
from tests.test_card_v9_unit import v9

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nie jest zainstalowany")

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
NEEDS_JSDOM = pytest.mark.skipif(not JSDOM.is_dir(), reason="potrzebny jsdom (apps/dashboard)")


def _script() -> str:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    return html[html.index("<script>") : html.index("</script>")]


def _code(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


def _batcher_source() -> str:
    script = _script()
    start = script.index("/*BATCH-BEGIN*/") + len("/*BATCH-BEGIN*/")
    return script[start : script.index("/*BATCH-END*/")]


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def test_the_batch_is_sent_in_one_place_and_only_the_yes_button_reaches_it() -> None:
    script = _code(_script())
    machine = _code(_batcher_source())
    assert script.count('"room.send_batch"') == 1, "wysyłka paczki ma jedno miejsce"
    assert script.count('"room.batch_token"') == 1
    go = machine[machine.index("function go()") : machine.index("function follow(")]
    assert '"room.send_batch"' in go
    # THE CALL CARRIES THE TOKEN AND NOTHING ELSE: no list of drafts, no times.
    assert 'env.call("room.send_batch", { token: token })' in go
    ask = machine[machine.index("function ask(") : machine.index("function open(")]
    assert '"room.batch_token"' in ask and '"room.send_batch"' not in ask
    # `go` is defined once and called from `dispatch` once: no timer, no answer, no poll calls it.
    assert machine.count("go()") == 2 and 'name === "go") go()' in machine
    dispatch = machine[machine.index("function dispatch(") :]
    assert dispatch.count("go()") == 1
    # The only button that says "go" is the panel's "Yes", drawn only when the confirmation is in hand.
    assert script.count('data-ba="go"') == 1
    ui = script[script.index("function batchSheetBody(") :]
    yes = ui[ui.index('data-ba="go"') - 120 : ui.index('data-ba="go"') + 80]
    assert "ready" in yes
    # The handlers: a click goes through `batchAction`; a tick only toggles; a key only cancels.
    handler = script[script.index('document.addEventListener("click"') :]
    assert 'batchAction(b.getAttribute("data-ba"), b.getAttribute("data-rev"))' in handler
    change = script[script.index('document.addEventListener("change"') :]
    change = change[: change.index("});")]
    assert 'batcher.dispatch("toggle", t.value)' in change and "go" not in change
    keys = script[script.index('if (ev.key === "Escape" && /^(checking|confirm|refused|done)$/') :]
    keys = keys[: keys.index("return; }")]
    assert 'batcher.dispatch("cancel")' in keys and '"go"' not in keys


def test_the_batch_machine_is_pure_and_talks_to_the_world_only_through_env() -> None:
    machine = _code(_batcher_source())
    for forbidden in (
        "document",
        "window",
        "navigator",
        "setTimeout",
        "setInterval",
        "fetch(",
        "rpc(",
    ):
        assert forbidden not in machine, forbidden
    assert "function makeBatcher(env)" in machine
    for member in (
        "env.call",
        "env.later",
        "env.now",
        "env.paint",
        "env.paintTracked",
        "env.apply",
        "env.T",
        "env.reason",
    ):
        assert member in machine, member
    # The only tools the machine names: the question, the act and the follow-up read.
    assert set(re.findall(r'"(room\.[a-z_]+)"', machine)) == {
        "room.batch_token",
        "room.send_batch",
        "room.send_status",
    }


def test_the_confirmation_is_read_from_meta_under_the_servers_key_and_nowhere_else() -> None:
    machine = _code(_batcher_source())
    assert f'META = "{META_TOKEN}"' in machine
    token = machine[machine.index("function tokenOf(") : machine.index("function set(")]
    assert "_meta" in token and "structuredContent" not in token and "content[0]" not in token


def test_the_poll_asks_only_room_send_status_and_the_cadence_is_the_documented_one() -> None:
    machine = _code(_batcher_source())
    assert "var STEP = 5000, SLOW = 12, SOON = 45000, LINGER = 1800000;" in machine
    poll = machine[machine.index("function poll(") : machine.index("function dispatch(")]
    assert poll.count("env.call(") == 1 and '"room.send_status"' in poll


def test_the_new_words_are_in_both_languages_with_the_same_placeholders() -> None:
    keys = {k for k in PL if k.startswith(("ba.", "rs."))}
    assert keys == {k for k in EN if k.startswith(("ba.", "rs."))} and len(keys) >= 50
    placeholder = re.compile(r"\{(\w+)\}")
    for key in keys:
        pl = {
            n
            for f in (PL[key] if isinstance(PL[key], list) else [PL[key]])
            for n in placeholder.findall(f)
        }
        en = {
            n
            for f in (EN[key] if isinstance(EN[key], list) else [EN[key]])
            for n in placeholder.findall(f)
        }
        assert pl == en, key
        for text in (PL[key], EN[key]):
            for form in text if isinstance(text, list) else [text]:
                assert not LONG_DASHES.search(form), key
        for form in EN[key] if isinstance(EN[key], list) else [EN[key]]:
            assert not re.search("[ąćęłńóśźż]", form), key
    assert EN["ba.open"] == "Send all ({n})" and PL["ba.open"] == "Wyślij wszystkie ({n})"
    assert EN["ba.yes"][1] == "Yes, send these {n}"
    for code in (
        "PACZKA_PUSTA", "PACZKA_ZA_DUZA", "PACZKA_ZMIENIONA", "POZA_ZAKRESEM", "POZA_LIMITEM",
        "LIMIT_WLASNYCH", "OKNO_ZA_KROTKIE", "ZAKRES_WYGASA", "PLAN_PRZESUNIETY", "BRAK_LIMITU",
    ):  # fmt: skip
        assert f"ref.{code}" in PL and f"ref.{code}" in EN, code
    for code in (
        "ZA_STARY", "ZNIKNAL", "ZABLOKOWANY", "JUZ_ODPOWIEDZIANO", "SESJA", "SESJA_NIEPEWNA",
        "INNE_KONTO", "TEMPO", "KAPSULA",
    ):  # fmt: skip
        assert f"rs.{code}" in PL and f"rs.{code}" in EN, code


# ---------------------------------------------------------------------------
# The machine, run in node: a fake host and a fake clock
# ---------------------------------------------------------------------------

HARNESS = r"""
var clock = { now: 0, timers: [] };
var T0 = Date.parse("2026-10-13T12:00:00Z");
function later(fn, ms) { clock.timers.push({ at: clock.now + ms, fn: fn, n: clock.timers.length }); return 1; }
function drain() { return new Promise(function (r) { setImmediate(r); }); }
async function tick(ms) {
  var target = clock.now + ms;
  await drain();
  for (;;) {
    var due = clock.timers.filter(function (t) { return t.at <= target; })
      .sort(function (a, b) { return a.at - b.at || a.n - b.n; })[0];
    if (!due) break;
    clock.timers.splice(clock.timers.indexOf(due), 1);
    clock.now = Math.max(clock.now, due.at);
    due.fn();
    await drain();
  }
  clock.now = target;
  await drain();
}
var TOKEN_KEY = "wenext/potwierdzenie";
function res(structured, meta) { var r = { structuredContent: structured }; if (meta) r._meta = meta; return { result: r }; }
// Relative to the fake clock NOW: the scenarios share one clock, and a moment is always "so many minutes from now".
function iso(minutes) { return new Date(T0 + clock.now + minutes * 60000).toISOString(); }
function plan(ids, extra) {
  return Object.assign({ ile: ids.length, teraz: 1, odstep_min: 15, zwloka_min: [1, 4], koniec: iso(16 * ids.length),
    limity: { godzina: 4, doba: 20, wlasne_doba: 1 }, zestaw: "z".repeat(64),
    pozycje: ids.map(function (id, i) { return { lp: i + 1, revision_id: id, platforma: "REDDIT", konto: "u", rodzaj: "REPLY", kiedy: iso(16 * i) }; }) }, extra || {});
}
function tokenAnswer(token, ids, extra) {
  var m = {}; m[TOKEN_KEY] = token;
  return res({ stan: "WYKONANE", paczka: plan(ids, extra), odrzucone: [] }, m);
}
function refusal(kod, extra) { return res(Object.assign({ stan: "NIEDOSTEPNE", kod: kod, powod: "powod " + kod }, extra || {})); }
function sentAnswer(ids) {
  return res({ stan: "OCZEKUJE", powod: "x", zaplanowane: ids.map(function (id, i) {
    return { revision_id: id, wniosek: "req-" + id, kiedy: iso(16 * i), platforma: "REDDIT", konto: "u" }; }), odmowy: [] });
}
function statusAnswer(status, extra) { return res({ stan: "WYKONANE", wniosek: Object.assign({ id: "r", status: status, widocznosc: null,
  opublikowano: null, adres: null, powod: null, powod_kod: null, najwczesniej: null }, extra || {}) }); }

var CANDS = [ { rev: "r1", odcisk: "a".repeat(64) }, { rev: "r2", odcisk: "b".repeat(64) }, { rev: "r3", odcisk: "c".repeat(64) } ];

// A host that answers from a script: for each tool a function of (the call number of that tool, args, world).
function makeWorld(answers) {
  answers = Object.assign({ "room.send_status": function () { return statusAnswer("QUEUED"); } }, answers);
  var w = { calls: [], applied: [], paints: [], tracked: [], minted: {}, spent: {}, doubleUse: 0, unknownToken: 0, badSend: [], setMismatch: 0, goPresses: 0, wrongSet: 0 };
  w.count = function (name) { return w.calls.filter(function (c) { return c.name === name; }).length; };
  w.env = {
    call: function (name, args) {
      var n = w.count(name);
      w.calls.push({ name: name, args: args, at: clock.now, phase: w.b ? w.b.state().phase : null });
      if (name === "room.send_batch") {
        if (Object.keys(args).join() !== "token") w.badSend.push(Object.keys(args));
        if (!w.minted[args.token]) w.unknownToken++;
        else {
          var st = w.b.state(), now = (st.cands || []).filter(function (c) { return st.picks[c.rev] && !st.excl[c.rev]; }).map(function (c) { return c.rev; }).sort().join();
          if (w.minted[args.token] !== now) w.wrongSet++;
        }
        if (w.spent[args.token]) w.doubleUse++;
        w.spent[args.token] = true;
      }
      return Promise.resolve(answers[name](n, args, w)).then(function (answer) {
        if (name === "room.batch_token" && answer && answer.result && answer.result._meta && answer.result._meta[TOKEN_KEY]) {
          // The server minted it for the set IN ITS PLAN (it may have left some out), not for the set it was asked about.
          var planned = answer.result.structuredContent && answer.result.structuredContent.paczka
            ? answer.result.structuredContent.paczka.pozycje.map(function (p) { return p.revision_id; }) : args.revision_ids;
          w.minted[answer.result._meta[TOKEN_KEY]] = planned.slice().sort().join();
        }
        return answer;
      });
    },
    later: later,
    now: function () { return T0 + clock.now; },
    paint: function () { w.paints.push(w.b.state().phase); },
    paintTracked: function () { w.tracked.push(w.b.tracked().map(function (t) { return t.status; }).join()); },
    apply: function (list) { w.applied.push(list.map(function (t) { return t.rev; })); },
    T: function (k) { return k; },
    reason: function (data, why) { return (data && data.kod) || why || "?"; }
  };
  w.b = makeBatcher(w.env);
  w.open = function () { w.b.dispatch("open", CANDS.map(function (c) { return { rev: c.rev, odcisk: c.odcisk }; })); };
  w.click = function (name, arg) { if (name === "go" && w.b.state().phase === "confirm" && w.b.state().token) w.goPresses++; w.b.dispatch(name, arg); };
  w.st = function () { return w.b.state(); };
  w.names = function () { return w.calls.map(function (c) { return c.name; }); };
  return w;
}
function snap(w) {
  var st = w.st();
  return { phase: st.phase, calls: w.names(), reason: st.reason || null, code: st.code || null, token: st.token || null,
    changed: !!st.changed, planN: st.plan ? st.plan.pozycje.length : null, picks: st.picks || null, excl: st.excl ? Object.keys(st.excl) : null, at: clock.now };
}
function ticked(w) { var st = w.st(); return (st.cands || []).filter(function (c) { return st.picks[c.rev] && !st.excl[c.rev]; }).map(function (c) { return c.rev; }); }
"""

SCENARIOS = r"""
var out = {};
var tokens = 0;
function okAsk() { return function (n, args) { return tokenAnswer("tok-" + (tokens++), args.revision_ids); }; }

// 1. THE HAPPY PATH: open asks for the whole set, Yes sends it with the token and nothing else, the list follows.
async function happy() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function (n, args) { return sentAnswer(["r1", "r2", "r3"]); },
    "room.send_status": function (n, args) {
      var id = args.request_id;
      return id === "req-r1" ? statusAnswer("PUBLISHED", { adres: "https://www.reddit.com/r/x/c/1/", widocznosc: "CONFIRMED" }) : statusAnswer("QUEUED", { najwczesniej: iso(16) });
    }
  });
  var seen = [];
  w.open(); seen.push(snap(w)); await tick(0); seen.push(snap(w));
  var asked = w.calls[0].args;
  w.click("go"); seen.push(snap(w)); await tick(0); seen.push(snap(w));
  var sent = w.calls.filter(function (c) { return c.name === "room.send_batch"; });
  var applied = w.applied.slice();
  await tick(5000); var afterOne = w.names().filter(function (n) { return n === "room.send_status"; }).length;
  await tick(60000);
  var statuses = w.b.tracked().map(function (t) { return [t.rev, t.status, t.url]; });
  return { seen: seen, asked: asked, sent: sent.map(function (c) { return c.args; }), applied: applied, afterOne: afterOne,
    statuses: statuses, names: w.names(), minted: Object.keys(w.minted).length };
}

// 2. A tick or an untick is a NEW question for the new set; the earlier confirmation is never used.
async function untick() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function (n, args) { return sentAnswer(ticked(w)); }
  });
  w.open(); await tick(0);
  var first = w.st().token;
  w.click("toggle", "r2"); var checking = snap(w); await tick(0);
  var second = snap(w);
  w.click("go"); await tick(0);
  var third = snap(w);
  return { first: first, checking: checking, second: second, third: third,
    asks: w.calls.filter(function (c) { return c.name === "room.batch_token"; }).map(function (c) { return c.args; }),
    sends: w.calls.filter(function (c) { return c.name === "room.send_batch"; }).map(function (c) { return c.args; }), wrongSet: w.wrongSet };
}

// 3. Untick everything: no question, no Yes, nothing to send, and a press on go does nothing.
async function untickAll() {
  tokens = 0;
  var w = makeWorld({ "room.batch_token": okAsk(), "room.send_batch": function () { return sentAnswer([]); } });
  w.open(); await tick(0);
  var asks = w.count("room.batch_token");
  w.click("toggle", "r1"); await tick(0); w.click("toggle", "r2"); await tick(0); w.click("toggle", "r3"); await tick(0);
  var empty = snap(w);
  w.click("go"); await tick(0);
  w.click("toggle", "r2"); await tick(0);
  return { asksAtOpen: asks, empty: empty, sends: w.count("room.send_batch"), back: snap(w), asksAfter: w.count("room.batch_token") };
}

// 4. The server turned some drafts down: they are unticked, shown with their reason, and cannot be ticked.
async function excluded() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": function (n, args) {
      var m = {}; m[TOKEN_KEY] = "tok-" + (tokens++);
      var ids = args.revision_ids.filter(function (id) { return id !== "r2"; });
      return res({ stan: "WYKONANE", paczka: plan(ids), odrzucone: args.revision_ids.indexOf("r2") >= 0 ? [{ revision_id: "r2", kod: "POZA_LIMITEM", powod: "p" }] : [] }, m);
    },
    "room.send_batch": function () { return sentAnswer(["r1", "r3"]); }
  });
  w.open(); await tick(0);
  var s = snap(w);
  w.click("toggle", "r2"); await tick(0);        // an excluded draft cannot be ticked
  var after = snap(w);
  return { s: s, after: after, asks: w.count("room.batch_token") };
}

// 5. The confirmation went stale at the click: ask again ONCE, end at a confirmation that waits for a click.
async function stale() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function (n) { return n < 2 ? refusal(n === 0 ? "POTWIERDZENIE" : "PACZKA_ZMIENIONA") : sentAnswer(["r1", "r2", "r3"]); }
  });
  w.open(); await tick(0);
  w.click("go"); await tick(0);
  var afterFirst = snap(w);            // asked again, ended at confirm, did NOT send by itself
  var sendsAfterFirst = w.count("room.send_batch");
  await tick(600000);
  var stillWaiting = snap(w);
  w.click("go"); await tick(0);        // the second click: refused again, and now it is shown
  var afterSecond = snap(w);
  return { afterFirst: afterFirst, sendsAfterFirst: sendsAfterFirst, stillWaiting: stillWaiting, afterSecond: afterSecond,
    asks: w.count("room.batch_token"), sends: w.count("room.send_batch") };
}

// 6. A batch that changed since it was shown: the new plan is shown with a note, and nothing was sent unseen.
async function changed() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function (n) { return n === 0 ? refusal("PACZKA_ZMIENIONA", { odrzucone: [{ revision_id: "r1", kod: "ODCISK", powod: "x" }] }) : sentAnswer(["r1", "r2", "r3"]); }
  });
  w.open(); await tick(0); w.click("go"); await tick(0);
  var s = snap(w);
  return { s: s, sends: w.count("room.send_batch") };
}

// 7. Every way the chain can fail ends in a refusal with a way out, and nothing is sent.
async function refusals() {
  var o = {};
  var cases = {
    refusedAll: { "room.batch_token": function () { return refusal("POZA_ZAKRESEM", { odrzucone: [{ revision_id: "r1", kod: "POZA_ZAKRESEM", powod: "p" }] }); } },
    silent: { "room.batch_token": function () { return { error: "silent" }; } },
    rejected: { "room.batch_token": function () { return { error: "rejected" }; } },
    noToken: { "room.batch_token": function (n, args) { return res({ stan: "WYKONANE", paczka: plan(args.revision_ids), odrzucone: [] }); } },
    tokenInTextOnly: { "room.batch_token": function (n, args) { return { result: { content: [{ type: "text", text: JSON.stringify({ stan: "WYKONANE", paczka: plan(args.revision_ids), potwierdzenie: "leaked" }) }] } }; } },
    sendSilent: { "room.batch_token": function (n, args) { return tokenAnswer("t", args.revision_ids); }, "room.send_batch": function () { return { error: "silent" }; } },
    sendRefused: { "room.batch_token": function (n, args) { return tokenAnswer("t", args.revision_ids); }, "room.send_batch": function () { return refusal("BUDZET"); } },
    sendNothing: { "room.batch_token": function (n, args) { return tokenAnswer("t", args.revision_ids); }, "room.send_batch": function () { return res({ stan: "OCZEKUJE", zaplanowane: [] }); } }
  };
  for (var name in cases) {
    tokens = 0;
    var w = makeWorld(Object.assign({ "room.send_batch": function () { return sentAnswer([]); } }, cases[name]));
    w.open(); await tick(0); w.click("go"); await tick(0); await tick(600000);
    o[name] = { s: snap(w), sends: w.count("room.send_batch"), tracked: w.b.tracked().length };
    w.click("retry"); await tick(0);
    o[name].retried = w.count("room.batch_token");
    w.click("close"); await tick(0);
    o[name].closed = w.st().phase;
  }
  return o;
}

// 8. A stale answer (after Cancel) is dropped and does nothing; so is a second open while one is under way.
async function staleAnswers() {
  var pending = null;
  var w = makeWorld({
    "room.batch_token": function () { return new Promise(function (r) { pending = r; }); },
    "room.send_batch": function () { return sentAnswer(["r1", "r2", "r3"]); }
  });
  w.open(); w.open(); await tick(0);
  var checking = snap(w);
  w.click("cancel"); await tick(0);
  pending(tokenAnswer("tok-stale", ["r1", "r2", "r3"])); await tick(0); await tick(60000);
  w.click("go"); await tick(0);
  return { checking: checking, after: snap(w), asks: w.count("room.batch_token"), sends: w.count("room.send_batch") };
}

// 9. A tick while the question is under way: only the LAST answer counts.
async function racingTicks() {
  var pendings = [];
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": function (n, args) { return new Promise(function (r) { pendings.push([r, args.revision_ids]); }); },
    "room.send_batch": function () { return sentAnswer(ticked(w)); }
  });
  w.open(); await tick(0);
  w.click("toggle", "r1"); await tick(0);
  w.click("toggle", "r3"); await tick(0);
  // the answers arrive in the wrong order: the first question's last
  pendings[2][0](tokenAnswer("tok-last", pendings[2][1])); await tick(0);
  pendings[0][0](tokenAnswer("tok-first", pendings[0][1])); await tick(0);
  pendings[1][0](tokenAnswer("tok-mid", pendings[1][1])); await tick(0);
  var s = snap(w);
  w.click("go"); await tick(0);
  return { s: s, sends: w.calls.filter(function (c) { return c.name === "room.send_batch"; }).map(function (c) { return c.args; }), wrongSet: w.wrongSet, asks: pendings.map(function (p) { return p[1]; }) };
}

// 10. THE FOLLOW-UP: near requests every 5 s, far ones every minute, only room.send_status, until the
//     publisher says; reasons come through; then it stops, and after the last moment it rests.
async function followUp() {
  tokens = 0;
  var script = { "req-r1": ["QUEUED", "QUEUED", "PUBLISHED"], "req-r2": ["QUEUED"], "req-r3": ["QUEUED", "SKIPPED"] };
  var seen = {};
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function () { return sentAnswer(["r1", "r2", "r3"]); },
    "room.send_status": function (n, args) {
      var id = args.request_id, k = seen[id] = (seen[id] || 0) + 1, list = script[id], s = list[Math.min(k - 1, list.length - 1)];
      if (s === "SKIPPED") return statusAnswer("SKIPPED", { powod: "wątek jest już za stary", powod_kod: "ZA_STARY" });
      if (s === "PUBLISHED") return statusAnswer("PUBLISHED", { adres: "https://www.reddit.com/r/x/c/1/" });
      return statusAnswer("QUEUED");
    }
  });
  w.open(); await tick(0); w.click("go"); await tick(0);
  var polls = function () { return w.calls.filter(function (c) { return c.name === "room.send_status"; }); };
  // r1's moment is now: it is polled at once and every 5 s; r2 (+16 min) and r3 (+32 min) wait for the minute
  await tick(5000); var r1First = polls().map(function (c) { return c.args.request_id; });
  await tick(5000); await tick(5000); await tick(5000);
  var early = polls().map(function (c) { return [c.args.request_id, c.at]; });
  await tick(60000); var afterMinute = polls().filter(function (c) { return c.args.request_id !== "req-r1"; }).length;
  var rows = w.b.tracked().map(function (t) { return [t.rev, t.status, t.code, t.reason, t.url]; });
  // far into the future: everything is final or rests after the last moment + 30 minutes
  await tick(3 * 3600 * 1000);
  var before = w.count("room.send_status");
  await tick(3600 * 1000);
  var after = w.count("room.send_status");
  return { r1First: r1First, early: early, afterMinute: afterMinute, rows: rows, before: before, after: after, resting: w.b.resting(), names: w.names() };
}

// 11. "Check again" after the rest asks about what is not final, again.
async function resting() {
  tokens = 0;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function () { return sentAnswer(["r1", "r2", "r3"]); },
    "room.send_status": function () { return statusAnswer("QUEUED"); }
  });
  w.open(); await tick(0); w.click("go"); await tick(0);
  await tick(2 * 3600 * 1000);
  var rested = w.b.resting(), polls = w.count("room.send_status");
  await tick(3600 * 1000);
  var still = w.count("room.send_status");
  w.click("refresh"); await tick(0); await tick(5000);
  var afterRefresh = w.count("room.send_status");
  await tick(5000);
  return { rested: rested, polls: polls, still: still, afterRefresh: afterRefresh, afterSecondRound: w.count("room.send_status"), restingAfter: w.b.resting() };
}

// 12. Close / Done / Cancel while the send is under way do nothing: the answer must land somewhere.
async function noEscapeWhileSending() {
  tokens = 0;
  var release = null;
  var w = makeWorld({
    "room.batch_token": okAsk(),
    "room.send_batch": function () { return new Promise(function (r) { release = r; }); }
  });
  w.open(); await tick(0); w.click("go"); await tick(0);
  var sending = snap(w);
  w.click("cancel"); w.click("close"); w.click("done"); await tick(0);
  var stillSending = snap(w);
  release(sentAnswer(["r1", "r2", "r3"])); await tick(0);
  return { sending: sending, stillSending: stillSending, done: snap(w), tracked: w.b.tracked().length };
}

// 13. THE RANDOM WALK: every action the card has, against a host that answers arbitrarily.
function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
async function walk(seed) {
  var r = rng(seed), pick = function (list) { return list[Math.floor(r() * list.length)]; };
  var minted = 0;
  var w = makeWorld({
    "room.batch_token": function (n, args) {
      var k = pick(["ok", "ok", "ok", "ok", "some", "refused", "error", "notoken"]);
      if (k === "ok") return tokenAnswer("tok-" + seed + "-" + (minted++), args.revision_ids);
      if (k === "some") {
        var m = {}; m[TOKEN_KEY] = "tok-" + seed + "-" + (minted++);
        var drop = args.revision_ids.length > 1 ? args.revision_ids[0] : null;
        var keepIds = args.revision_ids.filter(function (id) { return id !== drop; });
        return res({ stan: "WYKONANE", paczka: plan(keepIds), odrzucone: drop ? [{ revision_id: drop, kod: "POZA_LIMITEM", powod: "p" }] : [] }, m);
      }
      if (k === "refused") return refusal(pick(["POZA_ZAKRESEM", "ZAKRES_WSTRZYMANY", "PACZKA_PUSTA"]));
      if (k === "error") return { error: pick(["silent", "rejected"]) };
      return res({ stan: "WYKONANE", paczka: plan(args.revision_ids) });
    },
    "room.send_batch": function () {
      var k = pick(["ok", "ok", "ok", "stale", "changed", "refused", "error"]);
      if (k === "ok") return sentAnswer(ticked(w));
      if (k === "stale") return refusal("POTWIERDZENIE");
      if (k === "changed") return refusal("PACZKA_ZMIENIONA");
      if (k === "refused") return refusal("BUDZET");
      return { error: "silent" };
    },
    "room.send_status": function () {
      return statusAnswer(pick(["QUEUED", "QUEUED", "PUBLISHED", "SKIPPED", "UNKNOWN", "PUBLISHING"]));
    }
  });
  var actions = ["open", "toggle1", "toggle2", "toggle3", "go", "go", "retry", "cancel", "close", "done", "refresh", "t1", "t5", "t60", "t3h"];
  for (var step = 0; step < 90; step++) {
    var a = pick(actions);
    if (a === "t1") await tick(1000);
    else if (a === "t5") await tick(5000);
    else if (a === "t60") await tick(60000);
    else if (a === "t3h") await tick(40 * 60 * 1000);
    else if (a === "open") { if (w.st().phase === "idle") w.open(); await tick(0); }
    else if (a.indexOf("toggle") === 0) { w.click("toggle", "r" + a.slice(6)); await tick(0); }
    else { w.click(a); await tick(0); }
  }
  await tick(2 * 3600 * 1000);
  var polledOther = w.calls.filter(function (c) { return c.name === "room.send_status" && !/^req-r[123]$/.test(c.args.request_id); }).length;
  var unknownNames = w.names().filter(function (n) { return ["room.batch_token", "room.send_batch", "room.send_status"].indexOf(n) < 0; }).length;
  return { sends: w.count("room.send_batch"), asks: w.count("room.batch_token"), goPresses: w.goPresses, doubleUse: w.doubleUse,
    unknownToken: w.unknownToken, badSend: w.badSend.length, wrongSet: w.wrongSet, polledOther: polledOther, unknownNames: unknownNames };
}

(async function () {
  out.happy = await happy();
  out.untick = await untick();
  out.untickAll = await untickAll();
  out.excluded = await excluded();
  out.stale = await stale();
  out.changed = await changed();
  out.refusals = await refusals();
  out.staleAnswers = await staleAnswers();
  out.racingTicks = await racingTicks();
  out.followUp = await followUp();
  out.resting = await resting();
  out.noEscapeWhileSending = await noEscapeWhileSending();
  var walks = [];
  for (var seed = 1; seed <= 120; seed++) walks.push(await walk(seed));
  out.walks = walks;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> dict:
    program = tmp_path_factory.mktemp("card-batch") / "batch.js"
    program.write_text(_batcher_source() + HARNESS + SCENARIOS, encoding="utf-8")
    done = subprocess.run(
        ["node", str(program)], capture_output=True, text=True, timeout=180, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_open_asks_for_the_whole_set_and_yes_sends_with_the_token_and_nothing_else(
    observed,
) -> None:
    got = observed["happy"]
    opening, planned, sending, sent = got["seen"]
    assert opening["phase"] == "checking" and opening["token"] is None
    assert planned["phase"] == "confirm" and planned["token"] == "tok-0" and planned["planN"] == 3
    assert got["asked"] == {
        "revision_ids": ["r1", "r2", "r3"],
        "odciski": ["a" * 64, "b" * 64, "c" * 64],
    }
    assert sending["phase"] == "sending" and sent["phase"] == "done"
    # THE ACT CARRIES THE TOKEN AND NOTHING ELSE.
    assert got["sent"] == [{"token": "tok-0"}]
    assert got["applied"] == [["r1", "r2", "r3"]], "the pages learn which drafts were scheduled"
    assert got["names"][:3] == ["room.batch_token", "room.send_batch", "room.send_status"]


def test_the_list_is_followed_with_send_status_only_and_a_publication_is_posted_only_when_the_publisher_says(
    observed,
) -> None:
    got = observed["happy"]
    assert set(got["names"]) == {"room.batch_token", "room.send_batch", "room.send_status"}
    by = {rev: (status, url) for rev, status, url in got["statuses"]}
    assert by["r1"] == ("PUBLISHED", "https://www.reddit.com/r/x/c/1/")
    assert by["r2"] == ("QUEUED", None), "a request in the queue is not a post"


def test_a_tick_or_an_untick_is_a_new_question_and_the_earlier_confirmation_is_never_used(
    observed,
) -> None:
    got = observed["untick"]
    assert got["first"] == "tok-0"
    assert got["checking"]["phase"] == "checking" and got["checking"]["token"] is None, (
        "the old token is dropped at once"
    )
    assert (
        got["second"]["phase"] == "confirm"
        and got["second"]["token"] == "tok-1"
        and got["second"]["planN"] == 2
    )
    assert got["asks"] == [
        {"revision_ids": ["r1", "r2", "r3"], "odciski": ["a" * 64, "b" * 64, "c" * 64]},
        {"revision_ids": ["r1", "r3"], "odciski": ["a" * 64, "c" * 64]},
    ]
    assert got["sends"] == [{"token": "tok-1"}], "only the token of the set that was on the screen"
    assert got["wrongSet"] == 0 and got["third"]["phase"] == "done"


def test_with_nothing_ticked_there_is_no_question_no_yes_and_nothing_to_send(observed) -> None:
    got = observed["untickAll"]
    assert got["asksAtOpen"] == 1
    assert (
        got["empty"]["phase"] == "confirm"
        and got["empty"]["token"] is None
        and got["empty"]["planN"] is None
    )
    assert got["sends"] == 0, "go with no confirmation does nothing"
    assert got["back"]["phase"] == "checking" or got["back"]["phase"] == "confirm"
    assert got["asksAfter"] == 4, "ticking one again asks once more"


def test_a_draft_the_server_turned_down_is_unticked_and_cannot_be_ticked(observed) -> None:
    got = observed["excluded"]
    assert got["s"]["excl"] == ["r2"] and got["s"]["planN"] == 2
    assert got["after"]["excl"] == ["r2"] and got["asks"] == 1, "a click on it asks nothing"


def test_a_stale_confirmation_or_a_changed_batch_asks_again_once_and_waits_for_a_click(
    observed,
) -> None:
    got = observed["stale"]
    assert got["afterFirst"]["phase"] == "confirm" and got["afterFirst"]["token"] == "tok-1"
    assert got["sendsAfterFirst"] == 1, "the healing did not send by itself"
    assert got["stillWaiting"]["phase"] == "confirm", (
        "and it keeps waiting for the click, however long"
    )
    assert (
        got["afterSecond"]["phase"] == "refused"
        and got["afterSecond"]["code"] == "PACZKA_ZMIENIONA"
    )
    assert got["sends"] == 2 and got["asks"] == 2, (
        "ask again ONCE: a second refusal is shown, not healed"
    )
    changed = observed["changed"]
    assert (
        changed["s"]["phase"] == "confirm"
        and changed["s"]["changed"] is True
        and changed["sends"] == 1
    )


def test_every_failure_of_the_chain_ends_in_a_refusal_with_a_way_out_and_sends_nothing(
    observed,
) -> None:
    got = observed["refusals"]
    for name, case in got.items():
        assert case["s"]["phase"] == "refused", name
        assert case["closed"] == "idle", name
    for name in ("refusedAll", "silent", "rejected", "noToken", "tokenInTextOnly"):
        assert got[name]["sends"] == 0, name
    assert (
        got["refusedAll"]["s"]["code"] == "POZA_ZAKRESEM"
        and got["refusedAll"]["s"]["reason"] == "POZA_ZAKRESEM"
    )
    assert (
        got["noToken"]["s"]["reason"] == "e.noToken"
        and got["tokenInTextOnly"]["s"]["reason"] == "e.noToken"
    )
    assert got["silent"]["s"]["reason"] == "e.noConnect"
    assert got["sendSilent"]["s"]["reason"] == "e.unsure", (
        "silence after the click says it does not know"
    )
    assert got["sendRefused"]["s"]["code"] == "BUDZET"
    # "Try again" asks the question again, from the top; it never sends.
    assert all(case["retried"] >= 2 for name, case in got.items() if name in ("silent", "rejected"))


def test_a_stale_answer_after_cancel_does_nothing_and_a_second_open_is_ignored(observed) -> None:
    got = observed["staleAnswers"]
    assert got["checking"]["phase"] == "checking" and got["after"]["phase"] == "idle"
    assert got["asks"] == 1 and got["sends"] == 0


def test_only_the_last_question_counts_when_the_answers_arrive_out_of_order(observed) -> None:
    got = observed["racingTicks"]
    assert got["s"]["phase"] == "confirm" and got["s"]["token"] == "tok-last"
    assert got["sends"] == [{"token": "tok-last"}] and got["wrongSet"] == 0
    assert got["asks"][-1] == ["r2"], (
        "r1 and r3 were unticked, so the last question was about r2 alone"
    )


def test_the_follow_up_asks_near_requests_often_and_far_ones_each_minute_and_never_for_ever(
    observed,
) -> None:
    got = observed["followUp"]
    assert set(got["names"]) == {"room.batch_token", "room.send_batch", "room.send_status"}
    # The first round asks about everything (to see where it stands); then r1, whose moment is now, every 5 s,
    # until it is final, while r2 and r3 (16 and 32 minutes away) wait for the minute.
    assert got["r1First"] == ["req-r1", "req-r2", "req-r3"]
    early = [req for req, _at in got["early"]]
    assert (
        early.count("req-r1") == 3 and early.count("req-r2") == 1 and early.count("req-r3") == 1
    ), "a far request is not asked about every 5 s"
    ats = [at for req, at in got["early"] if req == "req-r1"]
    assert [later - earlier for earlier, later in pairwise(ats)] == [
        5000,
        5000,
    ], "every five seconds"
    assert got["afterMinute"] == 4, (
        "after a minute the far ones are asked about again, and only once"
    )
    rows = {rev: (status, code, reason) for rev, status, code, reason, _url in got["rows"]}
    assert rows["r1"][0] == "PUBLISHED" and rows["r3"][0] == "SKIPPED"
    assert rows["r3"][1] == "ZA_STARY" and "za stary" in rows["r3"][2], (
        "the publisher's reason and its code"
    )
    assert rows["r2"][0] == "QUEUED"
    assert got["resting"] is True and got["before"] == got["after"], (
        "after the last moment it rests: no more calls"
    )


def test_check_again_after_the_rest_asks_about_what_is_not_final(observed) -> None:
    got = observed["resting"]
    assert got["rested"] is True and got["still"] == got["polls"]
    assert got["afterRefresh"] > got["still"], "one round asks about everything that is not final"
    assert got["afterSecondRound"] == got["afterRefresh"] and got["restingAfter"] is True, (
        "then it rests again"
    )


def test_close_cancel_and_done_do_nothing_while_the_send_is_under_way(observed) -> None:
    got = observed["noEscapeWhileSending"]
    assert got["sending"]["phase"] == "sending" and got["stillSending"]["phase"] == "sending"
    assert got["done"]["phase"] == "done" and got["tracked"] == 3, (
        "the answer lands, and the list exists"
    )


def test_the_random_walk_sends_only_after_a_press_on_yes_and_only_the_set_that_was_confirmed(
    observed,
) -> None:
    walks = observed["walks"]
    assert len(walks) == 120
    assert sum(w["sends"] for w in walks) > 80, "a walk that never sends would prove nothing"
    for w in walks:
        # One act per press on "Yes" that had a confirmation, never more.
        assert w["sends"] <= w["goPresses"], w
        # Never a token the server did not mint, never a spent one, always the token of the ticked set.
        assert w["unknownToken"] == 0 and w["doubleUse"] == 0 and w["wrongSet"] == 0, w
        assert w["badSend"] == 0, "the act carries the token and nothing else"
        assert w["polledOther"] == 0 and w["unknownNames"] == 0, w


# ---------------------------------------------------------------------------
# The card, in jsdom
# ---------------------------------------------------------------------------

TOKEN = "batch-token-" + "b" * 20

LIB_BATCH = r"""
const T0 = Date.now();
const MINTED = {};
const SENT = [];
const ASKS = [];
const SCRIPT = { status: {} };                 // request id -> a function giving the next status answer
const SEEN = {};
let SERIAL = 0;
const iso = (minutes) => new Date(T0 + minutes * 60000).toISOString();
// EXCLUDE (revision -> code), SENTMODE, LIMITS and ORDER come from the prelude `_run` writes before this library.

const orig = host.postMessage.bind(host);
function answer(id, t) {
  const result = { content: [{ type: "text", text: JSON.stringify(t.structured) }], structuredContent: t.structured };
  if (t.meta) result._meta = t.meta;
  return reply(id, result);
}
host.postMessage = function (m) {
  if (m.method !== "tools/call") return orig(m);
  const name = m.params.name, args = m.params.arguments || {};
  if (name === "room.batch_token") {
    log.push(m); ASKS.push(args);
    const ids = (ORDER ? ORDER.filter((r) => args.revision_ids.includes(r)) : args.revision_ids).filter((r) => !EXCLUDE[r]);
    const token = "batch-token-" + (SERIAL++);
    MINTED[token] = ids;
    const pozycje = ids.map((id, i) => ({ lp: i + 1, revision_id: id, platforma: id === "REVX" ? "X" : "REDDIT", konto: "u", rodzaj: "REPLY", kiedy: iso(i * 16) }));
    const odrzucone = args.revision_ids.filter((r) => EXCLUDE[r]).map((r) => ({ revision_id: r, kod: EXCLUDE[r], powod: "Polish sentence for " + EXCLUDE[r] }));
    if (!ids.length) return answer(m.id, { structured: { stan: "NIEDOSTEPNE", kod: odrzucone[0] ? odrzucone[0].kod : "PACZKA_PUSTA", powod: "Polish refusal", odrzucone } });
    return answer(m.id, { structured: { stan: "WYKONANE", odrzucone,
      paczka: { ile: ids.length, teraz: 1, odstep_min: 15, zwloka_min: [1, 4], koniec: iso((ids.length - 1) * 16), limity: LIMITS, zestaw: "z".repeat(64), pozycje } },
      meta: { "wenext/potwierdzenie": token } });
  }
  if (name === "room.send_batch") {
    log.push(m); SENT.push(args);
    const ids = MINTED[args.token];
    if (!ids) return answer(m.id, { structured: { stan: "NIEDOSTEPNE", kod: "POTWIERDZENIE", powod: "no token" } });
    delete MINTED[args.token];
    if (SENTMODE === "changed") return answer(m.id, { structured: { stan: "NIEDOSTEPNE", kod: "PACZKA_ZMIENIONA", powod: "changed", odrzucone: [] } });
    return answer(m.id, { structured: { stan: "OCZEKUJE", powod: "ok", odmowy: [],
      zaplanowane: ids.map((id, i) => ({ revision_id: id, wniosek: "req-" + id, kiedy: iso(i * 16), platforma: id === "REVX" ? "X" : "REDDIT", konto: "u" })) } });
  }
  if (name === "room.send_status") {
    log.push(m);
    const id = args.request_id, k = (SEEN[id] = (SEEN[id] || 0) + 1);
    const fn = SCRIPT.status[id] || SCRIPT.status["*"];
    const w = fn ? fn(k, id) : { status: "QUEUED", widocznosc: null, opublikowano: null, adres: null, powod: null, powod_kod: null, najwczesniej: null };
    return answer(m.id, { structured: { stan: "WYKONANE", wniosek: Object.assign({ id: id, widocznosc: null, opublikowano: null, adres: null, powod: null, powod_kod: null, najwczesniej: null }, w) } });
  }
  return orig(m);
};
const asks = () => ASKS.map((a) => ({ ids: a.revision_ids, digests: a.odciski }));
const sends = () => SENT.slice();
const btn = () => $(".bar .sall");
const sheetOpen = () => $("#app").getAttribute("data-sheet");
const rows = () => $$(".sheet.batch .bl .br").map((r) => ({
  on: r.getAttribute("data-on"), checked: $("input", r).checked, disabled: $("input", r).disabled,
  badge: $(".badge", r).textContent, who: $(".bh b", r).textContent, where: $(".bp", r).textContent,
  line: $(".b1", r).textContent, time: $(".bk", r) ? $(".bk", r).textContent : null, rev: $("input", r).value,
}));
const sheetText = () => $(".sheet.batch").textContent.replace(/\s+/g, " ").trim();
const yes = () => $('.sheet.batch [data-ba="go"]');
const scheduled = () => $$(".sheet.batch .rows .rw").map((r) => ({
  badge: $(".badge", r).textContent, who: $(".t", r).textContent, sub: $(".s", r).textContent.replace(/\s+/g, " ").trim(),
  why: $(".w1", r) ? $(".w1", r).textContent : null, link: !!$("[data-ba=live]", r),
}));
const regionOf = (rev) => $('[data-box="' + rev + '"]');
const tick = (rev) => { const i = $('.sheet.batch input[value="' + rev + '"]'); i.checked = !i.checked; i.dispatchEvent(new win.Event("change", { bubbles: true })); };
"""


def _scope_ok(**over: Any) -> dict[str, Any]:
    from tests.test_card_v9_unit import _scope

    return _scope(**over)


def _run(
    tmp_path: Path,
    scenario: str,
    *,
    data: dict[str, Any] | None = None,
    exclude: dict[str, str] | None = None,
    sentmode: str = "ok",
    tools: dict | None = None,
) -> Any:
    """Run `scenario` in the card, against a fake host that PLANS from what it is asked."""
    prelude = (
        f"const EXCLUDE = {json.dumps(exclude or {})};\n"
        f"const SENTMODE = {json.dumps(sentmode)};\n"
        'const LIMITS = {"godzina": 4, "doba": 20, "wlasne_doba": 1};\n'
        "const ORDER = null;\n"
    )
    return _base_run(
        tmp_path,
        prelude + LIB_BATCH + scenario,
        data=data if data is not None else v9(),
        tools=tools,
    )


def _data(**over: Any) -> dict[str, Any]:
    data = v9()
    data.update(over)
    return data


@NEEDS_JSDOM
def test_the_button_counts_the_drafts_in_view_that_could_go(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        return { label: btn() ? btn().textContent.trim() : null, inBar: !!$(".bar .sall"), pagerFirst: $(".bar").firstElementChild.className,
          sheet: sheetOpen(), asks: asks().length };
        """,
    )
    assert got["label"] == "Send all (3)" and got["inBar"] is True and got["pagerFirst"] == "pager"
    assert got["sheet"] is None and got["asks"] == 0, "drawing the button asks nothing"


@NEEDS_JSDOM
def test_the_button_is_not_there_when_nothing_or_one_thing_could_go(tmp_path: Path) -> None:
    scenarios = {
        "paused": _data(zakres=_scope_ok(state="WSTRZYMANY")),
        "expired_scope": _data(zakres=_scope_ok(state="WYGASL")),
        "no_scope": _data(zakres=_scope_ok(state="BRAK")),
        "hosted": _data(wysylka="reczna"),
        "no_scope_block": {k: v for k, v in v9().items() if k != "zakres"},
        "no_accounts": _data(zakres=_scope_ok(konta=[])),
        "only_one_account": _data(
            zakres=_scope_ok(konta=[{"platforma": "X", "konto": "demo_founder_x"}])
        ),
        "no_reply_action": _data(zakres=_scope_ok(dzialania=["RESEARCH"])),
        "one_draft": _data(szkice=[v9()["szkice"][0]]),
        "expired_drafts": _data(szkice=[{**d, "wygasa": _iso(hours=-1)} for d in v9()["szkice"]]),
        "no_digest": _data(szkice=[{**d, "odcisk": None} for d in v9()["szkice"]]),
    }
    for name, data in scenarios.items():
        got = _run(
            tmp_path,
            "await open(); return { btn: !!$('.sall'), text: btn() ? btn().textContent : null };",
            data=data,
        )
        assert got == {"btn": False, "text": None}, name


@NEEDS_JSDOM
def test_an_account_outside_the_scope_or_an_action_it_lacks_takes_those_drafts_out_of_the_count(
    tmp_path: Path,
) -> None:
    only_reddit = _data(zakres=_scope_ok(konta=[{"platforma": "REDDIT", "konto": "demo_founder"}]))
    got = _run(tmp_path, "await open(); return btn().textContent.trim();", data=only_reddit)
    assert got == "Send all (2)", "the X draft is outside the scope"
    no_own = _data(zakres=_scope_ok(dzialania=["REPLY"]))
    got = _run(tmp_path, "await open(); return btn().textContent.trim();", data=no_own)
    assert got == "Send all (2)", "an own post needs OWN_POST in the scope"


@NEEDS_JSDOM
def test_a_draft_being_edited_or_sent_is_not_counted(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const before = btn().textContent.trim();
        $('[data-edit="0"]').click(); await sleep(10);
        const editing = btn() ? btn().textContent.trim() : null;
        return { before, editing };
        """,
        tools={
            "room.send_token": {
                "structured": {"stan": "WYKONANE", "zakres": {"rodzaj": "WYSLIJ"}},
                "meta": {"wenext/potwierdzenie": "t"},
            }
        },
    )
    assert got["before"] == "Send all (3)" and got["editing"] == "Send all (2)"


@NEEDS_JSDOM
def test_the_button_opens_a_panel_with_every_draft_ticked_the_schedule_and_the_limits(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        return { sheet: sheetOpen(), rows: rows(), text: sheetText(), yes: yes() ? yes().textContent.trim() : null, yesDisabled: yes() ? yes().disabled : null,
          asks: asks(), pagesHidden: win.getComputedStyle($(".stage")).display, sends: sends() };
        """,
    )
    assert got["sheet"] == "batch" and got["pagesHidden"] == "none", (
        "the panel takes the place of the pages"
    )
    assert got["asks"] == [{"ids": [REV_A, REV_B, REV_C], "digests": ["a" * 64] * 3}], (
        "one question, for every draft in view, with the digests the card holds"
    )
    assert len(got["rows"]) == 3 and all(
        r["checked"] and not r["disabled"] and r["on"] == "1" for r in got["rows"]
    ), "all ticked"
    by_rev = {r["rev"]: r for r in got["rows"]}
    a, b, c = by_rev[REV_A], by_rev[REV_B], by_rev[REV_C]
    assert (a["badge"], a["who"], a["where"]) == ("R", "u/demo_kestrel", "r/SideProject"), (
        "platform and who it answers"
    )
    assert a["line"].startswith("First users rarely come from one place"), (
        "the first line of the reply"
    )
    assert (b["badge"], b["who"], b["where"]) == ("X", "@demo_kestrel", "X")
    assert b["line"] == "Tools alone did not stick for me either."
    assert (c["badge"], c["who"]) == ("R", "Own post")
    assert all(r["time"] for r in got["rows"]), "each ticked row shows its moment"
    text = got["text"]
    assert "1 now, then one every 15 min or so, done by" in text, "the schedule the server will use"
    assert "Limits: 4 an hour, 20 a day." in text and "random delay" in text, (
        "the limits are stated"
    )
    assert "Nothing goes out without this click" in text and "check the thread again" in text
    assert got["yes"] == "Yes, send these 3" and got["yesDisabled"] is False
    assert got["sends"] == [], "opening the panel sends nothing"


@NEEDS_JSDOM
def test_unticking_asks_again_for_the_smaller_set_and_the_button_follows(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        f"""
        await open();
        btn().click(); await sleep(60);
        const before = {{ yes: yes().textContent.trim(), line: sheetText().match(/\\d+ now[^.]*\\./)[0] }};
        tick("{REV_B}");
        const checking = {{ yes: yes() ? yes().disabled : null, text: sheetText().includes("Planning the schedule") }};
        await sleep(60);
        return {{ before, checking, after: {{ yes: yes().textContent.trim(), rows: rows().map((r) => [r.rev, r.checked, r.on]) }}, asks: asks(),
          sends: sends() }};
        """,
    )
    assert got["before"]["yes"] == "Yes, send these 3"
    assert got["checking"] == {"yes": True, "text": True}, "while it asks, Yes cannot be pressed"
    assert got["after"]["yes"] == "Yes, send these 2"
    assert [r[1] for r in got["after"]["rows"]] == [True, False, True] and [
        r[2] for r in got["after"]["rows"]
    ] == ["1", "0", "1"]
    assert got["asks"][1] == {"ids": [REV_A, REV_C], "digests": ["a" * 64] * 2}
    assert got["sends"] == []


@NEEDS_JSDOM
def test_confirming_sends_once_with_the_token_and_shows_the_scheduled_list(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        yes().click(); await sleep(60);
        const out = { sends: sends(), sheet: sheetOpen(), list: scheduled(), text: $(".sheet.batch .sh-msg").textContent.replace(/\\s+/g, " ").trim(),
          done: !!$('.sheet.batch [data-ba="done"]'), yes: !!yes(), pages: $$("[data-panel]").length };
        return out;
        """,
    )
    assert got["sends"] == [{"token": "batch-token-0"}], (
        "the act carries the token and nothing else"
    )
    assert got["sheet"] == "batch" and got["yes"] is False and got["done"] is True
    assert (
        "Scheduled 3." in got["text"]
        and "I check each thread again right before it goes out" in got["text"]
    )
    assert "exactly as you approved it, or not at all" in got["text"]
    assert len(got["list"]) == 3
    first = got["list"][0]
    assert (
        first["badge"] in ("R", "X")
        and first["sub"].startswith("queued")
        and " at " in first["sub"]
    ), "time and live status"
    assert got["pages"] == 5


@NEEDS_JSDOM
def test_the_scheduled_list_follows_the_publisher_and_the_pages_say_where_each_draft_stands(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        f"""
        SCRIPT.status["req-{REV_A}"] = (k) => k < 2 ? {{ status: "QUEUED" }} : {{ status: "PUBLISHED", adres: "https://www.reddit.com/r/SideProject/comments/zz1/x/yy9/", widocznosc: "CONFIRMED" }};
        SCRIPT.status["req-{REV_B}"] = () => ({{ status: "SKIPPED", powod: "wątek jest już za stary na odpowiedź: ma 9 h", powod_kod: "ZA_STARY" }});
        SCRIPT.status["req-{REV_C}"] = () => ({{ status: "QUEUED", powod: "brak sesji Reddita konta u: wniosek czeka na kolejną próbę", powod_kod: "SESJA", najwczesniej: iso(48) }});
        await open();
        btn().click(); await sleep(60);
        yes().click(); await sleep(60);
        await sleep(900);
        const panel = scheduled();
        $('.sheet.batch [data-ba="done"]').click(); await sleep(30);
        const out = {{ panel, sheet: sheetOpen(), toggle: $('[data-tog="scheduled"]') ? $('[data-tog="scheduled"]').textContent.trim() : null,
          button: !!btn(), pageA: regionOf("{REV_A}").textContent.replace(/\\s+/g, " ").trim(), phaseA: regionOf("{REV_A}").getAttribute("data-phase") }};
        $('[data-tog="scheduled"]').click(); await sleep(20);
        out.drawer = $$('.dr[data-dr="scheduled"] .rw').map((r) => [$(".t", r).textContent, $(".s", r).textContent.replace(/\\s+/g, " ").trim(), $(".w1", r) ? $(".w1", r).textContent : null]);
        out.sendButtons = $$('[data-act="send"]').length;
        // the page of the skipped draft
        $('[data-nav="1"]').click(); await sleep(20);
        out.pageB = regionOf("{REV_B}").textContent.replace(/\\s+/g, " ").trim();
        $('[data-nav="1"]').click(); await sleep(20);
        out.pageC = regionOf("{REV_C}").textContent.replace(/\\s+/g, " ").trim();
        return out;
        """,
    )
    by_who = {row["who"].split(" · ")[0]: row for row in got["panel"]}
    assert len(got["panel"]) == 3
    posted = by_who["u/demo_kestrel"]
    assert posted["sub"] == "posted" and posted["link"] is True
    skipped = by_who["@demo_kestrel"]
    assert skipped["sub"] == "skipped"
    assert (
        skipped["why"]
        == "The thread went stale, so this reply was skipped. The text did not change."
    ), "English shows the card's own sentence for the code"
    waiting = by_who["Own post"]
    assert waiting["sub"].startswith("waiting for the next try") and "next try" in waiting["sub"]
    assert "agent's browser has no session for this account" in waiting["why"]
    # After Done: the card draws again, with the list as a disclosure and no more Send all.
    assert got["sheet"] is None and got["toggle"] == "Scheduled 3" and got["button"] is False
    assert got["sendButtons"] == 0, "a scheduled draft offers no Send"
    assert (got["phaseA"] == "queued" and got["pageA"].startswith("Posted")) or (
        "Posted" in got["pageA"]
    )
    assert len(got["drawer"]) == 3
    assert (
        "Failed" not in got["pageB"]
        and "It didn't go out." in got["pageB"]
        and "went stale" in got["pageB"]
    )
    assert "Scheduled for" in got["pageC"] and "Waiting for the next try." in got["pageC"]


@NEEDS_JSDOM
def test_the_polish_card_says_it_in_polish_with_the_servers_own_sentences(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const label = btn().textContent.trim();
        btn().click(); await sleep(60);
        const text = sheetText(), yesLabel = yes().textContent.trim();
        tick("__B__"); await sleep(70);
        return { label, text, yesLabel, two: yes().textContent.trim() };
        """.replace("__B__", REV_B),
        data=_data(jezyk="pl"),
    )
    assert got["label"] == "Wyślij wszystkie (3)"
    assert got["yesLabel"] == "Tak, wyślij te 3" and got["two"] == "Tak, wyślij te 2"
    assert "teraz, potem po jednej co ok. 15 min, koniec o" in got["text"]
    assert "Limity: 4 na godzinę, 20 na dobę." in got["text"]
    assert "Nic nie wyjdzie bez tego kliknięcia" in got["text"]


@NEEDS_JSDOM
def test_a_draft_the_server_leaves_out_is_shown_with_its_reason_and_cannot_be_ticked(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        f"""
        await open();
        btn().click(); await sleep(60);
        const r = rows();
        const out = {{ rows: r, yes: yes().textContent.trim(), asks: asks().length }};
        tick("{REV_B}"); await sleep(40);
        out.asksAfter = asks().length;
        out.stillOff = rows().find((x) => x.rev === "{REV_B}");
        out.sub = $$(".sheet.batch .bx").map((e) => e.textContent);
        return out;
        """,
        exclude={REV_B: "POZA_ZAKRESEM"},
    )
    off = next(r for r in got["rows"] if r["rev"] == REV_B)
    assert (
        off["on"] == "x"
        and off["checked"] is False
        and off["disabled"] is True
        and off["time"] is None
    )
    assert got["yes"] == "Yes, send these 2"
    assert got["sub"] == [
        "This draft's account is not in the signed scope. Send this draft on its own or set Limits: one click in a batch signs nothing."
    ], "English: the card's own sentence for the code"
    assert got["asksAfter"] == 1, "a disabled box asks nothing"


@NEEDS_JSDOM
def test_when_the_server_refuses_the_whole_batch_the_card_says_why_and_offers_the_fix(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        return { text: sheetText(), buttons: $$(".sheet.batch button").map((b) => [b.getAttribute("data-ba"), b.getAttribute("data-scope"), b.textContent.trim()]),
          rows: rows().map((r) => r.on), sends: sends() };
        """,
        exclude={REV_A: "POZA_ZAKRESEM", REV_B: "POZA_ZAKRESEM", REV_C: "POZA_ZAKRESEM"},
    )
    assert "not in the signed scope" in got["text"] or "signed scope" in got["text"]
    assert ["review", "Change scope"] in [[b[1], b[2]] for b in got["buttons"]], (
        "the Limits panel is the way to widen"
    )
    assert got["rows"] == ["x", "x", "x"] and got["sends"] == []


@NEEDS_JSDOM
def test_a_batch_that_changed_at_the_click_is_asked_again_and_waits_for_a_second_click(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        yes().click(); await sleep(80);
        return { asks: asks().length, sends: sends(), sheet: sheetOpen(), yes: yes() ? yes().textContent.trim() : null,
          note: $(".sheet.batch .bchg") ? $(".sheet.batch .bchg").textContent : null, phase: $(".sheet.batch").getAttribute("data-p") };
        """,
        sentmode="changed",
    )
    assert got["asks"] == 2 and len(got["sends"]) == 1, "asked again once, did not send by itself"
    assert got["phase"] == "confirm" and got["yes"] == "Yes, send these 3"
    assert got["note"] == "Something changed, so I sent nothing. This is the new plan."


@NEEDS_JSDOM
def test_cancel_and_escape_close_the_panel_and_send_nothing_and_the_arrows_do_not_page_under_it(
    tmp_path: Path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        key("ArrowRight");
        const underPanel = idx();
        key("Escape"); await sleep(30);
        const afterEscape = { sheet: sheetOpen(), btn: !!btn(), sends: sends().length };
        btn().click(); await sleep(60);
        $('.sheet.batch [data-ba="cancel"]').click(); await sleep(30);
        key("ArrowRight");
        return { underPanel, afterEscape, afterCancel: { sheet: sheetOpen(), btn: !!btn(), sends: sends().length }, paged: idx(), asks: asks().length };
        """,
    )
    assert got["underPanel"] == ["0"], "the arrows do not page while the panel is open"
    assert got["afterEscape"] == {"sheet": None, "btn": True, "sends": 0}
    assert got["afterCancel"] == {"sheet": None, "btn": True, "sends": 0}
    assert got["paged"] == ["1"], "and they page again once it is closed"


@NEEDS_JSDOM
def test_a_key_never_sends_only_a_click_on_yes_does(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        const focus = doc().activeElement && doc().activeElement.tagName;
        for (const k of ["Enter", " ", "y", "Tab"]) key(k);
        const box = $('.sheet.batch input'); box.focus();
        for (const k of ["Enter", " "]) box.dispatchEvent(new win.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
        await sleep(40);
        return { sends: sends().length, focus: focus, onYes: doc().activeElement === yes() };
        """,
    )
    assert got["sends"] == 0 and got["onYes"] is False


@NEEDS_JSDOM
def test_the_card_does_not_leak_the_confirmation_into_its_markup(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        btn().click(); await sleep(60);
        const html = doc().getElementById("app").innerHTML;
        return { leaked: html.includes("batch-token-"), data: $$("[data-token],[data-confirm]").length };
        """,
    )
    assert got == {"leaked": False, "data": 0}


@NEEDS_JSDOM
def test_a_card_from_a_server_that_knows_nothing_about_batches_draws_as_before(
    tmp_path: Path,
) -> None:
    old = copy.deepcopy(v9())
    old.pop("zakres")
    got = _run(
        tmp_path,
        "await open(); return { pages: $$('[data-panel]').length, btn: !!$('.sall'), sheet: !!$('.sheet.batch'), foot: !!$('.foot') };",
        data=old,
    )
    assert got == {"pages": 5, "btn": False, "sheet": True, "foot": False}


def test_the_draft_helper_still_describes_the_fixture_the_tests_rely_on() -> None:
    drafts = v9()["szkice"]
    assert [d["revision_id"] for d in drafts] == [REV_A, REV_B, REV_C]
    assert _draft(REV_A, None)["stan"] == "czeka_na_zgode"
