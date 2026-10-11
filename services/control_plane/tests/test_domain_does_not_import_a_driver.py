"""The domain layer cannot reach a database driver. Scanned, not asserted from a grep by hand.

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

The contract test next door proves the positive half: the same commands, two transports, one
answer. This module proves the negative half — that there is no second entrance. A command that
could open a connection would keep working after the E3 broker arrives and would keep working
against PostgreSQL, so the contract test would stay green while the seam it measures had already
stopped existing.

THE TRAP THIS MODULE HAD TO AVOID, named in the brief for this package: a scan over a directory
the test itself chose proves only that the chosen directory is clean. Three guards, and all three
are assertions rather than intentions:

1.  The directory is not chosen. It is `pathlib.Path(control_plane.domain.__file__).parent` — the
    package the production code actually imports. Point the import somewhere else and the
    production code goes with it.
2.  `test_the_scan_covers_every_file_of_the_package_that_is_on_disk` compares the scanned file
    names with what `rglob` finds on disk, requires the five modules that carry the layer by
    name, and requires `commands.py` to really define the domain commands. An emptied directory
    fails on the count; a renamed module fails on the names; a hollowed-out `commands.py` fails
    on the last one.
3.  `test_the_same_scanner_finds_the_driver_where_it_really_lives` runs the SAME scanner over
    `control_plane/jobs/store.py`, one directory away, where the driver genuinely is — and
    requires it to be found. A scanner that reports nothing everywhere would fail there.

WHAT THIS DOES NOT MEASURE, stated because a docstring promising more than it measures is worse
than no docstring: the scan is STATIC. A driver reached through a name assembled at runtime
(`importlib.import_module("async" + "pg")`) is invisible to it, and so is a driver reached
through a third module that the domain imports innocently. The first is caught by nothing here;
the second is the reason the port surface is checked as well — `domain/ports.py` returns only
frozen dataclasses, and `contracts/tests/test_job_contracts.py` scans the contracts port for the
same words.
"""

from __future__ import annotations

import ast
import pathlib
import re

import control_plane.domain
import control_plane.jobs.store

#: The package under scan, taken from the imported module rather than from a path typed here.
DOMAIN_PACKAGE = pathlib.Path(control_plane.domain.__file__).resolve().parent

#: The module where the driver legitimately lives. Used as the scanner's positive control, so
#: "found nothing" can never be the answer everywhere.
STORE_MODULE = pathlib.Path(control_plane.jobs.store.__file__).resolve()

#: The modules that carry the domain layer. Listed by name so that emptying the package, or
#: renaming a module out of the scan, is a failure rather than a smaller number.
REQUIRED_DOMAIN_MODULES = frozenset(
    {"__init__.py", "commands.py", "errors.py", "models.py", "ports.py"}
)

#: Import roots no module of the domain layer may have. Wider than this repository needs on
#: purpose: the point is that the layer stays free of ANY driver, not free of the one currently
#: in use, because the E3 swap is the situation being protected against.
DRIVER_MODULES = frozenset(
    {
        "aiomysql",
        "aiopg",
        "aioredis",
        "aiosqlite",
        "asyncmy",
        "asyncpg",
        "cx_Oracle",
        "databases",
        "motor",
        "MySQLdb",
        "oracledb",
        "pg8000",
        "psycopg",
        "psycopg2",
        "pymongo",
        "pymysql",
        "pyodbc",
        "redis",
        "sqlalchemy",
        "sqlite3",
        "sqlmodel",
    }
)

#: Driver names hunted in the RAW TEXT, which catches what the import graph does not: a name
#: inside `__import__(...)`, inside a docstring that spells the mechanism out, inside a string
#: passed to a loader.
#:
#: `postgres` is DELIBERATELY ABSENT from this tuple, and the omission is the honest part.
#: `domain/models.py` and `domain/commands.py` explain in prose that PostgreSQL computes the
#: deadlines, and "PostgreSQL" contains "postgres". Hunting it here would make the test fail on
#: an explanation instead of on a mechanism, and a test that fires on prose gets relaxed. The
#: import scan below carries the weight; this tuple only catches library names, which no comment
#: in that package has any reason to spell.
DRIVER_TOKENS: tuple[str, ...] = (
    "aiopg",
    "aioredis",
    "aiosqlite",
    "asyncmy",
    "asyncpg",
    "oracledb",
    "pg8000",
    "psycopg",
    "pymongo",
    "pymysql",
    "pyodbc",
    "sqlalchemy",
    "sqlite3",
)

