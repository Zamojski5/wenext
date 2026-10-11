#!/usr/bin/env bash
# Plan przeniesienia pilota do projektu, W KONTENERZE api (P4 etap B).
#
#   make pilot-to-project
#   make pilot-to-project PILOT_ARGS="--organization <uuid>"
#   make pilot-to-project PILOT_ARGS="--organization <uuid> --apply"
#
# WEJŚCIE JEST MODUŁEM PAKIETU, nie plikiem z `tools/dev`: obraz api nie zawiera katalogu
# `tools/`, więc `python3 /opt/wenext/tools/dev/...` kończyło się „can't open file" (zmierzone
# na live 19.09). Wszystko, co ma się wykonać w kontenerze, musi być tym, co do kontenera trafia.
#
# Na live są DWIE organizacje, a most dotyczy tej, w której stoi pilot; wskazuj ją jawnie
# przez --organization, tak samo jak przy p2-live-check.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

if [ -n "${WENEXT_BRIDGE_DSN:-}" ]; then
    # Ścieżka testowa: baza podana wprost, bez kontenera api.
    cd "$ROOT/services/control_plane" || exit 3
    DATABASE_URL="$WENEXT_BRIDGE_DSN" PYTHONPATH=. \
        "$ROOT/services/control_plane/.venv/bin/python" \
        -m control_plane.presence.bridge ${PILOT_ARGS:-}
    exit $?
fi

docker compose -f "$ROOT/docker-compose.yml" exec -T api \
    python -m control_plane.presence.bridge ${PILOT_ARGS:-}
