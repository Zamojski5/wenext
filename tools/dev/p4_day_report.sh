#!/usr/bin/env bash
# Raport dnia P4 (docs/ROADMAP-PIVOT.md: „po powrocie widzę wyniki").
#
#   make p4-day-report
#   REPO_ROOT=$PWD bash tools/dev/p4_day_report.sh
#   WENEXT_REPORT_HOURS=48 make p4-day-report
#
# NA JAKIE PYTANIE TO ODPOWIADA. Człowiek zamknął laptop wieczorem i wraca rano. Chce wiedzieć
# trzy rzeczy w tej kolejności: czy agent w ogóle działał, co zrobił, i ile to kosztowało.
# Raport jest ułożony dokładnie tak i NIE jest listą tabel: zaczyna się od zdania o każdym
# projekcie, a liczby są pod nim.
#
# DLACZEGO CISZA TEŻ JEST WYNIKIEM. Tik bez zdarzeń zapisuje wiersz w `wakeups` z powodem
# pominięcia, więc „nic się nie działo" i „kolektor stanął" wyglądają tu inaczej. Raport, który
# pokazywałby tylko drogie tiki, nie umiałby odróżnić spokojnej nocy od zepsutej.
#
# BAZA JEST TYLKO CZYTANA. Wyłącznie SELECT-y przez `docker exec` na kontenerze wenext-db-1.
# Ten skrypt nie stawia stacku, nie zatrzymuje go i nie pisze do niego ani jednego wiersza.
#
# REGUŁA O DOWODZIE (docs/KONWENCJE.md, D-007): brak bazy to NIE_URUCHOMIONO i kod wyjścia 3,
# nie pusty raport. Raport o zerowej liczbie projektów wygląda identycznie jak raport, którego
# nie udało się zrobić, i to jest dokładnie ta pomyłka, której ta reguła zabrania.
#
# Artefakt: artifacts/p4/<UTC>/raport.md i dzien.json.
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

LIVE_CONTAINER="${WENEXT_LIVE_PG_CONTAINER:-wenext-db-1}"
PG_USER="${POSTGRES_USER:-wenext}"
PG_DB="${POSTGRES_DB:-wenext}"
HOURS="${WENEXT_REPORT_HOURS:-24}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p4/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

echo "== raport dnia P4: baza $LIVE_CONTAINER (tylko odczyt), okno ${HOURS}h, artefakt $OUT_DIR =="

if ! docker exec "$LIVE_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    echo "NIE_URUCHOMIONO: kontener $LIVE_CONTAINER nie odpowiada na pg_isready." >&2
    echo "To nie jest pusty raport, tylko brak pomiaru (D-007)." >&2
    exit 3
fi

# Czy schemat P4 w ogóle stoi na tej bazie. Bez tego raport pokazałby zero obudzeń na bazie,
# która po prostu nie ma jeszcze migracji 0017, i wyglądałoby to jak cicha noc.
HAS_SCHEMA="$(docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('wakeups','project_delegations')" \
    2>/dev/null | tr -d '[:space:]')"
if [ "${HAS_SCHEMA:-0}" != "2" ]; then
    echo "NIE_URUCHOMIONO: ta baza nie ma tabel z migracji 0017 (wakeups, project_delegations)." >&2
    echo "Zero obudzeń na bazie bez schematu P4 to nie jest ciche popołudnie." >&2
    exit 3
fi

# Odmiana przez liczbę, bo raport czyta człowiek, a „pracował 1 razy" zdradza, że zdanie
# skleiła maszyna, i każe czytelnikowi przestać ufać reszcie.
times() { [ "$1" = "1" ] && echo "raz" || echo "$1 razy"; }
texts() {
    case "$1" in
        1) echo "1 tekst" ;;
        2|3|4|22|23|24|32|33|34) echo "$1 teksty" ;;
        *) echo "$1 tekstów" ;;
    esac
}

query() { docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAF'|' -c "$1" 2>/dev/null; }

WINDOW="now() - interval '$HOURS hours'"

PROJECTS="$(query "
SELECT p.id, p.name, coalesce(d.mode, '-'),
       CASE WHEN d.id IS NULL THEN 'niepowierzony'
            WHEN d.paused_at IS NOT NULL THEN 'wstrzymany'
            ELSE 'czuwa' END
  FROM projects p
  LEFT JOIN project_delegations d
    ON d.project_id = p.id AND d.revoked_at IS NULL
 WHERE p.archived_at IS NULL
 ORDER BY p.name")"

{
    echo "# Raport dnia: co agent robił przez ostatnie $HOURS h"
    echo
    echo "Wygenerowano $(date -u +%Y-%m-%dT%H:%M:%SZ) z bazy $LIVE_CONTAINER (tylko odczyt)."
    echo
} > "$OUT_DIR/raport.md"

