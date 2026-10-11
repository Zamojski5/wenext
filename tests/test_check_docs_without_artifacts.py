"""`check_docs.py` has to work on a fresh clone, where `artifacts/` does not exist.

Why this file exists — D-015: the documentation-freshness test read `artifacts/`, a directory
listed in `.gitignore` (line 29). A fresh clone therefore never has it, so on every machine
except one developer host the tool died with a false `ROZJAZD DOKUMENTACJI Z KODEM`. A meter
that only runs on one host measures nothing.

The second half of D-015 is the harder half. Absence of `artifacts/` must be an ACCEPTABLE
STATE, never a silent success: a control that rests on an artifact must say NIE_SPRAWDZONO
when the artifact is missing. Quietly turning missing evidence into a pass would be exactly
the disease being cured here — this project has already shipped six false passes.

So the tests below pin down three things at once, and each one is a separate way to get it
wrong:
  1. a fresh clone must not fail        (the reported defect),
  2. it must not silently report OK     (the false-pass trap),
  3. a mismatch must STILL be caught when the artifact IS there (proof the control was
     made tolerant of missing evidence, not disabled).

D-015 spells out the strongest form of proof and this file uses it literally: `git clone`
into a temporary directory and run the tool there. The real `artifacts/` directory of the
working repository is never written to, and an autouse fixture below asserts that.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_RELATIVE_PATH = pathlib.Path("tools") / "dev" / "check_docs.py"

#: `check_docs.check_conventions` imports `FORBIDDEN_NAMES` from this module, so every copy
#: of the repository built here has to carry it too, or the tool dies on ImportError instead
#: of on the thing under test.
COPIED_TOOLS = ("check_docs.py", "rename_identifiers.py")

#: The exact success line the tool prints when everything really was verified. It must NOT
#: appear when a control had no evidence — that assertion is the whole point of this file.
UNQUALIFIED_OK = "OK — dokumenty zgadzają się z kodem"
NOT_CHECKED_MARKER = "NIE_SPRAWDZONO"

REQUIRED_DOCS = (
    "STAN-PROJEKTU.md",
    "docs/STAN-PRAC.md",
    "docs/DECYZJE.md",
    "docs/KONWENCJE.md",
    ".claude/skills/kontekst/SKILL.md",
)

GATE_TABLE = """# Stan prac

| Bramka | Werdykt | Dowód | Zastrzeżenie |
| --- | --- | --- | --- |
| GATE-1 — kontrakt headless CDP | **GO** | `artifacts/gate1/cdp-contract.json` | brak |
"""

#: The shape `docs/STAN-PRAC.md` really has today: three gates, three **GO** declarations.
#: Used by the coverage tests, where the point is that the artifact carries FEWER verdicts
#: than the document declares.
THREE_GATE_TABLE = """# Stan prac

| Bramka | Werdykt | Dowód | Zastrzeżenie |
| --- | --- | --- | --- |
| GATE-1 — kontrakt headless CDP | **GO** | `artifacts/gate1/cdp-contract.json` | brak |
| GATE-2 — ścieżka wyjątkowa headful | **GO** | `artifacts/gate2/headful-handoff.json` | brak |
| GATE-3 — Codex jako marketing runtime | **GO** | `artifacts/gate3/codex-runtime.json` | brak |
"""

ALL_THREE_GATES = {
    "bramki": [
        {"id": "GATE-1", "werdykt": "GO"},
        {"id": "GATE-2", "werdykt": "GO"},
        {"id": "GATE-3", "werdykt": "GO"},
    ]
}

REPOSITORY_MAP_HEADER = """
## Mapa repozytorium

