#!/usr/bin/env bash
# The end-to-end run: PostgreSQL → control plane → Next.js → HTML, all real, all in one command.
#
#   REPO_ROOT=$PWD bash apps/dashboard/tools/e2e_db_render.sh
#
# WHAT IT PROVES that no other command in this package can. Every other test renders a component
# over a fixture, and a fixture cannot prove a read. This script inserts rows with raw SQL, starts
# the control plane against that database, builds and starts the dashboard, and then
# `e2e/dashboard_reads_postgres.e2e.test.ts` looks for the inserted identifiers in the served HTML.
#
# THE NETWORK SHAPE, MEASURED ON THIS HOST 2026-08-31 rather than assumed:
#
#   - PostgreSQL runs in a container published on 127.0.0.1 only, like `tools/dev/run_db_tests.sh`;
#   - the control plane runs on the HOST from `services/control_plane/.venv`, bound to 127.0.0.1;
#   - the Next.js build and server run in the PINNED Node image (apps/dashboard/NOTES.md: the host
#     has Node 24.12.0 and the lock pins 24.20.0, and `node_modules` here is built for linux), and
#     reach the control plane through `host.docker.internal`.
#
# That last hop is the one that needed measuring: a container CAN reach a host process bound to
# 127.0.0.1 through `--add-host=host.docker.internal:host-gateway` on Docker Desktop. Verified with
# `python3 -m http.server 18913 --bind 127.0.0.1` and `wget -O- http://host.docker.internal:18913/`
# from an alpine container — 200 with the directory listing. So the control plane does NOT have to be
# exposed on 0.0.0.0 for this run, and nothing here leaves the loopback interface (D-014).
#
# WHY NOT `make up`. Because other agents work in this repository at the same time, and because a
# compose stack would rebuild images this package does not own. This script touches only a
# throwaway container of its own and two processes it starts itself.
#
# ZASIEW BEZ SUITY: `WENEXT_E2E_SEED_ONLY=1`.
#
# Po co osobny przełącznik, skoro suita i tak zostawia bazę. Bo suita ZMIENIA to, co zasiew
# ustawił, a część stanów produktu istnieje tylko PRZED nią: nie ma jeszcze połączenia Codex
# (tworzy je test zapisów), społeczność nie jest obserwowana (klika to test kart), szkic nie jest
# poprawiony, a sesja RUNNING jeszcze trwa, bo nie przejechał po niej test strumienia. Zrzuty do
# porównania z makietą potrzebują OBU momentów, a udawanie ich przez ręczne SQL-e w bazie byłoby
# trzecim stanem, którego nie zasiał nikt. Skrypt buduje i podnosi dashboard tak samo; pomija
# wyłącznie krok `vitest`, i mówi to na wyjściu.
#
# FAST LOOP FOR MUTATION TESTING. `WENEXT_E2E_KEEP=1` leaves PostgreSQL and the control plane
# running and prints the exports needed to re-run only the render step; `WENEXT_E2E_ATTACH=1` skips
# provisioning and uses the variables already exported. Breaking a mechanism and watching the test
# fail then costs one `next build`, not a whole stack.
#
# Operator-facing output stays Polish on purpose (D-010).
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
APP_DIR="$ROOT/apps/dashboard"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

