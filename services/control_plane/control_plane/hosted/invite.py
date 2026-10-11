"""The administrator's command line for hosted Wenext: invite a person, list accesses, revoke one.

    docker compose exec -T mcp-edge python -m control_plane.hosted.invite create \\
        --email ktos@example.com --name "Nazwa produktu"
    docker compose exec -T mcp-edge python -m control_plane.hosted.invite list
    docker compose exec -T mcp-edge python -m control_plane.hosted.invite revoke --email ktos@...
    docker compose exec -T mcp-edge python -m control_plane.hosted.invite revoke --subject 1234567

THIS IS THE INTERNAL DOOR (administrator, testing, a hand-picked invitation). The product's front
door is the sign-in at the edge (`edge.py`): a person who signs in there gets the same project
created on their first request, with no token from us. Both doors end in the same rows, built by
`provision.add_workspace_and_project`.

`create` makes one organization with its owner (the identity store: the same rows the
self-service sign-up makes), then one workspace, one project, the placeholder accounts and the
person's token in a single transaction, stores ONLY the token's SHA-256 and prints the two commands
the person pastes into their agent. The token is printed once, here, and cannot be shown again:
lose it and the access is revoked and issued anew. `list` shows tokens and signed-in identities;
`revoke --id/--email` cuts a token, `revoke --subject` switches an identity off.

THE OWNER HAS NO USABLE PASSWORD. `users.credential_hash` must be an argon2id hash (a CHECK of
0002), so the owner is created with the hash of 32 random bytes that nobody ever saw. The person
reaches Wenext through their agent and the token, not through the dashboard; if they ever need
the dashboard, an administrator sets a password the normal way.

`--base-url` is where the person's agent will reach the endpoint (`http://127.0.0.1:8780` for a
local try-out); without it the address is `https://$WENEXT_PUBLIC_DOMAIN`, which is the ORIGIN
hostname (`origin.getwenext.com`), not the Worker's: the token door lives on the origin.

Operator-facing output is Polish (D-010). Exit codes: 0 done, 3 no database, 4 refused (a
sentence says why), 5 the configuration is wrong (no base address).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from control_plane.auth import DSN_VARIABLE
from control_plane.identity.store import MAX_NAME_LENGTH, IdentityRefused, IdentityStore

from .provision import ExternalIdentityStore, add_workspace_and_project
from .tokens import HostedTokenStore, TokenRefused

ENV_PUBLIC_DOMAIN = "WENEXT_PUBLIC_DOMAIN"

EXIT_NO_DATABASE = 3
EXIT_REFUSED = 4
EXIT_BAD_CONFIG = 5


@dataclass(frozen=True)
class Invitation:
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    project_name: str
    email: str
    token: str


def base_url(argument: str | None, env: dict[str, str] | None = None) -> str:
    """The origin the person's agent will use, without a trailing slash, or a refusal."""
    source = os.environ if env is None else env
    explicit = (argument or "").strip().rstrip("/")
    if explicit:
        if not explicit.startswith(("http://", "https://")):
            raise ValueError(f"--base-url musi zaczynać się od http:// albo https://: {explicit!r}")
        return explicit
    domain = (source.get(ENV_PUBLIC_DOMAIN) or "").strip().strip("/")
    if not domain:
        raise ValueError(
            f"Nie wiem, pod jakim adresem agent ma sięgać po Wenext. Ustaw {ENV_PUBLIC_DOMAIN} "
            "albo podaj --base-url."
        )
    return f"https://{domain}"


def install_commands(origin: str, token: str) -> tuple[str, str]:
    """The two one-line commands, in the order they are printed: Codex, then Claude Code."""
    url = f"{origin}/mcp/{token}"
    return (
        f"codex mcp add wenext --url {url}",
        f"claude mcp add --transport http wenext {url}",
    )


