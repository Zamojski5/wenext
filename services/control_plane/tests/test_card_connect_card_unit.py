"""The card connects an account, and ONLY THE PERSON'S PRESS opens the window (v10).

THE FOUNDER (12.10): his X session was lost overnight and the only fix was a dashboard card: "I hate the
dashboard. I don't know what to click." The footer of the card now carries a dot for each account and,
where a click helps, "Reconnect @account". What is held here, from the outside in:

  * THE MACHINE (`makeConnector`, cut out of the script by its CONNECT markers) is PURE and is run in node
    with a fake host and a fake clock, so the card's logic is what is executed and not a copy of it;
  * THE INVARIANT: `room.connect_account` is called only from the press on "Reconnect" (and its repeats,
    "Open the window again" and "Try again"), once per confirmation the server minted, with a token nobody
    used; `room.connect_done` only from "I'm signed in" and "Cancel"; the poll calls `room.account_status`
    and nothing else. Held by a table of scenarios and by a seeded random walk over every action the card
    has, against a host that answers arbitrarily; and in the source: one string literal for each name,
    inside the one function that may call it;
  * the poll asks every 4 seconds for up to 3 minutes, never reads the platform (it cannot: the person's
    browser has no DevTools endpoint while they are at the window) and turns the dot green by itself when
    the server says the account is connected; after the three minutes it rests and keeps its buttons;
  * the address of the window is read from `_meta` and is never in the text of the card;
  * the card, in jsdom with the fake host of `test_card_pager_unit.py`: the footer shows the dots, the
    button appears only where a click helps, the press asks and acts and opens the link the server gave,
    the panel lives in the footer, the hosted card shows none of it, and a card from a server that knows
    nothing about v10 still draws;
  * the new words are in both languages with the same placeholders (`test_card_language_unit` holds the
    rest).

NOT HELD HERE: how it looks (the PNGs the task report names), and that a Codex host opens the link and
proxies the card's calls the way the fake host does (unverified live).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from control_plane.tools import apps_ui, card_connect
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_pager_unit import JSDOM, fixture
from tests.test_card_pager_unit import _run as _run_card

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nie jest zainstalowany")

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
NEEDS_JSDOM = pytest.mark.skipif(not JSDOM.is_dir(), reason="potrzebny jsdom (apps/dashboard)")
HREF = "http://127.0.0.1:8761/login-capsule#ticket=TICKET-SECRET&plg=1&ilg=1"


def _script() -> str:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    return html[html.index("<script>") : html.index("</script>")]


def _connector_source() -> str:
    script = _script()
    start = script.index("/*CONNECT-BEGIN*/") + len("/*CONNECT-BEGIN*/")
    return script[start : script.index("/*CONNECT-END*/")]


def _code(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def test_each_connect_tool_is_named_in_one_place_inside_the_one_function_that_may_call_it() -> None:
    script = _code(_script())
    machine = _code(_connector_source())
    for name in (
        "room.connect_token",
        "room.connect_account",
        "room.connect_done",
        "room.account_status",
    ):
        assert script.count(f'"{name}"') == 1, name
    begin = machine[machine.index("function begin(") : machine.index("function finish(")]
    finish = machine[machine.index("function finish(") : machine.index("function resume(")]
    poll = machine[machine.index("function poll(") : machine.index("function begin(")]
    assert '"room.connect_token"' in begin and '"room.connect_account"' in begin
    assert '"room.connect_done"' in finish
    assert '"room.account_status"' in poll
    # `begin` is reached only by the three presses that mean "open it", `finish` only by two others.
    dispatch = machine[machine.index("function dispatch(") :]
    assert 'name === "connect" || name === "again" || name === "retry") begin(' in dispatch
    assert "begin(" not in dispatch.replace('retry") begin(', "")
    assert dispatch.count("finish(") == 2 and 'name === "done") finish(p, false)' in dispatch
    # And `dispatch` is reached from `connectAction`, which only the document's click handler calls.
    assert len(re.findall(r"connectAction\(", script)) == 2
    handler = script[script.index('document.addEventListener("click"') :]
    assert 'connectAction(b.getAttribute("data-conn")' in handler


def test_the_connect_machine_is_pure_and_talks_to_the_world_only_through_env() -> None:
    code = _code(_connector_source())
    for forbidden in ("document", "window", "navigator", "setTimeout", "setInterval", "fetch(", "rpc("):
        assert forbidden not in code, forbidden
    assert "function makeConnector(env)" in code
    for member in ("env.call", "env.later", "env.paint", "env.T", "env.reason", "env.open", "env.apply"):
        assert member in code, member


def test_the_poll_is_every_four_seconds_for_three_minutes_and_the_keys_match_the_server() -> None:
    code = _code(_connector_source())
    assert "var STEP = 4000, TRIES = 45;" in code and 4000 * 45 == 180_000
    # The two keys of `_meta` are the server's, spelled the same.
    assert f'META_TOKEN = "{META_TOKEN}"' in code
    assert f'META_URL = "{card_connect.META_LOGIN_URL}"' in code
    # The address is never read from text: `metaOf` reads `_meta` and nothing else.
    meta = code[code.index("function metaOf(") : code.index("function of(")]
    assert "_meta" in meta and "structuredContent" not in meta and "content[0]" not in meta


def test_the_new_words_have_the_same_placeholders_in_both_languages() -> None:
    keys = {k for k in PL if k.startswith("ac.")}
    assert keys == {k for k in EN if k.startswith("ac.")} and len(keys) >= 20
    for key in keys:
        assert set(PLACEHOLDER.findall(str(PL[key]))) == set(PLACEHOLDER.findall(str(EN[key]))), key
        assert not LONG_DASHES.search(str(PL[key]) + str(EN[key])), key
        assert not re.search("[ąćęłńóśźż]", str(EN[key])), key
    assert PL["ac.reconnect"] == "Połącz ponownie {who}" and EN["ac.reconnect"] == "Reconnect {who}"
    for code in (card_connect.CODE_SIGN_IN, card_connect.CODE_OTHER_ACCOUNT, card_connect.CODE_UNREADABLE):
        assert f"ref.{code}" in PL and f"ref.{code}" in EN


# ---------------------------------------------------------------------------
# The machine, run in node: a fake host and a fake clock
# ---------------------------------------------------------------------------

HARNESS = r"""
var clock = { now: 0, timers: [] };
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
var TOKEN_KEY = "wenext/potwierdzenie", URL_KEY = "wenext/adres_logowania";
var HREF = "http://127.0.0.1:8761/login-capsule#ticket=T";
function res(structured, meta) { var r = { structuredContent: structured }; if (meta) r._meta = meta; return { result: r }; }
function tokenOk(t) { var m = {}; m[TOKEN_KEY] = t; return res({ stan: "WYKONANE", platforma: "X", konto: "M" }, m); }
function opened(href) { var m = {}; if (href) m[URL_KEY] = href; return res({ stan: "WYKONANE", ekran: "otwarty" }, m); }
function refusal(kod) { return res({ stan: "NIEDOSTEPNE", kod: kod, powod: "powod " + kod }); }
function status(stan, ekran) { return res({ stan: "WYKONANE", konta: [{ platforma: "X", konto: "M", stan: stan, ekran: ekran || "otwarty" }] }); }
function doneRes(ok, kod) { return res({ stan: "WYKONANE", polaczone: ok, konto: "M", kod_odczytu: kod || (ok ? "POLACZONE" : "ZALOGUJ"), detail: "d" }); }

