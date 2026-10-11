"""The card speaks Polish and English (v5), and the choice between them is not a guess.

What is held here, from the outside in:

  * the two dictionaries have the same keys, the same kinds of value (a string, or a list of
    plural forms: three in Polish, two in English) and the same `{placeholders}`, so no string is
    ever missing in a language, and no sentence is filled with a value the other one has no place for;
  * no long dash (U+2014, U+2013) in either, and nothing a string could use to close the script;
  * every key the card's script asks for is defined, every defined key is asked for, and the
    markup holds no Polish word of its own;
  * every refusal CODE the server can send (`kod`) has a sentence of the card's own in both
    languages, so an English reader is shown English and the server's Polish detail is only the
    fallback for a code the card has never heard of;
  * which language is chosen: the pure JS function `pickLang` (cut out of the card by its markers
    and run in node) and the server's `card_language`, over the same table of cases.

NOT HELD HERE: how the card looks. Both languages are rendered in a headless browser by hand
(the PNGs the task report names), because the card's layout is not a thing a string comparison can
see; and that a host really sends `hostContext.locale` is unverified live (the spec has the field,
the hosts were not asked).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from control_plane.presence import card_consent, human_approval
from control_plane.tools import apps_ui, card_send

CONTROL_PLANE = Path(__file__).resolve().parents[1]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
PL = apps_ui.CARD_STRINGS["pl"]
EN = apps_ui.CARD_STRINGS["en"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _forms(value: object) -> list[str]:
    return value if isinstance(value, list) else [value]  # type: ignore[list-item]


def _script() -> str:
    """The card's script as written in the source: the dictionaries still a placeholder."""
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    start, end = html.index("<script>"), html.index("</script>")
    return html[start:end]


# ---------------------------------------------------------------------------
# The dictionaries
# ---------------------------------------------------------------------------


def test_english_has_every_key_polish_has_and_the_other_way_round() -> None:
    assert set(EN) - set(PL) == set(), "klucze tylko w angielskim"
    assert set(PL) - set(EN) == set(), "klucze tylko w polskim: brakuje tekstu po angielsku"
    assert set(apps_ui.CARD_STRINGS) == {"pl", "en"}


def test_each_key_is_the_same_kind_of_value_with_the_forms_its_language_has() -> None:
    for key in PL:
        assert type(PL[key]) is type(EN[key]), key
        if isinstance(PL[key], list):
            assert len(PL[key]) == 3, f"{key}: polski ma trzy formy (1, 2 do 4, reszta)"
            assert len(EN[key]) == 2, f"{key}: angielski ma dwie formy (1, reszta)"
        for lang in (PL, EN):
            for form in _forms(lang[key]):
                assert isinstance(form, str) and form.strip(), f"{key}: pusty tekst"


def test_both_languages_fill_the_same_placeholders() -> None:
    for key in PL:
        pl = {name for form in _forms(PL[key]) for name in PLACEHOLDER.findall(form)}
        en = {name for form in _forms(EN[key]) for name in PLACEHOLDER.findall(form)}
        assert pl == en, f"{key}: {sorted(pl)} po polsku, {sorted(en)} po angielsku"
        # And inside one language every form of a count has the same ones: a form that forgot the
        # number would print a sentence with a hole in it for that count only.
        for lang in (PL, EN):
            forms = _forms(lang[key])
            assert len({frozenset(PLACEHOLDER.findall(form)) for form in forms}) == 1, key


def test_no_string_has_a_long_dash_or_can_close_the_script() -> None:
    for name, lang in apps_ui.CARD_STRINGS.items():
        for key, value in lang.items():
            for form in _forms(value):
                assert not LONG_DASHES.search(form), f"{name}.{key}: długa pauza"
                assert "</script" not in form.lower() and "<!--" not in form, f"{name}.{key}"
    assert not LONG_DASHES.search(apps_ui.OPPORTUNITIES_HTML)
    assert "</script" not in apps_ui._strings_json().lower()


def test_the_markup_in_a_string_is_only_the_few_tags_the_card_allows() -> None:
    allowed = {"b", "strong", "i"}
    for name, lang in apps_ui.CARD_STRINGS.items():
        for key, value in lang.items():
            for form in _forms(value):
                tags = set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", form))
                assert tags <= allowed, f"{name}.{key}: {sorted(tags - allowed)}"
                assert form.count("<") == form.count(">"), f"{name}.{key}: nawiasy"