| Katalog | Co pilnuje | Testów |
| --- | --- | --- |
"""

#: Every verdict token this project actually writes down, in one place, because the
#: comparison in `check_docs.py` can only be whole-token if it knows the whole vocabulary.
#: Sources, each verified by grep rather than remembered:
#:   `tools/gonogo/report.py::Verdict`      → GO, NO_GO, NIE_URUCHOMIONO, DOWOD_NIEKOMPLETNY
#:   ROADMAP L5383, L5498, L5558            → PASS, CONDITIONAL_PASS, BLOCK
#:   ROADMAP L1427, L4602, L4621            → NO_DATA
EXPECTED_VERDICTS = (
    "GO",
    "NO_GO",
    "NIE_URUCHOMIONO",
    "DOWOD_NIEKOMPLETNY",
    "PASS",
    "BLOCK",
    "NO_DATA",
    "CONDITIONAL_PASS",
)

#: (verdict in the artifact, verdict in the document) — pairs that MUST be drift.
#:
#: The first two rows are the measured false pass this file was extended for: the comparison
#: was `if werdykt not in row.group(0)`, a SUBSTRING test, and `"GO"` is a substring of
#: `"NO_GO"`. A document declaring **NO_GO** against an artifact saying GO therefore agreed
#: with itself and the tool printed the unqualified OK line. `"PASS"` ⊂ `"CONDITIONAL_PASS"`
#: is the same hole in the vocabulary of section 25.2, and it proves the fix is a rule about
#: tokens rather than a special case bolted onto GO/NO_GO.
#:
#: The remaining pairs are the ordinary conflicts, which the substring test already caught.
#: They are here as the control: a fix that reports everything is worth as little as one that
#: reports nothing, so both halves are asserted against the same code path.
VERDICT_CONFLICTS = (
    ("GO", "NO_GO"),
    ("PASS", "CONDITIONAL_PASS"),
    ("NO_GO", "GO"),
    ("CONDITIONAL_PASS", "PASS"),
    ("GO", "NIE_URUCHOMIONO"),
    ("NO_DATA", "GO"),
    ("BLOCK", "PASS"),
    ("DOWOD_NIEKOMPLETNY", "GO"),
)


def one_gate_table(verdict: str, *, gate_id: str = "GATE-1") -> str:
    """A gate table with one row, whose verdict cell reads `**verdict**`.

    Marked up exactly the way the document really is: `tools/gonogo/report.py` writes
    `| {id} | {nazwa} | **{werdykt.value}** | ...`, and `docs/STAN-PRAC.md` rows 18-20 are
    that shape. A test table that used a different markup would prove nothing about the
    document this control actually reads.
    """
    return (
        "# Stan prac\n\n"
        "| Bramka | Werdykt | Dowód | Zastrzeżenie |\n"
        "| --- | --- | --- | --- |\n"
        f"| {gate_id} — kontrakt headless CDP | **{verdict}** | "
        "`artifacts/gate1/cdp-contract.json` | brak |\n"
    )


def snapshot_of(directory: pathlib.Path) -> object:
    """Listing of `directory` (path, size, mtime) or None when it does not exist."""
    if not directory.exists():
        return None
    return sorted(
        (str(entry.relative_to(directory)), entry.stat().st_size, entry.stat().st_mtime_ns)
        for entry in directory.rglob("*")
    )


@pytest.fixture(autouse=True)
def real_artifacts_directory_stays_untouched():
    """Every test here works on a copy; the working repository's evidence is off limits.

    Deleting `artifacts/` to simulate a fresh clone would destroy gate evidence that cannot
    be regenerated cheaply (GATE-3 alone is a 3-hour measured run), so the guard is asserted
    rather than assumed.
    """
    before = snapshot_of(REPO_ROOT / "artifacts")
    yield
    assert snapshot_of(REPO_ROOT / "artifacts") == before, (
        "test naruszył prawdziwy katalog artifacts/ — dowody bramek nie są materiałem testowym"
    )


def run_tool(repository_root: pathlib.Path, *extra_arguments: str) -> subprocess.CompletedProcess:
    """Runs the tool as the operator and `make check-docs` run it: as a separate process.

    In-process import would share `ROOT`, which is resolved at import time from `__file__`,
    and the first test would pin every later one to the real repository.
    """
    return subprocess.run(
        [sys.executable, str(repository_root / TOOL_RELATIVE_PATH), *extra_arguments],
        capture_output=True,
        text=True,
        cwd=repository_root,
        timeout=300,
    )


def overlay_tool_under_test(repository_root: pathlib.Path) -> None:
    """Copies the WORKING-TREE tool into a repository copy.

    Without this the clone-based test would forever measure the last commit instead of the
    change being made, and would keep passing (or failing) for reasons unrelated to the code
    under review.
    """
    destination = repository_root / TOOL_RELATIVE_PATH.parent
    destination.mkdir(parents=True, exist_ok=True)
    for name in COPIED_TOOLS:
        shutil.copy2(REPO_ROOT / "tools" / "dev" / name, destination / name)


def build_minimal_repository(
    root: pathlib.Path,
    *,
    map_rows: str = "",
    gonogo_report: dict | None = None,
    gate_table: str = GATE_TABLE,
) -> pathlib.Path:
    """Builds the smallest tree the tool accepts, deliberately WITHOUT `artifacts/`.

    Used for the cases a clone cannot express — a repository map pointing at an untracked
    directory, or a Go/No-Go artifact that contradicts the document.
    """
    for relative_path in REQUIRED_DOCS:
        document = root / relative_path
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(f"# {relative_path}\n", encoding="utf-8")

    (root / "docs" / "STAN-PRAC.md").write_text(
        gate_table + REPOSITORY_MAP_HEADER + map_rows, encoding="utf-8"
    )
    overlay_tool_under_test(root)

    if gonogo_report is not None:
        report_path = root / "artifacts" / "gonogo" / "gonogo.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(gonogo_report, ensure_ascii=False), encoding="utf-8")
    else:
        assert not (root / "artifacts").exists(), "kopia miała powstać BEZ artifacts/"
    return root


def test_the_tool_passes_on_a_real_fresh_clone_without_artifacts(tmp_path) -> None:
    """D-015, verbatim: `git clone` to a temporary directory and run it there.

    This is the whole reported defect in one test. Nothing is deleted, nothing is simulated:
    the clone genuinely lacks the evidence the tool looks for.

    The premise is stated as the gonogo artifact, not the whole `artifacts/` tree, and that is
    deliberate. `artifacts/` is in `.gitignore`, but evidence runs have since been force-added
    to it (conversation-quality episodes on 2026-09-06, pilot reports on 2026-09-12), because
    a positive verdict in this repo owes a committed artifact. So a clone DOES carry some of
    `artifacts/` and always will. `artifacts/gonogo/gonogo.json` is the file `check_docs.py`
    confronts the gate table with, it is not tracked, and its absence in a clone is the defect
    D-015 reported. Asserting on the directory made the premise fail on 2026-09-06 and the test
    stayed red until 2026-09-17 while the behaviour it guards never broke.
    """
    if shutil.which("git") is None:
        pytest.skip("brak `git` — bez niego nie da się odtworzyć świeżego klonu")

    clone = tmp_path / "fresh-clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(clone)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    assert not (clone / "artifacts" / "gonogo").exists(), (
        "przesłanka testu upadła: klon MA artifacts/gonogo/, więc niczego to nie dowodzi"
    )
    overlay_tool_under_test(clone)

    result = run_tool(clone, "--details")
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"świeży klon nadal pada:\n{output}"
    assert NOT_CHECKED_MARKER in output, (
        f"brak artefaktu przemilczany — to cichy fałszywy pass:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, (
        f"narzędzie melduje pełne OK, choć kontroli werdyktów nie wykonało:\n{output}"
    )


def test_missing_gonogo_artifact_is_reported_as_not_checked_never_as_plain_ok(tmp_path) -> None:
    """Missing evidence is NIE_SPRAWDZONO, not GO.

    The fix must not swing from "false alarm" to "silent pass": the document below declares
    **GO**, and with no artifact to confront it with, the tool has to name that verdict as
    unverified instead of blessing it.
    """
    repository = build_minimal_repository(tmp_path / "minimal")

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"brak artifacts/ ma być stanem dopuszczalnym:\n{output}"
    assert NOT_CHECKED_MARKER in output, f"kontrola bez dowodu przemilczana:\n{output}"
    assert "gonogo.json" in output, f"nie widać, KTÓREGO dowodu brakuje:\n{output}"
    assert UNQUALIFIED_OK not in output, f"niesprawdzone ogłoszone jako OK:\n{output}"


def test_require_artifacts_turns_not_checked_into_a_failure(tmp_path) -> None:
    """On the host that produces the evidence, a missing artifact is a defect.

    Tolerating the gap by default is only correct for a clone. The gate host needs the
    opposite setting, otherwise "nobody ran gonogo" looks the same as "fresh checkout".
    """
    repository = build_minimal_repository(tmp_path / "minimal")

    result = run_tool(repository, "--require-artifacts")
    output = result.stdout + result.stderr

    assert result.returncode == 1, f"--require-artifacts nie zaostrza niczego:\n{output}"
    assert NOT_CHECKED_MARKER in output, output


def test_a_map_row_pointing_at_an_untracked_directory_is_not_checked_not_drift(tmp_path) -> None:
    """A directory that `.gitignore` excludes cannot be missing "by mistake" in a clone.

    The repository map may legitimately mention `artifacts/`. Reporting its absence as
    `mapa wskazuje nieistniejący katalog` is a false alarm on every fresh checkout — and
    silently skipping it would be a false pass. It has to be NIE_SPRAWDZONO.
    """
    repository = build_minimal_repository(
        tmp_path / "minimal",
        map_rows="| `artifacts/gate3/` | dowody bramki nr 3 | 4 |\n",
    )

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"nietrzymany w gicie katalog zgłoszony jako rozjazd:\n{output}"
    assert "nieistniejący katalog" not in output, f"fałszywy alarm o katalogu:\n{output}"
    assert NOT_CHECKED_MARKER in output and "artifacts/gate3" in output, (
        f"pominięcie przemilczane zamiast zgłoszone jako niesprawdzone:\n{output}"
    )


def test_a_genuinely_missing_tracked_directory_is_still_drift(tmp_path) -> None:
    """The tolerance covers untracked paths ONLY.

    Without this, "be lenient about missing directories" would quietly delete the check that
    the repository map describes a repository that exists.
    """
    repository = build_minimal_repository(
        tmp_path / "minimal",
        map_rows="| `services/nonexistent/` | katalog, którego nie ma | 7 |\n",
    )

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert result.returncode == 1, f"nieistniejący ŚLEDZONY katalog przeszedł bez słowa:\n{output}"
    assert "services/nonexistent" in output, output


@pytest.mark.parametrize("artifact_verdict", ["NO_GO", "NO-GO"])
def test_verdict_mismatch_is_still_caught_when_the_artifact_is_present(
    artifact_verdict: str, tmp_path
) -> None:
    """Proof the control was made tolerant of a missing artifact, not switched off.

    The document says **GO** for GATE-1 and the artifact contradicts it. That must fail —
    before D-015 and after it.

    BOTH SPELLINGS, and the parametrisation was added when the substring comparison was
    replaced by a whole-token one, because that change moved this test off the path its name
    describes. `NO_GO` is what `tools/gonogo/report.py::Verdict` emits, so it takes the
    verdict-mismatch path. `NO-GO` — the hyphen — is NOT in the vocabulary, so it now takes
    the outside-the-vocabulary path instead. Same exit code, different reason, and the test
    was silently exercising only the second one. A test whose name no longer describes what
    it runs is the quiet start of a meter that measures something else than it says.
    """
    repository = build_minimal_repository(
        tmp_path / "minimal",
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": artifact_verdict}]},
    )

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert result.returncode == 1, f"rozjazd werdyktu przepuszczony:\n{output}"
    assert "GATE-1" in output, output
    assert NOT_CHECKED_MARKER not in output, (
        f"artefakt jest, więc nic nie powinno być niesprawdzone:\n{output}"
    )


def test_a_corrupt_gonogo_artifact_is_a_reported_problem_not_a_traceback(tmp_path) -> None:
    """Broken evidence must be reported, not crash the tool.

    A tool that dies with a traceback in the middle of `make test-all` tells the operator
    nothing about the documents, and an unreadable artifact is exactly as much proof of a
    verdict as no artifact at all.
    """
    repository = build_minimal_repository(tmp_path / "minimal")
    report_path = repository / "artifacts" / "gonogo" / "gonogo.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text('{"bramki": [ur', encoding="utf-8")

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert result.returncode == 1, f"uszkodzony artefakt uznany za dowód:\n{output}"
    assert "Traceback" not in output, f"narzędzie się wywróciło zamiast zgłosić usterkę:\n{output}"


def test_an_empty_gate_list_is_never_a_pass_and_one_gate_still_is(tmp_path) -> None:
    """The boundary the count guard has to sit on: zero confrontations versus one.

    An artifact that exists but holds no gate confronts NOTHING — and used to print OK.
    Third time in this project: `for gate in data.get("bramki", [])` over an empty list runs
    zero times, every check below it is skipped, and `main()` reaches the unqualified success
    line. That is D-011 word for word — "a gate with no defined controls is not a passed
    gate" — and the same shape as the two false passes `docs/KONWENCJE.md` records for
    `all([])`. Zero confrontations against a report that IS on disk is a broken report on
    this host, so it is drift, not NIE_SPRAWDZONO: the fresh-clone excuse covers only an
    artifact that is absent.

    Both sides are asserted in one test on purpose, because a guard written one notch too
    strong would reject EVERY artifact, and a meter that cannot pass is worth as little as
    one that cannot fail. The second half is that control: document and artifact agree,
    exactly one verdict is confronted, and nothing may be reported.
    """
    empty = build_minimal_repository(tmp_path / "empty", gonogo_report={"bramki": []})

    result = run_tool(empty)
    output = result.stdout + result.stderr

    assert result.returncode == 1, f"zero konfrontacji ogłoszone jako sukces:\n{output}"
    assert UNQUALIFIED_OK not in output, f"pusta lista bramek zaliczona jako OK:\n{output}"
    assert "gonogo.json" in output, f"nie widać, który artefakt jest pusty:\n{output}"

    confrontable = build_minimal_repository(
        tmp_path / "confrontable",
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(confrontable)
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"zgodny artefakt zgłoszony jako usterka:\n{output}"
    assert UNQUALIFIED_OK in output, f"wszystko sprawdzone, a brak pełnego OK:\n{output}"
    assert NOT_CHECKED_MARKER not in output, output


#: Artifacts that are VALID JSON and still unusable. That is the whole point: the tool caught
#: `json.JSONDecodeError`, so none of these was ever caught — each one either sailed through
#: as a silent pass (the wrong key) or blew up on `.get` with an `AttributeError` traceback
#: in the middle of `make test-all`, which tells the operator nothing about the documents.
#:
#: `docs/KONWENCJE.md`: "a parser that silently loses data is the same disease as a control
#: with no measurement" — it has to raise, not `continue`.
MALFORMED_ARTIFACTS = (
    ("tablica na najwyższym poziomie", '[{"id": "GATE-1", "werdykt": "GO"}]'),
    ("string na najwyższym poziomie", '"GATE-1: GO"'),
    ("null na najwyższym poziomie", "null"),
    ("liczba na najwyższym poziomie", "42"),
    ("bramki pod innym kluczem", '{"gates": [{"id": "GATE-1", "werdykt": "GO"}]}'),
    ("bramki nie są listą", '{"bramki": "GATE-1"}'),
    ("wpis bramki nie jest obiektem", '{"bramki": ["GATE-1"]}'),
    # The two below are the same disease one level deeper, and the first round of this fix
    # missed them: it validated the CONTAINERS (top level is an object, `bramki` is a list,
    # each entry is an object) and then trusted the FIELDS. `{"id": 1}` is valid JSON, is
    # truthy, and killed the tool on `re.escape(1)` → `TypeError: decoding to str`;
    # `{"werdykt": 7}` killed it on `7 not in "<row>"`. Both measured on the tool as
    # committed, both a traceback in the middle of `make test-all`.
    ("id nie jest napisem", '{"bramki": [{"id": 1, "werdykt": "GO"}]}'),
    ("werdykt nie jest napisem", '{"bramki": [{"id": "GATE-1", "werdykt": 7}]}'),
)


@pytest.mark.parametrize("shape_name, artifact_text", MALFORMED_ARTIFACTS)
def test_a_malformed_artifact_is_drift_not_a_traceback(
    shape_name: str, artifact_text: str, tmp_path
) -> None:
    """Every shape here is syntactically valid JSON, so the JSON decoder never sees a fault.

    Two failure modes had to be closed at once and both are asserted below:
      * `{"gates": ...}` — the key the tool reads is simply absent, `data.get("bramki", [])`
        returns the default, and the run ends in the unqualified OK line. A false pass.
      * a list / string / null / number — `.get` does not exist on them, so the tool dies
        with a traceback instead of reporting a finding, and every check that had not run
        yet dies with it.
    """
    repository = build_minimal_repository(tmp_path / "minimal")
    report_path = repository / "artifacts" / "gonogo" / "gonogo.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(artifact_text, encoding="utf-8")

    result = run_tool(repository)
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"{shape_name}: narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, f"{shape_name}: zły kształt przeszedł jako dowód:\n{output}"
    assert UNQUALIFIED_OK not in output, f"{shape_name}: ogłoszone jako pełne OK:\n{output}"
    assert "gonogo.json" in output, f"{shape_name}: nie widać, co jest zepsute:\n{output}"


def test_a_go_row_with_no_gate_in_the_artifact_is_drift_not_a_full_pass(tmp_path) -> None:
    """The empty-list guard closed zero-out-of-N. This closes one-out-of-N — the same hole.

    Found while verifying the empty-list fix, and it is the SAME false pass one notch up:
    the artifact is confronted with the document, but only where the artifact happens to
    have an entry. A report carrying one gate against a document declaring three left two
    **GO** verdicts touched by nothing, and the tool still printed the unqualified
    "OK — dokumenty zgadzają się z kodem". Measured on the committed tool before the fix:
    three **GO** rows, one gate in the artifact, exit code 0, full OK.

    That is D-011 again — a verdict nobody confronted is not a verdict with evidence — and
    it is the reason the count has to be compared with what the document CLAIMS, not merely
    with zero. Counting confrontations and never reading the counter (which is what the
    first fix did: `confronted` was incremented and then dropped) is a guard in the comment
    only.

    The second half is the negative control, and it is not optional: a rule written one
    notch too strong would reject the real `docs/STAN-PRAC.md`, whose three **GO** rows all
    do have evidence. A meter that cannot pass is worth as little as one that cannot fail.
    """
    partial = build_minimal_repository(
        tmp_path / "partial",
        gate_table=THREE_GATE_TABLE,
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(partial, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"dwa werdykty **GO** bez ani jednej konfrontacji z dowodem, a mimo to sukces:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, (
        f"niepotwierdzone **GO** ogłoszone jako pełne OK:\n{output}"
    )
    assert "GATE-2" in output and "GATE-3" in output, (
        f"nie widać, KTÓRYCH werdyktów nie skonfrontowano:\n{output}"
    )

    complete = build_minimal_repository(
        tmp_path / "complete",
        gate_table=THREE_GATE_TABLE,
        gonogo_report=ALL_THREE_GATES,
    )

    result = run_tool(complete, "--details")
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"komplet dowodów zgłoszony jako usterka:\n{output}"
    assert UNQUALIFIED_OK in output, f"wszystko skonfrontowane, a brak pełnego OK:\n{output}"
    assert NOT_CHECKED_MARKER not in output, output


def test_a_go_claim_outside_the_gate_table_is_not_checked_never_silently_ok(tmp_path) -> None:
    """A **GO** in prose is not drift and not a pass — it is a claim nobody confronted.

    Deliberately the WEAKER of the two channels, and the reason is a lesson this file
    already records for the Polish-identifier meter: a control that raises false alarms
    gets switched off, and a switched-off control measures nothing. Only a table row can be
    matched against a gate id, so only a table row is held to `ROZJAZD`; a sentence saying
    **GO** goes to NIE_SPRAWDZONO, where `--require-artifacts` can still make it fatal on
    the host that owes the evidence.

    Silence was the third option and it is the one that is forbidden: a verdict printed in
    the status document with nothing behind it is exactly the false pass this project has
    shipped six times.
    """
    prose = build_minimal_repository(
        tmp_path / "prose",
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )
    status_document = prose / "docs" / "STAN-PRAC.md"
    status_document.write_text(
        status_document.read_text(encoding="utf-8")
        + "\nBramka nr 2 dostała **GO** w piątek, na słowo.\n",
        encoding="utf-8",
    )

    result = run_tool(prose)
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 0, f"deklaracja w prozie potraktowana jak rozjazd:\n{output}"
    assert NOT_CHECKED_MARKER in output, (
        f"**GO** poza tabelą przemilczane — cichy fałszywy pass:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, (
        f"nieskonfrontowane **GO** ogłoszone jako pełne OK:\n{output}"
    )

    result = run_tool(prose, "--require-artifacts")
    output = result.stdout + result.stderr

    assert result.returncode == 1, (
        f"--require-artifacts nie zaostrza niepotwierdzonej deklaracji **GO**:\n{output}"
    )


def test_a_hidden_directory_is_not_mistaken_for_an_untracked_one() -> None:
    """`lstrip("./")` strips a SET OF CHARACTERS, not a prefix — a delayed-fuse bomb.

    `".data/notes.md"` is a tracked hidden directory; `lstrip("./")` ate the leading dot and
    turned it into `"data/notes.md"`, which matches the untracked prefix `data/`. The tool
    would then excuse the file's absence forever instead of reporting drift. Harmless only
    for as long as no `UNTRACKED_BY_DESIGN` entry has a leading-dot neighbour — which is not
    a property anybody maintains.

    The `./` tolerance itself has to survive the fix, so it is asserted in the same test.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools" / "dev"))
    from check_docs import untracked_by_design

    assert untracked_by_design(".data/notes.md") is False, (
        "ukryty katalog `.data/` uznany za `data/` — usprawiedliwienie dla śledzonego pliku"
    )
    assert untracked_by_design("artifacts/gonogo/gonogo.json") is True
    assert untracked_by_design("./artifacts/gonogo/gonogo.json") is True
    assert untracked_by_design("docs/STAN-PRAC.md") is False


