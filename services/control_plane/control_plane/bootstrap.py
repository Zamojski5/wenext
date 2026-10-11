"""Provision the single tenant of an installation: one organization, one workspace, one operator.

The first thing a fresh database needs and, until 2026-09-03, the one thing no script did:
`make up` left the schema empty and `make migrate` left it without an operator, so nobody could
log in to the dashboard without hand-written SQL. On a VPS running Outcome A (`docs/OUTCOMES.md`
§8) that gap is the difference between a runbook and a puzzle.

What this module refuses, and why each refusal is a refusal rather than a default:

* **Two organizations, or two workspaces, or two enabled operators.** E1 is one of each
  (D-016), `control_plane.runtime._sole_organization` and `ObservationStore.sole_workspace`
  both stop on "several", and the partial unique index `operators_one_enabled_per_organization`
  (0002) does the same for operators. Provisioning a second of anything here would create the
  state everything else treats as an error.
* **A password on the command line.** It lands in shell history and in `ps`. The password comes
  from `WENEXT_OPERATOR_PASSWORD` only, and this module never prints it.
* **An empty password.** `auth.hash_credential` refuses it too; the check here is so the message
  names the variable.

Rotation (`--rotate-password`) disables the enabled operator and inserts a new one — never an
UPDATE of the hash in place, because `approval_decisions` will one day point at the operator who
signed something, and that row has to stay who it was.

Usage inside the api container (the only place with `DATABASE_URL`):

    docker compose exec -T -e WENEXT_OPERATOR_PASSWORD api \\
        python -m control_plane.bootstrap --organization wenext --workspace anna

Operator-facing output is Polish (D-010).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from control_plane.auth import DSN_VARIABLE, create_operator

PASSWORD_VARIABLE = "WENEXT_OPERATOR_PASSWORD"


class BootstrapRefused(Exception):
    """The database is not in a state this module is willing to add to."""


@dataclass(frozen=True)
class BootstrapResult:
    organization_id: UUID
    workspace_id: UUID
    operator_id: UUID
    organization_created: bool
    workspace_created: bool
    operator_created: bool
    operator_rotated: bool

    def describe(self) -> str:
        def mark(created: bool) -> str:
            return "utworzono" if created else "istniało"

        lines = [
            f"organizacja  {self.organization_id}  ({mark(self.organization_created)})",
            f"workspace    {self.workspace_id}  ({mark(self.workspace_created)})",
            f"operator     {self.operator_id}  ("
            + ("zrotowano hasło" if self.operator_rotated else mark(self.operator_created))
            + ")",
        ]
        return "\n".join(lines)


async def discover_organization(
    connection: asyncpg.Connection, *, organization_name: str
) -> tuple[UUID, bool]:
    """The single organization: created when there is none, reused when there is one, refused
    when there are several. Returns `(id, created)`."""
    organizations = await connection.fetch("SELECT id FROM organizations ORDER BY created_at, id")
    if len(organizations) > 1:
        raise BootstrapRefused(
            f"w bazie są {len(organizations)} organizacje — E1 zakłada jedną (D-016); "
            "ten skrypt nie zgaduje, do której dołożyć operatora"
        )
    if organizations:
        return organizations[0]["id"], False
    organization_id = await connection.fetchval(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id", organization_name
    )
    return organization_id, True


async def provision(
    connection: asyncpg.Connection,
    *,
    organization_id: UUID,
    organization_created: bool,
    workspace_name: str,
    password: str,
    rotate_password: bool = False,
) -> BootstrapResult:
    """Workspace and operator for a KNOWN organization. Separate from discovery so it can be
    tested on a database other suites have already filled with organizations."""
    if not password.strip():
        raise BootstrapRefused(
            f"puste hasło — ustaw {PASSWORD_VARIABLE} w środowisku procesu (nie w argumentach)"
        )
    if not workspace_name.strip():
        raise BootstrapRefused("nazwa workspace'u nie może być pusta")

    workspaces = await connection.fetch(
        "SELECT id FROM workspaces WHERE organization_id = $1 ORDER BY created_at, id",
        organization_id,
    )
    if len(workspaces) > 1:
        raise BootstrapRefused(
            f"organizacja ma {len(workspaces)} workspace'y — E1 zakłada jeden; "
            "obserwacja i osądy wymagają dokładnie jednego"
        )
    workspace_created = False
    if workspaces:
        workspace_id = workspaces[0]["id"]
    else:
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            organization_id,
            workspace_name,
        )
        workspace_created = True

    enabled = await connection.fetch(
        "SELECT id FROM operators WHERE organization_id = $1 AND disabled_at IS NULL",
        organization_id,
    )
    if enabled and not rotate_password:
        raise BootstrapRefused(
            f"organizacja ma już aktywnego operatora {enabled[0]['id']}. Zmiana hasła to "
            "`--rotate-password`: stary wiersz zostaje wyłączony, nie nadpisany"
        )
    operator_rotated = False
    if enabled:
        await connection.execute(
            "UPDATE operators SET disabled_at = now() "
            "WHERE organization_id = $1 AND disabled_at IS NULL",
            organization_id,
        )
        operator_rotated = True
    operator_id = await create_operator(
        connection, organization_id=organization_id, password=password
    )
    return BootstrapResult(
        organization_id=organization_id,
        workspace_id=workspace_id,
        operator_id=operator_id,
        organization_created=organization_created,
        workspace_created=workspace_created,
        operator_created=not operator_rotated,
        operator_rotated=operator_rotated,
    )


async def bootstrap(
    connection: asyncpg.Connection,
    *,
    organization_name: str,
    workspace_name: str,
    password: str,
    rotate_password: bool = False,
) -> BootstrapResult:
    """Create what is missing, reuse what exists, refuse what is ambiguous. One transaction."""
    if not organization_name.strip():
        raise BootstrapRefused("nazwa organizacji nie może być pusta")
    async with connection.transaction():
        organization_id, created = await discover_organization(
            connection, organization_name=organization_name
        )
        return await provision(
            connection,
            organization_id=organization_id,
            organization_created=created,
            workspace_name=workspace_name,
            password=password,
            rotate_password=rotate_password,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_plane.bootstrap",
        description=(
            "Zakłada jedyną organizację, jedyny workspace i jedynego operatora. "
            f"Hasło wyłącznie ze zmiennej {PASSWORD_VARIABLE}."
        ),
    )
    parser.add_argument(
        "--organization", default="wenext", help="nazwa organizacji (przy tworzeniu)"
    )
    parser.add_argument("--workspace", default="default", help="nazwa workspace'u (przy tworzeniu)")
    parser.add_argument(
        "--rotate-password",
        action="store_true",
        help="wyłącz aktywnego operatora i załóż nowego z hasłem ze środowiska",
    )
    parser.add_argument("--dsn", default=None, help=f"DSN; domyślnie {DSN_VARIABLE} ze środowiska")
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    dsn = arguments.dsn or os.environ.get(DSN_VARIABLE)
    if not dsn:
        print(f"brak {DSN_VARIABLE} — nie wiem, do której bazy się połączyć", file=sys.stderr)
        return 2
    password = os.environ.get(PASSWORD_VARIABLE, "")
    connection = await asyncpg.connect(dsn)
    try:
        result = await bootstrap(
            connection,
            organization_name=arguments.organization,
            workspace_name=arguments.workspace,
            password=password,
            rotate_password=arguments.rotate_password,
        )
    except BootstrapRefused as refused:
        print(f"odmowa: {refused}", file=sys.stderr)
        return 3
    finally:
        await connection.close()
    print(result.describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
