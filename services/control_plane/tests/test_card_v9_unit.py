"""The card of v9 takes the dashboard out of the flow: skipped, inline edit, sent, pause and limits.

THE FOUNDER (11.10): "the user should not need our web dashboard at all". What is held here, from the
outside in, in jsdom with the fake host of `test_card_pager_unit.py` (the card's own script is what runs):

  * SKIPPED (N): a quiet disclosure with the threads the agent decided not to answer, each with its
    platform, community, title, a link and one line of reason (a thread the person skipped says so);
  * SENT (N): the replies that went out, with a link, the visibility and the latest numbers, and a
    number nobody measured is "not measured", never 0;
  * EDIT INLINE: "Edit" turns the reply into a textarea; Save calls `room.revise_draft` with the
    revision and the text and nothing else, the card repaints with the NEW revision and digest, and the
    next "Send" asks about the new text; "Ask the agent to rewrite" stays;
  * PAUSE, RESUME, LIMITS: three confirmations behind the same one-time token as the send. Asking
    (`room.scope_token`) changes nothing; `room.pause`, `room.resume` and `room.sign_scope` are called
    ONLY from the "Yes" button, with the token and nothing else (the card never sends accounts or limits
    to the redeeming call); an empty form is refused on the card; a refusal of a send for a reason a
    scope change fixes offers that change inline;
  * NO DASHBOARD IN THE FLOW: no "Approve in Room", no "Open Room"; one small "Open full view" link in
    the footer of a local install, never on the hosted card.

NOT HELD HERE: how it looks (the PNGs the task report names) and that Codex proxies the card's calls
the way the fake host does (unverified live).
"""

from __future__ import annotations

import copy
import re
import shutil
from typing import Any

import pytest
from control_plane.tools import apps_ui
from tests.test_card_pager_unit import (
    DIGEST,
    JSDOM,
    OPP_A,
    OPP_B,
    OPP_C,
    OPP_D,
    REV_A,
    REV_B,
    REV_C,
    ROOM,
    TOKEN_OK,
    _draft,
    _iso,
    _refusal,
)
from tests.test_card_pager_unit import _run as _base_run
from tests.test_card_pager_unit import fixture as local_fixture

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not JSDOM.is_dir(),
    reason="potrzebny node i jsdom (apps/dashboard/node_modules)",
)

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")

ACC_R = "00000000-0000-4000-8000-0000000000a1"
ACC_X = "00000000-0000-4000-8000-0000000000a2"
REV_A2 = "44444444-4444-4444-8444-444444444444"
DIGEST2 = "b" * 64
SCOPE_TOKEN = "scope-" + "t" * 24


def _scope(state: str = "AKTYWNY", **over: Any) -> dict[str, Any]:
    data = {
        "stan": state,
        "tryb": "REVIEW",
        "wygasa": _iso(days=14),
        "konta": [
            {"platforma": "REDDIT", "konto": "demo_founder"},
            {"platforma": "X", "konto": "demo_founder_x"},
        ],
        "dzialania": ["REPLY", "OWN_POST", "RESEARCH"],
        "limity": {"godzina": 4, "doba": 20, "wlasne_doba": 1},
        "do_wyboru": [
            {
                "id": ACC_R,
                "platforma": "REDDIT",
                "konto": "demo_founder",
                "dzialania": ["REPLY", "OWN_POST"],
                "w_zakresie": True,
            },
            {
                "id": ACC_X,
                "platforma": "X",
                "konto": "demo_founder_x",
                "dzialania": ["REPLY", "OWN_POST"],
                "w_zakresie": True,
            },
        ],
        "domyslne": {"godzina": 2, "doba": 10, "wlasne_doba": 1, "dni": 7},
        "granice": {"godzina": 10, "doba": 50, "wlasne_doba": 10},
    }
    data.update(over)
    return data


SKIPPED = [
    {
        "id": "s1",
        "platforma": "REDDIT",
        "spolecznosc": "r/SaaS",
        "tytul": "Is cold email dead for B2B tools?",
        "url": "https://www.reddit.com/r/SaaS/comments/zz8k1a/cold_email/",
        "pominieto": _iso(hours=-2),
        "przez": "agent",
        "powod": "Eleven days old and the top answer already says what I would.",
    },
    {
        "id": "s2",
        "platforma": "X",
        "spolecznosc": "X",
        "tytul": "Hiring a growth marketer, DM me",
        "url": "https://x.com/demo_recruiter/status/1000000000000000009",
        "pominieto": _iso(hours=-5),
        "przez": "agent",
        "powod": "A hiring post, not somebody asking for help.",
    },
    {
        "id": "s3",
        "platforma": "REDDIT",
        "spolecznosc": "r/indiehackers",
        "tytul": "Rate my landing page",
        "url": "https://www.reddit.com/r/indiehackers/comments/zz8k2b/rate/",
        "pominieto": _iso(days=-1),
        "przez": "czlowiek",
        "powod": None,
    },
]


def v9(**over: Any) -> dict[str, Any]:
    """The local card's `room.show`: the pager's fixture plus what v9 reads."""
    data = local_fixture()
    data["wysylka"] = "karta"
    data["zakres"] = _scope()
    data["pominiete"] = copy.deepcopy(SKIPPED)
    data["pominiete_lacznie"] = 5
    data["wyniki"]["pozycje"].append(
        {
            "url": "https://x.com/demo_founder_x/status/2000000000000000002",
            "okazja_id": None,
            "platforma": "X",
            "tekst": "Answering one live thread a day beat every tool I tried.",
            "widocznosc": "UNVERIFIED",
            "glosy": None,
            "odpowiedzi": None,
            "opublikowano": _iso(hours=-9),
        }
    )
    data.update(over)
    return data


#: Helpers every scenario here may use: the footer's pieces and a row's pieces, read one by one (the card
#: lays them out with flex gaps, so their `textContent` alone has no spaces between them).
PRE = r"""
const foot = () => $$(".foot > *").map((e) => e.textContent.trim()).join(" ");
// v11: the quiet line under the pager ("Sent 2 · Skipped 5 · Limits ⋯"), read piece by piece.
const quiet = () => $$(".ql > *").map((e) => e.textContent.trim()).filter(Boolean).join(" ");
const sheetText = () => $$(".sheet .sh-msg").map((e) => e.textContent.replace(/\s+/g, " ").trim()).join(" ");
"""


def _run(tmp_path, scenario, **kw):
    return _base_run(tmp_path, PRE + scenario, **kw)


# ---------------------------------------------------------------------------
# Skipped (N)
# ---------------------------------------------------------------------------


def test_skipped_is_a_quiet_disclosure_with_the_threads_and_the_agents_reasons(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        const tog = $('[data-tog="skipped"]');
        out.label = tog.textContent.trim();
        out.closed = [tog.getAttribute("aria-expanded"), $(".drawer").hasAttribute("data-open")];
        tog.click();
        out.opened = [tog.getAttribute("aria-expanded"), $(".drawer").getAttribute("data-open")];
        out.rows = $$('.dr[data-dr="skipped"] .rw').map((r) => ({
          badge: $(".badge", r).textContent, title: $(".t", r).textContent, where: $(".s", r).textContent.replace(/\\s+/g, " "),
          why: $(".w1", r).textContent, link: !!$("[data-skopen]", r),
        }));
        out.more = $(".rmore") ? $(".rmore").textContent : null;
        tog.click();
        out.closedAgain = [tog.getAttribute("aria-expanded"), $(".drawer").hasAttribute("data-open")];
        out.pages = $$("[data-panel]").length;
        return out;
        """,
        data=v9(),
    )
    assert got["label"] == "Skipped 5", "N is the total, not the length of the list"
    assert got["closed"] == ["false", False], "closed it takes no room"
    assert got["opened"] == ["true", "skipped"]
    assert [r["badge"] for r in got["rows"]] == ["R", "X", "R"]
    assert [r["title"] for r in got["rows"]][:2] == [
        "Is cold email dead for B2B tools?",
        "Hiring a growth marketer, DM me",
    ]
    assert got["rows"][0]["where"].startswith("r/SaaS")
    assert got["rows"][0]["why"] == "Eleven days old and the top answer already says what I would."
    assert got["rows"][2]["why"] == "You skipped this thread.", (
        "a person's skip has no agent reason"
    )
    assert all(r["link"] for r in got["rows"])
    assert got["more"] == "and 2 more"
    assert got["closedAgain"] == ["false", False]
    assert got["pages"] == 5, "the disclosure is not a page: the pager is the same"


def test_a_skipped_thread_opens_in_the_browser_and_only_through_the_host(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-tog="skipped"]').click();
        $$('[data-skopen]')[1].click();
        await sleep(20);
        return log.filter((m) => m.method === "ui/open-link").map((m) => m.params.url);
        """,
        data=v9(),
    )
    assert got == ["https://x.com/demo_recruiter/status/1000000000000000009"]


