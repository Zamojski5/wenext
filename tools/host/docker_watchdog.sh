#!/bin/bash
# Strażnik Docker Desktop dla pilota wenext (D-029 pkt 6). Uruchamiany przez LaunchAgent co 120 s
# (tools/host/pl.wenext.docker-watchdog.plist). Demon odpowiada: zeruje licznik porażek. Nie odpowiada:
# po 2 kolejnych porażkach restartuje Docker Desktop. Nie dotyka kontenerów: wstają same
# (restart: unless-stopped), łańcuch pilota odbudowuje PilotGuard przy pierwszym pollu.
# Log: var/host/docker-watchdog.log. Instalacja: tools/host/README.md.
#
# ZEGAR MUSI ZABIJAĆ, BO INACZEJ STRAŻNIK SAM SIĘ ZAWIESZA. Do 26.09 obie granice czasu
# stały na `perl -e 'alarm N; exec @ARGV' docker ...`. To nie działa: `docker` jest binarką Go,
# a runtime Go przechwytuje SIGALRM i go ignoruje, więc alarm wystrzeliwał w próżnię.
# Skutek zmierzony na tym hoście: `docker desktop restart` z 26.09 01:55 wisiał 17 godzin,
# strażnik czekał na niego w `wait`, a launchd nie startuje drugiej kopii, dopóki pierwsza żyje.
# Przez te 17 godzin nikt nie próbował podnieść demona, choć od tego zależy cały stack.
# Dlatego granicę czasu trzyma teraz `limit_czasu`: SIGKILL (Go go nie zignoruje) i ubicie
# najpierw dzieci, bo `docker desktop restart` odpala plugin CLI jako osobny proces potomny.
ROOT="${WENEXT_ROOT:-$HOME/Dev/Projekty/wenext}"
LOG="$ROOT/var/host/docker-watchdog.log"
STATE="$ROOT/var/host/docker-watchdog.state"
mkdir -p "$ROOT/var/host"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# Uruchamia polecenie z twardą granicą czasu. Zwraca 124 po przekroczeniu limitu, tak jak
# coreutils `timeout`, którego na tym macOS-ie nie ma w PATH LaunchAgenta.
#
# O PRZEKROCZENIU LIMITU ROZSTRZYGA ZNACZNIK, NIE KOD WYJŚCIA. Kat ubija najpierw dzieci, więc
# rodzic zdąża zwrócić własny kod, zanim dosięgnie go SIGKILL: zmierzone na
# `bash -c 'sleep 60 & wait'`, które po ubiciu dziecka kończyło się zerem. Gdyby limit czytać
# z kodu wyjścia, ubity `docker desktop restart` mógłby zostać zapisany jako udany restart,
# a to jest dokładnie ta klasa fałszywego zaliczenia, przed którą ostrzegają KONWENCJE.
limit_czasu() {
  local limit="$1"; shift
  local znacznik; znacznik=$(mktemp -t wenext-watchdog) || return 125
  "$@" &
  local pid=$!
  ( sleep "$limit"; kill -0 "$pid" 2>/dev/null || exit 0
    printf 'ubity' > "$znacznik"
    pkill -9 -P "$pid" 2>/dev/null; kill -9 "$pid" 2>/dev/null ) &
  local kat=$!
  wait "$pid"; local rc=$?
  kill "$kat" 2>/dev/null; wait "$kat" 2>/dev/null
  # mktemp zostawia plik pusty; kat zapisuje do niego przed zabiciem, więc po powrocie z `wait`
  # niepusty znacznik znaczy „limit przekroczony".
  [ -s "$znacznik" ] && rc=124
  rm -f "$znacznik"
  return "$rc"
}

probe() { limit_czasu 25 docker info >/dev/null 2>&1; }

if probe; then
  prev=$(cat "$STATE" 2>/dev/null || echo 0)
  [ "$prev" != "0" ] && log "demon znów odpowiada (po $prev porażkach)"
  echo 0 > "$STATE"
  exit 0
fi
fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
log "demon nie odpowiada (porażka nr $fails)"
if ! pgrep -q -f "Docker Desktop.app"; then
  log "Docker Desktop nie działa: open -a Docker"
  open -a Docker
  exit 0
fi
if [ "$fails" -ge 2 ]; then
  log "restart Docker Desktop (docker desktop restart, w razie porażki quit + open)"
  limit_czasu 90 docker desktop restart >>"$LOG" 2>&1
  rc=$?
  if [ "$rc" != "0" ]; then
    [ "$rc" = "124" ] && log "docker desktop restart przekroczył 90 s i został ubity"
    osascript -e 'quit app "Docker"' >/dev/null 2>&1 || true
    sleep 20
    pgrep -q -f "Docker Desktop.app" && pkill -f "Docker Desktop.app" || true
    sleep 5
    open -a Docker
  fi
  echo 0 > "$STATE"
fi
