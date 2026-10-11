"""Profil runtime sesji produktu, osobny od hardeningu pilota (D-046).

CZEGO PILNUJE TEN PLIK. Rozmowa dostaje natywne wyszukiwanie w sieci, a pilot ma zostać
dokładnie tym, czym był, kiedy mierzono D12. Te dwa zdania łatwo napisać i łatwo złamać jednym
polem w generatorze, więc test nie opisuje różnicy słowami: porównuje WYGENEROWANE PLIKI i
sprawdza, że różnią się jedną linią, a pięć flag `[features]` jest wyłączonych w obu.
"""

from __future__ import annotations

import pytest
from codex_runtime.hardening import (
    REQUIRED_FEATURES,
    HardeningConfig,
    effective_config_mismatches,
    render_config_toml,
)
from control_plane.sessions.runtime_profile import (
    WEB_SEARCH_OFF,
    hardening_is_broken,
    profile_for,
)
from control_plane.sessions.store import SESSION_CONVERSATION, SESSION_PRESENCE

pytestmark = [pytest.mark.p2]


def _rendered(kind: str | None) -> str:
    return render_config_toml(HardeningConfig(web_search=profile_for(kind).web_search))


def test_the_pilot_gets_exactly_the_configuration_it_had() -> None:
    """Bajt w bajt z domyślnym hardeningiem. Gdyby profil produktu przeciekł na obecność,
    pilot zacząłby chodzić na konfiguracji, której nikt dla niego nie mierzył."""
    assert _rendered(SESSION_PRESENCE) == render_config_toml(HardeningConfig())
    assert profile_for(SESSION_PRESENCE).web_search == WEB_SEARCH_OFF
    assert profile_for(SESSION_PRESENCE).searches_the_web is False


def test_a_conversation_differs_from_the_pilot_by_one_line() -> None:
    pilot = _rendered(SESSION_PRESENCE).splitlines()
    conversation = _rendered(SESSION_CONVERSATION).splitlines()
    assert len(pilot) == len(conversation)
    different = [
        (before, after)
        for before, after in zip(pilot, conversation, strict=True)
        if before != after
    ]
    assert different == [('web_search = "disabled"', 'web_search = "live"')], different


def test_the_five_flags_stay_off_in_every_profile() -> None:
    """To są flagi, które dają modelowi powłokę. Wyszukiwanie w sieci ich nie dotyczy i nie
    wolno, żeby dotknęło przy okazji."""
    for kind in (SESSION_PRESENCE, SESSION_CONVERSATION, None, "COS_NOWEGO"):
        rendered = _rendered(kind)
        for flag in REQUIRED_FEATURES:
            assert f"{flag} = false" in rendered, (kind, flag)


def test_an_unknown_kind_gets_the_hardened_profile() -> None:
    """Domyślna odpowiedź dla rodzaju, którego ten moduł nie zna, jest tą, której używa pilot."""
    assert profile_for("COS_NOWEGO").web_search == WEB_SEARCH_OFF
    assert profile_for(None).web_search == WEB_SEARCH_OFF


def test_the_read_back_compares_against_what_this_session_asked_for() -> None:
    """Sesja, która poprosiła o wyszukiwanie, nie ma zgłaszać swojej własnej decyzji jako
    niezgodności; gdyby zgłaszała, prawdziwa niezgodność utonęłaby w szumie."""
    effective = {
        "config": {
            "web_search": "live",
            "forced_login_method": "chatgpt",
            "cli_auth_credentials_store": "file",
            "features": dict.fromkeys(REQUIRED_FEATURES, False),
        }
    }
    assert effective_config_mismatches(effective, web_search="live") == []
    # Ten sam odczyt przy domyślnym oczekiwaniu JEST niezgodnością: tak wygląda pilot, który
    # dostał konfigurację produktu.
    assert effective_config_mismatches(effective) == ["web_search='live', oczekiwano 'disabled'"]


