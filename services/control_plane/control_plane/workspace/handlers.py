"""The two queue jobs of a Codex connection: waiting out a login, and re-reading the state.

WHY THE LOGIN IS A JOB AND NOT AN HTTP REQUEST. A device-code login is completed by the
APP-SERVER PROCESS, which holds the `loginId` and exchanges it once the human enters the code.
The human needs up to fifteen minutes. An HTTP handler holding that would occupy a worker for
the whole time and die with the first timeout anywhere in the chain, and the code would then
never be exchanged — the screen would show a code that cannot work. So the wait runs in the
queue this product already has, with its lease, its heartbeat and its restart behaviour.

`ATTEMPT_LIMIT = 1` AND THAT IS THE POINT. Every other job in this system retries; this one must
not. A retry starts a SECOND device code while the human is still looking at the first, and the
code on the screen silently stops working. A login that ran out of time is a finished job with a
recorded reason, and starting another one is a decision the human makes by pressing the button
again.

THE JOB NEVER RAISES FOR A REFUSAL IT UNDERSTOOD. Device code disabled on the account, the human
cancelling, the deadline passing: each is written to the row as a state with a sentence and the
job completes. An exception is reserved for what nobody diagnosed, so that a failed job in the
queue keeps meaning "something unexpected", not "the user changed their mind".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.dispatcher import Handler
from control_plane.workspace.codex_home import ROOT_READ_DISABLED_REASON, ROOT_REF, resolve_home
from control_plane.workspace.connections import CodexConnectionStore
from control_plane.workspace.engine import (
    CodexEnginePort,
    EngineUnavailable,
    LoginCancelled,
    LoginRefused,
    LoginTicket,
    LoginTimedOut,
)

JOB_LOGIN = "codex.connection.login"
JOB_READ = "codex.connection.read"
ROUTING_KEY = "codex.connection"

#: Below the observe jobs: a human is watching this one, and a poll that overtakes it only
#: delays a screen nobody is looking at.
PRIORITY = 50

#: One attempt. See the module docstring — a retry would start a second device code.
ATTEMPT_LIMIT = 1

#: How long the human has. `CodexAccountAdapter.login_timeout_s` defaults to the same 900 s, and
#: this is the value the row publishes so the screen can say how much time is left.
LOGIN_DEADLINE = timedelta(seconds=900)

_PREFIX = "codex-connection:"


def connection_ref(connection_id: UUID) -> str:
    return f"{_PREFIX}{connection_id}"


def connection_from_ref(payload_ref: str | None) -> UUID:
    if not payload_ref or not payload_ref.startswith(_PREFIX):
        raise ValueError(
            f"payload_ref={payload_ref!r} nie wskazuje połączenia Codex. Job bez wskazania "
            "połączenia nie ma czego zalogować"
        )
    return UUID(payload_ref[len(_PREFIX) :])


async def schedule_login(
    port,
    *,
    organization_id: UUID,
    connection_id: UUID,
) -> object:
    """Put the login wait in the queue. The idempotency key is the connection and the attempt.

    Keyed by the connection ALONE would mean a second login for the same connection is silently
    dropped after the first one expired, which is exactly the button the human presses again.
    Keyed by connection plus a timestamp would enqueue a duplicate on a double click. The key is
    therefore the connection plus the minute: a double click does nothing, a retry a minute later
    works.
    """
    minute = int(datetime.now(UTC).timestamp() // 60)
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_LOGIN,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"codex-login:{connection_id}:{minute}",
        payload_ref=connection_ref(connection_id),
    )


async def schedule_read(port, *, organization_id: UUID, connection_id: UUID) -> object:
    minute = int(datetime.now(UTC).timestamp() // 60)
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_READ,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"codex-read:{connection_id}:{minute}",
        payload_ref=connection_ref(connection_id),
    )


@dataclass
class CodexConnectionHandlers:
    """Both handlers over one store and one engine port.

    `engine` and `home_root` may be None, and then every job records a stated reason instead of
    raising: a deployment with no `WENEXT_CODEX_HOME` cannot do this, and the screen has to say
    so in words rather than show a failed job nobody can interpret.
    """

    store: CodexConnectionStore
    engine: CodexEnginePort | None
    home_root: Path | None
    organization_id: UUID

    def as_mapping(self) -> dict[str, Handler]:
        return {JOB_LOGIN: self.wait_for_login, JOB_READ: self.read_connection}

    async def _home(self, claim: ClaimedJob) -> tuple[UUID, Path] | None:
        """The directory to work in, or None when this job must not open an app-server.

        Every `None` here is a decision recorded on the row, not a silence.
        """
        connection_id = connection_from_ref(claim.payload_ref)
        now = datetime.now(UTC)
        row = await self.store.get(
            organization_id=self.organization_id, connection_id=connection_id
        )
        if row is None:
            # The connection was removed between enqueueing and claiming. Nothing to record on
            # and nothing wrong: the job finishes.
            return None
        if row.home_ref == ROOT_REF:
            # THE VOLUME ROOT IS NEVER OPENED BY US. It is the directory the live pilot's turn
            # runner uses, and two app-servers on one `CODEX_HOME` are forbidden (L2159) —
            # see `ROOT_READ_DISABLED_REASON` for what would actually break. The screen gets the
            # sentence and the files; it does not get an invented state.
            await self.store.record_not_read(
                connection_id=connection_id, reason=ROOT_READ_DISABLED_REASON, now=now
            )
            return None
        if self.engine is None or self.home_root is None:
            await self.store.record_error(
                connection_id=connection_id,
                reason=(
                    "to wdrożenie nie ma skonfigurowanego katalogu Codeksa (WENEXT_CODEX_HOME) "
                    "albo runtime'u app-servera, więc nie ma z czym rozmawiać"
                ),
                now=now,
            )
            return None
        return connection_id, resolve_home(self.home_root, row.home_ref)

    async def wait_for_login(self, claim: ClaimedJob) -> None:
        resolved = await self._home(claim)
        if resolved is None:
            return
        connection_id, home = resolved
        assert self.engine is not None
        deadline = datetime.now(UTC) + LOGIN_DEADLINE

        async def publish(ticket: LoginTicket) -> None:
            await self.store.set_login_ticket(
                connection_id=connection_id, ticket=ticket, deadline=deadline
            )

        async def cancelled() -> bool:
            return await self.store.cancel_requested(connection_id=connection_id)

        try:
            snapshot = await self.engine.run_login(
                home=home,
                on_ticket=publish,
                should_cancel=cancelled,
                deadline=deadline,
            )
        except LoginCancelled:
            # The row is already DISCONNECTED — that is what the job saw. Nothing to add: writing
            # ERROR here would turn the human's own decision into a fault.
            return
        except (LoginRefused, LoginTimedOut, EngineUnavailable) as exc:
            await self.store.record_error(
                connection_id=connection_id, reason=str(exc), now=datetime.now(UTC)
            )
            return
        await self.store.record_snapshot(
            connection_id=connection_id, snapshot=snapshot, now=datetime.now(UTC)
        )

    async def read_connection(self, claim: ClaimedJob) -> None:
        """Re-read one connection: account, model catalogue, limits.

        Works on a connection that is not logged in too, and records exactly that — the catalogue
        comes back (measured: `model/list` needs no account) while the limits come back as
        `read: false` with the app-server's reason.
        """
        resolved = await self._home(claim)
        if resolved is None:
            return
        connection_id, home = resolved
        assert self.engine is not None
        try:
            snapshot = await self.engine.read(home=home)
        except EngineUnavailable as exc:
            await self.store.record_error(
                connection_id=connection_id, reason=str(exc), now=datetime.now(UTC)
            )
            return
        await self.store.record_snapshot(
            connection_id=connection_id, snapshot=snapshot, now=datetime.now(UTC)
        )
