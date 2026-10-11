"""Go/No-Go gate no. 2 — the headful exception path.

The E1.F0 scope (L5161-5163) and the Definition of Done (L5175-5176) require proof of:

    "profile handoff test: a profile logged in in headful mode is correctly used
     by headless without a second login and without copying cookies" (L5162)
    "the same profile can be taken over immediately in the headful view without losing
     the session" (L5176)

This gate is executed WITHOUT a real social account. It proves a property of the capsule —
that the profile survives a mode switch — and not that some specific platform works headless.
That second thing is the capability probe from phase F2 (L4381) and is explicitly outside F0.

The helper server runs on the loopback inside the capsule. It is not a fixture site (D16),
just a minimal stub, so that gate no. 2 does not depend on any other deliverable.
"""

from __future__ import annotations

import http.server
import json
import signal
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from community_os_contracts import CapsuleMode

from .contract import CheckResult, ContractResult
from .supervisor import BrowserSupervisor, ForbiddenInteractiveReason

#: A cookie with an explicit Max-Age, not a session one. Chrome does not persist session
#: cookies between process runs, so a test on a session cookie would measure something other
#: than what it declares. A real "remember me" login sets exactly this kind of persistent cookie.
COOKIE_NAME = "wenext_sesja"
COOKIE_VALUE = "zalogowany-w-headful"

#: The evidence file of GATE-2, named in `tools/gonogo/criteria.yaml` (`zrodlo_dowodu`).
#: The name is part of the contract with the report — it does not change.
ARTIFACT_NAME = "headful-handoff.json"

#: Signals whose DEFAULT disposition ends the process inside the C runtime: no Python
#: exception is raised, so no `finally` runs and no artifact is written. `docker stop` sends
#: SIGTERM, and so does every process manager and every `kill` without arguments; SIGHUP is
#: what a closing terminal sends. Both are ORDINARY events, unlike the SIGKILL the old
#: comment here named as the only gap.
#:
#: SIGINT is deliberately NOT on the list: Python already raises `KeyboardInterrupt` for it,
#: `finally` already runs, and taking it over would change Ctrl+C into a different exception
#: type for no gain.
TERMINATING_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGHUP)


class TerminatedBySignal(BaseException):
    """A terminating signal, re-raised so that the `finally` chain gets to run.

    Derived from `BaseException`, NOT `Exception`, and that is the whole point: gate 2 is
    full of `except Exception` blocks that turn an error into a failed check and carry on
    (check no. 1, `_shut_down`). An `Exception` here would be swallowed by them, the run
    would continue after the operator had already asked it to stop, and the artifact would
    describe a measurement made during a shutdown. As a `BaseException` it passes through
    all of them and is caught only by the one handler that records it and re-raises.
    """

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(f"sygnał {signal.Signals(signum).name} ({signum})")


@contextmanager
def _terminating_signals_raise() -> Iterator[bool]:
    """Makes SIGTERM/SIGHUP raise `TerminatedBySignal` for the length of the run.

    Yields whether the handlers were actually installed. `signal.signal` works ONLY on the
    main thread and raises `ValueError` anywhere else; gate 2 must degrade there rather than
    die, because a run that measured all five checks is worth more than the guarantee it
    could not arm. What is lost off the main thread is exactly the signal guarantee, and
    `_write_artifact` says so where it states what the write survives.

    The previous handlers are restored on the way out, so a caller running gate 2 inside a
    larger process gets its own signal handling back.
    """

    def handler(signum: int, _frame: FrameType | None) -> None:
        raise TerminatedBySignal(signum)

    previous: list[tuple[signal.Signals, object]] = []
    try:
        for sig in TERMINATING_SIGNALS:
            previous.append((sig, signal.signal(sig, handler)))
    except ValueError:
        pass

    try:
        yield len(previous) == len(TERMINATING_SIGNALS)
    finally:
        for sig, earlier in reversed(previous):
            signal.signal(sig, earlier)


class _LoginStub(http.server.BaseHTTPRequestHandler):
    """A minimal server: `/login` sets a persistent cookie, `/whoami` reads it back."""

    def do_GET(self):  # the BaseHTTPRequestHandler interface
        if self.path == "/login":
            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={COOKIE_VALUE}; Path=/; Max-Age=86400; SameSite=Lax",
            )
            self._send(b"<html><body><h1 id='stan'>zalogowano</h1></body></html>")
            return

        if self.path == "/whoami":
            cookies = self.headers.get("Cookie", "")
            logged_in = f"{COOKIE_NAME}={COOKIE_VALUE}" in cookies
            state = "ZALOGOWANY" if logged_in else "ANONIM"
            self.send_response(200)
            self._send(f"<html><body><h1 id='stan'>{state}</h1></body></html>".encode())
            return

        self.send_response(404)
        self._send(b"nie ma")

    def _send(self, content: bytes) -> None:
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        return


