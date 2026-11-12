"""The capsule's watchdog: a browser that stops answering is replaced, not waited for.

WHY IT EXISTS. On 12 and 13.10.2026 the X capsule hung twice, once for four hours, until a person
restarted it (the cause, a full stderr pipe, is in `drain.py`). Docker saw it: the healthcheck
failed 1907 times in a row. And did nothing, because `restart: unless-stopped` reacts to a
process that EXITS, never to a container that is merely "unhealthy". The service loop of
`main.py` already ends the capsule when Chrome dies (exit code 1, compose brings it back); it had
no way to notice a Chrome that is alive and answers nothing. This module is that way.

TWO SIGNS, EITHER ONE IS ENOUGH:

  * the DevTools endpoint does not answer `/json/version` for `max_failures` checks in a row. One
    failed check is a busy browser (a heavy page, a loaded host); `max_failures` of them, spaced
    `interval_s` apart, is a hung one. A launch for a person has no endpoint on purpose
    (`args.HUMAN_ARGS`), so there is nothing to ask and the watchdog stays out of it;
  * one request has been in the reader for longer than `handler_stall_s`. The reader serves one
    request at a time, and every request has a deadline well under a minute (navigation 20 s, an
    act 45 s); a request older than five minutes is stuck in a call that will not return, and every
    caller queued behind it waits with it. This catches the hang that leaves the endpoint alive.

WHAT IT DOES ABOUT IT (`main.heal_and_exit`): kills the browser (that alone unblocks a reader thread
waiting for it), tidies what can be tidied within a deadline and EXITS the process with a non-zero
code, so compose starts the capsule again on the same profile; the sign-in in the profile survives,
as it did through every manual restart. It does not try to repair the browser in place: a Chromium
that has stopped answering has no state worth saving, and a second thread driving Playwright
against the first one's stuck call is the way to make one hang into two.

PURE. `HangWatchdog` takes its probe and its clock as arguments, so the decision is tested without
a browser, a network or a sleep.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

#: Seconds between two checks.
CHECK_INTERVAL_S = 10.0
#: Failed checks in a row that make a hang. 4 x 10 s: a heavy page does not hold Chromium's
#: DevTools handler for that long, a hung browser holds it for ever.
MAX_FAILURES = 4
#: A request this old in the reader is stuck (see the module docstring).
HANDLER_STALL_S = 300.0
#: How long one `/json/version` may take. Short: the hung browser never answers, and the check
#: runs on the service loop, which has a stop signal to watch.
PROBE_TIMEOUT_S = 3.0
#: The exit code of a capsule that ended itself because its browser hung. Not 1 (Chrome died) and
#: not 2 (configuration), so the log says which of the three it was.
EXIT_CODE_HUNG = 70


def cdp_answers(http_base: str, *, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Does the DevTools endpoint answer `/json/version` within `timeout`? Loopback only."""
    try:
        with urllib.request.urlopen(f"{http_base}/json/version", timeout=timeout) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


class HangWatchdog:
    """Decides, one `poll` at a time, whether the capsule's browser has hung."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        interval_s: float = CHECK_INTERVAL_S,
        max_failures: int = MAX_FAILURES,
        handler_stall_s: float = HANDLER_STALL_S,
    ) -> None:
        self._clock = clock
        self._interval = interval_s
        self._max_failures = max_failures
        self._stall = handler_stall_s
        self._failures = 0
        self._next_at: float | None = None
        self._launch: str | None = None

    @property
    def failures(self) -> int:
        return self._failures

    def reset(self) -> None:
        """Forget what was seen: a new launch of the browser starts with a clean sheet."""
        self._failures = 0
        self._next_at = None

    def poll(
        self,
        *,
        launch: str,
        probe: Callable[[], bool] | None,
        busy_for: Callable[[], float | None],
    ) -> str | None:
        """The reason to recover the capsule, or `None`.

        `launch` identifies the browser launch being watched; a different one resets the count.
        `probe` is `None` while there is nothing to ask (a person is at the screen). `busy_for`
        returns how long the reader's current request has been running, or `None` when idle.
        Cheap between checks: it returns `None` until `interval_s` has passed.
        """
        now = self._clock()
        if launch != self._launch:
            self._launch = launch
            self.reset()
        busy = busy_for()
        if busy is not None and busy > self._stall:
            return (
                f"żądanie w czytniku trwa już {busy:.0f} s (limit {self._stall:.0f} s): wisi w "
                "wywołaniu, które nie wraca, a wszyscy za nim czekają"
            )
        if probe is None:
            self._failures = 0
            return None
        if self._next_at is not None and now < self._next_at:
            return None
        self._next_at = now + self._interval
        if probe():
            self._failures = 0
            return None
        self._failures += 1
        if self._failures >= self._max_failures:
            return (
                f"przeglądarka nie odpowiada na /json/version od {self._failures} sprawdzeń z "
                f"rzędu (co {self._interval:.0f} s): wisi, a nie pada"
            )
        return None

    def poll_running(
        self,
        running: Any,
        *,
        switching: bool,
        busy_for: Callable[[], float | None],
        answers: Callable[[str], bool] = cdp_answers,
    ) -> str | None:
        """`poll` for the launch the service loop is looking at (`RunningCapsule`).

        Nothing is asked of a launch for a person (no endpoint by design) nor while the mode is
        being switched (the old browser is going away and the new one is not there yet).
        """
        endpoint = running.endpoint
        asked = None if endpoint is None or switching else (lambda: answers(endpoint.http_base))
        return self.poll(launch=running.process_instance_id, probe=asked, busy_for=busy_for)
