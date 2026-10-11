"""Repository-wide consistency tests — what no single package will ever check.

They defend properties that are easy to break by accident with a change in one place:
a duplicated browser version, an exposed CDP port, an ADR with no test coverage,
a Go/No-Go report that treats a lack of evidence as a success.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Directories no scanner in this file may descend into.
#:
#: `node_modules` and `.next` join the list on 2026-08-31, BEFORE the Next.js dashboard of
#: D-014 exists, and the failure direction was measured rather than assumed: three scanners
#: here walk the whole tree (`*.py`, `__init__.py`, `test_*.py`), and a planted
#: `node_modules/…/browser.py` containing `--no-sandbox`, a planted `__all__` naming an unbound
#: symbol, and a planted `parametrize` mismatch under `.next/` each broke a different test.
#: Dependency code the repository did not write must not be able to fail the repository's own
#: conventions — and finding that out on the day the dashboard lands would mean debugging it
#: under time pressure, against generated files.
#:
#: The same names go into `[tool.ruff] extend-exclude` in the root pyproject.toml.
IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    ".pytest_cache",
    "var",
    "node_modules",
    ".next",
}


def _is_ignored(path: pathlib.Path) -> bool:
    return bool(IGNORED_DIRS & set(path.parts))


@pytest.fixture(scope="module")
def lock() -> dict:
    return tomllib.loads((ROOT / "versions.lock.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def test_build_files_contain_no_chrome_version() -> None:
    """DoD L5180: the exact browser version has ONE source of truth.

    We check the BUILD FILES — Dockerfiles, compose and the Makefile. They are the ones
    that decide what actually ends up in the image, and it is in them that a duplicated
    version silently drifts the build away from the capability snapshots.

    We do not check the code or the tests, and that is deliberate: `fetch_chrome.py`
    mentions 153.0.8001.0 in an error message (because it is the first version with
    linux-arm64), and the supervisor tests use 152.0.7977.64 precisely to prove that a
    mismatched build is REJECTED. Banning the literal there would ban describing reality.
    """
    pattern = re.compile(r"\b15\d\.\d+\.\d+\.\d+\b")
    candidates: list[pathlib.Path] = [
        *ROOT.rglob("Dockerfile"),
        ROOT / "docker-compose.yml",
        ROOT / "compose.test.yml",
        ROOT / "Makefile",
    ]

    violations: list[str] = []
    for path in candidates:
        if not path.exists() or _is_ignored(path):
            continue
        for found in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            violations.append(f"{path.relative_to(ROOT)}: {found}")

    assert not violations, (
        "Plik budujący zawiera literał wersji Chrome zamiast czytać go z versions.lock.toml: "
        + ", ".join(violations)
    )


def test_capsule_dockerfile_contains_no_chrome_version() -> None:
    """The version is to be read from the lock by fetch_chrome.py, not written in a layer."""
    dockerfile = (ROOT / "infra" / "capsule" / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"\b15\d\.\d+\.\d+\.\d+\b", dockerfile), (
        "Dockerfile kapsuły zawiera literał wersji Chrome — ma ją czytać z versions.lock.toml"
    )


def test_check_versions_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "dev" / "check_versions.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Ports — see tests/test_published_ports.py
# ---------------------------------------------------------------------------
#
# The whole port invariant lived here until 2026-08-31 and has moved to its own file. It
# grew two teeth after a red-team review (ports hidden behind a YAML anchor, and
# `network_mode: host`, which publishes everything with no `ports:` entry at all) and no
# longer fits between an ADR check and an `__all__` check. The parser itself now lives in
# `tools/dev/check_versions.py`, so `make check-versions` and the tests run ONE
# implementation instead of two that drift.


def test_no_cdp_or_vnc_port_is_published() -> None:
    """L2353, L5600: the public network has no access to CDP or to VNC.

    Kept here as a crude second lock: a literal text scan that does not depend on the
    compose parser being right. If the parser ever goes blind again, this still shouts.
    """
    for nazwa in ("docker-compose.yml", "compose.test.yml"):
        path = ROOT / nazwa
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for forbidden_port in ("9222", "5900", "5901"):
            pattern = re.compile(rf'^\s*-\s*"[^"]*:{forbidden_port}"', re.M)
            assert not pattern.search(content), f"{nazwa} publikuje port {forbidden_port}"


# ---------------------------------------------------------------------------
# Scope boundary
# ---------------------------------------------------------------------------


def test_no_sandbox_appears_nowhere_in_the_code() -> None:
    """`--no-sandbox` is forbidden; the only allowed occurrence is the ban list itself."""
    allowed_files = {
        "args.py",  # definition of the FORBIDDEN_FLAGS list
        "test_args_and_profile.py",  # test of that list
        "contract.py",  # check that tries to add the flag and verifies the rejection
        "test_repo_consistency.py",  # this file
    }
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        if _is_ignored(path):
            continue
        if path.name in allowed_files:
            continue
        if "--no-sandbox" in path.read_text(encoding="utf-8", errors="ignore"):
            violations.append(str(path.relative_to(ROOT)))

    assert not violations, (
        "`--no-sandbox` jest zakazany (sekcja 4 + wymóg sandboxa z 14.4). Wystąpienia: "
        + ", ".join(violations)
    )


# ---------------------------------------------------------------------------
# Go/No-Go report
# ---------------------------------------------------------------------------


def test_report_does_not_treat_missing_evidence_as_a_pass(tmp_path: pathlib.Path) -> None:
    """The most important test in this file.

    We run the generator on an EMPTY artifacts directory. No gate may then be given
    GO — a lack of evidence must yield `NIE_URUCHOMIONO`.
    """
    output_dir = tmp_path / "raport"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gonogo" / "report.py"),
            "--artifacts",
            str(tmp_path / "puste"),
            "--out",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    data = json.loads((output_dir / "gonogo.json").read_text(encoding="utf-8"))
    verdicts = {b["id"]: b["werdykt"] for b in data["bramki"]}

    assert verdicts, "raport nie zawiera żadnej bramki"
    assert "GO" not in verdicts.values(), (
        f"Bez artefaktów żadna bramka nie może mieć GO, a mamy: {verdicts}"
    )
    assert all(outcome == "NIE_URUCHOMIONO" for outcome in verdicts.values()), verdicts


def test_report_refuses_go_for_an_unclosed_run(tmp_path: pathlib.Path) -> None:
    """A mid-measurement artifact must never yield GO, even if it claims `zdane: true`.

    The multi-hour artifact is rewritten after every cycle, so the file on disk can come
    from the middle of the window — or from a run killed without a signal, where the
    handler that sets `zakonczony` never ran. Measured in production: 48 minutes into a
    six-hour window the artifact already held `zdane: true` for all six controls, with
    evidence quoting `planned_hours` as if it were elapsed time.

    `codex_runtime.unattended` now refuses that verdict (D-009), but the report must be
    robust against artifacts written by an OLDER build of that code — the container image
    is regularly older than the host sources. This test pins the report side.
    """
    checks = [
        "sdk_wystawia_model_effort_schema",
        "model_list_pelna_paginacja",
        "cztery_role_w_osobnych_watkach",
        "brak_shell_i_exec",
        "tylko_dozwolony_mcp",
    ]
    artifact = {
        "zdane": True,
        "liczba_kontroli": 6,
        "kontrole": [
            *({"nazwa": n, "zdane": True, "dowod": "ok", "szczegoly": {}} for n in checks),
            {
                "nazwa": "unattended_rozstrzygniete",
                "zdane": True,
                "dowod": "10 cykli przez 6.0 h, 1 restartów runtime'u, zero żądań.",
                "szczegoly": {
                    "przebieg": {
                        "rozpoczety": "2026-08-30T10:16:37+00:00",
                        "planned_hours": 6.0,
                        "cycles_run": 10,
                        "successful_cycles": 10,
                        "zakonczony": None,
                        "przerwany_przez_czlowieka": False,
                    }
                },
            },
        ],
    }
    gate_dir = tmp_path / "artefakty" / "gate3"
    gate_dir.mkdir(parents=True)
    (gate_dir / "codex-runtime.json").write_text(json.dumps(artifact), encoding="utf-8")

    output_dir = tmp_path / "raport"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gonogo" / "report.py"),
            "--artifacts",
            str(tmp_path / "artefakty"),
            "--out",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    data = json.loads((output_dir / "gonogo.json").read_text(encoding="utf-8"))
    gate3 = next(b for b in data["bramki"] if b["id"] == "GATE-3")

    assert gate3["werdykt"] != "GO", (
        "a run with no `zakonczony` is a lack of evidence — the report must not call it GO"
    )
    assert gate3["werdykt"] == "NIE_URUCHOMIONO", gate3


def test_report_says_the_phase_is_not_closed(tmp_path: pathlib.Path) -> None:
    output_dir = tmp_path / "raport"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gonogo" / "report.py"),
            "--artifacts",
            str(tmp_path / "puste"),
            "--out",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    markdown = (output_dir / "gonogo.md").read_text(encoding="utf-8")
    assert "nie jest zamknięta" in markdown
    assert "Czego ten raport NIE dowodzi" in markdown


def test_criteria_are_frozen_with_a_date() -> None:
    """Thresholds must be written before measurement and carry a freeze date (L4303, L4619)."""
    content = (ROOT / "tools" / "gonogo" / "criteria.yaml").read_text(encoding="utf-8")
    assert "zamrozone_dnia:" in content
    assert "GATE-1" in content and "GATE-2" in content and "GATE-3" in content


# ---------------------------------------------------------------------------
# ADRs
# ---------------------------------------------------------------------------


def test_every_adr_has_a_status_and_a_date() -> None:
    directory = ROOT / "docs" / "adr"
    if not directory.exists():
        pytest.skip("brak katalogu docs/adr")
    adr_files = sorted(directory.glob("ADR-*.md"))
    assert adr_files, "brak jakiegokolwiek ADR-a"
    for adr_file in adr_files:
        content = adr_file.read_text(encoding="utf-8")
        assert "**Status:**" in content, f"{adr_file.name} nie ma statusu"
        assert "**Data:**" in content, f"{adr_file.name} nie ma daty"


# ---------------------------------------------------------------------------
# Workspace structure
# ---------------------------------------------------------------------------


def test_every_workspace_member_has_a_pyproject() -> None:
    """`uv lock` fails on a member without pyproject.toml, blocking the whole workspace."""
    root_pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = root_pyproject["tool"]["uv"]["workspace"]["members"]

    braki: list[str] = []
    for pattern in patterns:
        for directory in sorted(ROOT.glob(pattern)):
            if directory.is_dir() and not (directory / "pyproject.toml").exists():
                braki.append(str(directory.relative_to(ROOT)))

    assert not braki, f"członkowie workspace'u bez pyproject.toml: {braki}"


#: Top-level source trees that the consistency suites READ AT RUNTIME and that the tests image
#: must therefore contain in full. Partially copied trees (`fixtures/site`, `docs/adr`,
#: `tools/gonogo`) are deliberately absent from this list — the Dockerfile copies exactly the
#: files those suites touch, and demanding the whole tree would be a rule nobody follows.
#:
#: `apps` is on the list although it does not exist yet: it is the Next.js dashboard of D-014,
#: and `is_dir()` below skips it until it lands — at which point this test starts failing and
#: the commented-out `COPY` line in tests/Dockerfile has to be uncommented. That is the point.
#: A `COPY` of a non-existent path is a hard build error, so the line cannot be added early;
#: without this test the comment asking for it would be a convention nobody enforces, which is
#: exactly how the port invariant went unchecked for months (D-014).
SOURCE_TREES_THE_SUITES_READ = ("contracts", "evals", "services", "apps", "tests", "db")


def test_the_tests_image_copies_every_source_tree_the_suites_read() -> None:
    """`make test` runs these suites INSIDE a container, over whatever that image contains.

    Until 2026-08-31 the image had no `services/` in it at all, so every scanner in this file
    ran in the container over a tree with no service code — and came out green over nothing.
    Green over nothing is the shape of every false pass this project has had.
    """
    dockerfile = (ROOT / "tests" / "Dockerfile").read_text(encoding="utf-8")

    copied: set[str] = set()
    for line in dockerfile.splitlines():
        stripped = line.strip()
        # A commented-out COPY copies nothing. Counting it would make the test satisfiable by
        # a comment, which is how a check stops checking.
        if stripped.startswith("#") or not stripped.upper().startswith("COPY "):
            continue
        arguments = [
            token
            for token in stripped.split()[1:]
            if not token.startswith("--") and not token.startswith("[")
        ]
        copied.update(arguments[:-1])  # every argument but the destination

    present = [tree for tree in SOURCE_TREES_THE_SUITES_READ if (ROOT / tree).is_dir()]
    assert present, "żadne z drzew źródłowych nie istnieje — ten test nic nie sprawdza"
    assert copied, "w tests/Dockerfile nie ma ANI JEDNEJ instrukcji COPY — parser oślepł"

    missing = [tree for tree in present if tree not in copied]
    assert not missing, (
        f"tests/Dockerfile nie kopiuje drzew: {missing}. Suity spójności czytają je w trakcie "
        "działania, więc w kontenerze przeskanują pustkę i przejdą — zielone nad niczym"
    )


def test_every_all_entry_is_actually_bound() -> None:
    """A package must not claim exports it does not have.

    Checked statically, because each package lives in its own virtualenv and this suite
    runs in only one of them.

    Found by the D-010 migration, but PRE-DATING it: at the first commit `__all__` in
    `mcp_sidecar` listed 11 names and `codex_runtime` 16 that had NEVER been defined
    anywhere in the repo — Polish names left over after an earlier rename to English that
    updated the modules and the import lines but not `__all__`. `from <pkg> import *`
    raised AttributeError the whole time and no test noticed.

    `docs/DECYZJE.md` D-005 cited one of those phantoms (`DOZWOLONE_METODY`) as the proof
    that L2222 is satisfied. The mechanism was real — the constant is `ALLOWED_METHODS` —
    but the decision log pointed at a name that did not exist.
    """
    import ast

    problems: list[str] = []
    for init in ROOT.rglob("__init__.py"):
        if _is_ignored(init):
            continue
        tree = ast.parse(init.read_text(encoding="utf-8"))
        exported: list[str] = []
        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                exported = [
                    e.value
                    for e in getattr(node.value, "elts", [])
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
            elif isinstance(node, ast.alias):
                bound.add(node.asname or node.name.split(".")[-1])
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                bound.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)

        for name in exported:
            if name not in bound:
                problems.append(
                    f"{init.relative_to(ROOT)}: __all__ ma '{name}', ale nic go nie wiąże"
                )

    assert not problems, "\n".join(problems)


def test_parametrize_argnames_match_the_function_signature() -> None:
    """`parametrize` binds arguments BY THE STRING, so a rename can silently miss it.

    `docs/KONWENCJE.md` has warned about this since the beginning, and pointed at this
    file as the place where the scanner lives — but no such scanner existed. The D-010
    migration then hit exactly this: nine `parametrize` sites kept Polish argument names
    while the function signatures had been renamed, in two different spellings
    (a comma-separated string, and a tuple of strings).

    pytest does report it, but only as a collection error that aborts the whole module,
    one file at a time. This lists every mismatch in the repo at once.
    """
    import ast

    problems: list[str] = []
    for path in ROOT.rglob("test_*.py"):
        if _is_ignored(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                func = decorator.func
                if (
                    func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                ) != "parametrize":
                    continue

                first = decorator.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names = [s.strip() for s in first.value.split(",") if s.strip()]
                elif isinstance(first, ast.List | ast.Tuple):
                    names = [
                        e.value
                        for e in first.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
                else:
                    continue  # computed argnames — out of reach of a static check

                for name in names:
                    if name not in params:
                        problems.append(
                            f"{path.relative_to(ROOT)}:{node.lineno} "
                            f"{node.name}: parametrize podaje '{name}', "
                            f"a sygnatura ma {sorted(params)}"
                        )

    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Documents that cite a test as proof
# ---------------------------------------------------------------------------
#
# `check_docs.py` verifies FILE PATHS in the documents, never names inside backticks — which
# is how three phantom names survived for months (D-005 cited `DOZWOLONE_METODY`, a constant
# that never existed under that name). The D-010 rename then created 34 more in one commit:
# `controls.yaml` names a test function as the evidence for each threat-model control, and
# the rename updated only the `test:` path fields, because those were the only ones a test
# was watching. A control whose evidence points at a function that does not exist is worse
# than a control with no evidence: it reads as covered.


def _test_functions_in_the_repo() -> set[str]:
    import ast

    names: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if _is_ignored(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        names |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
    return names


def _documents_that_cite_tests() -> list[pathlib.Path]:
    seen: list[pathlib.Path] = []
    for pattern in ("docs/**/*.md", "docs/**/*.yaml", "*.md", "**/NOTES.md"):
        for path in ROOT.glob(pattern):
            if not _is_ignored(path) and path not in seen:
                seen.append(path)
    return seen


def test_every_test_name_cited_in_a_document_exists() -> None:
    """A document naming a test as its proof must name one that exists.

    Module basenames (`test_lease_fencing`) are excluded — those are file references, not
    function references, and `check_docs.py` already verifies paths.
    """
    live = _test_functions_in_the_repo()
    modules = {path.stem for path in ROOT.rglob("test_*.py")}

    problems = []
    for document in _documents_that_cite_tests():
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for cited in sorted(set(re.findall(r"\btest_[a-zA-Z0-9_]+", text))):
            if cited not in live and cited not in modules:
                problems.append(f"{document.relative_to(ROOT)}: cytuje nieistniejący {cited}")

    assert not problems, (
        "dokument wskazuje na test, którego nie ma — dowód wskazuje w próżnię:\n"
        + "\n".join(problems)
    )


def test_the_convention_meter_actually_detects_polish_names() -> None:
    """The meter must fail on injected Polish, not merely pass on a clean repo.

    Its predecessor read 0 while 36 Polish names were present: a closed list of 26 word stems,
    chosen against the pre-migration code, matched none of what the migration actually left
    behind. A meter that cannot fail proves nothing, so this asserts the failure direction —
    the only direction a green run never exercises.
    """
    sys.path.insert(0, str(ROOT / "tools" / "dev"))
    from check_docs import polish_part

    must_detect = [
        "sciezka_pliku",
        "liczba_watkow",
        "wartosc_domyslna",
        "wynik_koncowy",
        "zbior_odrzuconych",
        "ARCH_NA_PLATFORME",
        "PROG_POKRYCIA",
        "test_rejestr_jest_spojny",
        "maszyna",
        "pobranie",
    ]
    missed = [name for name in must_detect if not polish_part(name)]
    assert not missed, f"miernik NIE widzi polskich nazw: {missed}"

    must_ignore = [
        "test_run_in_progress_is_never_a_pass",
        "shell_methods_all_rejected",
        "ALLOWED_METHODS",
        "exactly_one_of_value_or_reason",
        "reattach",
        "timezone",
        "platform",
        "probability",
        "slowly",
        "progress",
    ]
    false_alarms = [(name, polish_part(name)) for name in must_ignore if polish_part(name)]
    assert not false_alarms, f"miernik daje fałszywe alarmy: {false_alarms}"