def _start_stub() -> tuple[http.server.HTTPServer, int]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _LoginStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _read_state(endpoint_http: str, url: str) -> str:
    """Opens the page over CDP and returns the text of the #stan element."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint_http)
        context = browser.contexts[0]
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        text = page.locator("#stan").inner_text()
        page.close()
        browser.close()
    return text


def _cookie_count(endpoint_http: str) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint_http)
        cookies = browser.contexts[0].cookies()
        browser.close()
    return len([c for c in cookies if c["name"] == COOKIE_NAME])


@dataclass
class Gate2Run:
    """The measurement window of gate no. 2 — the marker saying whether the run FINISHED.

    The field names are Polish ON PURPOSE: `asdict()` writes them straight into the artifact,
    so they are keys in the data, not identifiers (D-010 pt. 2, list `FORBIDDEN_NAMES`).
    `tools/gonogo/report.py::_evidence_is_closed` reads exactly `przebieg.rozpoczety` and
    `przebieg.zakonczony`: a check whose evidence carries no end marker is never counted as
    passed, only as a lack of evidence. That is the D-009 mechanism, built for gate no. 3 —
    gate no. 2 had the same hole and this is the same lock.
    """

    rozpoczety: str
    zakonczony: str | None = None
    #: Why the run is not closed: the interruption, or a teardown that failed. Free text for
    #: the operator. `szczegol` is the name `RunEvent` already uses for the same thing.
    szczegol: str = ""

    def note(self, text: str) -> None:
        self.szczegol = f"{self.szczegol}; {text}" if self.szczegol else text


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_artifact(directory: Path, result: ContractResult, run: Gate2Run) -> None:
    """Writes the gate 2 artifact. Called from a `finally`, so it runs on EVERY exit path.

    Why it exists at all: the write used to sit AFTER the `try/finally`, and the "headful did
    not start" path left through an early `return`. The file on disk then stayed exactly as it
    was — the PREVIOUS run's `GO` — and `tools/gonogo/report.py` read it as fresh evidence.
    An aborted run has to overwrite that verdict; it must never leave it standing. This is the
    same hole D-009 closed for gate no. 3, reported there as a false pass six hours early.

    Two rules are encoded here, and neither may be dropped:

    * an unclosed run (`zakonczony is None`) marks EVERY check `wymaga_czlowieka`, so the
      report renders `NIE_URUCHOMIONO` — a missing result, not a `NO_GO`. Those are two
      different statements and the document requires the distinction (KONWENCJE, "Bramki").
    * the top-level `zdane` demands at least one check. `ContractResult.zdane` is `all(...)`
      and `all([])` is `True` — the trap behind two of the six false passes in this project.
      An interruption before the first check must not write `"zdane": true` over anything.
      `bool(kontrole)` is that guard, and it is measured on its own: a test that only
      interrupts the run cannot see it, because an interrupted run is also an unclosed one
      and `closed` alone drives `zdane` to false. Proved by mutation — the guard was deleted
      and the whole file stayed green. The cardinality is now asserted SEPARATELY from the
      predicate over it, which is the rule KONWENCJE states for exactly this trap:
      `test_interruption_before_the_first_check_writes_no_pass`, second half.

    "Runs on EVERY exit path" is a claim, so the exact list of what it does and does not
    survive lives at the call site, in the innermost `finally` of `_run_gate2`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    closed = run.zakonczony is not None
    przebieg = asdict(run)

    kontrole: list[dict] = []
    for check in result.kontrole:
        entry = asdict(check)
        szczegoly = dict(entry.get("szczegoly") or {})
        szczegoly["przebieg"] = przebieg
        if not closed:
            szczegoly["wymaga_czlowieka"] = True
        entry["szczegoly"] = szczegoly
        kontrole.append(entry)

    data = {
        "zdane": bool(kontrole) and closed and result.zdane,
        "liczba_kontroli": len(kontrole),
        "kontrole": kontrole,
        "przebieg": przebieg,
    }
    (directory / ARTIFACT_NAME).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _shut_down(
    run: Gate2Run, stub: http.server.HTTPServer | None, supervisor: BrowserSupervisor
) -> None:
    """Tears the run down without letting a teardown error skip the artifact.

    Each step is guarded separately: a failure of the first must not skip the second, and
    neither may propagate — the one thing that must always happen is the write. A teardown
    failure is DATA and it also means the capsule may still be running (releasing the lease
    requires stopping the WHOLE capsule, L2431), so it is recorded and leaves the run
    unclosed instead of being swallowed.
    """
    steps: list[tuple[str, Callable[[], None]]] = []
    if stub is not None:
        steps.append(("serwer stub", stub.shutdown))
    steps.append(("supervisor.stop", supervisor.stop))

    for label, step in steps:
        try:
            step()
        except Exception as exc:  # a teardown error is recorded, never raised
            run.note(f"sprzątanie {label}: {type(exc).__name__}: {exc}")


