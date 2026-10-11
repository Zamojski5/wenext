"""The card in MANUAL sending (v8, hosted): Wenext sends nothing, the person posts from their own account.

`room.show` says `wysylka: "reczna"` and gives each draft `watek_url` and, on X, `intent_url`. What is
held here, from the outside in, in jsdom with a fake host (the harness of `test_card_pager_unit.py`):

  * THE BUTTON FOLLOWS THE PLATFORM: on Reddit "Copy and open thread", on X "Post on X", and there is
    no "Send", no "Approve in Room" and no consent anywhere on the card;
  * COPYING HAS THREE RUNGS: the async clipboard, then a hidden textarea and `execCommand`, then a
    selected read-only textarea and the words "Cmd+C"; each is detected, none is assumed;
  * OPENING IS THE HOST'S: the thread (Reddit) or the compose link (X) goes out as `ui/open-link`,
    and nothing else leaves the card before the person says they posted;
  * "POSTED IT?" RECORDS A STATEMENT: `draft.mark_posted` with the revision (and the link when given)
    through the host, a link that is not the person's own post on Reddit or X is refused on the
    card, a failed call can be retried or handed to the agent, and a host that cannot carry a call
    gets the agent asked in the person's name; the box says "Wenext did not check it";
  * THE LOCAL FLOW IS UNTOUCHED: without `wysylka` (every local session and every older result) the
    card has the send button and no manual one, and the send machine is byte for byte v7's;
  * a link being typed is not paging, the box survives a redraw, and both languages are there.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from typing import Any

import pytest
from control_plane.tools import apps_ui
from tests.test_card_pager_unit import (
    JSDOM,
    LONG_REPLY,
    OPP_A,
    OPP_B,
    OPP_C,
    REV_A,
    REV_B,
    _draft,
    _opportunity,
    _run,
)
from tests.test_card_pager_unit import (
    fixture as local_fixture,
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not JSDOM.is_dir(),
    reason="potrzebny node i jsdom (apps/dashboard/node_modules)",
)

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")

REDDIT_THREAD = "https://www.reddit.com/r/SideProject/comments/zz1/first_users/"
X_THREAD = "https://x.com/demo_builder_x/status/1000000000000000001"
X_REPLY = "Tools alone did not stick for me either & that is fine."
INTENT = (
    "https://x.com/intent/post?text=Tools%20alone%20did%20not%20stick%20for%20me%20either%20%26%20"
    "that%20is%20fine.&in_reply_to=1000000000000000001"
)
POSTED = "https://www.reddit.com/r/SideProject/comments/zz1/first_users/yy2/"

MARKED = {
    "draft.mark_posted": {
        "structured": {
            "stan": "WYKONANE",
            "zapisano": {"revision_id": REV_A, "adres_posta": POSTED},
            "uwaga": "x",
        }
    }
}


# What the card sees from a hosted `room.show`: no account, no dashboard address, two people with a
# reply (Reddit, then X) and one without.
def manual_fixture(**over: Any) -> dict[str, Any]:
    data = {
        "stan": "WYKONANE",
        "wysylka": "reczna",
        "okazje": [
            _opportunity(OPP_A, stan="SZKIC", szkic=REV_A, url=REDDIT_THREAD),
            _opportunity(
                OPP_B,
                platforma="X",
                spolecznosc="X",
                autor="demo_builder_x",
                stan="SZKIC",
                szkic=REV_B,
                url=X_THREAD,
            ),
            _opportunity(OPP_C),
        ],
        "szkice": [
            _draft(REV_A, OPP_A, konto=None, watek_url=REDDIT_THREAD, intent_url=None),
            _draft(
                REV_B,
                OPP_B,
                platforma="X",
                konto=None,
                tekst=X_REPLY,
                w_odpowiedzi_na={"url": X_THREAD, "tytul": "", "autor": "demo_builder_x"},
                watek_url=X_THREAD,
                intent_url=INTENT,
            ),
        ],
        "wyniki": {
            "okno_dni": 7, "opublikowane": 0, "odpowiedzi": None, "glosy": None, "usuniete": None,
            "pozycje": [],
        },
        "pokoj": "/w/x/p/y/room",
    }  # fmt: skip
    data.update(over)
    return data


# The clipboard of the page, in every state a host can leave it in.
HOOK = r"""
const clip = { calls: [], mode: "__CLIP__", exec: "__EXEC__", execCalls: 0 };
const hook = (w) => {
  Object.defineProperty(w.navigator, "clipboard", { configurable: true,
    value: clip.mode === "none" ? undefined : { writeText: (t) => { clip.calls.push(t);
      return clip.mode === "ok" ? Promise.resolve() : Promise.reject(new Error("denied")); } } });
  w.document.execCommand = (name) => { clip.execCalls++; return name === "copy" && clip.exec === "yes"; };
};
const opened = () => log.filter((m) => m.method === "ui/open-link").map((m) => m.params.url);
const said = () => log.filter((m) => m.method === "ui/message").map((m) => m.params.content[0].text);
const here = () => active()[0];
const go = () => $('[data-man="go"]', here()).click();
const man = (name) => $('[data-man="' + name + '"]', here());
const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
"""


def run(tmp_path, scenario: str, *, data=None, tools=None, clip="ok", exec_="no") -> Any:
    """`clip` is how the page's `navigator.clipboard` behaves: ok, denied or none. `exec_` is whether
    `document.execCommand("copy")` works: yes or no."""
    hook = HOOK.replace("__CLIP__", clip).replace("__EXEC__", exec_)
    return _run(tmp_path, hook + scenario, tools=tools, data=data)


# ---------------------------------------------------------------------------
# What the card draws
# ---------------------------------------------------------------------------


def test_the_button_follows_the_platform_and_there_is_no_send_anywhere(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook);
        const out = { first: text(man("go")), note: text($(".hint", here())), edit: !!$("[data-fix]", here()) };
        next(); out.second = text(man("go"));
        out.send = $$('[data-act="send"]').length;
        out.approve = $$("[data-approve]").length;
        out.room = $$("[data-room]").length;
        out.foot = text($(".foot"));
        out.sum = text($(".sum"));
        out.calls = calls();
        return out;
        """,
        data=manual_fixture(),
    )
    assert got["first"] == "Copy and open thread" and got["second"] == "Post on X"
    assert got["note"] == "You post it yourself, from your account."
    assert got["edit"] is True
    assert got["send"] == 0 and got["approve"] == 0, "no send, no approval link"
    assert got["room"] == 0, "no dashboard address, so no Open Room"
    assert got["foot"] == "Wenext never posts. You post it yourself, from your account."
    assert "2 replies ready to post" in got["sum"] and "waiting for your OK" not in got["sum"]
    assert got["calls"] == [], "drawing the card asks the server for nothing"