// A host that answers from a script: for each tool a function of (the call number of that tool, args, world).
function makeWorld(answers) {
  var w = { calls: [], opened: [], applied: [], phases: [], minted: {}, spent: {}, doubleUse: 0, unknownToken: 0, pressed: 0, inBegin: false };
  w.count = function (name) { return w.calls.filter(function (c) { return c.name === name; }).length; };
  w.env = {
    call: function (name, args) {
      var n = w.count(name);
      w.calls.push({ name: name, args: args, at: clock.now, phase: w.c ? w.c.of("X").phase : null });
      if (name === "room.connect_account") {
        if (!w.minted[args.token]) w.unknownToken++;
        if (w.spent[args.token]) w.doubleUse++;
        w.spent[args.token] = true;
      }
      return Promise.resolve(answers[name](n, args, w)).then(function (answer) {
        if (name === "room.connect_token" && answer && answer.result && answer.result._meta) {
          w.minted[answer.result._meta[TOKEN_KEY]] = true;   // the server minted it
        }
        return answer;
      });
    },
    later: later,
    paint: function (p) { w.phases.push(w.c.of(p).phase); },
    T: function (k) { return k; },
    reason: function (data, why) { return (data && data.kod) || why || "?"; },
    open: function (url) { w.opened.push(url); },
    apply: function (p, h) { w.applied.push([p, h]); }
  };
  w.c = makeConnector(w.env);
  w.click = function (name) { if (name === "connect" || name === "again" || name === "retry") w.pressed++; w.c.dispatch(name, "X", "M"); };
  w.st = function () { return w.c.of("X"); };
  w.names = function () { return w.calls.map(function (c) { return c.name; }); };
  return w;
}
function snap(w) {
  var st = w.st();
  return { phase: st.phase, calls: w.names(), href: st.href || null, reason: st.reason || null, code: st.code || null, at: clock.now };
}
"""

SCENARIOS = r"""
var out = {};