def test_a_thread_the_agent_gave_no_reason_for_says_so_and_a_card_without_skips_has_no_toggle(
    tmp_path,
) -> None:
    quiet = v9(pominiete=[], pominiete_lacznie=0)
    no_reason = v9()
    no_reason["pominiete"] = [dict(SKIPPED[0], powod=None)]
    no_reason["pominiete_lacznie"] = 1
    got = _run(
        tmp_path,
        "await open(); return { togs: $$('[data-tog]').map((b) => b.getAttribute('data-tog')) };",
        data=quiet,
    )
    assert got["togs"] == ["sent"], "nothing skipped, nothing to open"
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-tog="skipped"]').click();
        return { why: $(".w1").textContent, label: $('[data-tog="skipped"]').textContent.trim() };
        """,
        data=no_reason,
    )
    assert got == {"why": "No reason was recorded.", "label": "Skipped 1"}


def test_the_two_disclosures_open_one_at_a_time_and_the_card_still_works(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const open1 = () => $(".drawer").getAttribute("data-open");
        const out = [];
        $('[data-tog="skipped"]').click(); out.push(open1());
        $('[data-tog="sent"]').click(); out.push(open1());
        $('[data-tog="sent"]').click(); out.push(open1());
        next();
        out.push(idx());
        return out;
        """,
        data=v9(),
    )
    assert got == ["skipped", "sent", None, ["1"]]


def test_the_skipped_label_is_polish_in_polish_and_has_the_plural_of_a_count(tmp_path) -> None:
    got = _run(
        tmp_path,
        "await open(); return $('[data-tog=\"skipped\"]').textContent.trim() + '|' + $('[data-tog=\"sent\"]').textContent.trim();",
        data=v9(jezyk="pl"),
    )
    assert got == "Pominięte 5|Wysłane 2"


# ---------------------------------------------------------------------------
# Sent (N)
# ---------------------------------------------------------------------------


def test_sent_lists_the_replies_with_their_numbers_and_a_gap_is_never_a_zero(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const tog = $('[data-tog="sent"]');
        const out = { label: tog.textContent.trim() };
        tog.click();
        out.rows = $$('.dr[data-dr="sent"] .rw').map((r) => ({
          title: $(".t", r).textContent, nums: $$(".nums span", r).map((s) => s.textContent.trim()).join(" "), link: !!$("[data-snopen]", r),
        }));
        $$('[data-snopen]')[0].click();
        await sleep(20);
        out.opened = log.filter((m) => m.method === "ui/open-link").map((m) => m.params.url);
        return out;
        """,
        data=v9(),
    )
    assert got["label"] == "Sent 2"
    assert got["rows"][0]["title"].startswith("How do you find your first ten users")
    assert got["rows"][0]["nums"] == "Votes 41 Replies 3 visible"
    # The X reply: "Likes", and what nobody measured is "not measured" (and not confirmed).
    assert got["rows"][1]["nums"] == "Likes not measured Replies not measured not confirmed"
    assert "0" not in got["rows"][1]["nums"]
    assert all(r["link"] for r in got["rows"])
    assert got["opened"] == ["https://www.reddit.com/r/SideProject/comments/zz4/x/yy1/"]


# ---------------------------------------------------------------------------
# The dashboard is not in the flow
# ---------------------------------------------------------------------------


def test_there_is_no_approve_or_open_room_button_and_one_full_view_link_in_the_footer(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = { texts: [], rooms: $$("[data-room]").map((b) => [b.textContent.trim(), !!b.closest(".menu")]) };
        $("[data-room]").click();
        await sleep(20);
        for (let k = 0; k < $$("[data-panel]").length; k++) { out.texts.push($("#app").textContent); next(); }
        out.approve = $$("[data-approve]").length;
        out.opened = log.filter((m) => m.method === "ui/open-link").map((m) => m.params.url);
        return out;
        """,
        data=v9(),
    )
    # v11: the full view is behind the small menu of the quiet line, not in the footer.
    assert got["rooms"] == [["Open full view ↗", True]]
    assert got["approve"] == 0
    assert not any("Approve in Room" in t or "Open Room" in t for t in got["texts"])
    # It lands on the draft that is on screen.
    assert got["opened"] == [ROOM + "#szkic-" + REV_A]


def test_the_hosted_card_never_has_the_full_view_link_or_any_scope_control(tmp_path) -> None:
    hosted = v9(wysylka="reczna")
    hosted["szkice"] = [dict(d, konto=None) for d in hosted["szkice"]]
    got = _run(
        tmp_path,
        "await open(); return { room: $$('[data-room]').length, scope: $$('[data-scope]').length, foot: $('.foot').textContent.trim() };",
        data=hosted,
    )
    assert got["room"] == 0 and got["scope"] == 0
    assert got["foot"].startswith("Wenext never posts")


def test_a_host_that_cannot_carry_a_tool_call_gets_no_scope_control_and_the_old_edit(
    tmp_path,
) -> None:
    # The host says it has no `serverTools`: the card draws again without them.
    got = _run(
        tmp_path,
        """
        const orig = host.postMessage.bind(host);
        host.postMessage = (m) => {
          if (m.method === "ui/initialize") { log.push(m); return reply(m.id, { protocolVersion: "2026-01-26", hostCapabilities: {}, hostContext: {} }); }
          return orig(m);
        };
        await open();
        await sleep(30);
        const out = { scope: $$("[data-scope]").length, edit: $$("[data-edit]").length, fix: $$("[data-fix]", active()[0]).length };
        $("[data-fix]", active()[0]).click();
        await sleep(20);
        out.asked = log.filter((m) => m.method === "ui/message").length;
        out.textareas = $$("textarea").length;
        return out;
        """,
        data=v9(),
    )
    assert got == {"scope": 0, "edit": 0, "fix": 1, "asked": 1, "textareas": 0}


# ---------------------------------------------------------------------------
# Inline edit
# ---------------------------------------------------------------------------

NEW_TEXT = "Answering one live thread a day, by hand, beat every tool I tried."


def _revised(rev: str = REV_A2, text: str = NEW_TEXT, **over: Any) -> dict[str, Any]:
    draft = _draft(rev, OPP_A, tekst=text, odcisk=DIGEST2)
    draft.update(over)
    return {"structured": {"stan": "WYKONANE", "szkic": draft, "poprzednia": REV_A}}


def test_edit_turns_the_reply_into_a_textarea_with_save_cancel_and_the_agent_as_the_second_path(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        out.value = ta && ta.value;
        out.focused = doc().activeElement === ta;
        out.buttons = $$(".ed button", page()).map((b) => b.textContent.trim());
        out.label = $(".ed .pv-label", page()).textContent.trim();
        out.sendButton = $$('[data-act="send"]', page()).length;
        out.sentToHost = log.filter((m) => m.method === "ui/message" || m.method === "tools/call").length;
        // Cancel gives the reply and the buttons back, and calls nothing.
        $('[data-ed="cancel"]', page()).click();
        out.afterCancel = [$$("textarea", page()).length, $$('[data-act="send"]', page()).length, !!$(".pvb", page())];
        out.calls = calls().length;
        // "Ask the agent" is still there, and it is the old path: a message, no tool.
        $("[data-edit]", page()).click();
        $(".ed [data-fix]", page()).click();
        await sleep(20);
        out.asked = log.filter((m) => m.method === "ui/message").length;
        out.callsAfterAsk = calls().length;
        out.stillEditing = $$("textarea", page()).length;
        return out;
        """,
        data=v9(),
    )
    assert got["value"].startswith("First users rarely come from one place")
    assert got["focused"] is True
    assert got["buttons"] == ["Save", "Cancel", "Ask the agent to rewrite instead"]
    assert got["label"] == "Edit your reply"
    assert got["sendButton"] == 0, "while editing there is no Send on the page"
    assert got["sentToHost"] == 0, "opening the editor asks nobody"
    assert got["afterCancel"] == [0, 1, True]
    assert got["calls"] == 0
    assert got["asked"] == 1 and got["callsAfterAsk"] == 0 and got["stillEditing"] == 1


def test_escape_leaves_the_editor_like_cancel(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $("[data-edit]", active()[0]).click();
        const ta = $("textarea.edit");
        ta.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
        return { textareas: $$("textarea").length, calls: calls().length };
        """,
        data=v9(),
    )
    assert got == {"textareas": 0, "calls": 0}


