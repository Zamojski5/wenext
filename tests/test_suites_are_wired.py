"""A package with tests that no runner ever starts is a package with no tests.

`make test-all` prints a verdict over the suites listed in `tools/dev/run_all_tests.sh`. That
list is hand-written, and a hand-written list of things to run is a list somebody forgets — the
new package's tests are green on the author's machine, absent from the summary, and the run
that omits them says "Wszystkie suity przeszły".

This is the same failure as the one repaired in the runner on 2026-08-31 (a missing `.venv` was
skipped in silence), one level up: there the suite existed and was not started, here the suite
exists and was never even named. Phase E1.F1 adds two service packages, so the hole is not
hypothetical — it is exactly the size of this phase.

WHY THIS FILE WAS REWRITTEN THE SAME DAY IT WAS WRITTEN (pakiet FIX-3).
The first version asked whether the package's path occurred ANYWHERE in the runner
(`name in text`). That is satisfied by a comment, by a `printf` header, by a `# TODO`. The
agent that added the two E1.F1 packages satisfied it in the same commit that wrote it, so the
check never once separated a wired suite from a mentioned one. A check a comment can satisfy
guards nothing — this is the ninth false pass in this project's history and the same disease as
`all([]) == True` (D-011): the assertion held while measuring nothing.

What "wired" means here now — three shapes, and every one of them is a COMMAND bash executes,
never a string that merely appears in the file:

  1. a `run_suite` call whose directory argument plus pytest target land inside the package.
     Parsed positionally out of the call, so `$ROOT` and the pytest flags are understood.
     The repo-level suite ("spójność repo", directory `$ROOT`, target `tests`) is recognised
     by this rule too — the previous version had to exempt `tests` by name;
  2. a `skip_suite` call whose SUITE NAME OPENS with the package's own path
     (`skip_suite "services/x (cała suita)" "powód"`). A conscious skip stays expressible
     (that is the point of `skip_suite`), but it has to name the directory it skips, otherwise
     "declared" collapses back into "mentioned";
  3. a `.sh` script under `tools/dev/` that the runner actually executes, which PASSES the
     package path as a command argument. This is how `services/profile_lease` is wired: the
     runner runs `tools/dev/run_lease_tests.sh`, which passes `services/profile_lease` to
     `run_db_tests.sh`. The descent is ONE level deep and gives credit at script granularity —
     a script that passes a package and then does not run its tests would fool this rule, but
     the script's exit code is counted by the runner, which no comment ever is.

Everything else — a name in a comment, in a `printf`, in a docstring-ish header block — is
"forgotten", and `test_a_package_named_only_in_a_comment_counts_as_forgotten` plus
`test_the_lease_suite_is_wired_by_its_script_not_by_the_printf_that_names_it` are the reverse
controls that were watched failing before the parser below existed.

AND WHY IT WAS REWRITTEN A SECOND TIME (pakiet FIX-F, the same day again).
FIX-3 shut "the name occurs anywhere in the FILE" and left a narrower door open in its place:
the matcher behind rules 2 and 3 searched for the package path anywhere INSIDE a shell word,
and rule 2 ran it over EVERY argument of `skip_suite`. So PROSE satisfied the check — the
reason a founder reads for skipping suite A wired package B, which has no runner line at all.
Same false pass, same consequence, one layer down. Rules 2 and 3 now credit a path only as a
whole word or as the LEADING TOKEN of one, and rule 2 reads the suite name only, never the
reason. Reverse controls: `test_prose_in_a_skip_suite_argument_never_wires_another_package`
and `test_a_dev_script_that_only_talks_about_a_package_does_not_wire_it`, both watched failing
first — four failures, one per shape.
"""

from __future__ import annotations

import os
import pathlib
import posixpath
import re
import shlex

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "dev" / "run_all_tests.sh"

# The tests image (tests/Dockerfile) copies only `tools/dev/check_versions.py` out of tools/dev,
# so the runner is genuinely absent inside the container. Same reason, same wording and same
# mechanism as `tests/test_all_tests_runner.py`, which guards the other half of this script —
# a skip is never counted as a pass, and the host run that these tests actually guard always
# has the file.
pytestmark = pytest.mark.skipif(
    not RUNNER.is_file(),
    reason=f"brak {RUNNER} — skrypt hostowy nie jest kopiowany do obrazu tests",
)

