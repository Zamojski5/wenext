#!/usr/bin/env bash
# Lease and fencing tests against a single-use PostgreSQL.
#
# The whole body of this script moved into `tools/dev/run_db_tests.sh` on 2026-08-31, and what
# is left is the delegation. Two reasons, both defects rather than tidiness:
#
#   1. This script applied `0001_f0_kernel.sql` BY NAME. Phase E1.F1 adds migrations 0002 and
#      0003; a runner still applying only the first one does not fail — it silently tests an
#      older schema. `apply_migrations.sh` (called by run_db_tests.sh) applies all of them.
#   2. It ran pytest and trusted the exit code. A module whose tests are all skipped exits 0.
#      run_db_tests.sh reads --junitxml and refuses a run with `passed == 0` or `skipped > 0`.
#
# `services/profile_lease/tests/test_lease_fencing.py` still carries
# `skipif(not WENEXT_TEST_DSN)`. That file belongs to phase E1.F0 and is not touched here — but
# it is now covered from the outside: the DSN is always set on this path, so nothing is skipped,
# and if that ever changes the runner fails instead of going quietly green.
#
# Operator-facing output stays Polish on purpose (D-010).
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

exec env REPO_ROOT="$ROOT" bash "$ROOT/tools/dev/run_db_tests.sh" services/profile_lease "$@"