#: Identifier fragments that mean "this code is holding a database". Matched on WHOLE parts of a
#: name after splitting on underscores and camel-case humps, never as substrings: `fetch_job` is
#: a port method and has to stay legal, while a `conn` argument must not.
#:
#: `fetch` is deliberately not here for exactly that reason — `fetch_job` and `fetch_jobs` are
#: the two reading methods of the port.
DRIVER_SHAPED_PARTS = frozenset(
    {
        "acquire",
        "conn",
        "connection",
        "cursor",
        "dsn",
        "executemany",
        "fetchall",
        "fetchrow",
        "fetchval",
        "pool",
        "rollback",
        "sql",
    }
)


def name_parts(name: str) -> list[str]:
    """`fetch_job` → ['fetch', 'job']; `ClaimBatch` → ['claim', 'batch']; `DSN` → ['d','s','n'].

    Whole parts, because substring matching turns `fetch_job` into a finding and a test that
    fires on a legal name is a test somebody switches off.
    """
    return [
        part.lower()
        for chunk in re.split(r"[^A-Za-z0-9]+", name)
        if chunk
        for part in re.findall(r"[A-Za-z][a-z0-9]*|[0-9]+", chunk)
    ]


def driver_findings(source: str, *, where: str) -> list[str]:
    """Every reason to believe `source` reaches a database driver. Empty means none were found.

    Three passes, because each one sees what the others cannot:
      * imports      — the only way a driver normally enters a module,
      * identifiers  — a driver handed in as an argument or bound to a name,
      * raw text     — a driver named in a string or a comment.
    """
    findings: list[str] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in DRIVER_MODULES:
                    findings.append(f"{where}: import sterownika {alias.name!r}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in DRIVER_MODULES
        ):
            findings.append(f"{where}: import ze sterownika {node.module!r}")

    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.name
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.alias):
            name = node.asname or node.name
        if name is None:
            continue
        shaped = DRIVER_SHAPED_PARTS.intersection(name_parts(name))
        if shaped:
            findings.append(
                f"{where}: nazwa {name!r} mówi o sterowniku ({', '.join(sorted(shaped))})"
            )

    lowered = source.lower()
    for token in DRIVER_TOKENS:
        if token in lowered:
            findings.append(f"{where}: tekst modułu zawiera {token!r}")

    return findings


def scan_package(package: pathlib.Path) -> dict[str, list[str]]:
    """Findings per file for every `.py` in `package`, `__pycache__` excluded.

    Returns a mapping keyed by file NAME, so the caller can compare the set of scanned files with
    what is on disk — the guard that stops this from being a scan of nothing.
    """
    return {
        module_path.name: driver_findings(
            module_path.read_text(encoding="utf-8"), where=module_path.name
        )
        for module_path in sorted(package.rglob("*.py"))
        if "__pycache__" not in module_path.parts
    }


def test_no_module_in_the_domain_package_reaches_a_database_driver() -> None:
    """The whole point of the package. Count first, predicate second.

    `all([])` is `True` and this repository has been bitten by it four times, so the number of
    scanned files is asserted BEFORE anything is said about their contents. A scan that found no
    files would otherwise report a perfectly clean domain layer.
    """
    scanned = scan_package(DOMAIN_PACKAGE)

    assert len(scanned) >= len(REQUIRED_DOMAIN_MODULES), (
        f"przeskanowano {len(scanned)} plików w {DOMAIN_PACKAGE} — mniej niż "
        f"{len(REQUIRED_DOMAIN_MODULES)} modułów, z których składa się ta warstwa. Pusty skan "
        "ogłasza czystość, której nie zmierzył"
    )

    findings = [finding for file_findings in scanned.values() for finding in file_findings]
    assert not findings, (
        "warstwa domenowa sięga po sterownik bazy danych — a wtedy podmiana transportu w E3 "
        "przestaje być podmianą (L5206, L5216):\n  " + "\n  ".join(findings)
    )


