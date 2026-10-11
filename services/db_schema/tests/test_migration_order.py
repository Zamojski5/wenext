"""The order in which migrations are applied is the order of their names — and it is total.

`tools/dev/apply_migrations.sh` applies `db/migrations/*.sql` sorted by name. That is a fine
rule right up to the day two files share a numeric prefix, or somebody writes `10_` next to
`0002_`, at which point "sorted by name" quietly stops meaning "in the intended order" and the
database ends up in a state nobody designed. The check is here, on the host, because it needs
no database and therefore has no excuse to be skipped.
"""

from __future__ import annotations

from db_schema.migrations import MIGRATIONS_DIR, migration_files, migration_number


def test_migration_files_are_discovered() -> None:
    """An empty directory is an ERROR, not "nothing to apply".

    A migration runner that reports success over zero files is `all([]) == True` wearing a
    shell script (D-011): the database is then empty, every later test that reads a table
    fails with something confusing, and the runner that caused it exited zero.
    """
    files = migration_files()
    assert files, f"brak plików migracji w {MIGRATIONS_DIR} — to błąd, nie stan „nic do zrobienia”"


def test_migration_numbers_are_unique_and_ordered() -> None:
    numbers = [migration_number(path) for path in migration_files()]

    # None would make `sorted()` below raise TypeError instead of reporting the real problem.
    assert None not in numbers, [path.name for path in migration_files()]
    assert len(set(numbers)) == len(numbers), (
        f"dwie migracje mają ten sam numer: {numbers}. Kolejność „po nazwie” przestaje wtedy "
        "być kolejnością zamierzoną, a wynik zależy od sortowania łańcuchów"
    )
    assert numbers == sorted(numbers), (
        f"kolejność po nazwie {numbers} nie jest kolejnością po numerze — plik `10_` sortuje "
        "się przed `0002_`, a apply_migrations.sh stosuje pliki dokładnie po nazwie"
    )


def test_every_migration_file_is_numbered() -> None:
    """A file `db/migrations/fix.sql` would be applied, in an unpredictable position."""
    unnumbered = [
        path.name for path in MIGRATIONS_DIR.glob("*.sql") if migration_number(path) is None
    ]
    assert not unnumbered, (
        f"pliki migracji bez numeru w nazwie: {unnumbered}. apply_migrations.sh zastosuje je "
        "mimo to, w miejscu wynikającym z sortowania alfabetycznego"
    )
