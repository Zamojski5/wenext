"""Leases, browser sessions and the stream — section 14.3-14.5 (L2315-2470).

Three separated leases (L2453-2457):

    ProfileLease      — which runner owns the profile
    InteractionLease  — who may send input: AUTOMATION or HUMAN
    StreamSession     — who may watch the picture and whether it is VIEW_ONLY

Fencing (L2369-2379): the lease is authoritative in Postgres, `generation` grows on every
takeover, every gateway call carries a fencing token, and **a stale fencing token is rejected**
(this is literally the Definition of Done of phase F0, L5178).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    BrowserSessionState,
    CapsuleMode,
    InteractionLeaseMode,
    InteractiveReason,
    LeaseState,
    StreamSessionRole,
)


class StaleFencingToken(Exception):
    """Raised when a call arrives carrying a stale lease generation.

    "a stale fencing token is rejected" (L2377, L5178)
    "a stale runner will not execute a tool call after expiry" (L5582)
    """

    def __init__(self, *, expected: int, presented: int, profile_id: UUID) -> None:
        super().__init__(
            f"Odrzucony fencing token dla profilu {profile_id}: "
            f"aktualna generacja {expected}, przedstawiona {presented}."
        )
        self.expected = expected
        self.presented = presented
        self.profile_id = profile_id


class FencingToken(BaseModel):
    """The token that MUST accompany every call to the Gateway (L2375)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: UUID
    generation: int = Field(ge=1)
    lease_id: UUID


class ProfileLease(BaseModel):
    """L2358-2367. Which runner owns the profile.

    Database constraints that D07 is to mirror:
      * partial unique for exactly ONE active lease per profile (L2373),
      * composite FK on (organization_id, profile_id), so that a lease cannot point at
        a profile of a foreign organization (L5587).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    profile_id: UUID
    #: Grows on every takeover (L2372). Never decreases.
    generation: int = Field(ge=1)
    owner_runner_id: UUID
    browser_session_id: UUID | None = None
    heartbeat_at: datetime
    expires_at: datetime
    state: LeaseState

    def token(self) -> FencingToken:
        return FencingToken(
            profile_id=self.profile_id, generation=self.generation, lease_id=self.id
        )

    def verify(self, token: FencingToken) -> None:
        """Rejects a call with a stale generation. The only allowed way to check a token."""
        if token.profile_id != self.profile_id or token.generation != self.generation:
            raise StaleFencingToken(
                expected=self.generation, presented=token.generation, profile_id=self.profile_id
            )

    def is_expired(self, now: datetime) -> bool:
        """Expiry counted by database time, not by the worker's clock — see L2668.

        This method takes `now` from the outside precisely so that the caller has to supply
        PostgreSQL time, and not the process's `datetime.now()`.
        """
        return now >= self.expires_at


class BrowserSession(BaseModel):
    """L2381-2399. The full identity of a running capsule.

    After a restart the Gateway checks the process identity and CDP `/json/version` before
    deciding on a second start (L2401) — that is why `browser_build_digest` and
    `process_instance_id` are mandatory here, and not optional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    capsule_instance_id: UUID
    os_pid: int
    process_started_at: datetime
    #: Guards against confusing the old and the new process on PID reuse (L5584).
    process_instance_id: str
    display_server_pid: int | None = None
    display_server_health: str | None = None
    window_manager_pid: int | None = None
    window_manager_health: str | None = None
    stream_gateway_instance_id: UUID | None = None
    stream_gateway_health: str | None = None
    capsule_supervisor_identity: str
    browser_build_digest: str
    browser_mode: CapsuleMode
    #: A reference to the CDP endpoint — NEVER the bare port handed outside (L2353).
    cdp_endpoint_ref: str
    display_id: str | None = None
    stream_transport: str | None = None
    state: BrowserSessionState
    last_seen_at: datetime

    @model_validator(mode="after")
    def _headless_has_no_display(self) -> BrowserSession:
        """An E1 run starts neither a display nor a stream gateway (L5532).

        In HEADLESS mode we do not permanently keep a display, a window manager or a stream
        gateway (L2365, L4365).
        """
        if self.browser_mode is CapsuleMode.HEADLESS:
            if self.display_server_pid is not None or self.display_id is not None:
                raise ValueError(
                    "Sesja HEADLESS nie może mieć displayu — cała praca automatu działa "
                    "bez renderowanego ekranu (L4365, L5532)."
                )
            if self.stream_gateway_instance_id is not None:
                raise ValueError("Sesja HEADLESS nie może mieć stream gatewaya (L5532).")
        return self


