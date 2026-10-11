#!/usr/bin/env bash
# Applies EVERY file from db/migrations/ to a PostgreSQL container, in name order.
#
# Why this script exists at all: until 2026-08-31 `tools/dev/run_lease_tests.sh` applied
# `0001_f0_kernel.sql` BY NAME, hard-coded. Phase E1.F1 adds migrations 0002 and 0003, and a
# runner that keeps applying only the first one does not fail — it runs the tests against an
# older schema and reports whatever that schema happens to do. Applying migrations is the kind
# of step that must never be "almost right".
#
# Rules, each one from a mistake this repository has already paid for:
#   - ON_ERROR_STOP=1. Without it psql prints the error, keeps going and EXITS ZERO, so half
#     an applied migration looks exactly like a whole one.
#   - one psql invocation per file, exit code checked per file. Concatenating the files into
#     one stream would give a single exit code for the whole batch and no way to say which
#     file broke.
#   - an EMPTY migrations directory is an ERROR, not "nothing to do". A runner reporting
#     success over zero files is `all([]) == True` written in shell (D-011): the database ends
#     up empty, every later test fails for a confusing reason, and the thing that caused it
#     exited zero.
#
# Usage:
#   REPO_ROOT=$PWD WENEXT_PG_CONTAINER=<nazwa> bash tools/dev/apply_migrations.sh
#   REPO_ROOT=$PWD bash tools/dev/apply_migrations.sh <nazwa-kontenera> [użytkownik] [baza]
#
# Communication with the operator stays Polish on purpose (D-010): the founder reads this
# output, and `criteria.yaml` plus the Go/No-Go report speak Polish too.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

CONTAINER="${1:-${WENEXT_PG_CONTAINER:-}}"
PG_USER="${2:-${WENEXT_PG_USER:-wenext}}"
PG_DB="${3:-${WENEXT_PG_DB:-wenext}}"
MIGRATIONS_DIR="$ROOT/db/migrations"

if [ -z "$CONTAINER" ]; then
  echo "BŁĄD: nie podano kontenera PostgreSQL." >&2
  echo "  Ustaw WENEXT_PG_CONTAINER albo podaj nazwę pierwszym argumentem." >&2
  echo "  Ten skrypt świadomie NIE zgaduje kontenera: zgadnięcie i zastosowanie migracji" >&2
  echo "  do cudzej bazy jest operacją jednokierunkową." >&2
  exit 2
fi

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "BŁĄD: nie ma katalogu $MIGRATIONS_DIR — to zła ścieżka, nie „zero migracji”." >&2
  exit 3
fi

# `mapfile` is bash 4; macOS ships bash 3.2. Process substitution works in both.
# LC_ALL=C so the order does not depend on the developer's locale — the whole point of
# ordering by name is that everybody gets the same order.
files=()
while IFS= read -r path; do
  files+=("$path")
done < <(LC_ALL=C find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' | LC_ALL=C sort)

if [ "${#files[@]}" -eq 0 ]; then
  echo "BŁĄD: w $MIGRATIONS_DIR nie ma ANI JEDNEGO pliku *.sql." >&2
  echo "  To jest porażka, nie stan „nic do zastosowania”: skrypt kończący się zerem nad" >&2
  echo "  pustym zbiorem plików zostawia pustą bazę i wygląda jak sukces." >&2
  exit 4
fi

echo "== migracje: ${#files[@]} plików z $MIGRATIONS_DIR -> kontener $CONTAINER (baza $PG_DB) =="

applied=()
for path in "${files[@]}"; do
  name="$(basename "$path")"
  # Command substitution, NOT a pipe: a pipe would hand back the exit code of the last
  # command in it and psql's own status would vanish (docs/KONWENCJE.md).
  output="$(docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" \
            -q -f - < "$path" 2>&1)"
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "   PADŁA: $name (kod wyjścia psql: $status)" >&2
    echo "$output" >&2
    echo "== zastosowano przed awarią: ${applied[*]:-nic} ==" >&2
    exit "$status"
  fi
  applied+=("$name")
  echo "   OK: $name"
done

echo "== zastosowane migracje (${#applied[@]}) =="
for name in "${applied[@]}"; do
  echo "   - $name"
done
