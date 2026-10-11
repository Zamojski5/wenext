"""Regression: the mere fact of being logged in does NOT pass the unattended work check.

The error this file guards against was more dangerous than a failure: gate3 reported 6/6
immediately after the login, before the multi-hour measurement had even started.
The Go/No-Go report would then announce GATE-3 = GO without any evidence whatsoever — that is,
a confident, wrong verdict. This is exactly the class of mistake the gate protects against.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

GATE3 = Path(__file__).resolve().parents[1] / "gate3.py"


def _check_source() -> str:
    """Cuts out the body of the `unattended_rozstrzygniete` function from gate3.py."""
    content = GATE3.read_text(encoding="utf-8")
    start = content.index("def unattended_rozstrzygniete")
    koniec = content.index("_run_check(", start)
    return content[start:koniec]


def test_check_does_not_return_true_after_login_alone() -> None:
    """In the whole function there must be no path returning `True`.

    The only thing that decides is `codex_runtime.unattended`, which overwrites this entry
    after an actual run.
    """
    source = _check_source()
    true_returns = re.findall(r"return\s*\(\s*\n\s*True\b", source)
    assert not true_returns, (
        "gate3 zalicza kontrolę pracy bez nadzoru bez wykonania pomiaru — to daje GO bez dowodu"
    )


def test_check_marks_itself_as_requiring_a_human() -> None:
    """Without this marker the report would turn a missing measurement into NO_GO, not NIE_URUCHOMIONO."""
    assert '"wymaga_czlowieka": True' in _check_source()


def test_only_unattended_may_pass_this_check() -> None:
    """The only place allowed to write `zdane=True` for this check is unattended."""
    from codex_runtime import unattended

    source = inspect.getsource(unattended._write_artifact)
    assert '"nazwa": "unattended_rozstrzygniete"' in source
    assert '"zdane": bool(zdane)' in source, (
        "unattended musi zapisywać PRAWDZIWY werdykt przebiegu, nie stałą"
    )


# --- brak_shell_i_exec: nic nie zablokowane ≠ wszystko zablokowane -----------------
#
# These exercise the real decision function with real inputs, instead of grepping the
# source the way the checks above have to. The failure they guard against is the fifth
# false pass of this class in this module: `all()` over an empty mapping is True, so a
# control meant to prove the L2222 boundary holds passed precisely when it had vanished.

SHELL_METHODS = ("thread/shellCommand", "command/exec", "process/spawn")
REJECTED = "MetodaNiedozwolona: method outside the allowlist"
PASSED = "PRZESZŁA PRZEZ ALLOWLIST: wywołanie zwróciło wynik bez wyjątku"


def test_nothing_recorded_is_not_a_pass() -> None:
    """The regression itself: an empty mapping means no method was blocked, not all of them.

    This is the state the code produced when every shell call SUCCEEDED — the allowlist was
    gone, nothing raised, nothing was recorded, and the control announced a pass.
    """
    from gate3 import shell_methods_all_rejected

    assert not shell_methods_all_rejected({}, SHELL_METHODS)


def test_every_method_rejected_is_a_pass() -> None:
    from gate3 import shell_methods_all_rejected

    assert shell_methods_all_rejected(dict.fromkeys(SHELL_METHODS, REJECTED), SHELL_METHODS)


def test_one_method_getting_through_is_not_a_pass() -> None:
    from gate3 import shell_methods_all_rejected

    outcomes = dict.fromkeys(SHELL_METHODS, REJECTED)
    outcomes["command/exec"] = PASSED
    assert not shell_methods_all_rejected(outcomes, SHELL_METHODS)


def test_a_method_missing_from_the_record_is_not_a_pass() -> None:
    """A partial record must not pass: an unrecorded method is an unproven one."""
    from gate3 import shell_methods_all_rejected

    outcomes = dict.fromkeys(SHELL_METHODS, REJECTED)
    del outcomes["process/spawn"]
    assert not shell_methods_all_rejected(outcomes, SHELL_METHODS)


def test_the_loop_records_an_outcome_even_when_the_call_succeeds() -> None:
    """Without the `else` branch the successful call is the one that leaves no trace.

    The count check alone would not save us if the loop simply skipped the method, so the
    two locks are tested separately: this one asserts the loop cannot stay silent.
    """
    source = GATE3.read_text(encoding="utf-8")
    start = source.index("def brak_shell_i_exec")
    body = source[start : source.index("config_read = client.request", start)]
    assert "else:" in body, (
        "petla musi zapisac wynik takze wtedy, gdy metoda shell PRZESZLA — "
        "inaczej udane wywolanie nie zostawia sladu i kontrola zalicza sie pusta"
    )
