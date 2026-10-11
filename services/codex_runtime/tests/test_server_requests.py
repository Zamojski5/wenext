"""Żądania serwer do klienta muszą dostać odpowiedź (awaria live z 19.09).

CO SIĘ STAŁO. Pierwsza prawdziwa sesja na Codeksie wisiała w RUNNING ponad siedem minut i
skończyła się dopiero wygaśnięciem lease'u. Ślad pokazał, że o 18:38:46 UTC app-server przysłał
`mcpServer/elicitation/request` (seq 74), a `AsyncAppServerClient` zapisał to jako zwykłe
zdarzenie i nie odpowiedział. Codex robił dokładnie to, co mówi protokół: czekał na odpowiedź na
żądanie, które wysłał.

RÓŻNICA JEST JEDNĄ LINIĄ FORMATU: ramka z `method` ORAZ `id` to ŻĄDANIE, ramka z `method` bez
`id` to notyfikacja. Przypięty klient (`openai_codex.client._reader_loop`) dzieli je tak samo.

TESTY NIE UDAJĄ TEJ SYTUACJI, tylko ją odtwarzają: atrapa app-servera BLOKUJE na odpowiedzi,
więc klient, który jej nie wyśle, zawiesza turę i test pada na timeout, zamiast przejść przez
przypadek.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codex_runtime.session_client import (
    SILENCE_TIMEOUT_S,
    AsyncAppServerClient,
    _answer_for,
    _server_name,
)

pytestmark = [pytest.mark.p2]


# ---------------------------------------------------------------------------
# Kształt odpowiedzi, bez procesu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "accept", "expected"),
    [
        ("item/commandExecution/requestApproval", True, {"decision": "accept"}),
        ("item/fileChange/requestApproval", False, {"decision": "denied"}),
        ("mcpServer/elicitation/request", True, {"action": "accept"}),
        ("mcpServer/elicitation/request", False, {"action": "decline"}),
    ],
)
def test_the_answer_shape_matches_the_method(method, accept, expected) -> None:
    """Dwa kształty, bo protokół ma dwa. `decision` jest wzięte z domyślnego handlera
    przypiętego klienta, `action` to pisownia MCP; żadne nie jest zgadnięte."""
    assert _answer_for(method, accept=accept) == expected


def test_an_unknown_request_still_gets_an_answer() -> None:
    """Pustym obiektem, ale DOSTAJE. Jedyną rzeczą, której serwer nie przeżyje, jest cisza."""
    assert _answer_for("cos/zupelnie/nowego", accept=False) == {}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"server": "wenext"}, "wenext"),
        ({"serverName": "obcy"}, "obcy"),
        ({"mcpServer": {"name": "wenext"}}, "wenext"),
        ({}, ""),
        ({"server": "   "}, ""),
    ],
)
def test_the_server_name_is_read_defensively(params, expected) -> None:
    """Kształt tego żądania nie jest w przypiętej paczce zamodelowany, więc klucz czytamy
    ostrożnie. Nieznany kształt kończy się ODMOWĄ Z POWODEM, nigdy ciszą."""
    assert _server_name(params) == expected


def test_a_request_from_our_server_is_accepted_and_others_are_not() -> None:
    client = AsyncAppServerClient(
        codex_bin=Path("/nonexistent"), codex_home=Path("/tmp"), sink=None
    )
    accept, server, why = client._decide("mcpServer/elicitation/request", {"server": "wenext"})
    assert accept is True and server == "wenext"
    assert "zakresie" in why

    accept, server, why = client._decide("mcpServer/elicitation/request", {"server": "obcy"})
    assert accept is False and server == "obcy"
    assert "nikt w tej sesji nie decydował" in why

    accept, server, why = client._decide("mcpServer/elicitation/request", {})
    assert accept is False
    assert "nie mówi, od którego serwera" in why


#: Domyślny budżet czasu tury z `sessions/handlers.py::DEFAULT_BUDGET` (max_minutes). Wpisany
#: tutaj liczbą, a nie zaimportowany, bo `control_plane` nie jest widoczne z tego pakietu; zmiana
#: tamtej wartości ma ten test ZŁAMAĆ, i to jest jego zadanie.
DEFAULT_TURN_BUDGET_S = 20 * 60


def test_silence_is_caught_earlier_than_the_budget() -> None:
    """DWA ZEGARY I WŁAŚCIWA KOLEJNOŚĆ. Budżet ogranicza turę, która PRACUJE; cisza ogranicza
    turę, która przestała cokolwiek mówić. Gdyby cisza była dłuższa od budżetu, nigdy by się nie
    odezwała, a awaria z 19.09 skończyłaby się tak samo: „tura przekroczyła budżet" zamiast
    „app-server nie przysłał ani jednego zdarzenia", czyli zdaniem, które nie wskazuje przyczyny.
    """
    assert SILENCE_TIMEOUT_S < DEFAULT_TURN_BUDGET_S, (
        f"cisza {SILENCE_TIMEOUT_S} s przy budżecie {DEFAULT_TURN_BUDGET_S} s nigdy nie zadziała"
    )
    # I nie tak krótka, żeby zabijała myślący model albo odczyt wątku. Zdarzenia w prawdziwej
    # turze idą co kilka sekund; minuta byłaby progiem, który łapie pracę zamiast awarii.
    assert SILENCE_TIMEOUT_S >= 120, "próg krótszy niż dwie minuty zabija powolne, ale żywe tury"


# ---------------------------------------------------------------------------
# Nazwy pól na wire (usterka 1 z audytu)
# ---------------------------------------------------------------------------


def test_snake_case_is_mapped_to_the_contracts_spelling() -> None:
    """AWARIA, KTÓREJ NIKT BY NIE ZAUWAŻYŁ. App-server PRZYJMUJE nieznane pola i je IGNORUJE:
    audyt wysłał `base_instructions` z liczbą 123456789 i nie dostał błędu, a przy
    `baseInstructions` walidacja zadziałała. Nasz klient wysyłał snake_case, więc pierwsza
    prawdziwa sesja biegła BEZ instrukcji bazowych, a serwer nie protestował."""
    from codex_runtime.session_client import THREAD_START_FIELDS, to_wire

    wire = to_wire(
        {"base_instructions": "treść", "approval_policy": "never"},
        THREAD_START_FIELDS,
        "thread/start",
    )
    assert wire == {"baseInstructions": "treść", "approvalPolicy": "never"}


def test_a_field_outside_the_contract_is_refused_not_dropped() -> None:
    """Ciche pominięcie byłoby dokładnie tym samym błędem, tylko po naszej stronie."""
    from codex_runtime.protocol import ProtocolError
    from codex_runtime.session_client import THREAD_START_FIELDS, to_wire

    with pytest.raises(ProtocolError) as caught:
        to_wire({"baseInstructions": "x", "wymyslone": 1}, THREAD_START_FIELDS, "thread/start")
    assert "wymyslone" in str(caught.value)
    assert "IGNORUJE" in str(caught.value)


def test_a_name_already_in_the_contracts_spelling_passes_through() -> None:
    from codex_runtime.session_client import TURN_START_FIELDS, to_wire

    wire = to_wire({"threadId": "th_1", "input": []}, TURN_START_FIELDS, "turn/start")
    assert wire == {"threadId": "th_1", "input": []}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("THREAD_START_FIELDS", "baseInstructions"),
        ("THREAD_RESUME_FIELDS", "baseInstructions"),
        ("TURN_START_FIELDS", "input"),
    ],
)
def test_the_pinned_field_sets_match_the_schema_copies(fields, expected) -> None:
    """Zbiory są przepisane ze schematów binarki 0.147.0; ten test pilnuje, żeby ktoś, kto je
    zawęzi w pośpiechu, wywalił pole, bez którego metoda nie ma sensu."""
    import codex_runtime.session_client as client

    assert expected in getattr(client, fields)
