#!/usr/bin/env bash
# Ćwiczenie odtworzenia kopii (P7 etap B).
#
#   make restore-drill
#   REPO_ROOT=$PWD bash tools/dev/restore_drill.sh [katalog-kopii]
#
# PO CO ĆWICZENIE, SKORO KOPIA SIĘ ZROBIŁA. Bo „plik istnieje" i „da się z niego wrócić" to dwa
# różne fakty, a odkrywanie różnicy w dniu awarii jest najdroższym możliwym momentem. Ten skrypt
# bierze NAJNOWSZĄ kopię (albo wskazaną), wgrywa ją do ŚWIEŻEGO kontenera i porównuje liczbę
# wierszy per tabela z bazą źródłową. Wzór: tools/dev/p0_restore_check.sh.
#
# TRZY KROKI, TRZY OSOBNE PYTANIA:
#   1. czy suma kontrolna się zgadza (plik nie zgnił po drodze);
#   2. czy zrzut w ogóle wchodzi do pustej bazy (psql bez błędu);
#   3. czy odtworzona baza ma TE SAME DANE CO PLIK (liczba wierszy per tabela, zrzut kontra
#      odtworzenie).
#
# Krok 3 jest tym, którego zwykle brakuje. Bez niego „odtworzenie się powiodło" znaczy tylko
# „psql nie krzyknął", a to jest prawda także o zrzucie samego schematu, bez ani jednego wiersza.
#
# PORÓWNUJEMY Z PLIKIEM, A NIE Z ŻYWĄ BAZĄ, i to jest poprawka zrobiona po pierwszym przebiegu
# tego skryptu: baza produkcyjna PRACUJE, więc między `pg_dump` a policzeniem w niej wierszy
# przybywa jobów i cykli pilota. Pierwsze uruchomienie pokazało cztery „różnice" po dwa wiersze,
# wszystkie w tabelach kolejki, i żadna z nich nie była wadą kopii: mierzyły upływ czasu.
# Pytanie, na które ćwiczenie ma odpowiedzieć, brzmi „czy z TEGO pliku wraca wszystko, co w nim
# jest", a spójność samego pliku gwarantuje migawka `pg_dump`. Skutek uboczny jest korzystny:
# ćwiczenie nie potrzebuje już działającej bazy źródłowej, więc da się je zrobić na kopii
# przeniesionej na inną maszynę, czyli dokładnie tam, gdzie się przydaje.
#
# REGUŁA O DOWODZIE (D-007): brak kopii, brak Dockera albo zerwane porównanie to NIE_URUCHOMIONO
# i kod 3, nie „ćwiczenie zdane". Zero porównanych tabel jest porażką, nie „nic do porównania":
# `all([])` już dwa razy w tym repozytorium ogłosiło sukces nad pustym zbiorem.
#
# Artefakt: artifacts/p7/restore/<UTC>/drill.json
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

LIVE_CONTAINER="${WENEXT_LIVE_PG_CONTAINER:-wenext-db-1}"
PG_USER="${POSTGRES_USER:-wenext}"
PG_DB="${POSTGRES_DB:-wenext}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p7/restore/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

BACKUP_DIR="${1:-}"
if [ -z "$BACKUP_DIR" ]; then
    BACKUP_DIR="$(ls -1d "$ROOT/artifacts/p7/backup"/*/ 2>/dev/null | sort | tail -1)"
fi

verdict() {
    local status="$1" reason="$2"
    python3 - "$status" "$reason" "$OUT_DIR" <<'PYEOF' > "$OUT_DIR/drill.json"
import json, sys
print(json.dumps({"werdykt": sys.argv[1], "powod": sys.argv[2]}, ensure_ascii=False, indent=2))
PYEOF
    echo "$status: $reason" >&2
}

if [ -z "$BACKUP_DIR" ] || [ ! -f "$BACKUP_DIR/dump.sql.gz" ]; then
    verdict "NIE_URUCHOMIONO" "nie ma żadnej kopii do odtworzenia; uruchom najpierw make backup"
    exit 3
fi

echo "== ćwiczenie odtworzenia: kopia $(basename "$BACKUP_DIR"), artefakt $OUT_DIR =="

# --- krok 1: suma kontrolna ---------------------------------------------------------------
if [ -f "$BACKUP_DIR/dump.sql.gz.sha256" ]; then
    EXPECTED="$(awk '{print $1}' < "$BACKUP_DIR/dump.sql.gz.sha256")"
    if command -v shasum >/dev/null 2>&1; then
        ACTUAL="$(shasum -a 256 "$BACKUP_DIR/dump.sql.gz" | awk '{print $1}')"
    else
        ACTUAL="$(sha256sum "$BACKUP_DIR/dump.sql.gz" | awk '{print $1}')"
    fi
    if [ "$EXPECTED" != "$ACTUAL" ]; then
        verdict "NIEZDANE" "suma kontrolna kopii się nie zgadza: $ACTUAL zamiast $EXPECTED"
        exit 1
    fi
    echo "   [OK] suma kontrolna zgadza się z zapisaną przy kopii"
else
    echo "   [UWAGA] kopia nie ma zapisanej sumy kontrolnej"
fi

