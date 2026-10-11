"""Consistency test of the register of what is not feasible in F0.

Deliverable D04 requires this explicitly (dod):

    "brak w rejestrze pozycji, która jednocześnie figuruje jako DoD F0 (test spójności)"

The contradiction this test guards against is real: a register that lists something the F0
scope promises to prove invalidates either the register or the Definition of Done. One of
the two would have to be false.

The test checks four things:

1. Every entry has the complete set of fields: „Czego F0 nie dowodzi", a rationale
   („Dlaczego..."), „Faza docelowa" and „Test, który to zamknie".
2. No entry duplicates a Definition of Done point of phase F0 (L5175-5182).
3. There are at least eight entries — that is how many the D04 specification enumerates.
4. `tools/gonogo/report.py` actually refers to this directory.

Running without pytest:

    python3 docs/f0-limits/test_registry.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTER = pathlib.Path(__file__).resolve().parent / "README.md"
DOKUMENT = ROOT / "ROADMAP_CODEX_CDP_COMMUNITY_OS.md"
REPORT = ROOT / "tools" / "gonogo" / "report.py"

#: Definition of Done of phase E1.F0 — the exact document lines.
DOD_F0_LINES = range(5175, 5183)

REQUIRED_FIELDS = ("Czego F0 nie dowodzi", "Faza docelowa", "Test, który to zamknie")

#: Above this share of common content words we treat the entry's thesis as duplicating a DoD
#: point. Measured 2026-08-30: the maximum coverage in this register is 29% (entry 2
#: against L5182), so a threshold of 50% leaves a margin and still catches a thesis copied
#: from the DoD. Entry 2 is a borderline case here on purpose — it is the closest to the
#: Definition of Done and it is what justifies comparing the thesis, not the whole entry.
COVERAGE_THRESHOLD = 0.5

IGNORED_WORDS = {
    "ktora",
    "które",
    "ktore",
    "który",
    "ktory",
    "jednoczesnie",
    "figuruje",
    "jako",
    "przez",
    "wtedy",
    "zostac",
    "zostać",
    "bez",
    "oraz",
    "albo",
    "dla",
    "jest",
    "sie",
    "się",
    "nie",
    "tego",
    "tym",
    "tej",
    "ten",
    "tak",
    "wszystkie",
    "kazda",
    "każda",
}


def _words(text: str) -> set[str]:
    """Content words: no markdown, no punctuation, minimum five characters."""
    clean = re.sub(r"`[^`]*`", " ", text)
    clean = re.sub(r"[*_\[\]()#|>-]", " ", clean).lower()
    return {
        s
        for s in re.findall(r"[0-9a-ząćęłńóśźż]+", clean)
        if len(s) >= 5 and s not in IGNORED_WORDS
    }


def _entries() -> list[tuple[str, str]]:
    """Returns a list of (title, content) for the register's `## N. ...` headings."""
    text = REGISTER.read_text(encoding="utf-8")
    naglowki = list(re.finditer(r"^## (\d+)\.\s+(.+)$", text, flags=re.MULTILINE))
    result: list[tuple[str, str]] = []
    for i, match in enumerate(naglowki):
        start_pos = match.end()
        end_pos = naglowki[i + 1].start() if i + 1 < len(naglowki) else len(text)
        result.append((f"{match.group(1)}. {match.group(2)}", text[start_pos:end_pos]))
    return result


def _teza(content: str) -> str:
    """Content of the „Czego F0 nie dowodzi" field — up to the next field of the list."""
    match = re.search(
        r"\*\*Czego F0 nie dowodzi:\*\*(.*?)(?=\n- \*\*|\Z)", content, flags=re.DOTALL
    )
    return match.group(1) if match else ""


def _punkty_dod() -> list[str]:
    lines = DOKUMENT.read_text(encoding="utf-8").splitlines()
    return [lines[n - 1].lstrip("- ").strip() for n in DOD_F0_LINES]


def check_all() -> list[str]:
    errors: list[str] = []
    ranked = _entries()

    if len(ranked) < 8:
        errors.append(f"rejestr ma {len(ranked)} pozycji, specyfikacja D04 wylicza co najmniej 8")

    for title_excerpt, content in ranked:
        for field in REQUIRED_FIELDS:
            if f"**{field}:**" not in content:
                errors.append(f"{title_excerpt}: brak pola „{field}”")
        if "**Dlaczego" not in content:
            errors.append(f"{title_excerpt}: brak uzasadnienia („Dlaczego...”)")

        # We compare ONLY the entry's thesis — title and the „Czego F0 nie dowodzi" field.
        # Other fields may quote the document, Definition of Done included; the thesis must
        # not duplicate it, because then F0 would at once promise something and deny it.
        entry_words = _words(title_excerpt + " " + _teza(content))
        for number, punkt in zip(DOD_F0_LINES, _punkty_dod(), strict=True):
            dod_words = _words(punkt)
            if not dod_words:
                continue
            coverage = len(dod_words & entry_words) / len(dod_words)
            if coverage >= COVERAGE_THRESHOLD:
                errors.append(
                    f"{title_excerpt}: powiela DoD F0 z L{number} (pokrycie {coverage:.0%}): {punkt!r}"
                )

    if "f0-limits" not in REPORT.read_text(encoding="utf-8"):
        errors.append("tools/gonogo/report.py nie odwołuje się do docs/f0-limits/")

    return errors


# ---------------------------------------------------------------------------
# pytest
# ---------------------------------------------------------------------------


def test_the_registry_is_consistent() -> None:
    errors = check_all()
    assert not errors, "Rejestr jest niespójny:\n" + "\n".join(f"  - {b}" for b in errors)


def test_no_entry_duplicates_a_dod_f0_item() -> None:
    errors = [b for b in check_all() if "powiela DoD F0" in b]
    assert not errors, "\n".join(errors)


def test_the_gonogo_generator_cites_the_registry() -> None:
    """`report.py` prints this file's path in section „Czego ten raport NIE dowodzi"."""
    content = REPORT.read_text(encoding="utf-8")
    assert '"f0-limits" / "README.md"' in content


# ---------------------------------------------------------------------------


def main() -> int:
    ranked = _entries()
    print(f"Pozycji w rejestrze: {len(ranked)}")
    for title_excerpt, content in ranked:
        faza = re.search(r"\*\*Faza docelowa:\*\*\s*(.+)", content)
        print(f"  {title_excerpt:60.60s} → {faza.group(1).strip() if faza else '???'}")

    errors = check_all()
    if errors:
        print("\nBŁĘDY:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nOK — komplet pól, żadna pozycja nie powiela Definition of Done fazy F0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