def test_the_polish_card_says_the_same_in_polish(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook);
        return { first: text(man("go")), note: text($(".hint", here())), foot: text($(".foot")) };
        """,
        data=manual_fixture(jezyk="pl"),
    )
    assert got["first"] == "Skopiuj i otwórz wątek"
    assert got["note"] == "Publikujesz sam, ze swojego konta."
    assert got["foot"].startswith("Wenext niczego nie publikuje.")


def test_the_card_does_not_show_a_login_it_was_not_given(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook);
        return { head: text($(".pv-head", here())) };
        """,
        data=manual_fixture(),
    )
    assert "u/your_account" in got["head"], "a placeholder, never a login of the project"


# ---------------------------------------------------------------------------
# Copy and open (Reddit)
# ---------------------------------------------------------------------------


def test_copy_and_open_copies_the_exact_reply_and_opens_the_thread_and_nothing_else(
    tmp_path,
) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        return { clip: clip.calls, opened: opened(), box: box(), calls: calls(), said: said(),
                 input: !!$("input[data-man-link]", here()), done: text(man("done")), exec: clip.execCalls };
        """,
        data=manual_fixture(),
    )
    assert got["clip"] == [LONG_REPLY], "exactly the text that was drawn, untrimmed"
    assert got["opened"] == [REDDIT_THREAD]
    (box,) = got["box"]
    assert box["phase"] == "opened"
    assert box["text"].startswith("Reply copied. The thread is open: paste it there and post.")
    assert "Posted it? Paste the link (optional)" in box["text"]
    assert got["input"] is True and got["done"] == "Done"
    assert got["calls"] == [] and got["said"] == [], "nothing is sent to Wenext or the agent yet"
    assert got["exec"] == 0, "the async clipboard worked, so no fallback ran"


def test_when_the_clipboard_is_refused_a_hidden_textarea_is_the_second_rung(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        return { clip: clip.calls.length, exec: clip.execCalls, box: box(), hidden: $$("textarea").length,
                 fallback: !!$("[data-copybox]", here()) };
        """,
        data=manual_fixture(),
        clip="denied",
        exec_="yes",
    )
    assert got["clip"] == 1 and got["exec"] == 1
    assert got["box"][0]["text"].startswith("Reply copied. The thread is open")
    assert got["fallback"] is False and got["hidden"] == 0, "the hidden textarea is removed again"


