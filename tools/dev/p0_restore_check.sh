#!/usr/bin/env bash
# Test migracji i odtworzenia (P0, docs/ROADMAP-PIVOT.md: "istniejąca historia i przypisania
# przechodzą test migracji oraz odtworzenia").
#
#   make p0-restore-check
#   REPO_ROOT=$PWD bash tools/dev/p0_restore_check.sh
#
# TRZY KROKI, TRZY OSOBNE PYTANIA:
#
#   1. CZY MIGRACJE SKŁADAJĄ SIĘ NA TO, CO STOI NA ŻYWO. Świeży kontener z obrazu przypiętego
#      w versions.lock.toml, wszystkie migracje przez apply_migrations.sh, pg_dump --schema-only.
#      Osobno pg_dump --schema-only bazy live. Po normalizacji różnica ma być pusta. Różnica
#      niepusta nie jest tu awarią skryptu, tylko WYNIKIEM: znaczy, że baza live ma coś, czego
#      nie odtworzy nowe środowisko, albo odwrotnie.
#   2. CZY ZRZUT DA SIĘ ODTWORZYĆ. Pełny pg_dump bazy live wgrany do drugiego świeżego
#      kontenera. Krok osobny od pierwszego, bo schemat zgodny z migracjami a zrzut, który
#      wchodzi bez błędu, to dwa różne fakty.
#   3. CZY ODTWORZONA BAZA MA TE SAME DANE. Liczba wierszy per tabela, live kontra odtworzenie.
#      Bez tego „odtworzenie się powiodło" znaczyłoby tylko „psql nie krzyknął".
#
# BAZA LIVE JEST TYLKO CZYTANA. Jedyne, co ten skrypt robi z kontenerem wenext-db-1, to pg_dump
# i SELECT-y przez `docker exec`. Nie zatrzymuje stacku, nie stawia go, nie pisze do niego.
#
# REGUŁA O DOWODZIE (docs/KONWENCJE.md, D-007): krok, którego nie udało się wykonać, ma stan
# NIE_URUCHOMIONO i NIE jest zaliczeniem. Pusty zrzut, zero tabel albo zero porównanych wierszy
# to porażka, nie „nic do porównania": `all([])` jest prawdą i już dwa razy w tym repozytorium
# ogłosiło sukces nad pustym zbiorem.
#
# Artefakt: artifacts/p0/<UTC>/restore.json i restore.md.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

LIVE_CONTAINER="${WENEXT_LIVE_PG_CONTAINER:-wenext-db-1}"
PG_USER="${POSTGRES_USER:-wenext}"
PG_DB="${POSTGRES_DB:-wenext}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p0/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

HEAD_SHA="$(cd "$ROOT" && git rev-parse HEAD 2>/dev/null || echo "nieznany")"

echo "== test odtworzenia P0: baza live $LIVE_CONTAINER (tylko odczyt), artefakt $OUT_DIR =="

# Stany kroków, uzupełniane w miarę wykonywania. Domyślnie NIE_URUCHOMIONO, żeby przerwanie
# w połowie nie zostawiło po sobie raportu twierdzącego cokolwiek o krokach, które nie ruszyły.
STEP_SCHEMA="NIE_URUCHOMIONO"
STEP_RESTORE="NIE_URUCHOMIONO"
STEP_ROWS="NIE_URUCHOMIONO"
NOTE_SCHEMA=""
NOTE_RESTORE=""
NOTE_ROWS=""

PG_IMAGE="$(ROOT="$ROOT" python3 - <<'PY'
import os, pathlib, tomllib
lock = tomllib.loads((pathlib.Path(os.environ["ROOT"]) / "versions.lock.toml").read_text("utf-8"))
pg = lock["images"]["postgres"]
print(f"{pg['ref']}@{pg['digest']}")
PY
)"
if [ -z "$PG_IMAGE" ]; then
  echo "BŁĄD: nie udało się odczytać obrazu PostgreSQL z versions.lock.toml." >&2
  exit 3
fi

FRESH="wenext-p0-fresh-$$"
RESTORED="wenext-p0-restored-$$"

