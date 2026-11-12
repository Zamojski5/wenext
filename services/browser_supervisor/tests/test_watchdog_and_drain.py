"""A capsule whose browser hangs ends itself and is started again (12 and 13.10.2026, capsule-x).

THE CHAIN, each link held below:

  1. Chromium writes its log to a stderr pipe the supervisor never read after a successful start.
     64 KiB later its main thread blocks in `write(2)` for ever. `StderrTail` reads the pipe for as
     long as the process lives (and the first test shows, with a real child process, what happens
     when nobody does);
  2. a hung browser answers nothing, the reader's single thread sits in a call to it, Docker says
     "unhealthy" and does nothing. `HangWatchdog` notices (the endpoint does not answer for four
     checks in a row, or one request has been in the reader for five minutes) and `heal_and_exit`
     kills the browser, tidies within a deadline and exits, so compose starts the capsule again on
     the same profile;
  3. the healthcheck that reports the state fits in the time Docker gives it, and says why it failed.
"""

from __future__ import annotations

import http.server
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_supervisor import healthcheck
from browser_supervisor import main as main_module
from browser_supervisor.drain import TAIL_BYTES, StderrTail, stderr_after_exit
from browser_supervisor.reader import READER_TOKEN_HEADER, FetchResult, ReaderServer
from browser_supervisor.supervisor import BrowserSupervisor
from browser_supervisor.watchdog import (
    CHECK_INTERVAL_S,
    EXIT_CODE_HUNG,
    HANDLER_STALL_S,
    MAX_FAILURES,
    HangWatchdog,
    cdp_answers,
)

# -- 1. the stderr pipe ---------------------------------------------------------------------------

#: A child that writes much more than a pipe holds, then reports how far it got. 300 KB against a
#: pipe of 64 KiB (16 KiB on some systems): without a reader it stops in `write`.
NOISY_CHILD = (
    "import sys\n"
    "for _ in range(300):\n"
    "    sys.stderr.write('x' * 1000 + chr(10))\n"
    "    sys.stderr.flush()\n"
    "sys.stderr.write('THE-END')\n"
    "sys.stderr.flush()\n"
)


def _spawn() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", NOISY_CHILD], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )


def test_a_process_whose_stderr_nobody_reads_blocks_for_ever() -> None:
    """The negative control, and the whole incident: this is what the capsule's Chromium did."""
    child = _spawn()
    try:
        time.sleep(1.0)
        assert child.poll() is None, "300 KB into an unread pipe must block the writer"
    finally:
        child.kill()
        child.communicate(timeout=10)


def test_a_drained_process_finishes_and_leaves_its_last_words() -> None:
    child = _spawn()
    tail = StderrTail.attach(child, name="test")
    assert tail is not None
    assert child.wait(timeout=20) == 0
    tail.join(5.0)
    assert not tail.alive
    assert tail.bytes_seen == 300 * 1001 + len("THE-END")
    kept = tail.tail_bytes()
    assert len(kept) == TAIL_BYTES, (
        "bounded: a capsule that runs for months keeps the same kilobytes"
    )
    assert kept.endswith(b"THE-END") and tail.tail(20).endswith("THE-END")
    assert len(tail.tail_bytes(10)) == 10


def test_a_process_without_a_pipe_has_nothing_to_drain() -> None:
    assert StderrTail.attach(SimpleNamespace(), name="fake") is None
    assert StderrTail.attach(SimpleNamespace(stderr=None), name="fake") is None


def test_stderr_after_exit_prefers_the_drain_and_falls_back_to_communicate() -> None:
    child = _spawn()
    tail = StderrTail.attach(child, name="test")
    text = stderr_after_exit(child, tail, timeout=20.0)
    assert text.endswith(b"THE-END")

    class Old:
        def communicate(self, timeout=None):
            return b"", b"stary sposob"

    assert stderr_after_exit(Old(), None) == b"stary sposob"

    class Broken:
        def communicate(self, timeout=None):
            raise OSError("zamkniete")

    assert stderr_after_exit(Broken(), None) == b""