def test_untracked_prefixes_stay_in_step_with_gitignore() -> None:
    """The tolerance list must not drift away from `.gitignore`.

    If someone starts tracking one of these directories, the tool would keep excusing its
    absence forever — the same class of blind spot as a meter that cannot fail.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools" / "dev"))
    from check_docs import UNTRACKED_BY_DESIGN

    gitignore_lines = {
        line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    # COUNT FIRST, PREDICATE SECOND — the rule `docs/KONWENCJE.md` states for `all([])`, and
    # this test was breaking it. `absent` is built by a comprehension over
    # `UNTRACKED_BY_DESIGN`: had that tuple been emptied (or the import silently bound to
    # something empty), the comprehension would yield `[]`, `not absent` would be True, and
    # the test would pass having compared NOTHING with `.gitignore` — the vacuous pass this
    # project has already shipped. The six entries are named in `check_docs.py` lines 57-64;
    # the number is asserted so that quietly deleting one cannot quietly delete this control.
    assert len(UNTRACKED_BY_DESIGN) == 6, (
        f"lista tolerowanych prefiksów zmieniła liczność: {UNTRACKED_BY_DESIGN}. "
        "Jeżeli świadomie — popraw ten test i uzasadnij w DECYZJE.md"
    )
    assert len(gitignore_lines) >= len(UNTRACKED_BY_DESIGN), (
        f".gitignore ma {len(gitignore_lines)} linii — za mało, żeby cokolwiek potwierdzać"
    )
    absent = [prefix for prefix in UNTRACKED_BY_DESIGN if prefix not in gitignore_lines]
    assert not absent, f"nie ma tego w .gitignore, więc tolerancja jest nieuzasadniona: {absent}"


def test_the_verdict_vocabulary_is_explicit_and_the_traps_below_are_load_bearing() -> None:
    """The whole-token comparison is only as complete as the list of verdicts it knows.

    Two separate things are pinned here, and both were free to rot before:
      * the vocabulary itself — its contents AND its length, so that dropping a verdict from
        `check_docs.KNOWN_VERDICTS` (which would send that verdict back to the substring
        path, or silently out of the comparison) breaks a test instead of nothing;
      * the trap table `VERDICT_CONFLICTS` — if it stopped containing a pair where one
        verdict is a substring of the other, the parametrised test below would still run,
        still pass, and would no longer be testing the defect it exists for. That is the
        empty-set false pass wearing test-data clothing.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools" / "dev"))
    from check_docs import KNOWN_VERDICTS

    assert len(EXPECTED_VERDICTS) == 8, EXPECTED_VERDICTS
    assert len(KNOWN_VERDICTS) == 8, (
        f"słownik werdyktów zmienił liczność: {KNOWN_VERDICTS}. Dopisanie werdyktu jest "
        "legalne — dopisz go w OBU miejscach i podnieś tę liczbę świadomie"
    )
    assert set(KNOWN_VERDICTS) == set(EXPECTED_VERDICTS), (
        f"narzędzie i test nie znają tych samych werdyktów: "
        f"tylko w narzędziu {sorted(set(KNOWN_VERDICTS) - set(EXPECTED_VERDICTS))}, "
        f"tylko w teście {sorted(set(EXPECTED_VERDICTS) - set(KNOWN_VERDICTS))}"
    )

    substring_traps = [
        (artifact, document)
        for artifact, document in VERDICT_CONFLICTS
        if artifact != document and (artifact in document or document in artifact)
    ]
    assert len(substring_traps) >= 2, (
        f"tabela konfliktów nie zawiera już ani jednej pary, w której jeden werdykt jest "
        f"podciągiem drugiego — test podciągu przeszedłby ją w całości: {VERDICT_CONFLICTS}"
    )