if [ -z "$PROJECTS" ]; then
    echo "NIE_URUCHOMIONO: baza nie ma ani jednego projektu, więc nie ma o czym raportować." >&2
    exit 3
fi

TOTAL_PROJECTS=0
while IFS='|' read -r project_id project_name mode state; do
    [ -z "$project_id" ] && continue
    TOTAL_PROJECTS=$((TOTAL_PROJECTS + 1))

    WOKE="$(query "SELECT count(*) FROM wakeups WHERE project_id = '$project_id' AND created_at >= $WINDOW")"
    RAN="$(query "SELECT count(*) FROM wakeups WHERE project_id = '$project_id' AND created_at >= $WINDOW AND session_id IS NOT NULL")"
    QUIET="$(query "
SELECT coalesce(skipped_reason, 'W_TOKU') || ' x' || count(*)
  FROM wakeups
 WHERE project_id = '$project_id' AND created_at >= $WINDOW AND session_id IS NULL
 GROUP BY skipped_reason ORDER BY count(*) DESC")"
    PUBLISHED="$(query "SELECT count(*) FROM publication_requests WHERE project_id = '$project_id' AND published_at >= $WINDOW")"
    QUEUED="$(query "SELECT count(*) FROM publication_requests WHERE project_id = '$project_id' AND status = 'QUEUED'")"
    REMOVED="$(query "SELECT count(*) FROM publication_requests WHERE project_id = '$project_id' AND public_visibility = 'REMOVED'")"
    UNVERIFIED="$(query "SELECT count(*) FROM publication_requests WHERE project_id = '$project_id' AND public_visibility = 'UNVERIFIED' AND status = 'PUBLISHED'")"
    WAITING="$(query "
SELECT count(*) FROM action_proposal_revisions r
  JOIN action_proposals a ON a.id = r.proposal_id AND a.organization_id = r.organization_id
  JOIN project_account_assignments x ON x.social_account_id = a.social_account_id
   AND x.project_id = '$project_id' AND x.unassigned_at IS NULL
 WHERE r.state = 'READY_FOR_APPROVAL' AND r.expires_at > now()
   AND NOT EXISTS (SELECT 1 FROM publication_requests q WHERE q.proposal_revision_id = r.id)")"
    LAST="$(query "SELECT to_char(max(created_at), 'YYYY-MM-DD HH24:MI') FROM wakeups WHERE project_id = '$project_id'")"

    # Zdanie NAJPIERW, liczby pod nim. Kolejność jest treścią: człowiek czyta pierwszą linię
    # i wie, czy musi coś zrobić.
    if [ "$state" = "niepowierzony" ]; then
        HEADLINE="nie jest powierzony agentowi, więc nic nie robił"
    elif [ "$state" = "wstrzymany" ]; then
        HEADLINE="jest wstrzymany; agent nie ruszał"
    elif [ "${WOKE:-0}" = "0" ]; then
        HEADLINE="czuwał, ale ani razu się nie obudził przez ostatnie $HOURS h (ostatnio: ${LAST:-nigdy})"
    elif [ "${RAN:-0}" = "0" ]; then
        HEADLINE="obudził się $(times "$WOKE") i za każdym razem uznał, że nie ma co robić"
    else
        HEADLINE="obudził się $(times "$WOKE"), pracował $(times "$RAN"), opublikował $(texts "${PUBLISHED:-0}")"
    fi

    {
        echo "## $project_name"
        echo
        echo "$HEADLINE."
        echo
        echo "- tryb: $mode, stan: $state"
        echo "- obudzenia w oknie: ${WOKE:-0}, z tego z sesją modelu: ${RAN:-0}"
        if [ -n "$QUIET" ]; then
            echo "- ciche tiki po powodzie: $(echo "$QUIET" | paste -sd', ' -)"
        fi
        echo "- publikacje w oknie: ${PUBLISHED:-0}, w kolejce teraz: ${QUEUED:-0}"
        echo "- usunięte przez moderację: ${REMOVED:-0}, opublikowane bez potwierdzenia widoczności: ${UNVERIFIED:-0}"
        echo "- czeka na decyzję człowieka: ${WAITING:-0}"
        echo
    } >> "$OUT_DIR/raport.md"
done <<< "$PROJECTS"

python3 - "$OUT_DIR" "$HOURS" "$TOTAL_PROJECTS" <<'PYEOF' >> "$OUT_DIR/dzien.json"
import json, sys
out_dir, hours, projects = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
print(json.dumps({"okno_godzin": hours, "projektow": projects, "raport": f"{out_dir}/raport.md"},
                 ensure_ascii=False, indent=2))
PYEOF

echo "== projektów w raporcie: $TOTAL_PROJECTS =="
echo "== raport: $OUT_DIR/raport.md =="
