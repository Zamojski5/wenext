"""`check_docs.py` in a public export of the code, where the project log does not exist.

The public export (`scripts/public/export.sh`) ships the code without the private project log
(`STAN-PROJEKTU.md`, `docs/STAN-PRAC.md`, `docs/DECYZJE.md`). The tool then has nothing to compare
with the code. Reporting every missing document as drift would make `make test-all` red on every
export for a reason nobody can act on, and printing the plain "everything agrees" line would be
the false pass this repository keeps paying for. So the tool must say NIE_SPRAWDZONO, exit 0, and
still fail under `--require-artifacts`.

The check runs on a bare copy of the tool in a temporary directory, so it holds on the working
repository (which has the log) and on an export (which does not) alike.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ("check_docs.py", "rename_identifiers.py")

#: The line the tool prints only when every control had evidence. It must never appear here.
UNQUALIFIED_OK = "OK " + chr(0x2014) + " dokumenty zgadzają się z kodem"


def _bare_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    tools = tmp_path / "tools" / "dev"
    tools.mkdir(parents=True)
    for name in TOOLS:
        shutil.copy(REPO_ROOT / "tools" / "dev" / name, tools / name)
    return tmp_path


def _run(root: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "dev" / "check_docs.py"), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_checkout_without_the_project_log_is_not_checked_and_not_failed(
    tmp_path: pathlib.Path,
) -> None:
    result = _run(_bare_checkout(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NIE_SPRAWDZONO" in result.stdout
    assert UNQUALIFIED_OK not in result.stdout
    assert "ROZJAZD" not in result.stdout


def test_the_strict_flag_still_fails_a_checkout_without_the_project_log(
    tmp_path: pathlib.Path,
) -> None:
    result = _run(_bare_checkout(tmp_path), "--require-artifacts")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NIE_SPRAWDZONO" in result.stdout


def test_one_project_log_document_is_enough_to_run_the_real_checks(
    tmp_path: pathlib.Path,
) -> None:
    """The skip is for a checkout with NONE of the log. With one document present the tool runs
    its checks, which here find the other required documents missing."""
    root = _bare_checkout(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "STAN-PRAC.md").write_text("# Stan prac\n", encoding="utf-8")
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ROZJAZD DOKUMENTACJI Z KODEM" in result.stdout