@pytest.mark.parametrize("artifact_verdict, document_verdict", VERDICT_CONFLICTS)
def test_a_verdict_conflict_is_drift_even_when_one_verdict_contains_the_other(
    artifact_verdict: str, document_verdict: str, tmp_path
) -> None:
    """The measured false pass: `"GO" in "**NO_GO**"` is True, so the two agreed.

    The comparison was `if werdykt not in row.group(0)` — a substring test against the whole
    table row. A document row declaring **NO_GO** while the artifact says GO was therefore
    accepted, and the tool printed "OK — dokumenty zgadzają się z kodem". This is the meter
    the whole repository is judged by, so a lie here is a lie about every document under it.

    Both directions are parametrised, because only one of them was broken and a fix that
    repairs one direction while blunting the other would look identical from the outside.
    """
    repository = build_minimal_repository(
        tmp_path / "conflict",
        gate_table=one_gate_table(document_verdict),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": artifact_verdict}]},
    )

    result = run_tool(repository, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"dokument mówi **{document_verdict}**, artefakt {artifact_verdict}, "
        f"a narzędzie ogłasza sukces:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, f"sprzeczność werdyktów ogłoszona jako pełne OK:\n{output}"
    assert "GATE-1" in output and artifact_verdict in output, (
        f"nie widać, której bramki i którego werdyktu dotyczy rozjazd:\n{output}"
    )


@pytest.mark.parametrize("verdict", EXPECTED_VERDICTS)
def test_a_verdict_that_agrees_with_the_artifact_stays_a_full_pass(verdict: str, tmp_path) -> None:
    """The negative control, and it is not optional.

    A whole-token rule written one notch too strong (anchoring the match to the row, or
    forgetting that the document wraps the verdict in `**`) would reject EVERY document,
    including a correct one. A meter that cannot pass is worth exactly as much as a meter
    that cannot fail — `docs/KONWENCJE.md` records both. Every verdict in the vocabulary is
    exercised, not just GO, because the trap being closed lives in the rarely-used tokens.
    """
    repository = build_minimal_repository(
        tmp_path / "agreeing",
        gate_table=one_gate_table(verdict),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": verdict}]},
    )

    result = run_tool(repository, "--details")
    output = result.stdout + result.stderr

    assert result.returncode == 0, f"zgodny werdykt **{verdict}** zgłoszony jako rozjazd:\n{output}"
    assert UNQUALIFIED_OK in output, f"wszystko skonfrontowane, a brak pełnego OK:\n{output}"
    assert NOT_CHECKED_MARKER not in output, output


def test_a_verdict_outside_the_vocabulary_is_reported_never_silently_accepted(tmp_path) -> None:
    """A verdict the tool does not know cannot be confronted — and must not pass as agreement.

    Two shapes, both of which the substring test accepted:
      * `"G"` — one letter, a substring of practically any row, so the artifact could carry
        a truncated or typo'd verdict and the document would always "agree" with it;
      * `"CZESCIOWO"` — a token nobody defined, agreeing with a document row that repeats
        the same undefined token. Symmetric nonsense is still nonsense: if the vocabulary
        does not contain it, `tools/gonogo/report.py` cannot have produced it, so the
        artifact is not the artifact this control thinks it is reading.

    Reported as drift rather than NIE_SPRAWDZONO: the artifact is present, on this host, and
    it is wrong — the fresh-clone excuse (D-015) covers only evidence that is absent.
    """
    truncated = build_minimal_repository(
        tmp_path / "truncated",
        gate_table=one_gate_table("GO"),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "G"}]},
    )

    result = run_tool(truncated, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, f"jednoliterowy werdykt uznany za zgodny z **GO**:\n{output}"
    assert UNQUALIFIED_OK not in output, f"werdykt spoza słownika ogłoszony jako OK:\n{output}"

    undefined = build_minimal_repository(
        tmp_path / "undefined",
        gate_table=one_gate_table("CZESCIOWO"),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "CZESCIOWO"}]},
    )

    result = run_tool(undefined, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"werdykt spoza słownika przeszedł, bo dokument powtarza ten sam napis:\n{output}"
    )
    assert "CZESCIOWO" in output, f"nie widać, który werdykt jest nieznany:\n{output}"