DASHBOARD_PORT="${WENEXT_E2E_DASHBOARD_PORT:-3100}"
# Lista zmiennych, które wjeżdżają do przypiętego obrazu. Suita e2e biegnie TAM, nie na hoście,
# więc nazwa pominięta tutaj znaczy dla testu „brak środowiska", choć na hoście wszystko jest.
# Zawiera KOMPLET tego, co wypisuje zasiew, także wartości, których dziś nikt nie czyta
# (`WENEXT_E2E_ORGANIZATION_ID`, `WENEXT_E2E_OPERATOR_ID`): sprawdzenie zaraz po zasiewie wymaga
# kompletu, bo „ta akurat nie jest potrzebna" jest zdaniem, które starzeje się po cichu.
PASSTHROUGH="WENEXT_CONTROL_PLANE_URL WENEXT_E2E_BASE_URL WENEXT_E2E_ORGANIZATION_ID WENEXT_E2E_OPERATOR_ID WENEXT_E2E_OPERATOR_PASSWORD WENEXT_E2E_JOB_ID WENEXT_E2E_FRESH_JOB_ID WENEXT_E2E_LEASE_OWNER WENEXT_E2E_AUDIT_KIND WENEXT_E2E_COMMUNITY_ID WENEXT_E2E_JUDGMENT_ID WENEXT_E2E_THREAD_TITLE WENEXT_E2E_WORKSPACE_ID WENEXT_E2E_CODEX_ROOT WENEXT_E2E_KNOWLEDGE_PAGE_URL WENEXT_E2E_KNOWLEDGE_FEED_URL WENEXT_E2E_LIVE_PROJECT_ID WENEXT_E2E_LIVE_SESSION_ID WENEXT_E2E_STREAM_SECONDS WENEXT_E2E_DOCUMENT_ID WENEXT_E2E_DOCUMENT_SENTENCE WENEXT_E2E_DOCUMENT_REMOVED_SENTENCE WENEXT_E2E_DONE_SESSION_ID WENEXT_E2E_DONE_SESSION_TITLE WENEXT_E2E_DONE_SESSION_RESULT WENEXT_E2E_CARDS_SESSION_ID WENEXT_E2E_CARDS_LOGIN WENEXT_E2E_CARDS_PAUSED_LOGIN WENEXT_E2E_CARDS_COMMUNITY WENEXT_E2E_CARDS_WHY WENEXT_E2E_CARDS_ERROR WENEXT_E2E_DRAFT_REVISION WENEXT_E2E_DRAFT_TEXT"

fail() {
  echo "BŁĄD: $*" >&2
  exit 3
}

# ---------------------------------------------------------------------------
# The render step. Everything JS happens here, inside the pinned image.
# ---------------------------------------------------------------------------
render_and_test() {
  WENEXT_NODE_HOST_GATEWAY=1 \
  WENEXT_NODE_ENV_PASSTHROUGH="$PASSTHROUGH" \
  bash "$APP_DIR/tools/node.sh" sh -c "
set -e
echo '== build dashboardu =='
npm run build

echo '== start dashboardu na porcie $DASHBOARD_PORT =='
npx next start -p $DASHBOARD_PORT >/tmp/next-e2e.log 2>&1 &
NEXT_PID=\$!
trap 'kill \$NEXT_PID 2>/dev/null || true' EXIT

for _ in \$(seq 1 60); do
  if node -e \"fetch('http://127.0.0.1:$DASHBOARD_PORT/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\" 2>/dev/null; then
    break
  fi
  sleep 1
done
if ! node -e \"fetch('http://127.0.0.1:$DASHBOARD_PORT/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\"; then
  echo 'dashboard nie wstał — log:' >&2
  cat /tmp/next-e2e.log >&2
  exit 4
fi

if [ "${WENEXT_E2E_SEED_ONLY:-0}" = "1" ]; then
  echo '== WENEXT_E2E_SEED_ONLY=1: suita e2e POMINIĘTA, zasiew zostaje nietknięty =='
  exit 0
fi

echo '== suita e2e =='
npx vitest run --config vitest.e2e.config.mts \
  --reporter=default --reporter=json --outputFile=/tmp/vitest-e2e.json
node tools/verify_run.mjs /tmp/vitest-e2e.json e2e
"
}

if [ "${WENEXT_E2E_ATTACH:-0}" = "1" ]; then
  # Reusing a stack somebody kept. Every variable must already be present — a missing one here
  # would let the suite start against a half-configured environment.
  for name in $PASSTHROUGH; do
    if [ -z "${!name-}" ]; then
      fail "WENEXT_E2E_ATTACH=1, ale brakuje $name — dołączanie do stacku wymaga pełnego zestawu zmiennych"
    fi
  done
  echo "== dołączam do istniejącego stacku: $WENEXT_CONTROL_PLANE_URL =="
  render_and_test
  exit $?
fi

# ---------------------------------------------------------------------------
# 1. PostgreSQL, version from the lock — never typed in here.
# ---------------------------------------------------------------------------
PG_IMAGE="$(ROOT="$ROOT" python3 - <<'PY'
import os, pathlib, tomllib
lock = tomllib.loads((pathlib.Path(os.environ["ROOT"]) / "versions.lock.toml").read_text("utf-8"))
pg = lock["images"]["postgres"]
print(f"{pg['ref']}@{pg['digest']}")
PY
)"
[ -n "$PG_IMAGE" ] || fail "nie udało się odczytać obrazu PostgreSQL z versions.lock.toml"