#: Directories never scanned. `.venv` would otherwise contribute the test suites of every
#: installed dependency; `node_modules` and `.next` are the Next.js dashboard's build output
#: (D-014) and will contain thousands of files the moment it lands.
IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    ".pytest_cache",
    "var",
    "node_modules",
    ".next",
    "dist",
    "build",
}

#: Floor for both sides of the comparison. E1.F0 left nine packages with tests and E1.F1 adds
#: two. The number is asserted, not assumed: a scanner or a parser that stopped matching would
#: otherwise hand this file two empty sets and a green run.
MINIMUM_PACKAGES = 8

#: pytest flags used in the runner that take their value as a SEPARATE word. Without this the
#: value of `-m "not db"` would be read as a test path.
FLAGS_TAKING_A_VALUE = {"-m", "-k", "-p", "-o", "-c", "--rootdir"}


# --------------------------------------------------------------------------------------
# Reading the runner the way bash reads it: commands, not text.
# --------------------------------------------------------------------------------------


def _strip_comment(line: str) -> str:
    """Return `line` with a trailing `#` comment removed, honouring shell quoting.

    Quote-aware on purpose. `${#array[@]}` and a `#` inside a string are not comments, and a
    stripper that got that wrong would delete real commands — which would show up here as
    packages going quietly "unwired", the mirror image of the bug being fixed.
    """
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            continue
        # A comment opens only at the start of the line or after whitespace — that is bash's
        # own rule, and it keeps `${#var}` and `foo#bar` intact.
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _command_lines(text: str) -> list[str]:
    """Non-empty command lines of a shell script, comments removed."""
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw).strip()
        if stripped:
            lines.append(stripped)
    return lines


def _words(line: str) -> list[str]:
    """Shell words of one line, quotes removed.

    `shlex` raises on quoting it cannot balance (`$(...)` nesting in the dev scripts). Falling
    back to a whitespace split keeps a single awkward line from emptying the whole result set;
    the cardinality assertions in the tests are what stops a silent empty from passing.
    """
    try:
        return shlex.split(line)
    except ValueError:
        return [word.strip("\"'") for word in line.split()]


def _calls(text: str, function: str) -> list[list[str]]:
    """Argument lists of every CALL of `function` in `text`.

    A definition (`run_suite() {`) does not match — a call needs whitespace and at least one
    argument after the name. A commented-out call does not match either, because
    `_command_lines` has already thrown the comment away.
    """
    pattern = re.compile(rf"{re.escape(function)}\s+(\S.*)$")
    calls: list[list[str]] = []
    for line in _command_lines(text):
        match = pattern.match(line)
        if match:
            calls.append(_words(match.group(1)))
    return calls


def _repo_relative(word: str) -> str | None:
    """`"$ROOT/services/x"` → `services/x`. `None` for a word that is not a repo path."""
    for prefix in ("$ROOT/", "${ROOT}/"):
        if word.startswith(prefix):
            return posixpath.normpath(word[len(prefix) :])
    if word in {"$ROOT", "${ROOT}"}:
        return "."
    if not word or word.startswith(("-", "/", "$", "~")):
        return None
    return posixpath.normpath(word)


def _packages_named_in(words: list[str], package_dirs: frozenset[str]) -> set[str]:
    """Package directories DECLARED by these shell words — whole word, or leading token of one.

    Two forms, and only two. The whole word is the normal shape (`run_db_tests.sh
    services/profile_lease`); the leading token exists because
    `skip_suite "services/db_schema (cała suita)" "powód"` passes ONE shell word whose first
    token is the path, and a correctly declared skip must not read as a forgotten package.

    WHY THIS IS A LEADING TOKEN AND NOT A SEARCH INSIDE THE WORD (pakiet FIX-F).
    Until 2026-08-31 this matched `(?<![\\w./-])<package>(?![\\w./-])` ANYWHERE in the word, so
    prose credited whatever it happened to name. Measured, three shapes, all wired the ghost:
    a reason mentioning another package, a reason STARTING with another package, a suite title
    with a package name inside it. The consequence is precise and is the thing this whole file
    exists to refuse — the runner starts nothing for that package while the file stays green.
    Rule (3) had the identical hole one level down: an `echo` in a dev script (prose, but in a
    command, so `_strip_comment` keeps it) credited the package it talked about.

    Prose can now hold a package name freely; only the FIRST token of a word declares
    anything. Two limits kept from the previous version: the word is still normalised through
    `_repo_relative` (so `$ROOT/services/x` resolves), and membership is an exact set lookup,
    so `services/db_schema` is never credited to `services/db_schema_old`. What keeps this
    honest is not the matcher but WHERE it runs: only on arguments of a command bash executes,
    never on the file's text.
    """
    named: set[str] = set()
    for word in words:
        tokens = word.split()
        # The word itself first, then its leading token; anything after that is prose.
        for candidate in (word, tokens[0] if tokens else ""):
            relative = _repo_relative(candidate)
            if relative is not None and relative in package_dirs:
                named.add(relative)
    return named


