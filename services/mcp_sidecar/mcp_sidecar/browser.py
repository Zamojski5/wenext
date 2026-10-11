"""Browser backend for the sidecar — a narrow, read-only interface.

The sidecar does NOT drive the browser directly and does not know the CDP port. It talks to
the backend through this protocol; the production implementation belongs to the
BrowserSupervisor (D08) and will be wired in once it exists. Until then the sidecar starts
with `NoBackend`, which ends EVERY tool call with an explicit error — instead of pretending
that it did something.

The interface is deliberately poor. There is no `click`, `fill` or `press` here and they
cannot be added through configuration: the sidecar has no method at all that could call
them (L2521).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class BackendError(Exception):
    """Browser backend did not perform the operation. The sidecar does not guess a result."""


class BrowserBackend(Protocol):
    """The read-only surface the sidecar may use. Nothing more exists."""

    def open_url(self, url: str) -> dict[str, Any]:
        """Navigation to a URL already validated against the origin allowlist."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Structured dump of the page's accessible tree."""
        ...

    def screenshot(self) -> bytes:
        """Screenshot of the current page as PNG."""
        ...

    def wait_for(self, condition: str, timeout_ms: int) -> dict[str, Any]:
        """Waits for a condition described by a selector or by a loading state."""
        ...

    def current_url(self) -> str | None: ...


@dataclass
class NoBackend:
    """The backend that is not there. Every call ends with an explicit error.

    The existence of this class is a decision: the alternative would be returning empty
    snapshots, that is, faking a read. An empty result and a missing backend would then
    look the same, and the eval would measure silence instead of content.
    """

    reason: str = "backend przeglądarki (D08) nie jest podpięty"

    def _error(self) -> BackendError:
        return BackendError(self.reason)

    def open_url(self, url: str) -> dict[str, Any]:
        raise self._error()

    def snapshot(self) -> dict[str, Any]:
        raise self._error()

    def screenshot(self) -> bytes:
        raise self._error()

    def wait_for(self, condition: str, timeout_ms: int) -> dict[str, Any]:
        raise self._error()

    def current_url(self) -> str | None:
        return None


@dataclass
class BackendHttpReadOnly:
    """Test backend: fetches the page over HTTP GET and cannot write anything.

    Used in the fixture site tests. There is no method here that would send a POST,
    so the "tools are read-only" test measures a real impossibility, not a declaration.
    """

    timeout_s: float = 10.0
    _url: str | None = field(default=None, init=False)
    _content: str = field(default="", init=False)

    def open_url(self, url: str) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                self._content = response.read().decode("utf-8", errors="replace")
                self._url = response.geturl()
                status = response.status
        except urllib.error.URLError as exc:
            raise BackendError(f"nie udało się otworzyć {url}: {exc}") from exc
        return {"url": self._url, "status": status, "bytes": len(self._content)}

    def snapshot(self) -> dict[str, Any]:
        if self._url is None:
            raise BackendError("brak otwartej strony — najpierw open_allowed_url")
        import re

        text = re.sub(r"<script.*?</script>", " ", self._content, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"url": self._url, "text": text[:20_000], "html_bytes": len(self._content)}

    def screenshot(self) -> bytes:
        raise BackendError(
            "backend HTTP nie renderuje strony — screenshot wymaga kapsuły z Chromium"
        )

    def wait_for(self, condition: str, timeout_ms: int) -> dict[str, Any]:
        if self._url is None:
            raise BackendError("brak otwartej strony — najpierw open_allowed_url")
        satisfied = condition.lower() in self._content.lower()
        return {"condition": condition, "satisfied": satisfied, "waited_ms": 0}

    def current_url(self) -> str | None:
        return self._url
