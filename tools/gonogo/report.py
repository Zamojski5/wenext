#!/usr/bin/env python3
"""Go/No-Go report generator for phase E1.F0.

Definition of Done (L5181): "zapadły trzy osobne Go/No-Go: headless CDP contract,
headful exception path, Codex jako marketing runtime".

Three rules this generator enforces:

1. **A lack of evidence is NOT a positive result.** A gate with no artifact gets
   `NIE_URUCHOMIONO`, never `GO`. It is the same rule as „brak danych nigdy nie jest
   zamieniany na zero" (L5242), applied to a decision instead of to a metric.

2. **Thresholds come from `criteria.yaml`, which is frozen before the measurement.**
   The generator knows no threshold hardcoded and has no way to get around one.

3. **The report says what it does NOT state.** The „Czego ten raport nie dowodzi"
   section is mandatory and comes from `docs/f0-limits/`.

Running it:
    python3 tools/gonogo/report.py --artifacts artifacts --out artifacts/gonogo
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
from dataclasses import asdict, dataclass, field
from enum import StrEnum

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Verdict(StrEnum):
    GO = "GO"
    NO_GO = "NO_GO"
    #: The gate was not run. This is NEITHER GO nor NO_GO.
    NOT_RUN = "NIE_URUCHOMIONO"
    #: The artifact exists, but it is incomplete or unreadable.
    INCOMPLETE_EVIDENCE = "DOWOD_NIEKOMPLETNY"


@dataclass
class GateResult:
    id: str
    nazwa: str
    werdykt: Verdict
    pytanie: str
    wymaga_czlowieka: bool
    evidence_source: str
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    missing_checks: list[str] = field(default_factory=list)
    uzasadnienie: str = ""


def _load_criteria(path: pathlib.Path) -> dict:
    """Reads criteria.yaml.

    A minimal parser for the subset of YAML that this file uses — with no dependency on
    PyYAML, so that the report can be generated also where there is no full environment.
    Handles: maps nested by indentation, `- ` lists, `>-` blocks, comments.
    """
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0

    while i < len(lines):
        raw_text = lines[i]
        i += 1
        if not raw_text.strip() or raw_text.lstrip().startswith("#"):
            continue

        indent = len(raw_text) - len(raw_text.lstrip())
        content = raw_text.strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        if content.startswith("- "):
            value = content[2:].strip().strip('"').strip("'")
            if not isinstance(parent, list):
                # YAML allows a sequence to sit at the SAME indent as its key. This parser
                # then pops the list off the stack at the `>=` above and lands on the dict,
                # and the silent `continue` that used to be here dropped the item — leaving
                # `kontrole_wymagane: []` with no error. An empty required-check list makes
                # every gate GO (see the guard in `_evaluate_gate`), so a reformat that does
                # not change the YAML's meaning could turn three gates green. Losing a data
                # item without saying so is the same disease as a check that passes without
                # a measurement: fail loudly instead.
                raise ValueError(
                    f"criteria.yaml, wiersz {i}: element listy `- {value}` nie ma listy "
                    "nadrzędnej. Parser nie zgadnie, do czego należy — popraw wcięcie."
                )
            parent.append(value)
            continue

        if ":" not in content:
            continue
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest in (">-", "|", ">", "|-"):
            # Text block — we collect the more deeply indented lines.
            fragments: list[str] = []
            while i < len(lines):
                next_line = lines[i]
                if next_line.strip() and (len(next_line) - len(next_line.lstrip())) <= indent:
                    break
                fragments.append(next_line.strip())
                i += 1
            if isinstance(parent, dict):
                parent[key] = " ".join(f for f in fragments if f)
            continue

        if rest == "":
            # Nesting: a map or a list — the next non-empty line decides.
            next_content = ""
            for j in range(i, len(lines)):
                if lines[j].strip() and not lines[j].lstrip().startswith("#"):
                    next_content = lines[j].strip()
                    break
            child: object = [] if next_content.startswith("- ") else {}
            if isinstance(parent, dict):
                parent[key] = child
            stack.append((indent, child))
            continue

        raw_value = rest.strip().strip('"').strip("'")
        value: object = raw_value
        if raw_value in ("true", "false"):
            value = raw_value == "true"
        else:
            try:
                value = int(raw_value)
            except ValueError:
                with contextlib.suppress(ValueError):
                    value = float(raw_value)
        if isinstance(parent, dict):
            parent[key] = value

    return root


def _evidence_is_closed(check: dict) -> bool:
    """Whether a check's evidence comes from a run that has FINISHED.

    Artifacts of multi-hour measurements are written after every cycle, so that an
    interruption does not erase hours of observation. Side effect: the file on disk may
    come from the middle of the window or from a run killed without a signal (SIGKILL,
    OOM, Docker dying), where the signal handler never ran. Such a file was measured for
    real with `zdane: true` 48 minutes into the six hours that had been declared.

    The verdict is computed by `codex_runtime.unattended` (D-009), but the report must be
    robust also against an artifact written by an OLDER version of that code — the
    container image is sometimes older than the sources on the host. Hence the same rule
    repeated here: no `zakonczony` means no evidence, and no evidence is never `GO`.

    Checks with no `przebieg` field (gates 1 and 2, the remaining checks of gate 3) have
    no measurement window and pass through this gate unchanged.
    """
    przebieg = ((check.get("szczegoly") or {}).get("przebieg")) or {}
    if not isinstance(przebieg, dict) or "rozpoczety" not in przebieg:
        return True
    return przebieg.get("zakonczony") is not None


def _evaluate_gate(identyfikator: str, definition: dict, artifacts_dir: pathlib.Path) -> GateResult:
    required = list(definition.get("kontrole_wymagane", []))
    if not required:
        # A gate with no required checks is not a passed gate — it is a gate with no
        # criterion. Without this branch every list below stays empty, control falls to the
        # final `else`, and the verdict is GO with the reason "Wszystkie 0 wymaganych
        # kontroli zdane." — `all()` over an empty set, spread across four lists. The same
        # class as D-007 and D-009, but here it would hit ALL THREE gates at once, on
        # artifacts that say `zdane: false`.
        return GateResult(
            id=identyfikator,
            nazwa=definition.get("nazwa", identyfikator),
            werdykt=Verdict.INCOMPLETE_EVIDENCE,
            pytanie=definition.get("pytanie", ""),
            wymaga_czlowieka=True,
            evidence_source=definition.get("zrodlo_dowodu", ""),
            uzasadnienie=(
                f"Bramka {identyfikator} nie ma zdefiniowanej ani jednej kontroli wymaganej. "
                "To nie jest bramka zdana, tylko bramka bez kryterium — sprawdź "
                "tools/gonogo/criteria.yaml."
            ),
        )

    # `zrodlo_dowodu` is recorded relative to the repo root and starts with `artifacts/`.
    # We swap that first component for the directory pointed at by --artifacts.
    #
    # An earlier version had a fallback here to the path in the repo root. The effect was
    # that --artifacts was ignored and the report run on an empty directory still found
    # the old artifacts, meaning it could announce GO with no fresh evidence.
    # Found by `tests/test_repo_consistency.py`, test
    # `test_report_does_not_treat_missing_evidence_as_a_pass`.
    relative_path = pathlib.PurePosixPath(definition["zrodlo_dowodu"])
    if relative_path.parts and relative_path.parts[0] == "artifacts":
        file_path = artifacts_dir.joinpath(*relative_path.parts[1:])
    else:
        file_path = artifacts_dir / relative_path

    if not file_path.exists():
        file_path = None

    baza = GateResult(
        id=identyfikator,
        nazwa=definition.get("nazwa", identyfikator),
        werdykt=Verdict.NOT_RUN,
        pytanie=definition.get("pytanie", ""),
        wymaga_czlowieka=bool(definition.get("wymaga_czlowieka", False)),
        evidence_source=definition["zrodlo_dowodu"],
        missing_checks=required,
    )

    if file_path is None:
        if baza.wymaga_czlowieka:
            baza.uzasadnienie = (
                f"Brak artefaktu {definition['zrodlo_dowodu']}. Ta bramka wymaga człowieka "
                "z kontem ChatGPT — uruchom "
                f"`{definition.get('uruchamiane_przez', 'make codex-gonogo')}`. "
                "Brak wyniku NIE jest wynikiem negatywnym ani pozytywnym."
            )
        else:
            baza.uzasadnienie = (
                f"Brak artefaktu {definition['zrodlo_dowodu']} — bramka nie została uruchomiona."
            )
        return baza

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        baza.werdykt = Verdict.INCOMPLETE_EVIDENCE
        baza.uzasadnienie = f"Artefakt {file_path} nie jest poprawnym JSON-em: {exc}"
        return baza

    checks = data.get("kontrole", [])
    kontrole = {k["nazwa"]: bool(k.get("zdane")) and _evidence_is_closed(k) for k in checks}
    # A failed check that is marked `wymaga_czlowieka` is NOT a failure of the gate —
    # it is a lack of evidence. Announcing NO_GO for a runtime nobody has tested would be
    # a false verdict, and the document requires the distinction here (L2129, L5182).
    #
    # We treat a passed check the same way when its evidence shows an UNCLOSED run:
    # that is a lack of evidence, not a failure.
    needs_human = {
        k["nazwa"]
        for k in checks
        if not kontrole.get(k["nazwa"])
        and ((k.get("szczegoly") or {}).get("wymaga_czlowieka") or not _evidence_is_closed(k))
    }

    zdane = [n for n in required if kontrole.get(n) is True]
    failed = [n for n in required if kontrole.get(n) is False and n not in needs_human]
    not_run = [n for n in required if n in needs_human]
    missing = [n for n in required if n not in kontrole]

    baza.passed_checks = zdane
    baza.failed_checks = failed
    baza.missing_checks = missing

    if missing:
        baza.werdykt = Verdict.INCOMPLETE_EVIDENCE
        baza.uzasadnienie = (
            f"Artefakt istnieje, ale nie zawiera {len(missing)} wymaganych kontroli: "
            f"{', '.join(missing)}. Niekompletny dowód nie jest podstawą do GO."
        )
    elif failed:
        baza.werdykt = Verdict.NO_GO
        baza.uzasadnienie = f"Niezdane kontrole: {', '.join(failed)}."
    elif not_run:
        baza.werdykt = Verdict.NOT_RUN
        baza.missing_checks = not_run
        baza.uzasadnienie = (
            f"Pozostałe kontrole zdane ({len(zdane)}), ale {len(not_run)} wymaga "
            f"człowieka i nie zostało uruchomione: {', '.join(not_run)}. "
            "Brak dowodu nie jest ani GO, ani NO_GO."
        )
    else:
        baza.werdykt = Verdict.GO
        baza.uzasadnienie = f"Wszystkie {len(zdane)} wymaganych kontroli zdane."

    return baza


def _render_markdown(results: list[GateResult], kryteria: dict) -> str:
    lines: list[str] = []
    lines.append("# Raport Go/No-Go — faza E1.F0\n")
    lines.append(
        f"Kryteria: wersja {kryteria.get('wersja')}, zamrożone {kryteria.get('zamrozone_dnia')}.\n"
    )
    lines.append(
        "Progi zostały zapisane **przed** uruchomieniem pomiarów. Zmiana progu po zobaczeniu "
        "wyników unieważnia bieżący wynik bramki — ta sama reguła, co przy bramce E1 (L4619).\n"
    )

    lines.append("## Podsumowanie\n")
    lines.append("| Bramka | Nazwa | Werdykt | Wymaga człowieka |")
    lines.append("| --- | --- | --- | --- |")
    for outcome in results:
        lines.append(
            f"| {outcome.id} | {outcome.nazwa} | **{outcome.werdykt.value}** | "
            f"{'tak' if outcome.wymaga_czlowieka else 'nie'} |"
        )
    lines.append("")

    go_count = sum(1 for outcome in results if outcome.werdykt is Verdict.GO)
    no_go_count = sum(1 for outcome in results if outcome.werdykt is Verdict.NO_GO)
    not_run = [outcome for outcome in results if outcome.werdykt is Verdict.NOT_RUN]

    lines.append(
        f"Rozstrzygnięte: {go_count} GO, {no_go_count} NO_GO. Nierozstrzygnięte: {len(not_run)}.\n"
    )
    if not_run:
        lines.append(
            "> **Faza E1.F0 nie jest zamknięta.** Nierozstrzygnięta bramka nie jest bramką "
            "zdaną. Dopóki którakolwiek pozycja ma `NIE_URUCHOMIONO`, nie wolno raportować "
            "F0 jako ukończonej ani przechodzić do F1.\n"
        )

    for outcome in results:
        lines.append(f"## {outcome.id} — {outcome.nazwa}\n")
        lines.append(f"**Werdykt: {outcome.werdykt.value}**\n")
        lines.append(f"*Pytanie rozstrzygające:* {outcome.pytanie}\n")
        lines.append(f"*Źródło dowodu:* `{outcome.evidence_source}`\n")
        lines.append(f"*Uzasadnienie:* {outcome.uzasadnienie}\n")
        if outcome.passed_checks:
            lines.append("Zdane kontrole:\n")
            for k in outcome.passed_checks:
                lines.append(f"- {k}")
            lines.append("")
        if outcome.failed_checks:
            lines.append("**Niezdane kontrole:**\n")
            for k in outcome.failed_checks:
                lines.append(f"- {k}")
            lines.append("")
        if outcome.missing_checks:
            lines.append("**Brakujące w dowodzie:**\n")
            for k in outcome.missing_checks:
                lines.append(f"- {k}")
            lines.append("")

    lines.append("## Czego ten raport NIE dowodzi\n")
    limits = ROOT / "docs" / "f0-limits" / "README.md"
    if limits.exists():
        lines.append(f"Pełna lista: `{limits.relative_to(ROOT)}`.\n")
    lines.append(
        "- Nie dowodzi, że prawdziwe platformy zachowują się tak jak fixture site. "
        "Kontrakt CDP dowodzi kontraktu, nie tego, że dany serwis działa headless — "
        "to jest capability probe z fazy F2 (L4381).\n"
        "- Nie dowodzi jakości predykcyjnej modeli. Evale F0 dowodzą, że harness działa "
        "i że definicja jakości jest zakodowana, nie że model osiąga jakiś poziom (L5514).\n"
        "- Nie zastępuje decyzji człowieka. Raport przygotowuje dowody; decyzję o przejściu "
        "do kolejnej fazy podejmuje człowiek (L5503).\n"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raport Go/No-Go E1.F0")
    parser.add_argument("--artifacts", type=pathlib.Path, default=ROOT / "artifacts")
    parser.add_argument(
        "--criteria", type=pathlib.Path, default=ROOT / "tools/gonogo/criteria.yaml"
    )
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "artifacts" / "gonogo")
    args = parser.parse_args(argv)

    kryteria = _load_criteria(args.criteria)
    definitions = kryteria.get("bramki", {})
    if not definitions:
        print(f"Nie udało się wczytać bramek z {args.criteria}", file=sys.stderr)
        return 2

    results = [
        _evaluate_gate(ident, definition, args.artifacts)
        for ident, definition in definitions.items()
    ]

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "gonogo.json").write_text(
        json.dumps(
            {
                "kryteria_wersja": kryteria.get("wersja"),
                "kryteria_zamrozone_dnia": kryteria.get("zamrozone_dnia"),
                "bramki": [asdict(outcome) for outcome in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    markdown = _render_markdown(results, kryteria)
    (args.out / "gonogo.md").write_text(markdown, encoding="utf-8")

    for outcome in results:
        print(f"  {outcome.id:8s} {outcome.werdykt.value:18s} {outcome.nazwa}")
    print(f"\nRaport: {args.out / 'gonogo.md'}")

    # Exit code: 0 only when nothing is NO_GO. Unresolved gates are NOT a process
    # error — they are a state that the report is there to show.
    return 1 if any(outcome.werdykt is Verdict.NO_GO for outcome in results) else 0


if __name__ == "__main__":
    sys.exit(main())
