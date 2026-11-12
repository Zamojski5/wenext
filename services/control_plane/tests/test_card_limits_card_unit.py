"""The card's Limits panel sets a pace for EACH account, and every sentence tells it (v11, 13.10).

THE FOUNDER (13.10): X replies at six an hour waited behind Reddit's one an hour. The server now keeps
a pace per account (`presence/budget.py`); what the card does about it is held here, in jsdom with the
fake host of the other card tests (the card's own script is what runs):

  * THE PANEL opens on what binds each account now (the server's `limity` of that account, whichever
    level it came from), one block of three numbers per account that can publish, each inside the
    server's bounds; an UNTICKED account's block is dimmed and its fields are off, and Save sends
    numbers only for the ticked ones (the server refuses a limit for nobody);
  * THE ASK carries `limity_konta` (account id -> godzina, doba, wlasne_doba) beside the three project
    numbers of the old form, and the redeeming call still carries the token and nothing else;
  * THE CONFIRMATION says the pace account by account, so nothing is signed that was not on the screen;
  * THE FOOTER says "pace set per account" instead of one number that no longer describes the scope;
  * THE FIRST-SEND SENTENCE and THE BATCH PANEL tell each account's pace when the plan has it, and the
    old one-bucket sentence when it does not (a server from before v11, or a scope signed with one
    bucket).

NOT HELD HERE: how it looks (the PNG `limits-per-account.png` of the task report).
"""

from __future__ import annotations

import re
import shutil
from typing import Any

import pytest
from control_plane.tools import apps_ui
from tests.test_card_batch_card_unit import LIB_BATCH
from tests.test_card_pager_unit import JSDOM, _iso
from tests.test_card_pager_unit import _run as _base_run
from tests.test_card_v9_unit import ACC_R, ACC_X, _asked, _done, _scope, v9
from tests.test_card_v9_unit import _run as _v9_run

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not JSDOM.is_dir(),
    reason="potrzebny node i jsdom (apps/dashboard/node_modules)",
)

PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
NEW_KEYS = (
    "sc.paceNote",
    "sc.eachOwn",
    "sc.cSignAcct",
    "sc.paceRow",
    "c.limitsAcct",
    "ba.limitsAcct",
)


def _per_account_scope(**over: Any) -> dict[str, Any]:
    """The scope as a server with limits per account reports it: each account of `do_wyboru` carries
    what binds IT, and `wedlug_kont` is true."""
    scope = _scope(
        wedlug_kont=True,
        razem={"godzina": None, "doba": None},
        do_wyboru=[
            {
                "id": ACC_R,
                "platforma": "REDDIT",
                "konto": "demo_founder",
                "dzialania": ["REPLY", "OWN_POST"],
                "w_zakresie": True,
                "limity": {
                    "godzina": 2,
                    "doba": 10,
                    "wlasne_doba": 1,
                    "zrodlo": {"godzina": "account", "doba": "account", "wlasne_doba": "account"},
                    "wspolny_kosz": False,
                },
            },
            {
                "id": ACC_X,
                "platforma": "X",
                "konto": "demo_founder_x",
                "dzialania": ["REPLY", "OWN_POST"],
                "w_zakresie": True,
                "limity": {
                    "godzina": 6,
                    "doba": 30,
                    "wlasne_doba": 2,
                    "zrodlo": {"godzina": "account", "doba": "account", "wlasne_doba": "account"},
                    "wspolny_kosz": False,
                },
            },
        ],
    )
    scope.update(over)
    return scope


def _plan(**over: Any) -> dict[str, Any]:
    plan = {
        "rodzaj": "USTAW",
        "tryb": "REVIEW",
        "wygasa": _iso(days=7),
        "limity": {"godzina": 2, "doba": 10, "wlasne_doba": 1},
        "konta": [
            {"platforma": "REDDIT", "konto": "demo_founder"},
            {"platforma": "X", "konto": "demo_founder_x"},
        ],
        "dzialania": ["REPLY", "OWN_POST"],
        "poprzedni_tryb": "REVIEW",
        "limity_kont": [
            {
                "id": ACC_R,
                "platforma": "REDDIT",
                "konto": "demo_founder",
                "godzina": 2,
                "doba": 10,
                "wlasne_doba": 1,
            },
            {
                "id": ACC_X,
                "platforma": "X",
                "konto": "demo_founder_x",
                "godzina": 6,
                "doba": 30,
                "wlasne_doba": 2,
            },
        ],
    }
    plan.update(over)
    return plan


def _data(**over: Any) -> dict[str, Any]:
    return v9(zakres=_per_account_scope(**over))


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