// 1. THE HAPPY PATH: one press asks and acts, the window is opened with the address from `_meta`, the poll asks
//    every 4 s, and when the server says the account is connected the dot turns green by itself.
async function happy() {
  var w = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function (n) { return n < 2 ? status("czeka_na_ciebie") : status("polaczone", "zamkniety"); },
    "room.connect_done": function () { return doneRes(true); }
  });
  var seen = [];
  w.click("connect"); await tick(0); seen.push(snap(w));
  await tick(3999); seen.push(snap(w));       // not yet the first poll
  await tick(1); seen.push(snap(w));          // 4 s: first poll, window open: still waiting
  await tick(4000); seen.push(snap(w));       // 8 s: second poll, still waiting
  await tick(4000); seen.push(snap(w));       // 12 s: connected
  var applied = w.applied.slice();
  await tick(6000); seen.push(snap(w));       // the confirmation rests, then the footer is quiet
  var calls = w.names().length;
  await tick(600000);
  return { seen: seen, opened: w.opened, applied: applied, callsAfterwards: w.names().length - calls,
    names: w.names(), polledAt: w.calls.filter(function (c) { return c.name === "room.account_status"; }).map(function (c) { return c.at; }) };
}

// 2. Three minutes of polling, then it rests: no more calls, and "I'm signed in" still works from `slow`.
async function slowThenDone() {
  var w = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); },
    "room.connect_done": function () { return doneRes(true); }
  });
  w.click("connect"); await tick(0);
  await tick(179000); var before = snap(w);
  await tick(1000); var atThree = snap(w);
  var polls = w.count("room.account_status");
  await tick(1200000); var later = snap(w);
  var pollsLater = w.count("room.account_status");
  w.click("done"); await tick(0);
  return { before: before, atThree: atThree, polls: polls, pollsLater: pollsLater, later: later, finished: snap(w),
    doneCalls: w.count("room.connect_done"), applied: w.applied };
}

// 3. "I'm signed in": one `room.connect_done`, however many presses; connected turns the dot green.
async function doneOnce() {
  var w = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); },
    "room.connect_done": function () { return doneRes(true); }
  });
  w.click("connect"); await tick(0);
  w.click("done"); w.click("done"); w.click("done"); await tick(0);
  return { s: snap(w), done: w.count("room.connect_done"), applied: w.applied, connects: w.count("room.connect_account") };
}

// 4. Not signed in yet: the sentence is the server's code, and "Try again" opens a NEW window with a NEW confirmation.
async function notYet() {
  var w = makeWorld({
    "room.connect_token": function (n) { return tokenOk("t-" + n); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); },
    "room.connect_done": function (n) { return n === 0 ? doneRes(false, "ZALOGUJ") : doneRes(true); }
  });
  var seen = [];
  w.click("connect"); await tick(0);
  w.click("done"); await tick(0); seen.push(snap(w));
  var asked = w.count("room.connect_token");
  w.click("retry"); await tick(0); seen.push(snap(w));
  return { seen: seen, askedBefore: asked, asked: w.count("room.connect_token"), opens: w.count("room.connect_account"),
    minted: Object.keys(w.minted).length, doubleUse: w.doubleUse, opened: w.opened.length };
}

// 5. Cancel while waiting closes the window quietly: nothing is said when the sign-in is not there; and when
//    it is, the dot turns green.
async function cancels() {
  var a = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); },
    "room.connect_done": function () { return doneRes(false, "ZALOGUJ"); }
  });
  a.click("connect"); await tick(0); a.click("cancel"); await tick(0);
  var quiet = { s: snap(a), done: a.count("room.connect_done"), applied: a.applied.length };
  var b = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); },
    "room.connect_done": function () { return doneRes(true); }
  });
  b.click("connect"); await tick(0); b.click("cancel"); await tick(0);
  return { quiet: quiet, signedIn: { s: snap(b), applied: b.applied } };
}

// 6. Every way the chain can fail ends in a refusal with a way out, and the window is never opened by a
//    refused or a silent question.
async function refusals() {
  var o = {};
  var cases = {
    tokenRefused: { "room.connect_token": function () { return refusal("KAPSULA"); } },
    tokenSilent: { "room.connect_token": function () { return { error: "silent" }; } },
    tokenRejected: { "room.connect_token": function () { return { error: "rejected" }; } },
    noTokenInMeta: { "room.connect_token": function () { return res({ stan: "WYKONANE" }); } },
    tokenInTextOnly: { "room.connect_token": function () { return { result: { content: [{ type: "text", text: JSON.stringify({ stan: "WYKONANE", potwierdzenie: "leaked" }) }] } }; } },
    accountRefused: { "room.connect_token": function () { return tokenOk("t"); }, "room.connect_account": function () { return refusal("OPERATOR"); } },
    accountSilent: { "room.connect_token": function () { return tokenOk("t"); }, "room.connect_account": function () { return { error: "silent" }; } },
    noAddress: { "room.connect_token": function () { return tokenOk("t"); }, "room.connect_account": function () { return opened(null); } },
    addressInTextOnly: { "room.connect_token": function () { return tokenOk("t"); },
      "room.connect_account": function () { return { result: { structuredContent: { stan: "WYKONANE", adres: HREF } } }; } }
  };
  for (var name in cases) {
    var w = makeWorld(Object.assign({
      "room.connect_account": function () { return opened(HREF); },
      "room.account_status": function () { return status("czeka_na_ciebie"); },
      "room.connect_done": function () { return doneRes(true); }
    }, cases[name]));
    w.click("connect"); await tick(0); await tick(600000);
    o[name] = { s: snap(w), opened: w.opened.length, accounts: w.count("room.connect_account"), polls: w.count("room.account_status") };
    w.click("close"); await tick(0);
    o[name].closed = w.st().phase;
  }
  return o;
}

