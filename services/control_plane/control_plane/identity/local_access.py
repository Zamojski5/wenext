"""Tryb lokalny: wejście bez hasła na maszynie, na której produkt i tak stoi.

PO CO. Założyciel pracuje z produktem na własnym laptopie i logowanie przeszkadza mu w walidacji:
hasło do WŁASNEGO wdrożenia, wpisywane po każdym restarcie, nie chroni przed nikim, kto już siedzi
przy tej maszynie. Ten moduł pozwala je pominąć i robi to w jeden sposób, który da się sprawdzić
testem, zamiast pięciu, które da się obejść.

TRZY WARUNKI NARAZ, KAŻDY Z INNEGO POWODU. Brak któregokolwiek znaczy odmowę:

  1. **przełącznik nazywa właściciela.** `WENEXT_LOCAL_AUTOLOGIN` trzyma adres e-mail albo nazwę
     organizacji. Pusty (i tak jest domyślnie) wyłącza wszystko. Wywołujący NIE podaje, kim chce
     być: użytkownika wybiera serwer po tej zmiennej, więc otwarta trasa nie jest wyborem konta;
  2. **wdrożenie nie ma drzwi z internetu.** `WENEXT_PUBLIC_DOMAIN` puste i `WENEXT_EDGE_BIND`
     pusty albo na pętli zwrotnej. To są dokładnie te dwa pola, które ustawia nakładka
     produkcyjna (`compose.prod.yml`), więc serwer z domeną odmawia nawet przy włączonym
     przełączniku;
  3. **rozmowa toczy się wewnątrz maszyny.** Adres, na którym słucha API, i adres dzwoniącego
     muszą być pętlą zwrotną albo siecią prywatną. Publiczny adres po którejkolwiek stronie to
     odmowa.

CZEGO TE WARUNKI NIE OBIECUJĄ, powiedziane wprost, bo obietnica na wyrost jest tu gorsza niż
brak funkcji: przy włączonym przełączniku KAŻDY, kto dosięgnie portu dashboardu na tej maszynie
(drugie konto w systemie, otwarty tunel ssh), wchodzi bez hasła. To jest cena trybu lokalnego
i dlatego jest domyślnie wyłączony, widoczny paskiem na ekranie i zapisywany w dzienniku przy
każdej otwartej sesji.

DLACZEGO NIE „BIND MUSI BYĆ 127.0.0.1” DOSŁOWNIE. W tym wdrożeniu usługi chodzą w kontenerach
i wewnątrz nich słuchają na 0.0.0.0; z hosta dashboard jest opublikowany na 127.0.0.1, a API
nie jest opublikowane wcale. Dosłowny warunek na adres bindu byłby więc zawsze fałszywy i
funkcja nie działałaby nigdy, a warunek przepisany na „adres kontenera" nie mówiłby nic
o dostępności z zewnątrz. Stąd para: adresy muszą być prywatne ORAZ wdrożenie nie może mieć
skonfigurowanych drzwi z internetu. Pierwszy warunek wyklucza publiczny socket, drugi serwer.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from uuid import UUID

import asyncpg

LOCAL_AUTOLOGIN_VARIABLE = "WENEXT_LOCAL_AUTOLOGIN"
PUBLIC_DOMAIN_VARIABLE = "WENEXT_PUBLIC_DOMAIN"
EDGE_BIND_VARIABLE = "WENEXT_EDGE_BIND"


class LocalAccessRefused(Exception):
    """Odmowa z powodem do pokazania człowiekowi i do wpisania w log."""


def who_is_armed(env: Mapping[str, str] | None = None) -> str:
    """Adres albo nazwa organizacji właściciela, albo pusty łańcuch, gdy tryb jest wyłączony."""
    source = env if env is not None else os.environ
    return (source.get(LOCAL_AUTOLOGIN_VARIABLE) or "").strip()


def _is_local_address(value: str | None) -> bool:
    """Pętla zwrotna albo sieć prywatna. Nazwa hosta, której nie umiemy odczytać, to NIE."""
    if not value:
        return False
    text = value.strip().strip("[]")
    if text in {"localhost", "0.0.0.0", "::"}:
        # 0.0.0.0 i :: to „słucham na wszystkim". To nie jest adres, z którego ktoś dzwoni,
        # i nie jest dowodem lokalności; `localhost` jest.
        return text == "localhost"
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def deployment_is_local(
    *, server_host: str | None, client_host: str | None, env: Mapping[str, str] | None = None
) -> tuple[bool, str]:
    """Czy wolno tu otworzyć sesję bez hasła. Zwraca werdykt i zdanie z powodem.

    Funkcja CZYSTA: bierze adresy i środowisko, niczego nie czyta z globalnego stanu poza
    domyślnym `os.environ`. Dzięki temu odmowa przy adresie publicznym ma test jednostkowy,
    a nie tylko dobre intencje.
    """
    source = env if env is not None else os.environ
    if not who_is_armed(source):
        return False, f"{LOCAL_AUTOLOGIN_VARIABLE} nie jest ustawione; tryb lokalny jest wyłączony"
    if (source.get(PUBLIC_DOMAIN_VARIABLE) or "").strip():
        return False, (
            f"to wdrożenie ma domenę w {PUBLIC_DOMAIN_VARIABLE}, więc nie jest maszyną lokalną; "
            "tryb lokalny jest tu odmawiany niezależnie od przełącznika"
        )
    edge = (source.get(EDGE_BIND_VARIABLE) or "").strip()
    if edge and not _is_local_address(edge):
        return False, (
            f"{EDGE_BIND_VARIABLE}={edge} wystawia to wdrożenie poza pętlę zwrotną; "
            "tryb lokalny jest tu odmawiany"
        )
    if not _is_local_address(server_host):
        return False, f"API słucha na {server_host!r}, co nie jest adresem lokalnym"
    if not _is_local_address(client_host):
        return False, f"wywołanie przyszło z {client_host!r}, co nie jest adresem lokalnym"
    return True, "tryb lokalny: maszyna bez drzwi z internetu"


async def find_owner(connection: asyncpg.Connection, who: str) -> tuple[UUID, UUID]:
    """Użytkownik i jego organizacja, wskazani adresem albo nazwą organizacji.

    DWA DOPASOWANIA TO ODMOWA. Wybranie pierwszego z brzegu otwierałoby sesję komuś innemu, niż
    właściciel maszyny miał na myśli, i nikt by tego nie zauważył.
    """
    text = who.strip()
    if not text:
        raise LocalAccessRefused("nie wskazano właściciela")
    if "@" in text:
        rows = await connection.fetch(
            """
            SELECT u.id, m.organization_id
              FROM users u
              JOIN memberships m ON m.user_id = u.id AND m.revoked_at IS NULL
             WHERE u.email = $1 AND u.disabled_at IS NULL
             ORDER BY m.created_at
            """,
            text.lower(),
        )
    else:
        rows = await connection.fetch(
            """
            SELECT u.id, m.organization_id
              FROM users u
              JOIN memberships m ON m.user_id = u.id AND m.revoked_at IS NULL
              JOIN organizations o ON o.id = m.organization_id
             WHERE o.name = $1 AND u.disabled_at IS NULL
             ORDER BY u.created_at
            """,
            text,
        )
    if not rows:
        raise LocalAccessRefused(f"{text!r} nie wskazuje żadnego czynnego konta")
    people = {row["id"] for row in rows}
    if len(people) > 1:
        raise LocalAccessRefused(
            f"{text!r} pasuje do {len(people)} kont; wpisz adres e-mail właściciela, bo nie zgaduję"
        )
    return rows[0]["id"], rows[0]["organization_id"]
