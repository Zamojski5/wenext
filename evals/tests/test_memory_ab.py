"""The held-out set for the memory comparison, and the rule that it stays held out (P5 etap A).

TWO THINGS ARE BEING PROTECTED HERE and they are not the same thing:

  1. the cases are usable: complete, with their dimensions covered by criteria that were written
     BEFORE any measurement, and with a memory side that really differs from the no-memory side.
     A case that fails this would produce a number nobody could interpret;
  2. the cases never reach the product's memory. A held-out set written into `knowledge_items`
     stops being held out, and the comparison then measures whether the model can read back what
     it was given. The check is static and blunt: no module under `services/` may mention this
     directory, and the harness may not import a database driver or the control plane.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evals.harness.memory_ab import (
    CASES_DIR,
    REQUIRED_CASE_FIELDS,
    VERDICT_NOT_RUN,
    VERDICT_STUB_OK,
    check,
    load_cases,
    load_criteria,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_offline_half_passes_and_the_model_half_says_it_did_not_run() -> None:
    """NIE_URUCHOMIONO, not zero. A zero in a report reads as a measurement."""
    report = check()
    assert report.problems == [], report.problems
    assert report.verdict_stub == VERDICT_STUB_OK
    assert report.verdict_model == VERDICT_NOT_RUN
    assert report.as_json()["prog_zastosowany"] is None


def test_every_case_carries_a_situation_a_memory_and_an_expectation() -> None:
    cases = load_cases()
    assert len(cases) >= 4, "za mało przypadków, żeby porównanie cokolwiek pokazało"
    for case in cases:
        for field in REQUIRED_CASE_FIELDS:
            assert case.get(field), f"{case.get('id')}: brak {field}"


def test_silence_and_a_short_answer_are_allowed_outcomes() -> None:
    """P5 says so outright, and a harness that rewarded length would quietly contradict it."""
    cases = {case["id"]: case for case in load_cases()}
    allowed = cases["usuniete-przez-moderacje"]["oczekiwane_zachowanie"]["automat"]
    assert "SILENCE" in allowed["dopuszczalne_decyzje"]
    shorter = cases["za-dlugie"]["oczekiwane_zachowanie"]["automat"]
    assert shorter["maks_znakow"] < 1400


def test_no_criterion_has_a_threshold_before_the_first_real_measurement() -> None:
    """A threshold written after seeing results measures what happened, not what was intended."""
    for entry in load_criteria()["kryteria"]:
        assert entry["prog"] is None, entry["id"]


def test_the_cases_are_not_readable_by_anything_in_the_product() -> None:
    """The isolation rule, checked rather than promised.

    Two greps: no service may mention the directory, and the harness may not reach a database.
    Blunt on purpose — a subtle check here would be a check somebody edits around.
    """
    mentions = []
    for path in (REPO_ROOT / "services").rglob("*.py"):
        if "memory-ab" in path.read_text(encoding="utf-8"):
            mentions.append(str(path.relative_to(REPO_ROOT)))
    assert mentions == [], (
        f"moduły produktu wskazują na zbiór odłożony: {mentions}. Przypadki oceniające, które "
        "trafiają do pamięci, przestają mierzyć cokolwiek"
    )

    # The IMPORTS, not the prose: the docstring names `control_plane` precisely to say it is not
    # imported, and a grep over the text would flag the sentence that promises the property.
    source = (REPO_ROOT / "evals" / "harness" / "memory_ab.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("asyncpg", "control_plane", "psycopg", "sqlalchemy"):
        assert forbidden not in imported, (
            f"harness porównania importuje {forbidden}; ma nie mieć jak zapisać przypadku do bazy"
        )


def test_an_empty_case_directory_is_an_error_and_not_a_clean_run(tmp_path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "criteria.yaml").write_text("kryteria: []\n", encoding="utf-8")
    report = check(tmp_path)
    assert report.verdict_stub != VERDICT_STUB_OK
    assert any("ani jednego przypadku" in problem for problem in report.problems)


def test_a_case_whose_memory_does_not_differ_is_refused(tmp_path) -> None:
    """If the A side and the B side hold the same thing, the comparison measures noise."""
    import shutil

    shutil.copytree(CASES_DIR, tmp_path / "set")
    weak = tmp_path / "set" / "cases" / "99-bez-pamieci.yaml"
    weak.write_text(
        "id: pusty\nnazwa: Bez pamięci\nwymiary: [trafnosc]\n"
        "sytuacja: {spolecznosc: r/x, zadanie: odpowiedz}\n"
        "pamiec: [{rodzaj: FACT, tresc: cokolwiek}]\n"
        "oczekiwane_zachowanie: {opis: cokolwiek}\nbez_pamieci: {opis: to samo}\n",
        encoding="utf-8",
    )
    report = check(tmp_path / "set")
    assert any("nie różni się" in problem for problem in report.problems), report.problems


@pytest.mark.parametrize("dimension", ["trafnosc", "zastosowanie_korekty", "koszt"])
def test_every_dimension_used_by_a_case_has_a_written_criterion(dimension: str) -> None:
    known = {entry["id"] for entry in load_criteria()["kryteria"]}
    assert dimension in known