def test_save_revises_through_the_host_and_the_next_send_is_about_the_new_text(tmp_path) -> None:
    got = _run(
        tmp_path,
        f"""
        await open();
        const out = {{}};
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        ta.value = "  {NEW_TEXT}  ";
        ta.dispatchEvent(new win.Event("input", {{ bubbles: true }}));
        $('[data-ed="save"]', page()).click();
        await sleep(40);
        out.revise = calls().filter((c) => c.name === "room.revise_draft");
        out.box = $("[data-box]", page()).getAttribute("data-box");
        out.text = $(".pt", page()).textContent;
        out.saved = $(".hint", page()).textContent.includes("Saved as a new version");
        out.textareas = $$("textarea").length;
        out.stayed = idx();
        // The send flow now works on the new revision and its digest, and never on the old one.
        $('[data-act="send"]', page()).click();
        await sleep(40);
        out.token = calls().filter((c) => c.name === "room.send_token").map((c) => c.args);
        out.draftCalls = calls().filter((c) => c.name === "room.send_draft").length;
        return out;
        """,
        tools={"room.revise_draft": _revised(), "room.send_token": TOKEN_OK},
        data=v9(),
    )
    assert got["revise"] == [
        {"name": "room.revise_draft", "args": {"revision_id": REV_A, "text": NEW_TEXT}}
    ], "the call carries the revision and the text, trimmed, and nothing else"
    assert got["box"] == REV_A2
    assert got["text"] == NEW_TEXT
    assert got["saved"] is True and got["textareas"] == 0 and got["stayed"] == ["0"]
    assert got["token"] == [{"revision_id": REV_A2, "odcisk": DIGEST2}]
    assert got["draftCalls"] == 0, "saving never sends, and neither does asking for a confirmation"


def test_a_saved_draft_that_belongs_to_no_opportunity_keeps_its_page(tmp_path) -> None:
    fresh = _draft(REV_B, None, tekst=NEW_TEXT, odcisk=DIGEST2, rodzaj="OWN_POST", tytul="T")
    fresh["revision_id"] = REV_A2
    got = _run(
        tmp_path,
        f"""
        await open();
        for (let k = 0; k < 2; k++) next();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        $("textarea.edit", page()).value = "{NEW_TEXT}";
        $('[data-ed="save"]', page()).click();
        await sleep(40);
        return {{ idx: idx(), box: $("[data-box]", page()).getAttribute("data-box"), count: $(".count").textContent }};
        """,
        tools={
            "room.revise_draft": {
                "structured": {"stan": "WYKONANE", "szkic": fresh, "poprzednia": REV_C}
            }
        },
        data=v9(),
    )
    assert got == {"idx": ["2"], "box": REV_A2, "count": "3 of 5"}


@pytest.mark.parametrize(
    ("code", "extra", "words"),
    [
        ("DLUGA_PAUZA", {}, "Use a period, comma or colon"),
        ("DLUGOSC_X", {"waga": 312, "limit": 280}, "This text counts 312 on X, which allows 280."),
        ("STAN", {}, "not waiting for approval"),
        ("NIEAKTUALNY", {}, "no longer waiting"),
    ],
)
def test_a_refused_save_says_why_keeps_the_text_and_changes_nothing(
    tmp_path, code, extra, words
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        ta.value = "A new text that the server will not take.";
        ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        $('[data-ed="save"]', page()).click();
        await sleep(40);
        const out = {
          msg: $(".edmsg", page()).textContent, kept: $("textarea.edit", page()).value,
          box: $("[data-box]", page()).getAttribute("data-box"), buttons: $$(".ed button", page()).map((b) => b.disabled),
        };
        // Typing clears the message.
        const again = $("textarea.edit", page());
        again.value += "!"; again.dispatchEvent(new win.Event("input", { bubbles: true }));
        out.cleared = $(".edmsg", page()).textContent;
        return out;
        """,
        tools={
            "room.revise_draft": {
                "structured": {"stan": "NIEDOSTEPNE", "kod": code, "powod": "po polsku", **extra}
            }
        },
        data=v9(),
    )
    assert words in got["msg"]
    assert got["kept"] == "A new text that the server will not take."
    assert got["box"] == REV_A, "the draft on screen is still the one it was"
    assert got["buttons"] == [False, False, False]
    assert got["cleared"] == ""


def test_an_unchanged_or_empty_text_is_refused_on_the_card_without_asking_the_server(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        $('[data-ed="save"]', page()).click();
        await sleep(20);
        const out = { same: $(".edmsg", page()).textContent };
        const ta = $("textarea.edit", page());
        ta.value = "   "; ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        $('[data-ed="save"]', page()).click();
        await sleep(20);
        out.empty = $(".edmsg", page()).textContent;
        out.calls = calls().length;
        return out;
        """,
        data=v9(),
    )
    assert got == {
        "same": "Nothing changed in the text.",
        "empty": "The text is empty.",
        "calls": 0,
    }


def test_a_host_that_does_not_answer_a_save_leaves_the_editor_open_with_the_text(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        ta.value = "Another wording of the same reply."; ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        $('[data-ed="save"]', page()).click();
        await sleep(40);
        return { msg: $(".edmsg", page()).textContent, kept: $("textarea.edit", page()).value };
        """,
        tools={},  # the fake host answers an unknown tool with an error
        data=v9(),
    )
    assert "Could not reach Wenext" in got["msg"]
    assert got["kept"] == "Another wording of the same reply."


def test_an_x_reply_shows_how_long_x_counts_it_and_warns_over_280(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        next();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const out = { start: $(".edcount", page()).textContent };
        const ta = $("textarea.edit", page());
        ta.value = "a".repeat(281); ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        out.over = [$(".edcount", page()).textContent, $(".edcount", page()).hasAttribute("data-over")];
        ta.value = "see https://example.com/a/very/long/address/that/counts/23 ok";
        ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        out.url = $(".edcount", page()).textContent;
        ta.value = "ab\\u{1F600}"; ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        out.emoji = $(".edcount", page()).textContent;
        return out;
        """,
        data=v9(),
    )
    assert got["start"] == "40 / 280"
    assert got["over"] == ["281 / 280", True]
    assert got["url"] == "30 / 280", "a link is 23 whatever its length"
    assert got["emoji"] == "4 / 280"


def test_an_own_post_is_edited_by_its_body_and_the_title_stays_where_it_is(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        next(); next();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        return { label: $(".ed .pv-label", page()).textContent.trim(), title: $(".edt", page()).textContent, value: $("textarea.edit", page()).value };
        """,
        data=v9(),
    )
    assert got == {
        "label": "Edit your post",
        "title": "I shipped a tiny tool",
        "value": "The first ten users came from replies.",
    }


def test_the_editor_is_not_offered_to_the_hosted_card_which_asks_the_agent_as_before(
    tmp_path,
) -> None:
    hosted = v9(wysylka="reczna")
    hosted["szkice"] = [
        dict(d, konto=None, watek_url=None, intent_url=None) for d in hosted["szkice"]
    ]
    got = _run(
        tmp_path,
        """
        await open();
        const out = { edit: $$("[data-edit]").length, fix: $$("[data-fix]", active()[0]).length };
        $("[data-fix]", active()[0]).click();
        await sleep(20);
        out.asked = log.filter((m) => m.method === "ui/message").length;
        return out;
        """,
        data=hosted,
    )
    assert got == {"edit": 0, "fix": 1, "asked": 1}


def test_the_editor_survives_a_redraw_with_the_text_typed_so_far(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        ta.value = "Half of a better reply"; ta.dispatchEvent(new win.Event("input", { bubbles: true }));
        toWin({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { locale: "pl-PL" } });
        await sleep(20);
        return { value: $("textarea.edit", page()).value, label: $(".ed .pv-label", page()).textContent.trim() };
        """,
        data=v9(),
    )
    assert got == {"value": "Half of a better reply", "label": "Edytuj odpowiedź"}


