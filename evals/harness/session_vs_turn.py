"""Comparing the old turn with the new session on the same task (P2 etap B).

    python -m evals.harness.session_vs_turn            # offline half, no model, no database
    python -m evals.harness.session_vs_turn --json     # the same, machine readable
    python -m evals.harness.session_vs_turn --report   # also writes artifacts/evals/

WHAT P2 ASKS FOR, in its own words: "porównanie ze starą turą na tym samym zadaniu i modelu
pozwala ocenić wpływ środowiska". The difference under test is the ENVIRONMENT — one call with a
prepared context package against a thread with tools, memory and materials — so the model, the
base prompt and the thread must be identical on both sides. A case that only one path can run
would measure the shape of the case.

WHAT RUNS TODAY, and it is deliberately small, because without a logged-in account there is no
model:

  * every case loads and names a thread, a goal and an expectation;
  * every case is SYMMETRIC: what it offers is offered to both paths, and nothing in it names a
    tool only the new path has. This is the check that keeps the comparison honest;
  * every dimension a case uses has a criterion written before the measurement, and no criterion
    has a threshold yet.

WHAT IT DOES NOT DO: open a database, import `control_plane`, or set a pass mark. The held-out
tasks must never reach `knowledge_items` — a set that leaked into memory stops being held out —
and `evals/tests/test_session_vs_turn.py` checks both halves of that by reading imports rather
than prose.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).resolve().parents[1] / "session-vs-turn"
REPORT_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "evals"
REPORT_NAME = "session-vs-turn-stub.json"

VERDICT_STUB_OK = "ZALICZONE_NA_STUBIE"
VERDICT_FAILED = "NIE_ZALICZONE"
VERDICT_NOT_RUN = "NIE_URUCHOMIONO"

REQUIRED_CASE_FIELDS = ("id", "nazwa", "wymiary", "watek", "cel", "oczekiwane_zachowanie")
REQUIRED_THREAD_FIELDS = ("spolecznosc", "tytul", "tresc")

#: Words that would make a case runnable by the new path only. A task mentioning a tool by name
#: is a task written for the side that has it.
ONLY_NEW_PATH = ("memory.", "sources.", "catalog.", "draft.create", "narzędzie wenext")


@dataclass
class Report:
    cases: int = 0
    paths: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    verdict_stub: str = VERDICT_STUB_OK
    verdict_model: str = VERDICT_NOT_RUN
    model_reason: str = (
        "porównanie starej tury z nową sesją wymaga zalogowanego Codeksa i tego samego modelu "
        "po obu stronach; uruchamiane po wdrożeniu, nie w tej suicie"
    )

    def as_json(self) -> dict[str, Any]:
        return {
            "przypadkow": self.cases,
            "sciezki": self.paths,
            "wymiary": sorted(set(self.dimensions)),
            "problemy": self.problems,
            "werdykt_stub": self.verdict_stub,
            "werdykt_model": self.verdict_model,
            "powod_modelu": self.model_reason,
            "prog_zastosowany": None,
            "wygenerowano": datetime.now(UTC).isoformat(),
        }


def _load(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_criteria(root: Path = CASES_DIR) -> dict[str, Any]:
    return _load(root / "criteria.yaml")


def load_cases(root: Path = CASES_DIR) -> list[dict[str, Any]]:
    files = sorted((root / "cases").glob("*.yaml"))
    if not files:
        raise FileNotFoundError(
            f"w {root / 'cases'} nie ma ani jednego zadania. Zero zadań to nie jest przebieg bez "
            "zastrzeżeń, tylko przebieg, który niczego nie porównał"
        )
    return [_load(path) for path in files]


def check(root: Path = CASES_DIR) -> Report:
    report = Report()
    try:
        criteria = load_criteria(root)
        cases = load_cases(root)
    except (FileNotFoundError, ValueError) as exc:
        report.verdict_stub = VERDICT_FAILED
        report.problems.append(str(exc))
        return report

    known = {entry["id"] for entry in criteria.get("kryteria", [])}
    report.paths = [entry["id"] for entry in criteria.get("porownywane_sciezki", [])]
    if len(report.paths) != 2:
        report.problems.append(
            f"porównanie ma {len(report.paths)} ścieżek zamiast dwóch: bez dwóch nie ma czego "
            "z czym zestawić"
        )
    for entry in criteria.get("kryteria", []):
        if entry.get("prog") is not None:
            report.problems.append(
                f"kryterium {entry['id']} ma próg {entry['prog']!r} przed pierwszym pomiarem na "
                "prawdziwym modelu. Próg wpisany przed pomiarem mierzy życzenie"
            )

    report.cases = len(cases)
    for case in cases:
        name = case.get("id", "(bez id)")
        for required in REQUIRED_CASE_FIELDS:
            if not case.get(required):
                report.problems.append(f"{name}: brak pola {required}")
        thread = case.get("watek") or {}
        for required in REQUIRED_THREAD_FIELDS:
            if not thread.get(required):
                report.problems.append(f"{name}: wątek bez pola {required}")
        for dimension in case.get("wymiary", []):
            report.dimensions.append(dimension)
            if dimension not in known:
                report.problems.append(
                    f"{name}: wymiar {dimension!r} nie ma kryterium w criteria.yaml"
                )
        # THE symmetry check. A task that names a tool is a task only one side can run, and the
        # comparison would then measure the wording of the task.
        blob = json.dumps(case, ensure_ascii=False).lower()
        for marker in ONLY_NEW_PATH:
            if marker.lower() in blob:
                report.problems.append(
                    f"{name}: zadanie wymienia {marker!r}, czyli jest napisane pod nową ścieżkę. "
                    "Obie ścieżki mają dostać to samo zadanie"
                )
        if not (case.get("oczekiwane_zachowanie") or {}).get("opis"):
            report.problems.append(f"{name}: oczekiwane zachowanie bez opisu")

    if report.problems:
        report.verdict_stub = VERDICT_FAILED
    return report


def write_report(report: Report, directory: Path = REPORT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / REPORT_NAME
    path.write_text(
        json.dumps(report.as_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = check()
    if "--json" in argv:
        print(json.dumps(report.as_json(), ensure_ascii=False, indent=2))
    else:
        print(f"zadań: {report.cases}, ścieżki: {', '.join(report.paths)}")
        print(f"wymiary: {', '.join(sorted(set(report.dimensions)))}")
        for problem in report.problems:
            print(f"  - {problem}")
        print(f"część na stubie: {report.verdict_stub}")
        print(f"porównanie na modelu: {report.verdict_model} ({report.model_reason})")
    if "--report" in argv or "--json" not in argv:
        written = write_report(report)
        print(f"raport: {written}")
    return 0 if report.verdict_stub == VERDICT_STUB_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
