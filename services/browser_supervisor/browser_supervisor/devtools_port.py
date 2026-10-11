"""Safe handling of the `DevToolsActivePort` file.

The E1.F0 scope requires it explicitly: "safe handling of a stale `DevToolsActivePort`" (L5159).

Why this is a real problem, not a theoretical one:

    Measured 2026-08-30 in an arm64 container. After `kill -9` of the Chrome process the
    `DevToolsActivePort` file **stays in the profile directory** and still contains the port
    number and the WebSocket path. The port is dead by then. A Gateway that trusted the mere
    existence of the file would try to attach to a browser that does not exist — or, worse, to
    SOMEONE ELSE'S process that has taken over that port number in the meantime.

File format (two lines):

    43823
    /devtools/browser/0bbae64d-dd8f-4dbf-a98b-0a50097eed25

The verification we apply has three stages and all three have to pass:

1. the port answers on `/json/version`,
2. the `Browser` field matches the pinned build — otherwise it is not our browser,
3. `webSocketDebuggerUrl` contains **exactly the same identifier** as the file's second line.

Point 1 alone is not enough: once the port number has been reused by another process the
answer will come, but it will come from a different browser. Point 3 rules that out.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PORT_FILE_NAME = "DevToolsActivePort"


class StaleDevToolsPort(Exception):
    """The file exists, but it does not describe a live browser of ours."""


class MissingDevToolsPort(Exception):
    """The file did not appear within the given time."""


@dataclass(frozen=True)
class DevToolsEndpoint:
    """A verified CDP endpoint."""

    port: int
    browser_path: str
    host: str = "127.0.0.1"

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.browser_path}"


def port_file_path(user_data_dir: Path) -> Path:
    return user_data_dir / PORT_FILE_NAME


def remove_if_exists(user_data_dir: Path) -> bool:
    """Deletes the file before start. Returns True if something was deleted.

    Called ONLY by the lease owner, right before launching the process.
    Thanks to that, the file we see after start certainly comes from the new process,
    and not from the previous, killed one.
    """
    file = port_file_path(user_data_dir)
    if file.exists():
        file.unlink()
        return True
    return False


def read_raw(user_data_dir: Path) -> tuple[int, str]:
    """Reads the file without any verification. Do not use it directly for attaching."""
    file = port_file_path(user_data_dir)
    lines = file.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise StaleDevToolsPort(
            f"{file} ma {len(lines)} linii zamiast dwóch — plik jest niekompletny "
            "(prawdopodobnie przerwany zapis przy crashu)."
        )
    try:
        port = int(lines[0].strip())
    except ValueError as exc:
        raise StaleDevToolsPort(
            f"{file}: pierwsza linia nie jest numerem portu: {lines[0]!r}"
        ) from exc
    return port, lines[1].strip()


def _fetch_json_version(host: str, port: int, timeout: float) -> dict:
    url = f"http://{host}:{port}/json/version"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # loopback only
        return json.load(response)


def verify(
    user_data_dir: Path,
    *,
    expected_browser: str,
    host: str = "127.0.0.1",
    timeout: float = 5.0,
) -> DevToolsEndpoint:
    """Returns a verified endpoint or raises `StaleDevToolsPort`.

    `expected_browser` is the value of the `Browser` field from `/json/version`, e.g.
    `Chrome/153.0.8010.12`. It comes from `versions.lock.toml` — this is how the requirement
    "rejecting a mismatched build" (L5159) is implemented.
    """
    file = port_file_path(user_data_dir)
    if not file.exists():
        raise MissingDevToolsPort(f"Brak {file}")

    port, browser_path = read_raw(user_data_dir)

    try:
        data = _fetch_json_version(host, port, timeout)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise StaleDevToolsPort(
            f"{file} wskazuje port {port}, który nie odpowiada na /json/version ({exc}). "
            "To jest klasyczny stale port po kill -9 — plik zostaje, proces nie żyje."
        ) from exc
    except json.JSONDecodeError as exc:
        raise StaleDevToolsPort(
            f"Port {port} odpowiedział, ale nie JSON-em CDP — to nie jest nasza przeglądarka."
        ) from exc

    actual_browser = data.get("Browser", "")
    if actual_browser != expected_browser:
        raise StaleDevToolsPort(
            f"Niezgodny build przeglądarki na porcie {port}: oczekiwano {expected_browser!r}, "
            f"jest {actual_browser!r}. Kapsuła odmawia użycia — mieszanie buildów na jednym "
            "profilu jest zakazane (L2308)."
        )

    ws = data.get("webSocketDebuggerUrl", "")
    if browser_path not in ws:
        raise StaleDevToolsPort(
            f"Port {port} odpowiada, ale identyfikator przeglądarki się nie zgadza: "
            f"plik mówi {browser_path!r}, /json/version mówi {ws!r}. "
            "Numer portu został prawdopodobnie przejęty przez inny proces."
        )

    return DevToolsEndpoint(port=port, browser_path=browser_path, host=host)


def wait_for_endpoint(
    user_data_dir: Path,
    *,
    expected_browser: str,
    host: str = "127.0.0.1",
    timeout_s: float = 60.0,
    interval_s: float = 0.25,
) -> DevToolsEndpoint:
    """Waits until a freshly launched process writes and exposes the endpoint.

    Measured: a cold start of Chrome for Testing in an arm64 container can take a dozen or so
    seconds, so the default limit is generous. A shorter limit gives false alarms on slow I/O.
    """
    end_pos = time.monotonic() + timeout_s
    last_error: Exception | None = None

    while time.monotonic() < end_pos:
        try:
            return verify(user_data_dir, expected_browser=expected_browser, host=host, timeout=2.0)
        except (MissingDevToolsPort, StaleDevToolsPort) as exc:
            # While the start is still running, the file may not exist or be incomplete yet.
            last_error = exc
            time.sleep(interval_s)

    raise MissingDevToolsPort(
        f"Endpoint CDP nie pojawił się w ciągu {timeout_s:.0f} s. Ostatni błąd: {last_error}"
    )


def close_restored_pages(
    endpoint: DevToolsEndpoint,
    *,
    timeout: float = 3.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> int:
    """Close every page but one right after a launch; returns how many were closed.

    Measured 27.09: the X capsule's Chrome held ten `about:blank` tabs after twenty-seven minutes.
    Every read closes the tab it opened; the tabs came from RESTARTS, each launch bringing back the
    previous session's tabs and adding its own start page. Over a pilot of days that is memory
    growing without end. Nothing reads a page the browser brought back by itself, so right after a
    launch one page stays and the rest go. The DevTools HTTP interface on the loopback, the same
    one `verify` reads; no platform request, no page input. A failure is not fatal: the capsule
    works with extra tabs, it only should not keep collecting them.
    """
    try:
        # The loopback only, like `verify`.
        with opener(f"{endpoint.http_base}/json/list", timeout=timeout) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return 0
    pages = [one for one in targets if isinstance(one, dict) and one.get("type") == "page"]
    closed = 0
    for page in pages[1:]:
        target_id = str(page.get("id") or "")
        if not target_id:
            continue
        try:
            with opener(f"{endpoint.http_base}/json/close/{target_id}", timeout=timeout):
                closed += 1
        except (urllib.error.URLError, OSError, TimeoutError):
            continue
    return closed
