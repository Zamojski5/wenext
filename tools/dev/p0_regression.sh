#!/usr/bin/env bash
# Zestaw regresji P0 (docs/ROADMAP-PIVOT.md): praca nie ginie i nie dubluje się, a status
# odróżnia wykonanie, niepewny skutek i potwierdzony rezultat.
#
#   make p0-regression
#   REPO_ROOT=$PWD bash tools/dev/p0_regression.sh
#
# PO CO TO JEST OSOBNYM SKRYPTEM, A NIE CELEM W MAKEFILE: macOS ma GNU Make 3.81, bez
# `.ONESHELL`. Wielolinijkowa recepta rozpada się tam na osobne powłoki i zmienne nie przeżywają
# między liniami (docs/KONWENCJE.md). Recepta celu ma więc jedną linię i woła ten plik.
#
# CO TEN SKRYPT MIERZY. Pięć scenariuszy, każdy z osobnym, świeżym Postgresem i osobnym
# werdyktem:
#
#   1. restart w czasie zadania            zabity worker, job wykonany dokładnie raz
#   2. utrata lease'u i zombie             stary worker nie zapisuje nic po przejęciu
#   3. wolna baza                          heartbeat w TTL trzyma lease; zawieszenie bazy ponad
#                                          TTL gubi joba CZYTELNIE i bez drugiego efektu ubocznego
#   4. niejednoznaczny submit              po SUBMIT_STARTED nie ma ponowienia, jest rekoncyliacja
#   5. usunięta treść nie jest sukcesem    PUBLISHED + odczyt autora „jest” + anonimowy „nie ma”
#                                          daje REMOVED, nigdy potwierdzenia
#
# REGUŁA, KTÓREJ TEN SKRYPT PILNUJE NA SOBIE SAMYM (docs/KONWENCJE.md, D-007): nie wolno mu
# ogłosić sukcesu bez dowodu. Scenariusz bez ANI JEDNEGO zdanego testu to porażka, nie „nic do
# zrobienia”; jeden pominięty test to porażka, bo pominięty test bazodanowy w podsumowaniu
# wygląda identycznie jak zielony; a scenariusz, którego nie udało się uruchomić, trafia na jawną
# listę NIE_URUCHOMIONO i NIE jest liczony jako przejście.
#
# Werdykt czyta się z linii „== wynik: PASSED …” wypisanej przez run_db_tests.sh, nigdy z kodu
# wyjścia: `make build` przez wiele tygodni budował połowę obrazów i kończył się zerem.
#
# Artefakt: artifacts/p0/<UTC>/raport.json i raport.md, plus pełne logi każdego przebiegu.
# Komunikaty dla operatora po polsku (D-010).
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

RUNNER="$ROOT/tools/dev/run_db_tests.sh"
if [ ! -f "$RUNNER" ]; then
  echo "BŁĄD: nie ma $RUNNER, a bez niego nie ma jak uruchomić testów bazodanowych." >&2
  exit 3
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p0/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

HEAD_SHA="$(cd "$ROOT" && git rev-parse HEAD 2>/dev/null || echo "nieznany")"
BRANCH="$(cd "$ROOT" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "nieznana")"
(cd "$ROOT" && git status --short 2>/dev/null) > "$OUT_DIR/git-status.txt"

echo "== regresja P0: HEAD $HEAD_SHA ($BRANCH), artefakt $OUT_DIR =="

