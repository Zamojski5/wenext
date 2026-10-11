"""Stan konta platformy z RZECZYWISTOŚCI, a nie tylko z wiersza w bazie (D-046).

PROBLEM, KTÓRY TO ZAMYKA, zmierzony na live 20.09: kapsuła jest zalogowana i czyta Reddita, a
`social_accounts.state` konta `Demo_Maintainer` stoi na `PROVISIONED`, bo NIKT W TYM KODZIE
nigdy tej kolumny nie aktualizował. Karta konta w rozmowie czyta wiersz, więc pokazywała
„Brakuje połączenia” obok działającego konta. Dwa stany świata, jeden z nich nieprawdziwy.

CO TU JEST, A CZEGO NIE MA:

  * jest JEDNA droga zapisu, `note_capsule_login`, i wołają ją oba miejsca, które i tak pytają
    kapsułę o sesję: potwierdzenie sesji kapsuły oraz narzędzie `accounts.status`. Druga droga
    znaczyłaby dwie definicje tego, co to jest „konto gotowe”;
  * nie ma tu odczytu kapsuły. Ta funkcja dostaje GOTOWY odczyt (`SessionReading`), bo pytanie
    platformy kosztuje żądanie, a decyzję o tym, kiedy je zadać, podejmuje wołający;
  * nie ma zgadywania. Odczyt bez nazwy konta albo z nazwą, która nie pasuje do żadnego konta
    tej organizacji, nie zmienia niczego i mówi o tym wprost zwracanym powodem.

MASZYNA STANÓW OBOWIĄZUJE. `PROVISIONED → VERIFYING → READY` to jedyna droga do gotowości
(L2585-2587), więc funkcja robi oba kroki w jednej transakcji zamiast skakać na skróty.
`REAUTH_REQUIRED` i `QUARANTINED` nie mają wyjścia w tej maszynie i ta funkcja ich nie rusza:
konto odstawione decyzją człowieka nie wraca do pracy dlatego, że przeglądarka jest zalogowana.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from control_plane.audit import AuditWriter

#: Stany, z których odczyt kapsuły wolno przekuć w gotowość.
_CAN_BECOME_READY = ("PROVISIONED", "VERIFYING")


@dataclass(frozen=True)
class AccountNoted:
    """Co zrobił odczyt kapsuły ze stanem konta."""

    account_id: UUID | None
    login: str | None
    changed: bool
    #: Zdanie dla człowieka, gdy nic się nie zmieniło. `None`, gdy zmiana zaszła.
    reason: str | None = None


async def note_capsule_login(
    pool: Any,
    *,
    organization_id: UUID,
    platform: str,
    logged_in: bool | None,
    username: str | None,
    actor_ref: str,
) -> AccountNoted:
    """Zapisz, że kapsuła jest zalogowana na TYM koncie. Idempotentne i wąskie.

    Zwraca, czy coś się zmieniło, i przy braku zmiany podaje powód, bo „nic nie zrobiłem” bez
    powodu jest nieodróżnialne od „nie zadziałało”.
    """
    if logged_in is not True:
        return AccountNoted(None, None, False, "kapsuła nie zgłasza zalogowanej sesji")
    login = (username or "").strip()
    if not login:
        return AccountNoted(None, None, False, "kapsuła nie podała nazwy zalogowanego konta")

    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, external_account_id, state FROM social_accounts "
            "WHERE organization_id = $1 AND platform = $2 AND lower(external_account_id) = $3",
            organization_id,
            platform,
            login.lower(),
        )
        if row is None:
            return AccountNoted(
                None,
                login,
                False,
                f"kapsuła jest zalogowana jako {login}, ale to konto nie jest zarejestrowane "
                "w tej organizacji",
            )
        state = str(row["state"])
        if state == "READY":
            return AccountNoted(row["id"], str(row["external_account_id"]), False, None)
        if state not in _CAN_BECOME_READY:
            return AccountNoted(
                row["id"],
                str(row["external_account_id"]),
                False,
                f"konto jest w stanie {state}; to jest decyzja człowieka, a nie skutek logowania "
                "w przeglądarce",
            )
        # Oba kroki maszyny, w jednej transakcji: konto zostało zweryfikowane odczytem
        # `/api/me.json`, więc przechodzi przez VERIFYING, a nie obok niego. Warunek na stanie
        # źródłowym jest po to, żeby dwie równoczesne próby dały jedną zmianę, a nie dwie.
        moved = await conn.execute(
            "UPDATE social_accounts SET state = 'VERIFYING' WHERE id = $1 AND state = $2",
            row["id"],
            state,
        )
        if moved.split()[-1] != "1":
            # Ktoś inny przestawił konto między odczytem a zapisem. Nie dopisuję audytu o zmianie,
            # której nie zrobiłem: „zmieniłem” bez zmiany jest gorsze niż „nic nie zrobiłem”.
            return AccountNoted(
                row["id"],
                str(row["external_account_id"]),
                False,
                "stan konta zmienił się w trakcie sprawdzania; spróbuj jeszcze raz",
            )
        await conn.execute(
            "UPDATE social_accounts SET state = 'READY' WHERE id = $1 AND state = 'VERIFYING'",
            row["id"],
        )
        await AuditWriter(conn).record(
            kind="SOCIAL_ACCOUNT_VERIFIED",
            organization_id=organization_id,
            subject_ref=f"social_account:{row['id']}",
            actor_ref=actor_ref,
            payload={"from_state": state, "to_state": "READY"},
        )
    return AccountNoted(row["id"], str(row["external_account_id"]), True, None)