def test_with_no_clipboard_at_all_the_second_rung_still_copies(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        return { exec: clip.execCalls, box: box(), fallback: !!$("[data-copybox]", here()) };
        """,
        data=manual_fixture(),
        clip="none",
        exec_="yes",
    )
    assert got["exec"] == 1 and got["fallback"] is False
    assert got["box"][0]["text"].startswith("Reply copied.")


def test_when_nothing_can_copy_the_reply_is_selected_in_a_box_and_the_card_says_cmd_c(
    tmp_path,
) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        const ta = $("[data-copybox]", here());
        return { box: box(), value: ta && ta.value, readonly: ta && ta.hasAttribute("readonly"),
                 selected: ta && ta.selectionStart === 0 && ta.selectionEnd === ta.value.length,
                 focused: ta && win.document.activeElement === ta, opened: opened() };
        """,
        data=manual_fixture(),
        clip="denied",
        exec_="no",
    )
    assert got["value"] == LONG_REPLY and got["readonly"] is True
    assert got["selected"] is True and got["focused"] is True
    assert (
        "Cmd+C" in got["box"][0]["text"] and "Couldn't copy automatically" in got["box"][0]["text"]
    )
    assert not got["box"][0]["text"].startswith("Reply copied")
    assert got["opened"] == [REDDIT_THREAD], "the thread is opened even when the copy needs help"


def test_a_host_whose_clipboard_never_answers_does_not_hang_the_card(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open((w) => { hook(w); Object.defineProperty(w.navigator, "clipboard", { configurable: true,
          value: { writeText: () => new Promise(() => {}) } }); });
        go(); await sleep(120);
        return { box: box(), fallback: !!$("[data-copybox]", here()) };
        """,
        data=manual_fixture(),
    )
    assert got["fallback"] is True and "Cmd+C" in got["box"][0]["text"]


def test_a_draft_with_no_thread_address_still_copies_and_says_to_open_the_thread_yourself(
    tmp_path,
) -> None:
    data = manual_fixture()
    data["szkice"][0]["watek_url"] = None
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        return { opened: opened(), box: box() };
        """,
        data=data,
    )
    assert got["opened"] == []
    assert got["box"][0]["text"].startswith("Reply copied. Open the thread, paste it and post.")


# ---------------------------------------------------------------------------
# Post on X
# ---------------------------------------------------------------------------


