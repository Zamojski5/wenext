"""Regression: a run may only be reported as passed once it actually happened.

Four errors of the same class have now been found in this module, and every one of them
produced a FALSE PASS rather than a visible failure:

  1. gate3 reported 6/6 right after login, without performing the measurement,
  2. waiting for login did not pump the channel and hung despite a successful login,
  3. `successful_cycles` grew before the resume probe, so a cycle ending in an exception
     still entered the statistics as successful,
  4. `_werdykt` had no notion of "run still in progress": the artifact is written after
     every cycle, so 48 minutes into a six-hour window it already declared
     `zdane: true` with the evidence string "10 cykli przez 6.0 h" — where `6.0` was
     `planned_hours`, a declaration made at startup, not anything measured.

Common denominator: the system claimed something had passed when it had not.

Case 4 is the dangerous one operationally, because the artifact is the only thing
`tools/gonogo/report.py` reads. A run killed without a signal (SIGKILL, OOM, Docker
dying) never reaches the code that sets `zakonczony`, so without the guard the last
mid-run write stays on disk permanently, claiming GO.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from codex_runtime import unattended
from codex_runtime.unattended import UnattendedRun
from codex_runtime.unattended import _verdict as _verdict

#: Fixed start instant — the verdict must not depend on the wall clock.
_START = datetime(2026, 8, 30, 10, 16, 37, tzinfo=UTC)


def _run(
    *,
    cycles: int,
    successful: int,
    reauth: int = 0,
    interrupted: bool = False,
    finished: bool = True,
    planned_hours: float = 1.0,
    measured_hours: float | None = None,
) -> UnattendedRun:
    """Builds a run.

    `finished=False` reproduces a write performed WHILE the measurement is running —
    which is the normal case, since the artifact is rewritten after every cycle.
    `measured_hours` defaults to the full planned window.

    Field names of `UnattendedRun` stay Polish on purpose: `asdict()` serialises them
    straight into the gate artifact, so they are data keys, not identifiers.
    """
    run = UnattendedRun(rozpoczety=_START.isoformat(), planned_hours=planned_hours)
    run.cycles_run = cycles
    run.successful_cycles = successful
    run.reauth_wymagane = reauth
    run.przerwany_przez_czlowieka = interrupted
    if finished:
        elapsed = planned_hours if measured_hours is None else measured_hours
        run.zakonczony = (_START + timedelta(hours=elapsed)).isoformat()
    return run


# --- the core of the fourth regression ---------------------------------------------


def test_run_in_progress_is_never_a_pass() -> None:
    """The exact production case: 10 successful cycles, 48 minutes into a 6 h window."""
    passed, evidence = _verdict(_run(cycles=10, successful=10, finished=False, planned_hours=6.0))
    assert passed is None, (
        "a run still in progress must be unresolved — the artifact is written after every "
        "cycle, so returning True here declares GO minutes into a six-hour window"
    )
    assert "nie został domknięty" in evidence


def test_run_killed_without_signal_is_not_a_pass() -> None:
    """SIGKILL / OOM / Docker dying: no signal handler runs, so no interruption flag.

    Only the missing `zakonczony` distinguishes this from a completed run. Without the
    guard the artifact left on disk claims GO forever.
    """
    passed, _ = _verdict(
        _run(cycles=10, successful=10, finished=False, interrupted=False, planned_hours=6.0)
    )
    assert passed is None


def test_shortened_window_is_not_a_pass() -> None:
    """A closed but shortened window does not prove multi-hour unattended operation."""
    passed, evidence = _verdict(
        _run(cycles=10, successful=10, planned_hours=6.0, measured_hours=0.8)
    )
    assert passed is None, "a shortened window is a lack of result, not a negative result"
    assert "0.80 h" in evidence and "6.0 h" in evidence


def test_evidence_quotes_measured_hours_not_planned() -> None:
    """The evidence string must carry what was measured, never what was declared."""
    _, evidence = _verdict(_run(cycles=74, successful=74, planned_hours=6.0, measured_hours=6.5))
    assert "6.50 h" in evidence, "evidence must quote measured time"
    assert "ZMIERZONYCH" in evidence


def test_full_window_passes() -> None:
    passed, _ = _verdict(_run(cycles=12, successful=12, planned_hours=6.0, measured_hours=6.0))
    assert passed is True


def test_clock_skew_within_tolerance_still_passes() -> None:
    """Wall-clock skew of a few seconds must not turn a full window into a non-result."""
    skew = unattended.WINDOW_TOLERANCE_S / 2 / 3600
    passed, _ = _verdict(
        _run(cycles=72, successful=72, planned_hours=6.0, measured_hours=6.0 - skew)
    )
    assert passed is True


# --- earlier regressions of the same class -----------------------------------------


def test_incomplete_success_count_is_no_go() -> None:
    passed, evidence = _verdict(_run(cycles=12, successful=11))
    assert passed is False
    assert "11/12" in evidence


def test_reauth_is_no_go_regardless_of_counter() -> None:
    passed, evidence = _verdict(_run(cycles=12, successful=12, reauth=1))
    assert passed is False
    assert "ponownego logowania" in evidence


def test_human_interruption_is_unresolved_not_no_go() -> None:
    passed, _ = _verdict(_run(cycles=5, successful=5, interrupted=True))
    assert passed is None, "a human interruption is a lack of result, not a negative result"


def test_success_counter_grows_only_after_a_whole_cycle() -> None:
    """Checked against the source, because a full cycle needs a live app-server."""
    src = inspect.getsource(unattended.main)
    assert "cycle_ok = False" in src
    assert "if cycle_ok:\n            przebieg.successful_cycles += 1" in src, (
        "the successful-cycle counter must grow AFTER try/except, otherwise a cycle that "
        "raised counts as successful"
    )
    counter_at = src.index("przebieg.successful_cycles += 1")
    except_at = src.index("except Exception")
    assert counter_at > except_at, "the counter still grows before exception handling"


def test_resume_probe_builds_registry_with_every_argument() -> None:
    """`ThreadRegistry` needs five arguments — a missing one is a TypeError hours into
    a multi-hour run, i.e. a defect found after hours instead of immediately."""
    src = inspect.getsource(unattended._check_resume)
    for required in (
        "client=",
        "codex_connection_id=",
        "workspace_id=",
        "tool_manifest_version=",
        "security_policy_version=",
    ):
        assert required in src, f"missing argument {required} when building ThreadRegistry"