def test_a_search_we_did_not_get_is_not_the_same_as_a_shell_we_did() -> None:
    """Dwie niezgodności, dwa znaczenia. Sklejenie ich kończy się alarmem przy braku narzędzia
    albo ciszą przy włączonej powłoce."""
    effective = {
        "config": {
            "web_search": "disabled",
            "forced_login_method": "chatgpt",
            "cli_auth_credentials_store": "file",
            "features": dict.fromkeys(REQUIRED_FEATURES, False),
        }
    }
    only_search = effective_config_mismatches(effective, web_search="live")
    assert only_search and hardening_is_broken(only_search) == []

    effective["config"]["features"]["shell_tool"] = True
    with_shell = effective_config_mismatches(effective, web_search="live")
    broken = hardening_is_broken(with_shell)
    assert len(broken) == 1 and broken[0].startswith("features.shell_tool")


def test_a_mode_that_is_not_in_the_enum_is_refused_before_it_is_written() -> None:
    with pytest.raises(ValueError, match="WebSearchMode"):
        HardeningConfig(web_search="wlaczone")
    with pytest.raises(ValueError, match="WebSearchMode"):
        effective_config_mismatches({"config": {}}, web_search="wlaczone")


# ---------------------------------------------------------------------------
# Serwer narzędzi jest OSOBNYM PROCESEM i widzi tylko to, co mu podamy
# ---------------------------------------------------------------------------


def test_the_tool_server_is_handed_the_capsule_variables_the_parent_has(tmp_path, monkeypatch):
    """Zmierzone na live 20.09 (sesja 01ef065e): `accounts.status` oddało „brak połączenia" o
    koncie, na którym kapsuła w tej samej chwili czytała Reddita.

    Przyczyna nie była w kapsule ani w koncie: narzędzia modelu biegną w serwerze MCP `wenext`,
    czyli w osobnym procesie, któremu sami budujemy listę zmiennych. Dostawał samą ścieżkę pliku
    zdolności, więc `probe_from_env()` zwracało tam `None`. Proces rodzic widział prawdę, proces
    dziecko nie, i nic tej różnicy nie nazywało.

    Test czyta ARGUMENTY TURY, a nie wywołanie w kodzie: od E9 (21.09) to one są umową z
    app-serverem i to one decydują, z czym wystartuje proces potomny. Wcześniej tym samym była
    wygenerowana sekcja w `config.toml`; przeniosło się miejsce, nie pytanie.
    """
    from control_plane.sessions.handlers import _turn_overrides

    monkeypatch.setenv("WENEXT_BROWSER_READER_URL", "http://capsule:8765")
    monkeypatch.setenv("WENEXT_BROWSER_READER_TOKEN", "token-testowy")
    monkeypatch.setenv("WENEXT_REDDIT_REQUIRE_LOGIN", "true")

    config = "\n".join(_turn_overrides(tmp_path / "zdolnosc.json"))

    assert "WENEXT_BROWSER_READER_URL" in config, config
    assert "http://capsule:8765" in config
    assert "WENEXT_BROWSER_READER_TOKEN" in config
    assert "WENEXT_REDDIT_REQUIRE_LOGIN" in config
    assert "zdolnosc.json" in config, "plik zdolności jedzie dalej, to jest droga do bazy"


def test_a_variable_the_parent_does_not_have_is_not_passed_as_an_empty_string(
    tmp_path, monkeypatch
):
    """Pusty token to 401, czyli awaria, którą trudno odróżnić od zepsutej kapsuły.

    Brak zmiennej ma dojechać do procesu potomnego jako BRAK: wtedy `probe_from_env()` zwraca
    `None`, a narzędzie mówi „kapsuła nie jest skonfigurowana w tym procesie" i to zdanie da się
    przeczytać na karcie.
    """
    from control_plane.observe.capsule_session import capsule_env
    from control_plane.sessions.handlers import _turn_overrides

    monkeypatch.delenv("WENEXT_BROWSER_READER_URL", raising=False)
    monkeypatch.setenv("WENEXT_BROWSER_READER_TOKEN", "   ")

    config = "\n".join(_turn_overrides(tmp_path / "zdolnosc.json"))
    assert "WENEXT_BROWSER_READER_URL" not in config, config
    assert "WENEXT_BROWSER_READER_TOKEN" not in config, "sam biały znak to nie jest token"
    assert capsule_env({"WENEXT_BROWSER_READER_URL": "http://x"}) == {
        "WENEXT_BROWSER_READER_URL": "http://x"
    }