def test_post_on_x_opens_the_compose_link_and_copies_nothing(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); next(); go(); await sleep(60);
        return { clip: clip.calls, opened: opened(), box: box(), calls: calls() };
        """,
        data=manual_fixture(),
    )
    assert got["opened"] == [INTENT]
    assert got["clip"] == [], "the reply is in the link, nothing goes to the clipboard"
    assert got["box"][0]["text"].startswith("X is open with your reply filled in. Post it there.")
    assert got["calls"] == []


def test_an_x_reply_over_the_limit_is_said_on_the_card_before_the_click(tmp_path) -> None:
    data = manual_fixture()
    data["szkice"][1]["intent_uwaga"] = "The reply is 300 long in X's count and X allows 280."
    got = run(
        tmp_path,
        """
        await open(hook); next();
        return { note: text($(".hint", here())) };
        """,
        data=data,
    )
    assert "This reply is over X's length limit. Shorten it before you post." in got["note"]
    assert got["note"].startswith("You post it yourself")


def test_an_x_draft_without_a_compose_link_falls_back_to_copy_and_the_thread(tmp_path) -> None:
    data = manual_fixture()
    data["szkice"][1]["intent_url"] = None
    got = run(
        tmp_path,
        """
        await open(hook); next();
        const label = text(man("go")); go(); await sleep(60);
        return { label, clip: clip.calls, opened: opened() };
        """,
        data=data,
    )
    assert got["label"] == "Copy and open thread"
    assert got["clip"] == [X_REPLY] and got["opened"] == [X_THREAD]


# ---------------------------------------------------------------------------
# "Posted it? Paste the link"
# ---------------------------------------------------------------------------


def test_done_with_a_link_records_it_through_the_host_and_says_it_was_not_checked(tmp_path) -> None:
    got = run(
        tmp_path,
        f"""
        await open(hook); go(); await sleep(60);
        $("input[data-man-link]", here()).value = "{POSTED}";
        man("done").click(); await sleep(80);
        const out = {{ calls: calls(), box: box(), view: text(man("view")) }};
        man("view").click(); await sleep(30);
        out.opened = opened();
        return out;
        """,
        data=manual_fixture(),
        tools=MARKED,
    )
    assert got["calls"] == [
        {"name": "draft.mark_posted", "args": {"revision_id": REV_A, "url": POSTED}}
    ]
    (box,) = got["box"]
    assert box["phase"] == "done"
    assert (
        "Marked as posted." in box["text"]
        and "Wenext did not check it on the platform." in box["text"]
    )
    assert "verified" not in box["text"].replace("not check", "")
    assert got["view"] == "View your post ↗" and got["opened"][-1] == POSTED


def test_done_without_a_link_sends_only_the_revision(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        man("done").click(); await sleep(80);
        return { calls: calls(), box: box(), view: !!man("view") };
        """,
        data=manual_fixture(),
        tools={
            "draft.mark_posted": {
                "structured": {"stan": "WYKONANE", "zapisano": {"adres_posta": None}}
            }
        },
    )
    assert got["calls"] == [{"name": "draft.mark_posted", "args": {"revision_id": REV_A}}]
    assert got["box"][0]["phase"] == "done" and got["view"] is False


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/https://www.reddit.com/x",
        "http://www.reddit.com/r/a/comments/b1/c/",
        "javascript:alert(1)",
        "https://reddit.com.evil.example/x",
        "not a link",
    ],
)
def test_a_link_that_is_not_the_persons_post_on_reddit_or_x_is_refused_on_the_card(
    tmp_path, link
) -> None:
    got = run(
        tmp_path,
        f"""
        await open(hook); go(); await sleep(60);
        $("input[data-man-link]", here()).value = {link!r};
        man("done").click(); await sleep(60);
        return {{ calls: calls(), box: box(), bad: text($(".badlink", here())), value: $("input[data-man-link]", here()).value }};
        """,
        data=manual_fixture(),
        tools=MARKED,
    )
    assert got["calls"] == [], "a refused link is never sent"
    assert got["box"][0]["phase"] == "opened"
    assert (
        got["bad"] == "Paste the link to your post on Reddit or X (https://...), or leave it empty."
    )
    assert got["value"] == link, "what the person typed stays, so they can fix it"


@pytest.mark.parametrize(
    "link",
    [
        "https://www.reddit.com/r/a/comments/b1/c/d2/",
        "https://x.com/me/status/123",
        "https://twitter.com/me/status/123",
        "https://redd.it/abc",
        "https://old.reddit.com/r/a/comments/b1/c/",
    ],
)
def test_a_link_to_reddit_or_x_is_taken(tmp_path, link) -> None:
    got = run(
        tmp_path,
        f"""
        await open(hook); go(); await sleep(60);
        $("input[data-man-link]", here()).value = {link!r};
        man("done").click(); await sleep(60);
        return {{ calls: calls() }};
        """,
        data=manual_fixture(),
        tools=MARKED,
    )
    assert got["calls"][0]["args"] == {"revision_id": REV_A, "url": link}


def test_a_failed_call_can_be_retried_or_handed_to_the_agent(tmp_path) -> None:
    got = run(
        tmp_path,
        f"""
        await open(hook); go(); await sleep(60);
        $("input[data-man-link]", here()).value = "{POSTED}";
        man("done").click(); await sleep(80);
        const failed = box();
        man("retry").click(); await sleep(30);
        const again = box();
        const kept = $("input[data-man-link]", here()).value;
        man("done").click(); await sleep(80);
        man("ask").click(); await sleep(30);
        return {{ failed, again, kept, said: said(), after: box() }};
        """,
        data=manual_fixture(),
        tools={},
    )
    assert got["failed"][0]["phase"] == "failed"
    assert got["failed"][0]["text"].startswith(
        "Couldn't save it. Try again, or ask your agent to do it."
    )
    assert got["again"][0]["phase"] == "opened" and got["kept"] == POSTED
    (said,) = got["said"]
    assert REV_A in said and "draft.mark_posted" in said and POSTED in said
    assert got["after"][0]["phase"] == "asked"
    assert got["after"][0]["text"] == "I asked your agent to record it. It will answer in the chat."