def run_gate2(supervisor: BrowserSupervisor, *, artifacts_dir: Path) -> ContractResult:
    """Runs the full profile handoff test between the modes.

    Split in two on purpose: this half ARMS the apparatus, `_run_gate2` does the measuring.
    The handlers have to be in place before the first statement that could be interrupted,
    and they may only be taken down after the artifact has been written — which is what
    wrapping the whole of `_run_gate2` buys, since its write sits in its innermost `finally`.
    """
    with _terminating_signals_raise():
        return _run_gate2(supervisor, artifacts_dir=artifacts_dir)


def _run_gate2(supervisor: BrowserSupervisor, *, artifacts_dir: Path) -> ContractResult:
    """The measurement itself.

    EVERY exit path from this function leaves an artifact — see `_write_artifact` for why.
    """
    result = ContractResult()
    run = Gate2Run(rozpoczety=_now())
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    server: http.server.HTTPServer | None = None
    # Set as the LAST statement of the body. The early `return` below skips it, and so does
    # every exception — which is exactly the difference between a finished run and an
    # aborted one.
    body_finished = False

    try:
        server, port = _start_stub()
        base_url = f"http://127.0.0.1:{port}"

        # --- 1. login in INTERACTIVE mode (headful + Xvnc) -------------------
        try:
            # display_only, not FIRST_LOGIN: since 27.09 a launch for a person has no DevTools
            # endpoint (Google refuses a sign-in in a browser that has one, `args.HUMAN_ARGS`),
            # and this gate logs in through CDP. What it measures is the profile handoff between
            # headful and headless, and a display-only launch is the same executable, the same
            # profile and the same headful Chrome on Xtigervnc.
            capsule = supervisor.start(CapsuleMode.INTERACTIVE, display_only=True)
            _read_state(capsule.endpoint.http_base, f"{base_url}/login")
            headful_cookie_count = _cookie_count(capsule.endpoint.http_base)
            result.add(
                CheckResult(
                    nazwa="headful_startuje_na_tym_samym_profilu",
                    wymog_z_dokumentu="headful capsule jako ścieżka wyjątkowa (L5161)",
                    zdane=headful_cookie_count == 1,
                    dowod=(
                        f"tryb INTERACTIVE wystartował na profilu {supervisor.user_data_dir}, "
                        f"logowanie ustawiło {headful_cookie_count} ciasteczko"
                    ),
                    szczegoly={"display_id": capsule.display_process is not None},
                )
            )
        except Exception as exc:
            result.add(
                CheckResult(
                    nazwa="headful_startuje_na_tym_samym_profilu",
                    wymog_z_dokumentu="headful capsule jako ścieżka wyjątkowa (L5161)",
                    zdane=False,
                    dowod=f"{type(exc).__name__}: {exc}",
                )
            )
            # THE PATH THIS FIX IS ABOUT. Without a headful capsule the remaining four
            # checks cannot be measured, so we leave — but we leave the run OPEN on purpose:
            # one failed check out of five is a lack of evidence, not a gate result. Before
            # the fix this `return` jumped over the write entirely and the previous run's
            # `GO` stayed on disk as if it had just been measured.
            run.note(
                "przebieg przerwany: headful nie wystartował, pozostałe cztery kontrole "
                "nie zostały zmierzone"
            )
            return result

        previous_session = id(capsule)

        # --- 2. switching to HEADLESS on THE SAME profile --------------------
        headless = supervisor.switch_mode(CapsuleMode.HEADLESS, safe_stop_done=True)

        result.add(
            CheckResult(
                nazwa="przejscie_trybu_tworzy_nowa_sesje",
                wymog_z_dokumentu=(
                    "przełączenie trybu zapisuje audit event i nową browser_session (L2448)"
                ),
                zdane=id(headless) != previous_session
                and headless.process.pid != capsule.process.pid,
                dowod=(
                    f"nowy proces pid={headless.process.pid} "
                    f"(poprzedni {capsule.process.pid}), "
                    f"instancja={headless.process_instance_id}"
                ),
            )
        )

        # --- 3. the session survived the switch ------------------------------
        state = _read_state(headless.endpoint.http_base, f"{base_url}/whoami")
        result.add(
            CheckResult(
                nazwa="sesja_przezywa_przelaczenie_do_headless",
                wymog_z_dokumentu=(
                    "profil zalogowany w trybie headful jest używany przez headless "
                    "bez ponownego loginu (L5162, L5176, L5534)"
                ),
                zdane=state == "ZALOGOWANY",
                dowod=f"/whoami w trybie HEADLESS zwrócił {state!r} bez ponownego logowania",
            )
        )

        # --- 4. no copying of cookies ----------------------------------------
        # Negative evidence: the profile is one and the same directory, so there was nothing
        # to copy. We check that no export file appeared anywhere in the profile tree.
        suspicious_files = [
            p.name
            for p in supervisor.user_data_dir.rglob("*")
            if p.is_file()
            and any(s in p.name.lower() for s in ("cookies.json", "export", "backup"))
        ]
        result.add(
            CheckResult(
                nazwa="brak_kopiowania_cookies",
                wymog_z_dokumentu="bez kopiowania cookies (L5162); zakaz z sekcji 4 (L246)",
                zdane=not suspicious_files,
                dowod=(
                    "oba tryby użyły tego samego user-data-dir "
                    f"({supervisor.user_data_dir}); brak plików eksportu ciasteczek"
                    if not suspicious_files
                    else f"znaleziono podejrzane pliki: {suspicious_files}"
                ),
            )
        )

        # --- 5. the closed list of headful reasons ---------------------------
        supervisor.stop()
        try:
            supervisor.start(CapsuleMode.INTERACTIVE, reason=None)
            rejected = False
            szczegol = "start bez powodu PRZESZEDŁ — lista nie jest egzekwowana"
        except ForbiddenInteractiveReason as exc:
            rejected = True
            szczegol = str(exc)[:160]

        result.add(
            CheckResult(
                nazwa="powod_spoza_listy_odrzucony",
                wymog_z_dokumentu=(
                    "lista dozwolonych powodów headful jest zamknięta; każde inne użycie "
                    "tworzy alert i wymaga decyzji (L5518)"
                ),
                zdane=rejected,
                dowod=szczegol,
            )
        )

        body_finished = True

    except BaseException as exc:  # KeyboardInterrupt/SystemExit ON PURPOSE
        # An interrupted run has no verdict (D-009). We record WHY and re-raise: swallowing
        # the error would turn a crash into a quiet "no result", and the caller would print
        # a summary of a run that never happened.
        run.note(f"przebieg przerwany: {type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            _shut_down(run, server, supervisor)
            # The end marker goes in ONLY when the body ran to the end AND the teardown
            # succeeded. `run.szczegol` is non-empty for exactly one reason: something went
            # wrong. Anything else stays an unclosed run and therefore has no verdict.
            if body_finished and not run.szczegol:
                run.zakonczony = _now()
        finally:
            # The innermost `finally`. What it survives, stated as a list rather than as a
            # slogan: every `return`, every `Exception`, `KeyboardInterrupt`, `SystemExit`,
            # a teardown that itself blew up, and — since `run_gate2` arms
            # `_terminating_signals_raise` — SIGTERM and SIGHUP.
            #
            # What it does NOT survive, and this half is the point: SIGKILL, SIGSTOP followed
            # by a kill, `os._exit`, a segfault in a C extension, the machine losing power,
            # and any of the above on a gate 2 running OFF the main thread, where
            # `signal.signal` cannot be installed at all. The sentence that used to stand here
            # — "short of SIGKILL, nothing may skip this write" — named the rare case and
            # missed the common one: the default disposition of SIGTERM ends the process
            # without raising anything in Python, so no `finally` ran, and `docker stop` sends
            # exactly that. Measured 2026-08-31: the child died with code -15 and the previous
            # run's `GO` was still on disk, 5/5. Eighth false pass of this class in the
            # project. A comment claiming a guarantee the code does not give is the same
            # disease as a check passed without a measurement.
            _write_artifact(artifacts_dir, result, run)

    return result


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
