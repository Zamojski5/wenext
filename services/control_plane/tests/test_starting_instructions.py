"""Instrukcje startowe: kiedy hipoteza jest zadaniem, a kiedy nie (D-044 skorygowane D-046).

DLACZEGO TO MA TEST. Poprzednia wersja tekstu mówiła „zacznij pracę od wypracowania hipotezy",
więc prośba „sprawdź, gdzie warto promować ten projekt" potrafiła zamienić się w wywiad zamiast
w research. Zmiana jest w JEDNYM akapicie promptu i nic w kodzie nie upadnie, gdy ktoś ją cofnie,
więc pilnuje jej test na treści: co ten akapit musi mówić i czego nie ma prawa mówić.
"""

from __future__ import annotations

import pytest
from control_plane.sessions.context import StartingContext

pytestmark = [pytest.mark.p2]


def _context(*, has_hypothesis: bool) -> StartingContext:
    return StartingContext(
        goal="Sprawdź, gdzie warto promować ten projekt.",
        project_name="Wenext",
        project_mode="REVIEW",
        accounts=("reddit/anna",),
        communities=("r/SaaS",),
        memory_entries=3,
        has_hypothesis=has_hypothesis,
        tool_names=("catalog.list", "memory.write"),
    )


def test_a_project_without_a_hypothesis_does_not_start_with_an_interview() -> None:
    text = _context(has_hypothesis=False).render()

    assert "NIE MA JESZCZE ŻADNEJ HIPOTEZY" in text, "stan projektu nadal jest powiedziany wprost"
    assert "Nie jest to powód, żeby zaczynać od wywiadu" in text
    assert "zwykły research" in text and "od razu" in text
    assert "Zacznij pracę od wypracowania" not in text, (
        "to zdanie zamieniało każdą prośbę w wywiad i ma nie wrócić"
    )


def test_a_user_agreed_hypothesis_requires_their_statement() -> None:
    """User attribution needs confirmation; an agent's own interpretation does not."""
    text = _context(has_hypothesis=False).render()

    assert "gdy on potwierdzi ją słowami" in text
    assert "payload.confirmed_at_seq" in text
    assert "Nie zakładaj hipotezy w milczeniu" in text


def test_a_project_that_has_one_gets_no_paragraph_about_it() -> None:
    text = _context(has_hypothesis=True).render()

    assert "HIPOTEZY" not in text
    assert "confirmed_at_seq" not in text


def test_the_session_instructions_carry_the_founders_rule_on_long_dashes() -> None:
    """The founder's rule of 2026-09-04: no em or en dash in any text the model writes. Every
    observe prompt carries it; the session instructions, written later, did not (27.09), and the
    agent's summaries showed them on the founder's screen. The text names the characters by code
    point and never contains them."""
    from control_plane.sessions.context import BASE_INSTRUCTIONS

    assert "U+2014" in BASE_INSTRUCTIONS and "U+2013" in BASE_INSTRUCTIONS
    assert "szkicach publikacji" in BASE_INSTRUCTIONS
    assert not any(char in BASE_INSTRUCTIONS for char in "\u2014\u2013")
