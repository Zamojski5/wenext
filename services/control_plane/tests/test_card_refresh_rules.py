"""Przekład rzeczywistości na stan karty, bez bazy (D-046).

Te reguły decydują o tym, co człowiek widzi na karcie, a każda z nich ma jedno zdanie
uzasadnienia w kodzie. Test trzyma je osobno od bazy, bo są czystym przekładem: wiersz wejściowy
w środku, słownik karty na wyjściu.
"""

from __future__ import annotations

import pytest
from control_plane.sessions.projection import _draft_now, _visibility_sentence
from control_plane.tools.cards import (
    DRAFT_NEW,
    DRAFT_PUBLISHED,
    DRAFT_PUBLISHING,
    STATE_ERROR,
    draft_card,
)

pytestmark = [pytest.mark.p2]


def _card(state: str = DRAFT_PUBLISHING) -> dict:
    return draft_card(
        revision="11111111-1111-1111-1111-111111111111",
        title="Propozycja",
        community="r/devops",
        profile="Anna",
        text="odpowiedź agenta",
        state=state,
        delegation_allows=True,
        actions=[],
    )


def test_a_published_request_puts_the_permalink_on_the_card() -> None:
    from datetime import UTC, datetime

    fresh = _draft_now(
        _card(),
        {
            "status": "PUBLISHED",
            "detail": None,
            "public_visibility": "CONFIRMED",
            "public_visibility_checked_at": datetime(2026, 9, 20, 6, 30, tzinfo=UTC),
            "permalink": "https://www.reddit.com/r/devops/comments/abc/x/",
            "revision_state": "APPROVED",
        },
        may_publish=True,
    )

    assert fresh["stan"] == DRAFT_PUBLISHED
    assert fresh["adres"] == "https://www.reddit.com/r/devops/comments/abc/x/"
    assert "Widoczna publicznie" in fresh["widocznosc"]
    assert fresh["akcje"] == []


def test_an_unverified_publication_says_nobody_checked_instead_of_nothing() -> None:
    """„Brak danych to nie zero”: UNVERIFIED znaczy „nie sprawdzono”, a nie „nie ma jej”."""
    fresh = _draft_now(
        _card(),
        {
            "status": "PUBLISHED",
            "detail": None,
            "public_visibility": "UNVERIFIED",
            "public_visibility_checked_at": None,
            "permalink": None,
            "revision_state": "APPROVED",
        },
        may_publish=True,
    )

    assert fresh["stan"] == DRAFT_PUBLISHED
    assert fresh["adres"] is None
    assert "Nikt jeszcze nie sprawdzał" in fresh["widocznosc"]


@pytest.mark.parametrize("status", ["UNKNOWN", "BLOCKED", "SKIPPED", "STOPPED"])
def test_every_way_a_publication_stops_shows_a_reason(status: str) -> None:
    fresh = _draft_now(
        _card(),
        {
            "status": status,
            "detail": None,
            "public_visibility": "UNVERIFIED",
            "public_visibility_checked_at": None,
            "permalink": None,
            "revision_state": "APPROVED",
        },
        may_publish=True,
    )

    assert fresh["stan"] == STATE_ERROR
    assert fresh["powod"], "karta w błędzie bez powodu jest gorsza niż brak karty"


def test_a_superseded_revision_keeps_its_text_and_loses_its_buttons() -> None:
    fresh = _draft_now(
        _card(DRAFT_NEW),
        {
            "status": None,
            "detail": None,
            "public_visibility": None,
            "public_visibility_checked_at": None,
            "permalink": None,
            "revision_state": "REJECTED",
        },
        may_publish=True,
    )

    assert fresh["stan"] == DRAFT_NEW, "tekst dalej jest do przeczytania"
    assert fresh["akcje"] == []
    assert "zastąpiona" in fresh["dlaczego_bez_akcji"]


def test_an_approval_without_a_queue_entry_is_reported_not_smoothed_over() -> None:
    """W tym produkcie wniosek powstaje razem ze zgodą, więc jego brak jest niezgodnością."""
    fresh = _draft_now(
        _card(DRAFT_NEW),
        {
            "status": None,
            "detail": None,
            "public_visibility": None,
            "public_visibility_checked_at": None,
            "permalink": None,
            "revision_state": "APPROVED",
        },
        may_publish=True,
    )

    assert fresh["stan"] == STATE_ERROR
    assert "nie ma wniosku publikacyjnego" in fresh["powod"]


def test_a_draft_nobody_touched_is_left_exactly_as_it_was() -> None:
    card = _card(DRAFT_NEW)

    assert _draft_now(card, None, may_publish=True) == card


