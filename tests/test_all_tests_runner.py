"""`make test-all` must not be able to pass a suite it never ran.

Until 2026-08-31 the missing-interpreter branch of `tools/dev/run_all_tests.sh` appended
a "POMINIĘTE" row and did a bare `return` — without touching the `failures` counter. A
suite whose `.venv` had never been built was therefore skipped in silence, and the script
still exited 0 with "Wszystkie suity przeszły". That is the failure mode named in
docs/KONWENCJE.md L110-111: "Polecenie, które kończy się kodem 0, nie znaczy, że coś
zrobiło. (...) `make build` przez wiele tygodni budował 4 z 8 obrazów i wychodził na
zero." Phase E1.F1 adds new service packages, so the whole phase could have "passed"
without starting a single one of its own tests.

Every test here runs the REAL script against a FAKE repository root. The script reads
`REPO_ROOT` with a fallback to its own location, so pointing it at a throwaway tree is
enough; nothing in this file touches the eight real `.venv` trees of this repository.
The fake interpreters are `/bin/sh` stubs that exit 0, which is all `run_suite` needs:
it checks `-x` and then runs `"$python_bin" -m pytest ...`.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "dev" / "run_all_tests.sh"

# The tests image (tests/Dockerfile) copies only `tools/dev/check_versions.py` out of
# tools/dev, so the runner is genuinely absent inside the container. Reporting that as a
# skip is honest — a skip is never counted as a pass, and the host `make test-all` (the
# thing these tests guard) always has the file.
pytestmark = pytest.mark.skipif(
    not RUNNER.is_file(),
    reason=f"brak {RUNNER} — skrypt hostowy nie jest kopiowany do obrazu tests",
)


#: Every package whose `.venv/bin/python` the runner needs, in the runner's own order.
#: `contracts` serves two suites (its own and "spójność repo"), hence appearing once.
def suite_packages() -> tuple[str, ...]:
    """Package directories the runner expects a `.venv` in, DERIVED from the runner itself.

    This list used to be a hardcoded constant, and phase E1.F1 broke it the moment it added
    `run_suite` lines for `services/db_schema` and `services/control_plane`: the fake root
    built without those two `.venv`s made the runner report BRAK ŚRODOWISKA, so three tests
    of the runner failed for a reason that had nothing to do with what they test.

    Deriving is not merely convenient here. A constant would keep passing while drifting away
    from the script, which is the exact failure this file exists to prevent — the same reason
    `test_published_ports.py` globs the repository instead of trusting a list.
    """
    body = RUNNER.read_text(encoding="utf-8")
    # run_suite "<name>" "$ROOT/<dir>" "$ROOT/<dir>/.venv/bin/python" ...
    found = tuple(dict.fromkeys(re.findall(r'"\$ROOT/([^"]+)/\.venv/bin/python"', body)))
    # Counted separately from the predicate: a regex that stopped matching would otherwise
    # build an empty fake root, every suite would report BRAK ŚRODOWISKA, and the tests below
    # would still look meaningful. `all([])` has already produced false passes here (D-011).
    assert len(found) >= 8, (
        f"ze skryptu wyprowadzono {len(found)} pakietów, a wierszy run_suite jest więcej — "
        "wzorzec przestał pasować i atrapa repo byłaby pusta"
    )
    return found


def js_suite_dirs() -> tuple[str, ...]:
    """Directories driven by `run_js_suite`, DERIVED from the runner for the same reason.

    The dashboard is the first non-Python suite in this repository. Hardcoding it here would
    repeat the mistake `suite_packages()` above documents.
    """
    body = RUNNER.read_text(encoding="utf-8")
    # run_js_suite "<name>" "$ROOT/<dir>"
    found = tuple(
        dict.fromkeys(re.findall(r'^run_js_suite\s+"[^"]+"\s+"\$ROOT/([^"]+)"', body, re.M))
    )
    assert found, (
        "ze skryptu nie wyprowadzono żadnego katalogu run_js_suite — wzorzec przestał pasować, "
        "a atrapa repo nie zaspokoiłaby gałęzi JS"
    )
    return found


SUITE_PACKAGES = suite_packages()

#: Line the runner prints once the suite list is done. Used to splice extra declarations
#: into a copy of the script. If it ever disappears, the tests below fail loudly instead
#: of silently testing nothing.
ANCHOR = r"printf '\n=== spójność dokumentacji z kodem ===\n'"


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repository root that satisfies the runner without running any real test."""
    root = tmp_path / "fake-repo"
    for package in SUITE_PACKAGES:
        _write_executable(root / package / ".venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    # The runner also shells out to these two; stub both so that a non-zero exit code in
    # a test can only come from the suite handling under test.
    (root / "tools" / "dev").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "dev" / "check_docs.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    _write_executable(root / "tools" / "dev" / "run_lease_tests.sh", "#!/bin/sh\nexit 0\n")
    for directory in js_suite_dirs():
        # `run_js_suite` refuses on three separate grounds — no directory, no executable
        # `tools/check.sh`, no `node_modules` — and each of them counts as a failure. The fake
        # root has to satisfy all three or every test in this file fails for a reason that has
        # nothing to do with what it tests, which is exactly what happened when the dashboard
        # suite landed.
        _write_executable(root / directory / "tools" / "check.sh", "#!/bin/sh\nexit 0\n")
        (root / directory / "node_modules").mkdir(parents=True, exist_ok=True)
    return root


def _run(root: pathlib.Path, script: pathlib.Path = RUNNER) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["REPO_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def _patched_script(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    copy = tmp_path / "run_all_tests_patched.sh"
    copy.write_text(text, encoding="utf-8")
    return copy


def test_control_full_environment_passes(tmp_path: pathlib.Path) -> None:
    """Control: with every environment present the runner still reports success.

    Without this, a runner that failed unconditionally would satisfy every other test
    in this file.
    """
    result = _run(_fake_root(tmp_path))
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"pełne środowisko, a runner padł:\n{output}"
    assert "Wszystkie suity przeszły." in output


def test_missing_environment_is_a_failure(tmp_path: pathlib.Path) -> None:
    """The regression proper: an unbuilt `.venv` must make the whole run fail."""
    root = _fake_root(tmp_path)
    missing = root / "services" / "stream_gateway" / ".venv" / "bin" / "python"
    missing.unlink()

    result = _run(root)
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"suita bez środowiska została pominięta po cichu, a runner wyszedł na zero:\n{output}"
    )
    assert "Wszystkie suity przeszły." not in output
    assert "BRAK ŚRODOWISKA" in output, "podsumowanie musi odróżniać brak środowiska od porażki"
    assert "stream_gateway" in output


def test_missing_suite_directory_is_a_failure(tmp_path: pathlib.Path) -> None:
    """A suite directory that does not exist is the same accident, one level up."""
    root = _fake_root(tmp_path)
    shutil.rmtree(root / "evals")

    result = _run(root)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"brak katalogu suity nie zatrzymał runnera:\n{output}"
    assert "Wszystkie suity przeszły." not in output
    assert "evals" in output


def test_missing_environment_is_distinguished_from_a_red_suite(tmp_path: pathlib.Path) -> None:
    """PADŁO and BRAK ŚRODOWISKA need different labels — they need different repairs."""
    root = _fake_root(tmp_path)
    (root / "services" / "mcp_sidecar" / ".venv" / "bin" / "python").unlink()
    # A suite that runs and reports red, so both labels appear in the same summary.
    _write_executable(root / "contracts" / ".venv" / "bin" / "python", "#!/bin/sh\nexit 1\n")

    result = _run(root)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "BRAK ŚRODOWISKA" in output
    assert "PADŁO" in output
    assert "Suit z porażką: 3" in output, (
        "trzy pozycje mają być czerwone: mcp_sidecar bez środowiska oraz dwie suity "
        f"na interpreterze contracts:\n{output}"
    )


def test_declared_skip_is_not_a_failure(tmp_path: pathlib.Path) -> None:
    """A skip written down by name, with a reason, may stay green — that is the point.

    The accidental skip is the bug; the conscious one (a suite needing a live ChatGPT
    account) has to remain expressible, otherwise the fix just gets reverted later.
    """
    text = RUNNER.read_text(encoding="utf-8")
    assert ANCHOR in text, f"kotwica {ANCHOR!r} zniknęła ze skryptu — test nie ma czego sprawdzić"
    declared = 'skip_suite "przykładowa-live" "wymaga zalogowanego konta ChatGPT"\n\n'
    script = _patched_script(tmp_path, text.replace(ANCHOR, declared + ANCHOR, 1))

    result = _run(_fake_root(tmp_path), script=script)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"zadeklarowane pominięcie nie może psuć przebiegu:\n{output}"
    assert "POMINIĘTE" in output
    assert "wymaga zalogowanego konta ChatGPT" in output, "powód musi trafić do podsumowania"
    assert "Wszystkie suity przeszły." in output


def test_skip_without_a_reason_is_a_failure(tmp_path: pathlib.Path) -> None:
    """A nameless or reasonless skip is the old silent skip wearing a new hat."""
    text = RUNNER.read_text(encoding="utf-8")
    assert ANCHOR in text, f"kotwica {ANCHOR!r} zniknęła ze skryptu — test nie ma czego sprawdzić"
    script = _patched_script(
        tmp_path, text.replace(ANCHOR, 'skip_suite "bez-powodu"\n\n' + ANCHOR, 1)
    )

    result = _run(_fake_root(tmp_path), script=script)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"pominięcie bez powodu przeszło bez śladu:\n{output}"
    assert "Wszystkie suity przeszły." not in output


def test_run_that_starts_no_suite_is_not_a_pass(tmp_path: pathlib.Path) -> None:
    """`all([]) == True` has cost this project two false passes already.

    A runner whose suite list has evaporated must not report a green run just because
    nothing was left to be red.
    """
    text = RUNNER.read_text(encoding="utf-8")
    without_suites, replaced = re.subn(r"(?m)^run_suite ", "# run_suite ", text)
    assert replaced >= 8, f"spodziewano się co najmniej 8 wywołań run_suite, znaleziono {replaced}"
    script = _patched_script(tmp_path, without_suites)

    result = _run(_fake_root(tmp_path), script=script)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"przebieg bez jednej suity ogłosił sukces:\n{output}"
    assert "ANI JEDNEJ" in output
    assert "Wszystkie suity przeszły." not in output
