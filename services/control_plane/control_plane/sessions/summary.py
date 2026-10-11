"""Jak nazywa się rozmowa i co z niej wyszło, w jednym miejscu (D-045).

DLACZEGO TO JEST MODUŁ, A NIE DWIE FUNKCJE W DWÓCH PLIKACH. Te same dwa zdania czytają dziś trzy
miejsca: pasek rozmów projektu, indeks poprzednich rozmów w kontekście startowym sesji i lista
sesji w API. Gdyby każde liczyło je po swojemu, ta sama rozmowa nazywałaby się w pasku inaczej
niż w prompcie modelu, a człowiek nie miałby jak zgadnąć, że to ta sama.

LICZONE PO STRONIE SERWERA, nie w przeglądarce, i to jest ta sama decyzja, co przy `_state_of`
w `api/presence.py`: odpowiedź na pytanie „jak nazywa się ta rozmowa" ma mieć w produkcie JEDNĄ
definicję. Przeglądarka dostaje gotowy napis albo `null`; `null` znaczy „nie da się nazwać",
a nie „pusty napis", żeby ekran nie musiał rozpoznawać pustki po długości i wymyślać zastępczej
nazwy, bo wtedy definicja wróciłaby do dwóch miejsc.
"""

from __future__ import annotations

import re

#: Ile znaków ma tytuł w pasku. Osiemdziesiąt, bo tyle mieści się w wąskiej kolumnie bez zawijania.
MAX_TITLE_CHARS = 80

#: Ile znaków ma jednozdaniowy wynik. Dłuższy nie jest już jednym zdaniem dla oka.
MAX_SENTENCE_CHARS = 160

#: Koniec zdania: kropka, wykrzyknik albo pytajnik, po których stoi spacja albo koniec tekstu.
#: Skróty („np.", „itd.") potrafią to oszukać i czasem utną tytuł wcześniej, niż człowiek by chciał.
#: Zostaje tak świadomie: lista skrótów byłaby kolejną rzeczą do utrzymania, a najgorsze, co się
#: może stać, to tytuł krótszy o kilka słów, nie tytuł nieprawdziwy.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _squeezed(text: str | None) -> str:
    return " ".join((text or "").split())


def _first_sentence(text: str, limit: int) -> str:
    first = _SENTENCE_END.split(_squeezed(text), maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    # Ucinamy na granicy słowa, żeby nie zostawiać połowy wyrazu; wielokropek mówi, że to skrót.
    cut = first[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut or first[: limit - 1]}…"


def title_of(goal: str | None) -> str | None:
    """Pierwsze zdanie celu jako nazwa rozmowy, albo `None`.

    `None` DLA CELU, KTÓREGO NIE DA SIĘ NAZWAĆ: pustego, złożonego z samych znaków białych albo
    tak krótkiego, że nie jest nazwą niczego („ok", „?"). Ekran pisze wtedy „Rozmowa bez nazwy"
    i to jest jedyne miejsce, gdzie ta decyzja stoi.
    """
    first = _first_sentence(goal or "", MAX_TITLE_CHARS)
    return first if len(first) >= 3 else None


def one_sentence(summary: str | None) -> str | None:
    """Jedno zdanie wyniku rozmowy, albo `None`, gdy rozmowa jeszcze nic nie powiedziała.

    Pełny `result_summary` zostaje nietknięty i jedzie obok: pasek pokazuje krótkie, ekran pełne.
    Skracanie ZAWSZE po stronie serwera, z tego samego powodu co tytuł.
    """
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary or "")
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    # A list marker is not the start of the sentence (27.09: "- Pierwsza odpowiedź..." on the
    # presence screen); a numbered one would even end it, "1." being a sentence to the splitter.
    text = re.sub(r"(?m)^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    first = _first_sentence(text, MAX_SENTENCE_CHARS)
    return first or None
