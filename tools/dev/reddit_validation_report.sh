#!/usr/bin/env bash
# Raport walidacji jednego projektu na Reddicie (P4 i P5).
#
#   make reddit-validation-report PILOT_ARGS="--project <uuid> --days 1"
#   REPO_ROOT=$PWD bash tools/dev/reddit_validation_report.sh --project <uuid> --days 1
#
# Cienka warstwa nad `reddit_validation_report.py`: wybiera interpreter i przekazuje argumenty.
# Cała logika, progi i reguła NIE_URUCHOMIONO są w Pythonie, bo tam da się je przetestować.
#
# BAZA JEST TYLKO CZYTANA (SELECT-y przez `docker exec`). Ten skrypt nie stawia stacku ani go
# nie zatrzymuje, więc można go puścić przy działającym pilocie.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

# Interpreter z venva control plane, bo tam jest PyYAML. Bez niego skrypt też działa: czyta progi
# awaryjnym czytnikiem i mówi o tym w raporcie tylko tyle, ile naprawdę odczytał.
PYTHON="$ROOT/services/control_plane/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
[ -n "$PYTHON" ] || { echo "NIE_URUCHOMIONO: nie ma interpretera Pythona" >&2; exit 3; }

REPO_ROOT="$ROOT" exec "$PYTHON" "$ROOT/tools/dev/reddit_validation_report.py" "$@"
