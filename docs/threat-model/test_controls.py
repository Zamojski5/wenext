"""Threat model integrity test: a control with no enforcement point is a pretend control.

Deliverable D03 requires this explicitly (dod):

    "threat model jest wejściem do `tools/gonogo` — plik `threat-model/controls.yaml` jest
     maszynowo czytelny i sprawdzany przez test, że każda kontrola z `enforced_by` istnieje
     na dysku"

This test checks five things:

1. `controls.yaml` can be parsed.
2. Every path from `enforced_by` and from `test` exists on disk.
3. A control without `enforced_by` MUST have `status: PLANOWANE` and a `faza` given.
4. A control with `status: EGZEKWOWANE` MUST have both `enforced_by` and `test`.
5. `zrodlo_w_dokumencie` points at an existing roadmap line, and `id` and `granica` are
   consistent.

Running without pytest:

    python3 docs/threat-model/test_controls.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTROLS = pathlib.Path(__file__).resolve().parent / "controls.yaml"
DOKUMENT = ROOT / "ROADMAP_CODEX_CDP_COMMUNITY_OS.md"

ALLOWED_STATUSES = {"EGZEKWOWANE", "CZESCIOWO", "PLANOWANE"}


def _load(path: pathlib.Path) -> dict:
    """Reads controls.yaml. PyYAML when it is there; an own subset parser when it is not.

    The dependency on PyYAML is optional on purpose: the same reason for which
    `tools/gonogo/report.py` has its own parser — the report and the threat model have to
    be checkable also where no development environment is installed.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError:
        return _parsuj_podzbior(text)
    return yaml.safe_load(text)


def _parsuj_podzbior(text: str) -> dict:
    """Parser of exactly the shape that controls.yaml uses.

    Handles: maps by indentation, `- ` lists of scalars, `- id:` lists of maps, `>-` blocks,
    empty lists `[]`, quoted values, comments.
    """
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        raw_value = lines[i]
        i += 1
        if not raw_value.strip() or raw_value.lstrip().startswith("#"):
            continue

        indent = len(raw_value) - len(raw_value.lstrip())
        content = raw_value.strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        if content.startswith("- "):
            rest = content[2:].strip()
            if ":" in rest and not rest.startswith(('"', "'")):
                # A list element that is a map: `- id: C-01`
                element: dict = {}
                if isinstance(parent, list):
                    parent.append(element)
                # The following fields of this map are indented two characters past the dash.
                stack.append((indent + 1, element))
                key, _, value = rest.partition(":")
                element[key.strip()] = _skalar(value.strip())
                continue
            if isinstance(parent, list):
                parent.append(_skalar(rest))
            continue

        if ":" not in content:
            continue
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest in (">-", "|", ">", "|-"):
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
            next_content = ""
            next_indent = 0
            for j in range(i, len(lines)):
                if lines[j].strip() and not lines[j].lstrip().startswith("#"):
                    next_content = lines[j].strip()
                    next_indent = len(lines[j]) - len(lines[j].lstrip())
                    break
            if next_indent <= indent:
                # A key with no content (e.g. `test:` with nothing below) — an empty list.
                if isinstance(parent, dict):
                    parent[key] = []
                continue
            child: object = [] if next_content.startswith("- ") else {}
            if isinstance(parent, dict):
                parent[key] = child
            stack.append((indent, child))
            continue

        if isinstance(parent, dict):
            parent[key] = _skalar(rest)

    return root


def _skalar(text: str) -> object:
    text = text.strip()
    if text == "[]":
        return []
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return text


DATA = _load(CONTROLS)
CONTROL_ENTRIES: list[dict] = DATA["kontrole"]
DOCUMENT_LINE_COUNT = len(DOKUMENT.read_text(encoding="utf-8").splitlines())


def _paths(control: dict) -> list[tuple[str, str]]:
    """All paths of a control together with the name of the field they come from."""
    result: list[tuple[str, str]] = []
    for field in ("enforced_by", "test"):
        for path in control.get(field) or []:
            result.append((field, path))
    return result


