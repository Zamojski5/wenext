"""The card heals the refusals that pass, and it NEVER sends without the person's click (v6).

THE FOUNDER'S FIRST REAL USE (11.10): four drafts clicked quickly, one confirmed. The queue of the
publisher was busy, so a draft showed "poprzednia odpowiedź jeszcze czeka w kolejce", and the sentence
stayed after the cause was gone; another showed "zakres zmienił się", because its confirmation had been
asked for before the signature. v6 asks again by itself. What is held here, from the outside in:

  * the send machine of the card (`makeSender`, cut out of the script by its SEND markers) is PURE and
    is run in node with a fake host and a fake clock, so the card's logic is what is executed and not a
    copy of it;
  * a busy queue (`ZAKRES_KOLEJKA`) is waited out, asking `room.send_token` every four seconds for
    about a minute, and ends at a confirmation to CLICK; past the minute it shows the refusal with
    "Try again"; "Cancel" stops it and a stale answer is dropped;
  * a stale confirmation (`ZAKRES_ZMIENIONY`, `POTWIERDZENIE`) is asked for again ONCE and shown with
    its new sentence; a second one is a refusal, and so is any other code, each with "Try again";
  * THE INVARIANT: `room.send_draft` is called only from the click on "Yes, send", once per
    confirmation, with a token the server minted and nobody has used. Held by a table of scenarios,
    and by a seeded random walk over every action the card has, against a host that answers
    arbitrarily, in which a call that does not come out of a `go` click is a failure;
  * and in the source: one string literal `"room.send_draft"`, inside `go`, reached from `dispatch`.

NOT HELD HERE: how it looks (the PNGs the task report names) and that Codex proxies the card's calls
the way the fake host does (unverified live; `tests/test_card_send_unit.py` says what is).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from control_plane.tools import apps_ui

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nie jest zainstalowany")

CONTROL_PLANE = Path(__file__).resolve().parents[1]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")


def _script() -> str:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    return html[html.index("<script>") : html.index("</script>")]


def _sender_source() -> str:
    script = _script()
    start = script.index("/*SEND-BEGIN*/") + len("/*SEND-BEGIN*/")
    return script[start : script.index("/*SEND-END*/")]


def _code(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def test_the_send_is_made_in_one_place_and_only_the_yes_button_reaches_it() -> None:
    script = _script()
    code = _code(script)
    assert code.count('"room.send_draft"') == 1, "wysyłka z karty ma jedno miejsce"
    sender = _code(_sender_source())
    go = sender[sender.index("function go(d)") : sender.index("function follow(d, run, n)")]
    assert '"room.send_draft"' in go
    # `go` is defined once and called from `dispatch` once: no timer, no answer, no poll calls it.
    assert sender.count("go(d)") == 2
    dispatch = sender[sender.index("function dispatch(") :]
    assert 'name === "go") go(d)' in dispatch
    # And `dispatch` is reached from `sendAction`, which only the document's click handler calls.
    assert len(re.findall(r"sendAction\(", script)) == 2
    handler = script[script.index('document.addEventListener("click"') :]
    assert 'sendAction(b.getAttribute("data-act")' in handler


def test_the_send_machine_is_pure_and_talks_to_the_world_only_through_env() -> None:
    code = _code(_sender_source())
    for forbidden in (
        "document",
        "window",
        "navigator",
        "setTimeout",
        "setInterval",
        "fetch(",
        "rpc(",
    ):
        assert forbidden not in code, forbidden
    assert "function makeSender(env)" in code
    for member in (
        "env.call",
        "env.later",
        "env.paint",
        "env.T",
        "env.reason",
        "env.lang",
        "env.open",
    ):
        assert member in code, member


def test_the_polling_is_bounded_and_the_wait_has_a_way_out() -> None:
    code = _code(_sender_source())
    assert "var WAIT_STEP = 4000;" in code and "var WAIT_LIMIT = 60000;" in code
    assert 'name === "cancel"' in code and "++seq" in code, "anuluj kończy przebieg"
    script = _script()
    for key in ("sp.waiting", "sp.retry", "sp.waitingNote", "c.changed"):
        assert f'"{key}"' in script, key
    assert apps_ui.CARD_STRINGS["pl"]["sp.waiting"] == "Czekam, aż poprzednia odpowiedź wyjdzie..."
    assert apps_ui.CARD_STRINGS["en"]["sp.waiting"] == "Waiting for the previous reply to go out..."
    assert apps_ui.CARD_STRINGS["pl"]["sp.retry"] == "Spróbuj ponownie"
    assert apps_ui.CARD_STRINGS["en"]["sp.retry"] == "Try again"


def test_every_refusal_panel_has_a_try_again_button_and_the_waiting_panel_a_cancel() -> None:
    script = _script()
    refused = script[script.index('case "refused":') : script.index("default: return")]
    assert 'act("retry"' in refused and 'act("close"' in refused
    waiting = script[script.index('case "waiting":') : script.index('case "confirm":')]
    assert 'act("cancel"' in waiting


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
var PLAN_SIGN = { rodzaj: "PODPISZ", konta: [{ platforma: "X", konto: "a" }, { platforma: "REDDIT", konto: "b" }] };
var PLAN_SEND = { rodzaj: "WYSLIJ", konta: [] };
function ok(plan, token) {
  return { result: { structuredContent: { stan: "WYKONANE", zakres: plan }, _meta: { "wenext/potwierdzenie": token } } };
}
function refusal(kod) { return { result: { structuredContent: { stan: "NIEDOSTEPNE", kod: kod, powod: "powod " + kod } } }; }
function queued(id) { return { result: { structuredContent: { stan: "OCZEKUJE", wniosek: id } } }; }
function status(s) { return { result: { structuredContent: { stan: "WYKONANE", wniosek: { status: s, widocznosc: "CONFIRMED", adres: "https://x.test/p" } } } }; }
var DRAFT = { revision_id: "rev-1", odcisk: "a".repeat(64), platforma: "REDDIT", konto: "b", rodzaj: "REPLY" };

// A host that answers from a script: for each tool a function of (the call number of that tool, args).
function makeWorld(answers) {
  var w = { calls: [], phases: [], inGo: false, strayDrafts: 0, minted: {}, spent: {}, doubleUse: 0, unknownToken: 0 };
  w.count = function (name) { return w.calls.filter(function (c) { return c.name === name; }).length; };
  w.env = {
    call: function (name, args) {
      var n = w.count(name);
      w.calls.push({ name: name, args: args, at: clock.now });
      if (name === "room.send_draft") {
        if (!w.inGo) w.strayDrafts++;
        if (!w.minted[args.token]) w.unknownToken++;
        if (w.spent[args.token]) w.doubleUse++;
        w.spent[args.token] = true;
      }
      return Promise.resolve(answers[name](n, args, w)).then(function (answer) {
        if (name === "room.send_token" && answer && answer.result && answer.result._meta) {
          w.minted[answer.result._meta["wenext/potwierdzenie"]] = true;   // the server minted it
        }
        return answer;
      });
    },
    later: later,
    paint: function (d) { w.phases.push(w.sender.of(d).phase); },
    T: function (k) { return k; },
    lang: function () { return "en"; },
    reason: function (data, why) { return (data && data.kod) || why || "?"; },
    open: function () {}
  };
  w.sender = makeSender(w.env);
  w.click = function (name) {
    if (name === "go") { w.inGo = true; try { w.sender.dispatch("go", DRAFT); } finally { w.inGo = false; } }
    else w.sender.dispatch(name, DRAFT);
  };
  w.st = function () { return w.sender.of(DRAFT); };
  w.names = function () { return w.calls.map(function (c) { return c.name; }); };
  return w;
}
function snap(w) {
  var st = w.st();
  return { phase: st.phase, calls: w.names(), token: st.token || null, reason: st.reason || null,
    reminted: !!st.reminted, changed: !!st.changed, plan: st.plan ? st.plan.rodzaj : null, at: clock.now };
}
"""