PYTHON_BIN="$ROOT/services/control_plane/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || fail "brak środowiska $PYTHON_BIN — bez niego nie ma czym uruchomić control plane"

CONTAINER_NAME="wenext-e2e-pg-$$"
PG_PORT="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")"
API_PORT="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")"
PG_PASSWORD="e2e-only-$$"
API_LOG="$(mktemp)"
API_PID=""

cleanup() {
  if [ "${WENEXT_E2E_KEEP:-0}" = "1" ]; then
    echo ""
    echo "== WENEXT_E2E_KEEP=1: stack ZOSTAJE. Ponowny przebieg tylko renderu: =="
    echo "export WENEXT_CONTROL_PLANE_URL='http://host.docker.internal:$API_PORT'"
    echo "export WENEXT_E2E_BASE_URL='http://127.0.0.1:$DASHBOARD_PORT'"
    # Z `PASSTHROUGH`, A NIE Z DRUGIEJ LISTY OBOK. Ta pętla miała kiedyś własny spis nazw i
    # zestarzał się po cichu: zasiew dorobił dokumenty, karty i szkic, a wypisane tu `export`-y
    # ich nie miały, więc ponowny przebieg z `WENEXT_E2E_ATTACH=1` startował bez połowy
    # środowiska i zgłaszał „BRAK ŚRODOWISKA E2E" dla stacku, w którym wszystko stało.
    for name in $PASSTHROUGH; do
      case "$name" in WENEXT_CONTROL_PLANE_URL | WENEXT_E2E_BASE_URL) continue ;; esac
      echo "export $name='${!name-}'"
    done
    echo "export WENEXT_E2E_ATTACH=1"
    echo "REPO_ROOT=\$PWD bash apps/dashboard/tools/e2e_db_render.sh"
    echo ""
    echo "Sprzątanie ręczne:  kill $API_PID; docker rm -f $CONTAINER_NAME"
    return
  fi
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null
  [ -n "${FIXTURE_PID:-}" ] && kill "$FIXTURE_PID" 2>/dev/null
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
  rm -f "$API_LOG"
  [ -n "${CODEX_ROOT:-}" ] && rm -rf "$CODEX_ROOT"
  [ -n "${FIXTURE_DIR:-}" ] && rm -rf "$FIXTURE_DIR"
  [ -n "${ARTIFACTS_DIR:-}" ] && rm -rf "$ARTIFACTS_DIR"
}
trap cleanup EXIT

echo "== start PostgreSQL ($PG_IMAGE) na 127.0.0.1:$PG_PORT =="
docker run -d --name "$CONTAINER_NAME" \
  -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=wenext -e POSTGRES_USER=wenext \
  -p "127.0.0.1:$PG_PORT:5432" \
  "$PG_IMAGE" >/dev/null || fail "nie udało się wystartować Postgresa"

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_NAME" pg_isready -U wenext -d wenext >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$CONTAINER_NAME" pg_isready -U wenext -d wenext >/dev/null 2>&1 \
  || fail "Postgres nie wstał"

# ---------------------------------------------------------------------------
# 2. Every migration, in name order. An empty directory is an error there, not "nothing to do".
# ---------------------------------------------------------------------------
REPO_ROOT="$ROOT" WENEXT_PG_CONTAINER="$CONTAINER_NAME" \
  bash "$ROOT/tools/dev/apply_migrations.sh" || fail "migracje nie przeszły"

export DATABASE_URL="postgresql://wenext:$PG_PASSWORD@127.0.0.1:$PG_PORT/wenext"

# ---------------------------------------------------------------------------
# 3. The rows the suite looks for, inserted with raw SQL.
# ---------------------------------------------------------------------------
echo "== zasiew wierszy (raw SQL) =="
SEED_OUTPUT="$(cd "$ROOT/services/control_plane" && PYTHONPATH="." "$PYTHON_BIN" "$APP_DIR/tools/e2e_seed.py")"
seed_status=$?
[ "$seed_status" -eq 0 ] || fail "zasiew padł (kod $seed_status)"
[ -n "$SEED_OUTPUT" ] || fail "zasiew nie wypisał ani jednej wartości — nie ma czego szukać w HTML-u"

while IFS='=' read -r key value; do
  [ -z "$key" ] && continue
  export "$key=$value"
done <<< "$SEED_OUTPUT"