def test_the_english_has_the_lines_the_launch_needs() -> None:
    assert EN["rd.headNow"][1] == (
        "<strong>{n} people</strong> are writing right now about the problem your code solves."
    )
    assert "recently" in EN["rd.headRecent"][1] and "right now" not in EN["rd.headRecent"][1]
    assert EN["rd.waiting"] == ["{n} reply waiting for your OK", "{n} replies waiting for your OK"]
    assert (EN["ap.send"], EN["c.plainReply"]) == (
        "Send",
        "Send from <b>{h}</b>? The reply will go out exactly as you see it.",
    )
    assert EN["sp.yes"] == "Yes, send"
    assert EN["c.sign"] == (
        "Wenext can write from {accounts}. I'll show you every reply before it goes out."
    )
    assert PL["c.sign"] == (
        "Wenext może pisać z {accounts}. Każdą odpowiedź pokażę Ci przed wysłaniem."
    )
    assert (EN["state.ODPOWIEDZIANO"], EN["sp.queued"], EN["ap.edit"]) == (
        "Posted",
        "Queued.",
        "Edit",
    )
    assert (EN["ap.original"], EN["oc.openRoom"]) == ("Original ↗", "Open Room ↗")
    assert EN["rd.foot"] == "Nothing posts without your click."
    assert EN["fig.none"] == "not measured"


# ---------------------------------------------------------------------------
# The script asks only for words that exist, and the markup has none
# ---------------------------------------------------------------------------

#: Keys the script builds from data (`"state." + o.stan`, `"ref." + data.kod`, `"w." + status`).
DYNAMIC = ("state.", "ref.", "w.", "seen.")


def test_every_key_the_script_asks_for_exists_and_every_key_is_asked_for() -> None:
    script = _script()
    prefixes = {key.split(".")[0] for key in PL}
    literals = set(re.findall(r"""["']([a-z]+\.[A-Za-z_]+)["']""", script))
    asked = {key for key in literals if key.split(".")[0] in prefixes}
    assert asked <= set(PL), f"klucze z kodu karty bez tekstu: {sorted(asked - set(PL))}"
    unused = {key for key in PL if key not in asked and not key.startswith(DYNAMIC)}
    assert unused == set(), f"teksty, o które karta nigdy nie prosi: {sorted(unused)}"
    # The dynamic families are really built that way, not left over.
    for prefix in ("state.", "ref.", "w."):
        assert f'"{prefix}" +' in script, prefix


def test_the_markup_and_the_script_hold_no_polish_word_of_their_own() -> None:
    html = apps_ui.OPPORTUNITIES_HTML.replace(apps_ui._strings_json(), "__STRINGS__")
    markup = html[html.index("<body>") : html.index("<script>")]
    assert re.sub(r"\s+", " ", markup).strip() == '<body> <div class="panel" id="app"></div>'
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", _script(), flags=re.S)  # comments may be Polish
    assert not re.search("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", code), "polskie słowo wpisane w kod karty"
    assert '<html lang="en">' in html, "język ustawia dopiero karta"
    assert "document.documentElement.lang = LANG" in html


# ---------------------------------------------------------------------------
# A refusal CODE has a sentence of the card's own
# ---------------------------------------------------------------------------


def _codes(*modules: object) -> set[str]:
    return {
        value
        for module in modules
        for name, value in vars(module).items()
        if name.startswith(("CODE_", "REFUSED_")) and isinstance(value, str)
    }


def test_every_refusal_code_the_server_sends_has_a_sentence_in_both_languages() -> None:
    codes = _codes(card_send, card_consent, human_approval)
    assert len(codes) >= 14, "kody znikły z modułów: test nic by nie sprawdzał"
    for lang in (PL, EN):
        assert {key.removeprefix("ref.") for key in lang if key.startswith("ref.")} == codes


def test_a_refusal_sentence_is_in_english_for_english_and_the_server_one_is_the_fallback() -> None:
    # The English sentences are English: none of the Polish letters, and the Polish ones have them.
    for code in _codes(card_send, card_consent, human_approval):
        assert not re.search("[ąćęłńóśźż]", EN[f"ref.{code}"]), code
    assert re.search("[ąćęłńóśźż]", PL["ref.OKNO"])
    # The fallback is in the card's code, not only in a comment: own sentence first for English,
    # the server's `powod` first for Polish, then the card's own default.
    script = _script()
    assert 'L("ref." + data.kod, null)' in script
    assert 'LANG === "pl" ? server || own : own || server' in script


# ---------------------------------------------------------------------------
# Which language
# ---------------------------------------------------------------------------

#: (explicit setting from the server, the host's locale) -> the language the card speaks.
PICK = [
    ("pl", "en-US", "pl"),  # a Polish install stays Polish whatever its host reports
    ("en", "pl-PL", "en"),
    ("PL", "", "pl"),
    (" en ", "pl", "en"),
    (None, "pl-PL", "pl"),
    ("", "pl", "pl"),
    (None, "pl_PL", "pl"),
    (None, "PL-pl", "pl"),
    (None, "en-US", "en"),
    (None, "en", "en"),
    (None, "de-DE", "en"),
    (None, "fr", "en"),
    (None, "pol", "en"),  # not Polish: only `pl` and `pl-...` are
    (None, "plx", "en"),
    (None, "", "en"),  # no signal at all: English, the public default
    (None, None, "en"),
    ("auto", "pl-PL", "pl"),  # an explicit value the card does not know is no choice at all
    ("de", "pl-PL", "pl"),
    ("de", "en-GB", "en"),
]
LOCALES = [
    ("pl", "en-US", "pl-PL"),
    ("pl", "", "pl-PL"),
    ("en", "en-GB", "en-GB"),
    ("en", "en_GB", "en-GB"),
    ("en", "en", "en"),
    ("en", "de-DE", "en-US"),
    ("en", "pl-PL", "en-US"),
    ("en", "", "en-US"),
    ("en", None, "en-US"),
]


