"""Stream Gateway tests: VncAuth, VIEW_ONLY enforcement and ticket one-time use."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from community_os_contracts import StreamSessionRole
from stream_gateway.rfb import (
    INPUT_MESSAGES,
    ClientMessageType,
    RFBError,
    may_forward,
    split_messages,
)
from stream_gateway.tickets import TicketRegistry, TicketRejected
from stream_gateway.vncauth import challenge_response, des_key

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# VncAuth
# ---------------------------------------------------------------------------


def test_des_key_has_reversed_bits() -> None:
    """A VncAuth property whose absence breaks the handshake silently."""
    # 0b00000001 -> 0b10000000
    assert des_key(b"\x01")[0] == 0x80
    assert des_key(b"\xff")[0] == 0xFF


def test_des_key_is_always_eight_bytes() -> None:
    assert len(des_key(b"")) == 8
    assert len(des_key(b"krotkie")) == 8
    assert len(des_key(b"bardzo-dlugie-haslo-vnc")) == 8


def test_password_longer_than_eight_characters_is_truncated() -> None:
    """Practical consequence: passwords differing only from the 9th character are the same."""
    assert des_key(b"12345678ABC") == des_key(b"12345678XYZ")


def test_response_is_deterministic_and_16_bytes_long() -> None:
    challenge = bytes(range(16))
    a = challenge_response(b"tajne", challenge)
    b = challenge_response(b"tajne", challenge)
    assert a == b
    assert len(a) == 16


def test_different_passwords_give_different_responses() -> None:
    challenge = bytes(range(16))
    assert challenge_response(b"full", challenge) != challenge_response(b"viewonly", challenge)


def test_wrong_challenge_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="Challenge RFB"):
        challenge_response(b"tajne", b"za krotkie")


# ---------------------------------------------------------------------------
# VIEW_ONLY enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message_type", sorted(INPUT_MESSAGES))
def test_view_only_does_not_forward_input(message_type: int) -> None:
    """L5594: VIEW_ONLY cannot send input even after a manual attempt at RFB frames."""
    allowed, reason = may_forward(bytes([message_type, 0, 0, 0]), StreamSessionRole.VIEW_ONLY)
    assert allowed is False
    assert "VIEW_ONLY" in reason


@pytest.mark.parametrize("message_type", sorted(INPUT_MESSAGES))
def test_control_forwards_input(message_type: int) -> None:
    allowed, _ = may_forward(bytes([message_type, 0, 0, 0]), StreamSessionRole.CONTROL)
    assert allowed is True


def test_view_only_forwards_framebuffer_update_request() -> None:
    """A viewer must be able to ask for a frame — that is not input."""
    message = bytes([ClientMessageType.FRAMEBUFFER_UPDATE_REQUEST]) + b"\x00" * 9
    allowed, _ = may_forward(message, StreamSessionRole.VIEW_ONLY)
    assert allowed is True


def test_clipboard_is_treated_as_input() -> None:
    """Pasting into the remote desktop's clipboard changes state, so it is not viewing."""
    allowed, _ = may_forward(
        bytes([ClientMessageType.CLIENT_CUT_TEXT]), StreamSessionRole.VIEW_ONLY
    )
    assert allowed is False


# ---------------------------------------------------------------------------
# Stream parsing — the bypass via concatenating messages
# ---------------------------------------------------------------------------


def test_concatenated_messages_are_split() -> None:
    """Critical for security: WebSocket does not preserve RFB message boundaries.

    If the filter looked only at the first byte of the frame, it would be enough to
    concatenate a harmless message with a keyboard event to push the event through.
    """
    framebuffer_request = bytes([ClientMessageType.FRAMEBUFFER_UPDATE_REQUEST]) + b"\x00" * 9
    key_event = bytes([ClientMessageType.KEY_EVENT]) + b"\x00" * 7
    buffer = bytearray(framebuffer_request + key_event)

    messages = split_messages(buffer)

    assert len(messages) == 2
    assert messages[0][0] == ClientMessageType.FRAMEBUFFER_UPDATE_REQUEST
    assert messages[1][0] == ClientMessageType.KEY_EVENT
    # The second of them will be rejected for VIEW_ONLY.
    assert may_forward(messages[0], StreamSessionRole.VIEW_ONLY)[0] is True
    assert may_forward(messages[1], StreamSessionRole.VIEW_ONLY)[0] is False


