#!/usr/bin/env bash
# Runs every suite that can run on the host and prints a combined result.
#
# What is NOT here, and why:
#   - tests needing the Chrome capsule — those go through `make gonogo` in a container,
#   - tests marked `live` — they need a ChatGPT account (see scripts/founder/),
#   - lease tests — they have their own runner because they bring up a Postgres
#     container (`tools/dev/run_lease_tests.sh`); this script calls it.
#
# Operator-facing output stays Polish on purpose (D-010): the founder reads this
# summary, and the Go/No-Go report speaks Polish too.
#
# A MISSING ENVIRONMENT IS A FAILURE, NOT A SKIP.
# Until 2026-08-31 the missing-interpreter branch of `run_suite` appended a "POMINIĘTE"
# row and did a bare `return` — WITHOUT touching `failures`. A suite whose `.venv` had
# never been built was therefore skipped in silence while `make test-all` still exited 0
# and printed "Wszystkie suity przeszły". That is exactly the disease named in
# docs/KONWENCJE.md L110-111: "Polecenie, które kończy się kodem 0, nie znaczy, że coś
# zrobiło. (...) `make build` przez wiele tygodni budował 4 z 8 obrazów i wychodził na
# zero." Phase E1.F1 adds new service packages, so an unbuilt environment must never be
# able to look like a green run.
# The ONLY skip that does not count as a failure is one declared HERE, BY NAME, through
# `skip_suite` with a reason. A skip has to be a decision; it must never be a side effect
# of a missing directory or an unbuilt `.venv`.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT"

RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
failures=0
# Number of pytest suites that were actually started. Guards the summary against
# announcing success for a run in which nothing ran — see the check before the verdict.
executed=0
declared_skips=0
summary=()

# run_suite <name> <dir> <python> <pythonpath> [pytest args...]
#
# `pythonpath` is a SEPARATE, explicit parameter. An earlier version read it as
# `${4:-.}` after `shift 3` — once shifted, `$4` points at a different argument, so
# fixture-site got PYTHONPATH="." and failed to import its own package. The suite blew
# up inside the runner while passing when run by hand.
run_suite() {
  local name="$1" dir="$2" python_bin="$3" pythonpath="$4"; shift 4
  printf '\n=== %s ===\n' "$name"

  # Two ways a suite can fail to start, both counted as failures, both labelled
  # "BRAK ŚRODOWISKA" so the summary keeps them apart from "PADŁO" (the suite ran and
  # something in it was red). Different repair, identical consequence: non-zero exit.
  if [ ! -d "$dir" ]; then
    summary+=("${RED}BRAK ŚRODOWISKA${RESET} $name (nie ma katalogu $dir)")
    echo "  BŁĄD: nie ma katalogu $dir — suita NIE została uruchomiona"
    failures=$((failures + 1))
    return
  fi
  if [ ! -x "$python_bin" ]; then
    summary+=("${RED}BRAK ŚRODOWISKA${RESET} $name (nie ma $python_bin)")
    echo "  BŁĄD: brak środowiska $python_bin — suita NIE została uruchomiona"
    echo "        zbuduj je ('uv sync' w $dir) albo — jeśli pominięcie jest świadome —"
    echo "        zadeklaruj je z nazwy przez skip_suite w tym skrypcie"
    failures=$((failures + 1))
    return
  fi

  executed=$((executed + 1))
  if (cd "$dir" && PYTHONPATH="$pythonpath" "$python_bin" -m pytest "$@" -q 2>&1 | tail -20); then
    summary+=("${GREEN}OK${RESET}              $name")
  else
    summary+=("${RED}PADŁO${RESET}           $name")
    failures=$((failures + 1))
  fi
}

# skip_suite <name> <reason>
#
# The one legitimate skip: a suite consciously left out of the host run (a suite needing
# a live ChatGPT account, say — see scripts/founder/). It must be written here by name
# with a reason the founder can read in the summary. Anything that is NOT written here —
# a suite whose `.venv` or directory is missing — goes through `run_suite` and counts as
# a failure. Nothing can reach this function by accident.
skip_suite() {
  local name="${1:-}" reason="${2:-}"
  if [ -z "$name" ] || [ -z "$reason" ]; then
    echo "BŁĄD SKRYPTU: skip_suite wymaga nazwy suity i powodu pominięcia" >&2
    summary+=("${RED}PADŁO${RESET}           skip_suite bez nazwy albo bez powodu")
    failures=$((failures + 1))
    return
  fi
  printf '\n=== %s ===\n' "$name"
  echo "  pominięte świadomie: $reason"
  declared_skips=$((declared_skips + 1))
  summary+=("${YELLOW}POMINIĘTE${RESET}       $name (świadomie: $reason)")
}