# KAŻDA WARTOŚĆ Z ZASIEWU MUSI BYĆ NA LIŚCIE PRZEKAZYWANEJ DO KONTENERA. Suita e2e biegnie w
# przypiętym obrazie, a nie na hoście, i widzi wyłącznie zmienne wymienione w $PASSTHROUGH.
# Pominięcie jednej nazwy kosztowało pełny przebieg 20.09: zasiew zapisał dokumenty, strażnik
# niżej widział zmienne na hoście, a test w kontenerze dostał pustkę i zgłosił brak środowiska.
# Lista pisana ręcznie w dwóch miejscach rozjeżdża się przy pierwszym dopisaniu, więc to
# sprawdzenie jest tu zamiast trzeciej listy.
while IFS='=' read -r key _; do
  [ -z "$key" ] && continue
  case " $PASSTHROUGH " in
    *" $key "*) ;;
    *) fail "zasiew podaje $key, ale nie ma jej w PASSTHROUGH; suita w kontenerze jej nie zobaczy" ;;
  esac
done <<< "$SEED_OUTPUT"

for name in WENEXT_E2E_JOB_ID WENEXT_E2E_FRESH_JOB_ID WENEXT_E2E_LEASE_OWNER WENEXT_E2E_OPERATOR_PASSWORD WENEXT_E2E_AUDIT_KIND WENEXT_E2E_COMMUNITY_ID WENEXT_E2E_JUDGMENT_ID WENEXT_E2E_THREAD_TITLE WENEXT_E2E_WORKSPACE_ID WENEXT_E2E_LIVE_PROJECT_ID WENEXT_E2E_LIVE_SESSION_ID WENEXT_E2E_DOCUMENT_ID WENEXT_E2E_DOCUMENT_SENTENCE WENEXT_E2E_DOCUMENT_REMOVED_SENTENCE; do
  [ -n "${!name-}" ] || fail "zasiew nie podał $name"
done
echo "   job z próbami:     $WENEXT_E2E_JOB_ID"
echo "   job bez prób:      $WENEXT_E2E_FRESH_JOB_ID"
echo "   lease_owner:       $WENEXT_E2E_LEASE_OWNER"
echo "   community:         $WENEXT_E2E_COMMUNITY_ID"
echo "   osąd ENGAGE:       $WENEXT_E2E_JUDGMENT_ID"
echo "   workspace:         $WENEXT_E2E_WORKSPACE_ID"

# ---------------------------------------------------------------------------
# 4. The control plane, on the host, bound to loopback.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 3b. A throwaway `codex-home` volume for the Codex connections screen (P1 etap 2).
#
# THE BINARY IS DELIBERATELY A PATH THAT DOES NOT EXIST. With a real one the dispatcher inside
# the control plane would claim the login job this suite creates and start a REAL device-code
# login against a REAL account — an outward action no automated run may perform. With a missing
# one the engine answers `EngineUnavailable`, the connection records a stated reason, and the
# suite can still prove the whole path it cares about: HTTP → PostgreSQL → the directory on disk.
#
# The root carries a stand-in `auth.json` so "the configured home already holds a login" is a
# realistic starting state. It is not a token and nothing ever reads it.
# ---------------------------------------------------------------------------
CODEX_ROOT="$(mktemp -d)"
printf '%s' '{"atrapa":"e2e"}' > "$CODEX_ROOT/auth.json"
printf '%s\n' 'cli_auth_credentials_store = "file"' > "$CODEX_ROOT/config.toml"
chmod 700 "$CODEX_ROOT"
export WENEXT_E2E_CODEX_ROOT="$CODEX_ROOT"
echo "   codex-home (atrapa): $CODEX_ROOT"