# -----------------------------------------------------------------------------
# Scenariusze.
#
# Pola: id | pakiet | wyrażenie -k | opis.
#
# `-k` dopasowuje się do CAŁEGO identyfikatora testu, więc nazwa modułu wybiera cały plik, a
# nazwa testu pojedynczy przypadek. Marker `p0` zawęża do testów, które ktoś świadomie przypisał
# do tego zestawu; samo `-k` po nazwie pliku brałoby też testy dopisane tam później w innej
# sprawie. Testy spoza markera są DESELECTED, nie SKIPPED, więc nie wywracają reguły o pominięciu.
#
# Jeden scenariusz może mieć kilka linii (kilka pakietów albo kilka wyborów).
# -----------------------------------------------------------------------------
SCENARIOS=$(cat <<'SPEC'
restart_w_czasie_zadania|services/control_plane|test_restart_recovery|Zabity worker, job wstawiony przy wyłączonym dispatcherze, pętla przeżywająca wyjątek handlera i ticku
utrata_lease_i_zombie|services/control_plane|test_job_lease_fencing or test_reaper_single_transaction|Ogradzanie zapisu po przejęciu lease'u i przejęcie wygasłego lease'u w jednej transakcji
wolna_baza|services/control_plane|test_db_stall_no_second_side_effect or heartbeat|Heartbeat wolniejszy niż interwał, ale w TTL, trzyma lease; zawieszenie bazy ponad TTL gubi joba czytelnie i bez drugiego efektu ubocznego
niejednoznaczny_submit|services/control_plane|test_no_retry_after_submit_started or test_pilot_publisher or test_pilot_publication|Po SUBMIT_STARTED nie ma automatycznego ponowienia; rekoncyliacja i ponowienie tylko jawne i najwyżej raz
usunieta_tresc_nie_jest_sukcesem|services/control_plane|test_publication_visibility|Widoczność publiczna liczona z odczytu anonimowego i skrzynki; odmowa Reddita to NIESPRAWDZONE, nie usunięcie
usunieta_tresc_nie_jest_sukcesem|services/db_schema|test_publication_visibility_schema|Migracja 0012: listy zamknięte egzekwowane, domyślnie UNVERIFIED i AUTHOR, werdykt bez momentu pomiaru odrzucony
SPEC
)

RESULTS="$OUT_DIR/kroki.tsv"
: > "$RESULTS"
MIGRATIONS="$OUT_DIR/migracje.txt"
: > "$MIGRATIONS"

