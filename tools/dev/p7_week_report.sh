#!/usr/bin/env bash
# Raport tygodnia gotowości operacyjnej (P7 etap B).
#
#   make p7-week-report
#   WENEXT_LIVE_PG_CONTAINER=inny make p7-week-report
#
# NA JAKIE PYTANIE ODPOWIADA. Czy usługa działa niezależnie od laptopa autora. Cztery liczby
# rozstrzygają i żadna nie zastępuje pozostałych: ile chodziła, ile razy trzeba było ją popchnąć
# ręką, ile kosztowała jedna POTWIERDZONA publikacja i jak często nie wiemy, co się stało.
#
# PROGI SĄ W tools/gonogo/criteria-p7.yaml I ZOSTAŁY ZAMROŻONE PRZED PIERWSZYM POMIAREM. Ten
# skrypt ich nie zna: czyta je z pliku i porównuje. Próg dobrany po zobaczeniu wyniku nie jest
# progiem, tylko opisem tego, co wyszło.
#
# OKNO KRÓTSZE NIŻ SIEDEM DÓB TO NIE_URUCHOMIONO, nie „wynik wstępny". Pięć spokojnych dób
# wygląda jak stuprocentowa dostępność; tydzień z awarią w szóstej dobie wygląda inaczej i to ta
# druga liczba jest prawdą o usłudze.
#
# BAZA JEST TYLKO CZYTANA.
#
# Artefakt: artifacts/p7/week/<UTC>/{tydzien.json,raport.md}
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/infra/docker-config}"

LIVE_CONTAINER="${WENEXT_LIVE_PG_CONTAINER:-wenext-db-1}"
PG_USER="${POSTGRES_USER:-wenext}"
PG_DB="${POSTGRES_DB:-wenext}"
CRITERIA="${WENEXT_P7_CRITERIA:-$ROOT/tools/gonogo/criteria-p7.yaml}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/artifacts/p7/week/$STAMP"
mkdir -p "$OUT_DIR" || { echo "BŁĄD: nie mogę utworzyć $OUT_DIR" >&2; exit 3; }

echo "== raport tygodnia P7: baza $LIVE_CONTAINER (tylko odczyt), artefakt $OUT_DIR =="

[ -f "$CRITERIA" ] || { echo "NIE_URUCHOMIONO: nie ma pliku progów $CRITERIA" >&2; exit 3; }

if ! docker exec "$LIVE_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    echo "NIE_URUCHOMIONO: kontener $LIVE_CONTAINER nie odpowiada na pg_isready." >&2
    exit 3
fi

HAS_SCHEMA="$(docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables
      WHERE table_name IN ('service_heartbeats','incidents','cost_ledger')" \
    2>/dev/null | tr -d '[:space:]')"
if [ "${HAS_SCHEMA:-0}" != "3" ]; then
    echo "NIE_URUCHOMIONO: ta baza nie ma tabel z migracji 0020." >&2
    echo "Zero pomiarów na bazie bez schematu P7 to nie jest spokojny tydzień." >&2
    exit 3
fi

