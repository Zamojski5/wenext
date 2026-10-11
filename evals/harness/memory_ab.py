"""The A/B harness for memory: does what we remember change the next decision (P5 etap A)?

    python -m evals.harness.memory_ab            # stub run, no model, no database
    python -m evals.harness.memory_ab --json     # the same, as a machine-readable report

WHAT IT RUNS TODAY, and it is deliberately small. Without a logged-in account there is no model,
so the comparison that matters cannot happen. Rather than print a zero, this harness runs the
part that CAN be measured offline and reports the rest as `NIE_URUCHOMIONO`:

  * every case loads, and every case says what situation it is, what memory is available and
    what behaviour is expected. A case missing any of the three is a case whose result nobody
    could interpret, and it fails the run;
  * every dimension a case names exists in `criteria.yaml`, and the criteria were written before
    the measurement. A criterion invented after seeing the results measures what happened, not
    what was supposed to happen;
  * the memory side of each case really contains what is supposed to change the decision. If the
    `A` run and the `B` run differ only by noise, the comparison proves nothing later.

WHAT IT DOES NOT DO, ON PURPOSE:

  * it opens NO database connection and imports nothing from `control_plane`. The held-out cases
    must never reach `knowledge_items`: a held-out set that leaked into memory stops being
    held out, and a measurement on it measures recall of the answer sheet;
  * it sets NO pass threshold. `criteria.yaml` carries `prog: null` on every dimension until the
    first run against a real model, and the checker below refuses a file that quietly grew one
    without a recorded measurement.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The data lives beside the harness, in a directory named for what it is. The hyphen keeps it
#: out of Python's import path, which is a small extra guarantee that no production module can
#: import these cases by accident.
CASES_DIR = Path(__file__).resolve().parents[1] / "memory-ab"

VERDICT_STUB_OK = "ZALICZONE_NA_STUBIE"
VERDICT_FAILED = "NIE_ZALICZONE"
VERDICT_NOT_RUN = "NIE_URUCHOMIONO"

#: Fields a case must carry. Each one is here because a case without it produces a result nobody
#: can read: no situation means no comparison, no memory means no A side, no expectation means
#: no way to say whether the run did what it was supposed to.
REQUIRED_CASE_FIELDS = ("id", "nazwa", "wymiary", "sytuacja", "pamiec", "oczekiwane_zachowanie")
REQUIRED_SITUATION_FIELDS = ("spolecznosc", "zadanie")


@dataclass
class Report:
    """What the run found. `problems` is the reason the verdict is what it is."""

    cases: int = 0
    dimensions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    verdict_stub: str = VERDICT_STUB_OK
    verdict_model: str = VERDICT_NOT_RUN
    model_reason: str = (
        "porównanie A/B wymaga zalogowanego Codexa; uruchamiane po wdrożeniu, nie w tej suicie"
    )

    def as_json(self) -> dict[str, Any]:
        return {
            "przypadkow": self.cases,
            "wymiary": sorted(set(self.dimensions)),
            "problemy": self.problems,
            "werdykt_stub": self.verdict_stub,
            "werdykt_model": self.verdict_model,
            "powod_modelu": self.model_reason,
            "prog_zastosowany": None,
        }


def _load_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_criteria(root: Path = CASES_DIR) -> dict[str, Any]:
    return _load_yaml(root / "criteria.yaml")


def load_cases(root: Path = CASES_DIR) -> list[dict[str, Any]]:
    """Every case, in file order. An empty directory is an ERROR, not an empty run."""
    files = sorted((root / "cases").glob("*.yaml"))
    if not files:
        raise FileNotFoundError(
            f"w {root / 'cases'} nie ma ani jednego przypadku. Zero przypadków to nie jest "
            "przebieg bez zastrzeżeń, tylko przebieg, który niczego nie sprawdził"
        )
    return [_load_yaml(path) for path in files]


def check(root: Path = CASES_DIR) -> Report:
    """The offline half of the comparison. Returns a report; never raises for a bad case."""
    report = Report()
    try:
        criteria = load_criteria(root)
        cases = load_cases(root)
    except (FileNotFoundError, ValueError) as exc:
        report.verdict_stub = VERDICT_FAILED
        report.problems.append(str(exc))
        return report

    known = {entry["id"] for entry in criteria.get("kryteria", [])}
    if not known:
        report.problems.append("criteria.yaml nie ma ani jednego kryterium")

    for entry in criteria.get("kryteria", []):
        if entry.get("prog") is not None:
            # A threshold before the first real measurement is a number somebody made up.
            report.problems.append(
                f"kryterium {entry['id']} ma próg {entry['prog']!r}, a nie było ani jednego "
                "przebiegu na prawdziwym modelu. Próg wpisany przed pomiarem mierzy życzenie"
            )

    report.cases = len(cases)
    for case in cases:
        name = case.get("id", "(bez id)")
        for required in REQUIRED_CASE_FIELDS:
            if not case.get(required):
                report.problems.append(f"{name}: brak pola {required}")
        for required in REQUIRED_SITUATION_FIELDS:
            if not (case.get("sytuacja") or {}).get(required):
                report.problems.append(f"{name}: sytuacja bez pola {required}")
        for dimension in case.get("wymiary", []):
            report.dimensions.append(dimension)
            if dimension not in known:
                report.problems.append(
                    f"{name}: wymiar {dimension!r} nie ma kryterium w criteria.yaml"
                )
        memory = case.get("pamiec") or []
        kinds = {entry.get("rodzaj") for entry in memory}
        if not kinds & {"EXPERIENCE", "PREFERENCE"}:
            report.problems.append(
                f"{name}: pamięć przypadku nie zawiera ani doświadczenia, ani korekty człowieka, "
                "więc przebieg z pamięcią nie różni się od przebiegu bez niej"
            )
        if not (case.get("oczekiwane_zachowanie") or {}).get("opis"):
            report.problems.append(f"{name}: oczekiwane zachowanie bez opisu")
        if not case.get("bez_pamieci"):
            report.problems.append(
                f"{name}: brak opisu przebiegu bez pamięci, czyli nie wiadomo, z czym porównujemy"
            )

    if report.problems:
        report.verdict_stub = VERDICT_FAILED
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = check()
    if "--json" in argv:
        print(json.dumps(report.as_json(), ensure_ascii=False, indent=2))
    else:
        print(f"przypadków: {report.cases}")
        print(f"wymiary: {', '.join(sorted(set(report.dimensions)))}")
        for problem in report.problems:
            print(f"  - {problem}")
        print(f"część na stubie: {report.verdict_stub}")
        print(f"porównanie A/B na modelu: {report.verdict_model} ({report.model_reason})")
    return 0 if report.verdict_stub == VERDICT_STUB_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