def test_the_supervisor_starts_the_browser_and_the_display_with_a_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself: every `Popen` with `stderr=PIPE` in the supervisor is followed by a
    drain. The source is read, not run, so that a third such process added later is caught too."""
    import ast

    source = Path(sys.modules["browser_supervisor.supervisor"].__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    pipes = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Popen"):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        stderr = keywords.get("stderr")
        if isinstance(stderr, ast.Attribute) and stderr.attr == "PIPE":
            pipes += 1
    assert pipes == 2, "Chromium and Xtigervnc: the two processes whose stderr must be drained"
    assert source.count("StderrTail.attach(") == pipes, "each piped process gets its drain"


# -- 2. the watchdog ------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _watch(**kwargs) -> tuple[HangWatchdog, Clock]:
    clock = Clock()
    return HangWatchdog(clock=clock, **kwargs), clock


def test_one_missed_answer_is_a_busy_browser_four_in_a_row_is_a_hung_one() -> None:
    dog, clock = _watch()
    answers = iter([False] * MAX_FAILURES)
    seen: list[str | None] = []
    for _ in range(MAX_FAILURES):
        seen.append(dog.poll(launch="a", probe=lambda: next(answers), busy_for=lambda: None))
        clock.t += CHECK_INTERVAL_S
    assert seen[:-1] == [None] * (MAX_FAILURES - 1)
    assert seen[-1] is not None and f"{MAX_FAILURES} sprawdzeń" in seen[-1]
    assert "wisi" in seen[-1]


def test_an_answer_resets_the_count() -> None:
    dog, clock = _watch()
    script = iter([False, False, False, True, False, False, False])
    for _ in range(7):
        verdict = dog.poll(launch="a", probe=lambda: next(script), busy_for=lambda: None)
        assert verdict is None, "three failures, an answer, three failures: never four in a row"
        clock.t += CHECK_INTERVAL_S
    assert dog.failures == 3


def test_checks_are_spaced_so_a_fast_loop_does_not_hammer_the_browser() -> None:
    dog, clock = _watch()
    calls: list[int] = []

    def probe() -> bool:
        calls.append(1)
        return True

    for _ in range(50):  # the service loop turns once a second
        dog.poll(launch="a", probe=probe, busy_for=lambda: None)
        clock.t += 1.0
    assert len(calls) == 5, "50 turns of a one-second loop, one check per ten seconds"


def test_a_new_launch_starts_with_a_clean_sheet() -> None:
    dog, clock = _watch()
    for _ in range(MAX_FAILURES - 1):
        assert dog.poll(launch="a", probe=lambda: False, busy_for=lambda: None) is None
        clock.t += CHECK_INTERVAL_S
    assert dog.failures == MAX_FAILURES - 1
    # The browser was replaced (a mode switch): three failures of the OLD one do not count.
    assert dog.poll(launch="b", probe=lambda: False, busy_for=lambda: None) is None
    assert dog.failures == 1


def test_a_person_at_the_screen_has_no_endpoint_and_the_watchdog_stays_out() -> None:
    dog, clock = _watch()
    for _ in range(MAX_FAILURES * 3):
        assert dog.poll(launch="human", probe=None, busy_for=lambda: None) is None
        clock.t += CHECK_INTERVAL_S
    assert dog.failures == 0


def test_the_service_loop_asks_the_endpoint_of_the_launch_it_is_watching() -> None:
    asked: list[str] = []

    def answers(base: str) -> bool:
        asked.append(base)
        return False

    dog, clock = _watch()
    agent = SimpleNamespace(
        endpoint=SimpleNamespace(http_base="http://127.0.0.1:4242"), process_instance_id="8:1"
    )
    for _ in range(MAX_FAILURES):
        reason = dog.poll_running(agent, switching=False, busy_for=lambda: None, answers=answers)
        clock.t += CHECK_INTERVAL_S
    assert asked == ["http://127.0.0.1:4242"] * MAX_FAILURES and reason is not None

    # A person at the screen (no endpoint), and a mode switch in progress: nothing is asked.
    asked.clear()
    dog, clock = _watch()
    person = SimpleNamespace(endpoint=None, process_instance_id="9:1")
    for _ in range(MAX_FAILURES * 2):
        assert (
            dog.poll_running(person, switching=False, busy_for=lambda: None, answers=answers)
            is None
        )
        assert (
            dog.poll_running(agent, switching=True, busy_for=lambda: None, answers=answers) is None
        )
        clock.t += CHECK_INTERVAL_S
    assert asked == []


def test_a_request_stuck_in_the_reader_is_a_hang_even_when_the_endpoint_answers() -> None:
    dog, _ = _watch()
    assert dog.poll(launch="a", probe=lambda: True, busy_for=lambda: HANDLER_STALL_S - 1) is None
    reason = dog.poll(launch="a", probe=lambda: True, busy_for=lambda: HANDLER_STALL_S + 1)
    assert reason is not None and "żądanie w czytniku" in reason
    assert dog.poll(launch="a", probe=lambda: True, busy_for=lambda: None) is None, "idle"


def test_the_stall_limit_is_longer_than_anything_a_healthy_request_does() -> None:
    """Navigation 20 s, the render wait 10 s, the act budget 45 s, the X pace 8 s: a healthy request
    is a minute at the very most, and a mode switch (stop 20 s, start up to 90 s) is the longest
    thing the reader ever does. The limit must clear all of them, or the watchdog would kill a
    capsule that is working."""
    from browser_supervisor.reader import DEFAULT_ACT_TIMEOUT_S, DEFAULT_X_MIN_INTERVAL_S
    from browser_supervisor.x_reader import RENDER_WAIT_S, X_NAV_TIMEOUT_S

    worst_read = X_NAV_TIMEOUT_S + RENDER_WAIT_S + DEFAULT_X_MIN_INTERVAL_S
    assert 2 * max(worst_read, DEFAULT_ACT_TIMEOUT_S) < HANDLER_STALL_S
    assert HANDLER_STALL_S > 20.0 + 90.0, "a mode switch is the longest legitimate request"


class _Answering(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"Browser": "Chrome/x"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def test_cdp_answers_is_true_for_an_answering_port_false_for_a_dead_and_a_silent_one() -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Answering)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert cdp_answers(f"http://127.0.0.1:{server.server_port}", timeout=2.0) is True
    finally:
        server.shutdown()
        server.server_close()

    with socket.socket() as probe:  # a port nobody listens on: a dead browser refuses
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    assert cdp_answers(f"http://127.0.0.1:{dead}", timeout=1.0) is False

    # The hung browser: the connection is accepted (the kernel does it), and nothing is ever said.
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    try:
        started = time.monotonic()
        assert cdp_answers(f"http://127.0.0.1:{silent.getsockname()[1]}", timeout=0.4) is False
        assert time.monotonic() - started < 3.0, "bounded by its own timeout"
    finally:
        silent.close()


# -- the reader says how long its current request has been running --------------------------------


class _Gate:
    """A fetcher that holds the request until told to go: a stand-in for a call that does not
    return."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult:
        self.entered.set()
        self.release.wait(10)
        return FetchResult(status=200, final_url=url, content_type="application/json", body="{}")


