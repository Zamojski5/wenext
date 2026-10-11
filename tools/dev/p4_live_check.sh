#!/usr/bin/env bash
# Jeden tik obecności na PRAWDZIWYM Codeksie, w kontenerze api (P4 etap B).
#
#   make p4-live-check
#   make p4-live-check PILOT_ARGS="--project <uuid>"
#
# Biegnie tam, gdzie mieszka zalogowany Codex: w kontenerze api, na wolumenie codex-home.
# `exec` wchodzi do DZIAŁAJĄCEGO kontenera; niczego nie stawia i nie przebudowuje.
#
# TO NIE JEST TEST i nie ma prawa biec w suicie: wydaje cudzy limit konta i zależy od modelu.
# Werdykt czyta się z wypisanego zdania; kod wyjścia jest dla Make.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

docker compose -f "$ROOT/docker-compose.yml" exec -T api \
    python -m control_plane.presence.livecheck ${PILOT_ARGS:-}
