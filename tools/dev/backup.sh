#!/usr/bin/env bash
# Kopia zapasowa bazy (P7 etap B, ROADMAP-PIVOT: „usługa działa niezależnie od laptopa autora").
#
#   make backup
#   REPO_ROOT=$PWD bash tools/dev/backup.sh
#
# CO JEST W KOPII I CO NIE JEST, ŚWIADOMIE:
#
#   W KOPII: cała baza PostgreSQL. To jest wszystko, co odtwarza stan produktu: projekty,
#   powierzenia, sesje, ślad, wiedza, kolejka, dziennik audytu.
#
#   POZA KOPIĄ, CELOWO: wolumeny `codex-home` i `profiles`. Trzymają POŚWIADCZENIA: token
#   zalogowanego konta Codex i profil przeglądarki z ciasteczkami sesji platformy. Kopia
#   zapasowa jest plikiem, który z definicji ma trafić gdzie indziej niż maszyna źródłowa, a
#   poświadczenie skopiowane „na wszelki wypadek" jest poświadczeniem w drugim miejscu, którego
#   nikt nie pilnuje. Odtworzenie tych dwóch rzeczy polega na PONOWNYM ZALOGOWANIU, i to jest
#   tańsze niż wyciek. Ten skrypt wypisuje je jawnie, żeby ich brak był decyzją widoczną w
#   artefakcie, a nie przeoczeniem odkrytym przy odtwarzaniu.
#
# BAZA JEST TYLKO CZYTANA: jedyne, co tu robimy z kontenerem, to `pg_dump` przez `docker exec`.
#
# REGUŁA O DOWODZIE (D-007): werdykt czyta się z WYNIKU, nie z kodu wyjścia. Pusty zrzut, zrzut
# bez tabel albo suma kontrolna, której nie dało się policzyć, to PORAŻKA, nie „kopia zrobiona".
#
# Artefakt: artifacts/p7/backup/<UTC>/{dump.sql.gz,manifest.json}
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

LIVE_CONTAINER="${WENEXT_LIVE_PG_CONTAINER:-wenext-db-1}"
PG_USER="${POSTGRES_USER:-wenext}"
PG_DB="${POSTGRES_DB:-wenext}"

#: Ile kopii zostaje. Retencja jest tutaj, a nie „kiedyś w cronie": kopia bez retencji zapełnia
#: dysk i kończy się skasowaniem wszystkich naraz, zwykle w złym momencie.
KEEP="${WENEXT_BACKUP_KEEP:-7}"

#: Wolumeny z poświadczeniami. JAWNIE poza kopią; nazwy są wypisywane do manifestu.
EXCLUDED_VOLUMES=("codex-home" "profiles")

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p7/backup/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

echo "== kopia zapasowa: baza $LIVE_CONTAINER (tylko odczyt), artefakt $OUT_DIR =="

fail() {
    python3 - "$OUT_DIR" "$1" <<'PYEOF' > "$OUT_DIR/manifest.json"
import json, sys
print(json.dumps({"werdykt": "NIEUDANA", "powod": sys.argv[2]}, ensure_ascii=False, indent=2))
PYEOF
    echo "NIEUDANA: $1" >&2
    exit 3
}

if ! docker exec "$LIVE_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    fail "kontener $LIVE_CONTAINER nie odpowiada na pg_isready"
fi

DUMP="$OUT_DIR/dump.sql.gz"
if ! docker exec "$LIVE_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges \
        2>"$OUT_DIR/pg_dump.err" | gzip -9 > "$DUMP"; then
    fail "pg_dump nie powiódł się: $(tail -3 "$OUT_DIR/pg_dump.err" | tr '\n' ' ')"
fi

BYTES="$(wc -c < "$DUMP" | tr -d ' ')"
[ "${BYTES:-0}" -gt 1000 ] || fail "zrzut ma $BYTES bajtów, czyli jest pusty"

# Ile tabel naprawdę wjechało do zrzutu. Plik, który się zgzipował, ale nie zawiera ani jednego
# CREATE TABLE, jest plikiem, a nie kopią.
TABLES="$(gzip -dc "$DUMP" | grep -c '^CREATE TABLE' || true)"
[ "${TABLES:-0}" -gt 10 ] || fail "zrzut zawiera $TABLES tabel, spodziewano się kilkunastu"

