"""Karta SILNIK: co widzi człowiek, który wchodzi do produktu bez połączenia Codex (E1).

AUDYT UX 20.09, ustalenie 1: pole do pisania było WYŁĄCZONE bez połączenia, a tekst odsyłał na
ekran administracyjny z `auth.json` i ścieżką katalogu. Pierwszy cel nie dawał się zapisać, a
wiedza potrzebna do odblokowania była wiedzą operatora wdrożenia, nie klienta.

Te testy trzymają kształt karty bez bazy; przejścia (cel zapisany, tura rusza po zalogowaniu,
anulowanie wraca do braku połączenia) mierzy suita z bazą.
"""

from __future__ import annotations

import pytest
from control_plane.sessions.engine_card import (
    CONNECTIONS_PATH,
    STATE_OF_STATUS,
    _nothing_connected,
    _rank,
    _ticket,
)
from control_plane.tools.cards import (
    ENGINE_CONNECTED,
    ENGINE_CONNECTING,
    ENGINE_DISCONNECTED,
    STATE_ERROR,
    engine_card,
)

pytestmark = [pytest.mark.p2]


def test_a_product_with_no_engine_shows_one_button_and_no_jargon() -> None:
    """Stan pierwszego wejścia. Ma być zwykły, nie awaryjny: jedna akcja, żadnej ścieżki na dysku."""
    card = _nothing_connected()

    assert card["rodzaj"] == "SILNIK"
    assert card["karta_id"] == "silnik", "jedna karta na rozmowę, kolejne tury jej nie mnożą"
    assert card["stan"] == ENGINE_DISCONNECTED
    assert [one["nazwa"] for one in card["akcje"]] == ["polacz"]
    assert card["akcje"][0]["trasa"] == {"metoda": "POST", "sciezka": CONNECTIONS_PATH}
    assert card["akcje"][0]["payload"]["label"], "trasa wymaga etykiety, więc karta ją niesie"
    for forbidden in ("auth.json", "home_ref", "katalog", "CODEX_HOME"):
        assert forbidden not in str(card), f"{forbidden} to wiedza operatora, nie klienta"


def test_a_login_in_progress_carries_the_address_and_the_code() -> None:
    """W trakcie logowania urządzenia człowiek ma do zrobienia jedną rzecz: przepisać kod."""
    card = engine_card(
        state=ENGINE_CONNECTING,
        connection="11111111-1111-1111-1111-111111111111",
        label="Moje konto Codex",
        login_url="https://chatgpt.com/device",
        code="ABCD-1234",
        actions=[],
    )

    assert card["stan"] == ENGINE_CONNECTING
    assert card["adres_logowania"] == "https://chatgpt.com/device"
    assert card["kod"] == "ABCD-1234"


def test_the_states_of_a_connection_row_map_onto_four_card_states() -> None:
    assert STATE_OF_STATUS["CONNECTED"] == ENGINE_CONNECTED
    assert STATE_OF_STATUS["PENDING_LOGIN"] == ENGINE_CONNECTING
    assert STATE_OF_STATUS["ERROR"] == STATE_ERROR
    assert STATE_OF_STATUS["DISCONNECTED"] == ENGINE_DISCONNECTED


def test_the_connection_closest_to_working_answers_first() -> None:
    """Pytanie karty brzmi „czy praca ma na czym ruszyć", więc odpowiada połączenie najbliżej
    działania, a nie najstarsze."""
    assert _rank("CONNECTED") < _rank("PENDING_LOGIN") < _rank("ERROR") < _rank("DISCONNECTED")
    assert _rank("COS_NOWEGO") > _rank("DISCONNECTED"), "nieznany stan nie wyprzedza znanych"


def test_a_ticket_survives_being_a_string_and_a_mess() -> None:
    """`login_ticket` to `jsonb`, a surowy odczyt bywa napisem; nieparsowalny ma dać pustkę,
    a nie wyjątek w turze, która akurat odkryła brak silnika."""
    assert _ticket('{"user_code": "A-1"}') == {"user_code": "A-1"}
    assert _ticket("nie-json") == {}
    assert _ticket(None) == {}
    assert _ticket({"user_code": "A-1"})["user_code"] == "A-1"


def test_an_error_card_says_what_went_wrong_and_offers_another_try() -> None:
    card = engine_card(
        state=STATE_ERROR,
        connection="22222222-2222-2222-2222-222222222222",
        label="Moje konto Codex",
        reason="logowanie wygasło",
        actions=[],
    )

    assert card["stan"] == STATE_ERROR
    assert card["powod"] == "logowanie wygasło"


# ---------------------------------------------------------------------------
# Powód na karcie jest zdaniem dla człowieka, nie zapisem dla wdrażającego
# ---------------------------------------------------------------------------


def test_the_reason_on_the_card_carries_no_host_path() -> None:
    """ZMIERZONE NA ŻYWYM ZRZUCIE 20.09 (zgłoszenie wenext-ce): na karcie stało

        „nie udało się uruchomić app-servera Codeksa na /private/var/folders/g8/.../
         tmp.FpRzO0QqEM/8e41f485-… [Errno 2] No such file or directory:
         '/nonexistent/wenext-brak-codexa'"

    Warunek odbioru E1 mówi wprost, że na karcie nie ma wiedzy operatora wdrożenia. Ścieżka w
    katalogu tymczasowym hosta jest dokładnie tym: klientowi nie mówi nic, a wygląda jak awaria,
    za którą to on odpowiada. Pełny tekst zostaje w `codex_connections.last_error`.
    """
    from control_plane.sessions.engine_card import human_reason

    raw = (
        "nie udało się uruchomić app-servera Codeksa na /private/var/folders/g8/"
        "tmp.FpRzO0QqEM/8e41f485-1 [Errno 2] No such file or directory: "
        "'/nonexistent/wenext-brak-codexa'"
    )

    sentence = human_reason(raw)

    assert sentence == "nie udało się uruchomić Codexa z tego połączenia"
    for forbidden in ("/", "Errno", "app-server", "tmp."):
        assert forbidden not in sentence, f"{forbidden} to wiedza operatora, nie klienta"


def test_an_unrecognised_error_gets_a_sentence_instead_of_its_own_text() -> None:
    """Czego nie rozpoznamy, dostaje zdanie ogólne.

    Przepuszczanie nieznanego tekstu „bo może jest nieszkodliwy" jest tą samą decyzją, która
    wpuściła na kartę ścieżkę hosta: nikt nie wie z góry, co silnik wpisze w `last_error`.
    """
    from control_plane.sessions.engine_card import ENGINE_FAILED, human_reason

    assert human_reason("panic: goroutine 1 [running] /go/src/x.go:42") == ENGINE_FAILED
    assert human_reason(None) == "logowanie nie powiodło się"
    assert human_reason("   ") == "logowanie nie powiodło się"


def test_the_recognised_shapes_each_get_their_own_sentence() -> None:
    """Cztery kształty, cztery zdania, bo każde mówi człowiekowi co innego do zrobienia."""
    from control_plane.sessions.engine_card import human_reason

    assert "za długo" in human_reason("login timed out after 300s")
    assert "odmówił" in human_reason("HTTP 401 Unauthorized")
    assert "dobić" in human_reason("connection refused")
    assert "uruchomić" in human_reason("[Errno 2] No such file or directory")
