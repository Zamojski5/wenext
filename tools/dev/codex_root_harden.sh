#!/usr/bin/env bash
# Pokaż hardening korzenia codex-home, w kontenerze api (P4 etap B).
#
#   make codex-root-harden PILOT_ARGS="--dry-run"
#   make codex-root-harden PILOT_ARGS="--apply"    # tylko przy zatrzymanym pilocie
#
# Domyślnie NIC NIE ZAPISUJE. Zapis wymaga `--apply` i sprawdzenia w bazie, że nie ma biegu
# pilota w stanie ACTIVE ani żywej polityki.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"
ARGS="${PILOT_ARGS:---dry-run}"

if [ -n "${WENEXT_HARDEN_LOCAL:-}" ]; then
    PYTHONPATH="$ROOT/services/codex_runtime" \
        "$ROOT/services/control_plane/.venv/bin/python" \
        -m codex_runtime.root_harden $ARGS
    exit $?
fi

docker compose -f "$ROOT/docker-compose.yml" exec -T api \
    python -m codex_runtime.root_harden $ARGS