// 7. A stale answer (after Cancel, Close or a new press) is dropped and does nothing.
async function staleAnswers() {
  var pending = null;
  var w = makeWorld({
    "room.connect_token": function () { return new Promise(function (r) { pending = r; }); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); }
  });
  w.click("connect"); await tick(0);
  var asking = snap(w);
  w.click("cancel"); await tick(0);               // from `asking` it just goes idle
  pending(tokenOk("t-stale")); await tick(0); await tick(600000);
  return { asking: asking, after: snap(w), opens: w.count("room.connect_account"), opened: w.opened.length };
}

// 8. A double press on "Reconnect" opens one window.
async function doublePress() {
  var w = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("czeka_na_ciebie"); }
  });
  w.click("connect"); w.click("connect"); w.click("connect"); await tick(0);
  return { opens: w.count("room.connect_account"), asked: w.count("room.connect_token"), opened: w.opened.length };
}

// 9. The card was drawn again while the window was open: "Finish signing in" waits for it, opens nothing.
async function resumeWaiting() {
  var w = makeWorld({
    "room.account_status": function (n) { return n < 1 ? status("czeka_na_ciebie") : status("polaczone", "zamkniety"); },
    "room.connect_done": function () { return doneRes(true); }
  });
  w.c.dispatch("resume", "X", "M"); await tick(0);
  var s1 = snap(w);
  await tick(4000); await tick(4000);
  return { s1: s1, s2: snap(w), names: w.names(), applied: w.applied };
}

// 10. The window was closed by somebody else and the account is connected: green without any press.
async function greenByItself() {
  var w = makeWorld({
    "room.connect_token": function () { return tokenOk("t-1"); },
    "room.connect_account": function () { return opened(HREF); },
    "room.account_status": function () { return status("polaczone", "zamkniety"); }
  });
  w.click("connect"); await tick(0); await tick(4000);
  return { s: snap(w), applied: w.applied, done: w.count("room.connect_done") };
}

// 11. THE RANDOM WALK: every action the card has, against a host that answers arbitrarily.
function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
async function walk(seed) {
  var r = rng(seed), pick = function (list) { return list[Math.floor(r() * list.length)]; };
  var minted = 0;
  var w = makeWorld({
    "room.connect_token": function () {
      var k = pick(["ok", "ok", "ok", "refused", "error", "notoken"]);
      if (k === "ok") return tokenOk("tok-" + (minted++));
      if (k === "refused") return refusal(pick(["KAPSULA", "BRAK_KONTA", "OPERATOR", "POTWIERDZENIE"]));
      if (k === "error") return { error: pick(["silent", "rejected"]) };
      return res({ stan: "WYKONANE" });
    },
    "room.connect_account": function () {
      var k = pick(["ok", "ok", "ok", "refused", "error", "noaddress"]);
      if (k === "ok") return opened(HREF);
      if (k === "refused") return refusal(pick(["KAPSULA", "POTWIERDZENIE"]));
      if (k === "error") return { error: "silent" };
      return opened(null);
    },
    "room.account_status": function () {
      var k = pick(["wait", "wait", "wait", "connected", "lost", "error"]);
      if (k === "wait") return status("czeka_na_ciebie");
      if (k === "connected") return status("polaczone", "zamkniety");
      if (k === "lost") return status("utracona", "zamkniety");
      return { error: "silent" };
    },
    "room.connect_done": function () {
      var k = pick(["ok", "no", "other", "error", "refused"]);
      if (k === "ok") return doneRes(true);
      if (k === "no") return doneRes(false, "ZALOGUJ");
      if (k === "other") return doneRes(false, "INNE_KONTO");
      if (k === "refused") return refusal("KAPSULA");
      return { error: "silent" };
    }
  });
  var actions = ["connect", "again", "retry", "done", "cancel", "close", "resume", "t1", "t4", "t60", "t200"];
  var dones = 0;
  for (var step = 0; step < 70; step++) {
    var a = pick(actions);
    if (a === "t1") await tick(1000);
    else if (a === "t4") await tick(4000);
    else if (a === "t60") await tick(60000);
    else if (a === "t200") await tick(200000);
    else { if (a === "done" || a === "cancel") dones++; w.click(a); await tick(0); }
  }
  await tick(1000000);
  var polledOutsideWaiting = w.calls.filter(function (c) { return c.name === "room.account_status" && c.phase !== "waiting"; }).length;
  return { accounts: w.count("room.connect_account"), tokens: w.count("room.connect_token"), pressed: w.pressed,
    done: w.count("room.connect_done"), dones: dones, doubleUse: w.doubleUse, unknownToken: w.unknownToken,
    opened: w.opened.length, polledOutsideWaiting: polledOutsideWaiting, phase: w.st().phase };
}