def _pytest_targets(args: list[str]) -> list[str]:
    """The positional paths a `run_suite` call hands to pytest, flags and values removed."""
    targets: list[str] = []
    skip_next = False
    for word in args:
        if skip_next:
            skip_next = False
            continue
        if word in FLAGS_TAKING_A_VALUE:
            skip_next = True
            continue
        if word.startswith("-"):
            continue
        targets.append(word)
    return targets


def _suites_started(text: str) -> list[tuple[str, str, list[str]]]:
    """`(name, directory, targets)` per `run_suite` call, all paths repo-relative.

    Signature read from the script, not guessed (docs/KONWENCJE.md: read the definition):
    `run_suite <name> <dir> <python> <pythonpath> [pytest args...]`, and `pythonpath` is a
    separate parameter precisely because an earlier version of the runner read it as `${4:-.}`
    after `shift 3` and silently handed one suite the wrong PYTHONPATH.
    """
    started: list[tuple[str, str, list[str]]] = []
    for args in _calls(text, "run_suite"):
        assert len(args) >= 4, (
            f"wywołanie run_suite z {len(args)} argumentami: {args}. Sygnatura to "
            "run_suite <nazwa> <katalog> <python> <pythonpath> [argumenty pytest…]"
        )
        directory = _repo_relative(args[1])
        assert directory is not None, f"katalog suity nie jest ścieżką w repo: {args[1]!r}"
        started.append((args[0], directory, _pytest_targets(args[4:])))
    return started


def _dev_scripts_executed(text: str, root: pathlib.Path) -> list[pathlib.Path]:
    """Existing `.sh` files under `tools/dev/` that a command in `text` runs."""
    scripts: list[pathlib.Path] = []
    for line in _command_lines(text):
        for word in _words(line):
            relative = _repo_relative(word)
            if relative is None or not relative.endswith(".sh"):
                continue
            if not relative.startswith("tools/dev/"):
                continue
            path = root / relative
            if path.is_file() and path not in scripts:
                scripts.append(path)
    return scripts


def _owning_package(path: str, package_dirs: frozenset[str]) -> str | None:
    """The nearest package directory at or above `path`, or `None` if there is none."""
    current = posixpath.normpath(path)
    while current not in {"", ".", "/"}:
        if current in package_dirs:
            return current
        current = posixpath.dirname(current)
    return None


def _wired_packages(
    text: str,
    *,
    package_dirs: frozenset[str],
    root: pathlib.Path = ROOT,
) -> set[str]:
    """Packages the runner in `text` actually starts, skips by name, or delegates to a script.

    The three shapes are spelled out in this module's docstring. What they have in common is
    that each is a COMMAND — `_command_lines` has already removed every comment, and nothing
    below ever asks whether a name occurs in the file.
    """
    wired: set[str] = set()

    # (1) A `run_suite` call. The suite's own directory plus each path handed to pytest, mapped
    #     back to the package that owns it: `$ROOT/contracts` + `tests` → `contracts`,
    #     `$ROOT` + `tests` → `tests` (the repo-level suite), `$ROOT/evals` + `.` → `evals`.
    for _name, directory, targets in _suites_started(text):
        for target in targets or ["."]:
            reached = posixpath.normpath(posixpath.join(directory, target))
            owner = _owning_package(reached, package_dirs)
            if owner is not None:
                wired.add(owner)

    # (2) A conscious `skip_suite`, which has to open its SUITE NAME with the directory it
    #     skips. `skip_suite` prints a POMINIĘTE row the founder reads, so the decision is
    #     visible in the summary — but a skip that does not name its package is indistinguishable
    #     from a package nobody remembered, which is the whole failure being guarded against.
    #
    #     `args[:1]` — the name only, never the reason (pakiet FIX-F). The reason is free prose
    #     written for the founder; reading it as a declaration let the reason for skipping ONE
    #     suite wire a COMPLETELY DIFFERENT package. Reverse control:
    #     `test_prose_in_a_skip_suite_argument_never_wires_another_package`, watched failing on
    #     all three shapes before this line said `[:1]`.
    for args in _calls(text, "skip_suite"):
        wired |= _packages_named_in(args[:1], package_dirs)

    # (3) A dev script the runner actually executes, PASSING the package path as one of its own
    #     command arguments. One level of descent, deliberately: `run_all_tests.sh` runs
    #     `run_lease_tests.sh`, which passes `services/profile_lease` to `run_db_tests.sh`, and
    #     the argument sits in the script that is executed here. Going deeper would start
    #     crediting packages to scripts this runner never calls.
    #
    #     "Passing", not "mentioning" (pakiet FIX-F). An `echo` line in such a script is a
    #     COMMAND, so `_strip_comment` keeps it, and under the old inside-the-word matcher its
    #     prose credited the package it talked about. Reverse control:
    #     `test_a_dev_script_that_only_talks_about_a_package_does_not_wire_it`.
    for script in _dev_scripts_executed(text, root):
        for line in _command_lines(script.read_text(encoding="utf-8")):
            wired |= _packages_named_in(_words(line), package_dirs)

    return wired


