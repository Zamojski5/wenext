"""Job waiting states — L2144-2147.

"Jobs in `WAITING_*` states do not hold the runtime lease; the transition to `READY` emits
an event that atomically creates a new dispatch version for the waiting jobs." (L2147)

In F0 there is no job queue — that is explicitly F1 (L5193). This module supplies only the
mapping of the connection state and of runtime errors onto a waiting state, plus the invariant
"WAITING does not hold the lease", so that F1 does not have to derive it anew.
"""

from __future__ import annotations

from dataclasses import dataclass

from community_os_contracts import CodexConnectionState as ConnectionState
from community_os_contracts import JobWaitingState

#: None of the WAITING states holds the runtime lease (L2147). We keep this as data,
#: not as a sentence in a comment, because F1 will be checking it in the reaper.
WAITING_HOLDS_NO_LEASE: frozenset[JobWaitingState] = frozenset(JobWaitingState)


@dataclass(frozen=True)
class DispatchDecision:
    """Whether the job can go to the runtime, and if not, what it is waiting for."""

    can_dispatch: bool
    waiting_state: JobWaitingState | None
    reason: str

    def __post_init__(self) -> None:
        if self.can_dispatch and self.waiting_state is not None:
            raise ValueError("job gotowy do dispatchu nie może mieć stanu oczekiwania")
        if not self.can_dispatch and self.waiting_state is None:
            raise ValueError("job wstrzymany musi mieć jawny stan oczekiwania, nie milczenie")


def evaluate_dispatch(
    connection_state: ConnectionState,
    *,
    snapshot_complete: bool,
    sidecar_available: bool,
) -> DispatchDecision:
    """Maps the state of the world onto a dispatch decision and a `WAITING_*` state.

    The order of the checks matters: missing auth comes before missing quota, and a missing
    capability comes before everything to do with tools — because without the model catalog
    a turn cannot even be built, so waiting for the sidecar would be waiting for the wrong thing.
    """
    if connection_state in {
        ConnectionState.DISCONNECTED,
        ConnectionState.AUTH_PENDING,
        ConnectionState.REAUTH_REQUIRED,
    }:
        return DispatchDecision(
            False, JobWaitingState.WAITING_AUTH, f"połączenie w stanie {connection_state}"
        )
    if connection_state is ConnectionState.DISABLED:
        return DispatchDecision(
            False,
            JobWaitingState.WAITING_AUTH,
            "połączenie DISABLED — wymaga decyzji operatora, nie automatycznego retry",
        )
    if connection_state is ConnectionState.RATE_LIMITED:
        return DispatchDecision(
            False, JobWaitingState.WAITING_QUOTA, "okno limitu planu ChatGPT wyczerpane"
        )
    if (
        connection_state in {ConnectionState.CAPABILITIES_SYNCING, ConnectionState.SYNC_RETRY}
        or not snapshot_complete
    ):
        return DispatchDecision(
            False,
            JobWaitingState.WAITING_CAPABILITY,
            "katalog modeli niekompletny albo w trakcie synchronizacji — router nie zgaduje "
            "nazw modeli ani effortów (L2189)",
        )
    if not sidecar_available:
        return DispatchDecision(
            False,
            JobWaitingState.WAITING_CAPABILITY,
            "brak task-scoped sidecara MCP — turn kończy się fail-closed (L2503), więc job "
            "czeka, zamiast startować bez narzędzi",
        )
    if connection_state is ConnectionState.AUTHENTICATED:
        return DispatchDecision(
            False,
            JobWaitingState.WAITING_CAPABILITY,
            "połączenie uwierzytelnione, ale katalog nie został jeszcze zsynchronizowany",
        )
    return DispatchDecision(True, None, "połączenie READY, snapshot kompletny, sidecar obecny")
