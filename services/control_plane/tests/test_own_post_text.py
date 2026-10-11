"""Tytuł i treść własnego postu w JEDNYM polu, które wchodzi do odcisku zgody (0023).

DLACZEGO TO MA WŁASNY PLIK I ŻADNEJ BAZY. To jest czysta zasada, a zasada ma się dać sprawdzić
bez stawiania czegokolwiek: sklejenie i rozdzielenie muszą być swoimi odwrotnościami, bo inaczej
na platformie stanie wątek o tytule, którego człowiek nie czytał w tej postaci. Reszta drogi
własnego postu jest w `test_presence_own_post.py`, przy bazie.
"""

from __future__ import annotations

import pytest
from control_plane.pilot.publication import join_own_post, split_own_post

TITLE = "Czego nauczył nas tydzień z operatorem bez testów"
BODY = "Trzy rzeczy, po kolei, bez morałów."


def test_the_title_survives_the_round_trip_through_the_consented_field() -> None:
    joined = join_own_post(TITLE, BODY)
    assert split_own_post(joined) == (TITLE, BODY)


def test_a_title_that_would_split_wrong_is_refused_where_it_is_written() -> None:
    """Tytuł z nową linią rozjechałby sklejenie z rozdzieleniem, a rozjechanie się akurat tego
    znaczy wątek o tytule, którego człowiek nie czytał."""
    for bad in ["", "   ", "tytuł\nz nową linią", "x" * 301]:
        with pytest.raises(ValueError):
            join_own_post(bad, BODY)


def test_a_field_without_the_separator_is_all_title_and_no_body() -> None:
    """Zgadywanie w drugą stronę (pierwsze zdanie jako tytuł) publikowałoby coś, czego człowiek
    nie widział w tej postaci."""
    assert split_own_post("samo zdanie") == ("samo zdanie", "")
