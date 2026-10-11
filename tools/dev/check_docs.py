#!/usr/bin/env python3
"""Checks whether the documentation has drifted away from the code.

Why this exists:

A document that has drifted from the code is **worse than no document at all** — a gap
makes you check, an out-of-date document makes you believe. This script turns 'we ought to
be updating the docs' into a test that fails.

What it does NOT check: whether the content is wise. It checks machine-verifiable things —
whether the paths exist, whether the numbers agree, whether verdicts have evidence,
whether decisions have a complete set of fields.

Run by `make test-all` and by the `audyt` mode of the `kontekst` skill.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

REQUIRED_DOCS = (
    "STAN-PROJEKTU.md",
    "docs/STAN-PRAC.md",
    "docs/DECYZJE.md",
    "docs/KONWENCJE.md",
    ".claude/skills/kontekst/SKILL.md",
)

#: The private project log. A checkout that has NONE of these is a public export of the code
#: (`scripts/public/export.sh` drops them): there is no document here for this meter to compare
#: with the code, so it says NIE_SPRAWDZONO instead of reporting every missing file as drift.
PROJECT_LOG_DOCS = (
    "STAN-PROJEKTU.md",
    "docs/STAN-PRAC.md",
    "docs/DECYZJE.md",
)

#: Fields that every entry in the decision log must have.
DECISION_FIELDS = (
    "**Data:**",
    "**Kontekst:**",
    "**Decyzja:**",
    "**Konsekwencje dla kodu:**",
    "**Jak to sprawdzić:**",
    "**Kiedy to wygasa:**",
)

#: Directories `.gitignore` deliberately keeps out of the repository — lines 27 to 31
#: (`var/`, `data/`, `artifacts/`, `profiles/`, `codex-home/`) and line 47
#: (`infra/docker-config/`). A FRESH CLONE HAS NONE OF THEM.
#:
#: D-015: the freshness meter used to read `artifacts/` as if it were tracked, so on every
#: machine except one developer host it died with a false `ROZJAZD DOKUMENTACJI Z KODEM` —
#: a documentation test that ran nowhere. Their absence says nothing about the documents,
#: so it is never drift; it is also never a pass, because nothing was verified. Both
#: statements are what `Problems.skip` exists for.
#:
#: `tests/test_check_docs_without_artifacts.py` asserts every entry here is still a line in
#: `.gitignore`, so the excuse cannot outlive the reason for it.
UNTRACKED_BY_DESIGN = (
    "artifacts/",
    "var/",
    "data/",
    "profiles/",
    "codex-home/",
    "infra/docker-config/",
)

#: Every verdict token this project writes down, listed EXPLICITLY because a comparison can
#: only be whole-token if it knows which whole tokens exist. Each one was found by grep, not
#: from memory:
#:   `tools/gonogo/report.py::Verdict`  → GO, NO_GO, NIE_URUCHOMIONO, DOWOD_NIEKOMPLETNY
#:   ROADMAP L5383, L5498, L5558        → PASS, CONDITIONAL_PASS, BLOCK (decyzja etapu E1.F6)
#:   ROADMAP L1427, L4602, L4621        → NO_DATA (goal health i próbka poniżej minimum)
#:
#: WHY THIS LIST EXISTS AT ALL — the seventh false pass of this project, and the worst
#: placed, because it sat in the meter that judges every other document. `check_verdicts`
#: compared the artifact's verdict with the document row as `if werdykt not in row`: a
#: SUBSTRING test. `"GO"` is a substring of `"NO_GO"`, so a row declaring **NO_GO** against
#: an artifact saying GO agreed with itself and the tool printed "OK — dokumenty zgadzają
#: się z kodem". The same hole swallowed `"PASS"` ⊂ `"CONDITIONAL_PASS"`, and it accepted a
#: truncated verdict such as `"G"` against any row at all.
#:
#: Adding a verdict is legal; adding it HERE is the price. A verdict outside this list is
#: reported rather than compared — see `check_verdicts` — so the list going stale is loud,
#: not silent.
KNOWN_VERDICTS = (
    "GO",
    "NO_GO",
    "NIE_URUCHOMIONO",
    "DOWOD_NIEKOMPLETNY",
    "PASS",
    "BLOCK",
    "NO_DATA",
    "CONDITIONAL_PASS",
)

# Length asserted, and deliberately NOT with the `assert` statement: `python -O` strips
# `assert`, and a guard that silently disappears under a flag is this project's signature
# defect — a control that never ran, printed as OK. `tests/test_check_docs_without_artifacts
# .py::test_the_verdict_vocabulary_is_explicit_and_the_traps_below_are_load_bearing` pins the
# same number from the outside, so shrinking the vocabulary breaks a test as well as import.
if len(KNOWN_VERDICTS) != 8 or len(set(KNOWN_VERDICTS)) != 8:
    raise RuntimeError(
        f"KNOWN_VERDICTS ma być ośmioma różnymi werdyktami, jest {len(KNOWN_VERDICTS)}: "
        f"{KNOWN_VERDICTS}. Dopisanie werdyktu jest legalne — podnieś tę liczbę świadomie"
    )

#: Boundary characters, and `-` is in the class ON PURPOSE. `\b` alone is not enough here:
#: `_` is a word character, so `\bGO\b` correctly refuses to match inside `NO_GO` — but `-`
#: is NOT, so `\bGO\b` matches happily inside `NO-GO`, the spelling the older artifacts use.
#: Pinned by `tests/…::test_a_hyphen_spelling_in_the_document_row_is_not_a_verdict_token`,
#: which puts `NO-GO` in a DOCUMENT ROW — the one place this class decides anything — and
#: fails the moment the two `-` are dropped: the tail `GO` then becomes the row's declaration
#: and an artifact saying GO finds agreement.
#:
#: THE COMMENT THAT USED TO STAND HERE cited
#: `test_verdict_mismatch_is_still_caught_when_the_artifact_is_present` instead, and that test
#: cannot fail for this property: it puts `NO-GO` in the ARTIFACT, where the verdict is
#: outside KNOWN_VERDICTS and the outside-the-vocabulary branch of `check_verdicts` fires
#: before any boundary is consulted (the test's own docstring says so). So the class could be
#: loosened to `\b` — an immediate false pass — with all 43 tests green. A comment citing a
#: test that does not prove its claim is D-005's phantom name in another costume.
#:
#: Longest alternative first, so that at a position where two verdicts could both start the
#: longer one wins. No pair in the list shares a prefix today; sorting removes the need to
#: know that when the list grows.
_VERDICT_TOKEN = re.compile(
    r"(?<![0-9A-Za-z_-])("
    + "|".join(re.escape(verdict) for verdict in sorted(KNOWN_VERDICTS, key=len, reverse=True))
    + r")(?![0-9A-Za-z_-])"
)

#: The same boundary, applied to the gate identifier. `\b` was used here too and had the same
#: hole one column to the left: `GATE-1` matched the row of `GATE-1-bis`, so a gate with no
#: row of its own borrowed a stranger's verdict and was counted as confronted.
#: The identifier is followed by an em dash in the real table (`| GATE-1 — kontrakt …`), so
#: the boundary must permit a space and refuse `-`, `_` and alphanumerics.
#: Pinned by `tests/…::test_a_gate_id_matches_a_whole_row_identifier_never_a_prefix_of_one`,
#: which gives the document a `GATE-1-bis` row and no `GATE-1` row at all.
_ID_BOUNDARY = r"(?![0-9A-Za-z_-])"


def verdicts_declared_in_row(row: str) -> set[str]:
    """Verdicts the row's VERDICT COLUMN declares — whole tokens, never substrings.

    THE COLUMN IS PICKED CELL BY CELL, and the search stops at the first cell that names a
    verdict at all. Two passes over the cells, in this order:

    1. The BARE verdict cell. `tools/gonogo/report.py` writes `| {id} | {nazwa} |
       **{werdykt}** |` and `docs/STAN-PRAC.md` follows it, so a cell whose entire content is
       one verdict (bare, bolded, or in backticks) is the verdict column. The FIRST such cell
       wins and the rest of the row is not consulted.
    2. Only if no cell is a bare verdict: the first cell that NAMES a verdict as a whole
       token, so a column that words its verdict inside a sentence (`**GO** (warunkowo)`,
       `werdykt GO po pomiarze`) is still confronted rather than silently unconfrontable.
       Still token-exact — the substring path does not come back as a fallback.

    WHY BOTH PASSES STOP AT THE FIRST CELL, which is the whole repair here. Pass 1 used to
    union the verdicts of EVERY cell and pass 2 used to scan the WHOLE ROW, and both leaks
    have been measured as a full `OK — dokumenty zgadzają się z kodem` against an artifact
    declaring the opposite verdict:
      * `| … | **GO** (warunkowo) | … | gdyby ściśle, byłoby NO_GO |` — the verdict cell is
        not one token, so pass 1 found nothing, pass 2 scanned the row, and the NO_GO in the
        caveat prose became a verdict the row "declares". Not hypothetical: section 25.2 of
        the roadmap introduces CONDITIONAL_PASS, and the real GATE-3 row (`docs/STAN-PRAC.md`
        line 20) already carries a bolded caveat and qualifying prose.
      * `| … | **GO** | … | NO_GO |` — a caveat column holding a bare verdict token put a
        second verdict into the declaration without touching the verdict column at all.
    The older docstring promised "the caveat cell does not get a vote". It was true only
    while the verdict cell was a single naked token, and that condition was written down
    NOWHERE — which is how a guarantee quietly becomes a wish. Regression test:
    `tests/…::test_the_verdict_column_keeps_its_authority_when_it_is_not_a_bare_token`;
    pass 2 itself is exercised with a non-empty result by
    `tests/…::test_a_verdict_worded_inside_a_cell_is_read_by_the_second_pass` (until it was
    written, every fixture in the suite was decided by pass 1, so deleting pass 2 left the
    suite green).

    KNOWN GAP, stated rather than hidden: pass 2 takes the first cell that names a verdict,
    so a row whose GATE-NAME cell discussed a verdict in prose (`| GATE-9 — czy NO_GO ma
    sens | werdykt GO | …`) would be read one column too early. It fails LOUDLY (a reported
    conflict), never as agreement, and no row of `docs/STAN-PRAC.md` has that shape. Closing
    it properly means reading the column index out of the table header, which is worth doing
    the day such a row appears — not before.

    An empty result is a legitimate answer and the caller MUST treat it as drift: a row that
    declares no verdict has not agreed with anything. Returning the empty set into an
    `all()`/`in` test that reads it as agreement is precisely the shape of the false passes
    `docs/KONWENCJE.md` records.
    """
    cells = row.split("|")
    for cell in cells:
        bare = cell.strip().strip("*`").strip()
        if bare in KNOWN_VERDICTS:
            return {bare}
    # A whole pass over the cells before this one, never interleaved per cell: a bare verdict
    # column standing to the RIGHT of a cell that merely discusses a verdict must still win.
    for cell in cells:
        named = set(_VERDICT_TOKEN.findall(cell))
        if named:
            return named
    return set()


def untracked_by_design(relative_path: str) -> bool:
    """True when `relative_path` lives under a directory the repository does not track.

    `removeprefix`, never `lstrip("./")`. `lstrip` takes a SET OF CHARACTERS, so it ate the
    leading dot of any hidden path: `.data/notes.md` became `data/notes.md`, matched the
    untracked prefix `data/`, and a TRACKED missing file was excused as "outside the
    repository" forever. Nothing in `UNTRACKED_BY_DESIGN` starts with a dot today, which is
    the only reason it never fired — and that is not a property anybody maintains.
    Regression test: `test_a_hidden_directory_is_not_mistaken_for_an_untracked_one`.
    """
    normalised = relative_path.removeprefix("./")
    return any(
        normalised == prefix.rstrip("/") or normalised.startswith(prefix)
        for prefix in UNTRACKED_BY_DESIGN
    )


class Problems:
    """Two channels, and keeping them apart is the whole point.

    `items` is DRIFT: the document says something the code does not do. `not_checked` is
    MISSING EVIDENCE: the control could not run at all, because the artifact it reads is not
    in this checkout.

    Merging them either way produces a lie. Reporting missing evidence as drift is the bug
    D-015 records (the tool died on every fresh clone). Dropping it silently is worse — it
    is the sixth-false-pass disease of this project: a control that never ran, printed as OK.
    """

    def __init__(self) -> None:
        self.items: list[str] = []
        self.not_checked: list[str] = []

    def add(self, where: str, what: str) -> None:
        self.items.append(f"{where}: {what}")

    def skip(self, where: str, why: str) -> None:
        """Records a control that had no evidence to work on. NOT a pass."""
        self.not_checked.append(f"{where}: {why}")


def check_documents_exist(p: Problems) -> None:
    for relative_path in REQUIRED_DOCS:
        if not (ROOT / relative_path).exists():
            p.add(relative_path, "brak pliku wymaganego przez skill `kontekst`")


def check_decisions(p: Problems) -> None:
    """Every D-nnn entry has a complete set of fields and a unique number."""
    file_path = ROOT / "docs" / "DECYZJE.md"
    if not file_path.exists():
        return
    content = file_path.read_text(encoding="utf-8")
    entries = re.split(r"^## (D-\d+)", content, flags=re.M)[1:]
    numbers: list[str] = []

    for i in range(0, len(entries), 2):
        numer, body = entries[i], entries[i + 1]
        numbers.append(numer)
        for field in DECISION_FIELDS:
            if field not in body:
                p.add(f"DECYZJE.md/{numer}", f"brak pola {field}")

        # „Konsekwencje dla kodu" must point at files that exist.
        section = body.split("**Konsekwencje dla kodu:**", 1)
        if len(section) == 2:
            fragment = section[1].split("- **")[0]
            for path in re.findall(r"`([a-zA-Z0-9_./-]+\.(?:py|sql|toml|yml|yaml|md))`", fragment):
                if (ROOT / path).exists():
                    continue
                # A decision may legitimately point at generated evidence (D-015 itself cites
                # `artifacts/...`). In a fresh clone that file is absent BY DESIGN, so its
                # absence is not proof the decision is stale — and not proof it is current.
                if untracked_by_design(path):
                    p.skip(
                        f"DECYZJE.md/{numer}",
                        f"wskazuje {path} — plik spoza repozytorium (.gitignore); "
                        "w tym klonie nie da się sprawdzić, czy istnieje",
                    )
                else:
                    p.add(f"DECYZJE.md/{numer}", f"wskazuje nieistniejący plik: {path}")

    if len(numbers) != len(set(numbers)):
        p.add("DECYZJE.md", f"zduplikowane numery decyzji: {numbers}")


def gate_entries(data: object, p: Problems) -> list | None:
    """Returns the gate list from a parsed Go/No-Go artifact, or None after reporting it.

    Why the shape is checked EXPLICITLY, field by field: `json.JSONDecodeError` fires on
    broken syntax only. A top-level array, a string, `null` or a number are all perfectly
    valid JSON, so the decoder passes them through, and the caller's `data.get(...)` then
    dies with `AttributeError` — a traceback in the middle of `make test-all`, which says
    nothing about the documents and kills every check that had not run yet.

    Worse is the near miss: a report whose gate list sits under a DIFFERENT KEY. `.get`
    returned its default, the loop below ran zero times, and the tool printed the unqualified
    success line. `docs/KONWENCJE.md`: "a parser that silently loses data is the same disease
    as a control with no measurement" — it has to report, not `continue`.

    A malformed artifact is drift, not NIE_SPRAWDZONO: unlike the fresh clone that has no
    artifact at all, this file is here, on this host, and it is broken.
    """
    where = "artifacts/gonogo/gonogo.json"
    if not isinstance(data, dict):
        p.add(
            where,
            "artefakt ma zły kształt: na najwyższym poziomie oczekiwano obiektu JSON "
            f"z kluczem `bramki`, jest {type(data).__name__}",
        )
        return None
    if "bramki" not in data:
        p.add(
            where,
            f"artefakt nie ma klucza `bramki` (klucze najwyższego poziomu: {sorted(data)}) — "
            "nie ma czego skonfrontować z tabelą werdyktów",
        )
        return None
    gates = data["bramki"]
    if not isinstance(gates, list):
        p.add(where, f"`bramki` nie jest listą, tylko {type(gates).__name__}")
        return None
    return gates


def check_verdicts(p: Problems) -> None:
    """The verdict in STAN-PRAC.md must agree with the artifact, not with hope.

    Agreement is checked in BOTH directions, and the second one is the one that keeps being
    forgotten. Walking the artifact catches a verdict the document contradicts; it cannot
    catch a verdict the document declares and the artifact never mentions, because that gate
    is simply not in the loop. Both halves have already produced a full `OK` line here —
    zero gates in the artifact, and one gate against three **GO** rows.
    """
    status_doc = ROOT / "docs" / "STAN-PRAC.md"
    report = ROOT / "artifacts" / "gonogo" / "gonogo.json"
    if not status_doc.exists():
        return
    content = status_doc.read_text(encoding="utf-8")

    if not report.exists():
        # D-015: `artifacts/` is in `.gitignore` (line 29), so a fresh clone NEVER has this
        # file. The old code turned that absence into drift — "twierdzi GO, ale nie ma
        # artifacts/gonogo/gonogo.json" — and the whole tool then failed for everyone except
        # the one host that had run the gates. That false alarm is what D-015 orders removed.
        #
        # It must not flip to the opposite error. A missing report does not make the **GO**
        # rows in the document true; it makes them UNVERIFIED. So we count them and say so by
        # name, and `--require-artifacts` makes it fatal on the host that owes the evidence.
        # Silence here would be the false pass this project has already shipped six times.
        unverified_go = len(re.findall(r"\*\*GO\*\*", content))
        p.skip(
            "STAN-PRAC.md ↔ artifacts/gonogo/gonogo.json",
            "brak artefaktu (katalog `artifacts/` jest w .gitignore, więc świeży klon go nie "
            f"ma) — zgodności werdyktów z dowodem NIE sprawdzono; deklaracji **GO** w "
            f"dokumencie bez konfrontacji z artefaktem: {unverified_go}. "
            "Uruchom `make gonogo`, żeby ta kontrola miała na czym pracować",
        )
        return

    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        # An unreadable artifact proves exactly as much as no artifact — but unlike a fresh
        # clone it is a defect on this host, so it is drift, not NIE_SPRAWDZONO. Reported
        # rather than raised: a traceback in the middle of `make test-all` says nothing about
        # the documents and hides every other check that had not run yet.
        p.add("artifacts/gonogo/gonogo.json", f"artefakt nie jest czytelnym JSON-em: {error}")
        return

    gates = gate_entries(data, p)
    if gates is None:
        return

    # THE COUNT IS CHECKED SEPARATELY FROM THE PREDICATE, and this is the third time in this
    # project that the empty set has produced a false pass (`all([])` twice, see
    # `docs/KONWENCJE.md`). The loop below over an empty list confronts NOTHING, adds no
    # problem, and `main()` then prints "OK — dokumenty zgadzają się z kodem". D-011 states
    # the rule this violates: a gate with no defined controls is not a passed gate.
    #
    # Counted, not assumed: `confronted` below is incremented ONLY where a verdict was
    # actually compared with a row of the document, and — unlike the first version of this
    # fix — the counter is then READ. Incrementing a counter and dropping it is a guard that
    # exists in the comment and nowhere else.
    if not gates:
        # Counted INSIDE the branch that reads it. It used to be computed on the main path
        # and used only here: a scan of the whole document performed on every run of
        # `make test-all` whose result was thrown away in every case but this one. Dead work
        # on a hot path is also a lie about what the code depends on — a reader takes a value
        # computed at the top for one the checks below need.
        unverified_go = len(re.findall(r"\*\*GO\*\*", content))
        p.add(
            "artifacts/gonogo/gonogo.json",
            "artefakt istnieje, ale lista `bramki` jest pusta — ani jednej konfrontacji "
            f"werdyktu z dowodem; deklaracji **GO** w dokumencie: {unverified_go}. "
            "Uruchom `make gonogo`; pusty artefakt nie jest zaliczeniem (D-011)",
        )
        return

    confronted = 0
    # Text of every document line a gate verdict was actually held against — the row is the
    # only thing this loop and the coverage pass below share, and the coverage pass has to
    # answer "was THIS line confronted". Stripped on both sides so a CRLF checkout cannot
    # turn the whole control into a false alarm: `$` under `re.M` stops before `\n` and
    # leaves the `\r` on the match, while `splitlines()` removes it.
    confronted_rows: set[str] = set()
    for gate in gates:
        if not isinstance(gate, dict):
            p.add("artifacts/gonogo/gonogo.json", f"wpis bramki nie jest obiektem: {gate!r}")
            continue
        ident, werdykt = gate.get("id"), gate.get("werdykt")
        # `isinstance(..., str)`, not merely truthiness — the same defect as the shape check
        # above, one level deeper, and the first round of this fix missed it: it validated
        # the CONTAINERS and then trusted the fields. `{"id": 1}` is valid JSON and truthy,
        # and killed the tool on `re.escape(1)` (`TypeError: decoding to str`);
        # `{"werdykt": 7}` killed it on `7 not in "<row>"`. A traceback here takes every
        # check that had not run yet down with it.
        if not isinstance(ident, str) or not isinstance(werdykt, str) or not ident or not werdykt:
            p.add(
                "artifacts/gonogo/gonogo.json",
                f"wpis bramki bez `id`/`werdykt` jako niepustych napisów: {gate}",
            )
            continue
        row = re.search(rf"^\|\s*{re.escape(ident)}{_ID_BOUNDARY}.*$", content, re.M)
        if not row:
            p.add("STAN-PRAC.md", f"brak wiersza dla {ident} w tabeli bramek")
            continue
        confronted += 1
        confronted_rows.add(row.group(0).strip())

        # A verdict the vocabulary does not contain cannot be compared as a token, and it is
        # NOT to be compared as a substring instead — that is the defect being repaired. It
        # is drift, not NIE_SPRAWDZONO: the artifact is here, on this host, and `report.py`
        # cannot have produced this string, so the file is not the artifact this control
        # believes it is reading. Reported with the value, so the operator sees whether it is
        # a typo, a truncation (`"G"` used to agree with every row in the document) or a
        # genuinely new verdict that belongs in KNOWN_VERDICTS.
        if werdykt not in KNOWN_VERDICTS:
            p.add(
                "artifacts/gonogo/gonogo.json",
                f"{ident}: werdykt `{werdykt}` spoza słownika werdyktów "
                f"({', '.join(KNOWN_VERDICTS)}) — nie ma go z czym porównać na całym tokenie. "
                "Albo to literówka w artefakcie, albo nowy werdykt do dopisania w "
                "KNOWN_VERDICTS w tools/dev/check_docs.py",
            )
            continue

        # WHOLE TOKEN, NEVER A SUBSTRING. `if werdykt not in row.group(0)` stood here and it
        # was the seventh false pass: `"GO" in "| GATE-1 … | **NO_GO** | …"` is True, so a
        # document declaring the opposite verdict passed. Same for `"PASS"` inside
        # `"CONDITIONAL_PASS"`. See KNOWN_VERDICTS above for the full account.
        #
        # EXACT SET EQUALITY, not `werdykt in declared`. `in` asks "is the artifact's verdict
        # among those the column names", which accepts a column naming TWO verdicts
        # (`| **GO** albo NO_GO |`) whichever one the artifact carries — the same false pass
        # as the caveat leak, moved inside one cell, and the one shape narrowing the column to
        # the first cell does NOT close. An ambiguous verdict column is not evidence: it is
        # reported, with both verdicts named, so the operator moves the qualification into the
        # caveat column instead of the verdict one.
        declared = verdicts_declared_in_row(row.group(0))
        if declared != {werdykt}:
            # Both degenerate cases are spelled out rather than left to speak for themselves:
            # a row that declares no verdict at all lands here too, and it must read as
            # "confronted with nothing", not as a comparison that happened to come out equal.
            if not declared:
                named = "żadnego znanego werdyktu"
            elif len(declared) > 1:
                named = (
                    f"kilka werdyktów naraz ({', '.join(sorted(declared))}) — kolumna werdyktu "
                    "jest niejednoznaczna, więc nie ma czego skonfrontować"
                )
            else:
                named = next(iter(declared))
            p.add(
                "STAN-PRAC.md",
                f"{ident}: dokument mówi co innego niż artefakt (artefakt: {werdykt}; "
                f"wiersz deklaruje: {named})",
            )

    # Defence in depth, and it is unreachable TODAY on purpose: every `continue` above files
    # a problem first, so a non-empty gate list cannot reach here at zero without something
    # already being reported. It stays because that property is an accident of the three
    # branches above, not an invariant anybody maintains — and the cost of the empty set
    # producing a pass has already been paid three times in this project.
    if confronted == 0:
        p.add(
            "artifacts/gonogo/gonogo.json",
            f"artefakt ma {len(gates)} wpisów bramek, ale ani jeden nie został skonfrontowany "
            "z tabelą werdyktów — brak konfrontacji nie jest zaliczeniem (D-011)",
        )

    # THE SECOND HALF OF THE SAME COUNT GUARD, and the hole the first fix left open.
    # Comparing the number of confrontations with ZERO only catches an artifact that carries
    # no gates at all. An artifact carrying ONE gate against a document declaring three left
    # two **GO** verdicts confronted with nothing — and the tool printed the unqualified
    # "OK — dokumenty zgadzają się z kodem". Measured, not supposed: three **GO** rows against
    # a one-gate artifact exited 0 with the full OK line. Iteration is driven by the ARTIFACT,
    # so the document's surplus claims are invisible to the loop above; only walking the
    # document closes it. D-011: a verdict nobody confronted is not a verdict with evidence.
    #
    # Two channels, because a meter that raises false alarms gets switched off and a
    # switched-off meter measures nothing (the same lesson as the Polish-identifier meter
    # below). A TABLE ROW carries a gate id, so it can be matched and its failure to match is
    # drift. A **GO** in prose cannot be matched against anything, so it is NIE_SPRAWDZONO —
    # visible, exit code 0, and fatal under `--require-artifacts` on the host that owes the
    # evidence. Silence was the third option and it is the one that is forbidden.
    #
    # WHY THIS ONE STAYS A SUBSTRING TEST while the verdict comparison above became a
    # whole-token one — checked deliberately when the substring bug was found, because the
    # obvious "tighten it the same way" is wrong here and the reason is not visible from the
    # code. `**GO**` is not a substring of `**NO_GO**` (the `**` on both sides is the token
    # boundary), so this line does NOT carry the defect fixed above. What it does carry is a
    # coupling to one spelling — and that narrowness is load-bearing:
    # `verdicts_declared_in_row(stripped)` was substituted here and run against the real
    # `docs/STAN-PRAC.md`. Result, measured: 2 false `ROZJAZD` alarms on the RUNS table
    # (rows `| 1 | …` and `| 6, 17:22→20:22 UTC | … | **`GO`** |`, which have run numbers
    # where the matcher looks for gate ids) and 5 spurious NIE_SPRAWDZONO lines on prose
    # sentences that merely discuss the word `GO` ("stary `GO` zostaje na dysku"). Exit 1 on
    # a document that is correct.
    #
    # A meter that cries wolf gets switched off, and a switched-off meter measures nothing —
    # the lesson recorded two paragraphs up. So this pass stays narrow ON PURPOSE: it fires
    # only on the one spelling the gate table and `tools/gonogo/report.py` actually use for a
    # verdict column, `**GO**`. If a gate row ever declares GO in another markup, widen it by
    # matching the GATE TABLE first (rows whose first cell is a gate id) and only then reading
    # the verdict cell — never by loosening the pattern over every line of the document.
    for line in content.splitlines():
        stripped = line.strip()
        if "**GO**" not in stripped or stripped in confronted_rows:
            continue
        if stripped.startswith("|"):
            p.add(
                "STAN-PRAC.md",
                f"wiersz deklaruje **GO**, a artefakt nie ma dla niego bramki: {stripped}",
            )
        else:
            p.skip(
                "STAN-PRAC.md",
                f"deklaracja **GO** poza tabelą bramek, bez identyfikatora do skonfrontowania "
                f"z artefaktem: {stripped}",
            )


def check_test_counts(p: Problems) -> None:
    """Test counts in the repository map have to be true (tolerance ±10%).

    We count `def test_` functions, because parametrisation changes the number of cases,
    not the number of tests in a file — the document describes the latter.
    """
    status_doc = ROOT / "docs" / "STAN-PRAC.md"
    if not status_doc.exists():
        return
    content = status_doc.read_text(encoding="utf-8")

    for row in re.findall(r"^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(\d+)\s*\|", content, re.M):
        directory, declared_count = row[0].rstrip("/"), int(row[1])
        path = ROOT / directory
        if not path.exists():
            # Same rule as everywhere else in this file: a directory the repository does not
            # track (`artifacts/`, `var/`, …) is simply absent in a fresh clone, so counting
            # tests in it is impossible — not failed, not passed. See UNTRACKED_BY_DESIGN.
            if untracked_by_design(directory):
                p.skip(
                    "STAN-PRAC.md",
                    f"{directory}: katalog spoza repozytorium (.gitignore) — "
                    f"deklarowanych {declared_count} testów nie policzono",
                )
            else:
                p.add("STAN-PRAC.md", f"mapa wskazuje nieistniejący katalog: {directory}")
            continue
        actual_count = sum(
            len(re.findall(r"^\s*(?:async )?def test_", f.read_text(encoding="utf-8"), re.M))
            for f in path.rglob("test_*.py")
            if ".venv" not in f.parts
        )
        if actual_count == 0 and declared_count == 0:
            continue
        if abs(actual_count - declared_count) > max(2, declared_count * 0.1):
            p.add(
                "STAN-PRAC.md",
                f"{directory}: deklaruje {declared_count} testów, jest {actual_count}",
            )


def check_conventions(p: Problems) -> None:
    """D-010 convention: identifiers in English. We measure drift, not zero tolerance.

    Names that are simultaneously KEYS IN DATA are excluded — they are Polish on purpose
    and renaming one of them would decouple the Go/No-Go report from its evidence. The
    single source of that list is `rename_identifiers.FORBIDDEN_NAMES`, so the meter and
    the rename tool cannot drift apart.

    The `problem` stem was dropped from the pattern: it is an English word, and it made
    the meter report `problems`/`Problems` in this very file as Polish debt. A meter that
    counts false positives cannot be tightened, and an untightenable meter stops meaning
    anything.
    """
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from rename_identifiers import FORBIDDEN_NAMES

    IGNORED_DIRS = {".git", ".venv", "__pycache__", "artifacts", ".pytest_cache", "var"}
    found = polish_identifiers(IGNORED_DIRS, FORBIDDEN_NAMES)

    # Zero, not a margin. D-010 says the threshold is to be lowered once the migration ends;
    # it has ended, and the measured debt is 0, so any margin is a licence for silent drift.
    # The previous margin of 5 was not theoretical: injecting six Polish names into a tracked
    # file was counted as four and stayed silent, because the alarm fires on `> THRESHOLD`.
    THRESHOLD = 0
    if len(found) > THRESHOLD:
        listing = ", ".join(f"{name} ({where})" for name, where in sorted(found.items())[:12])
        more = f" i {len(found) - 12} więcej" if len(found) > 12 else ""
        p.add(
            "konwencje (D-010)",
            f"polskich identyfikatorów: {len(found)}, próg {THRESHOLD}. {listing}{more}. "
            "Albo je przemianuj, albo świadomie podnieś próg z uzasadnieniem w DECYZJE.md",
        )


#: Polish orthography that survives transliteration to ASCII identifiers.
#:
#: Replaces a closed list of 26 word stems chosen against the PRE-migration codebase. That
#: list read 0 while 36 Polish names were actually present — it matched none of `maszyna`,
#: `pobranie`, `RAPORT`, `PROG_POKRYCIA`, `ARCH_NA_PLATFORME`, and it had `problem` in it,
#: an English word, so it reported this file's own `problems` as debt. A meter that both
#: misses real debt and invents fake debt cannot be tightened, and an untightenable meter
#: stops meaning anything.
#:
#: These three patterns are orthographic, not lexical: `sz/cz/rz/prz/trz` and the inflectional
#: endings below do not occur in English identifiers. Calibrated against the whole repo:
#: zero false positives on names such as `ALLOWED_METHODS`, `exactly_one_of_value_or_reason`,
#: `reattach`, `timezone`, `test_one_role_cannot_get_two_threads`.
#:
#: KNOWN GAP, stated rather than hidden: it is a heuristic, so a Polish name built only from
#: letter sequences English also uses slips through — `rejestr`, `spojny` and `zamrozone` did,
#: and were found by hand. Widen the stems when the next one turns up; do NOT relax the
#: threshold instead.
_POLISH_CLUSTERS = re.compile(r"(szcz|prz|trz|krz|wsz|dzi|cz|sz|rz|chn)", re.I)
_POLISH_ENDINGS = re.compile(
    r"(osc|osci|anie|enie|acja|acji|ycja|ycji|owy|owa|owe|ych|nych|nym|"
    r"ujac|owany|owane|owania|owanie|iona|iony|onych)$",
    re.I,
)
#: Prefix match, deliberately: Polish inflects, so the stem `sciezk` has to catch `sciezka`,
#: `sciezki`, `sciezke`. Anchoring every stem at the end let `sciezka_pliku` through.
#:
#: Four stems are anchored anyway, because as free prefixes they sit inside ENGLISH words —
#: each was caught by the negative control, not by reasoning:
#:   prog  ⊂ progress   (this one fired on test_run_in_progress_is_never_a_pass)
#:   proba ⊂ probability
#:   slow  ⊂ slowly
#:   pol[ae] ⊂ polar, polling
_POLISH_STEMS = re.compile(
    r"^(raport|blok|prog(i|u|ow|iem)?$|pokryci|maszyn|wartosc|zbior|platform[ae]|pobrani|"
    r"katalog|liczb|wynik|kontrol|blad|bled|sciezk|tresc|zadani|nazw|plik|dane|klucz|wpis|"
    r"opis|powod|zrodl|prob[ay]$|krok|dlugosc|rozmiar|poziom|zakres|granic|tryb|rola|dostep|"
    r"zasob|lini|slow[aoy]$|wymagan|pozycj|pol[ae]$|kolejn|domysln|odrzuc|zamrozon|widzian|"
    r"surow|czyst|pozno|rodzic|rodzin|egzekwowan|dodatkow|faktyczn|ciasteczk|czesci|czytaj|"
    r"szyfrator|rejestr|spojn|limit[oy]w|istniej)",
    re.I,
)


def polish_part(name: str, forbidden: frozenset[str] = frozenset()) -> str | None:
    """Returns the Polish-looking fragment of `name`, or None.

    Splits on non-letters so that `ARCH_NA_PLATFORME` is judged part by part — a name is
    Polish if ANY of its parts is, which is what catches half-translated identifiers.
    """
    if name in forbidden or len(name) <= 2:
        return None
    for part in (fragment for fragment in re.split(r"[^a-zA-Z]+", name) if fragment):
        if len(part) < 3:
            continue
        if (
            _POLISH_CLUSTERS.search(part)
            or _POLISH_ENDINGS.search(part)
            or _POLISH_STEMS.match(part)
        ):
            return part
    return None


def polish_identifiers(ignored_dirs: set[str], forbidden: frozenset[str]) -> dict[str, str]:
    """Maps every Polish identifier in the repo to the first file it was seen in.

    Test function names are INCLUDED. The previous meter skipped anything starting with
    `test_`, while D-010 covers test names explicitly — so the one category the migration
    was most likely to miss was the one category the meter could not see.
    """
    import ast

    found: dict[str, str] = {}
    for file_path in ROOT.rglob("*.py"):
        if ignored_dirs & set(file_path.parts):
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = node.name
            elif isinstance(node, ast.arg):
                name = node.arg
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                name = node.id
            if name and name not in found and polish_part(name, forbidden):
                found[name] = str(file_path.relative_to(ROOT))
    return found


def is_public_export() -> bool:
    """True when none of the project-log documents exists, i.e. the code without its log."""
    return not any((ROOT / relative_path).exists() for relative_path in PROJECT_LOG_DOCS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spójność dokumentacji z kodem")
    parser.add_argument("--details", action="store_true")
    parser.add_argument(
        "--require-artifacts",
        action="store_true",
        help=(
            "Traktuj NIE_SPRAWDZONO jak błąd. Dla hosta, który sam wytwarza dowody: tam brak "
            "artefaktu nie jest świeżym klonem, tylko pomiarem, którego nikt nie uruchomił"
        ),
    )
    args = parser.parse_args(argv)

    # Not a pass and not drift: the documents this tool compares with the code are not in this
    # checkout at all. Printed as NIE_SPRAWDZONO, like any other control without evidence, and
    # `--require-artifacts` still turns it into a failure.
    if is_public_export():
        print(
            "check-docs: NIE_SPRAWDZONO, brak dziennika projektu "
            f"({', '.join(PROJECT_LOG_DOCS)}); to publiczny eksport kodu, nie ma czego "
            "porównywać (to NIE jest zaliczenie)"
        )
        return 1 if args.require_artifacts else 0

    p = Problems()
    for check in (
        check_documents_exist,
        check_decisions,
        check_verdicts,
        check_test_counts,
        check_conventions,
    ):
        check(p)

    # Printed BEFORE the verdict line and on every run, including a successful one. The whole
    # defect this section repairs (D-015) was a control that could not run; a control that
    # cannot run has to be visible, or the next reader takes the exit code for a full pass.
    if p.not_checked:
        print("check-docs: NIE_SPRAWDZONO — kontrole bez dowodu (to NIE jest zaliczenie)")
        for skipped in p.not_checked:
            print("  -", skipped)

    if p.items:
        print("check-docs: ROZJAZD DOKUMENTACJI Z KODEM")
        for problem in p.items:
            print("  -", problem)
        if args.details:
            print("\nZasada: jeżeli dokument twierdzi coś, czego kod nie robi — popraw DOKUMENT.")
        return 1

    if p.not_checked:
        if args.require_artifacts:
            print(
                f"check-docs: BŁĄD — {len(p.not_checked)} kontroli bez dowodu, a wywołano "
                "z --require-artifacts. Uruchom brakujące pomiary albo zdejmij tę flagę"
            )
            return 1
        # Deliberately NOT the plain "OK — dokumenty zgadzają się z kodem": part of the
        # documentation was never confronted with anything. Exit code 0, because a fresh
        # clone is an ACCEPTABLE STATE (D-015) — but the sentence must not overclaim.
        print(
            f"check-docs: OK w tym, co dało się sprawdzić — {len(p.not_checked)} kontroli "
            "bez dowodu (NIE_SPRAWDZONO powyżej)"
        )
        return 0

    print("check-docs: OK — dokumenty zgadzają się z kodem")
    return 0


if __name__ == "__main__":
    sys.exit(main())