def test_the_panel_has_a_block_per_account_opened_on_what_binds_that_account(tmp_path) -> None:
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        const blocks = $$("[data-pa]").map((b) => ({
          id: b.getAttribute("data-pa"), who: $(".pa-who", b).textContent, off: b.classList.contains("off"),
          numbers: ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`, b).value),
          max: ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`, b).getAttribute("max")),
          acc: ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`, b).getAttribute("data-acc")),
          labels: $$(".nf span", b).map((n) => n.textContent),
        }));
        return { blocks, loose: $$('.sf [data-sc="godzina"]:not([data-acc])').length, note: $(".sh-note").textContent };
        """,
        data=_data(),
    )
    reddit, x = got["blocks"]
    assert (reddit["who"], reddit["numbers"]) == ("u/demo_founder", ["2", "10", "1"])
    assert (x["who"], x["numbers"]) == ("@demo_founder_x", ["6", "30", "2"])
    assert reddit["max"] == x["max"] == ["10", "50", "10"], "the server's bounds, per account"
    assert x["acc"] == [ACC_X] * 3 and reddit["labels"] == [
        "Replies per hour",
        "Replies per day",
        "Own posts a day",
    ]
    assert not reddit["off"] and not x["off"]
    assert got["loose"] == 0, "no project-wide number is left in the panel"
    assert got["note"].startswith("Each account has its own pace.")


def test_an_unticked_account_is_dimmed_and_off_and_save_sends_numbers_for_the_ticked_only(
    tmp_path,
) -> None:
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        const reddit = $('[data-pa="__R__"]');
        $('[data-sc="acc"][value="__R__"]').click();
        const off = { dimmed: reddit.classList.contains("off"), disabled: $$("input", reddit).every((i) => i.disabled) };
        const el = $('[data-pa="__X__"] [data-sc="godzina"]');
        el.value = "8"; el.dispatchEvent(new win.Event("input", { bubbles: true }));
        $('[data-scact="save"]').click();
        await sleep(40);
        return { off, asked: calls().filter((c) => c.name === "room.scope_token").map((c) => c.args) };
        """.replace("__R__", ACC_R).replace("__X__", ACC_X),
        tools={
            "room.scope_token": _asked(_plan(konta=[{"platforma": "X", "konto": "demo_founder_x"}]))
        },
        data=_data(),
    )
    assert got["off"] == {"dimmed": True, "disabled": True}
    (body,) = got["asked"]
    assert body["konta"] == [ACC_X], "only the ticked account"
    assert body["limity_konta"] == {ACC_X: {"godzina": 8, "doba": 30, "wlasne_doba": 2}}
    assert body["akcja"] == "podpisz" and body["dni"] == 7


def test_ticking_the_account_again_brings_its_block_back(tmp_path) -> None:
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        const i = $(`[data-sc="acc"][value="__R__"]`), block = $(`[data-pa="__R__"]`);
        i.click(); const gone = block.classList.contains("off");
        i.click();
        return { gone, back: !block.classList.contains("off"), enabled: $$("input", block).every((x) => !x.disabled) };
        """.replace("__R__", ACC_R),
        data=_data(),
    )
    assert got == {"gone": True, "back": True, "enabled": True}


def test_a_server_that_sends_no_pace_per_account_opens_every_block_on_the_project_numbers(
    tmp_path,
) -> None:
    """A server from before v11 (or a scope signed with one bucket): `limity` of the scope is the
    project's, and each account's block opens on it."""
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        return $$("[data-pa]").map((b) => ["godzina", "doba", "wlasne_doba"].map((k) => $(`[data-sc="${k}"]`, b).value));
        """,
        data=v9(),
    )
    assert got == [["4", "20", "1"], ["4", "20", "1"]]


def test_the_confirmation_tells_the_pace_account_by_account_and_the_token_goes_alone(
    tmp_path,
) -> None:
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        $('[data-scact="save"]').click();
        await sleep(40);
        const sentence = $(".sheet .sh-msg").textContent.replace(/\\s+/g, " ").trim();
        const items = $$(".sheet .pl li").map((li) => li.textContent);
        const before = calls().filter((c) => c.name === "room.sign_scope").length;
        $('[data-scact="go"]').click();
        await sleep(40);
        return { sentence, items, before, signed: calls().filter((c) => c.name === "room.sign_scope").map((c) => c.args) };
        """,
        tools={
            "room.scope_token": _asked(_plan()),
            "room.sign_scope": _done(),
        },
        data=_data(),
    )
    assert got["sentence"].startswith(
        "Sign this scope? Wenext can write from u/demo_founder and @demo_founder_x"
    )
    assert "Each account has its own pace" in got["sentence"]
    assert got["items"] == [
        "u/demo_founder: up to 2 an hour and 10 a day, 1 own post a day",
        "@demo_founder_x: up to 6 an hour and 30 a day, 2 own posts a day",
    ]
    assert got["before"] == 0 and got["signed"] == [{"token": "scope-" + "t" * 24}]


def test_a_confirmation_without_a_pace_per_account_keeps_the_one_bucket_sentence(tmp_path) -> None:
    got = _v9_run(
        tmp_path,
        """
        await open();
        $('[data-scope="panel"]').click();
        $('[data-scact="save"]').click();
        await sleep(40);
        return { sentence: $(".sheet .sh-msg").textContent.replace(/\\s+/g, " ").trim(), items: $$(".sheet .pl li").length };
        """,
        tools={
            "room.scope_token": _asked(
                _plan(limity_kont=[], limity={"godzina": 5, "doba": 30, "wlasne_doba": 1})
            )
        },
        data=v9(),
    )
    assert "up to 5 an hour and 30 a day" in got["sentence"] and got["items"] == 0


def test_the_limits_panel_says_the_pace_is_set_per_account(tmp_path) -> None:
    """v11: the scope in a line is no longer in the footer; it is the first row of the Limits panel."""
    scenario = """
        await open();
        $('[data-scope="panel"]').click();
        return $(".nowl").textContent;
    """
    assert "pace set per account" in _v9_run(tmp_path, scenario, data=_data())
    legacy = _v9_run(tmp_path, scenario, data=v9())
    assert "4/h · 20/day" in legacy, "one bucket: the project's numbers, as before"
    assert "pace set per account" not in legacy


# ---------------------------------------------------------------------------
# The sentences that tell a pace without opening the panel
# ---------------------------------------------------------------------------


def test_the_first_send_sentence_tells_each_accounts_pace_when_the_plan_has_it(tmp_path) -> None:
    plan = _plan(rodzaj="PODPISZ", limity={"godzina": None, "doba": None})
    got = _v9_run(
        tmp_path,
        """
        await open();
        const page = () => active()[0];
        $('[data-act="send"]', page()).click();
        await sleep(40);
        return $(".send", page()).textContent.replace(/\\s+/g, " ").trim();
        """,
        tools={"room.send_token": _asked(plan)},
        data=_data(),
    )
    assert (
        "with its own pace for each account: u/demo_founder 2/h, 10/day; @demo_founder_x 6/h, 30/day"
        in got
    )


def _batch_run(tmp_path, scenario: str, limits: dict[str, Any], data: dict[str, Any]) -> Any:
    prelude = (
        "const EXCLUDE = {};\nconst SENTMODE = 'ok';\n"
        f"const LIMITS = {__import__('json').dumps(limits)};\nconst ORDER = null;\n"
    )
    return _base_run(tmp_path, prelude + LIB_BATCH + scenario, data=data)


def test_the_batch_panel_tells_the_pace_of_each_account_in_the_batch(tmp_path) -> None:
    per_account = {
        "godzina": 1,
        "doba": 6,
        "wlasne_doba": 1,
        "konta": [
            {
                "konto": "demo_founder",
                "platforma": "REDDIT",
                "godzina": 2,
                "doba": 10,
                "wlasne_doba": 1,
            },
            {
                "konto": "demo_founder_x",
                "platforma": "X",
                "godzina": 6,
                "doba": 30,
                "wlasne_doba": 2,
            },
        ],
    }
    got = _batch_run(
        tmp_path,
        """
        await open();
        btn().click();
        await sleep(60);
        return sheetText();
        """,
        per_account,
        v9(),
    )
    assert "Limits: u/demo_founder 2/h, 10/day; @demo_founder_x 6/h, 30/day." in got
    assert "random delay" in got
    legacy = _batch_run(
        tmp_path,
        "await open(); btn().click(); await sleep(60); return sheetText();",
        {"godzina": 4, "doba": 20, "wlasne_doba": 1},
        v9(),
    )
    assert "Limits: 4 an hour, 20 a day." in legacy


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_the_new_words_are_in_both_languages_with_the_same_placeholders_and_no_long_dash() -> None:
    placeholder = re.compile(r"\{(\w+)\}")
    for key in NEW_KEYS:
        assert key in PL and key in EN, key
        for text in (PL[key], EN[key]):
            assert not LONG_DASHES.search(text), key
        assert set(placeholder.findall(PL[key])) == set(placeholder.findall(EN[key])), key