# ---------------------------------------------------------------------------
# Pause, resume and limits: asking changes nothing, only "Yes" does, with the token only
# ---------------------------------------------------------------------------

TOK = {"wenext/potwierdzenie": SCOPE_TOKEN}
PLAN_PAUSE = {"rodzaj": "WSTRZYMAJ", "tryb": "REVIEW"}
PLAN_RESUME = {
    "rodzaj": "WZNOW",
    "tryb": "REVIEW",
    "wygasa": _iso(days=14),
    "konta": [
        {"platforma": "REDDIT", "konto": "demo_founder"},
        {"platforma": "X", "konto": "demo_founder_x"},
    ],
}
PLAN_SIGN = {
    "rodzaj": "USTAW",
    "tryb": "REVIEW",
    "wygasa": _iso(days=7),
    "limity": {"godzina": 4, "doba": 20, "wlasne_doba": 1},
    "konta": PLAN_RESUME["konta"],
    "dzialania": ["REPLY", "OWN_POST", "RESEARCH"],
    "poprzedni_tryb": "REVIEW",
}


def _asked(plan: dict[str, Any]) -> dict[str, Any]:
    return {"structured": {"stan": "WYKONANE", "zakres": plan}, "meta": TOK}


def _done(**scope: Any) -> dict[str, Any]:
    return {"structured": {"stan": "WYKONANE", "zakres": _scope(**scope)}}


def test_the_footer_says_the_scope_in_one_line_and_offers_pause(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = { quiet: quiet(), foot: $(".foot") ? foot() : null, scope: $$("[data-scope]").map((b) => b.getAttribute("data-scope")) };
        // The scope in a line, what Pause is behind, and the full view are NOT in the default view (v11): the first is in the Limits
        // panel, the other two behind the small menu.
        out.menu = [!!$(".menu[data-open]"), $$(".menu button").map((b) => b.textContent.trim())];
        $('[data-menu]').click();
        out.menuOpen = !!$(".menu[data-open]");
        out.expanded = $('[data-menu]').getAttribute("aria-expanded");
        $('[data-scope="panel"]').click();
        out.now = $(".nowl") ? $(".nowl").textContent : null;
        return out;
        """,
        data=v9(),
    )
    assert got["quiet"] == "Sent 2 · Skipped 5 · Limits ⋯", "one quiet line"
    assert got["foot"] is None, "nothing needs a person, so there is no footer"
    assert got["scope"] == ["panel", "pause"]
    assert got["menu"] == [False, ["Pause Wenext", "Open full view ↗"]]
    assert got["menuOpen"] is True and got["expanded"] == "true"
    assert re.fullmatch(r"2 accounts · 4/h · 20/day · until [A-Z][a-z]{2} \d{1,2}", got["now"]), got["now"]


def test_pause_asks_first_shows_the_sentence_and_pauses_only_on_yes_with_the_token_alone(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        $('[data-scope="pause"]').click();
        await sleep(40);
        out.afterAsk = calls().map((c) => [c.name, c.args]);
        out.sheet = $("#app").hasAttribute("data-sheet");
        out.text = sheetText();
        out.buttons = $$(".sheet button").map((b) => b.textContent.trim());
        out.pageStillThere = $$("[data-panel]").length;
        // Cancel: nothing was paused.
        $('[data-scact="cancel"]').click();
        out.afterCancel = [$("#app").hasAttribute("data-sheet"), calls().map((c) => c.name)];
        // Again, and this time Yes.
        $('[data-scope="pause"]').click();
        await sleep(40);
        $('[data-scact="go"]').click();
        await sleep(40);
        out.afterYes = calls().map((c) => [c.name, c.args]);
        out.done = sheetText();
        $('[data-scact="done"]').click();
        await sleep(20);
        out.footer = foot();
        out.scope = $$("[data-scope]").map((b) => b.getAttribute("data-scope"));
        out.sheetAfter = $("#app").hasAttribute("data-sheet");
        return out;
        """,
        tools={
            "room.scope_token": _asked(PLAN_PAUSE),
            "room.pause": _done(stan="WSTRZYMANY"),
        },
        data=v9(),
    )
    assert got["afterAsk"] == [["room.scope_token", {"akcja": "wstrzymaj"}]], (
        "asking sends the kind of action and nothing else, and nothing is paused yet"
    )
    assert got["sheet"] is True and got["pageStillThere"] == 5
    assert got["text"].startswith("Pause Wenext in this project?")
    assert got["buttons"] == ["Yes, pause", "Cancel"]
    assert got["afterCancel"] == [False, ["room.scope_token"]]
    assert got["afterYes"][-1] == ["room.pause", {"token": SCOPE_TOKEN}], (
        "the token and nothing else"
    )
    assert [c[0] for c in got["afterYes"]].count("room.pause") == 1
    assert "Paused" in got["done"]
    assert "Paused. Resume" in got["footer"] and got["scope"] == ["resume"]
    assert got["sheetAfter"] is False


def test_resume_is_a_click_behind_a_token_and_an_autonomous_scope_says_so(tmp_path) -> None:
    paused = v9(zakres=_scope("WSTRZYMANY"))
    got = _run(
        tmp_path,
        """
        await open();
        const out = { footer: foot(), scope: $$("[data-scope]").map((b) => b.getAttribute("data-scope")) };
        $('[data-scope="resume"]').click();
        await sleep(40);
        out.sentence = $(".sheet .sh-msg").textContent.replace(/\\s+/g, " ").trim();
        out.resumedBeforeYes = calls().filter((c) => c.name === "room.resume").length;
        $('[data-scact="go"]').click();
        await sleep(40);
        out.resume = calls().filter((c) => c.name === "room.resume").map((c) => c.args);
        $('[data-scact="done"]').click();
        await sleep(20);
        out.after = $$("[data-scope]").map((b) => b.getAttribute("data-scope"));
        return out;
        """,
        tools={
            "room.scope_token": _asked(PLAN_RESUME),
            "room.resume": _done(),
        },
        data=paused,
    )
    assert "Paused. Resume" in got["footer"] and got["scope"] == ["resume"]
    assert got["sentence"].startswith(
        "Resume Wenext? It may write again from u/demo_founder and @demo_founder_x."
    )
    assert "waits for your click" in got["sentence"]
    assert got["resumedBeforeYes"] == 0
    assert got["resume"] == [{"token": SCOPE_TOKEN}]
    assert got["after"] == ["panel", "pause"]
    auto = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="resume"]').click();
        await sleep(40);
        return $(".sheet .sh-msg").textContent.replace(/\\s+/g, " ").trim();
        """,
        tools={"room.scope_token": _asked(dict(PLAN_RESUME, tryb="AUTONOMOUS"))},
        data=paused,
    )
    assert "autonomous" in auto and "without asking you first" in auto


def test_the_limits_panel_is_prefilled_signs_only_after_yes_and_sends_the_token_alone(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        $('[data-scope="panel"]').click();
        out.sheet = $("#app").hasAttribute("data-sheet");
        out.accounts = $$('[data-sc="acc"]').map((i) => [i.value, i.checked]);
        out.actions = $$('[data-sc="act"]').map((i) => [i.value, i.checked]);
        // v11: a block of three numbers for EACH account, each opened on what binds that account now.
        out.numbers = $$("[data-pa]").map((b) => ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`, b).value));
        out.who = $$("[data-pa] .pa-who").map((n) => n.textContent);
        out.days = $$('[data-sc="dni"]').map((i) => [i.value, i.checked]);
        out.note = $(".sh-note").textContent;
        out.labels = $$(".sf .lab").map((l) => l.textContent);
        // The person raises the pace of the FIRST account and picks a fortnight, then Save.
        const set = (k, v) => { const el = $(`[data-pa] [data-sc="${k}"]`); el.value = v; el.dispatchEvent(new win.Event("input", { bubbles: true })); };
        set("godzina", "5"); set("doba", "30");
        $$('[data-sc="dni"]')[1].click();
        $('[data-scact="save"]').click();
        await sleep(40);
        out.asked = calls().map((c) => [c.name, c.args]);
        out.sentence = $(".sheet .sh-msg").textContent.replace(/\\s+/g, " ").trim();
        out.signedBeforeYes = calls().filter((c) => c.name === "room.sign_scope").length;
        $('[data-scact="go"]').click();
        await sleep(40);
        out.signed = calls().filter((c) => c.name === "room.sign_scope").map((c) => c.args);
        $('[data-scact="done"]').click();
        await sleep(20);
        out.footer = quiet();
        $('[data-scope="panel"]').click();
        out.now = $(".nowl") ? $(".nowl").textContent : null;
        return out;
        """,
        tools={
            "room.scope_token": _asked(PLAN_SIGN),
            "room.sign_scope": _done(limity={"godzina": 5, "doba": 30, "wlasne_doba": 1}),
        },
        data=v9(),
    )
    assert got["sheet"] is True
    assert got["accounts"] == [[ACC_R, True], [ACC_X, True]], "ticked from the project's accounts"
    assert got["actions"] == [["REPLY", True], ["OWN_POST", True], ["RESEARCH", True]]
    assert got["numbers"] == [["4", "20", "1"], ["4", "20", "1"]], (
        "the numbers that bind now, per account"
    )
    assert got["who"] == ["u/demo_founder", "@demo_founder_x"]
    assert got["days"] == [["7", True], ["14", False]]
    assert got["note"].startswith(
        "Each account has its own pace. Replies and own posts are counted"
    )
    assert "New account? One an hour and six a day is the safe pace." in got["note"]
    assert got["labels"] == ["Now", "Accounts", "Actions", "Pace", "For"]
    assert got["asked"] == [
        [
            "room.scope_token",
            {
                "akcja": "podpisz",
                "konta": [ACC_R, ACC_X],
                "dzialania": ["REPLY", "OWN_POST", "RESEARCH"],
                "limity": {},
                "limity_konta": {
                    ACC_R: {"godzina": 5, "doba": 30, "wlasne_doba": 1},
                    ACC_X: {"godzina": 4, "doba": 20, "wlasne_doba": 1},
                },
                "dni": 14,
            },
        ]
    ]
    assert got["sentence"].startswith(
        "Sign this scope? Wenext can write from u/demo_founder and @demo_founder_x"
    )
    assert "Every text still waits for your click." in got["sentence"]
    assert got["signedBeforeYes"] == 0
    assert got["signed"] == [{"token": SCOPE_TOKEN}], (
        "the redeeming call carries the token and nothing else"
    )
    assert got["footer"].endswith("Limits ⋯") and "5/h · 30/day" in got["now"]