def test_a_row_that_declares_no_verdict_at_all_is_drift_not_agreement(tmp_path) -> None:
    """The empty set, in the place the whole-token fix created for it.

    Whole-token matching returns a SET of the verdicts a row declares, and a row that
    declares none returns the empty set. `docs/KONWENCJE.md` records three false passes in
    this project born exactly there (`all([])` twice). So the boundary is nailed down from
    the outside: no verdict in the row is a confrontation that did not happen, and it has to
    read as drift — never as "nothing contradicted the artifact, therefore OK".
    """
    repository = build_minimal_repository(
        tmp_path / "verdictless",
        gate_table=(
            "# Stan prac\n\n"
            "| Bramka | Werdykt | Dowód | Zastrzeżenie |\n"
            "| --- | --- | --- | --- |\n"
            "| GATE-1 — kontrakt headless CDP | (do ustalenia) | `artifacts/gate1/x.json` | brak |\n"
        ),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(repository, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"wiersz bez żadnego werdyktu uznany za zgodny z artefaktem:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, f"brak konfrontacji ogłoszony jako OK:\n{output}"
    assert "GATE-1" in output, output


def test_a_verdict_named_in_a_caveat_cell_does_not_stand_in_for_the_verdict_column(
    tmp_path,
) -> None:
    """Why the whole-token rule reads the CELL first and the whole row only as a fallback.

    A naive whole-row token scan would fix the substring bug and open a new one in the same
    move: the caveat column of `docs/STAN-PRAC.md` is prose, and prose about a gate very
    naturally names the other verdict ("okno 3 h, inaczej byłoby NO_GO"). Such a row would
    then declare BOTH verdicts, and an artifact saying either one would find agreement — the
    same false pass, reached by a different road.

    Both halves are asserted against one row, because that is the only way to show the rule
    discriminates: the verdict cell decides, the caveat cell does not get a vote.
    """
    gate_table = (
        "# Stan prac\n\n"
        "| Bramka | Werdykt | Dowód | Zastrzeżenie |\n"
        "| --- | --- | --- | --- |\n"
        "| GATE-1 — kontrakt headless CDP | **GO** | `artifacts/gate1/x.json` | "
        "okno 3 h, nie 6 h; gdyby liczyć ściśle, byłoby NO_GO |\n"
    )

    agreeing = build_minimal_repository(
        tmp_path / "caveat-ok",
        gate_table=gate_table,
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )
    result = run_tool(agreeing, "--details")
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"werdykt wspomniany w zastrzeżeniu wywołał fałszywy alarm na zgodnym wierszu:\n{output}"
    )
    assert UNQUALIFIED_OK in output, output

    conflicting = build_minimal_repository(
        tmp_path / "caveat-conflict",
        gate_table=gate_table,
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "NO_GO"}]},
    )
    result = run_tool(conflicting, "--details")
    output = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"artefakt mówi NO_GO, kolumna werdyktu **GO**, a zdanie w zastrzeżeniu wystarczyło "
        f"za dowód zgodności:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, output