def test_the_reader_reports_how_long_the_current_request_has_been_running() -> None:
    gate = _Gate()
    clock = Clock()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token="t",
        fetcher=gate,
        min_interval_s=0.0,
        timeout_s=5.0,
        clock=clock,
        sleep=lambda _s: None,
    )
    server.start()
    try:
        assert server.busy_for() is None, "idle"
        url = (
            f"http://127.0.0.1:{server.port}/read?url="
            "https%3A%2F%2Fwww.reddit.com%2Fapi%2Fme.json%3Fraw_json%3D1"
        )
        request = urllib.request.Request(url, headers={READER_TOKEN_HEADER: "t"})
        worker = threading.Thread(
            target=lambda: urllib.request.urlopen(request, timeout=10).read(), daemon=True
        )
        worker.start()
        assert gate.entered.wait(5), "the request reached the fetcher"
        clock.t += 301.0
        busy = server.busy_for()
        assert busy is not None and busy >= 301.0
        gate.release.set()
        worker.join(5)
        deadline = time.monotonic() + 5
        while server.busy_for() is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.busy_for() is None, "idle again once the request is answered"
    finally:
        gate.release.set()
        server.stop()


# -- the exit ------------------------------------------------------------------------------------


class _Process:
    def __init__(self) -> None:
        self.returncode: int | None = None


