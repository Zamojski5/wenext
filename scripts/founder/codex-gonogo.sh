#!/usr/bin/env bash
# =============================================================================
# GATE-3 — Codex as a marketing runtime. The ONLY step of phase E1.F0 needing a human.
#
# What this script does NOT do, and never will:
#   * it does not bypass login or MFA (section 4, L243),
#   * it does not drive the ChatGPT site over CDP — the official path is a local
#     app-server logged in with the ChatGPT method (L2121),
#   * it does not guess the outcome. No confirmation means `NIE_URUCHOMIONO`, not `GO`.
#
# A `NO_GO` here is a CORRECT and useful result: it means the runtime is unfit for
# unattended work. The source document allows swapping the runtime without rebuilding
# the domain (L2127), so `NO_GO` does not stop the project — only `BLOCK` at the E1
# gate does, six phases later.
#
# Operator-facing text stays Polish on purpose (D-010): the founder reads it.
# =============================================================================
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"
COMPOSE="${COMPOSE:-docker compose}"

ARTIFACTS="$ROOT/artifacts/gate3"
CODEX_HOME_HOST="$ROOT/var/codex-home"

# Six hours by default. The window has to be comparable with the system working
# overnight in F6, not a symbolic five minutes — L2129 says "multi-hour".
HOURS="${WENEXT_UNATTENDED_HOURS:-6}"

header() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }
info()     { printf '  %s\n' "$1"; }
error()     { printf '\033[31m  %s\033[0m\n' "$1" >&2; }

# ---------------------------------------------------------------------------
header "0. Warunki wstępne"

if ! command -v docker >/dev/null 2>&1; then
  error "Brak dockera. Uruchom najpierw 'make doctor'."
  exit 1
fi
mkdir -p "$ARTIFACTS" "$CODEX_HOME_HOST"
chmod 700 "$CODEX_HOME_HOST"
info "CODEX_HOME: $CODEX_HOME_HOST (prawa 700)"
info "Artefakty:  $ARTIFACTS"

header "1. Budowanie obrazu runtime'u Codeksa"
if ! $COMPOSE --profile codex build codex; then
  error "Build serwisu 'codex' nie przeszedł."
  exit 1
fi

# ---------------------------------------------------------------------------
header "2. Logowanie kontem ChatGPT"
cat <<'TEKST'
  Za chwilę zobaczysz URL weryfikacyjny i kod. Otwórz ten adres w przeglądarce
  i wpisz kod, będąc zalogowanym na koncie ChatGPT, którego system ma używać.

  Uwagi, które oszczędzą ci czasu:

  * Metoda device code jest w becie i bywa wyłączona po stronie konta albo workspace'u.
    To, czy jest dostępna, rozstrzyga ODPOWIEDŹ serwera, a nie ten skrypt.
    Jeżeli metoda jest niedostępna, dostaniesz jawny error i fallback:
        docker compose --profile codex run --rm codex codex login --device-auth
  * Loguj się na koncie, które faktycznie ma pracować — plan i limity tego konta
    są przedmiotem pomiaru. Logowanie "testowym" kontem unieważnia wynik bramki.
  * Regulamin ChatGPT zakazuje współdzielenia konta. To ma być konto jednego z was.

TEKST

read -r -p "  Gotów? [enter aby kontynuować, Ctrl+C aby przerwać] " _

if ! $COMPOSE --profile codex run --rm \
      -v "$CODEX_HOME_HOST:/var/wenext/codex-home" \
      codex python -m codex_runtime.login; then
  error "Logowanie nie powiodło się."
  info "To jest NIE_URUCHOMIONO, nie NO_GO — bramka pozostaje nierozstrzygnięta."
  info "Raport i tak powstanie; uruchom 'make gonogo', żeby zobaczyć stan."
  exit 1
fi

# ---------------------------------------------------------------------------
header "3. Kontrakt SDK i izolacja wątków (szybkie, kilka minut)"
$COMPOSE --profile codex run --rm \
  -v "$CODEX_HOME_HOST:/var/wenext/codex-home" \
  -v "$ARTIFACTS:/var/wenext/artifacts/gate3" \
  codex python gate3.py --artifacts /var/wenext/artifacts/gate3 --codex-home /var/wenext/codex-home

# ---------------------------------------------------------------------------
header "4. Test pracy bez nadzoru ($HOURS h)"
cat <<TEKST
  Teraz najdłuższa część. Skrypt będzie przez $HOURS h cyklicznie:
    - wykonywał turny na przypiętych trasach model/effort,
    - restartował runtime i sprawdzał, czy auth przeżywa restart,
    - obserwował zachowanie przy wyczerpaniu quoty,
    - wznawiał wątki i sprawdzał, czy resume jest fail-closed przy zmianie bindingu.

  WAŻNE: zamknij dashboard i nie dotykaj tego terminala. Pytanie brzmi, czy runtime
  wytrzymuje pracę BEZ otwartego dashboardu (L2129) — obecność człowieka fałszuje wynik.

  Kontener startuje ODCZEPIONY. Możesz zamknąć ten terminal, a nawet ten skrypt —
  przebieg to przeżyje. Przerwanie: `docker stop` na nazwie wypisanej niżej.
  Częściowy wynik zostanie zapisany, a bramka pozostanie NIE_URUCHOMIONA — to jest
  uczciwe i tak ma być.