#: Rows whose VERDICT COLUMN declares GO while some OTHER cell of the same row names NO_GO.
#: Each one is a measured false pass of the committed tool, and each enters the hole from the
#: opposite side to `test_a_verdict_named_in_a_caveat_cell_does_not_stand_in_for_the_verdict_
#: column`: that test keeps the verdict cell BARE and asks whether the caveat cell can vote.
#: These two take the vote away from the verdict cell instead, each in one of the two ways the
#: two-pass rule can lose it, and then ask the same question.
#:
#:   1. THE VERDICT CELL IS NOT ONE TOKEN. `**GO** (warunkowo)` does not equal any verdict
#:      after `strip("*`")`, so pass 1 (the cell) matched NOTHING, control fell through to
#:      pass 2 — a token scan of the WHOLE ROW — and the NO_GO named in the caveat prose
#:      became a verdict the row "declares". An artifact saying NO_GO then found agreement.
#:      The shape is not hypothetical: section 25.2 of the roadmap introduces
#:      CONDITIONAL_PASS, and the real GATE-3 row of `docs/STAN-PRAC.md` (line 20) already
#:      carries a bolded caveat and qualifying prose.
#:   2. TWO CELLS ARE EXACTLY A VERDICT. Pass 1 collected verdicts from EVERY cell into one
#:      set, so a caveat column holding the bare token `NO_GO` (a previous verdict, a
#:      "otherwise it would be" note) put a second verdict into the row's declaration without
#:      touching the verdict column at all — and `werdykt in declared` accepted either.
#:
#: The docstring of `verdicts_declared_in_row` promised "the caveat cell does not get a vote".
#: That promise held only while the verdict cell was a single naked token, and THAT condition
#: was written down nowhere — which is how a guarantee quietly becomes a wish.
ROWS_WHOSE_VERDICT_COLUMN_IS_NOT_A_BARE_TOKEN = (
    (
        "kwalifikator w komórce werdyktu",
        "| GATE-1 — kontrakt headless CDP | **GO** (warunkowo) | `artifacts/gate1/x.json` | "
        "okno 3 h, nie 6 h; gdyby liczyć ściśle, byłoby NO_GO |\n",
    ),
    (
        "nagi werdykt w komórce zastrzeżenia",
        "| GATE-1 — kontrakt headless CDP | **GO** | `artifacts/gate1/x.json` | NO_GO |\n",
    ),
)


