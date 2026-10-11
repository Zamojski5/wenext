#!/usr/bin/env bash
# The dashboard suite, as `make test-all` runs it. ONE docker run, three steps.
#
#   1. `next build` — a page that does not compile is a broken image, and vitest alone would
#      not notice: the suite imports the layout and the components, not every route handler.
#      This is the cheapest place to find out, and it is the same reason `make lint` is a
#      target rather than a habit.
#   2. `vitest run` — the suite proper, with a JSON report written out as evidence.
#   3. `tools/verify_run.mjs` — refuses the run if the report says nothing was executed.
#      Vitest exits 0 on "no test files found"; that is `all([]) == True` wearing a different
#      hat, and this project has already paid for it three times (D-011).
#
# Everything runs inside the PINNED image (tools/node.sh). The host has Node v24.12.0 and the
# lock pins 24.20.0 — see apps/dashboard/NOTES.md for why that decision went this way.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HERE/.." && pwd)"

# Checked here as well as in `tools/dev/run_all_tests.sh`, so running this script by hand
# cannot silently test nothing. `npm ci` is NOT run for you: a suite runner that installs
# dependencies hides the difference between "the environment is missing" and "the suite is
# red", and the first of those has to stay loud.
if [ ! -d "$APP_DIR/node_modules" ]; then
  echo "BRAK ŚRODOWISKA: nie ma $APP_DIR/node_modules — suita NIE została uruchomiona." >&2
  echo "                 Zbuduj je: cd apps/dashboard && bash tools/node.sh npm ci" >&2
  exit 2
fi

exec bash "$HERE/node.sh" sh -c '
set -e
npm run build
npm test -- --reporter=default --reporter=json --outputFile=/tmp/vitest-run.json
node tools/verify_run.mjs /tmp/vitest-run.json unit
'
