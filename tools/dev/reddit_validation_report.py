"""Raport walidacji jednego projektu na Reddicie (P4 i P5).

    make reddit-validation-report PILOT_ARGS="--project <uuid> --days 1"

NA JAKIE PYTANIE ODPOWIADA. Nie „czy agent działa", tylko „czy warto, żeby działał dalej w TYM
projekcie". Liczby są te same, co w widoku powrotu na ekranie: ten sam kod nie mógł być użyty,
bo widok czyta przez API z sesją operatora, a raport czyta bazę na żywo przez `docker exec`,
ale pytania i definicje są jedne, spisane w `tools/gonogo/criteria-p4.yaml`.

PROGI SĄ ZAMROŻONE PRZED POMIAREM i wczytywane z tamtego pliku, nigdy stąd. Gdyby ten skrypt
miał własną kopię progów, pierwsza rozbieżność między nimi byłaby niewidoczna, a raport
pokazywałby werdykt, którego nikt nie zamroził.

BAZA JEST TYLKO CZYTANA. Same SELECT-y przez `docker exec` na kontenerze bazy. Ten skrypt nie
stawia stacku, nie zatrzymuje go i nie pisze do niego ani jednego wiersza.

REGUŁA O DOWODZIE (D-007): brak bazy, brak schematu albo brak projektu to NIE_URUCHOMIONO
i kod wyjścia 3, nie pusty raport. Okno krótsze niż doba też nie dostaje werdyktu: trzy godziny
bez publikacji wyglądają jak spokój, a doba bez publikacji wygląda inaczej.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[2]))
CRITERIA = ROOT / "tools" / "gonogo" / "criteria-p4.yaml"

#: Tabele, bez których raport pokazałby zera na bazie, która po prostu nie ma migracji.
REQUIRED_TABLES = ("wakeups", "project_visits", "publication_requests", "incidents", "cost_ledger")

NOT_MEASURED = "NIE_URUCHOMIONO"


class Missing(Exception):
    """Brak pomiaru, nie zły wynik. Kończy się kodem 3 i zdaniem, czego zabrakło."""


@dataclass(frozen=True)
class Measurement:
    name: str
    question: str
    value: float | None
    unit: str
    threshold: float
    higher_is_better: bool
    detail: str

    def verdict(self, window_too_short: bool) -> str:
        if window_too_short:
            return NOT_MEASURED
        if self.value is None:
            return NOT_MEASURED
        passed = (
            self.value >= self.threshold if self.higher_is_better else self.value <= self.threshold
        )
        return "GO" if passed else "NO_GO"


def query(container: str, user: str, database: str, sql: str) -> list[list[str]]:
    """Jedno zapytanie przez `docker exec psql`. Pusty wynik to pusta lista, nie wyjątek."""
    completed = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-d", database, "-tAF|", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise Missing(
            f"zapytanie nie przeszło na kontenerze {container}: "
            f"{completed.stderr.strip()[:200] or 'bez komunikatu'}"
        )
    return [line.split("|") for line in completed.stdout.splitlines() if line.strip()]


def one(rows: list[list[str]], column: int = 0, default: str = "0") -> str:
    return rows[0][column] if rows and len(rows[0]) > column and rows[0][column] != "" else default


def ratio(good: int, total: int) -> float | None:
    """Ułamek albo None. ZERO W MIANOWNIKU TO NIE JEST ZERO PROCENT, tylko brak pomiaru."""
    return None if total == 0 else round(good / total, 4)


def load_thresholds() -> dict[str, Any]:
    """Progi z zamrożonego pliku. Bez pliku nie ma werdyktu, bo nie ma z czym porównywać."""
    if not CRITERIA.exists():
        raise Missing(f"nie ma pliku progów {CRITERIA}")
    text = CRITERIA.read_text(encoding="utf-8")
    try:
        # Zależność opcjonalna: bez PyYAML progi czyta awaryjny czytnik niżej.
        import yaml

        data = yaml.safe_load(text)
    except ImportError:
        data = _thresholds_without_yaml(text)
    if not isinstance(data, dict) or "progi" not in data:
        raise Missing(f"plik progów {CRITERIA} nie ma sekcji `progi`")
    return data


def _thresholds_without_yaml(text: str) -> dict[str, Any]:
    """Awaryjny czytnik dla hosta bez PyYAML: bierze tylko nazwy i liczby po `prog:`.

    Świadomie prymitywny i świadomie wąski. Raport ma działać na maszynie, na której nikt nie
    instalował zależności, a jedyne, czego potrzebuje z tego pliku, to pięć liczb i data
    zamrożenia. Gdyby kiedyś potrzebował więcej, lepiej wymagać PyYAML niż rozbudowywać to.
    """
    thresholds: dict[str, Any] = {"progi": {}}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith("zamrozone_dnia:"):
            thresholds["zamrozone_dnia"] = stripped.split(":", 1)[1].strip().strip('"')
        if line.startswith("okno_dob:"):
            thresholds["okno_dob"] = int(stripped.split(":", 1)[1].strip())
        if line.startswith("  ") and stripped.endswith(":") and not line.startswith("    "):
            current = stripped[:-1]
            thresholds["progi"][current] = {}
        if current and stripped.startswith("prog:"):
            thresholds["progi"][current]["prog"] = float(stripped.split(":", 1)[1].strip())
    return thresholds


def measure(container: str, user: str, database: str, project: str, hours: int) -> dict[str, Any]:
    window = f"now() - interval '{hours} hours'"

    project_rows = query(
        container,
        user,
        database,
        f"SELECT name, organization_id FROM projects WHERE id = '{project}'",
    )
    if not project_rows:
        raise Missing(f"nie ma projektu {project} w tej bazie")
    name, organization = project_rows[0][0], project_rows[0][1]

    # 1. Puste cykle bez modelu. `cost IS NULL` znaczy „tura modelu nie ruszyła".
    skipped = query(
        container,
        user,
        database,
        f"""SELECT count(*) FILTER (WHERE cost IS NULL), count(*)
            FROM wakeups
            WHERE project_id = '{project}' AND skipped_reason IS NOT NULL
              AND created_at > {window}""",
    )
    free_skips = int(one(skipped, 0))
    all_skips = int(one(skipped, 1))

    # 2. Tury modelu na POTWIERDZONĄ publikację.
    turns = int(
        one(
            query(
                container,
                user,
                database,
                f"""SELECT coalesce(sum(units), 0) FROM cost_ledger
                    WHERE project_id = '{project}' AND kind = 'MODEL_TURN'
                      AND occurred_at > {window}""",
            )
        ).split(".")[0]
    )
    publications = query(
        container,
        user,
        database,
        f"""SELECT count(*) FILTER (WHERE public_visibility = 'CONFIRMED'), count(*)
            FROM publication_requests
            WHERE project_id = '{project}' AND queued_at > {window}""",
    )
    confirmed = int(one(publications, 0))
    all_publications = int(one(publications, 1))

    # 3. Interwencje człowieka: incydenty zgłoszone ręką plus korekty po powrocie.
    human = int(
        one(
            query(
                container,
                user,
                database,
                f"""SELECT count(*) FROM incidents
                    WHERE organization_id = '{organization}' AND detected_by = 'HUMAN'
                      AND started_at > {window}""",
            )
        )
    )
    corrections_sent = int(
        one(
            query(
                container,
                user,
                database,
                f"""SELECT count(*) FROM audit_events
                    WHERE kind = 'PROJECT_RETURN_CORRECTION'
                      AND subject_ref = 'project:{project}' AND occurred_at > {window}""",
            )
        )
    )

    # 4. Czy korekty docierają przed oczy agenta.
    reach = query(
        container,
        user,
        database,
        f"""SELECT count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM agent_sessions s
                    JOIN session_events e ON e.session_id = s.id
                    WHERE s.project_id = k.project_id AND s.created_at > k.created_at
                      AND e.kind = 'TOOL_CALL'
                      AND e.payload ->> 'tool' IN ('memory.list', 'memory.search'))),
                   count(*)
            FROM knowledge_items k
            WHERE k.project_id = '{project}' AND k.kind = 'PREFERENCE'
              AND k.stated_by = 'USER' AND k.retracted_at IS NULL
              AND k.created_at > {window}""",
    )
    reached = int(one(reach, 0))
    all_corrections = int(one(reach, 1))

    # Kontekst, nie próg: ile razy kolektor w ogóle pracował i ile sesji ruszyło.
    wakeups_total = int(
        one(
            query(
                container,
                user,
                database,
                f"SELECT count(*) FROM wakeups WHERE project_id = '{project}' "
                f"AND created_at > {window}",
            )
        )
    )
    sessions_total = int(
        one(
            query(
                container,
                user,
                database,
                f"SELECT count(*) FROM agent_sessions WHERE project_id = '{project}' "
                f"AND created_at > {window}",
            )
        )
    )

    return {
        "projekt": {"id": project, "nazwa": name, "organizacja": organization},
        "okno_godzin": hours,
        "kontekst": {
            "obudzen": wakeups_total,
            "sesji": sessions_total,
            "publikacji": all_publications,
            "korekt": all_corrections,
        },
        "surowe": {
            "puste_cykle_bez_modelu": free_skips,
            "puste_cykle": all_skips,
            "tury_modelu": turns,
            "publikacje_potwierdzone": confirmed,
            "incydenty_reka": human,
            "korekty_wyslane": corrections_sent,
            "korekty_w_zasiegu": reached,
        },
    }


def measurements(raw: dict[str, Any], thresholds: dict[str, Any]) -> list[Measurement]:
    thresholds_by_name = thresholds["progi"]
    counts = raw["surowe"]
    hours = raw["okno_godzin"]
    return [
        Measurement(
            name="obudzenia_bez_modelu",
            question="Jaki odsetek pustych cykli obszedł się bez tury modelu?",
            value=ratio(counts["puste_cykle_bez_modelu"], counts["puste_cykle"]),
            unit="ułamek pustych cykli",
            threshold=float(thresholds_by_name["obudzenia_bez_modelu"]["prog"]),
            higher_is_better=True,
            detail=f"{counts['puste_cykle_bez_modelu']} z {counts['puste_cykle']} pustych cykli",
        ),
        Measurement(
            name="koszt_na_potwierdzona_publikacje",
            question="Ile tur modelu kosztowała jedna POTWIERDZONA publikacja?",
            value=(
                None
                if counts["publikacje_potwierdzone"] == 0
                else round(counts["tury_modelu"] / counts["publikacje_potwierdzone"], 2)
            ),
            unit="tur modelu",
            threshold=float(thresholds_by_name["koszt_na_potwierdzona_publikacje"]["prog"]),
            higher_is_better=False,
            detail=(
                f"{counts['tury_modelu']} tur, {counts['publikacje_potwierdzone']} potwierdzonych "
                "publikacji"
            ),
        ),
        Measurement(
            name="potwierdzone_widocznosci",
            question="Jaki odsetek publikacji ma potwierdzoną widoczność?",
            value=ratio(counts["publikacje_potwierdzone"], raw["kontekst"]["publikacji"]),
            unit="ułamek publikacji",
            threshold=float(thresholds_by_name["potwierdzone_widocznosci"]["prog"]),
            higher_is_better=True,
            detail=(
                f"{counts['publikacje_potwierdzone']} z {raw['kontekst']['publikacji']} publikacji"
            ),
        ),
        Measurement(
            name="interwencje_czlowieka",
            question="Ile razy człowiek musiał wejść ręką?",
            value=round(
                (counts["incydenty_reka"] + counts["korekty_wyslane"]) / max(1, hours / 24), 2
            ),
            unit="zdarzeń na dobę",
            threshold=float(thresholds_by_name["interwencje_czlowieka"]["prog"]),
            higher_is_better=False,
            detail=(
                f"{counts['incydenty_reka']} incydentów zgłoszonych ręką, "
                f"{counts['korekty_wyslane']} korekt po powrocie"
            ),
        ),
        Measurement(
            name="korekty_docieraja",
            question="Po ilu korektach ruszyła sesja, która pytała o pamięć?",
            value=ratio(counts["korekty_w_zasiegu"], raw["kontekst"]["korekt"]),
            unit="ułamek korekt",
            threshold=float(thresholds_by_name["korekty_docieraja"]["prog"]),
            higher_is_better=True,
            detail=f"{counts['korekty_w_zasiegu']} z {raw['kontekst']['korekt']} korekt",
        ),
    ]


def render(raw: dict[str, Any], rows: list[Measurement], short: bool, frozen: str) -> str:
    lines = [
        f"# Walidacja projektu na Reddicie: {raw['projekt']['nazwa']}",
        "",
        f"Wygenerowano {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}, okno {raw['okno_godzin']} h, "
        f"baza czytana tylko do odczytu.",
        f"Progi zamrożone {frozen} w `tools/gonogo/criteria-p4.yaml`.",
        "",
    ]
    if short:
        lines += [
            f"## Werdykt: {NOT_MEASURED}",
            "",
            "Okno krótsze niż doba nie dostaje werdyktu. Trzy godziny bez publikacji wyglądają",
            "jak spokój, a doba bez publikacji wygląda inaczej. Liczby niżej są prawdziwe,",
            "ale nie są oceną.",
            "",
        ]
    else:
        verdicts = [row.verdict(short) for row in rows]
        overall = (
            NOT_MEASURED
            if NOT_MEASURED in verdicts
            else ("GO" if all(v == "GO" for v in verdicts) else "NO_GO")
        )
        lines += [f"## Werdykt: {overall}", ""]
        if overall == NOT_MEASURED:
            lines += [
                "Co najmniej jednej liczby nie dało się policzyć, bo nie było czego liczyć.",
                "Zero w mianowniku to brak pomiaru, nie wynik zerowy.",
                "",
            ]
    lines += [
        "## Liczby",
        "",
        "| miara | wynik | próg | werdykt | z czego |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        value = NOT_MEASURED if row.value is None else f"{row.value} {row.unit}"
        direction = "co najmniej" if row.higher_is_better else "najwyżej"
        lines.append(
            f"| {row.name} | {value} | {direction} {row.threshold} | "
            f"{row.verdict(short)} | {row.detail} |"
        )
    context = raw["kontekst"]
    lines += [
        "",
        "## Kontekst",
        "",
        f"- obudzeń: {context['obudzen']}"
        + (
            ""
            if context["obudzen"]
            else " (kolektor nie obudził agenta ani razu, to nie jest cisza, tylko brak pomiaru)"
        ),
        f"- sesji: {context['sesji']}",
        f"- publikacji: {context['publikacji']}",
        f"- korekt człowieka: {context['korekt']}",
        "",
        "## Czego ten raport NIE mówi",
        "",
        "- czy agent ZASTOSOWAŁ korektę. Widać, czy miał ją przed sobą, i tyle jest tu napisane;",
        "- czy publikacja była dobra. Widać, czy jest widoczna, nie czy ktoś ją docenił;",
        "- czy cisza była słuszna. Widać, ile razy kolektor pracował i ile to kosztowało.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Raport walidacji projektu na Reddicie")
    parser.add_argument("--project", required=True, help="identyfikator projektu")
    parser.add_argument("--days", type=float, default=1.0, help="okno w dobach, domyślnie 1")
    parser.add_argument(
        "--container", default=os.environ.get("WENEXT_LIVE_PG_CONTAINER", "wenext-db-1")
    )
    parser.add_argument("--user", default=os.environ.get("POSTGRES_USER", "wenext"))
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB", "wenext"))
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "validation"))
    args = parser.parse_args()

    hours = round(args.days * 24)
    if hours <= 0:
        print("BŁĄD: okno musi być dodatnie", file=sys.stderr)
        return 2

    try:
        thresholds = load_thresholds()
        ready = subprocess.run(
            ["docker", "exec", args.container, "pg_isready", "-U", args.user, "-d", args.database],
            capture_output=True,
            check=False,
        )
        if ready.returncode != 0:
            raise Missing(f"kontener {args.container} nie odpowiada na pg_isready")
        names = "', '".join(REQUIRED_TABLES)
        found = query(
            args.container,
            args.user,
            args.database,
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_schema = 'public' AND table_name IN ('{names}')",
        )
        if int(one(found)) != len(REQUIRED_TABLES):
            raise Missing(
                f"ta baza nie ma wszystkich tabel {REQUIRED_TABLES}; zera na bazie bez migracji "
                "to nie jest spokojna doba"
            )
        raw = measure(args.container, args.user, args.database, args.project, hours)
    except Missing as missing:
        print(f"{NOT_MEASURED}: {missing}", file=sys.stderr)
        print("Brak pomiaru to porażka, nie sukces (D-007).", file=sys.stderr)
        return 3

    rows = measurements(raw, thresholds)
    short = hours < 24
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = render(raw, rows, short, str(thresholds.get("zamrozone_dnia", "?")))
    (out_dir / "raport.md").write_text(report, encoding="utf-8")
    (out_dir / "pomiar.json").write_text(
        json.dumps(
            {
                **raw,
                "progi_zamrozone_dnia": thresholds.get("zamrozone_dnia"),
                "miary": [
                    {
                        "nazwa": row.name,
                        "wartosc": row.value,
                        "prog": row.threshold,
                        "werdykt": row.verdict(short),
                        "z_czego": row.detail,
                    }
                    for row in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)
    print(f"artefakt: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