# --------------------------------------------------------------------------------------
# Scanning the repository for packages that own tests.
# --------------------------------------------------------------------------------------


def _scan_packages() -> tuple[frozenset[str], frozenset[str]]:
    """`(every package directory, those owning at least one test file)`, repo-relative."""
    package_dirs: set[str] = set()
    directories_with_test_files: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Pruned during the walk rather than filtered afterwards: `.venv` and `var/codex-home`
        # are the two biggest trees in the repo and neither can contribute a workspace member.
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
        relative = pathlib.Path(dirpath).relative_to(ROOT).as_posix()
        if relative == ".":
            # The workspace root is not a package: its own suite is `tests/`, which is a member.
            continue
        if "pyproject.toml" in filenames:
            package_dirs.add(relative)
        if any(name.startswith("test_") and name.endswith(".py") for name in filenames):
            directories_with_test_files.add(relative)

    frozen = frozenset(package_dirs)
    with_tests = {
        owner
        for directory in directories_with_test_files
        if (owner := _owning_package(directory, frozen)) is not None
    }
    return frozen, frozenset(with_tests)


PACKAGE_DIRS, PACKAGES_WITH_TESTS = _scan_packages()


# --------------------------------------------------------------------------------------
# The checks.
# --------------------------------------------------------------------------------------


def test_every_package_with_tests_is_actually_started_by_the_runner() -> None:
    wired = _wired_packages(RUNNER.read_text(encoding="utf-8"), package_dirs=PACKAGE_DIRS)

    # BOTH sets asserted non-empty, BEFORE the comparison, and against a floor rather than
    # against zero. An empty set on either side makes the subset check vacuously true, and
    # `all([])` has already cost this project false passes (D-011). Empty here would mean the
    # scanner or the parser found nothing — not that everything is wired.
    assert len(PACKAGES_WITH_TESTS) >= MINIMUM_PACKAGES, (
        f"skaner znalazł {len(PACKAGES_WITH_TESTS)} pakietów z testami, a repo ma co najmniej "
        f"{MINIMUM_PACKAGES}. Nie znaczy to, że wszystko jest podpięte — znaczy, że ten test "
        "nic nie sprawdza"
    )
    assert len(wired) >= MINIMUM_PACKAGES, (
        f"z runnera wyprowadzono {len(wired)} podpiętych pakietów ({sorted(wired)}). Parser "
        "przestał pasować albo runner przestał uruchamiać suity — jedno i drugie ma tu paść"
    )

    missing = sorted(PACKAGES_WITH_TESTS - wired)
    assert not missing, (
        f"pakiety z testami, których `make test-all` nigdy nie uruchamia: {missing}. "
        f"Dopisz je do {RUNNER.relative_to(ROOT)} — przez `run_suite`, przez własny skrypt "
        "w tools/dev uruchamiany z tego runnera (ze ścieżką pakietu w argumentach polecenia), "
        "albo (świadomie, z powodem) przez `skip_suite`, którego NAZWA SUITY zaczyna się od "
        "tej ścieżki. Sama wzmianka o katalogu — w komentarzu, w nagłówku `printf` czy "
        "w prozie powodu pominięcia — NIE jest podpięciem: suita, której nikt nie startuje, "
        "jest w podsumowaniu nieodróżnialna od suity, która przeszła"
    )