def check_all() -> list[str]:
    """Returns a list of errors. An empty list = the threat model agrees with the disk."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    limits = set(DATA.get("granice", {}))

    if not CONTROL_ENTRIES:
        errors.append("controls.yaml nie zawiera ani jednej kontroli")

    for control in CONTROL_ENTRIES:
        ident = control.get("id", "<bez id>")

        if ident in seen_ids:
            errors.append(f"{ident}: zduplikowany identyfikator")
        seen_ids.add(ident)

        status = control.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{ident}: status {status!r} spoza {sorted(ALLOWED_STATUSES)}")

        if control.get("granica") not in limits:
            errors.append(f"{ident}: granica {control.get('granica')!r} nie jest zdefiniowana")

        line = control.get("zrodlo_w_dokumencie")
        if not isinstance(line, int) or not (1 <= line <= DOCUMENT_LINE_COUNT):
            errors.append(
                f"{ident}: zrodlo_w_dokumencie={line!r} nie wskazuje linii dokumentu "
                f"(1..{DOCUMENT_LINE_COUNT})"
            )

        enforced = control.get("enforced_by") or []
        testy = control.get("test") or []

        # Rule D03: an empty list of enforcement points is allowed ONLY as an explicit
        # PLANOWANE with a phase given. Silence in this place is forbidden.
        if not enforced and status != "PLANOWANE":
            errors.append(f"{ident}: brak enforced_by, a status to {status} zamiast PLANOWANE")
        if status == "PLANOWANE":
            if enforced:
                errors.append(f"{ident}: status PLANOWANE, ale enforced_by nie jest puste")
            if not control.get("faza"):
                errors.append(f"{ident}: status PLANOWANE bez podanej fazy docelowej")
        if status == "EGZEKWOWANE" and not testy:
            errors.append(f"{ident}: status EGZEKWOWANE bez wskazanego testu")

        for field, relative_path in _paths(control):
            if (ROOT / relative_path).exists():
                continue
            errors.append(f"{ident}: {field} wskazuje nieistniejącą ścieżkę {relative_path!r}")

    return errors


# ---------------------------------------------------------------------------
# pytest
# ---------------------------------------------------------------------------


def test_controls_yaml_is_consistent() -> None:
    errors = check_all()
    assert not errors, "Threat model jest niespójny:\n" + "\n".join(f"  - {b}" for b in errors)


def test_every_enforced_by_path_exists() -> None:
    missing = [
        f"{k['id']}: {s}"
        for k in CONTROL_ENTRIES
        for s in (k.get("enforced_by") or [])
        if not (ROOT / s).exists()
    ]
    assert not missing, "enforced_by wskazuje nieistniejące pliki:\n" + "\n".join(missing)


def test_gate_criteria_are_frozen() -> None:
    """Control C-34: `criteria.yaml` must carry the threshold freeze date (L4619)."""
    text = (ROOT / "tools" / "gonogo" / "criteria.yaml").read_text(encoding="utf-8")
    assert "zamrozone_dnia:" in text, "criteria.yaml bez `zamrozone_dnia` — progi nie są zamrożone"


def test_the_limits_registry_exists() -> None:
    """`tools/gonogo/report.py` quotes this file in section „Czego ten raport NIE dowodzi"."""
    assert (ROOT / "docs" / "f0-limits" / "README.md").exists()


# ---------------------------------------------------------------------------


def main() -> int:
    errors = check_all()
    statusy: dict[str, int] = {}
    for control in CONTROL_ENTRIES:
        statusy[control.get("status", "?")] = statusy.get(control.get("status", "?"), 0) + 1

    print(f"Kontroli: {len(CONTROL_ENTRIES)}")
    for status in sorted(statusy):
        print(f"  {status:12s} {statusy[status]}")
    path_count = sum(len(_paths(k)) for k in CONTROL_ENTRIES)
    print(f"Sprawdzonych ścieżek (enforced_by + test): {path_count}")

    if errors:
        print("\nBŁĘDY:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nOK — każda ścieżka istnieje, każda kontrola bez egzekwowania jest jawnie PLANOWANE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
