"""Jak nazywa się rozmowa i co z niej wyszło (D-045), bez bazy.

CZEGO PILNUJĄ TE TESTY. Tytuł rozmowy zobaczy człowiek w pasku, model w kontekście startowym i
ekran w nagłówku. Gdyby liczyło go każde z tych miejsc po swojemu, ta sama rozmowa nazywałaby się
w trzech miejscach na trzy sposoby, a człowiek nie miałby jak zgadnąć, że to ta sama.
"""

from __future__ import annotations

from control_plane.sessions.summary import MAX_TITLE_CHARS, one_sentence, title_of


def test_the_title_is_the_first_sentence_of_the_goal() -> None:
    assert title_of("Sprawdź, czemu odzew jest mały. Potem wróć do mnie.") == (
        "Sprawdź, czemu odzew jest mały."
    )


def test_a_goal_that_cannot_be_named_gives_null_not_an_empty_string() -> None:
    """`null`, bo pustkę ekran musiałby rozpoznawać po długości i wymyślać zastępczą nazwę,
    czyli definicja nazwy wróciłaby do dwóch miejsc."""
    for goal in ("", "   ", "\n\t", None, "ok", "?"):
        assert title_of(goal) is None, goal


def test_a_long_goal_is_cut_at_a_word_and_says_that_it_was_cut() -> None:
    goal = "Zbadaj, jak ludzie w tej społeczności mówią o cenach, i zaproponuj, co z tym zrobić"
    title = title_of(goal)
    assert title is not None
    assert len(title) <= MAX_TITLE_CHARS
    assert title.endswith("…"), title
    assert not title[:-1].endswith(" "), "ucięcie nie zostawia spacji przed wielokropkiem"
    assert goal.startswith(title[:-1].rstrip("…").strip())


def test_a_goal_exactly_at_the_limit_is_not_cut() -> None:
    goal = "x" * MAX_TITLE_CHARS
    assert title_of(goal) == goal


def test_the_outcome_is_one_sentence_and_absence_is_null() -> None:
    assert one_sentence("Gotowe. Szczegóły w rozmowie.") == "Gotowe."
    assert one_sentence("") is None
    assert one_sentence(None) is None


def test_whitespace_does_not_change_the_name_of_a_conversation() -> None:
    """Ta sama rozmowa nie może nazywać się dwa razy inaczej przez podwójną spację."""
    assert title_of("Sprawdź   to\n  dokładnie.") == title_of("Sprawdź to dokładnie.")


def test_activity_excerpt_is_readable_without_markdown_or_full_document() -> None:
    assert (
        one_sentence("## **Zapisane** w [dokumencie](/knowledge).\n\nCały długi materiał.")
        == "Zapisane w dokumencie."
    )


def test_a_list_marker_is_not_the_sentence() -> None:
    # Measured 27.09: a background session's summary began with a bullet, and the screen showed
    # "- Pierwsza odpowiedź..."; a numbered summary would have shown "1." alone.
    assert one_sentence("- Odpowiedź nie wyszła. Szczegóły niżej.") == "Odpowiedź nie wyszła."
    assert one_sentence("1. Opublikowałem post. 2. Zaplanowałem powrót.") == "Opublikowałem post."
    assert one_sentence("* Zapisane.") == "Zapisane."
    assert one_sentence("2026 był rokiem. Tak.") == "2026 był rokiem."
