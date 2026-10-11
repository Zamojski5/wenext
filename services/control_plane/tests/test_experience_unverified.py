"""`UNVERIFIED` nie jest doświadczeniem (P8 etap B, 21.09).

CO SIĘ ZMIENIŁO I DLACZEGO. Przemiatanie doświadczeń zapisywało po dobie „po N godzinach nikt nie
zareagował" dla KAŻDEJ publikacji, także takiej, której nigdy nie udało się odczytać anonimowo.
To zdanie mówi o cudzym zachowaniu, a zmierzony był wyłącznie nasz brak wiedzy: nie wiadomo nawet,
czy ktokolwiek to widział. Następna sesja czytała je jako lekcję o społeczności i o sobie.

Teraz brak reakcji zapisujemy tylko tam, gdzie widoczność jest POTWIERDZONA. Zaobserwowana reakcja
jest osobnym dowodem widoczności, więc odpowiedzi i punkty liczą się niezależnie od kolumny:
skoro ktoś odpowiedział, to zobaczył.

Bez bazy: `outcome_of` jest funkcją czystą, a to jest pytanie o regułę, nie o wiersze.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from control_plane.knowledge.experience import (
    OUTCOME_NO_REACTION,
    OUTCOME_REPLIED,
    Measured,
    outcome_of,
)

pytestmark = [pytest.mark.p8]


def _measured(
    *,
    visibility: str,
    still_present: bool | None,
    hours: float = 40.0,
    score: int | None = None,
    replies: int = 0,
) -> Measured:
    published = datetime.now(UTC) - timedelta(hours=hours)
    return Measured(
        request_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        published_at=published,
        target_url="https://www.reddit.com/r/devops/comments/x/_/y/",
        public_visibility=visibility,
        removal_source=None,
        decision="HELP",
        move="NUMBER",
        draft="tekst odpowiedzi",
        community_key="r/devops",
        platform="REDDIT",
        thread_title="wątek",
        thread_external_id="t3_x",
        thread_created_at=published - timedelta(hours=4),
        account="konto",
        person_profile_id=None,
        project_id=None,
        observed_at=datetime.now(UTC) if still_present is not None else None,
        still_present=still_present,
        score=score,
        reply_count=replies,
    )


def test_a_publication_nobody_could_verify_writes_nothing_even_after_a_day() -> None:
    """NAJWAŻNIEJSZY TEST TEGO PLIKU. „Nie wiemy" ma zostać brakiem ustalenia, nie porażką.

    Na żywym systemie 38 publikacji ma `UNVERIFIED`; po tej zmianie nie dostaną doświadczenia
    i to jest wynik pomiaru, a nie luka w pamięci.
    """
    row = _measured(visibility="UNVERIFIED", still_present=None)

    assert outcome_of(row) is None


def test_silence_under_a_confirmed_publication_is_still_a_finding() -> None:
    """Druga strona tej samej reguły: przy potwierdzonej widoczności cisza jest wiedzą."""
    row = _measured(visibility="CONFIRMED", still_present=True)

    outcome = outcome_of(row)

    assert outcome is not None and outcome["kind"] == OUTCOME_NO_REACTION


def test_author_presence_cannot_confirm_public_visibility() -> None:
    """The measured row carries an AUTHOR read; it cannot substitute for an anonymous one."""
    row = _measured(visibility="UNVERIFIED", still_present=True)

    assert outcome_of(row) is None


def test_a_reaction_is_its_own_proof_that_somebody_saw_it() -> None:
    """Odpowiedź pod naszym komentarzem znaczy, że ktoś go zobaczył, cokolwiek mówi kolumna."""
    row = _measured(visibility="UNVERIFIED", still_present=None, replies=2)

    outcome = outcome_of(row)

    assert outcome is not None and outcome["kind"] == OUTCOME_REPLIED


def test_author_absence_cannot_prove_a_moderation_removal() -> None:
    """A failed author read is not public proof that moderators removed a comment."""
    row = _measured(visibility="UNVERIFIED", still_present=False)

    assert outcome_of(row) is None


def test_before_the_threshold_a_confirmed_publication_is_still_too_early() -> None:
    """Potwierdzona widoczność nie skraca progu: po dwudziestu minutach nie ma czego zapisać."""
    row = _measured(visibility="CONFIRMED", still_present=True, hours=0.3)

    assert outcome_of(row) is None