@pytest.mark.parametrize(
    ("shape", "line"),
    [
        # The three shapes that satisfied the previous version of this check. The third one is
        # not hypothetical: a `printf` header is exactly what makes `services/profile_lease`
        # look wired today, and it is the reason that mention was mistaken for wiring.
        (
            "komentarz z pełnym wywołaniem",
            '# run_suite "{ghost}" "$ROOT/{ghost}" "$ROOT/{ghost}/.venv/bin/python" "." tests',
        ),
        ("komentarz-notatka", "# TODO: dopisać suitę {ghost}, jak tylko powstanie"),
        ("nagłówek printf", "printf '\\n=== {ghost} (własny kontener) ===\\n'"),
    ],
)
def test_a_package_named_only_in_a_comment_counts_as_forgotten(
    tmp_path: pathlib.Path, shape: str, line: str
) -> None:
    """The reverse control this whole rewrite exists for.

    A copy of the real runner, plus one line naming a package that has no `run_suite` call.
    The name IS in the file — that is asserted below, so the test cannot pass by failing to
    write the line — and the package must still come out unwired.
    """
    ghost = "services/nie_ma_takiej_suity"
    copy = tmp_path / "run_all_tests_with_a_mention.sh"
    copy.write_text(
        RUNNER.read_text(encoding="utf-8") + "\n" + line.format(ghost=ghost) + "\n",
        encoding="utf-8",
    )
    text = copy.read_text(encoding="utf-8")

    assert ghost in text, f"wzmianka nie trafiła do kopii skryptu ({shape}) — nie ma czego badać"

    wired = _wired_packages(text, package_dirs=PACKAGE_DIRS | {ghost})

    assert ghost not in wired, (
        f"{shape}: sama wzmianka o {ghost} zaliczyła pakiet jako podpięty. Dokładnie ta dziura "
        "była w pierwszej wersji tego pliku — test dawał się zaspokoić komentarzem"
    )
    # The parser still recognises the real suites in the patched copy, so the assertion above
    # is not passing merely because the parser broke on the extra line.
    assert len(wired) >= MINIMUM_PACKAGES, (
        f"{shape}: po dopisaniu wiersza parser widzi tylko {sorted(wired)} — pusty wynik "
        "zaliczyłby powyższą asercję bez sprawdzenia czegokolwiek"
    )


def test_the_lease_suite_is_wired_by_its_script_not_by_the_printf_that_names_it() -> None:
    """The same reverse control on a REAL package, which is stronger than a made-up one.

    `services/profile_lease` is named twice in the runner: in a `printf` header (a mention) and
    implicitly by `bash tools/dev/run_lease_tests.sh`, which passes the directory to
    `run_db_tests.sh` (an execution). Replace the execution and leave the `printf` alone: the
    package has to become unwired. Under the old `name in text` rule it stayed wired, because
    the `printf` was all that rule ever needed.
    """
    package = "services/profile_lease"
    text = RUNNER.read_text(encoding="utf-8")
    assert package in _wired_packages(text, package_dirs=PACKAGE_DIRS), (
        f"{package} jest uruchamiany przez tools/dev/run_lease_tests.sh, a wyszedł jako niepodpięty"
    )

    without_the_script = text.replace('bash "$ROOT/tools/dev/run_lease_tests.sh"', "true")
    assert without_the_script != text, (
        "wywołanie run_lease_tests.sh zmieniło postać w runnerze — ten test nie ma czego podmienić"
    )
    assert package in without_the_script, "nagłówek printf z nazwą pakietu musi zostać w kopii"

    assert package not in _wired_packages(without_the_script, package_dirs=PACKAGE_DIRS), (
        f"{package} zaliczony jako podpięty, choć jedyne, co go wymienia, to nagłówek printf"
    )


