"""Limit rozmowy: ile go jest, skąd się bierze i jak o nim mówimy (20.09, 01:02).

UWAGA ZAŁOŻYCIELA, od której to się zaczęło: rozmowa skończyła się po trzech jego zdaniach
komunikatem „Budżet sesji wyczerpany: tool_calls 5, turns 3", a on odczytał to jako limit swojego
konta Codex („mam limity, czemu pokazuje, że nie mam"). Dwie usterki w jednym zdaniu: limit był
ustawiony pod jednorazowe zlecenie, a nie pod rozmowę, i przedstawiał się nazwami kluczy, więc
wyglądał na cudzy.

Te testy nie potrzebują bazy: mierzą liczby i zdania, czyli dokładnie to, co człowiek zobaczy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from control_plane.sessions.handlers import (
    DEFAULT_BUDGET,
    PRESENCE_BUDGET,
    budget_of,
    budget_sentence,
)
from control_plane.sessions.store import SESSION_CONVERSATION, SESSION_PRESENCE


@dataclass
class FakeSession:
    kind: str = SESSION_CONVERSATION
    budget: dict[str, Any] | None = None
    id: Any = None

    def __post_init__(self) -> None:
        self.id = self.id or uuid4()


def test_a_conversation_gets_enough_room_for_a_conversation() -> None:
    """Dwieście wymian, nie dwanaście i nie trzydzieści (D-046).

    Tura modelu bierze po kilka wywołań narzędzi na odpowiedź, więc dwanaście tur kończyło
    rozmowę po trzech zdaniach człowieka, a trzydzieści po jednym popołudniu. Rozmowa ma być
    miejscem pracy, więc sufit jest tam, gdzie człowiek go w normalnym dniu nie dotknie.

    `max_minutes` zostaje limitem JEDNEJ TURY i celowo nie jest dobą: z niego liczy się czas
    życia pliku zdolności, a turę, która utknęła, ubija zegar ciszy klienta.
    """
    assert DEFAULT_BUDGET["max_turns"] == 200
    assert DEFAULT_BUDGET["max_tool_calls"] == 800
    assert DEFAULT_BUDGET["max_minutes"] == 180


def test_presence_keeps_the_old_smaller_budget() -> None:
    """Sesja obecności to jeden tik. Limitem, który tam naprawdę wiąże, jest budżet z
    powierzenia; ten pilnuje tylko, żeby tik nie chodził w kółko."""
    assert budget_of(FakeSession(kind=SESSION_PRESENCE)) == PRESENCE_BUDGET
    assert budget_of(FakeSession(kind=SESSION_CONVERSATION)) == DEFAULT_BUDGET


def test_what_the_project_set_wins_over_the_default() -> None:
    session = FakeSession(budget={"max_turns": 80})
    assert budget_of(session)["max_turns"] == 80
    # Reszta zostaje domyślna: ustawienie jednej liczby nie kasuje dwóch pozostałych.
    assert budget_of(session)["max_tool_calls"] == DEFAULT_BUDGET["max_tool_calls"]


def test_nonsense_in_the_stored_budget_is_ignored_rather_than_obeyed() -> None:
    """Zero tur i klucz spoza listy to nie są ustawienia, tylko uszkodzone dane. Posłuszeństwo
    wobec nich dałoby rozmowę, która nie może się zacząć, i nikt by nie wiedział czemu."""
    session = FakeSession(budget={"max_turns": 0, "co_to": 5, "max_tool_calls": -3})
    assert budget_of(session) == DEFAULT_BUDGET


def test_the_sentence_says_whose_limit_it_is_and_what_to_do() -> None:
    # Liczby brane Z BUDŻETU, nie przepisane: test o treści zdania nie ma padać przy każdej
    # zmianie sufitu, bo wtedy sufit podnosi się razem z usunięciem asercji.
    reached = DEFAULT_BUDGET["max_turns"] + 1
    sentence = budget_sentence(DEFAULT_BUDGET, {"turns": reached, "tool_calls": 12})

    assert "limitu ustawionego w Wenext" in sentence
    assert f"{DEFAULT_BUDGET['max_turns']} wymian zdań" in sentence
    # Trzy rzeczy, bez których to zdanie już raz wprowadziło w błąd.
    assert "To nie jest limit Twojego konta Codex" in sentence
    assert "Zacznij nową rozmowę" in sentence
    assert "ustawieniach projektu" in sentence
    # I ani jednej nazwy klucza.
    for key in ("tool_calls", "max_turns", "turns", "budget"):
        assert key not in sentence, key


def test_the_sentence_names_the_limit_that_was_actually_reached() -> None:
    tools = budget_sentence(
        DEFAULT_BUDGET, {"turns": 3, "tool_calls": DEFAULT_BUDGET["max_tool_calls"] + 1}
    )
    assert "użyć narzędzi" in tools and "wymian zdań" not in tools


def test_a_limit_we_cannot_name_still_tells_the_truth() -> None:
    """Liczniki, których nie umiemy przypisać, nie mogą dać zdania „wyczerpano 0 wymian":
    wtedy człowiek szuka błędu tam, gdzie go nie ma."""
    sentence = budget_sentence(DEFAULT_BUDGET, {"turns": 1, "tool_calls": 1})
    assert "ustawionego limitu" in sentence
    assert "0" not in sentence