# run_js_suite <name> <dir>
#
# The JavaScript half of `run_suite`: same contract, different toolchain. `run_suite` cannot be
# reused because it hardcodes `-m pytest`, and the dashboard suite is vitest driven from
# `<dir>/tools/check.sh` — one `docker run` on the Node version pinned in versions.lock.toml,
# for the same reason `make fmt` and `make lint` go through the pinned ruff image instead of the
# developer's PATH. This host runs Node v24.12.0 while the lock says 24.20.0; details in
# apps/dashboard/NOTES.md.
#
# THE THREE ENVIRONMENT CHECKS BELOW ARE FAILURES, NOT SKIPS — the property this whole script
# was repaired for on 2026-08-31. `node_modules` in particular: it is not in git, so the first
# run on a fresh clone finds it missing, and that has to be a red line rather than a quiet
# "POMINIĘTE". A conscious skip still has to be declared by name through `skip_suite`.
#
# It does NOT run `npm ci` for you. A runner that installs what it is missing erases the
# difference between "the environment was never built" and "the suite is red".
run_js_suite() {
  local name="$1" dir="$2"
  printf '\n=== %s ===\n' "$name"

  if [ ! -d "$dir" ]; then
    summary+=("${RED}BRAK ŚRODOWISKA${RESET} $name (nie ma katalogu $dir)")
    echo "  BŁĄD: nie ma katalogu $dir — suita NIE została uruchomiona"
    failures=$((failures + 1))
    return
  fi
  if [ ! -x "$dir/tools/check.sh" ]; then
    summary+=("${RED}BRAK ŚRODOWISKA${RESET} $name (nie ma $dir/tools/check.sh)")
    echo "  BŁĄD: brak wykonywalnego $dir/tools/check.sh — suita NIE została uruchomiona"
    failures=$((failures + 1))
    return
  fi
  if [ ! -d "$dir/node_modules" ]; then
    summary+=("${RED}BRAK ŚRODOWISKA${RESET} $name (nie ma $dir/node_modules)")
    echo "  BŁĄD: brak środowiska $dir/node_modules — suita NIE została uruchomiona"
    echo "        zbuduj je ('bash tools/node.sh npm ci' w $dir) albo — jeśli pominięcie jest"
    echo "        świadome — zadeklaruj je z nazwy przez skip_suite w tym skrypcie"
    failures=$((failures + 1))
    return
  fi

  # DELIBERATELY NOT counted in `executed`. That counter means "pytest suites that actually
  # started" and exists so that an evaporated suite list cannot look like a green run. A JS
  # suite incrementing it would let the dashboard alone satisfy that guard while every Python
  # suite had silently vanished. This row still reaches the summary and still moves `failures`,
  # which is what decides the exit code — the counter only has to stay unable to hide anything.
  if (cd "$dir" && bash tools/check.sh 2>&1 | tail -20); then
    summary+=("${GREEN}OK${RESET}              $name")
  else
    summary+=("${RED}PADŁO${RESET}           $name")
    failures=$((failures + 1))
  fi
}

run_suite "contracts"          "$ROOT/contracts"                   "$ROOT/contracts/.venv/bin/python"                   "src"  tests
run_suite "browser_supervisor" "$ROOT/services/browser_supervisor" "$ROOT/services/browser_supervisor/.venv/bin/python" "."    tests
run_suite "stream_gateway"     "$ROOT/services/stream_gateway"     "$ROOT/services/stream_gateway/.venv/bin/python"     "."    tests
run_suite "fixture-site"       "$ROOT/fixtures/site"               "$ROOT/fixtures/site/.venv/bin/python"               "src"  tests
run_suite "codex_runtime"      "$ROOT/services/codex_runtime"      "$ROOT/services/codex_runtime/.venv/bin/python"      "."    tests -m "not live"
run_suite "mcp_sidecar"        "$ROOT/services/mcp_sidecar"        "$ROOT/services/mcp_sidecar/.venv/bin/python"        "."    tests
run_suite "community_observer" "$ROOT/services/community_observer" "$ROOT/services/community_observer/.venv/bin/python" "."    tests -m "not live"
run_suite "evals"              "$ROOT/evals"                       "$ROOT/evals/.venv/bin/python"                       "."    . -m "not live"
run_suite "spójność repo"      "$ROOT"                             "$ROOT/contracts/.venv/bin/python"                   "."    tests