def _lang_source() -> str:
    script = _script()
    start = script.index("/*LANG-BEGIN*/") + len("/*LANG-BEGIN*/")
    return script[start : script.index("/*LANG-END*/")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node nie jest zainstalowany")
def test_the_card_picks_the_language_by_the_documented_order(tmp_path: Path) -> None:
    program = tmp_path / "pick.js"
    program.write_text(
        _lang_source()
        + f"\nconst picks = {json.dumps(PICK)}, locales = {json.dumps(LOCALES)};\n"
        + "console.log(JSON.stringify({"
        + "picks: picks.map(function (c) { return pickLang(c[0], c[1]); }),"
        + "locales: locales.map(function (c) { return localeFor(c[0], c[1]); })}));\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        ["node", str(program)], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got["picks"] == [case[2] for case in PICK]
    assert got["locales"] == [case[2] for case in LOCALES]


def test_the_chooser_is_a_pure_function_and_the_order_is_in_the_card() -> None:
    source = re.sub(r"//[^\n]*", "", _lang_source())  # the comments may name the host
    assert "document" not in source and "window" not in source and "navigator" not in source
    script = _script()
    assert "LANG = pickLang(explicit, loc)" in script
    assert "setLang(data && data.jezyk)" in script, "pierwsze jest jawne `jezyk` z wyniku room.show"
    assert (
        "ctx.locale" in script
        and "window.openai.locale" in script
        and "navigator.language" in script
    )
    assert "host-context-changed" in script
    assert 'version: "8"' in script


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pl", "pl"),
        ("PL", "pl"),
        ("pl_PL", "pl"),
        ("pl-PL", "pl"),
        (" pl ", "pl"),
        ("en", "en"),
        ("en-GB", "en"),
        ("de", "en"),
        ("polish", "en"),
        ("", None),
        ("  ", None),
        ("auto", None),
        ("AUTO", None),
        (None, None),
    ],
)
def test_the_server_pins_a_language_only_when_the_person_set_one(
    raw: str | None, expected: str | None
) -> None:
    assert apps_ui.language_of(raw) == expected
    assert apps_ui.card_language({} if raw is None else {"WENEXT_LANG": raw}) == expected


def test_the_server_reads_the_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WENEXT_LANG", raising=False)
    assert apps_ui.card_language() is None
    monkeypatch.setenv("WENEXT_LANG", "pl")
    assert apps_ui.card_language() == "pl"
    assert apps_ui.ENV_LANG == "WENEXT_LANG"


def test_what_a_host_may_show_about_the_card_follows_the_pinned_language() -> None:
    assert apps_ui.resources("pl")[0]["name"] == "Pokój Wenext"
    assert apps_ui.resources("en")[0]["name"] == "Wenext Room"
    assert apps_ui.resources()[0]["uri"] == apps_ui.OPPORTUNITIES_URI
    polish = apps_ui.read(apps_ui.OPPORTUNITIES_URI, "pl")
    english = apps_ui.read(apps_ui.OPPORTUNITIES_URI, "en")
    assert polish is not None and english is not None
    # v8: the host may grant the clipboard to the card (the manual flow copies the reply).
    assert polish["contents"][0]["_meta"]["ui"]["permissions"] == {"clipboardWrite": {}}
    assert polish["contents"][0]["_meta"]["openai/widgetDescription"].startswith("Karty")
    assert english["contents"][0]["_meta"]["openai/widgetDescription"].startswith("Cards")
    # Same card either way: the language is chosen by the card, not by which file is served.
    assert polish["contents"][0]["text"] == english["contents"][0]["text"]
    assert apps_ui.read("ui://wenext/okazje-v4.html") is None
    assert apps_ui.read("ui://wenext/okazje-v5.html") is None
    assert apps_ui.read("ui://wenext/okazje-v6.html") is None
    assert apps_ui.read("ui://wenext/okazje-v7.html") is None
    for text in apps_ui._RESOURCE_TEXT.values():
        assert not any(LONG_DASHES.search(value) for value in text.values())


def test_the_new_files_have_no_long_dash() -> None:
    for name in (
        "tests/test_card_language_unit.py",
        "control_plane/tools/apps_ui.py",
        "control_plane/tools/opportunity_tools.py",
    ):
        assert not LONG_DASHES.search((CONTROL_PLANE / name).read_text(encoding="utf-8")), name