query() { docker exec "$LIVE_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAF'|' -c "$1" 2>/dev/null; }

# Jak długie jest okno, którym W OGÓLE dysponujemy. Liczone od najstarszego dowodu pracy, bo to
# on wyznacza, od kiedy cokolwiek mierzyliśmy.
OLDEST="$(query "SELECT coalesce(extract(epoch FROM now() - min(interval_start)) / 86400.0, 0)
                   FROM service_heartbeats" | tr -d '[:space:]')"

AVAILABILITY="$(query "
SELECT h.component,
       count(*),
       CASE WHEN count(*) = 0 THEN 0
            ELSE count(*)::float / greatest(1, floor(extract(epoch FROM interval '7 days') / 300))
       END
  FROM service_heartbeats h
 WHERE h.interval_start >= now() - interval '7 days'
 GROUP BY h.component ORDER BY h.component")"

INCIDENTS="$(query "
SELECT component, detected_by, to_char(started_at, 'YYYY-MM-DD HH24:MI'),
       coalesce(to_char(ended_at, 'YYYY-MM-DD HH24:MI'), 'trwa'),
       round(extract(epoch FROM coalesce(ended_at, now()) - started_at) / 60.0)
  FROM incidents
 WHERE ended_at IS NULL OR ended_at >= now() - interval '7 days'
 ORDER BY started_at DESC")"

HUMAN="$(query "SELECT count(*) FROM incidents
                 WHERE detected_by = 'HUMAN'
                   AND (ended_at IS NULL OR ended_at >= now() - interval '7 days')" | tr -d '[:space:]')"

COST="$(query "
SELECT kind, coalesce(sum(units), 0)
  FROM cost_ledger WHERE occurred_at >= now() - interval '7 days'
 GROUP BY kind ORDER BY kind")"

PUBLISHED="$(query "SELECT count(*) FROM publication_requests
                     WHERE published_at >= now() - interval '7 days'" | tr -d '[:space:]')"
CONFIRMED="$(query "SELECT count(*) FROM publication_requests
                     WHERE published_at >= now() - interval '7 days'
                       AND public_visibility = 'CONFIRMED'" | tr -d '[:space:]')"
UNVERIFIED="$(query "SELECT count(*) FROM publication_requests
                      WHERE published_at >= now() - interval '7 days'
                        AND public_visibility = 'UNVERIFIED'" | tr -d '[:space:]')"
WAKEUPS="$(query "SELECT count(*) FROM wakeups WHERE created_at >= now() - interval '7 days'" | tr -d '[:space:]')"
WAKEUPS_RAN="$(query "SELECT count(*) FROM wakeups
                       WHERE created_at >= now() - interval '7 days' AND session_id IS NOT NULL" | tr -d '[:space:]')"

python3 - "$OUT_DIR" "$CRITERIA" "${OLDEST:-0}" "${HUMAN:-0}" "${PUBLISHED:-0}" \
    "${CONFIRMED:-0}" "${UNVERIFIED:-0}" "${WAKEUPS:-0}" "${WAKEUPS_RAN:-0}" \
    "$AVAILABILITY" "$INCIDENTS" "$COST" <<'PYEOF'
import json
import pathlib
import sys

import yaml

(
    out_dir, criteria_path, oldest, human, published, confirmed,
    unverified, wakeups, wakeups_ran, availability_raw, incidents_raw, cost_raw,
) = sys.argv[1:13]

criteria = yaml.safe_load(pathlib.Path(criteria_path).read_text("utf-8"))
thresholds = criteria["progi"]
window_days = float(oldest or 0)


def rows(blob, arity):
    out = []
    for line in (blob or "").splitlines():
        if "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= arity:
            out.append(parts[:arity])
    return out


availability = {name: (int(beats), float(share)) for name, beats, share in rows(availability_raw, 3)}
incidents = rows(incidents_raw, 5)
cost = {kind: float(units) for kind, units in rows(cost_raw, 2)}

published, confirmed, unverified = int(published), int(confirmed), int(unverified)
model_turns = cost.get("MODEL_TURN", 0.0)

# --- cztery liczby, każda przeciw swojemu progowi -------------------------------------------
measured = {}
worst_availability = min((share for _, share in availability.values()), default=0.0)
measured["dostepnosc"] = {
    "wartosc": round(worst_availability, 4),
    "prog": thresholds["dostepnosc"]["prog"],
    "spelniony": worst_availability >= thresholds["dostepnosc"]["prog"],
    "szczegol": {name: round(share, 4) for name, (_, share) in availability.items()},
}
measured["interwencje_czlowieka"] = {
    "wartosc": int(human),
    "prog": thresholds["interwencje_czlowieka"]["prog"],
    "spelniony": int(human) <= thresholds["interwencje_czlowieka"]["prog"],
}
# Mianownikiem są publikacje POTWIERDZONE. Zero potwierdzonych nie daje nieskończoności ani zera:
# daje BRAK POMIARU, bo dzielenie przez zero to nie jest wynik.
if confirmed > 0:
    per_publication = model_turns / confirmed
    measured["koszt_na_potwierdzona_publikacje"] = {
        "wartosc": round(per_publication, 2),
        "prog": thresholds["koszt_na_potwierdzona_publikacje"]["prog"],
        "spelniony": per_publication <= thresholds["koszt_na_potwierdzona_publikacje"]["prog"],
    }
else:
    measured["koszt_na_potwierdzona_publikacje"] = {
        "wartosc": None,
        "prog": thresholds["koszt_na_potwierdzona_publikacje"]["prog"],
        "spelniony": None,
        "powod": "ani jedna publikacja nie ma potwierdzonej widoczności, więc nie ma mianownika",
    }
share_unverified = (unverified / published) if published else None
measured["niepewne_skutki"] = {
    "wartosc": round(share_unverified, 4) if share_unverified is not None else None,
    "prog": thresholds["niepewne_skutki"]["prog"],
    "spelniony": (share_unverified <= thresholds["niepewne_skutki"]["prog"])
    if share_unverified is not None
    else None,
    **({} if published else {"powod": "w tym tygodniu nic nie opublikowano"}),
}

# --- werdykt ---------------------------------------------------------------------------------
if window_days < criteria["okno_dob"]:
    verdict = criteria["krotsze_okno"]
    reason = (
        f"okno pomiaru ma {window_days:.1f} doby, a tydzień to {criteria['okno_dob']}. "
        "Krótszy test nie jest tygodniem"
    )
elif any(one["spelniony"] is None for one in measured.values()):
    verdict = "NIE_URUCHOMIONO"
    missing = [name for name, one in measured.items() if one["spelniony"] is None]
    reason = f"nie da się ocenić progów: {', '.join(missing)}"
else:
    failed = [name for name, one in measured.items() if not one["spelniony"]]
    verdict = "GO" if not failed else "NO_GO"
    reason = "wszystkie cztery progi spełnione" if not failed else f"progi niespełnione: {', '.join(failed)}"

report = {
    "werdykt": verdict,
    "powod": reason,
    "okno_dob": round(window_days, 2),
    "progi_zamrozone_dnia": criteria["zamrozone_dnia"],
    "pomiary": measured,
    "surowe": {
        "puls": {name: beats for name, (beats, _) in availability.items()},
        "incydenty": [
            {"komponent": c, "zauwazyl": d, "od": s, "do": e, "minut": int(float(m))}
            for c, d, s, e, m in incidents
        ],
        "koszt": cost,
        "publikacje": {
            "opublikowane": published,
            "potwierdzone": confirmed,
            "niepotwierdzone": unverified,
        },
        "obudzenia": {"wszystkie": int(wakeups), "z_modelem": int(wakeups_ran)},
    },
}
pathlib.Path(out_dir, "tydzien.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)

lines = [
    "# Tydzień usługi",
    "",
    f"Werdykt: **{verdict}**. {reason}.",
    f"Progi zamrożone {criteria['zamrozone_dnia']}, okno pomiaru {window_days:.1f} doby.",
    "",
    "| miara | wynik | próg | spełniony |",
    "|---|---|---|---|",
]
for name, one in measured.items():
    value = one["wartosc"] if one["wartosc"] is not None else one.get("powod", "brak pomiaru")
    ok = {True: "tak", False: "NIE", None: "brak pomiaru"}[one["spelniony"]]
    lines.append(f"| {name} | {value} | {one['prog']} | {ok} |")
lines += ["", "## Incydenty", ""]
if incidents:
    for component, detected, start, end, minutes in incidents:
        lines.append(f"- {component}: od {start} do {end} ({int(float(minutes))} min), zauważył {detected}")
else:
    lines.append("Ani jednego incydentu w tym oknie.")
lines += [
    "",
    "## Co się działo",
    "",
    f"- obudzenia: {wakeups}, z tego z sesją modelu: {wakeups_ran}",
    f"- publikacje: {published}, potwierdzone publicznie: {confirmed}, niepotwierdzone: {unverified}",
    f"- koszt w jednostkach: {cost or 'brak wpisów'}",
    "",
]
pathlib.Path(out_dir, "raport.md").write_text("\n".join(lines), encoding="utf-8")

print(f"== werdykt tygodnia: {verdict} ==")
print(f"   {reason}")
print(f"== raport: {out_dir}/raport.md ==")
sys.exit(0 if verdict == "GO" else (3 if verdict == "NIE_URUCHOMIONO" else 1))
PYEOF
