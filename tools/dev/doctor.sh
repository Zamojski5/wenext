#!/usr/bin/env bash
# Checks whether the host can run the E1.F0 stack.
# A separate script because macOS ships GNU Make 3.81 without `.ONESHELL` — a
# multi-line recipe would split into separate shells and `fail` would not survive.
#
# Operator-facing output stays Polish on purpose (D-010).
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
fail=0

echo "== host =="
uname -sm

echo "== docker =="
docker --version || fail=1
docker compose version || fail=1

echo "== DOCKER_CONFIG =="
echo "  ${DOCKER_CONFIG:-<nieustawione>}"
if [ -f "${DOCKER_CONFIG:-}/config.json" ]; then
  echo "  config.json obecny: $(cat "${DOCKER_CONFIG}/config.json")"
else
  echo "  BRAK config.json — uruchom 'make env'"
  echo "  Bez tego helper poświadczeń Dockera na tym hoście wiesza każdy pull."
  fail=1
fi

echo "== architektura =="
docker_arch=$(docker info --format '{{.Architecture}}' 2>/dev/null || echo nieznana)
host_arch=$(uname -m)
echo "  host:   ${host_arch}"
echo "  docker: ${docker_arch}"
if [ "${host_arch}" = "arm64" ] && [ "${docker_arch}" = "x86_64" ]; then
  echo "  UWAGA: Docker raportuje x86_64 na hoście arm64 — to emulacja."
  echo "  Chrome pod emulacją praktycznie wisi. Ta ścieżka jest odrzucona (ADR-0002)."
  fail=1
fi

echo "== pamięć dla Dockera =="
mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
mem_gb=$(( mem / 1024 / 1024 / 1024 ))
echo "  ${mem_gb} GB"
if [ "${mem_gb}" -lt 4 ]; then
  echo "  Za mało. Chromium z --shm-size=2g potrzebuje realnie ≥ 4 GB."
  fail=1
fi

# Obciążenie hosta. Dopisane 19 września 2026, bo tego dnia demon Dockera padł DWA RAZY przy
# obciążeniu około 20 na 14 rdzeniach: `docker ps` przestawał odpowiadać, kontenery live znikały
# z listy, a stack wracał dopiero po samoczynnym restarcie Docker Desktop. To nie jest teoria
# o wydajności, tylko zmierzona przyczyna dwóch awarii pilota.
echo "== obciążenie hosta =="
cores=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
load=$(uptime | sed -n 's/.*averages*: *\([0-9.]*\).*/\1/p')
load=${load:-0}
echo "  rdzeni: ${cores}, obciążenie (1 min): ${load}"
# Porównanie w awk, bo bash nie liczy ułamków, a load zawsze ma kropkę.
if awk -v l="${load}" -v c="${cores}" 'BEGIN { exit !(l > c) }'; then
  echo "  UWAGA: obciążenie przekracza liczbę rdzeni."
  echo "  Na macOS Docker Desktop traci przy tym demona: 'docker ps' wisi, kontenery padają."
  echo "  Zmierzone dwa razy 19.09.2026. Zwolnij maszynę przed ciężkim przebiegiem."
  # To nie jest błąd konfiguracji, tylko stan chwili: nie podnosi `fail`, bo doctor ma
  # odpowiadać na pytanie „czy ten host UMIE uruchomić stack", a nie „czy akurat teraz może".
fi
echo "  Zalecenie stałe: w Docker Desktop ustaw limit rdzeni na co najwyżej połowę rdzeni hosta"
echo "  i pamięć na 8 GB. Bez limitu maszyna wirtualna konkuruje o procesor z tym, co na hoście."

echo "== .env =="
if [ -f "${ROOT}/.env" ]; then echo "  obecny"; else echo "  BRAK — uruchom 'make env'"; fail=1; fi

echo "== przypięte wersje =="
python3 "${ROOT}/tools/dev/check_versions.py" || fail=1

echo
if [ "${fail}" -eq 0 ]; then
  echo "doctor: host gotowy."
else
  echo "doctor: są problemy (powyżej)."
fi
exit "${fail}"