(async function () {
  out.happy = await happy();
  out.slowThenDone = await slowThenDone();
  out.doneOnce = await doneOnce();
  out.notYet = await notYet();
  out.cancels = await cancels();
  out.refusals = await refusals();
  out.staleAnswers = await staleAnswers();
  out.doublePress = await doublePress();
  out.resumeWaiting = await resumeWaiting();
  out.greenByItself = await greenByItself();
  var walks = [];
  for (var seed = 1; seed <= 300; seed++) walks.push(await walk(seed));
  out.walks = walks;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> dict:
    program = tmp_path_factory.mktemp("card-connect") / "connect.js"
    program.write_text(_connector_source() + HARNESS + SCENARIOS, encoding="utf-8")
    done = subprocess.run(
        ["node", str(program)], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_one_press_asks_and_acts_opens_the_window_and_the_poll_turns_it_green(observed) -> None:
    got = observed["happy"]
    first, before_poll, polled, second, green, quiet = got["seen"]
    # One press: the confirmation, then the act with it, in this order, and the address from `_meta` opened.
    assert got["names"][:2] == ["room.connect_token", "room.connect_account"]
    assert got["opened"] == ["http://127.0.0.1:8761/login-capsule#ticket=T"]
    assert first["phase"] == "waiting" and first["href"] == got["opened"][0]
    assert before_poll["calls"] == first["calls"], "przed czterema sekundami nie ma pytania o stan"
    assert got["polledAt"] == [4000, 8000, 12000], "co cztery sekundy"
    assert polled["phase"] == "waiting" and second["phase"] == "waiting"
    assert green["phase"] == "connected" and got["applied"] == [["X", "M"]]
    assert quiet["phase"] == "idle", "potwierdzenie znika po chwili, stopka wraca do spokoju"
    assert got["callsAfterwards"] == 0, "po połączeniu nie ma już żadnych pytań"
    # While the window is open the poll asks the status and NOTHING else: the platform is not read.
    assert set(got["names"]) == {"room.connect_token", "room.connect_account", "room.account_status"}


def test_the_poll_rests_after_three_minutes_and_keeps_its_buttons(observed) -> None:
    got = observed["slowThenDone"]
    assert got["before"]["phase"] == "waiting" and got["atThree"]["phase"] == "slow"
    assert got["polls"] == 45 and got["pollsLater"] == 45, "45 pytań, potem cisza"
    assert got["later"]["phase"] == "slow"
    assert got["finished"]["phase"] == "connected" and got["doneCalls"] == 1
    assert got["applied"] == [["X", "M"]]


def test_i_am_signed_in_calls_done_once_however_many_presses(observed) -> None:
    got = observed["doneOnce"]
    assert got["done"] == 1 and got["connects"] == 1
    assert got["s"]["phase"] == "connected" and got["applied"] == [["X", "M"]]


def test_not_signed_in_shows_the_code_and_try_again_opens_a_new_window_with_a_new_confirmation(
    observed,
) -> None:
    got = observed["notYet"]
    notyet, again = got["seen"]
    assert notyet["phase"] == "notyet" and notyet["code"] == "ZALOGUJ" and notyet["reason"] == "ZALOGUJ"
    assert got["askedBefore"] == 1 and got["asked"] == 2 and got["opens"] == 2
    assert got["minted"] == 2 and got["doubleUse"] == 0, "każde okno ma własne, świeże potwierdzenie"
    assert again["phase"] == "waiting" and got["opened"] == 2


def test_cancel_closes_the_window_quietly_and_a_sign_in_that_is_there_still_counts(observed) -> None:
    got = observed["cancels"]
    assert got["quiet"]["s"]["phase"] == "idle" and got["quiet"]["done"] == 1
    assert got["quiet"]["s"]["reason"] is None, "Anuluj nie mówi, że nie widać logowania"
    assert got["quiet"]["applied"] == 0
    assert got["signedIn"]["s"]["phase"] == "connected" and got["signedIn"]["applied"] == [["X", "M"]]


def test_every_failure_of_the_chain_ends_in_a_refusal_with_a_way_out(observed) -> None:
    got = observed["refusals"]
    for name, case in got.items():
        assert case["s"]["phase"] == "refused", name
        assert case["opened"] == 0 and case["polls"] == 0, name
        assert case["closed"] == "idle", name
    assert got["tokenRefused"]["s"]["reason"] == "KAPSULA", "zdanie serwera, po kodzie"
    assert got["tokenRefused"]["accounts"] == 0, "odmowa pytania nie otwiera niczego"
    assert got["tokenSilent"]["accounts"] == 0 and got["tokenRejected"]["accounts"] == 0
    # No confirmation in `_meta` means no act, even when the TEXT carries something that looks like one.
    assert got["noTokenInMeta"]["accounts"] == 0 and got["tokenInTextOnly"]["accounts"] == 0
    assert got["noTokenInMeta"]["s"]["reason"] == "ac.noToken"
    assert got["accountRefused"]["s"]["reason"] == "OPERATOR"
    assert got["noAddress"]["s"]["reason"] == "ac.noWindow"
    # An address in the structured result is not an address: only `_meta` is read.
    assert got["addressInTextOnly"]["s"]["reason"] == "ac.noWindow" and got["addressInTextOnly"]["opened"] == 0


def test_a_stale_answer_and_a_double_press_do_nothing_extra(observed) -> None:
    stale = observed["staleAnswers"]
    assert stale["asking"]["phase"] == "asking" and stale["after"]["phase"] == "idle"
    assert stale["opens"] == 0 and stale["opened"] == 0, "odpowiedź po Anuluj nic nie otwiera"
    double = observed["doublePress"]
    assert double == {"opens": 1, "asked": 1, "opened": 1}


def test_a_card_drawn_again_while_the_window_is_open_waits_without_opening_anything(observed) -> None:
    got = observed["resumeWaiting"]
    assert got["s1"]["phase"] == "waiting" and got["s2"]["phase"] == "connected"
    assert set(got["names"]) == {"room.account_status"} and got["applied"] == [["X", "M"]]


def test_a_window_closed_by_somebody_else_still_turns_the_dot_green_by_itself(observed) -> None:
    got = observed["greenByItself"]
    assert got["s"]["phase"] == "connected" and got["applied"] == [["X", "M"]] and got["done"] == 0


def test_the_random_walk_never_opens_a_window_that_no_press_asked_for(observed) -> None:
    walks = observed["walks"]
    assert len(walks) == 300
    assert sum(w["accounts"] for w in walks) > 200, "spacer nic by nie sprawdzał, gdyby nie otwierał"
    for w in walks:
        # One act per confirmation the server minted, and never one that was not minted or was spent.
        assert w["unknownToken"] == 0 and w["doubleUse"] == 0, w
        # At most one act per press of a button that means "open", and never more acts than asks.
        assert w["accounts"] <= w["pressed"] and w["accounts"] <= w["tokens"], w
        assert w["tokens"] <= w["pressed"], w
        # `room.connect_done` only from the two presses that mean "I'm signed in" or "Cancel".
        assert w["done"] <= w["dones"], w
        assert w["opened"] <= w["accounts"], w
        # The poll runs only while the card is waiting for the person.
        assert w["polledOutsideWaiting"] == 0, w


# ---------------------------------------------------------------------------
# The card, in jsdom: the footer, the press, the panel
# ---------------------------------------------------------------------------

ACC_X = "demo_founder"


def _accounts(*items: tuple[str, str, str, bool]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"acc-{index}",
            "platforma": platform,
            "konto": handle,
            "stan": state,
            "ekran": "zamkniety",
            "powod": None,
            "mozna_polaczyc": can,
        }
        for index, (platform, handle, state, can) in enumerate(items)
    ]


def _data(*items: tuple[str, str, str, bool], **over: Any) -> dict[str, Any]:
    return {**fixture(), "konta_stan": _accounts(*items), **over}


def _ok(structured: dict, **meta: str) -> dict:
    out: dict[str, Any] = {"structured": structured}
    if meta:
        out["meta"] = meta
    return out


TOOLS_HAPPY = {
    "room.connect_token": _ok({"stan": "WYKONANE", "platforma": "X"}, **{"wenext/potwierdzenie": "tok-" + "c" * 20}),
    "room.connect_account": _ok({"stan": "WYKONANE", "ekran": "otwarty"}, **{"wenext/adres_logowania": HREF}),
    "room.account_status": [
        _ok({"stan": "WYKONANE", "konta": _accounts(("X", ACC_X, "czeka_na_ciebie", False))}),
        _ok({"stan": "WYKONANE", "konta": _accounts(("X", ACC_X, "polaczone", False))}),
    ],
    "room.connect_done": _ok({"stan": "WYKONANE", "polaczone": True, "konto": ACC_X, "kod_odczytu": "POLACZONE", "detail": "ok"}),
}

LIB_FOOT = r"""
const chips = () => $$(".foot .acc").map((c) => ({
  tag: c.tagName, state: c.getAttribute("data-s"), conn: c.getAttribute("data-conn"),
  text: c.textContent.replace(/\s+/g, " ").trim(), title: c.getAttribute("title"),
}));
const panel = () => { const p = $(".botx .conn"); return p ? { phase: p.getAttribute("data-p"),
  text: p.textContent.replace(/\s+/g, " ").trim(), buttons: $$("button", p).map((b) => b.getAttribute("data-conn")) } : null; };
const links = () => log.filter((m) => m.method === "ui/open-link").map((m) => m.params.url);
const toolNames = () => calls().map((c) => c.name);
"""


def _run(tmp_path: Path, scenario: str, **kwargs: Any) -> Any:
    return _run_card(tmp_path, LIB_FOOT + scenario, **kwargs)


@NEEDS_JSDOM
def test_the_footer_shows_a_dot_for_each_account_and_a_button_only_where_a_click_helps(
    tmp_path: Path,
) -> None:
    data = _data(
        ("X", ACC_X, "utracona", True),
        ("REDDIT", "demo_founder", "polaczone", False),
        ("X", "second_x", "nigdy", True),
        ("X", "held_x", "wstrzymane", False),
        ("REDDIT", "blind_r", "nieznana", False),
    )
    got = _run(tmp_path, "await open(); return { chips: chips(), panel: panel(), inFoot: $$('.foot .acc').length };", data=data)
    by_text = {c["text"]: c for c in got["chips"]}
    # v11: the footer lists only the accounts that NEED A PERSON; a healthy one, and one nobody could check, are not there
    # (the Limits panel lists every account's session).
    assert "u/demo_founder" not in by_text and "u/blind_r" not in by_text
    assert by_text["Reconnect @demo_founder"] == {
        "tag": "BUTTON", "state": "utracona", "conn": "connect",
        "text": "Reconnect @demo_founder", "title": "session lost",
    }
    assert by_text["Connect @second_x"]["conn"] == "connect" and by_text["Connect @second_x"]["state"] == "nigdy"
    assert by_text["@held_x"]["tag"] == "SPAN" and by_text["@held_x"]["title"] == "paused"
    assert got["panel"] is None


@NEEDS_JSDOM
def test_the_footer_speaks_polish_when_the_server_says_so(tmp_path: Path) -> None:
    data = _data(("X", ACC_X, "utracona", True), jezyk="pl")
    got = _run(tmp_path, "await open(); return chips();", data=data)
    assert got[0]["text"] == "Połącz ponownie @demo_founder" and got[0]["title"] == "sesja utracona"


@NEEDS_JSDOM
def test_a_card_from_a_server_that_knows_nothing_about_accounts_still_draws_and_shows_none(
    tmp_path: Path,
) -> None:
    old = fixture()
    got = _run(tmp_path, "await open(); return { chips: chips(), foot: !!$('.foot'), pages: $$('[data-panel]').length };", data=old)
    assert got == {"chips": [], "foot": False, "pages": 5}
    junk = {**fixture(), "konta_stan": "nie lista"}
    again = _run(tmp_path, "await open(); return { chips: chips(), pages: $$('[data-panel]').length };", data=junk)
    assert again == {"chips": [], "pages": 5}


@NEEDS_JSDOM
def test_the_hosted_card_shows_no_accounts_even_if_it_is_handed_some(tmp_path: Path) -> None:
    data = _data(("X", ACC_X, "utracona", True), wysylka="reczna")
    got = _run(tmp_path, "await open(); return { chips: chips(), conn: $$('[data-conn]').length };", data=data)
    assert got == {"chips": [], "conn": 0}


@NEEDS_JSDOM
def test_a_card_with_nothing_to_show_still_carries_the_reconnect(tmp_path: Path) -> None:
    """The founder's morning: a lost session and no results at all. The footer is where the way out is."""
    data = {**_data(("X", ACC_X, "utracona", True)), "okazje": [], "szkice": [], "wyniki": None}
    got = _run(tmp_path, "await open(); return chips();", data=data)
    assert got[0]["text"] == "Reconnect @demo_founder" and got[0]["tag"] == "BUTTON"


@NEEDS_JSDOM
def test_the_press_asks_acts_and_opens_the_address_the_server_gave(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const before = { pages: $$("[data-panel]").length, active: idx() };
        $('.foot [data-conn="connect"]').click();
        await sleep(30);
        const out = { names: toolNames(), links: links(), panel: panel(), chips: chips(), before: before,
          after: { pages: $$("[data-panel]").length, active: idx() }, args: calls().map((c) => c.args) };
        const html = doc().getElementById("app").innerHTML;
        out.leaked = ["tok-" + "c".repeat(20), "TICKET-SECRET", "login-capsule"].filter((x) => html.includes(x));
        return out;
        """,
        data=_data(("X", ACC_X, "utracona", True)),
        tools=TOOLS_HAPPY,
    )
    assert got["names"] == ["room.connect_token", "room.connect_account"]
    assert got["args"][0] == {"platforma": "X"}
    assert got["args"][1] == {"platforma": "X", "token": "tok-" + "c" * 20}, "akt dostaje token i nic więcej"
    assert got["links"] == [HREF], "karta otwiera adres z `_meta`"
    assert got["panel"]["phase"] == "waiting" and got["panel"]["buttons"] == ["done", "again", "cancel"]
    assert "Sign in to X in the window that opened" in got["panel"]["text"]
    assert got["chips"] == [], "gdy panel mówi za platformę, chip milczy"
    # The pages, the pager and everything above the footer are as they were.
    assert got["after"] == got["before"]
    # THE SECRETS ARE NOT IN THE CARD: not the confirmation, not the ticket, not the address.
    assert got["leaked"] == []


@NEEDS_JSDOM
def test_the_poll_turns_the_dot_green_without_a_press_and_the_panel_goes_away(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('.foot [data-conn="connect"]').click();
        await sleep(30);
        const waiting = panel();
        // The card's clock is a hundred times faster: 4 s is 40 ms, 5 s is 50 ms.
        await sleep(120);
        const mid = { panel: panel(), chips: chips() };
        await sleep(400);
        return { waiting: waiting, mid: mid, panel: panel(), chips: chips(), names: toolNames() };
        """,
        data=_data(("X", ACC_X, "utracona", True)),
        tools=TOOLS_HAPPY,
    )
    assert got["waiting"]["phase"] == "waiting"
    assert got["panel"] is None, "po połączeniu panel znika"
    assert got["chips"] == [], "signed in: the account no longer needs a person, so it leaves the footer"
    names = got["names"]
    assert names[:2] == ["room.connect_token", "room.connect_account"]
    assert set(names[2:]) == {"room.account_status"}, "przy otwartym oknie pytamy tylko o stan"
    assert "room.connect_done" not in names


@NEEDS_JSDOM
def test_i_am_signed_in_closes_the_window_and_turns_the_dot_green(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('.foot [data-conn="connect"]').click();
        await sleep(30);
        $('.botx [data-conn="done"]').click();
        await sleep(15);
        const checking = panel();
        await sleep(30);
        return { checking: checking, connected: panel(), names: toolNames(), args: calls().map((c) => c.args) };
        """,
        data=_data(("X", ACC_X, "utracona", True)),
        tools={**TOOLS_HAPPY, "room.account_status": _ok({"stan": "WYKONANE", "konta": _accounts(("X", ACC_X, "czeka_na_ciebie", False))})},
    )
    assert "room.connect_done" in got["names"] and got["names"].count("room.connect_done") == 1
    assert got["args"][got["names"].index("room.connect_done")] == {"platforma": "X"}
    assert got["connected"] is None or got["connected"]["phase"] == "connected"


@NEEDS_JSDOM
def test_a_refusal_of_the_question_is_shown_in_the_footer_with_a_way_out(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('.foot [data-conn="connect"]').click();
        await sleep(40);
        const shown = panel();
        $('.botx [data-conn="close"]').click();
        await sleep(10);
        return { shown: shown, after: panel(), chips: chips(), names: toolNames(), links: links() };
        """,
        data=_data(("X", ACC_X, "utracona", True)),
        tools={"room.connect_token": _ok({"stan": "NIEDOSTEPNE", "kod": "KAPSULA", "powod": "capsule down"})},
    )
    assert got["names"] == ["room.connect_token"] and got["links"] == []
    assert got["shown"]["phase"] == "refused" and got["shown"]["buttons"] == ["retry", "close"]
    assert "agent's browser is not available" in got["shown"]["text"], "angielska karta ma swoje zdanie dla kodu"
    assert got["after"] is None and got["chips"][0]["conn"] == "connect", "po Zamknij znów jest przycisk"


@NEEDS_JSDOM
def test_a_window_that_is_open_when_the_card_is_drawn_offers_to_finish_it(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const before = chips();
        $('.foot [data-conn="resume"]').click();
        await sleep(10);
        return { before: before, panel: panel(), names: toolNames() };
        """,
        data=_data(("X", ACC_X, "czeka_na_ciebie", False)),
        tools={"room.account_status": _ok({"stan": "WYKONANE", "konta": _accounts(("X", ACC_X, "czeka_na_ciebie", False))})},
    )
    assert got["before"][0]["text"] == "Finish signing in @demo_founder" and got["before"][0]["conn"] == "resume"
    assert got["panel"]["phase"] == "waiting" and got["panel"]["buttons"] == ["done", "again", "cancel"]
    assert "room.connect_token" not in got["names"] and "room.connect_account" not in got["names"]