step_no=0
while IFS='|' read -r scenario package selector description; do
  [ -n "${scenario:-}" ] || continue
  step_no=$((step_no + 1))
  log="$OUT_DIR/krok-${step_no}-${scenario}.log"
  echo "-- [$step_no] $scenario ($package): $selector"

  REPO_ROOT="$ROOT" bash "$RUNNER" "$package" -m p0 -k "$selector" > "$log" 2>&1
  exit_code=$?

  # Werdykt z linii wyniku, nie z kodu wyjścia. Brak tej linii = przebieg NIE ODBYŁ SIĘ.
  verdict_line="$(grep -m1 '^== wynik: PASSED ' "$log" || true)"
  if [ -z "$verdict_line" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$scenario" "$package" "$selector" "NIE_URUCHOMIONO" "0" "0" "0" "$exit_code" >> "$RESULTS"
    echo "   NIE_URUCHOMIONO (kod $exit_code), szczegóły w $(basename "$log")"
    continue
  fi

  passed="$(echo "$verdict_line" | sed -E 's/.*PASSED ([0-9]+).*/\1/')"
  failed="$(echo "$verdict_line" | sed -E 's/.*FAILED ([0-9]+).*/\1/')"
  errors="$(echo "$verdict_line" | sed -E 's/.*ERRORS ([0-9]+).*/\1/')"
  skipped="$(echo "$verdict_line" | sed -E 's/.*SKIPPED ([0-9]+).*/\1/')"

  if [ "$passed" -gt 0 ] && [ "$failed" -eq 0 ] && [ "$errors" -eq 0 ] && [ "$skipped" -eq 0 ]; then
    state="ZALICZONY"
  else
    state="PORAZKA"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$scenario" "$package" "$selector" "$state" "$passed" "$failed" "$errors" "$skipped" \
    >> "$RESULTS"
  echo "   $state: PASSED $passed, FAILED $failed, ERRORS $errors, SKIPPED $skipped"

  # Migracje zastosowane w kontenerze testowym tego kroku, prosto z wypisu apply_migrations.sh.
  grep -E '^   OK: [0-9]+_.*\.sql$' "$log" | sed -E 's/^   OK: //' | sort -u >> "$MIGRATIONS"
done <<< "$SCENARIOS"

sort -u "$MIGRATIONS" -o "$MIGRATIONS"

# -----------------------------------------------------------------------------
# Raport. Składa go Python, bo to on wykonał odczyt wyników i on ma prawo je ogłosić.
# -----------------------------------------------------------------------------
OUT_DIR="$OUT_DIR" HEAD_SHA="$HEAD_SHA" BRANCH="$BRANCH" STAMP="$STAMP" python3 - <<'PY'
import json
import os
import pathlib

out = pathlib.Path(os.environ["OUT_DIR"])
steps = []
for line in (out / "kroki.tsv").read_text("utf-8").splitlines():
    if not line.strip():
        continue
    scenario, package, selector, state, passed, failed, errors, skipped = line.split("\t")
    steps.append(
        {
            "scenariusz": scenario,
            "pakiet": package,
            "wybor": selector,
            "stan": state,
            "passed": int(passed),
            "failed": int(failed),
            "errors": int(errors),
            "skipped": int(skipped),
        }
    )

# Scenariusz jest zaliczony, gdy zaliczone są WSZYSTKIE jego kroki. Wymagana jest też liczność:
# `all([])` to `True`, i dwa z sześciu fałszywych zaliczeń w tym repozytorium były dokładnie tym.
scenarios = {}
for step in steps:
    scenarios.setdefault(step["scenariusz"], []).append(step)

summary = []
for name, group in scenarios.items():
    if not group:
        state = "NIE_URUCHOMIONO"
    elif any(s["stan"] == "NIE_URUCHOMIONO" for s in group):
        state = "NIE_URUCHOMIONO"
    elif all(s["stan"] == "ZALICZONY" for s in group):
        state = "ZALICZONY"
    else:
        state = "PORAZKA"
    summary.append(
        {
            "scenariusz": name,
            "stan": state,
            "kroki": len(group),
            "passed": sum(s["passed"] for s in group),
            "failed": sum(s["failed"] for s in group),
            "errors": sum(s["errors"] for s in group),
            "skipped": sum(s["skipped"] for s in group),
        }
    )

EXPECTED = [
    "restart_w_czasie_zadania",
    "utrata_lease_i_zombie",
    "wolna_baza",
    "niejednoznaczny_submit",
    "usunieta_tresc_nie_jest_sukcesem",
]
not_run = [name for name in EXPECTED if name not in scenarios]
not_run += [s["scenariusz"] for s in summary if s["stan"] == "NIE_URUCHOMIONO"]

if not summary or len(scenarios) != len(EXPECTED):
    verdict = "NIE_URUCHOMIONO"
elif not_run:
    verdict = "NIE_URUCHOMIONO"
elif any(s["stan"] != "ZALICZONY" for s in summary):
    verdict = "PORAZKA"
else:
    verdict = "ZALICZONA"

# Najnowszy wynik testu odtworzenia, jeżeli jakiś jest. Świadomie NIE uruchamiamy go stąd:
# tamten czyta bazę live, a ten zestaw ma biec także tam, gdzie stacku live nie ma. Raport podaje
# więc CUDZY pomiar z jego własną datą, albo mówi wprost, że go nie było.
restore = None
for candidate in sorted((out.parent).glob("*/restore.json"), reverse=True):
    try:
        restore = json.loads(candidate.read_text("utf-8"))
        restore["plik"] = str(candidate.relative_to(out.parent.parent))
        break
    except (OSError, ValueError):
        continue

dirty = [
    line for line in (out / "git-status.txt").read_text("utf-8").splitlines() if line.strip()
]
migrations = [
    line for line in (out / "migracje.txt").read_text("utf-8").splitlines() if line.strip()
]

report = {
    "kamien": "P0",
    "wykonano_utc": os.environ["STAMP"],
    "head": os.environ["HEAD_SHA"],
    "galaz": os.environ["BRANCH"],
    "werdykt": verdict,
    "zmienione_pliki_robocze": dirty,
    "migracje_w_kontenerze_testowym": migrations,
    "scenariusze": summary,
    "kroki": steps,
    "nie_uruchomiono": sorted(set(not_run)),
    "test_odtworzenia": restore,
}
(out / "raport.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

SCENARIO_TITLES = {
    "restart_w_czasie_zadania": "Restart w czasie zadania",
    "utrata_lease_i_zombie": "Utrata lease'u i zombie",
    "wolna_baza": "Wolna baza",
    "niejednoznaczny_submit": "Niejednoznaczny submit",
    "usunieta_tresc_nie_jest_sukcesem": "Usunięta treść nie jest publicznym sukcesem",
}

lines = [
    "# Regresja P0",
    "",
    f"Wykonano: {os.environ['STAMP']} (UTC)",
    f"HEAD: `{os.environ['HEAD_SHA']}`, gałąź `{os.environ['BRANCH']}`",
    f"Werdykt: **{verdict}**",
    "",
    "## Scenariusze",
    "",
    "| Scenariusz | Stan | PASSED | FAILED | ERRORS | SKIPPED |",
    "| --- | --- | ---: | ---: | ---: | ---: |",
]
for name in EXPECTED:
    row = next((s for s in summary if s["scenariusz"] == name), None)
    title = SCENARIO_TITLES.get(name, name)
    if row is None:
        lines.append(f"| {title} | NIE_URUCHOMIONO | 0 | 0 | 0 | 0 |")
        continue
    lines.append(
        f"| {title} | {row['stan']} | {row['passed']} | {row['failed']} | "
        f"{row['errors']} | {row['skipped']} |"
    )

lines += ["", "## Test migracji i odtworzenia", ""]
if restore is None:
    lines += [
        "NIE_URUCHOMIONO. Ten zestaw go nie uruchamia, bo tamten czyta bazę live.",
        "Uruchom osobno: `make p0-restore-check`.",
    ]
else:
    lines += [
        f"Werdykt: **{restore.get('werdykt', '?')}** (pomiar {restore.get('wykonano_utc', '?')},",
        f"artefakt `{restore.get('plik', '?')}`).",
        "",
        "| Krok | Stan | Uwaga |",
        "| --- | --- | --- |",
    ]
    for step in restore.get("kroki", []):
        note = (step.get("uwaga") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {step.get('krok')} | {step.get('stan')} | {note} |")

lines += ["", "## Czego nie uruchomiono", ""]
if not_run:
    lines += [f"- {name}" for name in sorted(set(not_run))]
else:
    lines.append("Wszystkie pięć scenariuszy zostało uruchomionych.")

lines += [
    "",
    "## Czego ten zestaw NIE dowodzi",
    "",
    "- Nie jest pomiarem na żywym stacku ani na Reddicie. Wszystko biegnie na jednorazowym",
    "  kontenerze PostgreSQL, a platformę zastępują scenariuszowe adaptery.",
    "- Widoczność publiczna jest sprawdzona na fixture. Odczyt anonimowy i skrzynka konta mają",
    "  kod i testy jednostkowe transportu, ale nie zostały uruchomione na kapsule.",
    "- Sam nie mierzy odtworzenia bazy: cytuje wynik `make p0-restore-check` z jego własną datą.",
    "",
    "## Migracje zastosowane w kontenerze testowym",
    "",
]
lines += [f"- {name}" for name in migrations] or ["- (żadnych, co samo w sobie jest usterką)"]

lines += ["", "## Zmienione pliki robocze w momencie pomiaru", ""]
if dirty:
    lines.append(f"Plików: {len(dirty)}. Pełna lista: `git-status.txt` obok tego raportu.")
else:
    lines.append("Drzewo robocze czyste.")

lines += [
    "",
    "## Kroki",
    "",
    "| # | Scenariusz | Pakiet | Wybór `-k` | Stan | PASSED | FAILED | ERRORS | SKIPPED |",
    "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
]
for n, step in enumerate(steps, start=1):
    lines.append(
        f"| {n} | {step['scenariusz']} | `{step['pakiet']}` | `{step['wybor']}` | "
        f"{step['stan']} | {step['passed']} | {step['failed']} | {step['errors']} | "
        f"{step['skipped']} |"
    )
lines += ["", "Pełne logi każdego kroku leżą obok, w plikach `krok-*.log`.", ""]

(out / "raport.md").write_text("\n".join(lines), encoding="utf-8")

print()
print(f"== werdykt regresji P0: {verdict} ==")
for name in EXPECTED:
    row = next((s for s in summary if s["scenariusz"] == name), None)
    if row is None:
        print(f"  - {name}: NIE_URUCHOMIONO")
    else:
        print(
            f"  - {name}: {row['stan']} (PASSED {row['passed']}, FAILED {row['failed']}, "
            f"ERRORS {row['errors']}, SKIPPED {row['skipped']})"
        )
print(f"== raport: {out}/raport.md ==")
raise SystemExit(0 if verdict == "ZALICZONA" else 1)
PY
exit $?