class _Supervisor:
    def __init__(self, *, stop_blocks: bool = False) -> None:
        self.events: list[str] = []
        self.process = _Process()
        self.current = SimpleNamespace(process=self.process, stderr_tail=None)
        self._stop_blocks = stop_blocks

    def kill_hard(self, process) -> None:
        assert process is self.process
        self.events.append("kill")
        process.returncode = -9

    def stop(self) -> None:
        self.events.append("stop")
        if self._stop_blocks:
            time.sleep(30)


def test_a_hung_browser_is_killed_first_then_the_capsule_exits_with_its_own_code(capsys) -> None:
    supervisor = _Supervisor()
    exits: list[int] = []

    main_module.heal_and_exit(supervisor, "przeglądarka wisi", exit_fn=exits.append, cleanup_s=5.0)

    assert supervisor.events == ["kill", "stop"], "the kill unblocks the reader, then the tidying"
    assert exits == [EXIT_CODE_HUNG] and EXIT_CODE_HUNG not in (0, 1, 2)
    err = capsys.readouterr().err
    assert "przeglądarka wisi" in err and str(EXIT_CODE_HUNG) in err


def test_the_exit_does_not_wait_for_a_tidying_that_hangs_too() -> None:
    supervisor = _Supervisor(stop_blocks=True)
    exits: list[int] = []
    started = time.monotonic()

    main_module.heal_and_exit(supervisor, "x", exit_fn=exits.append, cleanup_s=0.3)

    assert exits == [EXIT_CODE_HUNG]
    assert time.monotonic() - started < 5.0, "bounded: the exit is the point, not the tidying"


def test_hard_kill_ends_a_process_that_ignores_the_polite_signal(tmp_path: Path) -> None:
    """`kill_hard` is for a browser that does not handle SIGTERM because its main thread is not
    there to; modelled here by a child that ignores it."""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None and child.stdout.readline().strip() == b"ready"
    BrowserSupervisor.kill_hard(child)
    assert child.poll() is not None
    BrowserSupervisor.kill_hard(child)  # already gone: no error
    BrowserSupervisor.kill_hard(None)


# -- 3. the healthcheck --------------------------------------------------------------------------


def test_the_healthchecks_two_waits_fit_well_inside_the_time_docker_gives_it() -> None:
    """Until 13.10.2026 they were 3 s each, 6 s against a 5 s timeout, so the hang read
    "Health check exceeded timeout (5s)" for four hours. Compose now gives it 10 s."""
    assert healthcheck.CDP_TIMEOUT_S + healthcheck.STATE_TIMEOUT_S <= 4.0
    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text("utf-8")
    block = compose.split("browser_supervisor.healthcheck", 1)[1].split("stop_grace_period", 1)[0]
    assert "timeout: 10s" in block


def test_a_hung_browser_makes_the_healthcheck_fail_fast_with_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A port that accepts and never answers: the check ends within its budget, rc 1, and says that
    the browser is hung (not that a file is stale)."""
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    port = silent.getsockname()[1]
    profile = tmp_path / "capsule-x"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/abc\n")
    lock = tmp_path / "versions.lock.toml"
    lock.write_text("# unused: the build is stubbed\n")
    monkeypatch.setenv("WENEXT_VERSIONS_LOCK", str(lock))
    monkeypatch.setenv("WENEXT_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("WENEXT_CAPSULE_ID", "capsule-x")
    monkeypatch.delenv("WENEXT_READER_PORT", raising=False)
    monkeypatch.setattr(healthcheck.pinned, "expected_browser_field", lambda *a, **k: "Chrome/x")
    monkeypatch.setattr(healthcheck, "CDP_TIMEOUT_S", 0.4)
    try:
        started = time.monotonic()
        code = healthcheck.main()
        elapsed = time.monotonic() - started
    finally:
        silent.close()
    assert code == 1 and elapsed < 4.0
    err = capsys.readouterr().err
    assert "wisi" in err and "nie padła" in err, err