# --- E1.F1 -------------------------------------------------------------------
# Both packages carry tests that need PostgreSQL, marked `db`. Those are DESELECTED here with
# `-m "not db"` — deselected, not skipped: pytest reports "N deselected" in the summary, while
# a skip reports nothing a reader distinguishes from a pass. What is left over runs on the host
# with no container.
#
# The database halves are declared as conscious skips below, by name, with the command that
# runs them. That is the one legitimate kind of skip in this script.
run_suite "db_schema"          "$ROOT/services/db_schema"          "$ROOT/services/db_schema/.venv/bin/python"          "."    tests -m "not db"
run_suite "control_plane"      "$ROOT/services/control_plane"      "$ROOT/services/control_plane/.venv/bin/python"      "."    tests -m "not db"

# `make test-db` is the runner for these; it brings up a single-use PostgreSQL per package,
# applies every migration and refuses a run with `passed == 0` or `skipped > 0`.
#
# Why they are not simply started from here, next to the lease suite: this script is itself
# under test (`tests/test_all_tests_runner.py`), and those tests run it against a FAKE
# repository root in which every external script it calls has to be stubbed by name. Adding a
# call to `tools/dev/run_db_tests.sh` here therefore requires a matching stub in a test file
# that belongs to another package. Declaring the skip is loud, costs nothing, and does not
# quietly break somebody else's suite; the alternative — deselecting `db` above and saying
# nothing — is the silent hole this whole script was repaired to close.
skip_suite "db_schema (baza)"     "wymaga kontenera Postgresa — uruchom 'make test-db'"
skip_suite "control_plane (baza)" "wymaga kontenera Postgresa — uruchom 'make test-db'"

# The dashboard (apps/dashboard) — the first non-Python suite in this repository, and until
# today the runner had no branch for JavaScript at all. A package whose tests no runner starts
# is a package with no tests (`tests/test_suites_are_wired.py`), and that scanner only looks for
# Python packages, so this line is the whole wiring.
#
# `tools/check.sh` runs `next build` and then vitest, and refuses a run whose JSON report says
# nothing was executed — the same rule `tools/dev/run_db_tests.sh` applies to the JUnit XML.
run_js_suite "dashboard (Next.js)" "$ROOT/apps/dashboard"

printf '\n=== spójność dokumentacji z kodem ===\n'
if python3 "$ROOT/tools/dev/check_docs.py"; then
  summary+=("${GREEN}OK${RESET}              dokumentacja")
else
  summary+=("${RED}PADŁO${RESET}           dokumentacja")
  failures=$((failures + 1))
fi

# The package path is spelled out in this header on purpose: `tests/test_suites_are_wired.py`
# looks for every package directory that owns tests somewhere in this file, and this suite is
# started through its own runner rather than through `run_suite`. Without the path here, the
# only suite in the repository launched by a dedicated script would read as "forgotten".
printf '\n=== lease + fencing: services/profile_lease (własny kontener Postgresa) ===\n'
if bash "$ROOT/tools/dev/run_lease_tests.sh" 2>&1 | tail -6; then
  summary+=("${GREEN}OK${RESET}              profile_lease")
else
  summary+=("${RED}PADŁO${RESET}           profile_lease")
  failures=$((failures + 1))
fi

printf '\n\n======================= PODSUMOWANIE =======================\n'
for row in "${summary[@]}"; do printf '  %s\n' "$row"; done
printf '\n'

# `all([]) == True` has already produced two false passes in this project. A run that
# started no suite at all is not a green run — it is a broken runner, and it says so.
if [ "$executed" -eq 0 ]; then
  echo "BŁĄD: nie uruchomiono ANI JEDNEJ suity pytest — to nie jest wynik pozytywny."
  failures=$((failures + 1))
fi

printf 'Suit uruchomionych: %d, świadomie pominiętych: %d, z porażką: %d\n' \
  "$executed" "$declared_skips" "$failures"
if [ "$failures" -eq 0 ]; then
  echo "Wszystkie suity przeszły."
else
  echo "Suit z porażką: $failures"
fi
exit "$failures"
