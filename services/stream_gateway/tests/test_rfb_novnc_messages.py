"""Client messages a REAL browser client sends — the splitter must know every one of them.

Measured 2026-09-04 on the first real stream (plan B level 2): after `ServerInit` the gateway
closed the WebSocket, and noVNC reported a clean disconnect. The cause was the splitter's
vocabulary — six RFB 3.8 base types — meeting TigerVNC + noVNC 1.7: `ClientFence` (248) in reply to
the server's flow-control fences, `EnableContinuousUpdates` (150), the Extended Clipboard form of
`ClientCutText` with a NEGATIVE length, and `QEMUClientMessage` (255) for typing. Fail-closed on an
unknown type is the right rule; these tests are what makes the known set complete.
"""

from __future__ import annotations

import pytest
from community_os_contracts import StreamSessionRole
from stream_gateway.rfb import ClientMessageType, RFBError, may_forward, split_messages


def _fence(payload: bytes = b"") -> bytes:
    return bytes([248, 0, 0, 0]) + (0).to_bytes(4, "big") + bytes([len(payload)]) + payload


def _continuous_updates(enable: int = 1) -> bytes:
    return (
        bytes([150, enable])
        + (0).to_bytes(2, "big") * 2
        + (1280).to_bytes(2, "big")
        + (900).to_bytes(2, "big")
    )


def _extended_cut_text(payload: bytes) -> bytes:
    return bytes([6, 0, 0, 0]) + (-len(payload)).to_bytes(4, "big", signed=True) + payload


def _qemu_key(down: int = 1, keysym: int = 0x61, keycode: int = 0x1E) -> bytes:
    return (
        bytes([255, 0])
        + down.to_bytes(2, "big")
        + keysym.to_bytes(4, "big")
        + keycode.to_bytes(4, "big")
    )


def _set_desktop_size(screens: int = 1) -> bytes:
    return (
        bytes([251, 0])
        + (1280).to_bytes(2, "big")
        + (900).to_bytes(2, "big")
        + bytes([screens, 0])
        + b"\x00" * (16 * screens)
    )


def test_a_fence_and_continuous_updates_are_split_and_forwarded_for_view_only() -> None:
    buffer = bytearray(_fence(b"abc") + _continuous_updates())
    messages = split_messages(buffer)
    assert [m[0] for m in messages] == [248, 150] and buffer == b""
    for message in messages:
        assert may_forward(message, StreamSessionRole.VIEW_ONLY) == (True, "")


def test_extended_clipboard_negative_length_is_a_complete_message_not_a_wait_forever() -> None:
    payload = b"\x00\x00\x00\x1f\x00\x00\x00\x03"  # caps message: flags + one length
    buffer = bytearray(_extended_cut_text(payload) + _fence())
    messages = split_messages(buffer)
    assert len(messages) == 2 and messages[0] == _extended_cut_text(payload)
    assert buffer == b""


def test_a_qemu_extended_key_event_is_twelve_bytes_and_counts_as_input() -> None:
    buffer = bytearray(_qemu_key() + _fence())
    messages = split_messages(buffer)
    assert len(messages[0]) == 12 and messages[0][0] == 255
    allowed, reason = may_forward(messages[0], StreamSessionRole.VIEW_ONLY)
    assert allowed is False and "VIEW_ONLY" in reason
    assert may_forward(messages[0], StreamSessionRole.CONTROL) == (True, "")


def test_an_unknown_qemu_subtype_fails_closed() -> None:
    with pytest.raises(RFBError, match="QEMU"):
        split_messages(bytearray(bytes([255, 1]) + b"\x00" * 10))


def test_set_desktop_size_is_split_by_its_screen_count_and_is_input() -> None:
    buffer = bytearray(_set_desktop_size(2) + _continuous_updates(0))
    messages = split_messages(buffer)
    assert len(messages[0]) == 8 + 32 and len(messages) == 2
    assert may_forward(messages[0], StreamSessionRole.VIEW_ONLY)[0] is False


def test_partial_messages_wait_for_the_rest() -> None:
    fence = _fence(b"xyz")
    buffer = bytearray(fence[:5])
    assert split_messages(buffer) == [] and len(buffer) == 5
    buffer.extend(fence[5:])
    assert split_messages(buffer) == [fence]


def test_the_vocabulary_is_exactly_what_novnc_and_tigervnc_use() -> None:
    assert {int(t) for t in ClientMessageType} == {0, 2, 3, 4, 5, 6, 150, 248, 251, 255}
    with pytest.raises(RFBError, match="Nieznany typ"):
        split_messages(bytearray(bytes([200]) + b"\x00" * 20))


def test_a_cut_text_that_declares_more_than_the_cap_ends_the_connection() -> None:
    """Review 2026-09-04, item 2. The signed length is read as `abs()`, which is right for the
    Extended Clipboard form — but it also means a client can DECLARE 2 GiB and the splitter would
    wait for it, holding every byte it gets. Above `MAX_CUT_TEXT_BYTES` the answer is `RFBError`
    (the stream closes with 4400), not a growing buffer.

    Both signs are tried, because the cap must sit after the `abs()`, not before it.
    """
    from stream_gateway.rfb import MAX_CUT_TEXT_BYTES

    assert MAX_CUT_TEXT_BYTES == 1024 * 1024

    for declared in (MAX_CUT_TEXT_BYTES + 1, -(MAX_CUT_TEXT_BYTES + 1), 2**31 - 1, -(2**31)):
        header = bytes([ClientMessageType.CLIENT_CUT_TEXT, 0, 0, 0])
        buffer = bytearray(header + declared.to_bytes(4, "big", signed=True) + b"x" * 16)
        with pytest.raises(RFBError, match="ponad limit"):
            split_messages(buffer)

    # The control case: exactly the cap is still a clipboard, and it waits for its payload.
    at_cap = bytearray(
        bytes([ClientMessageType.CLIENT_CUT_TEXT, 0, 0, 0])
        + MAX_CUT_TEXT_BYTES.to_bytes(4, "big", signed=True)
        + b"x" * 16
    )
    assert split_messages(at_cap) == []
    assert len(at_cap) == 8 + 16, "niekompletny komunikat zostaje w buforze"
