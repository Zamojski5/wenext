"""Instrukcja z `p4-live-check` ma nazywać to, co naprawdę jest na ekranie.

ZMIERZONE NA LIVE 19.09: pomiar stanął na „brak delegacji RESEARCH" i nie ruszył, bo odmowa
mówiła, CZEGO BRAKUJE, a nie, co z tym zrobić. Instrukcja to naprawiła, ale wprowadziła nowy
sposób na kłamstwo: nazwy ekranów i przycisków wpisane ręcznie żyją własnym życiem, a człowiek
szukający przycisku, którego nie ma, jest w gorszym położeniu niż ten, który nie dostał żadnej
wskazówki, bo najpierw straci czas na szukanie.

Ten test czyta oba pliki i porównuje: każdy fragment w cudzysłowie drukarskim z instrukcji musi
stać w źródle ekranu obecności. Nie sprawdza układu ani tego, czy przycisk działa, tylko jedno:
czy te słowa w ogóle padają tam, gdzie instrukcja je wskazuje.
"""

from __future__ import annotations

import re
from pathlib import Path

from control_plane.presence.livecheck import HOW_TO_SIGN

ROOT = Path(__file__).resolve().parents[3]
SCREEN = ROOT / "apps/dashboard/app/(studio)/w/[workspaceId]/p/[projectId]/presence/page.tsx"

#: Fragment w cudzysłowie drukarskim: „tak to jest napisane na ekranie”.
_QUOTED = re.compile(r"„([^”]+)”")


def _screen_text() -> str:
    """Źródło ekranu z rozwiniętymi encjami i bez łamania wierszy w JSX.

    Tekst w JSX bywa zawinięty przez formatter, więc „Konta, z których wolno pisać” może stać w
    dwóch liniach. Porównanie po zgnieceniu białych znaków pyta o słowa, a nie o formatowanie.
    """
    raw = SCREEN.read_text("utf-8")
    for entity, sign in (("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        raw = raw.replace(entity, sign)
    return " ".join(raw.split())


def test_the_instruction_names_things_that_are_really_on_the_screen() -> None:
    assert SCREEN.exists(), f"nie widzę ekranu obecności pod {SCREEN}"
    screen = _screen_text()
    quoted = _QUOTED.findall(HOW_TO_SIGN)
    assert len(quoted) >= 8, (
        f"instrukcja wskazuje tylko {len(quoted)} nazw; to za mało na krok po kroku"
    )
    missing = [one for one in quoted if " ".join(one.split()) not in screen]
    assert missing == [], (
        f"instrukcja wskazuje nazwy, których nie ma na ekranie obecności: {missing}. "
        "Albo ekran się zmienił, albo instrukcja zmyśla."
    )


def test_the_instruction_says_that_it_signs_nothing_itself() -> None:
    """Zgoda jest tym, co odróżnia agenta od skryptu, więc polecenie ma to powiedzieć wprost,
    zamiast zostawiać człowieka z pytaniem, czy przypadkiem samo czegoś nie podpisało."""
    squeezed = " ".join(HOW_TO_SIGN.split())
    assert "NICZEGO NIE PODPISZE" in squeezed, HOW_TO_SIGN
    assert "RESEARCH" in squeezed and "OWN_POST" in squeezed
