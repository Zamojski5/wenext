"""`CodexAccountAdapter` — App Server, ChatGPT login, model catalog, rate limits (13.1-13.2).

The split from L2107-2115 is taken literally here: this adapter handles the login, the account
state, the model list and the limits. Starting threads belongs to `CodexExecutionAdapter`.

The state machine is transcribed from L2134-2141 without adding transitions. `PRZEJSCIA` is
the only place where that knowledge lives — `przejdz()` refuses everything outside the table,
so "the state drifted apart" is not representable in this module.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from community_os_contracts import CodexConnectionState as ConnectionState

from .protocol import AppServerClient, RemoteError

#: L2134-2141, transcribed one to one. Nothing beyond that is allowed.
TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.DISCONNECTED: frozenset({ConnectionState.AUTH_PENDING}),
    ConnectionState.AUTH_PENDING: frozenset(
        {ConnectionState.AUTHENTICATED, ConnectionState.DISABLED, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.AUTHENTICATED: frozenset({ConnectionState.CAPABILITIES_SYNCING}),
    ConnectionState.CAPABILITIES_SYNCING: frozenset(
        {
            ConnectionState.READY,
            ConnectionState.REAUTH_REQUIRED,
            ConnectionState.SYNC_RETRY,
            ConnectionState.DISABLED,
        }
    ),
    ConnectionState.READY: frozenset(
        {
            ConnectionState.CAPABILITIES_SYNCING,
            ConnectionState.RATE_LIMITED,
            ConnectionState.REAUTH_REQUIRED,
            ConnectionState.DISABLED,
        }
    ),
    ConnectionState.SYNC_RETRY: frozenset(
        {ConnectionState.CAPABILITIES_SYNCING, ConnectionState.DISABLED}
    ),
    ConnectionState.RATE_LIMITED: frozenset(
        {
            ConnectionState.READY,
            ConnectionState.CAPABILITIES_SYNCING,
            ConnectionState.REAUTH_REQUIRED,
        }
    ),
    ConnectionState.REAUTH_REQUIRED: frozenset({ConnectionState.AUTH_PENDING}),
    ConnectionState.DISABLED: frozenset({ConnectionState.DISCONNECTED}),
}


class IllegalTransition(Exception):
    """A transition outside the L2134-2141 table."""


class LoginUnavailable(Exception):
    """`account/login/start` refused the method — the server's result decides, not a preflight
    (L2181).
    """


class SnapshotIncomplete(Exception):
    """Routing on an incomplete model catalog is forbidden (L2189)."""


@dataclass(frozen=True)
class EffortOption:
    reasoning_effort: str
    description: str = ""


@dataclass(frozen=True)
class ModelInfo:
    """A single catalog entry. Model names are NOT hardcoded anywhere in this package."""

    id: str
    model: str
    display_name: str
    hidden: bool
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[str, ...]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ModelInfo:
        effort_options = data.get("supportedReasoningEfforts", [])
        efforts = tuple(o["reasoningEffort"] for o in effort_options if "reasoningEffort" in o)
        return cls(
            id=data["id"],
            model=data.get("model", data["id"]),
            display_name=data.get("displayName", data["id"]),
            hidden=bool(data.get("hidden", False)),
            is_default=bool(data.get("isDefault", False)),
            default_reasoning_effort=data.get("defaultReasoningEffort", ""),
            supported_reasoning_efforts=efforts,
        )


@dataclass(frozen=True)
class ModelCatalogSnapshot:
    """A model catalog snapshot. `COMPLETE` only after reaching `nextCursor=null` (L2189)."""

    models: tuple[ModelInfo, ...]
    complete: bool
    include_hidden: bool
    page_count: int
    content_hash: str
    fetched_at: datetime

    def model(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.models if m.id == model_id), None)

    def supports(self, model_id: str, effort: str) -> bool:
        m = self.model(model_id)
        return bool(m and effort in m.supported_reasoning_efforts)


@dataclass(frozen=True)
class LoginAttempt:
    """Our own `LoginAttempt` with a local deadline, a cancel and a status (L2183)."""

    login_id: str
    verification_url: str
    user_code: str
    deadline: datetime
    method: str = "chatgptDeviceCode"

    def is_expired(self, teraz: datetime | None = None) -> bool:
        return (teraz or datetime.now(UTC)) >= self.deadline


@dataclass(frozen=True)
class AccountState:
    """We record ONLY documented and actually returned fields (L2185).

    `account/read` without a login returns `{"account": null, "requiresOpenaiAuth": true}` —
    checked by running it. This is not an error and it is not evidence of the account owner.
    """

    logged_in: bool
    auth_type: str | None
    email: str | None
    plan_type: str | None
    requires_openai_auth: bool
    #: A stable local fingerprint of the connection — NOT evidence of the real owner (L2185).
    fingerprint: str


@dataclass
class CodexAccountAdapter:
    """The Codex connection lifecycle: login, account state, model catalog, rate limits."""

    client: AppServerClient
    #: `includeHidden` is explicit and in production defaults to `false` (L2187).
    include_hidden: bool = False
    login_timeout_s: float = 900.0
    _state: ConnectionState = field(default=ConnectionState.DISCONNECTED, init=False)
    _history: list[tuple[ConnectionState, ConnectionState]] = field(
        default_factory=list, init=False
    )
    snapshot: ModelCatalogSnapshot | None = field(default=None, init=False)
    last_login_attempt: LoginAttempt | None = field(default=None, init=False)

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def history(self) -> list[tuple[ConnectionState, ConnectionState]]:
        return list(self._history)

    def transition_to(self, target: ConnectionState) -> None:
        allowed = TRANSITIONS[self._state]
        if target not in allowed:
            raise IllegalTransition(
                f"{self._state} → {target} nie istnieje w L2134-2141. "
                f"Dozwolone z {self._state}: {', '.join(sorted(allowed))}"
            )
        self._history.append((self._state, target))
        self._state = target

    # -- login ----------------------------------------------------------------

    def start_device_code_login(self) -> LoginAttempt:
        """`account/login/start(chatgptDeviceCode)` — L2166-2172.

        Device code is beta and may require enabling by the user or by the workspace
        admin. What decides is the RESULT of this call, including an explicit error about
        an unavailable method — not an invented preflight endpoint (L2181).
        """
        self.transition_to(ConnectionState.AUTH_PENDING)
        try:
            response = self.client.request("account/login/start", {"type": "chatgptDeviceCode"})
        except RemoteError as exc:
            self.transition_to(ConnectionState.DISABLED)
            raise LoginUnavailable(
                f"account/login/start(chatgptDeviceCode) odmówił: {exc.message}. "
                "Prawdopodobnie device code login nie jest włączony w ustawieniach konta "
                "albo w uprawnieniach workspace'u."
            ) from exc

        login_attempt = LoginAttempt(
            login_id=response["loginId"],
            verification_url=response["verificationUrl"],
            user_code=response["userCode"],
            deadline=datetime.fromtimestamp(time.time() + self.login_timeout_s, tz=UTC),
        )
        self.last_login_attempt = login_attempt
        return login_attempt

    def cancel_login(self) -> None:
        if self.last_login_attempt is None:
            return
        try:
            self.client.request(
                "account/login/cancel", {"loginId": self.last_login_attempt.login_id}
            )
        except RemoteError:
            pass
        finally:
            self.last_login_attempt = None
            if self._state is ConnectionState.AUTH_PENDING:
                self.transition_to(ConnectionState.DISCONNECTED)

    def wait_for_login(self, *, interval_s: float = 2.0) -> AccountState:
        """Waits for the login to complete, by polling `account/read`.

        BEWARE of the trap that has already blocked this once: `klient.notyfikacje` is
        an ordinary list, filled ONLY as a side effect of reading frames in `request()`.
        A loop that merely sleeps and peeks into that list never pumps the app-server
        stream — the `account/login/completed` notification has no way of getting into it
        and the wait runs until the deadline, even though the human entered the code long ago.

        That is why we ask `account/read`. This has two advantages at once: it pumps the
        channel (so the notifications land in the list anyway) and it is the SOURCE OF TRUTH —
        the presence of the account in the response says outright whether we are logged in,
        without relying on the notification arriving at all.
        """
        login_attempt = self.last_login_attempt
        if login_attempt is None:
            raise LoginUnavailable("nie ma otwartej próby logowania")

        while not login_attempt.is_expired():
            state = self.read_account()
            if state.logged_in:
                if self._state is ConnectionState.AUTH_PENDING:
                    self.transition_to(ConnectionState.AUTHENTICATED)
                self.last_login_attempt = None
                return state

            # The notification is sometimes faster than the next `account/read` — if it has
            # already arrived (and it arrived alongside the call above), we confirm it by reading.
            if any(r.get("method") == "account/login/completed" for r in self.client.notifications):
                if self._state is ConnectionState.AUTH_PENDING:
                    self.transition_to(ConnectionState.AUTHENTICATED)
                self.last_login_attempt = None
                return self.read_account()

            time.sleep(interval_s)

        raise TimeoutError(
            f"login {login_attempt.login_id} nie został ukończony do "
            f"{login_attempt.deadline.isoformat()}"
        )

    # -- account state --------------------------------------------------------

    def read_account(self) -> AccountState:
        response = self.client.request("account/read", {})
        konto = response.get("account")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "codex_home": str(self.client.codex_home),
                    "user_agent": (self.client.initialize_response or {}).get("userAgent", ""),
                    "auth_type": (konto or {}).get("type"),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return AccountState(
            logged_in=konto is not None,
            auth_type=(konto or {}).get("type"),
            email=(konto or {}).get("email"),
            plan_type=(konto or {}).get("planType"),
            requires_openai_auth=bool(response.get("requiresOpenaiAuth", True)),
            fingerprint=fingerprint,
        )

    # -- model catalog --------------------------------------------------------

    def sync_model_catalog(self, *, max_pages: int = 50) -> ModelCatalogSnapshot:
        """`model/list` iterated until `nextCursor=null` — L2187-2189.

        A single call without a loop over `nextCursor` gives an incomplete list, and an
        incomplete snapshot blocks routing with the `WAITING_CAPABILITY` state. That is why
        `complete` is a field of the snapshot, and not an assumption.
        """
        if self._state is not ConnectionState.CAPABILITIES_SYNCING:
            self.transition_to(ConnectionState.CAPABILITIES_SYNCING)

        models: list[ModelInfo] = []
        cursor: str | None = None
        strony = 0
        complete = False
        while strony < max_pages:
            params: dict[str, Any] = {"includeHidden": self.include_hidden}
            if cursor is not None:
                params["cursor"] = cursor
            response = self.client.request("model/list", params)
            strony += 1
            models.extend(ModelInfo.from_json(m) for m in response.get("data", []))
            cursor = response.get("nextCursor")
            if cursor is None:
                complete = True
                break

        content = json.dumps(
            [
                [
                    m.id,
                    m.model,
                    m.hidden,
                    m.default_reasoning_effort,
                    list(m.supported_reasoning_efforts),
                ]
                for m in models
            ],
            sort_keys=True,
        )
        snapshot = ModelCatalogSnapshot(
            models=tuple(models),
            complete=complete,
            include_hidden=self.include_hidden,
            page_count=strony,
            content_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            fetched_at=datetime.now(UTC),
        )
        self.snapshot = snapshot
        if complete:
            self.transition_to(ConnectionState.READY)
        else:
            self.transition_to(ConnectionState.SYNC_RETRY)
        return snapshot

    def require_complete_snapshot(self) -> ModelCatalogSnapshot:
        """The router does not guess model names or efforts (L2189)."""
        if self.snapshot is None or not self.snapshot.complete:
            raise SnapshotIncomplete(
                "katalog modeli jest niekompletny albo nie został pobrany — job idzie "
                "w WAITING_CAPABILITY, routing jest zablokowany (L2189, L5287)"
            )
        return self.snapshot

    # -- rate limits ----------------------------------------------------------

    def read_rate_limits(self) -> dict[str, Any]:
        """`account/rateLimits/read` — L2176.

        Without a logged-in account the app-server answers with the error
        "codex account authentication required to read rate limits" (measured).
        We raise it further up: we do NOT fake the limits.
        """
        return self.client.request("account/rateLimits/read", {})

    def mark_rate_limited(self) -> None:
        self.transition_to(ConnectionState.RATE_LIMITED)

    def after_rate_limit_clears(self, *, snapshot_fresh: bool) -> ConnectionState:
        """The return from `RATE_LIMITED` — L2145.

        "After the rate limit ends the connection returns directly to READY only when the
        snapshot stays complete and fresh; otherwise it synchronizes the catalog."
        """
        is_complete = self.snapshot is not None and self.snapshot.complete
        self.transition_to(
            ConnectionState.READY
            if (snapshot_fresh and is_complete)
            else ConnectionState.CAPABILITIES_SYNCING
        )
        return self._state


def find_codex_bin() -> Path:
    """The path to the `codex` binary shipped by `openai-codex-cli-bin`.

    The order: the binary from the installed package (because that is the one pinned together
    with the SDK), and only then `codex` from PATH. Mixing CLI and SDK versions is forbidden —
    the app-server protocol is versioned together with the CLI.
    """
    try:
        import codex_cli_bin  # conditional import, the package is sometimes absent

        candidate = Path(codex_cli_bin.__file__).parent / "bin" / "codex"
        if candidate.exists():
            return candidate
    except ImportError:
        pass

    import shutil

    codex_on_path = shutil.which("codex")
    if codex_on_path:
        return Path(codex_on_path)
    raise FileNotFoundError(
        "nie znaleziono binarki `codex`: brak pakietu openai-codex-cli-bin i brak `codex` na PATH"
    )