def test_a_draft_loses_its_button_when_the_project_lost_its_delegation() -> None:
    """Karta ma mówić to, co ZROBI kliknięcie, także wtedy, gdy nic się w niej nie zmieniło.

    Zmierzone na zrzutach 20.09 (zgłoszenie wenext-ce): projekt bez podpisanej delegacji
    pokazywał „Opublikuj" na karcie ze śladu, bo `delegacja_pozwala` i akcja pochodziły z
    chwili, w której karta powstała. Powierzenie da się w międzyczasie odwołać albo wstrzymać, a
    projekcja jest jedynym miejscem, które o tym wie w chwili odczytu ekranu.
    """
    card = draft_card(
        revision="11111111-1111-1111-1111-111111111111",
        title="Propozycja",
        community="r/devops",
        profile="Anna",
        text="odpowiedź agenta",
        state=DRAFT_NEW,
        delegation_allows=True,
        actions=[
            {"nazwa": "opublikuj", "trasa": {"metoda": "POST", "sciezka": "/x"}, "payload": {}},
            {"nazwa": "popraw", "trasa": {"metoda": "POST", "sciezka": "/y"}, "payload": {}},
        ],
    )

    without = _draft_now(card, None, may_publish=False)

    assert without["delegacja_pozwala"] is False
    assert [one["nazwa"] for one in without["akcje"]] == ["popraw"], (
        "znika TYLKO publikacja; poprawić szkic wolno bez powierzenia"
    )
    assert "powierzenia" in without["dlaczego_bez_akcji"]
    assert _draft_now(card, None, may_publish=True) == card, "z powierzeniem nic się nie zmienia"


def test_the_visibility_sentence_never_invents_a_verdict() -> None:
    assert _visibility_sentence(None, None) is None
    assert "Zniknęła" in _visibility_sentence("REMOVED", None)


# ---------------------------------------------------------------------------
# Kształt karty z TRASY poprawki, mierzony bez bazy
# ---------------------------------------------------------------------------


def test_the_title_of_an_own_post_comes_from_its_own_text() -> None:
    """ZMIERZONE 21.09, 9 porażek w suicie i jedna na e2e wenext-ce.

    Pierwsza wersja tej funkcji pytała `SemanticCapability.OWN_POST`, a takiego elementu nie ma:
    zdolności nazywają się `SUBMIT_POST` i `PUBLISH_CONTENT`, a `OWN_POST` to nazwa z kolumny
    `publication_requests.kind` z 0023. Dwa słowniki na jedno pojęcie, tak samo jak przy
    „dołączeniu" i „obserwacji". Skutkiem był `AttributeError`, czyli 500 na KAŻDEJ zgodzie i
    KAŻDEJ poprawce, a suita bez bazy tego nie widziała, bo te trasy chodzą tylko z bazą.

    Dlatego ten test jest TUTAJ, w pliku bez bazy: pytanie „czy to własny post" jest czystą
    funkcją i nie ma powodu, żeby jego pomiar wymagał Postgresa.
    """
    from community_os_contracts.enums import SemanticCapability
    from control_plane.api.project_approvals import _draft_title
    from control_plane.pilot.publication import join_own_post

    text = join_own_post("Czego nauczył nas tydzień bez testów", "Trzy rzeczy, po kolei.")
    for capability in (SemanticCapability.SUBMIT_POST, SemanticCapability.PUBLISH_CONTENT):
        title = _draft_title({"action_type": capability.value}, text)
        assert title == "Czego nauczył nas tydzień bez testów", capability


def test_the_title_of_a_reply_is_not_invented_from_its_first_words() -> None:
    """Odpowiedź pod cudzym wątkiem tytułu NIE MA i nie wolno go zmyślać.

    Człowiek zobaczyłby na karcie napis, którego nigdzie nie napisał. `fallback` mówi zamiast
    tego, co się z odpowiedzią właśnie stało.
    """
    from community_os_contracts.enums import SemanticCapability
    from control_plane.api.project_approvals import _draft_title

    payload = {"action_type": SemanticCapability.SUBMIT_COMMENT.value}

    assert _draft_title(payload, "Zaczynałbym od kolejki, nie od bazy.") == "Odpowiedź"
    assert _draft_title(payload, "cokolwiek", fallback="Poprawiona odpowiedź") == (
        "Poprawiona odpowiedź"
    )
    assert _draft_title({}, "bez rodzaju akcji") == "Odpowiedź"


def test_the_correction_button_is_there_with_and_without_a_delegation() -> None:
    """`popraw` ZAWSZE, `opublikuj` tylko przy powierzeniu (zgłoszenie wenext-ce, e2e 21.09).

    Trasa poprawki budowała własną listę akcji z samym `opublikuj`, więc w projekcie bez
    powierzenia karta wracała bez jednego przycisku i szkicu nie dało się poprawić drugi raz.
    Jedna funkcja dla obu miejsc znaczy jedną definicję tego, co wolno.
    """
    from control_plane.tools.cards import draft_actions

    without = draft_actions(
        "11111111-1111-1111-1111-111111111111",
        allowed=False,
        workspace_id="w",
        project_id="p",
        text="obecna treść",
    )
    with_delegation = draft_actions(
        "11111111-1111-1111-1111-111111111111",
        allowed=True,
        workspace_id="w",
        project_id="p",
        text="obecna treść",
    )

    assert [one["nazwa"] for one in without] == ["popraw"]
    assert [one["nazwa"] for one in with_delegation] == ["popraw", "opublikuj"]
    assert without[0]["payload"]["text"] == "obecna treść"
    assert without[0]["trasa"]["sciezka"].endswith("/revise")
    assert with_delegation[1]["trasa"]["sciezka"].endswith("/approve")