def test_a_skip_suite_counts_only_when_it_names_the_directory_it_skips() -> None:
    """The conscious skip stays expressible — but it has to say which package it skips.

    Both halves are here because a rule nobody watched work is a rule written to a guessed
    signature: with the `run_suite` line gone the package must read as forgotten, and adding a
    `skip_suite` that OPENS ITS SUITE NAME with that path must bring it back — the positive
    half of `test_prose_in_a_skip_suite_argument_never_wires_another_package`, and what stops
    the FIX-F tightening from being a rule that matches nothing at all. Today no `skip_suite`
    in the runner credits anything (`skip_suite "db_schema (baza)"` names a SUITE, not a
    directory, and both of those packages are started by `run_suite` anyway), so without this
    test the branch would ship unexecuted.
    """
    package = "services/db_schema"
    text = RUNNER.read_text(encoding="utf-8")

    # Matched by pattern, not by an exact string: the runner aligns its `run_suite` arguments in
    # columns, and phase E1.F1 is adding suites to that block right now. A test anchored on the
    # current run of spaces would fail on a re-alignment that changes nothing it tests.
    call = re.search(rf'(?m)^run_suite\s+"[^"]*"\s+"\$ROOT/{re.escape(package)}".*$', text)
    assert call is not None, (
        f"nie znaleziono wywołania run_suite dla {package} — nie ma czego podmienić"
    )
    without_the_suite = f"{text[: call.start()]}# {call.group(0)}{text[call.end() :]}"
    assert package not in _wired_packages(without_the_suite, package_dirs=PACKAGE_DIRS), (
        f"{package} bez wywołania run_suite nadal uchodzi za podpięty"
    )

    anchor = re.search(r"(?m)^skip_suite\s", without_the_suite)
    assert anchor is not None, "w runnerze nie ma ani jednego wywołania skip_suite"
    declared = (
        f"{without_the_suite[: anchor.start()]}"
        f'skip_suite "{package} (cała suita)" "powód dla założyciela"\n'
        f"{without_the_suite[anchor.start() :]}"
    )
    assert package in _wired_packages(declared, package_dirs=PACKAGE_DIRS), (
        f"świadome pominięcie {package} zadeklarowane ze ścieżką nie zostało uznane — "
        "wtedy jedynym sposobem podpięcia byłby run_suite, a `skip_suite` przestałby cokolwiek "
        "znaczyć dla tego skanera"
    )


@pytest.mark.parametrize(
    ("shape", "line"),
    [
        # All three were MEASURED against the previous version of `_packages_named_in` and all
        # three credited the ghost. The second is the nastiest: the path is the leading token of
        # the REASON, so "read only the leading token" is not enough on its own — the reason has
        # to be out of scope entirely.
        (
            "nazwa innego pakietu w środku powodu",
            'skip_suite "db_schema (baza)" "pomijamy, bo {ghost} nie ma jeszcze .venv"',
        ),
        (
            "nazwa innego pakietu na początku powodu",
            'skip_suite "db_schema (baza)" "{ghost} czeka na własny kontener"',
        ),
        (
            "nazwa innego pakietu w prozie nazwy suity",
            'skip_suite "suita zależna od {ghost} (cała)" "powód dla założyciela"',
        ),
    ],
)
def test_prose_in_a_skip_suite_argument_never_wires_another_package(
    tmp_path: pathlib.Path, shape: str, line: str
) -> None:
    """Reverse control for pakiet FIX-F: a skip of suite A must not wire package B.

    The FIX-3 rewrite closed "the name occurs anywhere in the FILE" and opened a narrower
    door in its place: `_packages_named_in` was applied to EVERY argument of EVERY
    `skip_suite` and matched the package path anywhere INSIDE a word. So the prose a founder
    reads — a suite title, a reason for skipping — credited whatever package it happened to
    name. Measured on 2026-08-31: all three lines below made
    `services/nie_ma_takiej_suity` come out wired, which is the tenth false pass of exactly
    the class this file exists to refuse: the runner starts nothing and the file is green.

    A conscious skip therefore counts only when the skipped directory is the LEADING TOKEN of
    the suite-name argument (`skip_suite "services/x (cała suita)" "powód"`). The reason is
    free prose for the founder and is never read as a declaration.
    """
    ghost = "services/nie_ma_takiej_suity"
    copy = tmp_path / "run_all_tests_with_a_prose_skip.sh"
    copy.write_text(
        RUNNER.read_text(encoding="utf-8") + "\n" + line.format(ghost=ghost) + "\n",
        encoding="utf-8",
    )
    text = copy.read_text(encoding="utf-8")

    assert ghost in text, f"wzmianka nie trafiła do kopii skryptu ({shape}) — nie ma czego badać"
    # The appended line really is parsed as a `skip_suite` CALL, so the assertion below cannot
    # pass merely because the parser never looked at it.
    skips = _calls(text, "skip_suite")
    assert any(ghost in " ".join(args) for args in skips), (
        f"{shape}: dopisany skip_suite nie został sparsowany jako wywołanie ({skips}) — "
        "test nie bada tego, co miał badać"
    )

    wired = _wired_packages(text, package_dirs=PACKAGE_DIRS | {ghost})

    assert ghost not in wired, (
        f"{shape}: proza w argumentach skip_suite zaliczyła {ghost} jako podpięty. Powód "
        "pominięcia JEDNEJ suity nie może podpinać INNEGO pakietu — to jest fałszywe "
        "zaliczenie tej samej klasy, którą ten plik zamyka"
    )
    assert len(wired) >= MINIMUM_PACKAGES, (
        f"{shape}: po dopisaniu wiersza parser widzi tylko {sorted(wired)} — pusty wynik "
        "zaliczyłby powyższą asercję bez sprawdzenia czegokolwiek"
    )