# --- krok 2: czy zrzut wchodzi do pustej bazy ----------------------------------------------
PG_IMAGE="$(ROOT="$ROOT" python3 - <<'PYEOF'
import os, pathlib, tomllib
lock = tomllib.loads((pathlib.Path(os.environ["ROOT"]) / "versions.lock.toml").read_text("utf-8"))
pg = lock["images"]["postgres"]
print(f"{pg['ref']}@{pg['digest']}")
PYEOF
)"
[ -n "$PG_IMAGE" ] || { verdict "NIE_URUCHOMIONO" "nie znam obrazu PostgreSQL z versions.lock.toml"; exit 3; }

TARGET="wenext-restore-drill-$$"
cleanup() { docker rm -f "$TARGET" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$TARGET" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=wenext \
    -e POSTGRES_USER=wenext "$PG_IMAGE" >/dev/null 2>&1 \
    || { verdict "NIE_URUCHOMIONO" "nie udało się wystartować kontenera do odtworzenia"; exit 3; }

for _ in $(seq 1 60); do
    docker exec "$TARGET" pg_isready -U wenext -d wenext >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$TARGET" pg_isready -U wenext -d wenext >/dev/null 2>&1 \
    || { verdict "NIE_URUCHOMIONO" "kontener do odtworzenia nie wstał"; exit 3; }

if ! gzip -dc "$BACKUP_DIR/dump.sql.gz" \
        | docker exec -i "$TARGET" psql -U wenext -d wenext -v ON_ERROR_STOP=1 \
        > "$OUT_DIR/restore.log" 2>&1; then
    verdict "NIEZDANE" "zrzut nie wszedł: $(tail -3 "$OUT_DIR/restore.log" | tr '\n' ' ')"
    exit 1
fi
echo "   [OK] zrzut wszedł do świeżej bazy bez błędu"

# --- krok 3: czy dane są te same co w pliku --------------------------------------------------
docker exec "$TARGET" psql -U wenext -d wenext -tAF'|' -c \
    "SELECT relname, (xpath('/row/c/text()', query_to_xml(
        format('SELECT count(*) AS c FROM %I.%I', schemaname, relname),
        false, true, '')))[1]::text::bigint
     FROM pg_stat_user_tables ORDER BY relname" > "$OUT_DIR/restored.txt" 2>/dev/null

python3 - "$ROOT/tools/dev" "$BACKUP_DIR/dump.sql.gz" "$OUT_DIR/restored.txt" "$OUT_DIR" \
    "$(basename "$BACKUP_DIR")" <<'PYEOF'
import json
import pathlib
import sys

tools_dir, dump_path, restored_file, out_dir, backup = sys.argv[1:6]

# JEDNA DEFINICJA LICZENIA WIERSZY ZE ZRZUTU, wspólna z `p0_restore_check.sh`. Wcześniej ta
# funkcja żyła tu, a tamten skrypt liczył wiersze w ŻYWEJ bazie i przewracał się, gdy system
# pracował w trakcie porównania (zmierzone 20.09). Dwie kopie tej samej reguły to dwie odpowiedzi
# na pytanie „czy kopia jest dobra", a jedna z nich zawsze jest starsza.
sys.path.insert(0, tools_dir)
from dump_rows import counts_from_dump as rows_in_dump  # noqa: E402


def read_counts(path):
    rows = {}
    for line in pathlib.Path(path).read_text("utf-8").splitlines():
        if "|" not in line:
            continue
        name, _, value = line.partition("|")
        rows[name.strip()] = int(value.strip() or 0)
    return rows


in_dump = rows_in_dump(dump_path)
restored = read_counts(restored_file)

# ZERO PORÓWNANYCH TABEL TO PORAŻKA, nie „nic do porównania": pusta pętla przechodzi każdy test,
# który ktokolwiek dla niej napisał (D-020).
if not in_dump:
    verdict = "NIE_URUCHOMIONO"
    reason = "w zrzucie nie ma ani jednego bloku COPY, więc nie ma czego porównywać"
    differences = []
else:
    differences = [
        {"tabela": name, "w_zrzucie": count, "odtworzone": restored.get(name)}
        for name, count in sorted(in_dump.items())
        if restored.get(name) != count
    ]
    verdict = "ZDANE" if not differences else "NIEZDANE"
    reason = (
        f"porównano {len(in_dump)} tabel z danymi, wszystkie zgodne"
        if not differences
        else f"{len(differences)} tabel różni się liczbą wierszy"
    )

pathlib.Path(out_dir, "drill.json").write_text(
    json.dumps(
        {
            "werdykt": verdict,
            "powod": reason,
            "kopia": backup,
            "tabel_porownanych": len(in_dump),
            "wierszy_w_zrzucie": sum(in_dump.values()),
            "roznice": differences[:50],
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"   [{'OK' if verdict == 'ZDANE' else 'BŁĄD'}] {reason}")
for row in differences[:10]:
    print(f"      {row['tabela']}: w zrzucie {row['w_zrzucie']}, odtworzone {row['odtworzone']}")
print(f"\n== werdykt ćwiczenia: {verdict} ==")
sys.exit(0 if verdict == "ZDANE" else 1)
PYEOF
STATUS=$?
echo "== artefakt: $OUT_DIR/drill.json =="
exit $STATUS