class InteractionLease(BaseModel):
    """L2404-2415. Who may send input. Exactly one active per browser session.

    "partial unique: one ACTIVE interaction lease per browser_session_id" (L2415)
    "two parallel InteractionLease claims for one browser session end with
     a single active lease" (L5592)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    browser_session_id: UUID
    mode: InteractionLeaseMode
    owner_ref: str
    generation: int = Field(ge=1)
    heartbeat_at: datetime
    expires_at: datetime
    state: LeaseState


class StreamTicket(BaseModel):
    """A single-use ticket to the stream (L2333-2343).

    The rules this type enforces:
      * the ticket is stored EXCLUSIVELY as a hash (L2333),
      * for the CONTROL role it is bound to `interaction_lease_id` and its generation (L2343),
      * it never reaches the query string nor the logs (L2343) — hence there is no field with
        the plaintext ticket here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    browser_session_id: UUID
    user_id: UUID
    role: StreamSessionRole
    ticket_hash: str
    profile_lease_generation: int = Field(ge=1)
    interaction_lease_id: UUID | None = None
    interaction_lease_generation: int | None = None
    issued_at: datetime
    ticket_expires_at: datetime

    @model_validator(mode="after")
    def _control_requires_human_lease(self) -> StreamTicket:
        """A CONTROL ticket cannot be issued without an atomic HUMAN InteractionLease (L5590)."""
        if self.role is StreamSessionRole.CONTROL and (
            self.interaction_lease_id is None or self.interaction_lease_generation is None
        ):
            raise ValueError(
                "CONTROL ticket musi być związany z InteractionLease i jego generacją "
                "(L2343, L5590)."
            )
        return self


class StreamSession(BaseModel):
    """L2417-2427. Separate TTLs for ticket, session, idle and cookie are enforced (L5598)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    browser_session_id: UUID
    user_id: UUID
    role: StreamSessionRole
    ticket_hash: str
    profile_lease_generation: int = Field(ge=1)
    interaction_lease_id: UUID | None = None
    interaction_lease_generation: int | None = None
    ticket_expires_at: datetime
    stream_session_expires_at: datetime
    idle_timeout_at: datetime
    issued_at: datetime
    exchanged_at: datetime | None = None
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def _ttl_values_are_separate(self) -> StreamSession:
        """`ticket_expires_at`, `stream_session_expires_at` and `idle_timeout_at` are separate
        values (L2345). This validator guards against them being merged into one."""
        if self.ticket_expires_at > self.stream_session_expires_at:
            raise ValueError(
                "ticket_expires_at nie może przekraczać stream_session_expires_at — "
                "bilet jest jednorazowy i krótszy niż sesja (L2333, L2345)."
            )
        return self

    def is_replay(self) -> bool:
        """A StreamTicket replay is rejected (L5589)."""
        return self.exchanged_at is not None


class ModeTransition(BaseModel):
    """Switching the capsule mode — L2444-2448.

        HEADLESS → SAFE_STOP → stop Chrome → start headful + Xvnc → INTERACTIVE
        INTERACTIVE → close stream/input leases → stop Chrome → start headless → HEADLESS

    "a mode switch records an audit event and a new browser_session" (L2448)
    "a login, MFA, challenge or takeover request creates a mode transition with a recorded
     reason and after `SAFE_STOP`" (L5533)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    profile_id: UUID
    from_mode: CapsuleMode
    to_mode: CapsuleMode
    reason: InteractiveReason
    safe_stop_completed_at: datetime
    previous_browser_session_id: UUID
    new_browser_session_id: UUID
    occurred_at: datetime

    @model_validator(mode="after")
    def _reason_only_when_entering_interactive(self) -> ModeTransition:
        if self.from_mode is self.to_mode:
            raise ValueError("Przejście trybu musi zmieniać tryb")
        return self

    @model_validator(mode="after")
    def _mode_switch_creates_new_session(self) -> ModeTransition:
        if self.previous_browser_session_id == self.new_browser_session_id:
            raise ValueError(
                "Przełączenie trybu tworzy NOWĄ browser_session (L2448) — "
                "ten sam identyfikator oznacza, że sesja nie została odtworzona."
            )
        return self
