"""Reading the stderr of the processes the supervisor starts, so that they never block on it.

THE INCIDENT (12 and 13.10.2026, capsule-x). The supervisor started Chromium and Xtigervnc with
`stderr=subprocess.PIPE` and read that pipe in exactly one place: the failure path of a START
(`communicate()` after a kill). After a successful start nobody read it. A pipe holds 64 KiB; the
Chromium of the X capsule writes about 7 KiB an hour (X's pages are chatty), so after nine to ten
hours the buffer was full and the next `write(2, ...)` of the browser process blocked FOR EVER.
Measured on the stuck container: the main thread of Chromium in `pipe_write`, syscall 64 (`write`)
on fd 2, 64 420 of 65 536 bytes in the pipe, its read end held by the supervisor and never read.
A browser whose main thread is blocked answers nothing: `/json/version` times out, every Playwright
call waits for a reply that does not come, the reader's single HTTP thread sits in one of those
calls, the accept queue of the reader fills, and Docker reports "unhealthy" without restarting
(a restart policy reacts to an exit, not to an unhealthy status). Two such hangs in 24 hours, the
first one lasting four hours until a manual restart.

THE FIX IS TO READ THE PIPE FOR AS LONG AS THE PROCESS LIVES. A daemon thread reads to end of
file and keeps only the last `TAIL_BYTES`, so the memory is bounded and the tail is still there for
the sentence that explains a failed start or a Chromium that died (`CapsuleStartFailed`, the main
loop's "Chrome zakończył się"). Dropping the output into `DEVNULL` would also have fixed the hang,
and would have thrown away the one diagnostic a crashed browser leaves.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

#: How much of the end of the output is kept. Enough for the stack of a crash, small enough that
#: a capsule which runs for months holds the same few kilobytes.
TAIL_BYTES = 16 * 1024
#: One read. `read1` returns what is there (at most this) instead of waiting for it to fill.
CHUNK_BYTES = 64 * 1024


class StderrTail:
    """Drains one stream on a daemon thread and keeps its last `TAIL_BYTES`."""

    def __init__(self, stream: Any, *, name: str, tail_bytes: int = TAIL_BYTES) -> None:
        self._stream = stream
        self._limit = tail_bytes
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.name = name
        #: Everything that ever went through, not only what was kept.
        self.bytes_seen = 0
        self._thread = threading.Thread(target=self._run, name=f"wenext-drain-{name}", daemon=True)
        self._thread.start()

    @classmethod
    def attach(cls, process: Any, *, name: str) -> StderrTail | None:
        """A drain for `process.stderr`, or `None` when the process has no such pipe (it was
        started with `DEVNULL`, or it is a test double)."""
        stream = getattr(process, "stderr", None)
        if stream is None:
            return None
        return cls(stream, name=name)

    def _run(self) -> None:
        stream = self._stream
        read = getattr(stream, "read1", None) or stream.read
        while True:
            try:
                chunk = read(CHUNK_BYTES)
            except (OSError, ValueError):  # the stream was closed under us
                return
            if not chunk:
                return
            with self._lock:
                self.bytes_seen += len(chunk)
                self._buffer += chunk
                if len(self._buffer) > self._limit:
                    del self._buffer[: len(self._buffer) - self._limit]

    def tail_bytes(self, limit: int | None = None) -> bytes:
        with self._lock:
            data = bytes(self._buffer)
        return data if limit is None else data[-limit:]

    def tail(self, limit: int = 800) -> str:
        """The last `limit` bytes as text, for a sentence about a failure."""
        return self.tail_bytes(limit).decode("utf-8", "replace")

    def join(self, timeout: float = 2.0) -> None:
        """Wait for the end of the stream (the process and its children are gone)."""
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


def stderr_after_exit(process: Any, tail: StderrTail | None, *, timeout: float = 5.0) -> bytes:
    """What a process that is finished (or has just been killed) wrote to stderr.

    With a drain the pipe is already being read, so a `communicate()` would race it for the same
    bytes; the drain's tail is the answer. Without one (a process started elsewhere, a test double)
    `communicate()` is the old way and still right.
    """
    if tail is not None:
        with contextlib.suppress(Exception):
            process.wait(timeout=timeout)
        tail.join(2.0)
        return tail.tail_bytes()
    try:
        return bytes(process.communicate(timeout=timeout)[1] or b"")
    except Exception:
        return b""
