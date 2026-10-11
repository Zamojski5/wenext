"""RFB layer aware of the protocol contents — handshake and VIEW_ONLY enforcement.

Two requirements from section 14.5 that this module implements (quoted verbatim from the
Polish source document):

    "`viewOnly` po stronie klienta noVNC nie jest granicą bezpieczeństwa." (L2470)
    "VIEW_ONLY nie może wysłać inputu także po ręcznej próbie ramek RFB, niezależnie
     od ustawienia klienta noVNC" (L5594)

Enforcement has two layers and both layers are needed:

1. **Upstream**: the gateway logs in to Xtigervnc with the view-only password. TigerVNC
   itself then ignores input events. This is the boundary on the VNC server side.
2. **In the gateway**: client→server messages carrying input are rejected before they
   leave the proxy. This is the boundary on the proxy side.

Layer 2 exists because layer 1 depends on a correct Xtigervnc configuration, and layer 1
exists because layer 2 depends on the parser being correct. A failure of one does not open
the way.
"""

from __future__ import annotations

import asyncio
from enum import IntEnum

from community_os_contracts import StreamSessionRole

from .vncauth import CHALLENGE_LENGTH, challenge_response


class ClientMessageType(IntEnum):
    """Client→server messages in RFB 3.8 — plus the extensions noVNC 1.7 and TigerVNC speak.

    Measured 2026-09-04 (first real stream, plan B level 2): with the six base types only, the
    gateway closed the stream right after `ServerInit` — noVNC answers TigerVNC's flow-control
    fences with `ClientFence` (248) and asks for `EnableContinuousUpdates` (150); typing goes
    through `QEMUClientMessage` (255, subtype 0 = extended key event). A splitter that does not know
    a type fails closed, which is right, and it must therefore know every type a real client sends.
    """

    SET_PIXEL_FORMAT = 0
    SET_ENCODINGS = 2
    FRAMEBUFFER_UPDATE_REQUEST = 3
    KEY_EVENT = 4
    POINTER_EVENT = 5
    CLIENT_CUT_TEXT = 6
    ENABLE_CONTINUOUS_UPDATES = 150
    CLIENT_FENCE = 248
    SET_DESKTOP_SIZE = 251
    QEMU_CLIENT_MESSAGE = 255


#: Messages that CARRY INPUT (or change the remote session). For the VIEW_ONLY role they are
#: rejected in the gateway. `CLIENT_CUT_TEXT` is here on purpose — pasting into the remote
#: desktop's clipboard is a state change, not viewing; so are a QEMU extended key event and a
#: desktop resize. Fences and continuous-update requests are protocol plumbing and stay allowed.
INPUT_MESSAGES: frozenset[int] = frozenset(
    {
        ClientMessageType.KEY_EVENT,
        ClientMessageType.POINTER_EVENT,
        ClientMessageType.CLIENT_CUT_TEXT,
        ClientMessageType.SET_DESKTOP_SIZE,
        ClientMessageType.QEMU_CLIENT_MESSAGE,
    }
)

#: Fixed length of client messages of known size (without the type header).
MESSAGE_LENGTHS: dict[int, int] = {
    ClientMessageType.SET_PIXEL_FORMAT: 19,
    ClientMessageType.FRAMEBUFFER_UPDATE_REQUEST: 9,
    ClientMessageType.KEY_EVENT: 7,
    ClientMessageType.POINTER_EVENT: 5,
    # enable flag (1) + x, y, w, h (4 x 2)
    ClientMessageType.ENABLE_CONTINUOUS_UPDATES: 9,
}

#: The one QEMU subtype a browser client sends: extended key event, 12 bytes in total.
QEMU_EXTENDED_KEY_EVENT = 0
QEMU_EXTENDED_KEY_EVENT_LENGTH = 12

#: The most a single `ClientCutText` may declare. A human pasting a login page's worth of text is
#: kilobytes; a megabyte is already generous, and 2 GiB (the signed-int32 range) is a buffer bomb.
MAX_CUT_TEXT_BYTES = 1024 * 1024


class RFBError(Exception):
    """The RFB handshake failed."""


class SecurityType(IntEnum):
    INVALID = 0
    NONE = 1
    VNC_AUTH = 2


async def perform_handshake(
    read: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    password: bytes | None,
) -> bytes:
    """Performs the RFB handshake as a client and returns the server's protocol version.

    `haslo` is supplied by the **gateway**, not by the browser. If the server offers
    `VNC_AUTH` and there is no password — the handshake ends with an error instead of
    silently falling back to `NONE`.
    """
    server_version = await read.readexactly(12)
    if not server_version.startswith(b"RFB "):
        raise RFBError(f"To nie jest serwer RFB: {server_version!r}")

    # We declare 3.8 — the only version this gateway supports.
    writer.write(b"RFB 003.008\n")
    await writer.drain()

    security_type_count = (await read.readexactly(1))[0]
    if security_type_count == 0:
        length = int.from_bytes(await read.readexactly(4), "big")
        reason = (await read.readexactly(length)).decode("utf-8", "replace")
        raise RFBError(f"Serwer odrzucił połączenie: {reason}")

    offered_types = set(await read.readexactly(security_type_count))

    if SecurityType.VNC_AUTH in offered_types:
        if password is None:
            raise RFBError(
                "Serwer wymaga VncAuth, a gateway nie ma poświadczenia dla tej roli. "
                "Fail-closed: nie przechodzimy na SecurityType=None."
            )
        writer.write(bytes([SecurityType.VNC_AUTH]))
        await writer.drain()
        challenge = await read.readexactly(CHALLENGE_LENGTH)
        writer.write(challenge_response(password, challenge))
        await writer.drain()
    elif SecurityType.NONE in offered_types:
        writer.write(bytes([SecurityType.NONE]))
        await writer.drain()
    else:
        raise RFBError(
            f"Serwer nie oferuje obsługiwanego typu bezpieczeństwa: {sorted(offered_types)}"
        )

    auth_result = int.from_bytes(await read.readexactly(4), "big")
    if auth_result != 0:
        length = int.from_bytes(await read.readexactly(4), "big")
        reason = (await read.readexactly(length)).decode("utf-8", "replace")
        raise RFBError(f"Uwierzytelnienie RFB odrzucone: {reason}")

    return server_version