def test_incomplete_message_stays_in_the_buffer() -> None:
    buffer = bytearray(bytes([ClientMessageType.KEY_EVENT]) + b"\x00" * 3)
    messages = split_messages(buffer)
    assert messages == []
    assert len(buffer) == 4


def test_set_encodings_has_variable_length() -> None:
    encoding_count = 3
    message = (
        bytes([ClientMessageType.SET_ENCODINGS, 0])
        + encoding_count.to_bytes(2, "big")
        + b"\x00" * 12
    )
    buffer = bytearray(message)
    assert len(split_messages(buffer)) == 1
    assert buffer == bytearray()


def test_unknown_message_type_closes_the_connection() -> None:
    """Fail-closed: guessing the length could let input through as a 'remainder'."""
    with pytest.raises(RFBError, match="Nieznany typ"):
        split_messages(bytearray(b"\x99\x00\x00"))


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


def _registry() -> tuple[TicketRegistry, dict]:
    return TicketRegistry(), {
        "browser_session_id": uuid4(),
        "user_id": uuid4(),
        "profile_lease_generation": 3,
    }


def test_view_only_ticket_can_be_exchanged_once() -> None:
    registry, shared = _registry()
    ticket, _ = registry.issue(role=StreamSessionRole.VIEW_ONLY, teraz=NOW, **shared)

    record = registry.exchange(ticket, teraz=NOW, current_profile_lease_generation=3)
    assert record.role is StreamSessionRole.VIEW_ONLY

    with pytest.raises(TicketRejected, match="Replay"):
        registry.exchange(ticket, teraz=NOW, current_profile_lease_generation=3)


def test_control_ticket_is_not_issued_without_interaction_lease() -> None:
    """L5590."""
    registry, shared = _registry()
    with pytest.raises(TicketRejected, match="HUMAN InteractionLease"):
        registry.issue(role=StreamSessionRole.CONTROL, teraz=NOW, **shared)


def test_control_ticket_with_interaction_lease_works() -> None:
    registry, shared = _registry()
    ticket, _ = registry.issue(
        role=StreamSessionRole.CONTROL,
        teraz=NOW,
        interaction_lease_id=uuid4(),
        interaction_lease_generation=7,
        **shared,
    )
    record = registry.exchange(
        ticket,
        teraz=NOW,
        current_profile_lease_generation=3,
        current_interaction_lease_generation=7,
    )
    assert record.role is StreamSessionRole.CONTROL


def test_interaction_generation_change_invalidates_the_ticket() -> None:
    """Losing the lease takes away the right to input (L2343)."""
    registry, shared = _registry()
    ticket, _ = registry.issue(
        role=StreamSessionRole.CONTROL,
        teraz=NOW,
        interaction_lease_id=uuid4(),
        interaction_lease_generation=7,
        **shared,
    )
    with pytest.raises(TicketRejected, match="InteractionLease"):
        registry.exchange(
            ticket,
            teraz=NOW,
            current_profile_lease_generation=3,
            current_interaction_lease_generation=8,
        )


def test_profile_generation_change_invalidates_the_ticket() -> None:
    registry, shared = _registry()
    ticket, _ = registry.issue(role=StreamSessionRole.VIEW_ONLY, teraz=NOW, **shared)
    with pytest.raises(TicketRejected, match="ProfileLease"):
        registry.exchange(ticket, teraz=NOW, current_profile_lease_generation=4)


def test_expired_ticket_is_rejected() -> None:
    registry, shared = _registry()
    ticket, record = registry.issue(role=StreamSessionRole.VIEW_ONLY, teraz=NOW, **shared)
    late = record.ticket_expires_at + timedelta(seconds=1)
    with pytest.raises(TicketRejected, match="wygasł"):
        registry.exchange(ticket, teraz=late, current_profile_lease_generation=3)


def test_ticket_is_not_stored_in_plaintext() -> None:
    """L2333: the ticket is stored only as a hash."""
    registry, shared = _registry()
    ticket, record = registry.issue(role=StreamSessionRole.VIEW_ONLY, teraz=NOW, **shared)

    assert ticket not in record.ticket_hash
    assert len(record.ticket_hash) == 64
    # The whole registry state does not contain the plaintext ticket.
    assert ticket not in repr(registry.__dict__)


def test_unknown_ticket_is_rejected() -> None:
    registry, _ = _registry()
    with pytest.raises(TicketRejected):
        registry.exchange("wymyslony", teraz=NOW, current_profile_lease_generation=3)