# ---------------------------------------------------------------------------
# 3c. A REAL web server with a page and a feed on it (P3 etap A).
#
# WHY A SERVER AND NOT A STUBBED FETCHER. The thing worth proving is that the product fetches an
# address, keeps the bytes and can say what it read and when. A stub would prove the code around
# that. So the page and the feed are served by `python3 -m http.server` on loopback, and the
# control plane is told — explicitly, by name — that this ONE origin may be reached over plaintext
# and on a non-routable address. Every other local address stays refused by the same code path,
# which the suite checks by pasting `http://169.254.169.254/` and expecting a refusal.
#
# `WENEXT_ARTIFACTS_DIR` is a throwaway directory here. Without it the control plane would keep
# the fetched bytes in `/var/wenext/artifacts`, which does not exist on a developer host.
# ---------------------------------------------------------------------------
FIXTURE_DIR="$(mktemp -d)"
ARTIFACTS_DIR="$(mktemp -d)"
FIXTURE_PORT="$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")"
cat > "$FIXTURE_DIR/artykul.html" <<'HTML'
<!doctype html><html><head><title>Kolejki bez ack</title></head>
<body><h1>Kolejki</h1><p>Zgubione wiadomosci: 12 tysiecy na dobe.</p></body></html>
HTML
cat > "$FIXTURE_DIR/kanal.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Notatki o kolejkach</title>
<item><title>Pierwszy wpis</title><link>https://example.test/1</link>
<pubDate>Fri, 19 Sep 2026 10:00:00 GMT</pubDate><description>Opis pierwszego wpisu</description></item>
</channel></rss>
XML
(cd "$FIXTURE_DIR" && exec python3 -m http.server "$FIXTURE_PORT" --bind 127.0.0.1) >/dev/null 2>&1 &
FIXTURE_PID=$!
FIXTURE_ORIGIN="http://127.0.0.1:$FIXTURE_PORT"
for _ in $(seq 1 30); do
  if curl -fsS "$FIXTURE_ORIGIN/artykul.html" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "$FIXTURE_ORIGIN/artykul.html" >/dev/null 2>&1   || fail "serwer materiałów nie wstał na $FIXTURE_ORIGIN"
export WENEXT_E2E_KNOWLEDGE_PAGE_URL="$FIXTURE_ORIGIN/artykul.html"
export WENEXT_E2E_KNOWLEDGE_FEED_URL="$FIXTURE_ORIGIN/kanal.xml"
echo "   materiały (atrapa): $FIXTURE_ORIGIN"

# Limit jednego połączenia strumienia. Dwie sekundy zamiast dwóch minut, żeby suita MOGŁA
# zobaczyć wznowienie: niesprawdzany limit czasu jest tym, przez co błąd z audytu 19.09 przeszedł
# przegląd. Sama wartość jedzie też do testów, żeby nie były zmuszone jej zgadywać.
STREAM_SECONDS="${WENEXT_E2E_STREAM_SECONDS:-2}"
export WENEXT_E2E_STREAM_SECONDS="$STREAM_SECONDS"

echo "== start control plane na 127.0.0.1:$API_PORT =="
# The redirect is on the SUBSHELL, not only on uvicorn. Measured: with `>"$API_LOG"` inside, the
# backgrounded subshell keeps the script's own stdout open, so a caller piping this script into
# `tail` (or anything else) waits for a writer that never closes — the run finishes and the terminal
# hangs. Closing the subshell's stdout is what makes `bash e2e_db_render.sh | tail` terminate.
(
  cd "$ROOT/services/control_plane" || exit 1
  PYTHONPATH="." DATABASE_URL="$DATABASE_URL" \
    WENEXT_CODEX_HOME="$CODEX_ROOT" \
    WENEXT_CODEX_BIN="/nonexistent/wenext-brak-codexa" \
    WENEXT_ARTIFACTS_DIR="$ARTIFACTS_DIR" \
    WENEXT_KNOWLEDGE_FIXTURE_ORIGINS="$FIXTURE_ORIGIN" \
    WENEXT_ORGANIZATION_RESCAN_SECONDS=3 \
    WENEXT_SESSION_STREAM_SECONDS="$STREAM_SECONDS" \
    exec "$PYTHON_BIN" -m uvicorn control_plane.app:create_app --factory \
      --host 127.0.0.1 --port "$API_PORT" --no-access-log
) >"$API_LOG" 2>&1 &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; then
  echo "control plane nie wstał — log:" >&2
  cat "$API_LOG" >&2
  fail "control plane nie odpowiada na /healthz"
fi

# ---------------------------------------------------------------------------
# 5. The dashboard, in the pinned image, talking to the host through host.docker.internal.
# ---------------------------------------------------------------------------
export WENEXT_CONTROL_PLANE_URL="http://host.docker.internal:$API_PORT"
export WENEXT_E2E_BASE_URL="http://127.0.0.1:$DASHBOARD_PORT"

render_and_test
render_status=$?

if [ "$render_status" -ne 0 ]; then
  echo "" >&2
  echo "== log control plane (ostatnie 40 linii) ==" >&2
  tail -40 "$API_LOG" >&2
  exit "$render_status"
fi

echo "== E2E: ZALICZONE =="
exit 0
