"""The browser's side of the RFB handshake — replayed by the gateway (measured gap, 2026-09-04).

After `perform_handshake` authenticated the gateway to Xtigervnc, the first real stream showed both
ends waiting: the server for `ClientInit`, noVNC for `RFB 003.008\\n`. `_browser_handshake` speaks
noVNC's side with SecurityType=None — the password never crosses the WebSocket — and hands the
`ClientInit` byte back so the caller can forward it. A fake WebSocket records exactly what the
browser would see. Driven with `asyncio.run`: this package's test environment has no asyncio
pytest plugin (the `asyncio_mode` warning in its output is the proof), and a coroutine test that is
collected but never awaited passes for nothing.
"""

from __future__ import annotations

import asyncio

import pytest
from stream_gateway.app import BrowserHandshakeFailed, _browser_handshake


class FakeWebSocket:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = list(frames)
        self.sent: list[bytes] = []

    async def receive_bytes(self) -> bytes:
        if not self.frames:
            raise AssertionError("przeglądarka nie ma już nic do wysłania, a gateway czeka")
        return self.frames.pop(0)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


def _run(ws: FakeWebSocket) -> bytes:
    return asyncio.run(_browser_handshake(ws))


def test_the_browser_sees_version_none_and_ok_and_the_client_init_comes_back() -> None:
    ws = FakeWebSocket([b"RFB 003.008\n", b"\x01", b"\x01"])
    client_init = _run(ws)
    assert ws.sent == [b"RFB 003.008\n", b"\x01\x01", b"\x00\x00\x00\x00"], (
        "wersja, jeden typ bezpieczeństwa (NONE), SecurityResult OK — i ani bajtu hasła"
    )
    assert client_init == b"\x01", "ClientInit (shared=1) wraca do wołającego dla Xtigervnc"


def test_fragmented_and_coalesced_frames_are_both_fine() -> None:
    coalesced = FakeWebSocket([b"RFB 003.008\n\x01\x00\x02\x03"])
    assert _run(coalesced) == b"\x00\x02\x03", (
        "ClientInit=0 plus bajty wysłane od razu po nim wracają razem"
    )
    fragmented = FakeWebSocket([b"RFB 0", b"03.008\n", b"", b"\x01", b"\x01"])
    assert _run(fragmented) == b"\x01"


def test_a_client_that_does_not_speak_rfb_is_refused() -> None:
    ws = FakeWebSocket([b"GET / HTTP/1.1\r"])
    with pytest.raises(BrowserHandshakeFailed, match="nie mówi RFB"):
        _run(ws)
    assert ws.sent == [b"RFB 003.008\n"], "odmowa zapada po wersji, przed ofertą typów"


def test_a_client_that_insists_on_vnc_auth_is_refused() -> None:
    ws = FakeWebSocket([b"RFB 003.008\n", b"\x02"])
    with pytest.raises(BrowserHandshakeFailed, match="nie NONE"):
        _run(ws)
    assert b"\x00\x00\x00\x00" not in ws.sent, "bez SecurityResult OK dla złego wyboru"


def test_the_gateway_test_environment_would_have_swallowed_an_async_test() -> None:
    """Why `_run` exists: an `async def test_…` here is collected and reported as a failure by
    pytest's async guard — not silently green, but not a measurement either."""
    assert not asyncio.iscoroutinefunction(test_a_client_that_insists_on_vnc_auth_is_refused)