def test_an_error_result_from_the_server_is_a_failure_not_a_success(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        man("done").click(); await sleep(80);
        return { box: box() };
        """,
        data=manual_fixture(),
        tools={"draft.mark_posted": {"structured": {"stan": "BLAD", "powod": "nie ma szkicu"}}},
    )
    assert got["box"][0]["phase"] == "failed"


def test_a_host_that_cannot_carry_a_tool_call_gets_the_agent_asked_in_the_persons_name(
    tmp_path,
) -> None:
    got = run(
        tmp_path,
        f"""
        const orig = host.postMessage.bind(host);
        host.postMessage = (m) => {{
          if (m.method === "ui/initialize") return reply(m.id, {{ protocolVersion: "2026-01-26", hostCapabilities: {{}}, hostContext: {{ locale: "en-US" }} }});
          return orig(m);
        }};
        await open(hook); await sleep(30); go(); await sleep(60);
        $("input[data-man-link]", here()).value = "{POSTED}";
        man("done").click(); await sleep(60);
        return {{ calls: calls(), said: said(), box: box() }};
        """,
        data=manual_fixture(),
        tools=MARKED,
    )
    assert got["calls"] == [], "the host said it cannot carry a call: none was tried"
    (said,) = got["said"]
    assert REV_A in said and "draft.mark_posted" in said and POSTED in said
    assert got["box"][0]["phase"] == "asked"


def test_not_posted_yet_and_open_again_do_what_they_say(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        man("again").click(); await sleep(60);
        const out = { clip: clip.calls.length, opened: opened().length, phase: box()[0].phase };
        man("no").click(); await sleep(30);
        out.after = { boxes: box().length, primary: text(man("go")), calls: calls() };
        return out;
        """,
        data=manual_fixture(),
    )
    assert (got["clip"], got["opened"], got["phase"]) == (2, 2, "opened")
    assert got["after"] == {"boxes": 0, "primary": "Copy and open thread", "calls": []}


def test_a_button_of_a_page_that_is_not_active_does_nothing(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook);
        const hidden = $$("[data-panel]:not([data-active]) [data-man=go]");
        hidden.forEach((b) => b.click()); await sleep(40);
        return { n: hidden.length, clip: clip.calls, opened: opened(), box: box() };
        """,
        data=manual_fixture(),
    )
    assert got["n"] == 1 and got["clip"] == [] and got["opened"] == [] and got["box"] == []


def test_typing_a_link_is_not_paging(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        const input = $("input[data-man-link]", here());
        input.focus();
        for (const k of ["ArrowRight", "ArrowLeft", "ArrowRight"]) {
          input.dispatchEvent(new win.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
        }
        const typed = idx();
        key("ArrowRight");
        return { typed, outside: idx() };
        """,
        data=manual_fixture(),
    )
    assert got["typed"] == ["0"], "arrow keys move the caret in the field"
    assert got["outside"] == ["1"], "and still page everywhere else"


def test_the_box_and_the_typed_link_survive_a_redraw_in_another_language(tmp_path) -> None:
    got = run(
        tmp_path,
        """
        await open(hook); go(); await sleep(60);
        $("input[data-man-link]", here()).value = "https://x.com/me/status/9";
        toWin({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { locale: "pl-PL" } });
        await sleep(40);
        return { box: box(), idx: idx(), done: text(man("done")) };
        """,
        data=manual_fixture(),
    )
    assert got["box"][0]["phase"] == "opened" and got["idx"] == ["0"]
    assert got["box"][0]["text"].startswith("Odpowiedź skopiowana. Wątek jest otwarty")
    assert got["done"] == "Gotowe"