SCENARIOS = r"""
var out = {};

// 1. The queue is busy twice, then clear: wait, then a confirmation to CLICK. Nothing is sent.
async function queueHeals() {
  var w = makeWorld({
    "room.send_token": function (n) { return n < 2 ? refusal("ZAKRES_KOLEJKA") : ok(PLAN_SIGN, "t-" + n); },
    "room.send_draft": function () { return queued("req-1"); },
    "room.send_status": function () { return status("QUEUED"); }
  });
  var seen = [];
  w.click("send"); await tick(0); seen.push(snap(w));
  await tick(3999); seen.push(snap(w));      // not yet the second ask
  await tick(1); seen.push(snap(w));         // 4 s: second ask, still busy
  await tick(4000); seen.push(snap(w));      // 8 s: clear, the confirmation
  await tick(120000); seen.push(snap(w));    // two minutes later: still waiting for the click, no polling
  var beforeGo = w.count("room.send_draft");
  w.click("go"); await tick(0); seen.push(snap(w));
  return { seen: seen, draftsBeforeGo: beforeGo, draftsAfterGo: w.count("room.send_draft"), stray: w.strayDrafts };
}

// 2. The queue never clears: about a minute of asking, then the refusal with a way out.
async function queueStays() {
  var w = makeWorld({
    "room.send_token": function (n) { return n < 100 ? refusal("ZAKRES_KOLEJKA") : ok(PLAN_SEND, "t-late"); },
    "room.send_draft": function () { return queued("req-1"); }
  });
  w.click("send"); await tick(0);
  await tick(59000); var atMinute = snap(w);
  await tick(5000); var afterMinute = snap(w);
  var asked = w.count("room.send_token");
  await tick(600000); var later600 = snap(w);
  return { atMinute: atMinute, afterMinute: afterMinute, asked: asked, askedLater: w.count("room.send_token"),
    later600: later600, drafts: w.count("room.send_draft") };
}

// 3. Cancel while waiting stops it, and an answer already on its way is dropped.
async function cancelWhileWaiting() {
  var pending = null;
  var w = makeWorld({
    "room.send_token": function (n) {
      if (n === 1) return new Promise(function (res) { pending = res; });
      return refusal("ZAKRES_KOLEJKA");
    },
    "room.send_draft": function () { return queued("req-1"); }
  });
  w.click("send"); await tick(0);               // asks (1st: busy)
  await tick(4000);                              // 2nd ask is in flight (never answered)
  var inFlight = snap(w);
  w.click("cancel"); await tick(0);
  var cancelled = snap(w);
  pending(ok(PLAN_SEND, "t-stale"));             // the stale answer arrives after the cancel
  await tick(0);
  await tick(600000);
  return { inFlight: inFlight, cancelled: cancelled, after: snap(w), asked: w.count("room.send_token"),
    drafts: w.count("room.send_draft") };
}

// 4. A confirmation that went stale (the scope moved): ask once more, show the new sentence, click again.
async function staleOnce(kod) {
  var w = makeWorld({
    "room.send_token": function (n) { return n === 0 ? ok(PLAN_SIGN, "t-old") : ok(PLAN_SEND, "t-new"); },
    "room.send_draft": function (n) { return n === 0 ? refusal(kod) : queued("req-1"); },
    "room.send_status": function () { return status("QUEUED"); }
  });
  var seen = [];
  w.click("send"); await tick(0); seen.push(snap(w));
  w.click("go"); await tick(0); seen.push(snap(w));        // refused, asked again, confirmation again
  await tick(600000); seen.push(snap(w));                  // and it waits for the click
  var draftsBefore = w.count("room.send_draft");
  w.click("go"); await tick(0); seen.push(snap(w));
  return { seen: seen, draftsBefore: draftsBefore, drafts: w.count("room.send_draft"), stray: w.strayDrafts,
    doubleUse: w.doubleUse };
}

// 5. A second stale refusal is shown (no endless healing); "Try again" asks from the top and ends at a click.
async function staleTwice() {
  var w = makeWorld({
    "room.send_token": function (n) { return ok(n === 0 ? PLAN_SIGN : PLAN_SEND, "t-" + n); },
    "room.send_draft": function (n) { return n < 2 ? refusal("ZAKRES_ZMIENIONY") : queued("req-1"); }
  });
  var seen = [];
  w.click("send"); await tick(0);
  w.click("go"); await tick(0); seen.push(snap(w));   // healed once: confirmation again
  w.click("go"); await tick(0); seen.push(snap(w));   // stale again: refused
  var after = { asked: w.count("room.send_token"), drafts: w.count("room.send_draft") };
  w.click("retry"); await tick(0); seen.push(snap(w));
  return { seen: seen, after: after, drafts: w.count("room.send_draft"), asked: w.count("room.send_token"), stray: w.strayDrafts };
}

// 6. Any other refusal, from either tool: shown with "Try again", never healed by itself.
async function otherRefusals() {
  var results = {};
  var codes = ["BUDZET", "ZAKRES_WSTRZYMANY", "ZAKRES_INNY_TRYB", "STAN", "OKNO", "ODCISK", "PODPIS", "NIE_ZNALEZIONO", "NOWY_KOD"];
  for (var i = 0; i < codes.length; i++) {
    var code = codes[i];
    var fromToken = makeWorld({
      "room.send_token": function (n) { return n === 0 ? refusal(code) : ok(PLAN_SEND, "t-retry"); },
      "room.send_draft": function () { return queued("req-1"); }
    });
    fromToken.click("send"); await tick(0); await tick(600000);
    var a = snap(fromToken);
    fromToken.click("retry"); await tick(0);
    var b = snap(fromToken);
    var fromDraft = makeWorld({
      "room.send_token": function () { return ok(PLAN_SEND, "t-" + Math.random()); },
      "room.send_draft": function () { return refusal(code); }
    });
    fromDraft.click("send"); await tick(0); fromDraft.click("go"); await tick(0); await tick(600000);
    var c = snap(fromDraft);
    var draftsAfter = fromDraft.count("room.send_draft");
    fromDraft.click("retry"); await tick(0);
    results[code] = { tokenRefusal: a, retried: b, draftRefusal: c, draftCalls: draftsAfter,
      draftCallsAfterRetry: fromDraft.count("room.send_draft"), askedAfterDraftRefusal: fromDraft.count("room.send_token") };
  }
  return results;
}

// 7. The agent did not answer the send: nobody knows whether it went out, so nothing is asked again by itself.
async function silentSend() {
  var w = makeWorld({
    "room.send_token": function () { return ok(PLAN_SEND, "t-1"); },
    "room.send_draft": function () { return { error: "silent" }; }
  });
  w.click("send"); await tick(0); w.click("go"); await tick(0); await tick(600000);
  return { s: snap(w), drafts: w.count("room.send_draft"), asked: w.count("room.send_token") };
}

// 8. Without a token in `_meta` there is no confirmation to show, and so no send.
async function noToken() {
  var w = makeWorld({
    "room.send_token": function () { return { result: { structuredContent: { stan: "WYKONANE", zakres: PLAN_SEND } } }; },
    "room.send_draft": function () { return queued("req-1"); }
  });
  w.click("send"); await tick(0); w.click("go"); await tick(0);
  return { s: snap(w), drafts: w.count("room.send_draft") };
}

// 9. A double click on "Yes, send" sends once.
async function doubleClick() {
  var w = makeWorld({
    "room.send_token": function () { return ok(PLAN_SEND, "t-1"); },
    "room.send_draft": function () { return queued("req-1"); },
    "room.send_status": function () { return status("PUBLISHED"); }
  });
  w.click("send"); await tick(0); w.click("go"); w.click("go"); w.click("go"); await tick(0);
  await tick(3000);
  return { drafts: w.count("room.send_draft"), s: snap(w) };
}

// 10. THE RANDOM WALK: every action the card has, against a host that answers arbitrarily.
function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
async function walk(seed, withGo) {
  var r = rng(seed), pick = function (list) { return list[Math.floor(r() * list.length)]; };
  var minted = 0;
  var w = makeWorld({
    "room.send_token": function () {
      var k = pick(["ok", "ok", "queue", "queue", "other", "error", "notoken", "stale"]);
      if (k === "ok") return ok(pick([PLAN_SIGN, PLAN_SEND]), "tok-" + (minted++));
      if (k === "queue") return refusal("ZAKRES_KOLEJKA");
      if (k === "other") return refusal(pick(["BUDZET", "ZAKRES_WSTRZYMANY", "NIE_ZNALEZIONO", "STAN"]));
      if (k === "error") return { error: pick(["silent", "rejected"]) };
      if (k === "stale") return Promise.resolve(ok(PLAN_SEND, "tok-" + (minted++)));
      return { result: { structuredContent: { stan: "WYKONANE", zakres: PLAN_SEND } } };
    },
    "room.send_draft": function () {
      var k = pick(["queued", "zmieniony", "potwierdzenie", "budzet", "error", "stan"]);
      if (k === "queued") return queued("req-" + r());
      if (k === "zmieniony") return refusal("ZAKRES_ZMIENIONY");
      if (k === "potwierdzenie") return refusal("POTWIERDZENIE");
      if (k === "budzet") return refusal("BUDZET");
      if (k === "stan") return refusal("STAN");
      return { error: "silent" };
    },
    "room.send_status": function () {
      return status(pick(["QUEUED", "PUBLISHING", "PUBLISHED", "BLOCKED", "SKIPPED", "UNKNOWN"]));
    }
  });
  var actions = ["send", "retry", "cancel", "close", "refresh", "live", "presence", "t1", "t4", "t60"];
  if (withGo) actions = actions.concat(["go", "go", "go"]);
  var goes = 0, goesInConfirm = 0;
  for (var step = 0; step < 60; step++) {
    var a = pick(actions);
    if (a === "go") { goes++; if (w.st().phase === "confirm" && w.st().token) goesInConfirm++; w.click("go"); await tick(0); }
    else if (a === "t1") await tick(1000);
    else if (a === "t4") await tick(4000);
    else if (a === "t60") await tick(60000);
    else { w.click(a); await tick(0); }
  }
  await tick(1000000);
  return { stray: w.strayDrafts, drafts: w.count("room.send_draft"), goes: goes, goesInConfirm: goesInConfirm,
    doubleUse: w.doubleUse, unknownToken: w.unknownToken, phase: w.st().phase };
}

(async function () {
  out.queueHeals = await queueHeals();
  out.queueStays = await queueStays();
  out.cancelWhileWaiting = await cancelWhileWaiting();
  out.staleChanged = await staleOnce("ZAKRES_ZMIENIONY");
  out.staleToken = await staleOnce("POTWIERDZENIE");
  out.staleTwice = await staleTwice();
  out.otherRefusals = await otherRefusals();
  out.silentSend = await silentSend();
  out.noToken = await noToken();
  out.doubleClick = await doubleClick();
  var noGo = [], withGo = [];
  for (var seed = 1; seed <= 250; seed++) noGo.push(await walk(seed, false));
  for (var seed2 = 1001; seed2 <= 1250; seed2++) withGo.push(await walk(seed2, true));
  out.noGo = noGo; out.withGo = withGo;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> dict:
    program = tmp_path_factory.mktemp("card-send") / "send.js"
    program.write_text(_sender_source() + HARNESS + SCENARIOS, encoding="utf-8")
    done = subprocess.run(
        ["node", str(program)], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_busy_queue_is_waited_out_and_ends_at_a_confirmation_to_click(observed: dict) -> None:
    run = observed["queueHeals"]
    first, before_second, second_busy, clear, long_after, sent = run["seen"]
    assert first["phase"] == "waiting" and first["calls"] == ["room.send_token"]
    assert before_second["calls"] == ["room.send_token"], "pyta co 4 s, nie częściej"
    assert second_busy["phase"] == "waiting" and second_busy["calls"].count("room.send_token") == 2
    # The queue cleared: the confirmation is on the screen, with the token to be spent by the click.
    assert clear["phase"] == "confirm" and clear["plan"] == "PODPISZ" and clear["token"] == "t-2"
    # Two minutes later it still waits for the person, asks nothing more, and has sent nothing.
    assert long_after["phase"] == "confirm"
    assert long_after["calls"] == ["room.send_token"] * 3
    assert run["draftsBeforeGo"] == 0, "powtórka bez kliknięcia nie może wysłać"
    assert sent["phase"] in ("queued", "sending") and run["draftsAfterGo"] == 1
    assert run["stray"] == 0


def test_a_queue_that_never_clears_is_given_about_a_minute_then_shown_with_try_again(
    observed: dict,
) -> None:
    run = observed["queueStays"]
    assert run["atMinute"]["phase"] == "waiting" and run["afterMinute"]["phase"] == "refused"
    assert run["afterMinute"]["reason"] == "ZAKRES_KOLEJKA"
    assert 14 <= run["asked"] <= 17, "około minuty co 4 s"
    assert run["askedLater"] == run["asked"], "po odmowie nie pyta już nic"
    assert run["later600"]["phase"] == "refused" and run["drafts"] == 0


def test_cancel_stops_the_wait_and_a_stale_answer_is_dropped(observed: dict) -> None:
    run = observed["cancelWhileWaiting"]
    assert run["inFlight"]["phase"] == "waiting"
    assert run["cancelled"]["phase"] == "idle"
    assert run["after"]["phase"] == "idle", "spóźniona odpowiedź nie wraca na ekran"
    assert run["after"]["token"] is None and run["drafts"] == 0
    assert run["asked"] == 2, "po anulowaniu nic już nie pyta"


@pytest.mark.parametrize("key", ["staleChanged", "staleToken"])
def test_a_stale_confirmation_is_asked_for_once_and_shown_again_for_a_click(
    observed: dict, key: str
) -> None:
    run = observed[key]
    asked_first, healed, waiting, sent = run["seen"]
    assert asked_first["phase"] == "confirm" and asked_first["plan"] == "PODPISZ"
    # The send was turned down, the card asked again by itself and shows the NEW sentence:
    assert healed["phase"] == "confirm" and healed["plan"] == "WYSLIJ"
    assert healed["token"] == "t-new" and healed["reminted"] is True
    assert healed["calls"] == ["room.send_token", "room.send_draft", "room.send_token"]
    assert healed["changed"] is (key == "staleChanged"), "nowe zdanie dostaje wzmiankę o zmianie"
    # ...and waits for the person: ten minutes later nothing was sent again.
    assert waiting["phase"] == "confirm" and waiting["calls"] == healed["calls"]
    assert run["draftsBefore"] == 1, "bez drugiego kliknięcia nie ma drugiej wysyłki"
    assert run["drafts"] == 2 and sent["phase"] in ("queued", "sending")
    assert run["stray"] == 0 and run["doubleUse"] == 0


def test_a_second_stale_refusal_is_shown_and_try_again_ends_at_a_click(observed: dict) -> None:
    run = observed["staleTwice"]
    healed, refused, retried = run["seen"]
    assert healed["phase"] == "confirm" and healed["reminted"] is True
    assert refused["phase"] == "refused" and refused["reason"] == "ZAKRES_ZMIENIONY"
    assert run["after"] == {"asked": 2, "drafts": 2}, "nie leczy w kółko"
    assert retried["phase"] == "confirm" and retried["reminted"] is False
    assert run["drafts"] == 2, "ponowienie samo niczego nie wysyła"
    assert run["stray"] == 0


def test_any_other_refusal_stays_with_try_again_and_is_never_healed_by_itself(
    observed: dict,
) -> None:
    for code, run in observed["otherRefusals"].items():
        assert run["tokenRefusal"]["phase"] == "refused", code
        assert run["tokenRefusal"]["calls"] == ["room.send_token"], f"{code}: pytała sama"
        assert run["retried"]["phase"] == "confirm", f"{code}: Spróbuj ponownie prowadzi do zgody"
        assert run["draftRefusal"]["phase"] == "refused", code
        assert run["draftCalls"] == 1 and run["draftCallsAfterRetry"] == 1, code
        assert run["askedAfterDraftRefusal"] == 2, f"{code}: ponowienie pyta raz, nie wysyła"


def test_an_unanswered_send_is_not_retried_by_the_card(observed: dict) -> None:
    run = observed["silentSend"]
    assert run["s"]["phase"] == "refused" and run["drafts"] == 1 and run["asked"] == 1


def test_no_token_in_meta_means_no_confirmation_and_no_send(observed: dict) -> None:
    run = observed["noToken"]
    assert run["s"]["phase"] == "refused" and run["drafts"] == 0


def test_a_double_click_on_yes_sends_once(observed: dict) -> None:
    assert observed["doubleClick"]["drafts"] == 1


def test_without_a_click_on_yes_the_card_never_sends_whatever_the_host_answers(
    observed: dict,
) -> None:
    """250 seeded walks over every action but "Yes, send": waits, retries, cancels, timers, a host
    that refuses, errors and answers late. Not one `room.send_draft`."""
    assert len(observed["noGo"]) == 250
    for walk in observed["noGo"]:
        assert walk["drafts"] == 0 and walk["stray"] == 0, walk


def test_with_clicks_every_send_comes_from_a_click_with_a_fresh_token(observed: dict) -> None:
    """250 more walks that include "Yes, send": a send is made only inside the click, once per
    confirmation, with a token the server minted and no one used before, and never more sends than
    clicks that found a confirmation on the screen (a remint adds a confirmation, never a send)."""
    sent = 0
    for walk in observed["withGo"]:
        assert walk["stray"] == 0, walk
        assert walk["doubleUse"] == 0 and walk["unknownToken"] == 0, walk
        assert walk["drafts"] <= walk["goesInConfirm"], walk
        sent += walk["drafts"]
    assert sent > 100, "kroki losowe powinny też zdarzać się wysyłki, inaczej test nic nie sprawdza"


def test_the_new_files_have_no_long_dash() -> None:
    text = (CONTROL_PLANE / "tests/test_card_send_card_unit.py").read_text(encoding="utf-8")
    assert not LONG_DASHES.search(text)