def gate_table_with_row(verdict_row: str) -> str:
    """The real four-column gate table header with `verdict_row` as its only row."""
    return (
        "# Stan prac\n\n"
        "| Bramka | Werdykt | Dowód | Zastrzeżenie |\n"
        "| --- | --- | --- | --- |\n" + verdict_row
    )


@pytest.mark.parametrize("shape_name, verdict_row", ROWS_WHOSE_VERDICT_COLUMN_IS_NOT_A_BARE_TOKEN)
def test_the_verdict_column_keeps_its_authority_when_it_is_not_a_bare_token(
    shape_name: str, verdict_row: str, tmp_path
) -> None:
    """A verdict named ANYWHERE ELSE in the row never stands in for the verdict column.

    Measured on the committed tool, both shapes: the document's verdict column said **GO**,
    the artifact said NO_GO, and the tool exited 0 printing "OK — dokumenty zgadzają się z
    kodem". The eighth false pass of this project would have been the same species as the
    seventh — a verdict agreeing with a string that is not the verdict.

    The second half of each case is the negative control and it is not optional: the same row
    against an artifact that agrees must stay a FULL pass. A rule written one notch too strong
    ("a qualified verdict cell declares nothing") would turn every conditional verdict into
    drift, and a meter that cannot pass is worth as little as one that cannot fail.
    """
    conflicting = build_minimal_repository(
        tmp_path / "conflict",
        gate_table=gate_table_with_row(verdict_row),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "NO_GO"}]},
    )

    result = run_tool(conflicting, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"{shape_name}: narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"{shape_name}: kolumna werdyktu mówi **GO**, artefakt NO_GO, a NO_GO wymieniony "
        f"w innej komórce wystarczył za dowód zgodności:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, (
        f"{shape_name}: sprzeczność werdyktów ogłoszona jako pełne OK:\n{output}"
    )
    assert "GATE-1" in output, f"{shape_name}: nie widać, której bramki dotyczy rozjazd:\n{output}"

    agreeing = build_minimal_repository(
        tmp_path / "agreeing",
        gate_table=gate_table_with_row(verdict_row),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(agreeing, "--details")
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"{shape_name}: zgodny werdykt **GO** w kolumnie werdyktu zgłoszony jako rozjazd:\n{output}"
    )
    assert UNQUALIFIED_OK in output, (
        f"{shape_name}: wszystko skonfrontowane, a brak pełnego OK:\n{output}"
    )
    assert NOT_CHECKED_MARKER not in output, output


def test_a_verdict_column_naming_two_verdicts_declares_neither(tmp_path) -> None:
    """The same leak as the caveat cell, moved INSIDE one cell — where narrowing cannot help.

    Reading only the first cell that names a verdict stops a caveat column from voting. It
    does nothing about a verdict column that names two verdicts by itself:

        | GATE-1 — … | **GO** albo NO_GO | … | brak |

    With the comparison written as `werdykt in declared` — "is the artifact's verdict among
    those the row names" — this row agreed with an artifact saying GO *and* with one saying
    NO_GO. Measured: exit 0 and "OK — dokumenty zgadzają się z kodem" for BOTH artifacts, so
    the meter blessed a document that had not decided anything. Hence exact set equality: the
    verdict column must name exactly one verdict, and it must be the artifact's.

    Both artifacts are asserted, because a rule that rejected only the disagreeing one would
    leave half the leak open — and the point is that an ambiguous column is not evidence in
    EITHER direction. The operator is told which two verdicts collided, so the fix (move the
    qualification into the caveat column) is visible from the message.
    """
    ambiguous_row = (
        "| GATE-1 — kontrakt headless CDP | **GO** albo NO_GO | `artifacts/gate1/x.json` | brak |\n"
    )

    for artifact_verdict in ("GO", "NO_GO"):
        repository = build_minimal_repository(
            tmp_path / f"ambiguous-{artifact_verdict}",
            gate_table=gate_table_with_row(ambiguous_row),
            gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": artifact_verdict}]},
        )

        result = run_tool(repository, "--details")
        output = result.stdout + result.stderr

        assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
        assert result.returncode == 1, (
            f"kolumna werdyktu wymienia GO i NO_GO naraz, artefakt {artifact_verdict}, "
            f"a niezdecydowany dokument uznano za zgodny:\n{output}"
        )
        assert UNQUALIFIED_OK not in output, (
            f"niejednoznaczna kolumna werdyktu ogłoszona jako pełne OK:\n{output}"
        )
        assert "GO" in output and "NO_GO" in output, (
            f"nie widać, które dwa werdykty się zderzyły:\n{output}"
        )


