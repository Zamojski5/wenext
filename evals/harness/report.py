"""Machine-readable eval result for `tools/gonogo` (D19).

`make evals` produces junit XML from pytest — that is enough to know what passed.
It is not enough to know WHAT computed it: which prompt version, on which model
and effort, on which `heuristic_policy_version` and on which set of fixtures.
This module writes that context alongside the junit, in JSON.

**This report contains no verdict.** It contains `pass_rate` and an explicit
`prog_zdawalnosci_zastosowany: null`, because in F0 there is no threshold — see `NOTES.md`.
GATE-3 is settled by `tools/gonogo` on the basis of `criteria.yaml`, not by this file.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .cases import CASE_COUNT_BY_FAMILY, TOTAL_CASES
from .policy import HEURISTIC_POLICY_VERSION
from .prompts import load_all
from .types import EvalFamily, EvalRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = REPO_ROOT / "artifacts" / "evals"
CRITERIA_PATH = REPO_ROOT / "tools" / "gonogo" / "criteria.yaml"
GONOGO_REPORT_PY = REPO_ROOT / "tools" / "gonogo" / "report.py"


def report_path(runtime_kind: str) -> Path:
    """Path of the result. `WENEXT_EVALS_REPORT_DIR` overrides the default one."""
    directory = Path(os.environ.get("WENEXT_EVALS_REPORT_DIR", DEFAULT_REPORT_DIR))
    return directory / f"evals-{runtime_kind}.json"


@dataclass
class Recorder:
    """Collects records during the pytest session and writes a single JSON at the end."""

    records: list[EvalRecord]

    def __init__(self) -> None:
        self.records = []

    def add(self, record: EvalRecord) -> EvalRecord:
        self.records.append(record)
        return record

    def summary(self) -> dict:
        return build_summary(self.records)

    def write(self, runtime_kind: str) -> Path | None:
        if not self.records:
            return None
        path = report_path(runtime_kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.summary(), ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return path


def build_summary(records: Sequence[EvalRecord]) -> dict:
    ordered_items = sorted(records, key=lambda r: r.case_id)
    zdane = [r for r in ordered_items if r.passed]
    runtimes = sorted({r.runtime.kind for r in ordered_items})

    per_family: dict[str, dict] = {}
    for family in EvalFamily:
        in_family = [r for r in ordered_items if r.family is family]
        if not in_family:
            continue
        per_family[family.value] = {
            "przypadkow": len(in_family),
            "zdanych": sum(1 for r in in_family if r.passed),
            "archetypy": sorted({r.archetype for r in in_family}),
        }

    return {
        "wersja_raportu": 1,
        "deliverable": "D17",
        "zakres": "L5168 — fixture evals: discovery, klasyfikacja jakości dyskusji, draft",
        "runtime": runtimes,
        "srodowisko": {
            "python": sys.version.split()[0],
            "platforma": platform.platform(),
            "maszyna": platform.machine(),
        },
        "heuristic_policy_version": HEURISTIC_POLICY_VERSION,
        "prompty": {
            p.name: {"version": p.version, "sha256": p.sha256}
            for p in sorted(load_all().values(), key=lambda p: p.name)
        },
        "katalog_przypadkow": {
            "zadeklarowanych_lacznie": TOTAL_CASES,
            "wg_rodziny": {f.value: n for f, n in CASE_COUNT_BY_FAMILY.items()},
        },
        "wynik": {
            "uruchomionych": len(ordered_items),
            "zdanych": len(zdane),
            "niezdanych": len(ordered_items) - len(zdane),
            "pass_rate": (len(zdane) / len(ordered_items)) if ordered_items else None,
            # In F0 there is no threshold. The field exists so that its absence is EXPLICIT,
            # and not so that someone later decides we simply forgot to add it.
            "prog_zdawalnosci_zastosowany": None,
            "co_ten_wynik_znaczy": (
                "Harness działa i definicja jakości z 22.7 jest zakodowana. "
                "NIE jest to pomiar jakości predykcyjnej modelu — w F0 nie ma ground "
                "truth (L5514), a próg jakości należy do F3/F6."
            ),
        },
        "wg_rodziny": per_family,
        "przypadki": [r.as_dict() for r in ordered_items],
    }


# ---------------------------------------------------------------------------
# Reading the frozen thresholds from `tools/gonogo/criteria.yaml`
# ---------------------------------------------------------------------------


def read_criteria() -> dict:
    """Loads `criteria.yaml` WITH THE D19 PARSER — not with a copy of our own.

    If the evals had a second parser, they could read something different from the file
    than the report generator does. One parser, one source of thresholds.
    """
    import importlib.util

    if not GONOGO_REPORT_PY.exists() or not CRITERIA_PATH.exists():
        raise FileNotFoundError(
            f"Brak {GONOGO_REPORT_PY} albo {CRITERIA_PATH} — D19 nie jest jeszcze na miejscu."
        )
    nazwa = "wenext_gonogo_report"
    if nazwa in sys.modules:
        return sys.modules[nazwa]._load_criteria(CRITERIA_PATH)
    spec = importlib.util.spec_from_file_location(nazwa, GONOGO_REPORT_PY)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"Nie da się załadować {GONOGO_REPORT_PY}")
    module = importlib.util.module_from_spec(spec)
    # Registration BEFORE `exec_module`: the module declares dataclasses, and `dataclasses`
    # resolves the annotations through `sys.modules[cls.__module__]`.
    sys.modules[nazwa] = module
    spec.loader.exec_module(module)
    return module._load_criteria(CRITERIA_PATH)


def eval_criteria() -> dict:
    """The `evale` section of the frozen criteria."""
    return read_criteria().get("evale", {})


def format_console(records: Iterable[EvalRecord]) -> str:
    """A short summary for stdout — without a verdict, because the verdict belongs to D19."""
    lista = list(records)
    if not lista:
        return "evals: brak rekordów"
    zdane = sum(1 for r in lista if r.passed)
    lines = [f"evals: {zdane}/{len(lista)} przypadków zdanych (bez progu — F0)"]
    for family in EvalFamily:
        outcome = [r for r in lista if r.family is family]
        if outcome:
            lines.append(
                f"  {family.value:10s} {sum(1 for r in outcome if r.passed)}/{len(outcome)}"
                f"  archetypy: {', '.join(sorted({r.archetype for r in outcome}))}"
            )
    return "\n".join(lines)


__all__ = [
    "CRITERIA_PATH",
    "DEFAULT_REPORT_DIR",
    "Recorder",
    "build_summary",
    "eval_criteria",
    "format_console",
    "read_criteria",
    "report_path",
]