def test_the_scan_covers_every_file_of_the_package_that_is_on_disk() -> None:
    """The scan is over the real package, whole, and that package still does the work.

    Three separate ways of lying are closed here, and each needs its own assertion: scanning
    fewer files than exist, scanning a package that no longer contains the layer, and scanning a
    `commands.py` from which the commands were removed.
    """
    scanned = set(scan_package(DOMAIN_PACKAGE))
    on_disk = {
        module_path.name
        for module_path in DOMAIN_PACKAGE.rglob("*.py")
        if "__pycache__" not in module_path.parts
    }

    assert on_disk, f"w {DOMAIN_PACKAGE} nie ma ani jednego pliku .py — to nie jest ta warstwa"
    assert scanned == on_disk, (
        f"skan objął {sorted(scanned)}, a na dysku leży {sorted(on_disk)}. Skan węższy niż "
        "katalog dowodzi czystości plików, które sam sobie wybrał"
    )
    missing = REQUIRED_DOMAIN_MODULES - on_disk
    assert not missing, (
        f"w warstwie domenowej brakuje modułów {sorted(missing)} — skan czystego katalogu, "
        "z którego usunięto kod, jest zielony i nic nie znaczy"
    )

    # And the layer still holds the commands the DoD line talks about. A `commands.py` reduced to
    # a docstring would satisfy every assertion above.
    commands_tree = ast.parse((DOMAIN_PACKAGE / "commands.py").read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(commands_tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    expected_commands = {
        "enqueue_job",
        "claim_batch",
        "report_result",
        "release_on_waiting",
        "read_job",
        "list_jobs",
    }
    assert expected_commands <= defined, (
        f"commands.py nie definiuje {sorted(expected_commands - defined)} — skanujemy warstwę, "
        "która przestała być warstwą komend"
    )


def test_the_same_scanner_finds_the_driver_where_it_really_lives() -> None:
    """The positive control. A scanner that reports nothing everywhere reports nothing here.

    `jobs/store.py` is one directory away from the domain layer and is SUPPOSED to hold the
    driver — it is the PostgreSQL half of the port. Running the identical function over it must
    produce findings from all three passes, otherwise the green result next door means only that
    the function returns an empty list.

    The synthetic cases below cover the shapes `store.py` happens not to have: a driver imported
    under an alias, and a driver handed in as an argument.
    """
    real_findings = driver_findings(
        STORE_MODULE.read_text(encoding="utf-8"), where=STORE_MODULE.name
    )
    assert len(real_findings) >= 3, (
        f"skaner znalazł {len(real_findings)} śladów sterownika w {STORE_MODULE.name}, "
        "a ten moduł jest zbudowany na sterowniku. Skaner, który tam niczego nie widzi, "
        "nie dowodzi niczego w warstwie domenowej"
    )
    assert any("import" in finding for finding in real_findings), real_findings
    assert any("tekst modułu" in finding for finding in real_findings), real_findings
    assert any("mówi o sterowniku" in finding for finding in real_findings), real_findings

    aliased = driver_findings("import asyncpg as pg\n", where="atrapa")
    assert aliased, "import pod aliasem przeszedł przez skaner"

    handed_in = driver_findings(
        "async def enqueue_job(conn, *, job_id):\n    return job_id\n", where="atrapa"
    )
    assert handed_in, "argument `conn` przeszedł przez skaner"

    # The negative control, so the two above are not simply "everything is a finding": the real
    # `commands.py` produces nothing at all.
    assert not driver_findings(
        (DOMAIN_PACKAGE / "commands.py").read_text(encoding="utf-8"), where="commands.py"
    ), "skaner uznaje czysty moduł domenowy za brudny — wtedy jego cisza nic nie znaczy"