def test_an_empty_form_is_refused_on_the_card_and_the_server_is_not_asked(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = {};
        $('[data-scope="panel"]').click();
        $$('[data-sc="acc"]').forEach((i) => i.click());
        $('[data-scact="save"]').click();
        out.noAccount = $(".sh-err") && $(".sh-err").textContent;
        $$('[data-sc="acc"]').forEach((i) => i.click());
        $$('[data-sc="act"]').forEach((i) => i.click());
        $('[data-scact="save"]').click();
        out.noAction = $(".sh-err") && $(".sh-err").textContent;
        out.calls = calls().length;
        // Ticking something clears the message, and the panel is still the panel.
        $$('[data-sc="act"]')[0].click();
        out.cleared = $(".sh-err") === null;
        out.phase = $(".sheet").getAttribute("data-p");
        return out;
        """,
        data=v9(),
    )
    assert got["noAccount"] == "Pick at least one account."
    assert got["noAction"] == "Pick at least one action."
    assert got["calls"] == 0
    assert got["cleared"] is True and got["phase"] == "panel"


def test_a_refusal_of_a_new_scope_goes_back_to_the_form_with_what_was_typed(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        const el = $('[data-sc="doba"]'); el.value = "33"; el.dispatchEvent(new win.Event("input", { bubbles: true }));
        $('[data-scact="save"]').click();
        await sleep(40);
        const out = { phase: $(".sheet").getAttribute("data-p"), text: $(".sheet .sh-msg").textContent };
        $('[data-scact="retry"]').click();
        out.back = [$(".sheet").getAttribute("data-p"), $('[data-sc="doba"]').value];
        return out;
        """,
        tools={
            "room.scope_token": _refusal("LIMIT_POZA_GRANICA"),
        },
        data=v9(),
    )
    assert got["phase"] == "refused" and "above what the card signs" in got["text"]
    assert got["back"] == ["panel", "33"]


def test_a_stale_confirmation_of_a_scope_change_is_a_refusal_and_never_a_second_call(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="pause"]').click();
        await sleep(40);
        $('[data-scact="go"]').click();
        await sleep(40);
        const out = { phase: $(".sheet").getAttribute("data-p"), text: $(".sheet .sh-msg").textContent };
        out.pauses = calls().filter((c) => c.name === "room.pause").length;
        // The way out ends at a NEW question, never at the change.
        $('[data-scact="retry"]').click();
        await sleep(40);
        out.afterRetry = [$(".sheet").getAttribute("data-p"), calls().filter((c) => c.name === "room.pause").length];
        return out;
        """,
        tools={
            "room.scope_token": _asked(PLAN_PAUSE),
            "room.pause": _refusal("ZAKRES_ZMIENIONY"),
        },
        data=v9(),
    )
    assert got["phase"] == "refused" and "scope changed" in got["text"]
    assert got["pauses"] == 1
    assert got["afterRetry"] == ["confirm", 1]


def test_a_host_that_gives_no_confirmation_cannot_change_the_scope(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="pause"]').click();
        await sleep(40);
        return { phase: $(".sheet").getAttribute("data-p"), text: $(".sheet .sh-msg").textContent, calls: calls().map((c) => c.name) };
        """,
        tools={
            "room.scope_token": {"structured": {"stan": "WYKONANE", "zakres": PLAN_PAUSE}}
        },  # no `_meta`
        data=v9(),
    )
    assert got["phase"] == "refused" and "did not pass the card a confirmation" in got["text"]
    assert got["calls"] == ["room.scope_token"]


