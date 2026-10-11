"""Logika raportu walidacji projektu na Reddicie, bez bazy i bez Dockera (P4 i P5).

CO TU JEST SPRAWDZANE. Nie SQL, tylko to, co ten raport ROBI Z LICZBAMI, bo tam mieszkają
pomyłki, które zamieniają brak pomiaru w dobrą wiadomość:

  * zero w mianowniku policzone jako zero procent, czyli „nic nie potwierdzono" pokazane jako
    wynik zamiast jako brak pomiaru;
  * werdykt wystawiony na oknie krótszym niż doba, którego progi wprost nie obejmują;
  * progi przepisane do skryptu zamiast wczytane z zamrożonego pliku, przez co zmiana progu
    w pliku niczego nie zmienia w raporcie.

Testy są w pakiecie repo, a nie w control_plane, bo narzędzie mieszka w `tools/dev` i nie
importuje niczego z control plane: to jest skrypt czytający bazę przez `docker exec`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dev" / "reddit_validation_report.py"
CRITERIA = ROOT / "tools" / "gonogo" / "criteria-p4.yaml"


def _module():
    specification = importlib.util.spec_from_file_location("reddit_validation_report", TOOL)
    assert specification is not None and specification.loader is not None, TOOL
    module = importlib.util.module_from_spec(specification)
    # Rejestracja PRZED wykonaniem: `@dataclass` zagląda do `sys.modules[cls.__module__]`, więc
    # moduł wczytany z pliku i niezarejestrowany wywraca się na własnej dataklasie.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


report = _module()


def _raw(**raw_counts) -> dict:
    """Jeden pomiar z domyślnymi zerami, żeby każdy test mówił tylko o swojej liczbie."""
    base = {
        "puste_cykle_bez_modelu": 0,
        "puste_cykle": 0,
        "tury_modelu": 0,
        "publikacje_potwierdzone": 0,
        "incydenty_reka": 0,
        "korekty_wyslane": 0,
        "korekty_w_zasiegu": 0,
    }
    base.update(raw_counts)
    return {
        "projekt": {"id": "p", "nazwa": "Projekt", "organizacja": "o"},
        "okno_godzin": 24,
        "kontekst": {"obudzen": 0, "sesji": 0, "publikacji": 0, "korekt": 0},
        "surowe": base,
    }


def test_the_thresholds_are_read_from_the_frozen_file_not_from_the_script() -> None:
    """Gdyby progi były w skrypcie, zmiana w pliku nie zmieniałaby werdyktu."""
    assert CRITERIA.exists(), CRITERIA
    thresholds = report.load_thresholds()
    assert sorted(thresholds["progi"]) == [
        "interwencje_czlowieka",
        "korekty_docieraja",
        "koszt_na_potwierdzona_publikacje",
        "obudzenia_bez_modelu",
        "potwierdzone_widocznosci",
    ]
    assert thresholds["zamrozone_dnia"], "raport bez daty zamrożenia progów nie jest dowodem"


def test_the_fallback_reader_agrees_with_the_real_one() -> None:
    """Host bez PyYAML ma czytać te same liczby, nie przybliżone."""
    pyyaml = pytest.importorskip("yaml")
    full = pyyaml.safe_load(CRITERIA.read_text(encoding="utf-8"))
    simple = report._thresholds_without_yaml(CRITERIA.read_text(encoding="utf-8"))
    assert sorted(simple["progi"]) == sorted(full["progi"])
    for name, body in full["progi"].items():
        assert simple["progi"][name]["prog"] == float(body["prog"]), name
    assert simple["zamrozone_dnia"] == full["zamrozone_dnia"]


def test_zero_in_the_denominator_is_not_zero_percent() -> None:
    assert report.ratio(0, 0) is None
    assert report.ratio(0, 4) == 0.0
    assert report.ratio(3, 4) == 0.75


def test_a_window_shorter_than_a_day_gets_no_verdict() -> None:
    rows = report.measurements(
        _raw(puste_cykle=10, puste_cykle_bez_modelu=10), report.load_thresholds()
    )
    assert [row.verdict(True) for row in rows] == [report.NOT_MEASURED] * 5
    # A na pełnej dobie ta sama liczba jest już oceniana.
    assert rows[0].verdict(False) == "GO"


def test_no_confirmed_publication_is_not_a_cheap_project() -> None:
    """Zero potwierdzonych publikacji to brak pomiaru kosztu, nie koszt zerowy."""
    rows = report.measurements(_raw(tury_modelu=40), report.load_thresholds())
    cost = next(row for row in rows if row.name == "koszt_na_potwierdzona_publikacje")
    assert cost.value is None
    assert cost.verdict(False) == report.NOT_MEASURED


def test_an_expensive_project_fails_its_threshold() -> None:
    raw = _raw(tury_modelu=40, publikacje_potwierdzone=1)
    rows = report.measurements(raw, report.load_thresholds())
    cost = next(row for row in rows if row.name == "koszt_na_potwierdzona_publikacje")
    assert cost.value == 40.0
    assert cost.verdict(False) == "NO_GO"


def test_the_report_says_what_it_does_not_know_and_carries_the_freeze_date() -> None:
    rows = report.measurements(_raw(), report.load_thresholds())
    text = report.render(_raw(), rows, False, "2026-09-19")
    assert "Progi zamrożone 2026-09-19" in text
    assert "Czego ten raport NIE mówi" in text
    assert "czy agent ZASTOSOWAŁ korektę" in text
    # Zasada z 4 września: bez długich pauz w tekście, który czyta człowiek.
    for dash in ("\u2014", "\u2013"):
        assert dash not in text, dash


def test_a_silent_collector_is_named_in_the_report() -> None:
    rows = report.measurements(_raw(), report.load_thresholds())
    text = report.render(_raw(), rows, False, "2026-09-19")
    assert "kolektor nie obudził agenta ani razu" in text
