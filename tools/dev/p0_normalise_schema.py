"""Compare two `pg_dump --schema-only` outputs after normalising away what cannot differ
meaningfully (P0, used by `tools/dev/p0_restore_check.sh`).

    python3 tools/dev/p0_normalise_schema.py <schema-a.sql> <schema-b.sql> <diff-out.txt>
                                             [katalog-prefiksow]

Exit code 0 when the two schemas are the same after normalisation, 1 when they are not, 2 when
the comparison could not be made at all, and 3 when B is exactly what A's migrations produce up
to some file and the rest have simply not been applied there yet. The third case is deliberately
NOT the second: a missing file is "not measured", never "they differ", and never "they match".

EXIT CODE 3 IS THE ONE WORTH EXPLAINING. "The live database drifted from the migrations" and
"the live database is two migrations behind the repository" are different facts with different
fixes, and a comparison that reports both as "different" makes the second one look like damage.
Given `katalog-prefiksow` (a directory of `NN_name.sql.schema` dumps, one per migration applied
in order), this script finds the newest prefix whose schema B matches exactly, and then says
which migrations are missing by name. If no prefix matches, the difference is real drift and the
exit code stays 1.

WHAT IS NORMALISED AWAY, AND WHY EACH ONE IS SAFE TO DROP:

  * `SET`, `SELECT pg_catalog.set_config(...)` and the `\\connect` preamble, which describe the
    dumping session rather than the schema;
  * `-- ` comments and `--` banner lines, including the dump's own version header (two servers of
    the same pinned image still print their build line);
  * `OWNER TO` clauses and `GRANT`/`REVOKE`, because the live database and a throwaway container
    are owned by roles created at different times;
  * `COMMENT ON EXTENSION`, which the extension's own installer writes;
  * blank lines and trailing whitespace.

WHAT IS NOT NORMALISED AWAY, on purpose: column order inside a CREATE TABLE, constraint names,
index definitions, defaults, NOT NULL. Those are the things a migration applied out of order, or
a hand-made change on the live database, would actually change. Normalising them would turn this
comparison into a test that two files are both non-empty.

ORDERING. The statements are compared as SETS, so a dump that emits the same objects in another
order passes. `pg_dump` orders by object name within a kind, but the two servers created their
objects in different orders (one by migrations from scratch, one over months), and a difference
in emission order is not a difference in schema. What IS reported, for both directions, is every
statement present in one and absent in the other.
"""

from __future__ import annotations

import pathlib
import re
import sys

#: Whole-line prefixes that describe the session or the dumper, not the schema.
SESSION_PREFIXES = (
    "SET ",
    "SELECT pg_catalog.set_config",
    "\\connect",
    "\\restrict",
    "\\unrestrict",
)

#: Statements about who may do what. The live database and a fresh container differ here by
#: construction, and P0 is about the shape of the data, not about roles.
PRIVILEGE_PREFIXES = ("GRANT ", "REVOKE ", "ALTER DEFAULT PRIVILEGES")

_OWNER = re.compile(r"\s+OWNER TO\s+[^;]+;", re.IGNORECASE)
_COMMENT_ON_EXTENSION = re.compile(r"^COMMENT ON EXTENSION\b", re.IGNORECASE)


def statements(sql: str) -> list[str]:
    """`sql` as a list of normalised statements, one per element.

    Splitting on `;` at the end of a line is enough for `pg_dump` output: it never puts two
    statements on one line and its function bodies are dollar-quoted, so a `;` inside one is not
    at the end of a line that also closes a statement. A hand-written file could break this; a
    dump cannot, and a dump is the only thing this script is pointed at.
    """
    cleaned: list[str] = []
    for raw in sql.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("--"):
            continue
        if any(line.startswith(prefix) for prefix in SESSION_PREFIXES):
            continue
        if any(line.startswith(prefix) for prefix in PRIVILEGE_PREFIXES):
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = _OWNER.sub(";", text)

    result: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip()
            current = []
            if not statement or _COMMENT_ON_EXTENSION.match(statement):
                continue
            result.append(statement)
    if current:
        leftover = "\n".join(current).strip()
        if leftover:
            result.append(leftover)
    return result


def _prefix_match(b: list[str], prefix_dir: pathlib.Path) -> str | None:
    """The newest prefix dump in `prefix_dir` whose statements equal `b`, by file name order."""
    best: str | None = None
    for dump in sorted(prefix_dir.glob("*.schema")):
        if set(statements(dump.read_text("utf-8"))) == set(b):
            best = dump.name.removesuffix(".schema")
    return best


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print(
            "Użycie: p0_normalise_schema.py <schemat-a.sql> <schemat-b.sql> <roznica.txt> "
            "[katalog-prefiksow]",
            file=sys.stderr,
        )
        return 2
    a_path, b_path, diff_path = (pathlib.Path(p) for p in argv[1:4])
    prefix_dir = pathlib.Path(argv[4]) if len(argv) == 5 else None
    for path in (a_path, b_path):
        if not path.exists():
            print(f"NIE_URUCHOMIONO: nie ma pliku {path}")
            return 2

    a = statements(a_path.read_text("utf-8"))
    b = statements(b_path.read_text("utf-8"))

    # Liczność przed predykatem: dwa puste zrzuty są IDENTYCZNE i przeszłyby każde porównanie,
    # ogłaszając zgodność schematów, których nikt nie odczytał (docs/KONWENCJE.md).
    if not a or not b:
        print(
            f"NIE_URUCHOMIONO: po normalizacji zostało {len(a)} instrukcji w {a_path.name} "
            f"i {len(b)} w {b_path.name}; porównanie pustki nie jest zgodnością"
        )
        return 2

    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))

    lines = [
        "# Porównanie schematów po normalizacji",
        f"# A: {a_path.name} ({len(a)} instrukcji)",
        f"# B: {b_path.name} ({len(b)} instrukcji)",
        "",
    ]
    if only_a:
        lines += [f"## Tylko w A ({len(only_a)})", ""]
        lines += [f"{s}\n" for s in only_a]
    if only_b:
        lines += [f"## Tylko w B ({len(only_b)})", ""]
        lines += [f"{s}\n" for s in only_b]
    if not only_a and not only_b:
        lines.append("Brak różnic.")
    diff_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if only_a or only_b:
        if prefix_dir is not None and prefix_dir.is_dir():
            matched = _prefix_match(b, prefix_dir)
            if matched is not None:
                applied = sorted(
                    d.name.removesuffix(".schema") for d in prefix_dir.glob("*.schema")
                )
                missing = [name for name in applied if name > matched]
                with diff_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"\n## Wyjaśnienie różnicy\n\nSchemat B odpowiada DOKŁADNIE migracjom "
                        f"do {matched} włącznie. Brakujące migracje: "
                        f"{', '.join(missing) or 'brak'}.\n"
                    )
                print(
                    f"baza B jest za migracjami: odpowiada stanowi po {matched}, niezastosowane "
                    f"pozostają {', '.join(missing) or 'brak'}"
                )
                return 3
        print(
            f"różnica schematów: {len(only_a)} instrukcji tylko w {a_path.name}, "
            f"{len(only_b)} tylko w {b_path.name} (szczegóły w {diff_path.name})"
        )
        return 1
    print(f"schematy identyczne po normalizacji ({len(a)} instrukcji po obu stronach)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