cleanup() {
  docker rm -f "$FRESH" >/dev/null 2>&1 || true
  docker rm -f "$RESTORED" >/dev/null 2>&1 || true
  docker rm -f "wenext-p0-prefix-$$" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_pg() {
  local name="$1"
  local port
  port="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")"
  docker run -d --name "$name" \
    -e POSTGRES_PASSWORD="p0-only-$$" -e POSTGRES_DB="$PG_DB" -e POSTGRES_USER="$PG_USER" \
    -p "127.0.0.1:$port:5432" "$PG_IMAGE" >/dev/null || return 1
  local i
  for i in $(seq 1 60); do
    if docker exec "$name" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

# -----------------------------------------------------------------------------
# Krok 1: schemat z migracji kontra schemat live
# -----------------------------------------------------------------------------
echo "-- [1] świeży kontener + wszystkie migracje, potem porównanie schematów"

if ! docker inspect "$LIVE_CONTAINER" >/dev/null 2>&1; then
  NOTE_SCHEMA="nie ma kontenera bazy live $LIVE_CONTAINER, więc nie ma z czym porównywać"
  echo "   NIE_URUCHOMIONO: $NOTE_SCHEMA"
else
  if ! start_pg "$FRESH"; then
    NOTE_SCHEMA="świeży PostgreSQL nie wstał"
    echo "   NIE_URUCHOMIONO: $NOTE_SCHEMA"
  elif ! REPO_ROOT="$ROOT" WENEXT_PG_CONTAINER="$FRESH" WENEXT_PG_USER="$PG_USER" \
        WENEXT_PG_DB="$PG_DB" bash "$ROOT/tools/dev/apply_migrations.sh" \
        > "$OUT_DIR/migracje-swieza-baza.log" 2>&1; then
    NOTE_SCHEMA="migracje nie zastosowały się na świeżej bazie (patrz migracje-swieza-baza.log)"
    echo "   NIE_URUCHOMIONO: $NOTE_SCHEMA"
  else
    # Drugi przebieg, tym razem migracja po migracji, ze zrzutem schematu po KAŻDEJ. Nie po to,
    # żeby cokolwiek zaliczyć, tylko żeby umieć odpowiedzieć na pytanie, które pada, gdy różnica
    # jest niepusta: czy baza live dryfuje, czy po prostu jest za repozytorium. Jedna baza, kilka
    # zrzutów, więc to kosztuje sekundy.
    PREFIX_DIR="$OUT_DIR/prefiksy"
    mkdir -p "$PREFIX_DIR"
    PREFIX_PG="wenext-p0-prefix-$$"
    if start_pg "$PREFIX_PG"; then
      for migration in "$ROOT"/db/migrations/*.sql; do
        name="$(basename "$migration")"
        if ! docker exec -i "$PREFIX_PG" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" \
             -q -f - < "$migration" >> "$OUT_DIR/migracje-prefiksy.log" 2>&1; then
          echo "   uwaga: $name nie zastosowała się w przebiegu prefiksowym" \
            >> "$OUT_DIR/migracje-prefiksy.log"
          break
        fi
        docker exec "$PREFIX_PG" pg_dump --schema-only --no-owner --no-privileges \
          -U "$PG_USER" -d "$PG_DB" > "$PREFIX_DIR/$name.schema" 2>/dev/null
      done
      docker rm -f "$PREFIX_PG" >/dev/null 2>&1 || true
    fi
    docker exec "$FRESH" pg_dump --schema-only --no-owner --no-privileges \
      -U "$PG_USER" -d "$PG_DB" > "$OUT_DIR/schemat-z-migracji.sql" 2>"$OUT_DIR/blad-dump-migracje.txt"
    fresh_status=$?
    docker exec "$LIVE_CONTAINER" pg_dump --schema-only --no-owner --no-privileges \
      -U "$PG_USER" -d "$PG_DB" > "$OUT_DIR/schemat-live.sql" 2>"$OUT_DIR/blad-dump-live.txt"
    live_status=$?
    if [ "$fresh_status" -ne 0 ] || [ "$live_status" -ne 0 ]; then
      NOTE_SCHEMA="pg_dump --schema-only nie powiódł się (kody: świeża $fresh_status, live $live_status)"
      echo "   NIE_URUCHOMIONO: $NOTE_SCHEMA"
    elif [ ! -s "$OUT_DIR/schemat-z-migracji.sql" ] || [ ! -s "$OUT_DIR/schemat-live.sql" ]; then
      NOTE_SCHEMA="któryś ze zrzutów schematu jest PUSTY, więc różnica byłaby zgodnością bez treści"
      echo "   PORAZKA: $NOTE_SCHEMA"
      STEP_SCHEMA="PORAZKA"
    else
      python3 "$ROOT/tools/dev/p0_normalise_schema.py" \
        "$OUT_DIR/schemat-z-migracji.sql" "$OUT_DIR/schemat-live.sql" \
        "$OUT_DIR/roznica-schematow.txt" "$OUT_DIR/prefiksy" \
        > "$OUT_DIR/roznica-schematow-podsumowanie.txt" 2>&1
      diff_status=$?
      NOTE_SCHEMA="$(head -c 400 "$OUT_DIR/roznica-schematow-podsumowanie.txt")"
      case "$diff_status" in
        0)
          STEP_SCHEMA="ZALICZONY"
          echo "   ZALICZONY: schematy identyczne po normalizacji"
          ;;
        3)
          # Baza live odpowiada dokładnie pewnemu prefiksowi migracji. To NIE jest dryf i nie
          # jest też zaliczenie: to niewdrożone migracje, czyli stan do decyzji operatora.
          STEP_SCHEMA="LIVE_ZA_MIGRACJAMI"
          echo "   LIVE_ZA_MIGRACJAMI: $NOTE_SCHEMA"
          ;;
        2)
          STEP_SCHEMA="NIE_URUCHOMIONO"
          echo "   NIE_URUCHOMIONO: $NOTE_SCHEMA"
          ;;
        *)
          STEP_SCHEMA="PORAZKA"
          echo "   PORAZKA: $NOTE_SCHEMA"
          ;;
      esac
    fi
  fi
fi

# -----------------------------------------------------------------------------
# Krok 2 i 3: pełny zrzut live, odtworzenie, porównanie liczby wierszy
# -----------------------------------------------------------------------------
echo "-- [2] pełny pg_dump bazy live i odtworzenie w drugim świeżym kontenerze"

if ! docker inspect "$LIVE_CONTAINER" >/dev/null 2>&1; then
  NOTE_RESTORE="nie ma kontenera bazy live $LIVE_CONTAINER"
  NOTE_ROWS="$NOTE_RESTORE"
  echo "   NIE_URUCHOMIONO: $NOTE_RESTORE"
else
  docker exec "$LIVE_CONTAINER" pg_dump --no-owner --no-privileges \
    -U "$PG_USER" -d "$PG_DB" > "$OUT_DIR/zrzut-live.sql" 2>"$OUT_DIR/blad-zrzut-live.txt"
  dump_status=$?
  if [ "$dump_status" -ne 0 ] || [ ! -s "$OUT_DIR/zrzut-live.sql" ]; then
    NOTE_RESTORE="pełny pg_dump bazy live nie powiódł się (kod $dump_status)"
    NOTE_ROWS="$NOTE_RESTORE"
    echo "   NIE_URUCHOMIONO: $NOTE_RESTORE"
  elif ! start_pg "$RESTORED"; then
    NOTE_RESTORE="kontener na odtworzenie nie wstał"
    NOTE_ROWS="$NOTE_RESTORE"
    echo "   NIE_URUCHOMIONO: $NOTE_RESTORE"
  else
    # ON_ERROR_STOP=1, bo bez niego psql wypisuje błędy, jedzie dalej i KOŃCZY SIĘ ZEREM,
    # więc połowa odtworzonej bazy wygląda dokładnie jak cała (tools/dev/apply_migrations.sh).
    docker exec -i "$RESTORED" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -q \
      < "$OUT_DIR/zrzut-live.sql" > "$OUT_DIR/odtworzenie.log" 2>&1
    restore_status=$?
    if [ "$restore_status" -ne 0 ]; then
      STEP_RESTORE="PORAZKA"
      NOTE_RESTORE="psql odrzucił zrzut (kod $restore_status, patrz odtworzenie.log)"
      NOTE_ROWS="odtworzenie nie powiodło się, więc nie ma czego porównywać"
      echo "   PORAZKA: $NOTE_RESTORE"
    else
      STEP_RESTORE="ZALICZONY"
      NOTE_RESTORE="zrzut wszedł bez błędu przy ON_ERROR_STOP=1"
      echo "   ZALICZONY: $NOTE_RESTORE"

      echo "-- [3] liczba wierszy per tabela, ZRZUT kontra odtworzenie (live osobno, na boku)"
      COUNT_SQL="SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
      docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c "$COUNT_SQL" \
        > "$OUT_DIR/tabele.txt" 2>/dev/null
      : > "$OUT_DIR/wiersze-live.tsv"
      : > "$OUT_DIR/wiersze-odtworzone.tsv"
      while IFS= read -r table; do
        [ -n "$table" ] || continue
        live_rows="$(docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At \
          -c "SELECT count(*) FROM public.\"$table\"" 2>/dev/null)"
        restored_rows="$(docker exec "$RESTORED" psql -U "$PG_USER" -d "$PG_DB" -At \
          -c "SELECT count(*) FROM public.\"$table\"" 2>/dev/null)"
        printf '%s\t%s\n' "$table" "${live_rows:-BRAK}" >> "$OUT_DIR/wiersze-live.tsv"
        printf '%s\t%s\n' "$table" "${restored_rows:-BRAK}" >> "$OUT_DIR/wiersze-odtworzone.tsv"
      done < "$OUT_DIR/tabele.txt"
    fi
  fi
fi

# -----------------------------------------------------------------------------
# Raport
# -----------------------------------------------------------------------------
OUT_DIR="$OUT_DIR" STAMP="$STAMP" HEAD_SHA="$HEAD_SHA" LIVE_CONTAINER="$LIVE_CONTAINER" \
TOOLS_DIR="$ROOT/tools/dev" \
STEP_SCHEMA="$STEP_SCHEMA" STEP_RESTORE="$STEP_RESTORE" STEP_ROWS="$STEP_ROWS" \
NOTE_SCHEMA="$NOTE_SCHEMA" NOTE_RESTORE="$NOTE_RESTORE" NOTE_ROWS="$NOTE_ROWS" \
python3 - <<'PY'
import json
import os
import pathlib
import sys

# ŚCIEŻKA Z ZEWNĄTRZ, bo ten blok idzie do `python3 -` przez stdin i NIE MA `__file__`. Pierwsze
# uruchomienie po zmianie wywróciło się dokładnie na tym; skrypt zna swój katalog, interpreter
# czytający ze standardowego wejścia nie zna żadnego.
sys.path.insert(0, os.environ["TOOLS_DIR"])
out = pathlib.Path(os.environ["OUT_DIR"])


def rows(name: str) -> dict[str, int]:
    path = out / name
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text("utf-8").splitlines():
        if "\t" in line:
            table, count = line.split("\t", 1)
            if count.strip().isdigit():
                result[table] = int(count)
    return result


live = rows("wiersze-live.tsv")
restored = rows("wiersze-odtworzone.tsv")

# WERDYKT PORÓWNUJE ODTWORZENIE ZE ZRZUTEM, nie z żywą bazą. Zmierzone 20.09: pierwszy przebieg
# dał porażkę na liczbie wierszy, bo między `pg_dump` a policzeniem doszły wiersze z naprawy
# powrotów i ze wznowionej rozmowy; drugi przebieg trzydzieści sekund później przeszedł. Porażka,
# która zależy od tego, czy system akurat pracował, nie mierzy odtworzenia.
step_rows = os.environ["STEP_ROWS"]
note_rows = os.environ["NOTE_ROWS"]
differences = []
moved = {"zdanie": "nie porównywano", "tabele": [], "wierszy_roznicy": 0, "tabel_z_ruchem": 0}
dump_path = out / "zrzut-live.sql"
if step_rows == "NIE_URUCHOMIONO" and dump_path.exists():
    from dump_rows import compare, counts_from_dump, drift

    in_dump = counts_from_dump(dump_path)
    verdict = compare(in_dump, restored)
    step_rows = verdict["stan"]
    note_rows = verdict["uwaga"]
    differences = verdict["roznice"]
    if live:
        # LINIA INFORMACYJNA, bez wpływu na werdykt: ile system zdążył zapisać w trakcie
        # ćwiczenia. Bez niej czytelnik widzi dwie różne liczby i nie wie, czy to usterka.
        moved = drift(in_dump, live)
    (out / "wiersze-zrzut.tsv").write_text(
        "".join(f"{name}\t{count}\n" for name, count in sorted(in_dump.items())), "utf-8"
    )
elif step_rows == "NIE_URUCHOMIONO" and not note_rows:
    note_rows = "nie zebrano ani jednej tabeli do porównania"

steps = [
    {
        "krok": "schemat_z_migracji_vs_live",
        "stan": os.environ["STEP_SCHEMA"],
        "uwaga": os.environ["NOTE_SCHEMA"],
    },
    {
        "krok": "odtworzenie_zrzutu",
        "stan": os.environ["STEP_RESTORE"],
        "uwaga": os.environ["NOTE_RESTORE"],
    },
    {"krok": "liczba_wierszy_per_tabela", "stan": step_rows, "uwaga": note_rows},
]

not_run = [s["krok"] for s in steps if s["stan"] == "NIE_URUCHOMIONO"]
# Trzy werdykty, nie dwa. "Baza live jest za migracjami" nie jest ani zaliczeniem, ani usterką:
# migracje 0012 i dalsze są w repozytorium i czekają na wdrożenie, a wdrożenie przerywa pilota,
# więc to decyzja człowieka. Wrzucenie tego do PORAZKI kazałoby szukać awarii, której nie ma;
# wrzucenie do ZALICZONEGO byłoby zaliczeniem stanu, którego nikt nie osiągnął.
if not_run:
    verdict = "NIE_URUCHOMIONO"
elif any(s["stan"] == "PORAZKA" for s in steps):
    verdict = "PORAZKA"
elif any(s["stan"] == "LIVE_ZA_MIGRACJAMI" for s in steps):
    verdict = "LIVE_ZA_MIGRACJAMI"
elif all(s["stan"] == "ZALICZONY" for s in steps) and len(steps) == 3:
    verdict = "ZALICZONY"
else:
    verdict = "NIE_URUCHOMIONO"

report = {
    "kamien": "P0",
    "kontrola": "migracje i odtworzenie",
    "wykonano_utc": os.environ["STAMP"],
    "head": os.environ["HEAD_SHA"],
    "baza_live": os.environ["LIVE_CONTAINER"],
    "werdykt": verdict,
    "kroki": steps,
    "nie_uruchomiono": not_run,
    "tabel_porownanych": len(rows("wiersze-zrzut.tsv")) or len(live),
    "roznice_w_liczbie_wierszy": differences,
    # INFORMACYJNIE, bez wpływu na werdykt: ile system zapisał między zrzutem a porównaniem.
    "live_kontra_zrzut": moved,
}
(out / "restore.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

lines = [
    "# Test migracji i odtworzenia (P0)",
    "",
    f"Wykonano: {os.environ['STAMP']} (UTC)",
    f"HEAD: `{os.environ['HEAD_SHA']}`",
    f"Baza live: `{os.environ['LIVE_CONTAINER']}`, wyłącznie odczyt (pg_dump i SELECT).",
    f"Werdykt: **{verdict}**",
    "",
    "| Krok | Stan | Uwaga |",
    "| --- | --- | --- |",
]
for step in steps:
    note = (step["uwaga"] or "").replace("|", "/").replace("\n", " ")
    lines.append(f"| {step['krok']} | {step['stan']} | {note} |")

lines += [
    "",
    f"Tabel porównanych: {len(rows('wiersze-zrzut.tsv')) or len(live)}.",
    f"Live teraz kontra zrzut (informacyjnie): {moved['zdanie']}.",
    "",
]
if differences:
    lines += [
        "## Tabele o różnej liczbie wierszy wobec ZRZUTU",
        "",
        "| Tabela | W zrzucie | Odtworzona |",
        "| --- | ---: | ---: |",
    ]
    lines += [
        f"| {d['tabela']} | {d['w_zrzucie']} | {d['odtworzone']} |" for d in differences
    ]
    lines.append("")
if moved["tabele"]:
    lines += [
        "## Ruch w bazie live w trakcie ćwiczenia (nie wpływa na werdykt)",
        "",
        "| Tabela | W zrzucie | Live teraz |",
        "| --- | ---: | ---: |",
    ]
    lines += [
        f"| {d['tabela']} | {d['w_zrzucie']} | {d['live_teraz']} |" for d in moved["tabele"]
    ]
    lines.append("")

lines += [
    "## Czego ta kontrola NIE dowodzi",
    "",
    "- Nie jest próbą odtworzenia po awarii. Zrzut robiony jest z DZIAŁAJĄCEJ bazy, a nie",
    "  z wolumenu po nagłym zatrzymaniu, i nie mierzy czasu powrotu usługi.",
    "- Nie sprawdza wolumenów poza bazą: profile Chromium i CODEX_HOME są osobnym zasobem.",
    "- Porównuje liczbę wierszy, nie ich treść.",
    "- Porównuje odtworzenie ZE ZRZUTEM, bo odtworzenie odtwarza plik. Różnica między żywą bazą",
    "  a zrzutem jest osobną liczbą i mówi tylko tyle, że system pracował w trakcie ćwiczenia.",
    "",
    "Pliki obok: `schemat-z-migracji.sql`, `schemat-live.sql`, `roznica-schematow.txt`,",
    "`zrzut-live.sql`, `odtworzenie.log`, `wiersze-live.tsv`, `wiersze-odtworzone.tsv`.",
    "",
]
(out / "restore.md").write_text("\n".join(lines), encoding="utf-8")

print()
print(f"== werdykt testu odtworzenia: {verdict} ==")
for step in steps:
    print(f"  - {step['krok']}: {step['stan']} ({step['uwaga'] or 'bez uwag'})")
print(f"== raport: {out}/restore.md ==")
# Kod wyjścia zero TYLKO przy pełnej zgodności. LIVE_ZA_MIGRACJAMI kończy się
# jedynką, bo to stan do zrobienia, a nie stan zrobiony.
raise SystemExit(0 if verdict == "ZALICZONY" else 1)
PY
exit $?
