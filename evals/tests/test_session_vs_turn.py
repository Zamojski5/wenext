"""The held-out comparison of the old turn with the new session (P2 etap B).

TWO PROPERTIES, and they are not the same one:

  1. the tasks are usable AND SYMMETRIC. A task that names a tool only the new path has would
     measure the wording of the task rather than the environment, and the comparison would come
     out in favour of whoever wrote it;
  2. the tasks never reach the product's memory. A held-out set written into `knowledge_items`
     stops being held out, and the comparison then measures recall of the answer sheet.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evals.harness.session_vs_turn import (
    CASES_DIR,
    ONLY_NEW_PATH,
    REQUIRED_CASE_FIELDS,
    VERDICT_NOT_RUN,
    VERDICT_STUB_OK,
    check,
    load_cases,
    load_criteria,
    write_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_offline_half_passes_and_the_model_half_says_it_did_not_run() -> None:
    report = check()
    assert report.problems == [], report.problems
    assert report.verdict_stub == VERDICT_STUB_OK
    assert report.verdict_model == VERDICT_NOT_RUN
    assert report.as_json()["prog_zastosowany"] is None


def test_both_paths_are_named_and_there_are_exactly_two() -> None:
    """One path is not a comparison, three is not this milestone's question."""
    paths = [entry["id"] for entry in load_criteria()["porownywane_sciezki"]]
    assert paths == ["stara_tura", "nowa_sesja"], paths


def test_every_task_has_a_thread_a_goal_and_an_expectation() -> None:
    cases = load_cases()
    assert len(cases) >= 3, "za mało zadań, żeby porównanie cokolwiek pokazało"
    for case in cases:
        for field in REQUIRED_CASE_FIELDS:
            assert case.get(field), f"{case.get('id')}: brak {field}"


def test_silence_is_an_allowed_result_for_at_least_one_task() -> None:
    """P2 says a short reaction and no reaction are both legitimate outcomes."""
    cases = {case["id"]: case for case in load_cases()}
    allowed = cases["watek-nie-dla-nas"]["oczekiwane_zachowanie"]["automat"]
    assert "SILENCE" in allowed["dopuszczalne_decyzje"]


def test_a_task_written_for_the_new_path_is_refused(tmp_path) -> None:
    """The symmetry check, exercised rather than trusted."""
    import shutil

    shutil.copytree(CASES_DIR, tmp_path / "set")
    (tmp_path / "set" / "cases" / "99-stronnicze.yaml").write_text(
        "id: stronnicze\nnazwa: Zadanie pod nową ścieżkę\nwymiary: [realizacja_celu]\n"
        "watek: {spolecznosc: r/x, tytul: t, tresc: c}\n"
        "cel: zawołaj memory.search i odpowiedz\n"
        "oczekiwane_zachowanie: {opis: cokolwiek}\n",
        encoding="utf-8",
    )
    report = check(tmp_path / "set")
    assert any("nową ścieżkę" in problem for problem in report.problems), report.problems


@pytest.mark.parametrize("marker", ONLY_NEW_PATH)
def test_no_shipped_task_names_a_tool(marker: str) -> None:
    import json

    blob = json.dumps(load_cases(), ensure_ascii=False).lower()
    assert marker.lower() not in blob


def test_no_criterion_has_a_threshold_before_the_first_measurement() -> None:
    for entry in load_criteria()["kryteria"]:
        assert entry["prog"] is None, entry["id"]


def test_the_tasks_are_not_readable_by_anything_in_the_product() -> None:
    mentions = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "services").rglob("*.py")
        if "session-vs-turn" in path.read_text(encoding="utf-8")
    ]
    assert mentions == [], (
        f"moduły produktu wskazują na zbiór odłożony: {mentions}. Zadania oceniające, które "
        "trafiają do pamięci, przestają mierzyć cokolwiek"
    )

    source = (REPO_ROOT / "evals" / "harness" / "session_vs_turn.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("asyncpg", "control_plane", "psycopg", "sqlalchemy"):
        assert forbidden not in imported, (
            f"harness porównania importuje {forbidden}; ma nie mieć jak zapisać zadania do bazy"
        )


def test_the_report_lands_where_the_other_eval_reports_do(tmp_path) -> None:
    """`artifacts/evals/` is where every eval in this repository leaves its evidence."""
    written = write_report(check(), tmp_path)
    assert written.name == "session-vs-turn-stub.json"
    assert '"werdykt_model": "NIE_URUCHOMIONO"' in written.read_text(encoding="utf-8")
