"""Ustawienie hasła użytkownika z wiersza poleceń (P7, awaryjnie).

    WENEXT_NEW_PASSWORD='...' make user-password USER_EMAIL=ktos@firma.test
    WENEXT_NEW_PASSWORD='...' make user-password USER_ORG=wenext USER_SET_EMAIL=ktos@firma.test

PO CO TO ISTNIEJE. Człowiek, który zapomniał hasła do WŁASNEGO wdrożenia, nie ma dziś żadnej
drogi z powrotem: nie ma wysyłki poczty, nie ma pytania pomocniczego, a konto przeniesione ze
starego operatora nie ma nawet adresu, na który cokolwiek mogłoby przyjść. Bez tego narzędzia
jedyną drogą jest ręczny UPDATE w bazie, czyli dokładnie to, czego to repozytorium zabrania.

HASŁO WYŁĄCZNIE ZE ZMIENNEJ, nigdy z argumentu. Argument widać w `ps` i zostaje w historii
powłoki; ta sama reguła rządzi `make bootstrap-operator` i jest tu powtórzona, bo pokusa
dopisania `--password` jest oczywista.

DWA DOPASOWANIA TO ODMOWA, nie „weź pierwsze". Skrypt, który przy dwóch kontach wybiera jedno,
zmienia hasło komuś, kto o tym nie wie, i nikt tego nie zauważy do czasu, aż tamten spróbuje
się zalogować.

CO ZOSTAJE W DZIENNIKU: `USER_PASSWORD_RESET` z identyfikatorem użytkownika i niczym więcej.
Ani nowego hasła, ani starego skrótu, ani adresu: dziennik jest append-only i pola, którego
nikt nie przejrzał, nie da się z niego usunąć.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

import asyncpg
from control_plane.identity.store import IdentityRefused, hash_password, normalised_email

#: Nowe hasło. Jedyna droga: zmienna środowiskowa.
PASSWORD_VARIABLE = "WENEXT_NEW_PASSWORD"
DSN_VARIABLE = "DATABASE_URL"

#: Najkrótsze hasło, jakie to narzędzie zapisze. Ta sama liczba, co przy rejestracji z P7:
#: awaryjne wejście nie jest powodem, żeby wpuścić słabsze hasło niż zwykłe drzwi.
MIN_PASSWORD_LENGTH = 12


class ResetRefused(Exception):
    """Odmowa ze zdaniem dla człowieka (D-010). Nigdy nie kończy się cichym zapisem."""


async def find_user(
    connection: asyncpg.Connection, *, email: str | None, organization: str | None
) -> UUID:
    """Jeden użytkownik albo odmowa. Nigdy „pierwszy z brzegu"."""
    if email:
        try:
            address = normalised_email(email)
        except IdentityRefused as exc:
            raise ResetRefused(f"{email!r} nie jest adresem: {exc}") from exc
        rows = await connection.fetch(
            "SELECT id FROM users WHERE email = $1 AND disabled_at IS NULL", address
        )
        if not rows:
            raise ResetRefused(f"nie ma czynnego konta z adresem {address}")
        if len(rows) > 1:  # pragma: no cover - broni indeks unikalny, ale nie zgadujemy
            raise ResetRefused(f"adres {address} pasuje do {len(rows)} kont; nie zgaduję")
        return rows[0]["id"]

    if not organization:
        raise ResetRefused("podaj USER_EMAIL albo USER_ORG; bez tego nie wiem, komu zmieniać hasło")

    # Konto przeniesione ze starego operatora nie ma adresu (0018), więc szuka się go po
    # organizacji. Szukamy TYLKO wśród kont bez adresu: konto z adresem ma własną drogę wyżej,
    # a wybieranie po organizacji spośród wielu ludzi byłoby losowaniem.
    rows = await connection.fetch(
        """
        SELECT u.id
          FROM users u
          JOIN memberships m ON m.user_id = u.id AND m.revoked_at IS NULL
          JOIN organizations o ON o.id = m.organization_id
         WHERE o.name = $1 AND u.email IS NULL AND u.disabled_at IS NULL
        """,
        organization,
    )
    if not rows:
        raise ResetRefused(
            f"organizacja {organization!r} nie ma czynnego konta bez adresu. Jeśli konto ma już "
            "adres, podaj USER_EMAIL"
        )
    if len(rows) > 1:
        raise ResetRefused(
            f"organizacja {organization!r} ma {len(rows)} konta bez adresu; podaj USER_EMAIL, "
            "bo nie zgaduję, któremu zmienić hasło"
        )
    return rows[0]["id"]


