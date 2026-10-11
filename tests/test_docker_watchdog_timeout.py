"""Strażnik Dockera nie może zawisnąć na własnym poleceniu ratunkowym (D-029 pkt 6).

CO TO ZAMYKA. 26.09 o 01:55 strażnik wykrył martwego demona i wywołał `docker desktop restart`.
To wywołanie wisiało 17 godzin. Strażnik czekał na nie w `wait`, a launchd nie startuje drugiej
kopii zadania, dopóki pierwsza żyje, więc przez te 17 godzin NIKT nie próbował podnieść Dockera
i cały stack stał. Obie granice czasu w skrypcie stały wtedy na
`perl -e 'alarm N; exec @ARGV' docker ...`, co wygląda jak zegar, ale nim nie jest: `docker`
jest binarką Go, a runtime Go przechwytuje SIGALRM i go ignoruje. Zmierzone na tym hoście:
`perl -e 'alarm 4; exec @ARGV' docker logs -f ...` żyło dalej po 40 sekundach.

Test celuje w samą funkcję `limit_czasu`, bo to ona jest gwarancją: strażnik ma prawo
przegrać restart, nie ma prawa przestać próbować.

Nie wymaga Dockera ani sieci, SIGALRM ignoruje tu proces zastępczy, tak jak robi to Go.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import time

import pytest

WATCHDOG = pathlib.Path(__file__).resolve().parents[1] / "tools" / "host" / "docker_watchdog.sh"

#: Proces, który udaje binarkę Go: ignoruje SIGALRM i SIGTERM, więc ubić go może wyłącznie
#: SIGKILL. Dokładnie ta odporność zamieniła stary `alarm` w zegar bez skutku.
GLUCHY_NA_ALARM = "trap '' ALRM TERM; sleep 60"


def _timeout_function() -> str:
    """Sama `limit_czasu` wycięta ze skryptu: reszta strażnika restartuje Docker Desktop."""
    text = WATCHDOG.read_text(encoding="utf-8")
    match = re.search(r"^limit_czasu\(\) \{.*?^\}", text, re.MULTILINE | re.DOTALL)
    assert match, "w strażniku nie ma już funkcji limit_czasu, test stracił przedmiot"
    return match.group(0)


def _run(command: str, timeout_s: float = 60.0) -> tuple[int, float]:
    """Odpala fragment bash-a korzystający z `limit_czasu` i zwraca (kod wyjścia, czas)."""
    script = f"{_timeout_function()}\n{command}\n"
    start = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=timeout_s
    )
    return result.returncode, time.monotonic() - start


def test_a_process_deaf_to_the_alarm_is_killed_within_the_limit() -> None:
    """Oryginalna awaria: polecenie ignorujące SIGALRM ma zginąć, a nie przeżyć strażnika."""
    kod, czas = _run(f"limit_czasu 3 bash -c {GLUCHY_NA_ALARM!r}")
    assert kod == 124, f"przekroczony limit ma dać 124, dostaliśmy {kod}"
    assert czas < 15, f"limit 3 s trwał {czas:.1f} s, zegar znowu nie zabija"


def test_a_killed_command_does_not_pass_for_a_success() -> None:
    """Kat ubija najpierw dzieci, więc rodzic zdąża zwrócić 0.

    Gdyby limit czytać z kodu wyjścia, ubity `docker desktop restart` zostałby zapisany
    w logu jako udany restart, a strażnik wyzerowałby licznik porażek i przestał ratować.
    To fałszywe zaliczenie w rozumieniu KONWENCJI, nie kosmetyka.
    """
    kod, _ = _run("limit_czasu 2 bash -c 'sleep 30 & wait'")
    assert kod == 124, f"ubity rodzic zwracający 0 musi zostać zgłoszony jako 124, nie {kod}"


def test_a_command_within_the_limit_returns_its_own_code() -> None:
    """Zegar nie może zmieniać wyniku poleceń, które zdążyły."""
    kod_ok, czas = _run("limit_czasu 30 true")
    assert kod_ok == 0
    assert czas < 10, f"szybkie polecenie czekało {czas:.1f} s, zegar blokuje zamiast pilnować"

    error_code, _ = _run("limit_czasu 30 bash -c 'exit 3'")
    assert error_code == 3, f"własny kod błędu ma przejść nietknięty, dostaliśmy {error_code}"


def _executable_lines() -> list[str]:
    """Linie skryptu bez komentarzy i bez treści cudzysłowów.

    Rozdział jest tu istotny: i komentarz, i `log "…"` zawierają te same słowa co prawdziwe
    wywołania, więc test czytający surowy tekst pytałby o dokumentację zamiast o zachowanie.
    Wpis do logu bywa doklejony warunkiem (`[ … ] && log "…"`), więc nie wystarczy odsiać
    linii zaczynających się od `log`, wycinamy każdy cytowany fragment.
    """
    lines = []
    for row in WATCHDOG.read_text(encoding="utf-8").splitlines():
        without_quotes = re.sub(r'"[^"]*"', '""', row)
        without_indent = without_quotes.strip()
        if not without_indent or without_indent.startswith("#"):
            continue
        lines.append(without_quotes)
    return lines


def test_the_watchdog_no_longer_uses_the_perl_alarm() -> None:
    """Regresja na kodzie: powrót `perl -e 'alarm N; exec'` przywraca zegar, który Go ignoruje."""
    offending = [entry for entry in _executable_lines() if "perl" in entry and "alarm" in entry]
    assert not offending, (
        "strażnik znowu ogranicza czas perlowym alarmem, a docker jest binarką Go "
        f"i SIGALRM ignoruje: {offending}"
    )


@pytest.mark.parametrize("call", ["docker info", "docker desktop restart"])
def test_both_docker_paths_have_a_time_limit(call: str) -> None:
    """Zawiesić może się zarówno sonda, jak i restart, obie muszą przechodzić przez limit."""
    lines = [entry for entry in _executable_lines() if call in entry]
    assert lines, f"nie znaleziono wywołania {call} w strażniku"
    without_limit = [entry for entry in lines if "limit_czasu" not in entry]
    assert not without_limit, f"{call} jest wołane bez granicy czasu: {without_limit}"