TEKST

read -r -p "  Startujemy? [enter] " _

# ---------------------------------------------------------------------------
# DLACZEGO ODCZEPIONY I BEZ HEALTHCHECKU — to nie jest optymalizacja, to warunek
# powodzenia. Cztery z pięciu podejść do GATE-3 padły dokładnie tutaj (D-012):
#
#   * `docker compose run` BEZ `-d` trzyma podłączonego klienta przez całe okno.
#     Każde zająknięcie demona zrywa strumień (`error waiting for container:
#     unexpected EOF`) i zabija przebieg. Udany przebieg nr 6 zrobiono ręcznie,
#     z pominięciem tego skryptu — i przez to skrypt został z wadą aż do dziś.
#   * healthcheck (`codex --version` co 10 s, timeout 10 s) przy oknie liczonym
#     w godzinach piętrzy wywołania i wywraca VM Dockera. Wyłącza go nakładka
#     compose.gate3-unattended.yml. Kontrakt D12 sprawdza i tak krok 3 powyżej.
#
# Nie zwijaj tego z powrotem do `run --rm` bez `-d`. Kosztowało to już cztery
# popołudnia.
# ---------------------------------------------------------------------------
RUN_NAME="wenext-gate3-$(date +%Y%m%d-%H%M%S)"

if ! $COMPOSE -f "$ROOT/docker-compose.yml" -f "$ROOT/compose.gate3-unattended.yml" \
      --profile codex run -d --name "$RUN_NAME" \
      -v "$CODEX_HOME_HOST:/var/wenext/codex-home" \
      -v "$ARTIFACTS:/var/wenext/artifacts/gate3" \
      codex python -m codex_runtime.unattended \
        --hours "$HOURS" \
        --artifacts /var/wenext/artifacts/gate3; then
  error "Nie udało się wystartować przebiegu bez nadzoru."
  info "To jest NIE_URUCHOMIONO, nie NO_GO."
  exit 1
fi

# Docker Desktop sam się pauzuje przy niskim obciążeniu (Resource Saver), a przerwy
# między cyklami są długie. Kontener podtrzymujący trzyma VM zajętą. ~4% CPU, nie
# dotyka runtime'u ani jego uwierzytelnienia (D-012, odstępstwo nr 3).
docker rm -f wenext-keepalive >/dev/null 2>&1
docker run -d --name wenext-keepalive --restart unless-stopped \
  busybox sh -c 'while true; do echo -n .; sleep 1; done' >/dev/null 2>&1 \
  && info "Kontener podtrzymujący VM: wenext-keepalive"

cat <<TEKST

  Przebieg wystartował ODCZEPIONY jako: $RUN_NAME

  Zablokuj jeszcze uśpienie macOS-a w OSOBNYM terminalu i zostaw to otwarte:
      caffeinate -ims

  Podgląd (czytaj LOGI, nigdy nie podłączaj się do strumienia):
      docker logs -f $RUN_NAME

  Stan bramki — czytaj \`zakonczony\`, nie \`planned_hours\`:
      python3 - <<'PY'
      import json, datetime
      d = json.load(open("artifacts/gate3/codex-runtime.json"))
      p = next(k for k in d["kontrole"] if k["nazwa"] == "unattended_rozstrzygniete")["szczegoly"]["przebieg"]
      if p["zakonczony"] is None:
          print("PRZEBIEG NIEDOMKNIĘTY — brak wyniku bramki")
      else:
          h = (datetime.datetime.fromisoformat(p["zakonczony"])
               - datetime.datetime.fromisoformat(p["rozpoczety"])).total_seconds() / 3600
          print(f"zmierzone {h:.2f} h, planowane {p['planned_hours']} h, cykli {p['cycles_run']}")
      PY

  Gdy się skończy (po ~$HOURS h), zbuduj raport:
      make gonogo
  i posprzątaj:
      docker rm -f $RUN_NAME wenext-keepalive

TEKST

# Raport NIE powstaje tutaj: przebieg dopiero wystartował i nie ma czego raportować.
# Wcześniejsza wersja tego skryptu wołała report.py zaraz po kroku 4, co przy
# przerwanym przebiegu produkowało raport z artefaktu poprzedniego pomiaru.
exit 0

# ---------------------------------------------------------------------------
header "5. Raport"
python3 "$ROOT/tools/gonogo/report.py" --artifacts "$ROOT/artifacts" --out "$ROOT/artifacts/gonogo"

cat <<'TEKST'

  Co dalej:

  * GO na GATE-3  → faza E1.F0 jest zamknięta. Następna jest F1 (correctness kernel,
                    linie 5184-5219 dokumentu).
  * NO_GO         → runtime nie nadaje się do pracy bez nadzoru. To NIE zatrzymuje
                    projektu: albo pracujecie pod nadzorem, albo wymieniacie runtime.
                    Domena się nie zmienia (L2127). Zapiszcie decyzję w docs/adr/.
  * NIE_URUCHOMIONO → coś przerwało pomiar. Powtórzcie; nie interpretujcie tego
                    jako wyniku negatywnego.

TEKST
