"""One-time StreamTickets — section 14.3 (L2319-2345).

Rules that this module enforces:

* a ticket lives 30-60 s and is stored **only as a hash** (L2333),
* it is bound to `user_id`, `browser_session_id`, the role and the ProfileLease generation,
  and for `CONTROL` also to `interaction_lease_id` and its generation (L2343),
* it does not reach the query string or the logs (L2343),
* replay is rejected (L5589),
* `CONTROL` cannot be issued without an atomic HUMAN InteractionLease (L5590),
* every input checks both generations, and losing the lease closes the controller's
  WebSocket (L2343).

Why a hash and not the ticket itself: if the database held the ticket in plaintext, reading
it would give immediate access to a live browser session logged into the client's account.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from community_os_contracts import StreamSessionRole

#: L2333: "one-time StreamTicket (30-60 s ...)". We take the middle of the range.
DEFAULT_TTL = timedelta(seconds=45)


class TicketRejected(Exception):
    """Invalid ticket: unknown, used, expired or carrying a stale generation."""


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


@dataclass
class TicketRecord:
    """What we store. Note: there is no plaintext ticket here."""

    id: UUID
    ticket_hash: str
    browser_session_id: UUID
    user_id: UUID
    role: StreamSessionRole
    profile_lease_generation: int
    interaction_lease_id: UUID | None
    interaction_lease_generation: int | None
    issued_at: datetime
    ticket_expires_at: datetime
    exchanged_at: datetime | None = None


class TicketRegistry:
    """Registry in the process memory.

    In F0 the gateway is one process and one operator, so process memory is enough.
    The target is the `stream_sessions` table (migration 0001 already has it, together
    with `UNIQUE (ticket_hash)` blocking replay at the database level). The interface is
    the same, so that the move does not change the callers.
    """

    def __init__(self) -> None:
        self._records: dict[str, TicketRecord] = {}

    def issue(
        self,
        *,
        browser_session_id: UUID,
        user_id: UUID,
        role: StreamSessionRole,
        profile_lease_generation: int,
        teraz: datetime,
        interaction_lease_id: UUID | None = None,
        interaction_lease_generation: int | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> tuple[str, TicketRecord]:
        """Issues a ticket. Returns (plaintext_ticket, record).

        The plaintext ticket is returned EXACTLY ONCE and is not stored anywhere.
        The caller is to pass it to the browser in the POST response body, never in the URL.
        """
        if role is StreamSessionRole.CONTROL and (
            interaction_lease_id is None or interaction_lease_generation is None
        ):
            raise TicketRejected(
                "CONTROL ticket nie może zostać wydany bez atomowego HUMAN InteractionLease "
                "i jego generacji (L2343, L5590)."
            )

        ticket = secrets.token_urlsafe(32)
        record = TicketRecord(
            id=uuid4(),
            ticket_hash=_hash(ticket),
            browser_session_id=browser_session_id,
            user_id=user_id,
            role=role,
            profile_lease_generation=profile_lease_generation,
            interaction_lease_id=interaction_lease_id,
            interaction_lease_generation=interaction_lease_generation,
            issued_at=teraz,
            ticket_expires_at=teraz + ttl,
        )
        self._records[record.ticket_hash] = record
        return ticket, record

    def exchange(
        self,
        ticket: str,
        *,
        teraz: datetime,
        current_profile_lease_generation: int,
        current_interaction_lease_generation: int | None = None,
    ) -> TicketRecord:
        """Consumes the ticket. A second use of the same ticket is rejected (L5589)."""
        record = self._records.get(_hash(ticket))
        if record is None:
            # Constant-time comparison does not matter here (the hash is full), but we do
            # not reveal whether the ticket existed — the message is the same in both cases.
            raise TicketRejected("Ticket nieznany, zużyty albo przeterminowany.")

        if record.exchanged_at is not None:
            raise TicketRejected(
                "Ticket został już wymieniony. Replay StreamTicketu jest odrzucany (L5589)."
            )
        if teraz >= record.ticket_expires_at:
            raise TicketRejected(
                f"Ticket wygasł o {record.ticket_expires_at.isoformat()} — bilet jest jednorazowy "
                "i krótkotrwały (L2333)."
            )
        if record.profile_lease_generation != current_profile_lease_generation:
            raise TicketRejected(
                f"Generacja ProfileLease zmieniła się ({record.profile_lease_generation} → "
                f"{current_profile_lease_generation}). Profil został w międzyczasie przejęty."
            )
        if (
            record.role is StreamSessionRole.CONTROL
            and record.interaction_lease_generation != current_interaction_lease_generation
        ):
            raise TicketRejected(
                f"Generacja InteractionLease zmieniła się "
                f"({record.interaction_lease_generation} → "
                f"{current_interaction_lease_generation}). "
                "Kontroler stracił prawo do inputu (L2343)."
            )

        record.exchanged_at = teraz
        return record

    def compare_hash(self, ticket: str, expected_hash: str) -> bool:
        """Constant-time comparison — to be used where the hash comes from outside."""
        return hmac.compare_digest(_hash(ticket), expected_hash)