def test_a_hyphen_spelling_in_the_document_row_is_not_a_verdict_token(tmp_path) -> None:
    """The token boundary is `(?<![0-9A-Za-z_-])`, NOT `\\b` — pinned where it decides.

    `-` is in that character class on purpose and this is the only test that holds it there.
    `_` is a word character, so `\\bGO\\b` already refuses to match inside `NO_GO`; `-` is
    not, so `\\bGO\\b` matches happily inside `NO-GO` — the hyphen spelling the older
    artifacts use. Delete the two `-` from the lookarounds (exactly `\\b` semantics) and the
    document row below, which declares NO-GO, hands its tail `GO` to an artifact saying GO:
    the tool finds agreement and exits 0 with the unqualified OK line. Measured, not reasoned.

    WHY THIS TEST HAD TO BE WRITTEN AT ALL, and it is the point: the production comment
    justified the character class by citing
    `test_verdict_mismatch_is_still_caught_when_the_artifact_is_present`, which puts `NO-GO`
    in the ARTIFACT — where the verdict is not in the vocabulary and the outside-the-
    vocabulary branch fires long before any token boundary is consulted. That test's own
    docstring says so. So the property was justified by a test that cannot fail for it: the
    class could be loosened to `\\b` and all 43 tests stayed green. A comment citing a test
    that does not prove its claim is D-005's phantom name in another costume.

    The document ROW is the only place this class decides anything, because the verdict cell
    here is not a bare token: pass 1 finds nothing (`NO-GO` is not in the vocabulary), so the
    scan of the cell runs, and the boundary is what stops `GO` from being read out of it.
    A row declaring an unknown spelling declares NO KNOWN VERDICT — that is drift, never
    agreement.
    """
    repository = build_minimal_repository(
        tmp_path / "hyphen",
        gate_table=gate_table_with_row(
            "| GATE-1 — kontrakt headless CDP | **NO-GO** | `artifacts/gate1/x.json` | brak |\n"
        ),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(repository, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"wiersz mówi **NO-GO**, artefakt GO, a ogon `GO` z pisowni z myślnikiem uznano "
        f"za deklarację wiersza:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, f"rozjazd werdyktu ogłoszony jako pełne OK:\n{output}"
    assert "żadnego znanego werdyktu" in output, (
        f"wiersz deklaruje pisownię spoza słownika, więc nie deklaruje ŻADNEGO znanego "
        f"werdyktu — a narzędzie twierdzi inaczej:\n{output}"
    )


def test_a_verdict_worded_inside_a_cell_is_read_by_the_second_pass(tmp_path) -> None:
    """The second pass, exercised with a NON-EMPTY result — until now nothing did that.

    Every other test in this file is decided by pass 1, the bare verdict cell. Pass 2 (the
    token scan) therefore carried the entire "a row that words its verdict in a sentence is
    still checked rather than silently unconfrontable" promise of
    `verdicts_declared_in_row` with no test behind it: deleting it outright left the suite
    green, because in every fixture pass 1 had already answered.

    Both directions are asserted on the same row, because that is what separates "pass 2
    exists" from "pass 2 is a rubber stamp". The cell says GO in prose:
      * artifact GO  → the verdict IS confronted, full OK. Delete pass 2 and this half fails,
        because the row would then declare nothing and nothing is drift (see
        `test_a_row_that_declares_no_verdict_at_all_is_drift_not_agreement`).
      * artifact NO_GO → drift. Pass 2 reports what the cell really says, so widening it into
        a substring test or an "any verdict in the row will do" scan breaks this half.
    """
    prose_row = (
        "| GATE-1 — kontrakt headless CDP | werdykt GO po pomiarze | "
        "`artifacts/gate1/x.json` | brak |\n"
    )

    agreeing = build_minimal_repository(
        tmp_path / "worded",
        gate_table=gate_table_with_row(prose_row),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(agreeing, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 0, (
        f"werdykt wysłowiony w komórce jest zgodny z artefaktem, a zgłoszono rozjazd — "
        f"przebieg 2 nie działa:\n{output}"
    )
    assert UNQUALIFIED_OK in output, (
        f"werdykt skonfrontowany przebiegiem 2, a brak pełnego OK:\n{output}"
    )
    assert NOT_CHECKED_MARKER not in output, output

    conflicting = build_minimal_repository(
        tmp_path / "worded-conflict",
        gate_table=gate_table_with_row(prose_row),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "NO_GO"}]},
    )

    result = run_tool(conflicting, "--details")
    output = result.stdout + result.stderr

    assert result.returncode == 1, (
        f"komórka mówi GO, artefakt NO_GO, a przebieg 2 przepuścił to jako zgodność:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, f"sprzeczność ogłoszona jako pełne OK:\n{output}"
    assert "GATE-1" in output, output


def test_a_gate_id_matches_a_whole_row_identifier_never_a_prefix_of_one(tmp_path) -> None:
    """The same disease as the verdict bug, one column to the left.

    The row was located with `^\\|\\s*{re.escape(ident)}\\b` — and `\\b` sits happily between
    `GATE-1` and the hyphen of `GATE-1-bis`, because a hyphen is not a word character. So
    the gate `GATE-1` matched the row of a DIFFERENT gate, took that row's verdict as its
    own, and marked it confronted. With no row of its own anywhere in the document, the tool
    exited 0 with the unqualified OK line.

    `\\b` was not wrong by accident: the identifier is followed by an em dash in the real
    table (`| GATE-1 — kontrakt headless CDP |`), so the boundary has to allow a space and
    forbid `-`, `_` and alphanumerics. That is why the fix is an explicit character class
    and not `\\b`.
    """
    repository = build_minimal_repository(
        tmp_path / "prefix",
        gate_table=one_gate_table("GO", gate_id="GATE-1-bis"),
        gonogo_report={"bramki": [{"id": "GATE-1", "werdykt": "GO"}]},
    )

    result = run_tool(repository, "--details")
    output = result.stdout + result.stderr

    assert "Traceback" not in output, f"narzędzie się wywróciło:\n{output}"
    assert result.returncode == 1, (
        f"bramka GATE-1 nie ma w dokumencie ani jednego wiersza, a mimo to sukces:\n{output}"
    )
    assert UNQUALIFIED_OK not in output, f"cudzy wiersz uznany za dowód:\n{output}"
    assert "brak wiersza dla GATE-1" in output, (
        f"nie widać, że wiersza dla GATE-1 po prostu nie ma:\n{output}"
    )