def may_forward(buffer: bytes, role: StreamSessionRole) -> tuple[bool, str]:
    """Whether this client message may be forwarded to the VNC server.

    Returns `(allowed, reason)`. For `CONTROL` everything goes through. For `VIEW_ONLY`
    input messages are rejected — **regardless of what the noVNC client has set**.
    """
    if role is StreamSessionRole.CONTROL:
        return True, ""
    if not buffer:
        return True, ""

    message_type = buffer[0]
    if message_type in INPUT_MESSAGES:
        nazwa = (
            ClientMessageType(message_type).name
            if message_type in list(ClientMessageType)
            else str(message_type)
        )
        return False, (
            f"Rola VIEW_ONLY nie może wysłać komunikatu {nazwa}. "
            "viewOnly po stronie klienta nie jest granicą bezpieczeństwa (L2470, L5594)."
        )
    return True, ""


def split_messages(buffer: bytearray) -> list[bytes]:
    """Extracts complete client messages from the buffer, leaving the remainder.

    Needed because WebSocket does not preserve RFB message boundaries — a single frame may
    contain several messages or a fragment of one. Without this step the VIEW_ONLY filter
    could be bypassed by concatenating an input event with the preceding message.
    """
    messages: list[bytes] = []

    while buffer:
        message_type = buffer[0]

        if message_type in MESSAGE_LENGTHS:
            length = 1 + MESSAGE_LENGTHS[message_type]
            if len(buffer) < length:
                break
            messages.append(bytes(buffer[:length]))
            del buffer[:length]
            continue

        if message_type == ClientMessageType.SET_ENCODINGS:
            if len(buffer) < 4:
                break
            encoding_count = int.from_bytes(buffer[2:4], "big")
            length = 4 + 4 * encoding_count
            if len(buffer) < length:
                break
            messages.append(bytes(buffer[:length]))
            del buffer[:length]
            continue

        if message_type == ClientMessageType.CLIENT_CUT_TEXT:
            if len(buffer) < 8:
                break
            # The length is a SIGNED 32-bit integer: negative means the Extended Clipboard
            # format (TigerVNC + noVNC negotiate it right after ServerInit), and its payload is
            # |length| bytes. Read unsigned, a negative length looked like ~4 GB and the splitter
            # waited for it forever.
            text_length = abs(int.from_bytes(buffer[4:8], "big", signed=True))
            if text_length > MAX_CUT_TEXT_BYTES:
                # A declared length is a promise the gateway would otherwise buffer up to 2 GiB
                # for (review 2026-09-04). A clipboard bigger than this is not a paste, it is a
                # flood; the connection ends instead of the memory.
                raise RFBError(
                    f"ClientCutText deklaruje {text_length} B — ponad limit "
                    f"{MAX_CUT_TEXT_BYTES} B. Gateway zamyka połączenie zamiast buforować."
                )
            length = 8 + text_length
            if len(buffer) < length:
                break
            messages.append(bytes(buffer[:length]))
            del buffer[:length]
            continue

        if message_type == ClientMessageType.CLIENT_FENCE:
            # type (1) + padding (3) + flags (4) + payload length (1) + payload
            if len(buffer) < 9:
                break
            length = 9 + buffer[8]
            if len(buffer) < length:
                break
            messages.append(bytes(buffer[:length]))
            del buffer[:length]
            continue

        if message_type == ClientMessageType.SET_DESKTOP_SIZE:
            # type (1) + padding (1) + width (2) + height (2) + screens (1) + padding (1) + 16 x n
            if len(buffer) < 8:
                break
            length = 8 + 16 * buffer[6]
            if len(buffer) < length:
                break
            messages.append(bytes(buffer[:length]))
            del buffer[:length]
            continue

        if message_type == ClientMessageType.QEMU_CLIENT_MESSAGE:
            if len(buffer) < 2:
                break
            if buffer[1] != QEMU_EXTENDED_KEY_EVENT:
                # Audio and anything else: no browser client we run sends it; fail closed.
                raise RFBError(
                    f"Nieobsługiwany podtyp komunikatu QEMU: {buffer[1]}. Gateway zamyka "
                    "połączenie zamiast zgadywać długość."
                )
            if len(buffer) < QEMU_EXTENDED_KEY_EVENT_LENGTH:
                break
            messages.append(bytes(buffer[:QEMU_EXTENDED_KEY_EVENT_LENGTH]))
            del buffer[:QEMU_EXTENDED_KEY_EVENT_LENGTH]
            continue

        # Unknown message type. Fail-closed: we do not guess the length, because a wrong
        # guess would desynchronise the stream and could let input through as a "remainder".
        raise RFBError(
            f"Nieznany typ komunikatu klienta RFB: {message_type}. Gateway zamyka połączenie "
            "zamiast zgadywać długość."
        )

    return messages