def test_a_dev_script_that_only_talks_about_a_package_does_not_wire_it(
    tmp_path: pathlib.Path,
) -> None:
    """The same substring hole lived in rule (3) too, and this is its reverse control.

    Rule (3) gives credit at script granularity: a `.sh` under `tools/dev/` that the runner
    executes wires whatever package its own command words name. Its words were matched with
    the same inside-a-word pattern, so an `echo` in the delegating script — prose, in a
    command, therefore surviving `_strip_comment` — credited the package it talked about.
    Measured on 2026-08-31: the fake script below made the ghost come out wired.

    Both halves are here on purpose. A rule tightened until it matches nothing is not a
    repair, so the second half proves the real shape still counts: `services/profile_lease`
    is wired because `run_lease_tests.sh` PASSES that directory to `run_db_tests.sh`.
    """
    ghost = "services/nie_ma_takiej_suity"
    real = "services/profile_lease"
    (tmp_path / "tools" / "dev").mkdir(parents=True)

    talker = tmp_path / "tools" / "dev" / "talks_about_it.sh"
    talker.write_text(
        f'echo "uruchom ręcznie testy {ghost}, bo nie mamy jeszcze runnera"\n',
        encoding="utf-8",
    )
    runner = tmp_path / "tools" / "dev" / "passes_it.sh"
    runner.write_text(
        f'exec bash "$ROOT/tools/dev/run_db_tests.sh" {real} "$@"\n', encoding="utf-8"
    )

    text = 'bash "$ROOT/tools/dev/talks_about_it.sh"\nbash "$ROOT/tools/dev/passes_it.sh"\n'
    # Both scripts are found and read — otherwise the assertions below would be measuring an
    # empty descent rather than the matcher.
    executed = _dev_scripts_executed(text, tmp_path)
    assert set(executed) == {talker, runner}, executed

    wired = _wired_packages(text, package_dirs=PACKAGE_DIRS | {ghost}, root=tmp_path)

    assert ghost not in wired, (
        f"{ghost} zaliczony jako podpięty, choć skrypt tylko o nim MÓWI w echo. Rozmowa "
        "o pakiecie nie jest uruchomieniem jego testów — to ta sama choroba, co wzmianka "
        "w komentarzu"
    )
    assert real in wired, (
        f"{real} przekazany jako argument do run_db_tests.sh wyszedł jako niepodpięty — "
        "reguła (3) przestała cokolwiek zaliczać, a to nie jest naprawa"
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # A comment opens at the start of a line or after whitespace, and nowhere else. A
        # stripper that got this wrong would delete real commands, and the packages they start
        # would silently read as forgotten — the mirror image of the bug this file fixes.
        ('run_suite "a" "$ROOT/a" # dopisać b', 'run_suite "a" "$ROOT/a"'),
        ("  # cały wiersz to komentarz", ""),
        ('echo "kratka # w cudzysłowie zostaje"', 'echo "kratka # w cudzysłowie zostaje"'),
        ("echo ${#tablica[@]}", "echo ${#tablica[@]}"),
        ("echo nie#komentarz", "echo nie#komentarz"),
        ("run_suite 'a' 'b'", "run_suite 'a' 'b'"),
    ],
)
def test_the_comment_stripper_follows_bash_rules(line: str, expected: str) -> None:
    assert _strip_comment(line).rstrip() == expected


