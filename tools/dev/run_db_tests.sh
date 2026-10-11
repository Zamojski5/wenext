#!/usr/bin/env bash
# Runs one package's test suite against a single-use PostgreSQL container.
#
#   REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh services/db_schema [dodatkowe argumenty pytest]
#
# THE POINT OF THIS SCRIPT IS THE VERDICT AT THE BOTTOM, NOT THE CONTAINER AT THE TOP.
#
# Measured by the red team on 2026-08-31, on this repository: a module carrying
# `pytestmark = [pytest.mark.skipif(not DSN, ...)]` prints "2 tests collected" and then
# "2 skipped", and pytest exits with code 0. A runner that trusts the exit code — or, worse,
# the COLLECTED COUNT — reports a database suite that never opened a connection as a green run.
# That is the sixth false pass of this project waiting to happen, so:
#
#   the script FAILS when passed == 0, and it FAILS when skipped > 0,
#   and it reads both numbers out of the --junitxml report, never out of the exit code.
#
# The collected count is NOT a guard: it counts skipped tests too. That is precisely how the
# hole was hidden.
#
# The PostgreSQL version comes from versions.lock.toml, never typed in here. A test schema
# validated against a different server than the one in `docker-compose.yml` proves nothing
# about the server the product runs on (L5180: exact versions).
#
# Operator-facing output stays Polish on purpose (D-010).
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

PACKAGE_DIR="${1:-}"
if [ -z "$PACKAGE_DIR" ]; then
  echo "Użycie: REPO_ROOT=\$PWD bash tools/dev/run_db_tests.sh <katalog-pakietu> [argumenty pytest]" >&2
  exit 2
fi
shift

PACKAGE_PATH="$ROOT/$PACKAGE_DIR"
PYTHON_BIN="$PACKAGE_PATH/.venv/bin/python"

if [ ! -d "$PACKAGE_PATH" ]; then
  echo "BŁĄD: nie ma katalogu $PACKAGE_PATH — suita NIE została uruchomiona." >&2
  exit 3
fi
if [ ! -x "$PYTHON_BIN" ]; then
  # A missing environment is a FAILURE, never a skip — same rule as in run_all_tests.sh.
  echo "BŁĄD: brak środowiska $PYTHON_BIN — suita NIE została uruchomiona." >&2
  echo "      Zbuduj je (uv venv + uv pip install) zanim uznasz ten pakiet za przetestowany." >&2
  exit 3
fi

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

CONTAINER_NAME="wenext-test-pg-$$"
PORT="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")"
PASSWORD="test-only-$$"
JUNIT_DIR="$(mktemp -d)"
JUNIT="$JUNIT_DIR/junit.xml"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$JUNIT_DIR"
}
trap cleanup EXIT

echo "== $PACKAGE_DIR: start PostgreSQL ($PG_IMAGE) na porcie $PORT =="
# Published on 127.0.0.1 only. This container lives for the duration of one test run and is
# not part of the compose stack, so it is outside the D-014 allowlist by construction — that
# allowlist covers the compose files, which is where a leaked port would actually survive.
docker run -d --name "$CONTAINER_NAME" \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=wenext -e POSTGRES_USER=wenext \
  -p "127.0.0.1:$PORT:5432" \
  "$PG_IMAGE" >/dev/null || { echo "nie udało się wystartować Postgresa" >&2; exit 3; }

echo "== czekam na gotowość =="
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" pg_isready -U wenext -d wenext >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$CONTAINER_NAME" pg_isready -U wenext -d wenext \
  || { echo "Postgres nie wstał" >&2; exit 3; }

REPO_ROOT="$ROOT" WENEXT_PG_CONTAINER="$CONTAINER_NAME" \
  bash "$ROOT/tools/dev/apply_migrations.sh" || exit $?

export WENEXT_TEST_DSN="postgresql://wenext:$PASSWORD@127.0.0.1:$PORT/wenext"

echo "== testy: $PACKAGE_DIR =="
(cd "$PACKAGE_PATH" && PYTHONPATH="." "$PYTHON_BIN" -m pytest tests -rs --junitxml="$JUNIT" "$@")
pytest_status=$?

# ---------------------------------------------------------------------------
# The verdict. Read from the report, not from the exit code.
# ---------------------------------------------------------------------------
if [ ! -f "$JUNIT" ]; then
  echo "BŁĄD: pytest nie zostawił raportu $JUNIT — nie ma z czego odczytać wyniku." >&2
  echo "      Brak dowodu to porażka, nie sukces." >&2
  exit 5
fi

JUNIT="$JUNIT" python3 - <<'PY'
import os, sys, xml.etree.ElementTree as ET

root = ET.parse(os.environ["JUNIT"]).getroot()
suites = root.iter("testsuite") if root.tag == "testsuites" else [root]

total = failures = errors = skipped = 0
for suite in suites:
    total += int(suite.get("tests", 0))
    failures += int(suite.get("failures", 0))
    errors += int(suite.get("errors", 0))
    skipped += int(suite.get("skipped", 0))

passed = total - failures - errors - skipped
print(f"== wynik: PASSED {passed}, FAILED {failures}, ERRORS {errors}, SKIPPED {skipped} "
      f"(zebranych {total}) ==")

problems = []
if skipped:
    problems.append(
        f"{skipped} testów POMINIĘTYCH. Pominięty test bazodanowy przechodzi w podsumowaniu "
        "za zielony — zmierzone: moduł ze `skipif(not DSN)` daje „2 tests collected”, potem "
        "„2 skipped” i kod wyjścia 0. Fixture bazodanowa ma robić pytest.fail, nie skipif."
    )
if passed == 0:
    problems.append(
        "ZERO testów zdanych. Liczba ZEBRANYCH nie jest dowodem — zebrane obejmuje pominięte."
    )
if failures or errors:
    problems.append(f"{failures} porażek i {errors} błędów.")

if problems:
    print("== SUITA NIE ZALICZONA ==")
    for problem in problems:
        print("  - " + problem)
    sys.exit(1)
PY
verdict_status=$?

if [ "$verdict_status" -ne 0 ]; then
  exit "$verdict_status"
fi
if [ "$pytest_status" -ne 0 ]; then
  echo "BŁĄD: pytest zakończył się kodem $pytest_status." >&2
  exit "$pytest_status"
fi
exit 0