def test_an_expired_scope_offers_renewal_and_no_scope_offers_limits(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        const out = { foot: foot(), scope: $$("[data-scope]").map((b) => b.getAttribute("data-scope")) };
        $('[data-scope="renew"]').click();
        out.days = $$('[data-sc="dni"]').map((i) => [i.value, i.checked]);
        out.numbers = ["godzina", "doba"].map((k) => $(`[data-sc="${k}"]`).value);
        return out;
        """,
        data=v9(zakres=_scope("WYGASL")),
    )
    assert got["foot"] == "Scope expired. Renew for 7 days"
    assert got["scope"] == ["renew"]
    assert got["days"] == [["7", True], ["14", False]]
    none = _run(
        tmp_path,
        """
        await open();
        const out = { scope: $$("[data-scope]").map((b) => b.getAttribute("data-scope")), quiet: quiet() };
        $('[data-scope="panel"]').click();
        out.numbers = ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`).value);
        return out;
        """,
        data=v9(zakres=_scope("BRAK", limity={"godzina": 1, "doba": 6, "wlasne_doba": 1})),
    )
    assert none["scope"] == ["panel"] and "Set limits" in none["quiet"]
    assert none["numbers"] == ["2", "10", "1"], (
        "no scope yet: the panel's own conservative defaults"
    )


# ---------------------------------------------------------------------------
# A refusal of a send that a scope change fixes offers the change inline
# ---------------------------------------------------------------------------


def _send_until_refused(code: str) -> str:
    return """
        await open();
        const out = {};
        const page = () => active()[0];
        $('[data-act="send"]', page()).click();
        await sleep(40);
        $('[data-act="go"]', page()).click();
        await sleep(40);
        out.box = $(".send", page()).getAttribute("data-p");
        out.buttons = $$(".send button", page()).map((b) => b.textContent.trim());
        out.text = $(".send .msg", page()).textContent.trim();
    """


@pytest.mark.parametrize(
    ("code", "label", "fix"),
    [
        ("BUDZET", "Raise limits", "raise"),
        ("ZAKRES_INNY_TRYB", "Change scope", "review"),
        ("ZAKRES", "Change scope", "review"),
        ("ZAKRES_WSTRZYMANY", "Resume Wenext", "resume"),
    ],
)
def test_a_refusal_a_scope_change_fixes_offers_it_instead_of_a_trip_to_the_dashboard(
    tmp_path, code, label, fix
) -> None:
    got = _run(
        tmp_path,
        _send_until_refused(code)
        + """
        out.presence = $$('[data-act="presence"]').length;
        $(`.send [data-scope]`, page()).click();
        await sleep(40);
        out.after = [$("#app").hasAttribute("data-sheet"), $(".sheet").getAttribute("data-p")];
        out.numbers = $$('[data-sc="godzina"],[data-sc="doba"]').map((i) => i.value);
        return out;
        """,
        tools={
            "room.send_token": TOKEN_OK,
            "room.send_draft": _refusal(code),
            "room.scope_token": _asked(PLAN_RESUME),
        },
        data=v9(),
    )
    assert got["box"] == "refused"
    assert got["buttons"][0] == label
    assert got["buttons"][1:] == ["Try again", "Close"]
    assert got["presence"] == 0, "the inline fix replaces the link to the dashboard"
    if fix == "resume":
        assert got["after"] == [True, "confirm"], (
            "a paused scope is resumed behind its own confirmation"
        )
    else:
        assert got["after"] == [True, "panel"]
    if fix == "raise":
        assert got["numbers"] == ["5", "30", "5", "30"], (
            "opened with the pace of every account raised, so Save is one honest step"
        )


def test_a_refusal_no_scope_change_fixes_keeps_the_link_it_had(tmp_path) -> None:
    got = _run(
        tmp_path,
        _send_until_refused("OKNO")
        + """
        out.fix = $$(".send [data-scope]", page()).length;
        out.presence = $$('[data-act="presence"]', page()).length;
        return out;
        """,
        tools={"room.send_token": TOKEN_OK, "room.send_draft": _refusal("OKNO")},
        data=v9(),
    )
    assert got["box"] == "refused" and got["fix"] == 0 and got["presence"] == 1


def test_after_the_scope_is_fixed_the_refusal_is_gone_and_send_is_one_click_away(tmp_path) -> None:
    got = _run(
        tmp_path,
        _send_until_refused("BUDZET")
        + """
        $(`.send [data-scope="raise"]`, page()).click();
        $('[data-scact="save"]').click();
        await sleep(40);
        $('[data-scact="go"]').click();
        await sleep(40);
        $('[data-scact="done"]').click();
        await sleep(30);
        out.send = $$('[data-act="send"]', page()).length;
        out.refused = $$('.send[data-p="refused"]').length;
        out.sendCalls = calls().filter((c) => c.name === "room.send_draft").length;
        return out;
        """,
        tools={
            "room.send_token": TOKEN_OK,
            "room.send_draft": _refusal("BUDZET"),
            "room.scope_token": _asked(PLAN_SIGN),
            "room.sign_scope": _done(),
        },
        data=v9(),
    )
    assert got["send"] == 1 and got["refused"] == 0
    assert got["sendCalls"] == 1, "fixing the scope sent nothing: the person presses Send again"


# ---------------------------------------------------------------------------
# THE INVARIANT: a scope changes only from the "Yes" click, with a token the server minted
# ---------------------------------------------------------------------------

WALK = r"""
let seed = 20261011;
const rnd = (n) => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed % n; };
const names = ["room.pause", "room.resume", "room.sign_scope"];
let minted = 0, redeemed = [], yes = 0, bad = [];
const orig = host.postMessage.bind(host);
host.postMessage = (m) => {
  if (m.method === "tools/call" && names.indexOf(m.params.name) >= 0) {
    const keys = Object.keys(m.params.arguments);
    if (keys.length !== 1 || keys[0] !== "token") bad.push("args " + keys.join(","));
    redeemed.push(m.params.name);
  }
  if (m.method === "tools/call" && m.params.name === "room.scope_token") minted++;
  return orig(m);
};
await open();
const actions = [
  () => $('[data-scope="pause"]') && $('[data-scope="pause"]').click(),
  () => $('[data-scope="resume"]') && $('[data-scope="resume"]').click(),
  () => $('[data-scope="panel"]') && $('[data-scope="panel"]').click(),
  () => $('[data-scact="save"]') && $('[data-scact="save"]').click(),
  () => $('[data-scact="back"]') && $('[data-scact="back"]').click(),
  () => $('[data-scact="retry"]') && $('[data-scact="retry"]').click(),
  () => $('[data-scact="cancel"]') && $('[data-scact="cancel"]').click(),
  () => $('[data-scact="close"]') && $('[data-scact="close"]').click(),
  () => $('[data-scact="done"]') && $('[data-scact="done"]').click(),
  () => { const b = $('[data-scact="go"]'); if (b) { yes++; b.click(); } },
  () => next(), () => prev(),
  () => key("Enter"), () => key("ArrowRight"),
  () => toWin({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: FIX } }),
];
for (let k = 0; k < 400; k++) {
  actions[rnd(actions.length)]();
  if (rnd(3) === 0) await sleep(8);
}
await sleep(60);
return { minted, redeemed: redeemed.length, yes, bad };
"""


def test_the_scope_changes_only_from_the_yes_button_and_only_with_the_token(tmp_path) -> None:
    got = _run(
        tmp_path,
        WALK,
        tools={
            "room.scope_token": [
                _asked(PLAN_PAUSE),
                _asked(PLAN_RESUME),
                _refusal("ZAKRES_KOLEJKA"),
                _asked(PLAN_SIGN),
            ],
            "room.pause": [_done(stan="WSTRZYMANY"), _refusal("POTWIERDZENIE")],
            "room.resume": [_done(), _refusal("POTWIERDZENIE")],
            "room.sign_scope": [_done(), _refusal("ZAKRES_ZMIENIONY")],
        },
        data=v9(),
    )
    assert got["yes"] > 0, "the walk reached the confirmation: a vacuous pass would prove nothing"
    assert got["redeemed"] <= got["yes"], "a scope tool was called without a click on Yes"
    assert got["redeemed"] <= got["minted"], "more redeeming calls than questions asked"
    assert got["bad"] == [], "the redeeming call carries the token and nothing else"


def test_the_source_names_each_tool_that_changes_the_scope_once_and_the_machine_is_pure() -> None:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    script = html[html.index("<script>") : html.index("</script>")]
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", script, flags=re.S)
    for name in ("room.pause", "room.resume", "room.sign_scope", "room.revise_draft"):
        assert code.count(f'"{name}"') == 1, name
    machine = script[script.index("/*SCOPE-BEGIN*/") : script.index("/*SCOPE-END*/")]
    pure = re.sub(r"/\*.*?\*/|//[^\n]*", "", machine, flags=re.S)
    for forbidden in ("document", "window", "navigator", "setTimeout", "fetch(", "rpc("):
        assert forbidden not in pure, forbidden
    assert "function makeScoper(env)" in pure
    # The tools that change the scope are named inside `go`, which `dispatch` reaches from "go" alone.
    go = pure[pure.index("function go()") : pure.index("function dispatch(")]
    assert '"room.pause"' in go and '"room.resume"' in go and '"room.sign_scope"' in go
    assert pure.count("go()") == 2  # the definition and the one call
    assert 'name === "go") go()' in pure
    # The one timer is the wait for a busy queue (a re-ask, never a change), and it is injected, not called.
    assert "setTimeout" not in pure and pure.count("env.later(") == 1
    ask = pure[pure.index("function ask(") : pure.index("function go()")]
    assert "env.later(" in ask and "room.pause" not in ask and "room.sign_scope" not in ask
    handler = script[script.index('document.addEventListener("click"') :]
    assert 'scopeButton(b.getAttribute("data-scact"))' in handler


def test_the_new_files_and_the_card_have_no_long_dash_and_the_words_are_in_both_languages() -> None:
    assert not LONG_DASHES.search(apps_ui.OPPORTUNITIES_HTML)
    for lang in (PL, EN):
        for key in (
            "sk.label",
            "sn.label",
            "ed.save",
            "sc.pause",
            "sc.resume",
            "sp.raise",
            "ft.full",
        ):
            assert key in lang
    assert PL["sk.label"] == "Pominięte ({n})" and EN["sk.label"] == "Skipped ({n})"
    assert set(PL) == set(EN)
    assert (
        OPP_B and OPP_C and OPP_D and DIGEST
    )  # the pager's fixture is the one this file builds on


# ---------------------------------------------------------------------------
# The editor's X counter is `community_os_contracts.x_text`, ported: same numbers on the same texts
# ---------------------------------------------------------------------------

FOUNDER_POST = (
    "I'm good at building things. Getting people to use them, not so much.\n\n"
    'So I\'m building Wenext: type "find my first users" in the Codex chat where you built your '
    "app. It finds people on Reddit who need it and drafts your reply.\n\n"
    "Nothing posts without your click. getwenext.com"
)
VECTORS = [
    "",
    "hello",
    "getwenext.com",
    "x.co",
    "www.getwenext.com",
    "see getwenext.com now",
    "https://example.com/" + "p" * 200,
    "http://example.com:8080/a?b=c#d",
    "https://getwenext.com/page.",
    "(see getwenext.com/x).",
    "see getwenext.com.",
    '"getwenext.com/x"',
    "getwenext.com/x" + chr(0x2026),
    "żółć.pl",
    "żółć",
    "a\nb",
    "\n\n",
    '"find my first users"',
    "日本",
    "\U0001f600",
    "a\U0001f600b",
    "e.g. 3.14 is fine",
    "v1.2.3",
    "10.10.2026",
    "U.S.A",
    "Next.js",
    "a" * 280,
    "a" * 281,
    "日" * 141,
    "日" * 140,
    "https://example.com/" + "p" * 270,
    "Zażółć gęślą jaźń, 日本語 \U0001f680!",
    FOUNDER_POST,
    FOUNDER_POST.removesuffix(" getwenext.com"),
]


def _fuzz() -> list[str]:
    import random

    pieces = [
        "getwenext.com", "x.co", "https://example.com/a?b=c.", "Next.js", "żółć.pl", "日本", "\U0001f600",
        " ", ".", ",", "a", "ab", "\n", "(see ", ")", chr(0x2026), "e.g.", "3.14", "v1.2.3",
        "www.getwenext.com/x).", '"', "'", chr(0x2019), "Zażółć", "_", "-", "http://a.b/c", "co.uk.",
        "foo_bar.baz", "1.5x", "a.b", "Hello.World", "https://", "://", "/path/", "\U0001f468‍\U0001f469",
    ]  # fmt: skip
    rng = random.Random(20261011)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 40))) for _ in range(600)]


def test_the_editors_counter_is_the_shared_counter_ported_on_every_vector_and_a_fuzz(
    tmp_path,
) -> None:
    import json
    import subprocess

    from community_os_contracts.x_text import X_MAX_WEIGHT, x_length_problem, x_weighted_length

    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    script = html[html.index("<script>") : html.index("</script>")]
    block = script[script.index("/*XLEN-BEGIN*/") : script.index("/*XLEN-END*/")]
    texts = [*VECTORS, *_fuzz()]
    (tmp_path / "texts.json").write_text(json.dumps(texts), encoding="utf-8")
    program = tmp_path / "xlen.js"
    program.write_text(
        block + "\nconst texts = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));\n"
        "process.stdout.write(JSON.stringify(texts.map((t) => [xWeight(t), xCount(t)])));\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        ["node", str(program), str(tmp_path / "texts.json")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    for text, (weight, count) in zip(texts, got, strict=True):
        problem = x_length_problem(text)
        assert weight == x_weighted_length(text), repr(text)
        # The number the server would quote: the plain length when it is over, else the weighted one.
        assert count == (problem[1] if problem else x_weighted_length(text)), repr(text)
    assert x_length_problem(FOUNDER_POST) == ("weighted", 285)
    assert got[VECTORS.index(FOUNDER_POST)] == [285, 285], (
        "the editor shows what the server refuses"
    )
    assert len(FOUNDER_POST) == 275 and X_MAX_WEIGHT == 280


def test_the_editor_shows_285_for_the_founders_post_and_marks_it_over(tmp_path) -> None:
    got = _run(
        tmp_path,
        f"""
        await open();
        next();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        const ta = $("textarea.edit", page());
        ta.value = {FOUNDER_POST!r};
        ta.dispatchEvent(new win.Event("input", {{ bubbles: true }}));
        return [$(".edcount", page()).textContent, $(".edcount", page()).hasAttribute("data-over")];
        """,
        data=v9(),
    )
    assert got == ["285 / 280", True]


# ---------------------------------------------------------------------------
# Motion: the first-drawing reveal, the shine, the beam and the label swap (none of it under reduced motion)
# ---------------------------------------------------------------------------

#: The harness runs the card's clock a hundred times faster; this slows it to ten times (the reveal lasts
#: about 90 ms of real time), so a test can look at the reveal while it is on.
SLOW = "(w) => { const fast = w.setTimeout; w.setTimeout = (fn, ms) => fast(fn, Math.round((ms || 0) * 10)); }"
REDUCED = "(w) => { w.matchMedia = (q) => ({ matches: /reduce/.test(q), media: q, addListener() {}, removeListener() {} }); }"


def test_the_first_drawing_opens_with_a_reveal_over_content_that_is_already_there(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open(__SLOW__);
        const out = {};
        const main = $(".main");
        out.reveal = main.hasAttribute("data-reveal");
        const layer = $(".rv");
        out.hidden = layer.getAttribute("aria-hidden");
        out.head = [$(".rv-a", layer).textContent.trim(), $(".rv-b > span:last-child", layer).textContent.trim()];
        out.tiles = $$(".tile", layer).map((t) => [$(".tt", t).textContent.trim(), t.className]);
        out.icons = $$(".rv-b .stack .av2", layer).length;
        // The real content is in the page from the start, and the pager is the one it always was.
        out.pages = $$("[data-panel]").length; out.h1 = $("h1").textContent.trim().slice(0, 8);
        await sleep(150);                      // the reveal is over after about 90 ms here
        out.after = [main.hasAttribute("data-reveal"), $$(".rv").length];
        // Drawn again (another language): no second reveal, once per session.
        toWin({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { locale: "pl-PL" } });
        await sleep(20);
        out.again = [$(".main").hasAttribute("data-reveal"), $$(".rv").length];
        return out;
        """.replace("__SLOW__", SLOW),
        data=v9(),
    )
    assert got["reveal"] is True and got["hidden"] == "true"
    assert got["head"][0] == "Searching threads"
    assert got["head"][1] == "Read 9 threads on r/SideProject, X, r/SaaS +1"
    assert len(got["tiles"]) == 4, "three people and the tile of what was skipped"
    assert got["tiles"][0][0].startswith("How do you find your first ten users")
    assert got["tiles"][3][0] == "+5 skipped"
    assert got["icons"] == 4
    assert got["pages"] == 5 and got["h1"] == "3 people"
    assert got["after"] == [False, 0]
    assert got["again"] == [False, 0]


def test_any_click_ends_the_reveal_and_the_skipped_tile_opens_what_was_skipped(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open(__SLOW__);
        const out = { before: $$(".rv").length };
        $(".rv [data-rvskip]").click();
        out.drawer = $(".drawer").getAttribute("data-open");
        out.reveal = $(".main").hasAttribute("data-reveal");
        out.out = $(".rv") ? $(".rv").hasAttribute("data-out") : "gone";
        return out;
        """.replace("__SLOW__", SLOW),
        data=v9(),
    )
    assert got == {"before": 1, "drawer": "skipped", "reveal": False, "out": True}
    key = _run(
        tmp_path,
        """
        await open(__SLOW__);
        key("a");
        return [$(".main").hasAttribute("data-reveal"), $(".rv").hasAttribute("data-out")];
        """.replace("__SLOW__", SLOW),
        data=v9(),
    )
    assert key == [False, True]


def test_the_reveal_reads_in_polish_and_has_no_zero_for_a_count_nobody_gave(tmp_path) -> None:
    data = v9(jezyk="pl")
    for opp in data["okazje"]:
        opp["komentarze"] = None
    got = _run(
        tmp_path,
        """
        await open();
        return { head: $(".rv-b > span:last-child").textContent.trim(), skip: $(".rv [data-rvskip] .tt").textContent, counts: $$(".rv .tc").length };
        """,
        data=data,
    )
    assert got["head"] == "Przeczytano 9 wątków: r/SideProject, X, r/SaaS +1"
    assert got["skip"] == "+5 pominiętych"
    assert got["counts"] == 0, "a thread with no count shows none, never a 0"


def test_a_card_with_nobody_on_it_has_no_reveal(tmp_path) -> None:
    nobody = v9()
    nobody["okazje"], nobody["szkice"] = [], []
    got = _run(
        tmp_path,
        "await open(); return [$$('.rv').length, $$('[data-reveal]').length, $$('[data-tog]').length];",
        data=nobody,
    )
    assert got == [0, 0, 2], "no people, no reveal; the disclosures are still there"


def test_under_reduced_motion_there_is_no_reveal_no_shine_no_beam_and_no_label_swap(
    tmp_path,
) -> None:
    got = _run(
        tmp_path,
        f"""
        await open({REDUCED});
        const out = {{ reveal: $$(".rv").length + $$("[data-reveal]").length, shine: $$("[data-shine]").length }};
        const page = () => active()[0];
        $('[data-act="send"]', page()).click();
        await sleep(40);
        out.shineConfirm = $$("[data-shine]").length;
        out.beam = $$("[data-beam]").length;
        out.morph = $$("[data-morph]").length + $$(".ghost").length;
        return out;
        """,
        tools={"room.send_token": TOKEN_OK},
        data=v9(),
    )
    assert got == {"reveal": 0, "shine": 0, "shineConfirm": 0, "beam": 0, "morph": 0}


def test_the_send_shines_once_and_a_confirmation_gets_a_beam_that_goes_away(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open(__SLOW__);
        await sleep(100);
        const waiting = $$("[data-shine]").length;             // while the reveal covers the card, nothing shines
        await sleep(160);                                      // the reveal is over, the panel is there, Send shines
        const out = { waiting };
        const page = () => active()[0];
        out.idle = $$("[data-shine]", page()).length;
        // The animation ends: the attribute goes, and the shine is one pass, not a loop.
        $$("[data-shine]").forEach((b) => { const ev = new win.Event("animationend", { bubbles: true }); ev.animationName = "shine"; b.dispatchEvent(ev); });
        out.afterPass = $$("[data-shine]").length;
        next(); prev();
        out.comeBack = $$("[data-shine]", page()).length;      // the same page does not shine again
        $('[data-act="send"]', page()).click();
        await sleep(40);
        out.confirm = [$$('[data-act="go"][data-shine]', page()).length, $(".stage").hasAttribute("data-beam")];
        await sleep(220);
        out.beamGone = $(".stage").hasAttribute("data-beam");
        return out;
        """.replace("__SLOW__", SLOW),
        tools={"room.send_token": TOKEN_OK},
        data=v9(),
    )
    assert got["waiting"] == 0, "the shine waits for the reveal: it would run under the layer"
    assert got["idle"] == 1
    assert got["afterPass"] == 0
    assert got["comeBack"] == 0
    assert got["confirm"] == [1, True]
    assert got["beamGone"] is False


def test_a_label_that_changes_crossfades_while_the_width_settles(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open((w) => {
          w.Element.prototype.getBoundingClientRect = function () {
            const wide = this.classList && this.classList.contains("pill") ? 150 : this.classList && this.classList.contains("primary") ? 64 : 0;
            return { width: wide, height: 34, x: 0, y: 0, top: 0, left: 0, right: wide, bottom: 34 };
          };
        });
        await sleep(30);
        const page = () => active()[0];
        $('[data-act="send"]', page()).click();        // drawn synchronously: look before the host answers
        const pill = $(".send .pill", page());
        const out = { morph: pill ? pill.hasAttribute("data-morph") : null, ghost: pill && $(".ghost", pill) ? $(".ghost", pill).textContent : null,
          width: pill ? pill.style.width : null, label: pill ? $(".lbl", pill).textContent.trim() : null };
        await sleep(40);
        const done = $(".send [data-act=go]", page());
        out.settled = [$$("[data-morph]").length, $$(".ghost").length];
        out.confirm = !!done;
        return out;
        """,
        tools={"room.send_token": TOKEN_OK},
        data=v9(),
    )
    assert got["ghost"] == "Send", "the old words fade out inside the new pill"
    assert got["morph"] is True and got["label"] == "Checking the draft"
    assert got["width"] in ("64px", "150px"), (
        "the width starts from the old one and settles to the new"
    )
    assert got["settled"] == [0, 0] and got["confirm"] is True


def test_the_posted_check_draws_itself_and_the_press_has_a_spring() -> None:
    html = apps_ui.OPPORTUNITIES_HTML
    assert 'pathLength="1"' in html and "stroke-dashoffset" in html
    assert "scale(0.97)" in html and "cubic-bezier(0.34, 1.56, 0.64, 1)" in html
    # Under reduced motion each of them is off, and the layer of the reveal is not drawn.
    reduced = html[html.index("@media (prefers-reduced-motion: reduce)") :]
    reduced = reduced[: reduced.index("</style>")]
    for needle in ("transform: none", ".rv { display: none; }", "animation: none", "data-beam"):
        assert needle in reduced, needle


# ---------------------------------------------------------------------------
# A busy queue is waited out (like the send), and a repeated save re-reads the Pokój
# ---------------------------------------------------------------------------


def test_a_signature_waits_for_a_busy_queue_asks_again_and_ends_at_a_confirmation(tmp_path) -> None:
    got = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        $('[data-scact="save"]').click();
        await sleep(30);
        const out = { phase: $(".sheet").getAttribute("data-p"), text: sheetText() };
        await sleep(120);                      // 4 s and 8 s of the card's clock
        out.after = $(".sheet").getAttribute("data-p");
        out.asks = calls().filter((c) => c.name === "room.scope_token").length;
        out.signed = calls().filter((c) => c.name === "room.sign_scope").length;
        return out;
        """,
        tools={
            "room.scope_token": [
                _refusal("ZAKRES_KOLEJKA"),
                _refusal("ZAKRES_KOLEJKA"),
                _asked(PLAN_SIGN),
            ],
        },
        data=v9(),
    )
    assert got["phase"] == "waiting" and "Waiting for the previous reply" in got["text"]
    assert "Nothing is signed without your click" in got["text"]
    assert got["after"] == "confirm", (
        "the wait ends at the sentence to click, never at the signature"
    )
    assert got["asks"] == 3 and got["signed"] == 0


def test_cancel_stops_the_wait_and_a_queue_that_never_clears_ends_in_a_refusal(tmp_path) -> None:
    stop = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        $('[data-scact="save"]').click();
        await sleep(30);
        $('[data-scact="cancel"]').click();
        const asks = calls().filter((c) => c.name === "room.scope_token").length;
        await sleep(150);
        return [$("#app").hasAttribute("data-sheet"), asks, calls().filter((c) => c.name === "room.scope_token").length];
        """,
        tools={"room.scope_token": _refusal("ZAKRES_KOLEJKA")},
        data=v9(),
    )
    assert stop == [False, 1, 1], "after Cancel nothing asks again"
    never = _run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        $('[data-scact="save"]').click();
        await sleep(900);                      // more than a minute of the card's clock
        return [$(".sheet").getAttribute("data-p"), calls().filter((c) => c.name === "room.scope_token").length];
        """,
        tools={"room.scope_token": _refusal("ZAKRES_KOLEJKA")},
        data=v9(),
    )
    assert never[0] == "refused" and 2 < never[1] <= 17


def test_a_repeated_save_that_the_server_calls_already_done_reads_the_pokoj_again(tmp_path) -> None:
    after = v9()
    after["szkice"] = [dict(d) for d in after["szkice"]]
    after["szkice"][0] = _draft(REV_A2, OPP_A, tekst=NEW_TEXT, odcisk=DIGEST2)
    after["okazje"][0]["szkic"] = REV_A2
    got = _run(
        tmp_path,
        f"""
        await open();
        const page = () => active()[0];
        $("[data-edit]", page()).click();
        $("textarea.edit", page()).value = "{NEW_TEXT}";
        $('[data-ed="save"]', page()).click();
        await sleep(60);
        return {{ box: $("[data-box]", page()).getAttribute("data-box"), text: $(".pt", page()).textContent,
                 calls: calls().map((c) => c.name), editors: $$("textarea").length }};
        """,
        tools={
            "room.revise_draft": {
                "structured": {
                    "stan": "WYKONANE",
                    "powtorzone": True,
                    "uwaga": "x",
                    "poprzedni_wynik": {},
                }
            },
            "room.show": {"structured": after},
        },
        data=v9(),
    )
    assert got["box"] == REV_A2 and got["text"] == NEW_TEXT and got["editors"] == 0
    assert got["calls"] == ["room.revise_draft", "room.show"]