async def create_invitation(pool: asyncpg.Pool, *, email: str, name: str) -> Invitation:
    """Organization, owner, workspace, project, placeholder accounts, token. Raises on a refusal.

    The organization and its owner come from the identity store (the same rows the self-service
    sign-up makes). Everything after them, including the token, is ONE transaction: a failure
    leaves an organization with an owner and no project (retry after `revoke`), never a project
    whose token was lost halfway.
    """
    title = (name or "").strip()
    if not title:
        raise IdentityRefused("podaj nazwę produktu (--name)")
    if len(title) > MAX_NAME_LENGTH:
        raise IdentityRefused(f"nazwa jest dłuższa niż {MAX_NAME_LENGTH} znaków")
    identity = IdentityStore(pool)
    # An argon2id hash of a password nobody ever saw: the CHECK on `users.credential_hash` wants
    # the shape, the person never signs in with it.
    user, membership = await identity.register_organization(
        email=email,
        password=secrets.token_urlsafe(32),
        organization_name=title,
    )
    async with pool.acquire() as conn, conn.transaction():
        workspace_id, project_id = await add_workspace_and_project(
            conn, organization_id=membership.organization_id, name=title[:100]
        )
        token = await HostedTokenStore(pool).create(
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            label=user.email or email,
            conn=conn,
        )
    return Invitation(
        organization_id=membership.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        project_name=title[:100],
        email=user.email or email,
        token=token,
    )


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _fmt(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment is not None else "-"


async def _run(arguments: argparse.Namespace) -> int:
    dsn = (os.environ.get(DSN_VARIABLE) or "").strip()
    if not dsn:
        _say(f"Brak {DSN_VARIABLE}: to polecenie ma biec w kontenerze, gdzie baza jest znana.")
        return EXIT_NO_DATABASE
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        _say(f"Nie udało się połączyć z bazą: {type(exc).__name__}: {exc}")
        return EXIT_NO_DATABASE
    try:
        return await _dispatch(pool, arguments)
    finally:
        await pool.close()


async def _dispatch(pool: asyncpg.Pool, arguments: argparse.Namespace) -> int:
    tokens = HostedTokenStore(pool)
    if arguments.command == "create":
        try:
            origin = base_url(arguments.base_url)
        except ValueError as exc:
            # BEFORE anything is created: a token minted for an address nobody can print is a
            # token nobody can hand over.
            _say(str(exc))
            return EXIT_BAD_CONFIG
        try:
            invitation = await create_invitation(pool, email=arguments.email, name=arguments.name)
        except IdentityRefused as exc:
            _say(f"Odmowa: {exc}")
            _say("Ten adres e-mail ma już dostęp? Zobacz `list`, cofnij go przez `revoke`.")
            return EXIT_REFUSED
        codex, claude = install_commands(origin, invitation.token)
        print(f"Zaproszenie dla {invitation.email}")
        print(f"  organizacja  {invitation.organization_id}")
        print(f"  przestrzeń   {invitation.workspace_id}")
        print(f"  projekt      {invitation.project_id}  ({invitation.project_name})")
        print()
        print("W Codexie:")
        print(f"  {codex}")
        print("W Claude Code:")
        print(f"  {claude}")
        print()
        print(
            "Token widać tylko teraz: w bazie jest sam skrót. Zgubiony token = cofnięcie "
            "dostępu i nowe zaproszenie."
        )
        return 0

    if arguments.command == "list":
        rows = await tokens.list_tokens()
        identities = await ExternalIdentityStore(pool).list_identities()
        if not rows and not identities:
            print("Brak zaproszeń i zalogowanych osób.")
            return 0
        if identities:
            print("Zalogowani przez brzeg (provider, subject):")
            for one in identities:
                state = "wyłączony" if one.disabled else "żywy"
                who = one.email or one.display_name or "-"
                print(f"  {one.provider:<10}{one.subject:<24}{state:<11}{who:<40}{one.project_id}")
            print()
        if not rows:
            return 0
        print(f"{'id':<9}{'stan':<9}{'etykieta':<42}{'utworzono':<18}{'ostatnio':<18}projekt")
        for row in rows:
            state = "żywy" if row.live else "cofnięty"
            print(
                f"{row.short:<9}{state:<9}{row.label[:40]:<42}{_fmt(row.created_at):<18}"
                f"{_fmt(row.last_used_at):<18}{row.project_id}"
            )
        return 0

    if arguments.command == "revoke":
        try:
            if arguments.subject:
                count = await ExternalIdentityStore(pool).disable(
                    subject=arguments.subject, provider=arguments.provider
                )
                if not count:
                    raise TokenRefused(f"nie ma żywej tożsamości o podmiocie {arguments.subject!r}")
            elif arguments.id:
                count = await tokens.revoke_prefix(arguments.id)
            else:
                count = await tokens.revoke_label(arguments.email)
        except TokenRefused as exc:
            _say(f"Odmowa: {exc}")
            return EXIT_REFUSED
        print(f"Cofnięto dostępów: {count}")
        return 0

    raise SystemExit(f"nieznana podkomenda: {arguments.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_plane.hosted.invite",
        description="Zaproszenia do hostowanego Wenext: utwórz, wypisz, cofnij.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create", help="załóż organizację, przestrzeń i projekt, wydaj token i wypisz polecenia"
    )
    create.add_argument("--email", required=True, help="adres osoby, której wysyłamy zaproszenie")
    create.add_argument("--name", required=True, help="nazwa produktu osoby (nazwa projektu)")
    create.add_argument(
        "--base-url",
        default=None,
        help=f"adres, pod którym agent sięga po Wenext; domyślnie https://${ENV_PUBLIC_DOMAIN}",
    )
    commands.add_parser("list", help="wypisz wydane dostępy (bez tokenów, których nikt nie ma)")
    revoke = commands.add_parser("revoke", help="cofnij dostęp")
    which = revoke.add_mutually_exclusive_group(required=True)
    which.add_argument("--id", help="początek identyfikatora z kolumny `id` w `list` (8+ znaków)")
    which.add_argument("--email", help="cofnij wszystkie żywe dostępy z tą etykietą")
    which.add_argument(
        "--subject", help="wyłącz zalogowaną osobę po jej `subject` (kolumna z `list`)"
    )
    revoke.add_argument("--provider", default=None, help="z --subject: zawęź do jednego dostawcy")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return asyncio.run(_run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
