"""Karta SILNIK: czy ta rozmowa ma na czym ruszyć i co z tym zrobić (E1, audyt UX 20.09).

DLACZEGO OSOBNY MODUŁ, A NIE DWIE LINIE W HANDLERZE. Tę samą kartę buduje tura (gdy odkrywa, że
nie ma połączenia) i projekcja (gdy odświeża stan przy każdym odczycie ekranu). Gdyby każda
robiła to po swojemu, karta w rozmowie i karta na ekranie mówiłyby w pewnym momencie co innego o
tym samym połączeniu, a różnicy nie zobaczyłby żaden test jednej ze stron.

GRANICA ORGANIZACJI ZOSTAJE. Zapytanie pyta wyłącznie o połączenia TEJ organizacji, a karta nie
niesie ani katalogu, ani pliku poświadczeń: człowiek dostaje adres logowania i kod, czyli
dokładnie to, co i tak zobaczy na ekranie dostawcy. Audyt mówi wprost, że rozjazdu organizacji
nie wolno rozwiązywać udostępnianiem cudzych poświadczeń.

CO KARTA MÓWI, GDY NIE MA CZEGO POWIEDZIEĆ: `brak_polaczenia` z akcją „polacz". To jest stan
pierwszego wejścia do produktu i najczęstszy stan tej karty, więc ma być zwykły, a nie awaryjny.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from control_plane.tools.cards import (
    ENGINE_CONNECTED,
    ENGINE_CONNECTING,
    ENGINE_DISCONNECTED,
    STATE_ERROR,
    action,
    engine_card,
)

#: Przekład stanu wiersza połączenia (0014) na stan karty. `PENDING_LOGIN` to `lacze`, bo z
#: punktu widzenia człowieka trwa wtedy jedna czynność: przepisanie kodu.
STATE_OF_STATUS = {
    "CONNECTED": ENGINE_CONNECTED,
    "PENDING_LOGIN": ENGINE_CONNECTING,
    "ERROR": STATE_ERROR,
    "DISCONNECTED": ENGINE_DISCONNECTED,
}

CONNECTIONS_PATH = "/api/codex-connections"

#: Zdanie dla CZŁOWIEKA, gdy logowanie nie wyszło. Zamknięta lista, bo `last_error` w wierszu
#: połączenia jest zapisem dla tego, kto to wdrażał, i na żywym zrzucie z 20.09 wyglądał tak:
#: „nie udało się uruchomić app-servera Codeksa na /private/var/folders/g8/.../tmp.FpRzO0QqEM/...
#: [Errno 2] No such file or directory: '/nonexistent/wenext-brak-codexa'". Warunek odbioru E1
#: mówi wprost, że na karcie nie ma wiedzy operatora wdrożenia, a ścieżka w katalogu tymczasowym
#: hosta jest dokładnie tym: klientowi nie mówi nic, a wygląda jak awaria, za którą to on
#: odpowiada. Pełny tekst zostaje w `codex_connections.last_error` i tam go szuka ten, kto
#: debuguje.
ENGINE_FAILED = "nie udało się połączyć z Codeksem"
_REASONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("no such file", "errno 2", "app-server", "nie udało się uruchomić"),
        "nie udało się uruchomić Codexa z tego połączenia",
    ),
    (
        ("timeout", "timed out", "za długo", "upłynął"),
        "logowanie trwało za długo i zostało przerwane",
    ),
    (
        ("401", "unauthorized", "forbidden", "odmów", "odmow"),
        "Codex odmówił logowania z tego konta",
    ),
    (
        ("network", "connection refused", "sieć", "siec"),
        "nie udało się dobić do Codexa z tego serwera",
    ),
)


def human_reason(raw: str | None) -> str:
    """Techniczny zapis błędu jako jedno zdanie dla człowieka.

    Dopasowanie po fragmentach, a nie po kodach, bo `last_error` powstaje w kilku miejscach i
    nie ma jednego kształtu. Czego nie rozpoznamy, dostaje zdanie ogólne: lepiej powiedzieć
    „nie udało się", niż pokazać ścieżkę z katalogu tymczasowego hosta.
    """
    text = (raw or "").strip().lower()
    if not text:
        return "logowanie nie powiodło się"
    for markers, sentence in _REASONS:
        if any(marker in text for marker in markers):
            return sentence
    return ENGINE_FAILED


async def card_for_organization(
    pool: Any, *, organization_id: UUID, workspace_id: UUID | None = None
) -> tuple[dict[str, Any], str]:
    """Karta SILNIK i jej stan. Jedno zapytanie, żadnych wyjątków dla wołającego.

    Wybór połączenia, gdy jest ich kilka: najpierw gotowe, potem logujące się, potem błędne.
    Pytanie karty brzmi „czy praca ma na czym ruszyć", więc odpowiada to połączenie, które jest
    najbliżej działania.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, label, status, login_ticket, last_error
              FROM codex_connections
             WHERE organization_id = $1 AND disconnected_at IS NULL
               AND ($2::uuid IS NULL OR workspace_id IS NULL OR workspace_id = $2)
             ORDER BY created_at
            """,
            organization_id,
            workspace_id,
        )

    ranked = sorted(rows, key=lambda row: _rank(str(row["status"])))
    if not ranked:
        return _nothing_connected(), ENGINE_DISCONNECTED

    row = ranked[0]
    state = STATE_OF_STATUS.get(str(row["status"]), ENGINE_DISCONNECTED)
    ticket = _ticket(row["login_ticket"])
    connection = str(row["id"])
    if state == ENGINE_CONNECTED:
        return (
            engine_card(
                state=state,
                connection=connection,
                label=str(row["label"]),
                actions=[],
            ),
            state,
        )
    if state == ENGINE_CONNECTING:
        return (
            engine_card(
                state=state,
                connection=connection,
                label=str(row["label"]),
                login_url=ticket.get("verification_url"),
                code=ticket.get("user_code"),
                # ANULOWANIE JEST ODŁĄCZENIEM TEGO POŁĄCZENIA, nie porzuceniem rozmowy: cel
                # zostaje, karta wraca do `brak_polaczenia`, a człowiek może zacząć logowanie od
                # nowa tym samym przyciskiem.
                actions=[action("anuluj", "DELETE", f"{CONNECTIONS_PATH}/{connection}")],
            ),
            state,
        )
    if state == STATE_ERROR:
        return (
            engine_card(
                state=state,
                connection=connection,
                label=str(row["label"]),
                reason=human_reason(row["last_error"]),
                actions=[action("polacz", "POST", f"{CONNECTIONS_PATH}/{connection}/login")],
            ),
            state,
        )
    return _nothing_connected(), ENGINE_DISCONNECTED


def _nothing_connected() -> dict[str, Any]:
    return engine_card(
        state=ENGINE_DISCONNECTED,
        actions=[
            action(
                "polacz",
                "POST",
                CONNECTIONS_PATH,
                # Etykieta jest wymagana przez trasę, a człowiek na tym etapie nie ma jeszcze
                # pojęcia, ile połączeń będzie miał. Nazwa domyślna jest do zmiany na ekranie
                # połączeń; wymuszanie jej przed pierwszym zalogowaniem byłoby pytaniem o coś,
                # czego użytkownik jeszcze nie wie.
                {"label": "Moje konto Codex"},
            )
        ],
    )


def _rank(status: str) -> int:
    order = {"CONNECTED": 0, "PENDING_LOGIN": 1, "ERROR": 2, "DISCONNECTED": 3}
    return order.get(status, 4)


def _ticket(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}