async def set_password(
    connection: asyncpg.Connection,
    *,
    email: str | None,
    organization: str | None,
    password: str,
    set_email: str | None = None,
) -> UUID:
    """Nowe hasło jednego użytkownika, z wpisem w dzienniku. Zwraca jego identyfikator."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ResetRefused(
            f"hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków; ustaw {PASSWORD_VARIABLE}"
        )
    user_id = await find_user(connection, email=email, organization=organization)
    address = normalised_email(set_email) if set_email else None

    from control_plane.audit import AuditWriter

    async with connection.transaction():
        organization_id = await connection.fetchval(
            "SELECT organization_id FROM memberships WHERE user_id = $1 AND revoked_at IS NULL "
            "ORDER BY created_at LIMIT 1",
            user_id,
        )
        if organization_id is None:
            raise ResetRefused(
                "to konto nie należy do żadnej czynnej organizacji; hasło nic by nie otworzyło"
            )
        await connection.execute(
            "UPDATE users SET credential_hash = $2 WHERE id = $1", user_id, hash_password(password)
        )
        if address is not None:
            taken = await connection.fetchval(
                "SELECT 1 FROM users WHERE email = $1 AND id <> $2", address, user_id
            )
            if taken:
                raise ResetRefused(f"adres {address} należy już do innego konta")
            await connection.execute("UPDATE users SET email = $2 WHERE id = $1", user_id, address)
        await AuditWriter(connection).record(
            kind="USER_PASSWORD_RESET",
            organization_id=organization_id,
            workspace_id=None,
            subject_ref=f"user:{user_id}",
            actor_ref="cli:user-password",
            payload={"user_id": str(user_id)},
        )
    return user_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_plane.identity.password_cli",
        description=(
            "Ustawia hasło jednego użytkownika. Hasło wyłącznie ze zmiennej "
            f"{PASSWORD_VARIABLE}; argument byłby widoczny w `ps` i w historii powłoki."
        ),
    )
    parser.add_argument("--email", default=os.environ.get("USER_EMAIL") or None)
    parser.add_argument("--organization", default=os.environ.get("USER_ORG") or None)
    parser.add_argument(
        "--set-email",
        default=os.environ.get("USER_SET_EMAIL") or None,
        help="ustaw adres kontu przeniesionemu z operatora (opcjonalne)",
    )
    parser.add_argument("--dsn", default=None)
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    dsn = arguments.dsn or os.environ.get(DSN_VARIABLE)
    if not dsn:
        print(f"brak {DSN_VARIABLE} — nie wiem, do której bazy się połączyć", file=sys.stderr)
        return 2
    password = os.environ.get(PASSWORD_VARIABLE, "")
    connection = await asyncpg.connect(dsn)
    try:
        user_id = await set_password(
            connection,
            email=arguments.email,
            organization=arguments.organization,
            password=password,
            set_email=arguments.set_email,
        )
    except (ResetRefused, IdentityRefused) as refused:
        print(f"odmowa: {refused}", file=sys.stderr)
        return 3
    finally:
        await connection.close()
    print(f"hasło ustawione dla użytkownika {user_id}. Wpis w dzienniku: USER_PASSWORD_RESET.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
