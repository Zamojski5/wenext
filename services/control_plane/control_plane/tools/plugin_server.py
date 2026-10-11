"""The entry point of the Wenext plugin for a person's OWN coding agent (Codex, Claude Code).

How it is run, and by whom:

    docker compose exec -T -e WENEXT_PROJECT -e WENEXT_HOST api \\
        python -m control_plane.tools.plugin_server serve

NOT by a person typing it: the plugin's launcher (`plugins/wenext/bin/wenext mcp`) `exec`s that
command, and the host agent talks MCP over its stdin and stdout. Everything that has to happen
before the first `initialize` happens HERE, inside the `api` container, in one process:

  1. find the organization, the workspace and the project (the sole organization, as
     `jobs/devcli.py` does, or the one named by `WENEXT_ORGANIZATION` on an installation that has
     several; the project from `WENEXT_PROJECT`, or the only non-archived one);
  2. find or create the project's plugin session (`SessionStore.open_host_session`);
  3. write the audit row `PLUGIN_SESSION_STARTED`;
  4. issue a capability that names the host, write it 0600, and point the environment at it;
  5. run `mcp_server.main()`, the same protocol loop Codex's own tool server runs;
  6. remove the file in a `finally`, as `sessions/handlers.py` does after a turn.

WHY NO HTTP STEP. The api port is not published, and every route needs an operator cookie that
the plugin does not have and must not get. Going through `docker compose exec` is the same trust
boundary the person already crossed by having Docker access to their own machine, and it reaches
the database with the DSN the container already holds.

WHICH SESSION AND WHICH SCOPE ARE NOT ARGUMENTS OF ANY TOOL. The project comes from the
environment of THIS process, set by the launcher from the person's configuration. A model that
read a web page asking it to "switch project" has no tool that would do it.

WHAT THE SESSION IS. An ordinary `CONVERSATION` row whose `budget` carries `{"host": "<agent>"}`
and which has no Codex connection. No new `kind`: the CHECK of 0017 and the dashboard's schema
would both have to change. It is seeded WITHOUT a `USER_MESSAGE` (`announce_goal=False`): that
event is what `memory.write` accepts as "the person said so", and a host agent could cite seq 1
as the person's own words and record a preference nobody stated.

THE LIMIT OF ALL OF THIS, written down so nobody reads more into it. A host agent has a shell.
It can reach the database container by other means, and nothing here stops one that does not
cooperate. The narrowed catalog, the refusal of preferences and the trace are guardrails for an
agent doing what it was asked, not a security boundary against one that is not.

Exit codes, passed through by the launcher: 2 the capability is unusable (from `mcp_server`) or
cannot be written,
3 no database or the audit row could not be written, 4 the organization or project cannot be
chosen (the message says what to set), 5 the configuration is wrong (host, lifetime).

Operator-facing output is Polish (D-010) and goes to STDERR: stdout is the MCP channel, and one
stray line there would be read by the host as a malformed message. The one exception is
`projects`, which is not an MCP server and prints its list to stdout for the launcher to parse.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import asyncpg

from control_plane.audit import AuditWriter
from control_plane.auth import DSN_VARIABLE
from control_plane.sessions.store import EVENT_STATUS, SessionRow, SessionStore

from .capability import (
    ENV_CAPABILITY_FILE,
    HOST_CODEX,
    HOSTS,
    MAX_TTL_MINUTES,
    CapabilityError,
    issue,
    write_capability,
)

ENV_HOST = "WENEXT_HOST"
ENV_PROJECT = "WENEXT_PROJECT"
#: Optional. An installation is one organization (D-016) and then nothing needs saying; a database
#: that holds several (the test suite's, a shared server) names the one this plugin serves. Like
#: the project, it comes from the environment of this process and from no tool argument.
ENV_ORGANIZATION = "WENEXT_ORGANIZATION"
ENV_TTL_HOURS = "WENEXT_PLUGIN_TTL_HOURS"
#: Set by the launcher to what the PERSON's browser can open (`http://127.0.0.1:<host port>`):
#: the container knows its own port 3000, not the one the host maps it to.
ENV_DASHBOARD_URL = "WENEXT_DASHBOARD_URL"
#: The directory of capability files. The same variable and default as
#: `sessions/handlers.py::CAPABILITY_DIR`; spelled again here because importing the handler would
#: pull the whole turn machinery into a process that only serves tools.
ENV_RUN_DIR = "WENEXT_SESSION_RUN_DIR"
DEFAULT_RUN_DIR = "/tmp/wenext-sessions"

#: A plugin capability lives as long as a working day of the person's agent window, not one turn:
#: the Wenext-run default (a turn plus five minutes) would expire in the middle of the work.
DEFAULT_TTL_HOURS = 8.0
MAX_TTL_HOURS = MAX_TTL_MINUTES / 60

#: The goal of a plugin session. It describes what the row IS for a person reading the list of
#: conversations; nobody typed it, which is why it is not a `USER_MESSAGE`.
PLUGIN_GOAL = "Wtyczka: praca w Twoim agencie"

#: How an agent is called in a sentence a person reads.
HOST_LABELS = {"codex": "Codex", "claude-code": "Claude Code"}

EXIT_CAPABILITY = 2  # the same code `mcp_server.main` gives an unusable capability
EXIT_NO_DATABASE = 3
EXIT_NO_PROJECT = 4
EXIT_BAD_CONFIG = 5


class PluginRefusal(Exception):
    """Something the person has to fix, in a sentence that says what (Polish, D-010)."""


@dataclass(frozen=True)
class ProjectChoice:
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    name: str
    workspace_name: str


def _one_line(text: str) -> str:
    """A name safe to print on one line and to split on a tab: whitespace runs become one space."""
    return re.sub(r"\s+", " ", text).strip()


def _listed(projects: list[ProjectChoice]) -> str:
    return ", ".join(f"{_one_line(one.name)} ({one.project_id})" for one in projects)


def choose_project(projects: list[ProjectChoice], reference: str | None) -> ProjectChoice:
    """The project this plugin works in, or the refusal that says what to set. PURE.

    `reference` is `WENEXT_PROJECT`: an id, or the name of a project (the exact name first, then
    ignoring case).
    Without one the only non-archived project is chosen, and ANY other count is a refusal:
    guessing between two projects would put drafts in the one the person did not mean, and a
    draft in the wrong project is a draft for the wrong audience.
    """
    wanted = (reference or "").strip()
    if not wanted:
        if not projects:
            raise PluginRefusal(
                "W Wenext nie ma jeszcze projektu. Utwórz go w dashboardzie i uruchom agenta "
                "ponownie."
            )
        if len(projects) > 1:
            raise PluginRefusal(
                f"Jest kilka projektów. Ustaw WENEXT_PROJECT. Dostępne: {_listed(projects)}"
            )
        return projects[0]

    try:
        by_id = UUID(wanted)
    except ValueError:
        by_id = None
    matches = [one for one in projects if by_id is not None and one.project_id == by_id]
    if not matches:
        # The exact name wins; only without one is the case ignored. "alfa" finds "Alfa", but
        # when both "Alfa" and "alfa" exist, "Alfa" must mean the first and not be ambiguous.
        matches = [one for one in projects if one.name.strip() == wanted]
    if not matches:
        matches = [one for one in projects if one.name.strip().casefold() == wanted.casefold()]
    if not matches:
        raise PluginRefusal(
            f"Nie ma projektu {wanted!r} wśród aktywnych. Dostępne: {_listed(projects) or 'brak'}"
        )
    if len(matches) > 1:
        raise PluginRefusal(
            f"Nazwa {wanted!r} pasuje do kilku projektów. Podaj w WENEXT_PROJECT identyfikator. "
            f"Pasują: {_listed(matches)}"
        )
    return matches[0]


async def list_projects(pool: asyncpg.Pool, organization: str | None = None) -> list[ProjectChoice]:
    """The non-archived projects of the organization, oldest first.

    The organization is resolved, never guessed (D-016): the one that exists, or the one named by
    `organization`; zero, or several with none named, is a refusal. The query is three lines here
    rather than an import of a private name from a dev tool, the choice `runtime.py` documents
    for itself.
    """
    organizations = await pool.fetch("SELECT id, name FROM organizations ORDER BY created_at, id")
    named = (organization or "").strip()
    if named:
        try:
            wanted = UUID(named)
        except ValueError:
            raise PluginRefusal(
                f"{ENV_ORGANIZATION}={named!r} nie jest identyfikatorem organizacji."
            ) from None
        organizations = [row for row in organizations if row["id"] == wanted]
        if not organizations:
            raise PluginRefusal(f"W bazie nie ma organizacji {named}.")
    if not organizations:
        raise PluginRefusal("W bazie nie ma żadnej organizacji. Najpierw uruchom make bootstrap.")
    if len(organizations) > 1:
        raise PluginRefusal(
            f"W bazie jest {len(organizations)} organizacji. Ustaw {ENV_ORGANIZATION} na "
            "identyfikator tej, której projekty ma obsługiwać wtyczka."
        )
    organization_id = organizations[0]["id"]
    rows = await pool.fetch(
        """
        SELECT p.id, p.workspace_id, p.name, w.name AS workspace_name
          FROM projects p
          JOIN workspaces w ON w.organization_id = p.organization_id AND w.id = p.workspace_id
         WHERE p.organization_id = $1 AND p.archived_at IS NULL
         ORDER BY p.created_at, p.id
        """,
        organization_id,
    )
    return [
        ProjectChoice(
            organization_id=organization_id,
            workspace_id=row["workspace_id"],
            project_id=row["id"],
            name=row["name"],
            workspace_name=row["workspace_name"],
        )
        for row in rows
    ]


def dashboard_path(choice: ProjectChoice, session: SessionRow) -> str:
    """Where the dashboard shows this conversation (`app/(studio)/w/.../p/.../c/...`)."""
    return f"/w/{choice.workspace_id}/p/{choice.project_id}/c/{session.id}"


async def open_session(
    pool: asyncpg.Pool, choice: ProjectChoice, host: str
) -> tuple[SessionRow, bool]:
    """Find or create the plugin session, and say so in its trace when it is new."""
    store = SessionStore(pool)
    session, created = await store.open_host_session(
        organization_id=choice.organization_id,
        workspace_id=choice.workspace_id,
        project_id=choice.project_id,
        host=host,
        goal=PLUGIN_GOAL,
    )
    if created:
        # A STATUS and not a USER_MESSAGE: the screen would otherwise open on nothing, and a
        # status is not what `memory.write` accepts as the person's word (`_confirmed_by_a_person`).
        await store.append_event(
            organization_id=choice.organization_id,
            session_id=session.id,
            kind=EVENT_STATUS,
            payload={
                "detail": (
                    f"Ta rozmowa jest prowadzona w Twoim agencie ({HOST_LABELS[host]}). Tutaj "
                    "widzisz, co agent zrobił, i zatwierdzasz szkice."
                )
            },
        )
    return session, created


async def record_audit(
    pool: asyncpg.Pool, choice: ProjectChoice, session: SessionRow, host: str, *, created: bool
) -> None:
    """The audit row of a capability issued to a host agent. Ids and one flag, no prose."""
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="PLUGIN_SESSION_STARTED",
            organization_id=choice.organization_id,
            workspace_id=choice.workspace_id,
            subject_ref=f"session:{session.id}",
            actor_ref=f"plugin:{host}",
            payload={
                "session_id": str(session.id),
                "project_id": str(choice.project_id),
                "created": created,
            },
        )


def _say(message: str) -> None:
    print(f"wenext-plugin: {message}", file=sys.stderr, flush=True)


def _host_from(argument: str | None) -> str:
    host = (argument or os.environ.get(ENV_HOST) or HOST_CODEX).strip()
    if host not in HOSTS:
        raise PluginRefusal(
            f"Nieznany agent {host!r} w WENEXT_HOST. Dozwolone: {', '.join(sorted(HOSTS))}."
        )
    return host


def _ttl_minutes() -> int:
    raw = (os.environ.get(ENV_TTL_HOURS) or "").strip()
    try:
        hours = float(raw) if raw else DEFAULT_TTL_HOURS
    except ValueError:
        raise PluginRefusal(f"{ENV_TTL_HOURS}={raw!r} nie jest liczbą godzin.") from None
    if not 0 < hours <= MAX_TTL_HOURS:
        raise PluginRefusal(
            f"{ENV_TTL_HOURS}={raw!r} poza zakresem: od zera do {MAX_TTL_HOURS:g} godzin."
        )
    return max(1, int(hours * 60))


async def _open_pool(dsn: str) -> asyncpg.Pool:
    # Wrapped in a coroutine for the reason `mcp_server.main` documents: a Pool is awaitable but
    # not a coroutine, and the loop machinery wants a coroutine.
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4)


async def _prepare(
    dsn: str, host: str, reference: str | None, organization: str | None
) -> tuple[ProjectChoice, SessionRow, bool]:
    """Everything before the protocol loop, on one short-lived pool: choice, session, audit."""
    pool = await _open_pool(dsn)
    try:
        choice = choose_project(await list_projects(pool, organization), reference)
        session, created = await open_session(pool, choice, host)
        await record_audit(pool, choice, session, host, created=created)
    finally:
        await pool.close()
    return choice, session, created


def _announce(choice: ProjectChoice, session: SessionRow, host: str, *, created: bool) -> None:
    path = dashboard_path(choice, session)
    base = (os.environ.get(ENV_DASHBOARD_URL) or "").strip().rstrip("/")
    state = "nowa rozmowa" if created else "ta sama rozmowa co poprzednio"
    _say(
        f"projekt {_one_line(choice.name)}, agent {HOST_LABELS[host]}, {state}. "
        f"Szkice zatwierdzasz tutaj: {base}{path}"
    )


def serve(
    host_argument: str | None,
    project_argument: str | None,
    organization_argument: str | None = None,
) -> int:
    from . import mcp_server

    try:
        host = _host_from(host_argument)
        ttl_minutes = _ttl_minutes()
    except PluginRefusal as exc:
        _say(str(exc))
        return EXIT_BAD_CONFIG
    dsn = (os.environ.get(DSN_VARIABLE) or "").strip()
    if not dsn:
        _say(f"Brak {DSN_VARIABLE}: ten proces ma biec w kontenerze api, gdzie baza jest znana.")
        return EXIT_NO_DATABASE
    reference = project_argument if project_argument is not None else os.environ.get(ENV_PROJECT)
    organization = (
        organization_argument
        if organization_argument is not None
        else os.environ.get(ENV_ORGANIZATION)
    )

    try:
        choice, session, created = asyncio.run(_prepare(dsn, host, reference, organization))
    except PluginRefusal as exc:
        _say(str(exc))
        return EXIT_NO_PROJECT
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        _say(f"Nie udało się przygotować rozmowy w bazie: {type(exc).__name__}: {exc}")
        return EXIT_NO_DATABASE

    try:
        capability = issue(
            session_id=session.id,
            organization_id=choice.organization_id,
            workspace_id=choice.workspace_id,
            project_id=choice.project_id,
            dsn=dsn,
            ttl_minutes=ttl_minutes,
            host=host,
        )
    except CapabilityError as exc:
        _say(str(exc))
        return EXIT_BAD_CONFIG

    # One file per PROCESS, not per session: two agent windows reuse one session, and the one that
    # closes first must not unlink the other's capability from under it.
    run_dir = Path(os.environ.get(ENV_RUN_DIR) or DEFAULT_RUN_DIR)
    capability_file = run_dir / f"{session.id}-{os.getpid()}.json"
    # The `try` opens BEFORE the write: a file half written when the disk or the permissions
    # fail is still a file with a DSN in it, and the `finally` is what removes it.
    try:
        write_capability(capability_file, capability)
        os.environ[ENV_CAPABILITY_FILE] = str(capability_file)
        # A terminated process runs its `finally` only if the signal becomes an exit.
        for name in ("SIGTERM", "SIGHUP"):
            with contextlib.suppress(OSError, ValueError, AttributeError):
                signal.signal(getattr(signal, name), lambda *_: sys.exit(143))
        _announce(choice, session, host, created=created)
        return mcp_server.main()
    except OSError as exc:
        _say(f"Nie mogę zapisać pliku zdolności {capability_file}: {type(exc).__name__}: {exc}")
        return EXIT_CAPABILITY
    finally:
        with contextlib.suppress(OSError):
            capability_file.unlink(missing_ok=True)


def projects(organization_argument: str | None = None) -> int:
    """Print `<id><TAB><project name>` for every active project, one per line. For the launcher.

    The name is the project's own, not prefixed with its workspace: the contract with the launcher
    is two columns, and the id in the first one is what tells two projects of one name apart.
    """
    dsn = (os.environ.get(DSN_VARIABLE) or "").strip()
    if not dsn:
        _say(f"Brak {DSN_VARIABLE}: ten proces ma biec w kontenerze api, gdzie baza jest znana.")
        return EXIT_NO_DATABASE

    async def _list() -> list[ProjectChoice]:
        pool = await _open_pool(dsn)
        try:
            return await list_projects(
                pool,
                organization_argument
                if organization_argument is not None
                else os.environ.get(ENV_ORGANIZATION),
            )
        finally:
            await pool.close()

    try:
        found = asyncio.run(_list())
    except PluginRefusal as exc:
        _say(str(exc))
        return EXIT_NO_PROJECT
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        _say(f"Nie udało się odczytać projektów: {type(exc).__name__}: {exc}")
        return EXIT_NO_DATABASE
    for one in found:
        print(f"{one.project_id}\t{_one_line(one.name)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_plane.tools.plugin_server",
        description=(
            "Serwer narzędzi Wenext dla agenta człowieka (Codex, Claude Code). Uruchamia go "
            "launcher wtyczki przez `docker compose exec`, nie człowiek."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser(
        "serve",
        help="znajdź albo załóż rozmowę projektu i uruchom serwer MCP na stdin/stdout",
    )
    run.add_argument(
        "--host",
        default=None,
        help=f"agent: {', '.join(sorted(HOSTS))}; domyślnie {ENV_HOST}, potem {HOST_CODEX}",
    )
    run.add_argument(
        "--project",
        default=None,
        help=f"identyfikator albo nazwa projektu; domyślnie {ENV_PROJECT}, potem jedyny projekt",
    )
    run.add_argument(
        "--organization",
        default=None,
        help=f"identyfikator organizacji; domyślnie {ENV_ORGANIZATION}, potem jedyna w bazie",
    )
    listing = subcommands.add_parser(
        "projects", help="wypisz aktywne projekty (identyfikator, tabulator, nazwa)"
    )
    listing.add_argument("--organization", default=None, help="jak przy `serve`")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "serve":
        return serve(arguments.host, arguments.project, arguments.organization)
    if arguments.command == "projects":
        return projects(arguments.organization)
    raise SystemExit(f"nieznana podkomenda: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
