"""Where the migrations are and in what order they are applied.

`tools/dev/apply_migrations.sh` is the thing that actually runs them; this module is the same
rule written down once more in Python, so the tests can check the rule without shelling out
and so that a later package can ask "which migrations exist" without hard-coding a path.

The rule is short: every `*.sql` file in `db/migrations/`, in ascending order of its numeric
prefix, and the prefix is mandatory.
"""

from __future__ import annotations

import os
import pathlib
import re

#: `0001_f0_kernel.sql` -> 1. The prefix is what orders the files; the rest of the name is for
#: humans. A file without one is a defect, not a file applied last (see `migration_number`).
_NUMBER = re.compile(r"^(\d+)_")

#: Overrides discovery. Set inside images, where the package lives in site-packages and there
#: is no repository tree above it to walk.
DIRECTORY_VARIABLE = "WENEXT_MIGRATIONS_DIR"


def _discover_directory() -> pathlib.Path:
    override = os.environ.get(DIRECTORY_VARIABLE)
    if override:
        return pathlib.Path(override)

    # Walk up from this file looking for `db/migrations`. Counting `parents[n]` with a fixed n
    # works for the editable install in the working tree and breaks silently everywhere else —
    # and "breaks silently" is the one behaviour this repository keeps paying for.
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "db" / "migrations"
        if candidate.is_dir():
            return candidate

    raise RuntimeError(
        "nie znaleziono katalogu db/migrations ani w górę od pakietu db_schema, ani w zmiennej "
        f"{DIRECTORY_VARIABLE}. Migracje bez katalogu to nie „zero migracji”, tylko zła ścieżka"
    )


#: Resolved once, at import. Tests read it to report where they looked.
MIGRATIONS_DIR = _discover_directory()


def migration_number(path: pathlib.Path) -> int | None:
    """The numeric prefix of a migration file, or None when it has none."""
    match = _NUMBER.match(path.name)
    return int(match.group(1)) if match else None


def migration_files() -> list[pathlib.Path]:
    """Every migration, in the order `apply_migrations.sh` applies them: sorted by name.

    Sorted by NAME rather than by number on purpose — that is what the shell glob does, and a
    Python helper that quietly disagreed with the script would be worse than no helper at all.
    `test_migration_numbers_are_unique_and_ordered` is what keeps the two orders identical.

    An empty directory is returned as an empty list; every caller in this repository treats
    that as an error. It is not this function's job to guess which error.
    """
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)
