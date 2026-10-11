"""Gate no. 2 never leaves the previous verdict on disk.

The hole these tests close: `run_gate2` wrote `headful-handoff.json` only AFTER the
`try/finally`, while the "headful did not start" path left through an early `return` and
every other failure left through an exception. Both jumped over the write, so the file on
disk stayed as it was — the PREVIOUS run's `GO` — and `tools/gonogo/report.py` read it as
fresh evidence of a run that had just crashed. Seventh false pass of this class in the
project; D-009 had already closed exactly this hole, but only for gate no. 3.

The tests need neither Chrome nor Playwright: the capsule launch is replaced (the closed
list of headful reasons is still checked by the REAL `BrowserSupervisor.start`), and the
two CDP helpers are monkeypatched.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_supervisor import gate2
from browser_supervisor.contract import ContractResult
from browser_supervisor.supervisor import BrowserSupervisor
from community_os_contracts import CapsuleMode

#: The five checks GATE-2 requires, copied from `tools/gonogo/criteria.yaml`. They are on
#: the `FORBIDDEN_NAMES` list — they are keys in the evidence, and the report finds the gate
#: result by exactly these strings, so a test that spelled them differently would prove
#: nothing about the report.
GATE2_REQUIRED_CHECKS = (
    "headful_startuje_na_tym_samym_profilu",
    "przejscie_trybu_tworzy_nowa_sesje",
    "sesja_przezywa_przelaczenie_do_headless",
    "brak_kopiowania_cookies",
    "powod_spoza_listy_odrzucony",
)

#: The evidence file named by `tools/gonogo/criteria.yaml` (`zrodlo_dowodu` of GATE-2).
#: Spelled out here rather than imported from `gate2`: the name is a contract with the
#: report, so this test has to fail if the module ever starts writing somewhere else.
ARTIFACT_NAME = "headful-handoff.json"

EXPECTED_BUILD = "Chrome/153.0.8010.12"


def _capsule(pid: int, instance: str) -> SimpleNamespace:
    """The parts of `RunningCapsule` that gate 2 actually touches."""
    return SimpleNamespace(
        process=SimpleNamespace(pid=pid),
        process_instance_id=instance,
        display_process=SimpleNamespace(),
        endpoint=SimpleNamespace(http_base=f"http://127.0.0.1:{9000 + pid}"),
    )


class _FakeSupervisor(BrowserSupervisor):
    """The real supervisor with only the process launch replaced.

    Subclassed rather than duck-typed on purpose: `user_data_dir`, the dataclass fields and
    the closed-list guard stay the real ones, so a change in `BrowserSupervisor` reaches
    this test instead of quietly passing it.
    """

    # Plain class attributes, deliberately without annotations: an annotation here would
    # make `dataclass` inheritance treat them as new fields of the supervisor.
    start_error = None
    switch_error = None
    stop_error = None
    stop_error_on_call = 0
    stop_calls = 0
    #: Called at the top of `switch_mode`. The signal tests use it to park a REAL process in
    #: the middle of the run — after check no. 1 has been recorded — so that the signal
    #: arrives where a `docker stop` would actually catch the gate.
    switch_hook = None

    def start(
        self,
        mode=CapsuleMode.HEADLESS,
        *,
        reason=None,
        display_only=False,
        timeout_s=90.0,
        start_url=None,
    ):
        if mode is CapsuleMode.INTERACTIVE and reason is None and not display_only:
            # Check no. 5 asks whether the closed list of headful reasons is enforced. We
            # delegate to the REAL guard (it refuses on `reason is None` before it touches
            # the filesystem); a fake answering that question itself would test the fake.
            return super().start(mode, reason=reason, timeout_s=timeout_s)
        if self.start_error is not None:
            raise self.start_error
        return _capsule(pid=101, instance="instancja-headful")

    def switch_mode(
        self, target_mode, *, reason=None, display_only=False, safe_stop_done=False, start_url=None
    ):
        if self.switch_hook is not None:
            self.switch_hook()
        if self.switch_error is not None:
            raise self.switch_error
        return _capsule(pid=202, instance="instancja-headless")

    def stop(self, *, timeout_s: float = 20.0) -> None:
        self.stop_calls += 1
        if self.stop_error is not None and self.stop_calls == self.stop_error_on_call:
            raise self.stop_error


def _supervisor(tmp_path: Path, **behaviour) -> _FakeSupervisor:
    supervisor = _FakeSupervisor(
        binary=tmp_path / "chrome" / "chrome",
        profiles_dir=tmp_path / "profiles",
        capsule_id="capsule-test",
        expected_cdp_browser=EXPECTED_BUILD,
    )
    for name, value in behaviour.items():
        setattr(supervisor, name, value)
    # Check no. 4 walks the profile tree looking for cookie exports.
    supervisor.user_data_dir.mkdir(parents=True, exist_ok=True)
    return supervisor


def _previous_go(artifacts_dir: Path) -> Path:
    """Puts a full `GO` on disk — the artifact of an earlier, successful run.

    Written in the shape the pre-fix code produced (no `przebieg`), because that is exactly
    the file a new run finds on the volume.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    file_path = artifacts_dir / ARTIFACT_NAME
    file_path.write_text(
        json.dumps(
            {
                "zdane": True,
                "liczba_kontroli": len(GATE2_REQUIRED_CHECKS),
                "kontrole": [
                    {
                        "nazwa": nazwa,
                        "wymog_z_dokumentu": "poprzedni przebieg",
                        "zdane": True,
                        "dowod": "dowod z poprzedniego przebiegu",
                        "szczegoly": {},
                    }
                    for nazwa in GATE2_REQUIRED_CHECKS
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return file_path


def _artifact(artifacts_dir: Path) -> dict:
    file_path = artifacts_dir / ARTIFACT_NAME
    assert file_path.exists(), (
        f"brak artefaktu {file_path} — ścieżka wyjścia ominęła zapis, a na dysku "
        "został werdykt poprzedniego przebiegu"
    )
    return json.loads(file_path.read_text(encoding="utf-8"))


def _passing_names(data: dict) -> set[str]:
    """Check names the report would count as PASSED.

    Mirrors `tools/gonogo/report.py`: a check counts only when `zdane` is true AND its
    evidence comes from a run with an end marker (`_evidence_is_closed`, D-009).
    """
    passing = set()
    for check in data["kontrole"]:
        przebieg = (check.get("szczegoly") or {}).get("przebieg") or {}
        closed = "rozpoczety" not in przebieg or przebieg.get("zakonczony") is not None
        if check["zdane"] and closed:
            passing.add(check["nazwa"])
    return passing


def _stub_read_state(_endpoint: str, _url: str) -> str:
    return "ZALOGOWANY"


def _stub_cookie_count(_endpoint: str) -> int:
    return 1


@pytest.fixture
def cdp_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces the two helpers that need a real browser over CDP."""
    monkeypatch.setattr(gate2, "_read_state", _stub_read_state)
    monkeypatch.setattr(gate2, "_cookie_count", _stub_cookie_count)


# ---------------------------------------------------------------------------
# An aborted run must not leave the previous GO
# ---------------------------------------------------------------------------


def test_headful_that_did_not_start_overwrites_the_previous_go(tmp_path: Path) -> None:
    """The early `return` from check no. 1 — the path that used to skip the write entirely."""
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    supervisor = _supervisor(tmp_path, start_error=RuntimeError("brak binarki Chrome"))

    result = gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)

    assert result.zdane is False
    data = _artifact(artifacts_dir)
    assert data["zdane"] is False
    assert [k["nazwa"] for k in data["kontrole"]] == ["headful_startuje_na_tym_samym_profilu"]
    assert _passing_names(data) == set()
    # An aborted run is a LACK of evidence, not a NO_GO — the report has to render
    # NIE_URUCHOMIONO for it (KONWENCJE, section "Bramki, pomiary i werdykty").
    assert data["przebieg"]["zakonczony"] is None
    assert data["kontrole"][0]["szczegoly"]["wymaga_czlowieka"] is True


def test_interruption_before_the_first_check_writes_no_pass(tmp_path: Path) -> None:
    """Interrupted with zero checks recorded — and the `all([])` guard measured SEPARATELY.

    The first half is the end-to-end path: the artifact appears at all, and it says no pass.

    The second half exists because the first half DOES NOT PROVE what this test used to
    claim. An interrupted run is also an unclosed run, so `closed` in `_write_artifact` is
    already false and it alone drives `zdane` to false; deleting the `bool(kontrole)` term
    from the production code left every assertion above green. Proved by mutation on
    2026-08-31 — a test declaring a protection it does not give is worse than no test,
    because the reader believes the trap is shut.

    So the guard is measured where nothing else can mask it: an EMPTY result plus a CLOSED
    run. `ContractResult.zdane` is `all(...)` over zero checks, and `all([])` is `True` —
    the exact trap behind two of this project's six false passes — so the cardinality term
    is then the only thing between the artifact and a `"zdane": true` with no evidence in
    it. That is the KONWENCJE rule spelled out: check the size of the set SEPARATELY from
    the predicate over it.
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    supervisor = _supervisor(tmp_path, start_error=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)

    data = _artifact(artifacts_dir)
    assert data["kontrole"] == []
    assert data["zdane"] is False
    assert data["przebieg"]["zakonczony"] is None

    # --- the trap itself, with the `closed` term deliberately taken out of the way ---
    empty = ContractResult()
    assert empty.zdane is True, (
        "all([]) is True — if this ever becomes False the trap moved to ContractResult "
        "and the guard below stops being the thing under test"
    )
    closed_run = gate2.Gate2Run(
        rozpoczety=data["przebieg"]["rozpoczety"], zakonczony="2026-08-31T12:00:00+00:00"
    )
    gate2._write_artifact(artifacts_dir, empty, closed_run)

    isolated = _artifact(artifacts_dir)
    assert isolated["przebieg"]["zakonczony"] is not None, "the counterfactual needs a CLOSED run"
    assert isolated["liczba_kontroli"] == 0
    assert isolated["zdane"] is False, (
        "zero checks were measured, so there is nothing that could have passed; a closed "
        "run with an empty check list must never be written as a pass"
    )


def test_interruption_in_the_middle_leaves_no_check_readable_as_passed(
    tmp_path: Path, cdp_answers: None
) -> None:
    """Ctrl+C after check no. 1 had already passed.

    The check itself stays `zdane: true` — it was measured — but its evidence comes from an
    unclosed run, so the report may not count it (D-009). And above all: the four checks
    from the previous GO must be gone from the file.
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    supervisor = _supervisor(tmp_path, switch_error=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)

    data = _artifact(artifacts_dir)
    assert data["zdane"] is False
    assert [k["nazwa"] for k in data["kontrole"]] == ["headful_startuje_na_tym_samym_profilu"]
    assert data["kontrole"][0]["zdane"] is True
    assert _passing_names(data) == set()
    assert "KeyboardInterrupt" in data["przebieg"]["szczegol"]


def test_teardown_failure_leaves_the_run_open_instead_of_a_go(
    tmp_path: Path, cdp_answers: None
) -> None:
    """All five checks passed, but the capsule could not be stopped.

    A run whose teardown failed is not closed: the capsule may still be holding the profile
    (releasing the lease requires stopping the WHOLE capsule, L2431). The measurement is kept
    in the artifact, the verdict is not.

    "The measurement is kept" is HALF of the claim, and until 2026-08-31 this test asserted
    only the other half. Every assertion here was about the missing verdict, and each one is
    satisfied by an artifact holding ZERO checks: `zdane` false, `zakonczony` None and
    `_passing_names(data) == set()` all hold over an empty list. That is the `all([])` trap
    wearing the report's clothes — the set was never sized, only quantified over. So the
    cardinality is asserted below, BEFORE any `all(...)` over the checks, which is the order
    KONWENCJE requires ("zawsze sprawdzaj liczność osobno od predykatu").
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    # Call no. 1 is check no. 5 inside the body; call no. 2 is the teardown.
    supervisor = _supervisor(
        tmp_path, stop_error=RuntimeError("nie udało się zatrzymać kapsuły"), stop_error_on_call=2
    )

    result = gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)

    assert result.zdane is True, "wszystkie pięć kontroli zmierzono i zdano"
    data = _artifact(artifacts_dir)

    # --- the measurement IS kept: five checks, in the order the body records them ---
    assert data["liczba_kontroli"] == len(GATE2_REQUIRED_CHECKS), (
        "przebieg zmierzył wszystkie pięć kontroli przed nieudanym sprzątaniem; artefakt, "
        f"który zgłasza {data['liczba_kontroli']}, zgubił pomiar, a nie werdykt"
    )
    assert [k["nazwa"] for k in data["kontrole"]] == list(GATE2_REQUIRED_CHECKS)
    # Only now, with the cardinality pinned, is a predicate over the list worth anything.
    assert all(k["zdane"] for k in data["kontrole"]), (
        "każda z pięciu kontroli została zmierzona i zdana — nieudane sprzątanie odbiera "
        "przebiegowi werdykt, nie unieważnia pomiarów"
    )

    # --- the verdict is NOT: unclosed run, every check flagged for a human ---
    assert data["przebieg"]["zakonczony"] is None
    assert data["zdane"] is False
    assert all(k["szczegoly"]["wymaga_czlowieka"] is True for k in data["kontrole"]), (
        "przebieg bez znacznika końca każe raportowi renderować NIE_URUCHOMIONO, więc "
        "znacznik `wymaga_czlowieka` musi stać przy KAŻDEJ kontroli, nie przy pierwszej"
    )
    assert _passing_names(data) == set()
    assert "nie udało się zatrzymać kapsuły" in data["przebieg"]["szczegol"]


# ---------------------------------------------------------------------------
# A terminating signal — the path `docker stop` takes, and the one the comment lied about
# ---------------------------------------------------------------------------

#: Exit code the driver below reports for "run_gate2 raised TerminatedBySignal". A dedicated
#: number rather than 1, so it cannot be confused with any other way the child could die: an
#: import error, an assertion, or the default signal disposition (which shows as a NEGATIVE
#: return code and is precisely the failure this test exists to catch).
TERMINATED_EXIT_CODE = 33

#: Run in a REAL child process, because the thing under test is what a signal does to a
#: process — and a fake signal delivered inside pytest would prove nothing about the default
#: disposition. The child parks inside `switch_mode`, after check no. 1 has been recorded, so
#: the artifact under inspection has both a measured check and an unclosed run.
_TERMINATION_DRIVER = '''\
"""Parks gate 2 mid-run and waits to be signalled. Written by test_gate2_artifact.py."""
import sys
import time
from pathlib import Path

service_root, tests_dir, workdir, artifacts_dir, ready_file, exit_code = sys.argv[1:]
sys.path.insert(0, tests_dir)
sys.path.insert(0, service_root)

import test_gate2_artifact as harness
from browser_supervisor import gate2

# The same two stubs the `cdp_answers` fixture installs, taken from the test module rather
# than rewritten here: a second copy would drift from the one the other tests use.
gate2._read_state = harness._stub_read_state
gate2._cookie_count = harness._stub_cookie_count


def park() -> None:
    Path(ready_file).write_text("gotowy", encoding="utf-8")
    while True:
        time.sleep(0.05)


supervisor = harness._supervisor(Path(workdir), switch_hook=park)
try:
    gate2.run_gate2(supervisor, artifacts_dir=Path(artifacts_dir))
except gate2.TerminatedBySignal:
    sys.exit(int(exit_code))
'''


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_a_terminating_signal_leaves_an_unclosed_artifact(tmp_path: Path, signal_name: str) -> None:
    """`docker stop` sends SIGTERM, and Python's default for it raises nothing at all.

    This is the charge against the previous fix. `_write_artifact` carried the sentence
    "short of SIGKILL, nothing may skip this write", and it was FALSE: the default
    disposition of SIGTERM and SIGHUP ends the process inside the C runtime, no exception is
    raised, and NO `finally` block runs. So on the one path a process manager takes every
    single time — `docker stop`, a systemd stop, a closing terminal — the previous run's `GO`
    stayed on disk exactly as it did before D-015, and `tools/gonogo/report.py` would have
    read it as fresh evidence. SIGKILL, the case the comment did name, is the RARE one.

    The fix is a handler that turns the signal into an exception, so the guarantee the
    comment claims becomes one the code actually provides. What the artifact must then say is
    the D-009 answer: `zakonczony: None` and `wymaga_czlowieka: true` on every check that was
    measured — a lack of evidence, never a verdict.
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    ready = tmp_path / "gotowy"
    driver = tmp_path / "driver.py"
    driver.write_text(_TERMINATION_DRIVER, encoding="utf-8")

    tests_dir = Path(__file__).resolve().parent
    child = subprocess.Popen(
        [
            sys.executable,
            str(driver),
            str(tests_dir.parent),
            str(tests_dir),
            str(tmp_path),
            str(artifacts_dir),
            str(ready),
            str(TERMINATED_EXIT_CODE),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not ready.exists() and child.poll() is None:
            time.sleep(0.02)
        assert ready.exists(), (
            "dziecko nie doszło do środka przebiegu; "
            f"kod wyjścia={child.poll()}, stderr={child.communicate(timeout=10)[1]}"
        )
        child.send_signal(getattr(signal, signal_name))
        _, stderr = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)

    assert child.returncode == TERMINATED_EXIT_CODE, (
        f"proces zakończył się kodem {child.returncode}, oczekiwano {TERMINATED_EXIT_CODE}. "
        f"Kod ujemny znaczy, że {signal_name} zabił proces domyślną obsługą — żaden `finally` "
        f"nie pobiegł, więc artefakt nie został nadpisany.\n{stderr}"
    )

    data = _artifact(artifacts_dir)
    # The previous GO is gone: one measured check, not the five from the file on disk.
    assert [k["nazwa"] for k in data["kontrole"]] == ["headful_startuje_na_tym_samym_profilu"]
    assert data["zdane"] is False
    assert data["przebieg"]["zakonczony"] is None
    assert data["kontrole"][0]["szczegoly"]["wymaga_czlowieka"] is True
    assert _passing_names(data) == set()
    # The evidence names the signal, so the operator reads WHY there is no verdict.
    assert signal_name in data["przebieg"]["szczegol"]
    assert "TerminatedBySignal" in data["przebieg"]["szczegol"]


# ---------------------------------------------------------------------------
# `TerminatedBySignal` derives from BaseException — "the whole point", and until
# 2026-08-31 the one guarantee in this module that no test guarded
# ---------------------------------------------------------------------------
#
# The charge, from the wave-1 verification: ten mutations of `gate2.py`, nine caught, and the
# escapee was `class TerminatedBySignal(BaseException)` → `(Exception)`. The whole suite —
# including `test_a_terminating_signal_leaves_an_unclosed_artifact`, which sends a REAL
# SIGTERM — stayed green at 33 passed. It could not see the difference, because it parks the
# child inside `switch_mode`, which sits between the two `except Exception` blocks and under
# the module's `except BaseException`, and that one catches either base class.
#
# What the base class actually buys: the signal must cross `except Exception` handlers that
# exist to turn an error into a FAILED CHECK and carry on. As an `Exception` the signal
# becomes such a check — the artifact then carries a measurement that was never taken, of a
# capsule state nobody observed, while the operator has already asked the run to stop. Six of
# this project's false passes came from exactly that shape: a claim standing in for a
# measurement. The two tests below therefore drive the signal THROUGH those handlers, one
# each, instead of around them.


def test_a_terminating_signal_is_not_swallowed_by_the_first_check(tmp_path: Path) -> None:
    """The signal arrives inside check no. 1, whose `except Exception` is the widest net here.

    Chosen because it is also the likeliest place for a real `docker stop` to land: check
    no. 1 launches the headful capsule and waits up to 90 s for it, so it is by far the
    longest-running statement in the gate.

    With `BaseException` the signal walks past that handler untouched, reaches the module's
    single `except BaseException`, is recorded as an interruption and re-raised. The artifact
    then says what D-009 requires: nothing was measured, `zakonczony` is None, no verdict.

    With `Exception` the handler eats it and writes `headful_startuje_na_tym_samym_profilu:
    zdane=false` into the artifact — a check reported as MEASURED although nothing about the
    profile was ever read, and blamed on the capsule ("headful nie wystartował") when the
    truth is that the operator stopped the run. The gate then RETURNS NORMALLY, so the caller
    prints a summary of a measurement that was killed mid-flight. Measured on the mutation
    2026-08-31: exactly that artifact, exactly that sentence.
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    supervisor = _supervisor(tmp_path, start_error=gate2.TerminatedBySignal(signal.SIGTERM))

    # Deliberately not `pytest.raises`: when the signal IS swallowed there is no exception to
    # inspect, and the artifact is the evidence that says what went wrong instead.
    raised: BaseException | None = None
    try:
        gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)
    except gate2.TerminatedBySignal as exc:
        raised = exc

    data = _artifact(artifacts_dir)
    assert raised is not None, (
        "sygnał kończący został POŁKNIĘTY przez `except Exception` kontroli nr 1 — "
        "`TerminatedBySignal` musi dziedziczyć po BaseException, nie po Exception. "
        f"Przebieg wyszedł normalnie, a artefakt zapisał kontrole "
        f"{[k['nazwa'] for k in data['kontrole']]} i szczegół "
        f"{data['przebieg']['szczegol']!r} — czyli pomiar, którego nikt nie wykonał"
    )
    assert data["kontrole"] == [], (
        "sygnał przyszedł przed zakończeniem kontroli nr 1, więc nie ma czego zapisać; "
        "kontrola z werdyktem byłaby zmyśleniem"
    )
    assert data["liczba_kontroli"] == 0
    assert data["zdane"] is False
    assert data["przebieg"]["zakonczony"] is None
    assert "TerminatedBySignal" in data["przebieg"]["szczegol"]
    assert "SIGTERM" in data["przebieg"]["szczegol"]


def test_a_terminating_signal_is_not_swallowed_by_the_teardown(
    tmp_path: Path, cdp_answers: None
) -> None:
    """The second `except Exception` on the way to the write: `_shut_down`.

    That handler is right by design — a teardown error is data, and it must not skip the
    artifact write — but it is `except Exception` and it runs INSIDE the `finally` that leads
    to the write, so it is the last thing standing between a signal and silence.

    With `BaseException` the signal passes through `_shut_down`, the end marker is never
    stamped, `_write_artifact` still runs from the innermost `finally`, and the exception
    reaches the caller.

    With `Exception` it is filed as `sprzątanie supervisor.stop: TerminatedBySignal: ...` —
    a note about a hiccup during cleanup — and `run_gate2` RETURNS NORMALLY. The operator's
    stop request disappears into a teardown remark. That the artifact happens to stay
    unclosed either way (a note in `szczegol` also blocks the end marker) is luck, not a
    guarantee: it is one refactor away from `zakonczony` being stamped over a killed run.
    """
    artifacts_dir = tmp_path / "gate2"
    _previous_go(artifacts_dir)
    # Call no. 1 is check no. 5 inside the body; call no. 2 is the teardown in the `finally`.
    supervisor = _supervisor(
        tmp_path,
        stop_error=gate2.TerminatedBySignal(signal.SIGHUP),
        stop_error_on_call=2,
    )

    raised: BaseException | None = None
    try:
        gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)
    except gate2.TerminatedBySignal as exc:
        raised = exc

    data = _artifact(artifacts_dir)
    szczegol = data["przebieg"]["szczegol"]
    assert raised is not None, (
        "sygnał kończący został POŁKNIĘTY przez `except Exception` w `_shut_down` — "
        "`TerminatedBySignal` musi dziedziczyć po BaseException, nie po Exception. "
        f"Przebieg wyszedł normalnie, a żądanie zatrzymania trafiło do szczegółu "
        f"sprzątania: {szczegol!r}"
    )
    assert "sprzątanie" not in szczegol, (
        f"sygnał zapisany jako usterka sprzątania: {szczegol!r} — to jest zdanie o "
        "nieudanym kroku teardownu, a nie o przebiegu przerwanym przez operatora"
    )
    # The write still happened from the innermost `finally`, and it kept the measurement.
    assert data["liczba_kontroli"] == len(GATE2_REQUIRED_CHECKS)
    assert [k["nazwa"] for k in data["kontrole"]] == list(GATE2_REQUIRED_CHECKS)
    assert data["przebieg"]["zakonczony"] is None
    assert data["zdane"] is False
    assert _passing_names(data) == set()


def test_terminated_by_signal_does_not_derive_from_exception() -> None:
    """The invariant stated as itself, so the mutation fails by name and not only by symptom.

    Its docstring in `gate2.py` calls the base class "the whole point". A property named that
    way and defended by nothing is the shape this project keeps getting burned by, so it gets
    an assertion of its own next to the two behavioural ones.
    """
    assert issubclass(gate2.TerminatedBySignal, BaseException)
    assert not issubclass(gate2.TerminatedBySignal, Exception), (
        "`TerminatedBySignal` dziedziczy po Exception — każdy `except Exception` między "
        "podniesieniem sygnału a zapisem artefaktu (kontrola nr 1 w `_run_gate2`, "
        "`_shut_down`) połknie go i zamieni żądanie zatrzymania w nieudaną kontrolę albo "
        "w notatkę o sprzątaniu. Zmiana klasy bazowej to zmiana kontraktu bramki nr 2."
    )


def test_gate2_runs_where_a_signal_handler_cannot_be_installed(
    tmp_path: Path, cdp_answers: None
) -> None:
    """Off the main thread `signal.signal` refuses, and gate 2 has to degrade, not die.

    The handler above is the whole reason a SIGTERM cannot skip the artifact write, and it
    can only be installed on the main thread — `signal.signal` raises `ValueError` anywhere
    else. Today `main.py` is the only caller and it is the main thread, but a run that
    measured all five checks must not be turned into a `ValueError` by the apparatus meant to
    protect it. What IS lost off-thread is the signal guarantee, and that limitation is
    written down beside the guarantee in `_write_artifact` rather than left to be discovered.
    """
    artifacts_dir = tmp_path / "gate2"
    supervisor = _supervisor(tmp_path)
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)
        # BaseException, not Exception: `TerminatedBySignal` and a `ValueError` from an
        # unarmed handler are different failures, and this test must be able to tell them
        # apart rather than let one of them kill the worker thread silently.
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="gate2-poza-glownym-watkiem")
    worker.start()
    worker.join(timeout=60)

    assert not worker.is_alive(), "przebieg zawisł poza wątkiem głównym"
    assert "error" not in outcome, (
        f"poza wątkiem głównym przebieg wywalił się: {outcome.get('error')!r} — instalacja "
        "handlera sygnałów nie może zabijać pomiaru, który poza tym się udał"
    )
    assert outcome["result"].zdane is True
    data = _artifact(artifacts_dir)
    assert data["zdane"] is True
    assert data["przebieg"]["zakonczony"] is not None


# ---------------------------------------------------------------------------
# The positive control: a finished run still has a verdict
# ---------------------------------------------------------------------------


def test_a_finished_run_is_written_as_a_closed_pass(tmp_path: Path, cdp_answers: None) -> None:
    """Without this test a "fix" that always wrote `zdane: false` would pass all the above."""
    artifacts_dir = tmp_path / "gate2"
    supervisor = _supervisor(tmp_path)

    result = gate2.run_gate2(supervisor, artifacts_dir=artifacts_dir)

    assert result.zdane is True
    data = _artifact(artifacts_dir)
    assert data["zdane"] is True
    assert data["liczba_kontroli"] == len(GATE2_REQUIRED_CHECKS)
    assert _passing_names(data) == set(GATE2_REQUIRED_CHECKS)
    assert data["przebieg"]["zakonczony"] is not None
    assert data["przebieg"]["szczegol"] == ""
    for check in data["kontrole"]:
        assert "wymaga_czlowieka" not in check["szczegoly"]