if command -v shasum >/dev/null 2>&1; then
    SHA="$(shasum -a 256 "$DUMP" | awk '{print $1}')"
else
    SHA="$(sha256sum "$DUMP" | awk '{print $1}')"
fi
[ -n "$SHA" ] || fail "nie udało się policzyć sumy kontrolnej"
echo "$SHA  dump.sql.gz" > "$OUT_DIR/dump.sql.gz.sha256"

ROWS="$(docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d '[:space:]')"

python3 - "$ROOT/tools/dev" "$OUT_DIR" "$STAMP" "$SHA" "$BYTES" "$TABLES" "${ROWS:-0}" \
    "${EXCLUDED_VOLUMES[@]}" <<'PYEOF' > "$OUT_DIR/manifest.json"
import json, pathlib, sys

tools_dir, out_dir, stamp, sha, size, tables, live_tables, *volumes = sys.argv[1:]

# LICZBY WIERSZY Z PLIKU, NIE Z BAZY, i to jest cała różnica: odtworzenie odtwarza ten plik, więc
# to z nim ma się zgadzać. Liczba z żywej bazy zmienia się między zrzutem a porównaniem i zamienia
# ćwiczenie odtworzenia w pomiar tego, czy system akurat pracował (zmierzone 20.09).
sys.path.insert(0, tools_dir)
try:
    from dump_rows import counts_from_dump

    per_table = counts_from_dump(pathlib.Path(out_dir) / "dump.sql.gz")
except Exception as exc:  # manifest bez liczb jest gorszy niż kopia bez manifestu
    per_table = {}
    print(f"UWAGA: nie policzono wierszy z pliku: {exc}", file=sys.stderr)

print(json.dumps({
    "werdykt": "UDANA",
    "wykonano": stamp,
    "plik": "dump.sql.gz",
    "sha256": sha,
    "bajtow": int(size),
    "tabel_w_zrzucie": int(tables),
    "tabel_w_bazie": int(live_tables),
    "wierszy_per_tabela": per_table,
    "wierszy_razem": sum(per_table.values()),
    "poza_kopia": [
        {
            "wolumen": name,
            "powod": (
                "trzyma poświadczenia (token konta silnika albo profil przeglądarki z sesją "
                "platformy). Kopia trafia gdzie indziej niż maszyna źródłowa, więc skopiowane "
                "poświadczenie jest poświadczeniem w drugim miejscu. Odtworzenie polega na "
                "ponownym zalogowaniu."
            ),
        }
        for name in volumes
    ],
}, ensure_ascii=False, indent=2))
PYEOF

# Retencja: zostają najnowsze $KEEP katalogi, reszta znika. Liczone po nazwie, bo nazwa jest
# znacznikiem czasu UTC i sortuje się leksykograficznie.
#
# Bez `mapfile`: macOS ma bash 3.2, w którym go nie ma, a skrypt kopii zapasowej jest ostatnim
# miejscem, w którym chcemy odkryć różnicę między powłokami. Zwykła pętla po `ls | sort` działa
# wszędzie, a nazwy katalogów są znacznikami czasu, więc nie zawierają spacji.
COUNT="$(ls -1d "$ROOT/artifacts/p7/backup"/*/ 2>/dev/null | wc -l | tr -d ' ')"
if [ "${COUNT:-0}" -gt "$KEEP" ]; then
    ls -1d "$ROOT/artifacts/p7/backup"/*/ 2>/dev/null | sort | head -n "$((COUNT - KEEP))"     | while IFS= read -r old; do
        rm -rf "$old"
        echo "   retencja: skasowano $(basename "$old")"
    done
fi

echo "== werdykt kopii: UDANA =="
echo "   plik: $DUMP ($BYTES bajtów, $TABLES tabel)"
echo "   sha256: $SHA"
echo "   poza kopią (poświadczenia): ${EXCLUDED_VOLUMES[*]}"
echo "== manifest: $OUT_DIR/manifest.json =="
