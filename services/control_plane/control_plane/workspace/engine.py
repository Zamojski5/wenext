"""The port to one Codex app-server, and the real adapter behind it.

TWO OPERATIONS AND NOT MORE. `run_login` holds a device-code login from start to finish, and
`read` opens a connection's directory and reports what the engine says about it. Everything else
the product needs — which connection a session uses, what the screen prints — is decided above
this module.

WHY `run_login` IS ONE CALL AND NOT THREE. The device-code flow is completed by the APP-SERVER
PROCESS: it holds the `loginId` and exchanges it for a session once the human enters the code. A
design with `start` in one HTTP request and `poll` in another would kill that process between
them and the code would never be exchanged, while every unit test passed. So the whole wait lives
inside one call, which the job runs — and the call publishes the ticket through `on_ticket` the
moment it has one, so the screen can show the code while the wait is still going.

WHAT IS NEVER FAKED HERE. `account/rateLimits/read` answers "codex account authentication
required to read rate limits" without a login (measured, error -32600). That is recorded as
`read: false` with the reason, never as zeros and never as an empty object that the screen could
mistake for "no limits". The same for an incomplete model catalogue: `complete: false` travels
with the snapshot, because routing on an incomplete catalogue is forbidden (L2189).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class LoginCancelled(Exception):
    """The human cancelled the login; `account/login/cancel` was sent."""


class LoginTimedOut(Exception):
    """The deadline passed without the login completing."""


class LoginRefused(Exception):
    """`account/login/start` refused — most often device code is disabled on the account.

    The message carries the app-server's own words. What decides whether the method is available
    is THIS refusal, not a preflight endpoint that does not exist (L2181).
    """


class EngineUnavailable(Exception):
    """This process cannot talk to an app-server at all (package or binary missing)."""


@dataclass(frozen=True)
class LoginTicket:
    """What the human has to see: where to go and what to type."""

    login_id: str
    verification_url: str
    user_code: str


@dataclass(frozen=True)
class EngineSnapshot:
    """Everything one read of a connection produced. Every field may be absent, and says so."""

    logged_in: bool
    account_email: str | None
    plan_type: str | None
    installation_id: str | None
    #: `{"complete": bool, "count": int, "models": [...], "fetched_at": iso}`
    models: dict[str, Any]
    #: `{"read": true, "at": iso, "payload": {...}}` or `{"read": false, "reason": "..."}`
    rate_limits: dict[str, Any]
    rate_limited: bool = False


class CodexEnginePort(Protocol):
    """The two things the control plane asks of an engine. Stubbed in every test."""

    async def run_login(
        self,
        *,
        home: Path,
        on_ticket: Callable[[LoginTicket], Awaitable[None]],
        should_cancel: Callable[[], Awaitable[bool]],
        deadline: datetime,
        poll_interval_s: float = 2.0,
    ) -> EngineSnapshot: ...

    async def read(self, *, home: Path) -> EngineSnapshot: ...


def _no_limits(reason: str) -> dict[str, Any]:
    """The honest shape of "we did not read the limits". Never zeros, never `{}`."""
    return {"read": False, "reason": reason}


class AppServerEngine:
    """`CodexEnginePort` over `codex_runtime`'s narrow JSON-RPC client.

    The import is LAZY and its failure is a loud `EngineUnavailable`, the same shape
    `observe/turns.py::_import_sdk` uses for the Codex SDK. The package ships in the control
    plane image; a process without it must say it cannot do this, not degrade to something else.

    Every call runs the synchronous client in a worker thread. The app-server speaks over stdio
    (`--listen stdio://`, measured — no TCP port, so several of these cannot collide on a port),
    and the client blocks on `readline`.
    """

    def __init__(self, *, codex_bin: Path | None = None) -> None:
        self._codex_bin = codex_bin

    def _client(self, home: Path):
        try:
            from codex_runtime.account_adapter import find_codex_bin
            from codex_runtime.protocol import AppServerClient
        except ImportError as exc:  # pragma: no cover - the image installs the package
            raise EngineUnavailable(
                "pakiet `wenext-codex-runtime` nie jest zainstalowany w tym procesie — bez niego "
                "nie ma czym rozmawiać z app-serverem Codeksa"
            ) from exc
        codex_bin = self._codex_bin
        if codex_bin is None:
            try:
                codex_bin = find_codex_bin()
            except FileNotFoundError as exc:
                raise EngineUnavailable(f"nie znaleziono binarki codex: {exc}") from exc
        return AppServerClient(codex_bin=codex_bin, codex_home=Path(home))

    # -- reading ------------------------------------------------------------------

    @staticmethod
    def _started(client):
        """Start and handshake, turning "there is no binary here" into `EngineUnavailable`.

        `subprocess.Popen` raises `FileNotFoundError` for a missing or unexecutable `codex`, and
        an uncaught one would surface as a job that failed with no explanation on the screen. A
        deployment without the binary cannot do this, which is a STATE with a sentence — the same
        answer as a deployment without the package.
        """
        try:
            client.start()
            client.initialize()
        except (OSError, ValueError) as exc:
            raise EngineUnavailable(
                f"nie udało się uruchomić app-servera Codeksa na {client.codex_home}: {exc}"
            ) from exc
        return client

    def _read_blocking(self, home: Path) -> EngineSnapshot:
        from codex_runtime.account_adapter import CodexAccountAdapter
        from codex_runtime.protocol import RemoteError

        client = self._started(self._client(home))
        try:
            adapter = CodexAccountAdapter(client=client)
            account = adapter.read_account()
            return EngineSnapshot(
                logged_in=account.logged_in,
                account_email=account.email,
                plan_type=account.plan_type,
                installation_id=_installation_id(home),
                models=self._models(client),
                rate_limits=self._limits(adapter, RemoteError, logged_in=account.logged_in),
            )
        finally:
            client.close()

    @staticmethod
    def _models(client) -> dict[str, Any]:
        """`model/list` paginated to `nextCursor = null`. It does NOT need a login.

        Measured on two empty homes: five models on one page. So the catalogue can be shown
        before anybody signs in — but `complete` still travels with it, because a snapshot that
        stopped early must not be routed on (L2189).
        """
        from codex_runtime.protocol import RemoteError

        models: list[dict[str, Any]] = []
        cursor: str | None = None
        complete = False
        try:
            for _ in range(50):
                params: dict[str, Any] = {"includeHidden": False}
                if cursor is not None:
                    params["cursor"] = cursor
                page = client.request("model/list", params)
                for entry in page.get("data", []):
                    models.append(
                        {
                            "id": entry.get("id"),
                            "display_name": entry.get("displayName", entry.get("id")),
                            "default_effort": entry.get("defaultReasoningEffort"),
                        }
                    )
                cursor = page.get("nextCursor")
                if cursor is None:
                    complete = True
                    break
        except RemoteError as exc:
            return {
                "complete": False,
                "count": len(models),
                "models": models,
                "reason": exc.message,
            }
        return {
            "complete": complete,
            "count": len(models),
            "models": models,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _limits(adapter, remote_error: type[Exception], *, logged_in: bool) -> dict[str, Any]:
        if not logged_in:
            # The app-server would answer -32600 anyway; saying it without the round trip keeps
            # the reason exact instead of quoting an error about a call nobody needed to make.
            return _no_limits("bez zalogowanego konta app-server nie udostępnia limitów")
        try:
            payload = adapter.read_rate_limits()
        except remote_error as exc:  # type: ignore[misc]
            return _no_limits(getattr(exc, "message", str(exc)))
        return {"read": True, "at": datetime.now(UTC).isoformat(), "payload": payload}

    async def read(self, *, home: Path) -> EngineSnapshot:
        return await asyncio.to_thread(self._read_blocking, home)

    # -- login --------------------------------------------------------------------

    async def run_login(
        self,
        *,
        home: Path,
        on_ticket: Callable[[LoginTicket], Awaitable[None]],
        should_cancel: Callable[[], Awaitable[bool]],
        deadline: datetime,
        poll_interval_s: float = 2.0,
    ) -> EngineSnapshot:
        """Start a device-code login and hold the process until it ends, one way or another.

        The loop asks `account/read` rather than waiting for the `account/login/completed`
        notification. The adapter documents why and it is not a preference: the notification list
        fills only as a side effect of reading frames, so a loop that merely sleeps never pumps
        the stream and waits out the whole deadline with the human long since signed in.
        """
        from codex_runtime.account_adapter import CodexAccountAdapter, LoginUnavailable
        from codex_runtime.protocol import RemoteError

        client = await asyncio.to_thread(self._started, self._client(home))
        try:
            adapter = CodexAccountAdapter(client=client)
            try:
                attempt = await asyncio.to_thread(adapter.start_device_code_login)
            except LoginUnavailable as exc:
                raise LoginRefused(str(exc)) from exc

            await on_ticket(
                LoginTicket(
                    login_id=attempt.login_id,
                    verification_url=attempt.verification_url,
                    user_code=attempt.user_code,
                )
            )

            while datetime.now(UTC) < deadline:
                if await should_cancel():
                    await asyncio.to_thread(adapter.cancel_login)
                    raise LoginCancelled("logowanie anulowane przez człowieka")
                account = await asyncio.to_thread(adapter.read_account)
                if account.logged_in:
                    return EngineSnapshot(
                        logged_in=True,
                        account_email=account.email,
                        plan_type=account.plan_type,
                        installation_id=_installation_id(home),
                        models=self._models(client),
                        rate_limits=self._limits(adapter, RemoteError, logged_in=True),
                    )
                await asyncio.sleep(poll_interval_s)

            await asyncio.to_thread(adapter.cancel_login)
            raise LoginTimedOut(f"logowanie nie zostało ukończone do {deadline.isoformat()}")
        finally:
            await asyncio.to_thread(client.close)


def _installation_id(home: Path) -> str | None:
    path = Path(home) / "installation_id"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