def test_a_published_one_by_hand_shows_as_posted_with_no_numbers(tmp_path) -> None:
    data = manual_fixture()
    data["okazje"][0]["stan"] = "ODPOWIEDZIANO"
    data["okazje"][0]["szkic"] = None
    data["szkice"] = data["szkice"][1:]
    data["wyniki"]["pozycje"] = [
        {
            "url": POSTED,
            "okazja_id": OPP_A,
            "platforma": "REDDIT",
            "tekst": "My reply.",
            "widocznosc": "UNVERIFIED",
            "glosy": None,
            "odpowiedzi": None,
            "opublikowano": "2026-10-11T10:00:00+00:00",
        }
    ]
    got = run(
        tmp_path,
        """
        await open(hook); next(); next();
        const p = here();
        return { state: text($(".statetxt", p)), page: text(p) };
        """,
        data=data,
    )
    assert got["state"] == "Posted"
    assert got["page"].count("not measured") >= 2, "a number nobody measured is never a zero"
    assert "not confirmed" in got["page"]


# ---------------------------------------------------------------------------
# The local flow is not changed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"wysylka": "karta"}, {"wysylka": None}, {"wysylka": "x"}])
def test_without_manual_sending_the_card_is_the_send_card_of_v7(tmp_path, extra) -> None:
    data = {**local_fixture(), **extra}
    got = run(
        tmp_path,
        """
        await open(hook);
        return { send: $$('[data-act="send"]', here()).length, man: $$("[data-man]").length,
                 foot: text($(".foot")), hint: text($(".hint", here())) };
        """,
        data=data,
    )
    assert got["send"] == 1 and got["man"] == 0
    assert got["foot"].startswith("Nothing posts without your click.")
    assert "Goes out from" in got["hint"]


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def _script() -> str:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    return html[html.index("<script>") : html.index("</script>")]


def test_the_send_machine_is_byte_for_byte_the_one_of_v7() -> None:
    script = _script()
    block = script[
        script.index("/*SEND-BEGIN*/") : script.index("/*SEND-END*/") + len("/*SEND-END*/")
    ]
    assert hashlib.sha256(block.encode()).hexdigest() == (
        "2c40f73f69401196dca1db635ca3a3935bec0c64f95f9ee4b8a18b6f3b27648f"
    )


def test_the_manual_flow_names_one_tool_and_none_of_the_send_ones() -> None:
    script = _script()
    manual = script[script.index("// ---- MANUAL SENDING") : script.index("// ---- The pages")]
    assert 'callTool("draft.mark_posted"' in manual and manual.count("callTool(") == 1
    assert "room.send" not in manual and "sender." not in manual
    assert script.count('"draft.mark_posted"') == 1
    assert script.count('"room.send_draft"') == 1


def test_the_clipboard_is_detected_and_never_assumed() -> None:
    script = _script()
    assert 'typeof navigator.clipboard.writeText === "function"' in script
    assert "navigator.clipboard.writeText(text)" in script
    assert 'document.execCommand("copy")' in script
    assert "[data-copybox]" in script and "cb.select()" in script


def test_the_manual_words_are_in_both_languages_with_the_same_placeholders() -> None:
    keys = [k for k in EN if k.startswith("man.")] + [
        "rd.waitingManual", "rd.footManual", "state.ready", "oc.draftGoneManual", "ask.fixTailManual",
    ]  # fmt: skip
    assert len([k for k in keys if k.startswith("man.")]) >= 20
    for key in keys:
        assert key in PL and key in EN, key
        pl, en = PL[key], EN[key]
        for value in (pl, en):
            for text in value if isinstance(value, list) else [value]:
                assert not LONG_DASHES.search(text), key
        if isinstance(pl, str):
            assert PLACEHOLDER.findall(pl) == PLACEHOLDER.findall(en), key
        else:
            assert len(pl) == 3 and len(en) == 2, key
    assert set(EN) == set(PL)
    assert (EN["man.copyOpen"], EN["man.postX"]) == ("Copy and open thread", "Post on X")
    assert EN["man.note"] == "You post it yourself, from your account."
    assert EN["man.posted"] == "Posted it? Paste the link (optional)"
    assert "Cmd+C" in EN["man.copyFail"] and "Cmd+C" in PL["man.copyFail"]
    assert not any(re.search("[ąćęłńóśźż]", EN[k]) for k in EN if k.startswith("man."))
