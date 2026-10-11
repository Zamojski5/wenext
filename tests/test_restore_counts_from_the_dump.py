"""Werdykt odtworzenia porównuje z PLIKIEM, nie z żywą bazą (P0, P7).

CO TO ZAMYKA. 20.09, tuż po ósmym wdrożeniu, `p0-restore-check` dał porażkę na liczbie wierszy,
a trzydzieści sekund później zaliczenie. Przyczyną nie było odtworzenie, tylko to, że między
zrzutem a policzeniem wierszy w żywej bazie system zapisał kilka wierszy: naprawę powrotu rozmowy
i wznowioną turę. Kontrola, której wynik zależy od tego, czy akurat coś się działo, nie mierzy
tego, co obiecuje, a pojawia się dokładnie wtedy, gdy system pracuje, czyli wtedy, gdy najbardziej
potrzeba pewności, że kopia jest dobra.

`restore_drill.sh` robił to dobrze od początku; ta sama funkcja jest teraz jedna i wspólna.
"""

from __future__ import annotations

import gzip
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "dev"))

from dump_rows import compare, counts_from_dump, counts_from_text, drift

DUMP = """--
-- PostgreSQL database dump
--
COPY public.organizations (id, name) FROM stdin;
1\tPierwsza
2\tDruga
\\.
COPY public."agent_sessions" (id, goal) FROM stdin;
a\tcel z nową linią\\nw środku
\\.
COPY public.puste (id) FROM stdin;
\\.
"""


def test_rows_are_counted_from_the_copy_blocks() -> None:
    counts = counts_from_text(DUMP)

    assert counts == {"organizations": 2, "agent_sessions": 1, "puste": 0}, counts


def test_a_value_with_a_newline_is_still_one_row() -> None:
    """W formacie COPY nowa linia w wartości jest zapisana jako `\\n`, więc linia to wiersz."""
    assert counts_from_text(DUMP)["agent_sessions"] == 1


def test_a_gzipped_dump_is_read_the_same_way(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "dump.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(DUMP)

    assert counts_from_dump(path) == counts_from_text(DUMP)


def test_a_write_between_the_dump_and_the_comparison_does_not_change_the_verdict() -> None:
    """SEDNO POPRAWKI. Odtworzenie zgadza się ze zrzutem, a żywa baza w międzyczasie urosła."""
    in_dump = counts_from_text(DUMP)
    restored = dict(in_dump)
    live = {**in_dump, "organizations": 5, "jobs": 12}

    verdict = compare(in_dump, restored)
    moved = drift(in_dump, live)

    assert verdict["stan"] == "ZALICZONY", verdict
    assert verdict["roznice"] == []
    assert moved["tabel_z_ruchem"] == 2, moved
    assert "organizations" in moved["zdanie"] and "+15" in moved["zdanie"], moved["zdanie"]


def test_a_restore_that_lost_rows_is_still_a_failure() -> None:
    """Poprawka nie może zamienić ćwiczenia w formalność: brak wiersza w odtworzeniu to porażka."""
    in_dump = counts_from_text(DUMP)
    verdict = compare(in_dump, {**in_dump, "organizations": 1})

    assert verdict["stan"] == "PORAZKA"
    assert verdict["roznice"][0]["tabela"] == "organizations"
    assert verdict["roznice"][0]["w_zrzucie"] == 2
    assert verdict["roznice"][0]["odtworzone"] == 1


def test_a_dump_without_data_is_not_a_pass() -> None:
    """Zrzut samego schematu przechodzi każde `all(...)`, więc ma własny stan (D-020)."""
    verdict = compare({}, {"cokolwiek": 3})

    assert verdict["stan"] == "NIE_URUCHOMIONO"
    assert "ani jednego bloku COPY" in verdict["uwaga"]


def test_the_drift_line_says_nothing_happened_when_nothing_did() -> None:
    in_dump = counts_from_text(DUMP)

    assert drift(in_dump, dict(in_dump))["zdanie"] == "live i zrzut mają tyle samo wierszy"
