"""Ile wierszy niesie ZRZUT, i czy odtworzenie się z nim zgadza.

PO CO OSOBNY MODUŁ. Ta sama liczba jest potrzebna w dwóch miejscach: w ćwiczeniu odtworzenia
(`restore_drill.sh`) i w teście migracji i odtworzenia (`p0_restore_check.sh`). Drugi z nich
liczył wiersze w ŻYWEJ bazie w chwili porównania i 20.09 dał porażkę, której przyczyną była
praca systemu: między zrzutem a porównaniem doszły wiersze z naprawy powrotów i ze wznowionej
rozmowy. Pierwszy robił to dobrze od początku, bo jego autor zmierzył ten sam problem wcześniej.

LICZBA, Z KTÓRĄ MA SIĘ ZGADZAĆ ODTWORZENIE, POCHODZI Z PLIKU. Odtworzenie odtwarza plik, a nie
bazę sprzed sekundy, więc porównywanie go z bazą mierzy dwie rzeczy naraz i przewraca się na tej,
której nie dotyczy. Różnica między żywą bazą a zrzutem jest osobną, INFORMACYJNĄ liczbą: mówi,
ile system zdążył zapisać, i nie ma prawa decydować o werdykcie.

Format jest własnością `pg_dump` w postaci tekstowej: blok `COPY public.<tabela> (...) FROM
stdin;`, potem jedna linia na wiersz, potem `\\.`. Wartości z nową linią są w tym formacie
zapisane jako `\\n`, więc jedna linia to dokładnie jeden wiersz.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path
from typing import Any

#: Ile różnic wypisujemy w raporcie. Więcej nie pomaga: przy dziesięciu tabelach różniących się
#: liczbą wierszy problem jest wspólny, a nie w jedenastej tabeli.
MAX_LISTED = 50


def counts_from_lines(lines: Any) -> dict[str, int]:
    """Liczba wierszy per tabela z linii zrzutu. Tabela bez danych ma zero, nie brak."""
    counts: dict[str, int] = {}
    table: str | None = None
    for line in lines:
        if table is None:
            if line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                name = line.split()[1]
                table = name.split(".")[-1].strip('"')
                counts.setdefault(table, 0)
            continue
        if line.startswith("\\."):
            table = None
            continue
        counts[table] += 1
    return counts


def counts_from_dump(path: str | Path) -> dict[str, int]:
    """To samo, z pliku: `.gz` albo zwykły tekst, rozpoznawane po nazwie."""
    target = Path(path)
    if target.suffix == ".gz":
        with gzip.open(target, "rt", encoding="utf-8", errors="replace") as handle:
            return counts_from_lines(handle)
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        return counts_from_lines(handle)


def counts_from_text(text: str) -> dict[str, int]:
    return counts_from_lines(io.StringIO(text))


def compare(in_dump: dict[str, int], restored: dict[str, int]) -> dict[str, Any]:
    """Werdykt odtworzenia: plik kontra odtworzona baza. Nic o żywej bazie.

    ZERO PORÓWNANYCH TABEL TO NIE ZALICZENIE (D-020): pusta pętla przechodzi każdy test, który
    ktokolwiek dla niej napisał, więc brak bloków COPY jest osobnym stanem.
    """
    if not in_dump:
        return {
            "stan": "NIE_URUCHOMIONO",
            "uwaga": "w zrzucie nie ma ani jednego bloku COPY, więc nie ma czego porównywać",
            "roznice": [],
            "tabel": 0,
            "wierszy_w_zrzucie": 0,
        }
    differences = [
        {"tabela": name, "w_zrzucie": count, "odtworzone": restored.get(name)}
        for name, count in sorted(in_dump.items())
        if restored.get(name) != count
    ]
    return {
        "stan": "ZALICZONY" if not differences else "PORAZKA",
        "uwaga": (
            f"{len(in_dump)} tabel z danymi, w każdej tyle samo wierszy co w zrzucie"
            if not differences
            else f"{len(differences)} tabel różni się liczbą wierszy wobec zrzutu"
        ),
        "roznice": differences[:MAX_LISTED],
        "tabel": len(in_dump),
        "wierszy_w_zrzucie": sum(in_dump.values()),
    }


def drift(in_dump: dict[str, int], live: dict[str, int]) -> dict[str, Any]:
    """Ile system zapisał MIĘDZY zrzutem a porównaniem. Informacja, nie werdykt.

    Ta liczba jest warta pokazania, bo mówi, że baza pracowała w trakcie ćwiczenia, i tłumaczy
    czytelnikowi, dlaczego „live" i „zrzut" nie muszą być równe. Nie wolno jej podnosić do rangi
    porażki: odtworzenie odtwarza PLIK.
    """
    moved = [
        {"tabela": name, "w_zrzucie": in_dump.get(name, 0), "live_teraz": value}
        for name, value in sorted(live.items())
        if in_dump.get(name, 0) != value
    ]
    total = sum(value - in_dump.get(name, 0) for name, value in live.items())
    return {
        "tabel_z_ruchem": len(moved),
        "wierszy_roznicy": total,
        "tabele": moved[:MAX_LISTED],
        "zdanie": (
            "live i zrzut mają tyle samo wierszy"
            if not moved
            else f"live teraz kontra zrzut: {total:+d} wierszy w {len(moved)} tabelach "
            + ", ".join(one["tabela"] for one in moved[:5])
        ),
    }