def test_a_runner_with_every_run_suite_line_commented_out_wires_almost_nothing() -> None:
    """`all([]) == True`, fourth appearance, refused in advance.

    A runner whose suite list has evaporated must show up here as a wall of missing packages,
    not as a green run — and the names left behind in the commented-out lines must not carry it.
    """
    text, replaced = re.subn(r"(?m)^run_suite ", "# run_suite ", RUNNER.read_text(encoding="utf-8"))
    assert replaced >= 8, f"spodziewano się co najmniej 8 wywołań run_suite, znaleziono {replaced}"

    wired = _wired_packages(text, package_dirs=PACKAGE_DIRS)
    missing = PACKAGES_WITH_TESTS - wired

    assert "contracts" in missing and "tests" in missing, (
        f"zakomentowane wywołania run_suite nadal zaliczają suity jako podpięte: {sorted(wired)}"
    )
    assert len(missing) >= MINIMUM_PACKAGES, (
        f"runner bez ani jednego run_suite dał tylko {len(missing)} brakujących pakietów "
        f"({sorted(missing)}) — to jest ciche zaliczenie, nie wynik"
    )


@pytest.mark.parametrize("package", ["services/db_schema", "services/control_plane"])
def test_the_two_new_f1_packages_are_started_not_merely_mentioned(package: str) -> None:
    """The two packages this phase adds, pinned by hand.

    The scanner above is generic and would go quiet if it ever stopped seeing a directory.
    These two are the reason it was written, so they are also named here — a scanner and a
    name, because each fails in a way the other survives.
    """
    assert package in PACKAGES_WITH_TESTS, f"{package} nie ma testów albo skaner go nie widzi"

    started = _suites_started(RUNNER.read_text(encoding="utf-8"))
    assert len(started) >= 10, f"runner startuje {len(started)} suit — za mało, by cokolwiek badać"

    reached = {
        _owning_package(posixpath.normpath(posixpath.join(directory, target)), PACKAGE_DIRS)
        for _name, directory, targets in started
        for target in targets or ["."]
    }
    assert package in reached, (
        f"{package} nie jest celem żadnego wywołania run_suite w {RUNNER.relative_to(ROOT)}. "
        "Wzmianka w komentarzu tego nie zastępuje"
    )


def test_the_runner_still_starts_a_double_digit_number_of_suites() -> None:
    """A count, so that deleting suites shows up as a failure rather than as a faster run."""
    started = _suites_started(RUNNER.read_text(encoding="utf-8"))
    assert len(started) >= 10, (
        f"runner ma {len(started)} wywołań `run_suite`, a fazy E1.F0 i E1.F1 dają razem co "
        "najmniej 10. Zniknięcie suity ma tu paść, a nie skrócić przebieg"
    )


def test_every_run_suite_call_points_at_paths_that_exist() -> None:
    """A parse that drifts from the script must fail loudly, not shrink the wired set.

    "Parser, który po cichu gubi dane, jest tą samą chorobą co kontrola bez pomiaru"
    (docs/KONWENCJE.md). A `run_suite` line this file cannot resolve would otherwise make a
    real suite look forgotten — or, worse, a typo in the runner would look wired.
    """
    started = _suites_started(RUNNER.read_text(encoding="utf-8"))
    assert len(started) >= 10, f"runner startuje {len(started)} suit — za mało, by cokolwiek badać"

    for name, directory, targets in started:
        assert (ROOT / directory).is_dir(), f"suita {name!r}: katalog {directory} nie istnieje"
        for target in targets:
            path = ROOT / posixpath.normpath(posixpath.join(directory, target))
            assert path.exists(), (
                f"suita {name!r}: cel pytest {path.relative_to(ROOT)} nie istnieje"
            )


@pytest.mark.parametrize("package", ["services/db_schema", "services/control_plane"])
def test_the_new_packages_have_a_conftest_that_fails_without_a_dsn(package: str) -> None:
    """The `pool` fixture must call `pytest.fail`, never `skipif`.

    Measured by the red team on 2026-08-31: a module marked `skipif(not DSN)` reports
    "2 tests collected" and then "2 skipped" with exit code 0. A database suite that never
    reached a database then reads as green. The runner refuses skips from the outside
    (`tools/dev/run_db_tests.sh`); this is the same rule enforced from the inside, so removing
    either lock still leaves one.
    """
    conftest = ROOT / package / "tests" / "conftest.py"
    assert conftest.is_file(), conftest

    text = conftest.read_text(encoding="utf-8")
    assert "pytest.fail" in text, f"{conftest.relative_to(ROOT)}: fixture bez pytest.fail"
    assert not re.search(r"(?m)^\s*(@pytest\.mark\.skipif|pytest\.skip\()", text), (
        f"{conftest.relative_to(ROOT)}: pominięcie w fixture bazodanowej. Brak bazy ma być "
        "PORAŻKĄ — pominięty test bazodanowy przechodzi w podsumowaniu za zielony"
    )
