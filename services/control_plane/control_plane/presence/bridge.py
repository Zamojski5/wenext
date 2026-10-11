"""Most z pilota do ścieżki projektowej (P4 etap B).

CO TU SIĘ DZIEJE I DLACZEGO NIE JEST TO MIGRACJA. Pilot (D-029) jest osobnym mechanizmem:
podpisana polityka, biegi per społeczność, konto podane w polityce z nazwy. Docelowo obecność na
Reddicie idzie przez PROJEKT z delegacją (P4). Przełożenie jednego na drugie nie jest zmianą
schematu, tylko ZAŁOŻENIEM NOWYCH WIERSZY na podstawie tego, co pilot już wie: jakie społeczności
obserwował, w których wolno mu było pisać, z jakiego konta i w jakim tempie.

TRZY ZASADY, KTÓRE RZĄDZĄ TYM PLIKIEM:

  1. **PLAN JEST ODDZIELONY OD ZAPISU.** Domyślnie powstaje opis tego, co by się stało, i nic
     więcej. Zapis wymaga osobnego kroku. Migracja pilota na projekt jest rzeczą, którą robi się
     raz i patrzy się na nią wcześniej.
  2. **DELEGACJI NIE PODPISUJE TEN KOD.** Plan zakłada wiersze projektu, profilu, konta i
     przypisań, ale delegacja jest ZGODĄ, a zgoda jest kliknięciem człowieka. Skrypt, który
     podpisałby ją za założyciela, wytworzyłby zgodę, której nikt nie wyraził, i to jest dokładnie
     ta jedna rzecz, której cały ten produkt nie ma prawa zrobić.
  3. **WIERSZE PILOTA ZOSTAJĄ NIETKNIĘTE.** Dotychczasowe publikacje wiąże się z projektem przez
     KONTO, w odczycie, a nie przez UPDATE na `publication_requests`. Historia pilota jest
     historią; przepisanie jej pod nowy projekt zmieniłoby to, co się wydarzyło, żeby pasowało
     do tego, co planujemy.

SPOŁECZNOŚCI PISZĄCE I TYLKO CZYTANE SĄ ROZDZIELONE, bo polityka je rozdziela: pilot obserwuje
trzynaście, a publikować wolno mu w dwóch. Wrzucenie wszystkich trzynastu do zakresu delegacji
byłoby cichym poszerzeniem zgody przy okazji przenosin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.policy import POLICY_ACTIVE

from .scope import ACTION_REPLY, MODE_AUTONOMOUS, Budget, Scope, digest

#: Nazwa projektu, do którego przenosi się obecność pilota. Stała, bo plan ma być
#: DETERMINISTYCZNY: dwa uruchomienia na tych samych danych mają dać ten sam plan, inaczej
#: „popatrz na plan, potem go zastosuj" nic nie gwarantuje.
PROJECT_NAME = "Obecność na Reddicie"

#: Tempo, gdy polityki nie da się odczytać. Bierzemy je z polityki, gdy jest; ta wartość jest
#: ostatnią deską i odpowiada temu, co pilot miał podpisane (D-029).
FALLBACK_PER_HOUR = 2
FALLBACK_PER_DAY = 12


@dataclass(frozen=True)
class PlannedCommunity:
    community_id: UUID
    community_key: str
    #: Czy polityka pozwalała TU PISAĆ. Trzynaście obserwowanych, dwie piszące (D-029).
    writable: bool


@dataclass
class Plan:
    """Co by powstało. Czytelne dla człowieka i porównywalne między uruchomieniami."""

    organization_id: UUID
    workspace_id: UUID
    policy_id: UUID | None
    project_name: str
    goal: str
    account_handle: str
    account_id: UUID | None
    person_profile_name: str
    communities: list[PlannedCommunity] = field(default_factory=list)
    mode: str = MODE_AUTONOMOUS
    per_hour: int = FALLBACK_PER_HOUR
    per_day: int = FALLBACK_PER_DAY
    #: Publikacje pilota, które po przenosinach będą czytane jako historia tego projektu.
    historical_publications: int = 0
    #: Czego planu NIE DA SIĘ wykonać i dlaczego. Pusta lista znaczy „da się"; niepusta znaczy,
    #: że `--apply` odmówi, zamiast założyć połowę wierszy.
    blockers: list[str] = field(default_factory=list)

    @property
    def writable_communities(self) -> list[PlannedCommunity]:
        return [one for one in self.communities if one.writable]

    @property
    def scope(self) -> Scope:
        """Zakres delegacji: TYLKO społeczności, w których polityka pozwalała pisać."""
        return Scope(
            accounts=(self.account_id,) if self.account_id else (),
            communities=tuple(sorted(one.community_key for one in self.writable_communities)),
            actions=(ACTION_REPLY,),
        )

    @property
    def budget(self) -> Budget:
        return Budget(publications_per_hour=self.per_hour, publications_per_day=self.per_day)

    @property
    def scope_sha256(self) -> str:
        return digest(mode=self.mode, scope=self.scope, budget=self.budget)

    def to_json(self) -> dict[str, Any]:
        return {
            "organizacja": str(self.organization_id),
            "workspace": str(self.workspace_id),
            "polityka": str(self.policy_id) if self.policy_id else None,
            "projekt": self.project_name,
            "cel": self.goal,
            "profil_osoby": self.person_profile_name,
            "konto": self.account_handle,
            "konto_id": str(self.account_id) if self.account_id else None,
            "tryb": self.mode,
            "zakres": {
                "spolecznosci_piszace": [one.community_key for one in self.writable_communities],
                "spolecznosci_tylko_czytane": [
                    one.community_key for one in self.communities if not one.writable
                ],
                "dzialania": list(self.scope.actions),
            },
            "budzet": {"publikacje_godzina": self.per_hour, "publikacje_doba": self.per_day},
            "odcisk_zakresu": self.scope_sha256 if self.account_id else None,
            "publikacje_historyczne": self.historical_publications,
            "przeszkody": list(self.blockers),
            "podpis": (
                "NIE jest częścią tego planu. Delegację podpisuje człowiek w studiu, bo to zgoda."
            ),
        }

    def render(self) -> str:
        """Plan po polsku, dla kogoś, kto zaraz zdecyduje, czy go wykonać."""
        lines = [
            f"Plan przeniesienia pilota do projektu „{self.project_name}”",
            "",
            f"  organizacja:      {self.organization_id}",
            f"  workspace:        {self.workspace_id}",
            f"  polityka pilota:  {self.policy_id or 'brak aktywnej'}",
            "",
            f"  projekt:          {self.project_name}",
            f"  cel:              {self.goal[:200]}",
            f"  profil osoby:     {self.person_profile_name}",
            f"  konto:            {self.account_handle}"
            + (f" ({self.account_id})" if self.account_id else " (NIE ZNALEZIONE W BAZIE)"),
            "",
            f"  tryb delegacji:   {self.mode}",
            f"  tempo:            {self.per_hour}/h, {self.per_day}/dobę",
            f"  działania:        {', '.join(self.scope.actions)}",
            "",
            f"  społeczności PISZĄCE ({len(self.writable_communities)}):",
        ]
        for one in self.writable_communities:
            lines.append(f"      {one.community_key}")
        only_read = [one for one in self.communities if not one.writable]
        lines.append(f"  społeczności TYLKO CZYTANE ({len(only_read)}):")
        for one in only_read:
            lines.append(f"      {one.community_key}")
        lines += [
            "",
            "  publikacje pilota widoczne potem jako historia projektu: "
            f"{self.historical_publications}",
            "      (powiązanie przez konto, w odczycie; wiersze pilota nie są zmieniane)",
            "",
            "  DELEGACJA NIE ZOSTANIE PODPISANA. Powstanie projekt, profil, przypięcie konta",
            "  i przypisanie do projektu; zgodę podpisuje człowiek w studiu.",
        ]
        if self.blockers:
            lines += ["", "  PRZESZKODY (przy --apply nic nie powstanie):"]
            lines += [f"      {one}" for one in self.blockers]
        return "\n".join(lines)


async def build_plan(
    conn: asyncpg.Connection, *, organization_id: UUID, now: datetime | None = None
) -> Plan:
    """Zbuduj plan z aktywnej polityki i z tego, co pilot obserwował.

    DETERMINISTYCZNY: wszystko, co trafia do planu, jest posortowane po kluczu społeczności albo
    po identyfikatorze. Plan, który przy drugim odczytaniu wygląda inaczej, nie daje się obejrzeć
    przed wykonaniem, a obejrzenie go przed wykonaniem jest całym sensem tego polecenia.
    """
    moment = now or datetime.now(UTC)
    policy = await conn.fetchrow(
        """
        SELECT id, workspace_id, community_ids, account_handle, max_per_hour, max_per_day,
               source_text, status, valid_until
          FROM publication_policies
         WHERE organization_id = $1 AND status = $2 AND valid_from <= $3 AND valid_until > $3
         ORDER BY signed_at DESC LIMIT 1
        """,
        organization_id,
        POLICY_ACTIVE,
        moment,
    )
    blockers: list[str] = []
    if policy is None:
        # Nie jest to porażka: pilot mógł już wygasnąć, a przeniesienie nadal ma sens. Plan
        # powstaje z samych biegów, a tempo z wartości, które pilot miał podpisane.
        workspace_id = await conn.fetchval(
            "SELECT id FROM workspaces WHERE organization_id = $1 ORDER BY created_at LIMIT 1",
            organization_id,
        )
        if workspace_id is None:
            blockers.append("ta organizacja nie ma ani jednego workspace'u")
        handle = await conn.fetchval(
            "SELECT account_handle FROM publication_policies WHERE organization_id = $1 "
            "ORDER BY signed_at DESC LIMIT 1",
            organization_id,
        )
        policy_id, writable_ids = None, set()
        per_hour, per_day = FALLBACK_PER_HOUR, FALLBACK_PER_DAY
        goal_source = ""
        if handle is None:
            blockers.append("nie ma ani jednej polityki pilota, więc nie wiadomo, z jakiego konta")
            handle = ""
    else:
        policy_id = policy["id"]
        workspace_id = policy["workspace_id"]
        writable_ids = set(policy["community_ids"])
        handle = policy["account_handle"]
        per_hour = int(policy["max_per_hour"]) or FALLBACK_PER_HOUR
        per_day = int(policy["max_per_day"]) or FALLBACK_PER_DAY
        goal_source = policy["source_text"]

    communities: list[PlannedCommunity] = []
    if workspace_id is not None:
        rows = await conn.fetch(
            """
            SELECT DISTINCT c.id, c.community_key
              FROM communities c
              JOIN pilot_runs r ON r.community_id = c.id
             WHERE c.organization_id = $1 AND c.workspace_id = $2
             ORDER BY c.community_key
            """,
            organization_id,
            workspace_id,
        )
        communities = [
            PlannedCommunity(
                community_id=row["id"],
                community_key=row["community_key"],
                writable=row["id"] in writable_ids,
            )
            for row in rows
        ]
    if not communities:
        blockers.append("pilot nie obserwował ani jednej społeczności w tym workspace")
    elif not any(one.writable for one in communities):
        # Delegacja bez ani jednej społeczności piszącej nie pozwala na nic. Lepiej powiedzieć to
        # teraz niż założyć zakres, który niczego nie autoryzuje.
        blockers.append(
            "żadna obserwowana społeczność nie jest w zakresie publikacji polityki; "
            "delegacja nie miałaby gdzie pisać"
        )

    account_id = None
    if handle:
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE organization_id = $1 AND platform = 'REDDIT' "
            "AND external_account_id = $2",
            organization_id,
            handle,
        )
        if account_id is None:
            blockers.append(
                f"konto {handle} nie jest zarejestrowane w `social_accounts`; "
                "najpierw trzeba je dodać w studiu"
            )

    historical = 0
    if account_id is not None:
        historical = int(
            await conn.fetchval(
                """
                SELECT count(*)
                  FROM publication_requests q
                  JOIN action_proposal_revisions r ON r.id = q.proposal_revision_id
                  JOIN action_proposals p ON p.id = r.proposal_id
                 WHERE q.organization_id = $1 AND p.social_account_id = $2
                """,
                organization_id,
                account_id,
            )
            or 0
        )

    return Plan(
        organization_id=organization_id,
        workspace_id=workspace_id,
        policy_id=policy_id,
        project_name=PROJECT_NAME,
        goal=_goal_from(goal_source),
        account_handle=handle or "(nieznane)",
        account_id=account_id,
        person_profile_name=handle or "(nieznane)",
        communities=communities,
        per_hour=per_hour,
        per_day=per_day,
        historical_publications=historical,
        blockers=blockers,
    )


def _goal_from(source_text: str) -> str:
    """Cel projektu z tekstu, którym człowiek podpisał politykę.

    Przycięty, nie przepisany: to są JEGO słowa i nie ma powodu ich ulepszać. Gdy polityki nie
    ma, cel mówi wprost, że go nie ma, zamiast wymyślać zdanie, którego nikt nie napisał.
    """
    text = " ".join((source_text or "").split())
    if not text:
        return "(cel przeniesiony z pilota; polityka nie zostawiła opisu, dopisz go w studiu)"
    return text[:1000]


@dataclass(frozen=True)
class Applied:
    """Co naprawdę powstało. Liczby, nie zdania, żeby dało się je porównać z planem."""

    project_id: UUID
    person_profile_id: UUID
    attached_account: bool
    assigned_to_project: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "projekt_id": str(self.project_id),
            "profil_osoby_id": str(self.person_profile_id),
            "konto_przypiete": self.attached_account,
            "konto_przypisane_do_projektu": self.assigned_to_project,
            "delegacja": "NIE podpisana: czeka na kliknięcie człowieka w studiu",
        }


async def apply_plan(conn: asyncpg.Connection, plan: Plan) -> Applied:
    """Załóż wiersze planu w JEDNEJ transakcji. Nie podpisuje delegacji i nie rusza pilota.

    Wywołanie na planie z przeszkodami jest odmawiane w całości. Połowa przeniesienia jest
    gorsza niż żadne: zostawia projekt bez konta albo profil bez projektu, a osoba, która to
    ogląda, nie wie, czy patrzy na wynik, czy na awarię.

    IDEMPOTENTNE: drugie uruchomienie znajduje to, co jest, i niczego nie dubluje. Polecenie
    przenosin jest z tych, które ktoś uruchomi dwa razy, żeby sprawdzić, czy zadziałało.
    """
    if plan.blockers:
        raise BridgeRefusal(
            "plan ma przeszkody, więc nic nie zostanie założone: " + "; ".join(plan.blockers)
        )
    assert plan.account_id is not None  # gwarantowane brakiem przeszkód

    project_id = await conn.fetchval(
        "SELECT id FROM projects WHERE organization_id = $1 AND workspace_id = $2 AND name = $3",
        plan.organization_id,
        plan.workspace_id,
        plan.project_name,
    )
    if project_id is None:
        project_id = await conn.fetchval(
            """
            INSERT INTO projects (organization_id, workspace_id, name, goal, mode)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            plan.organization_id,
            plan.workspace_id,
            plan.project_name,
            plan.goal,
            # Tryb projektu jest LUSTREM trybu delegacji (D-041), a delegacji jeszcze nie ma.
            # REVIEW do czasu podpisu: dopóki nikt nie podpisał zakresu, nic nie idzie samo.
            "REVIEW",
        )

    profile_id = await conn.fetchval(
        "SELECT id FROM person_profiles WHERE organization_id = $1 AND workspace_id = $2 "
        "AND name = $3",
        plan.organization_id,
        plan.workspace_id,
        plan.person_profile_name,
    )
    if profile_id is None:
        profile_id = await conn.fetchval(
            "INSERT INTO person_profiles (organization_id, workspace_id, name, kind) "
            "VALUES ($1, $2, $3, 'PERSON') RETURNING id",
            plan.organization_id,
            plan.workspace_id,
            plan.person_profile_name,
        )

    attached = await conn.fetchval(
        "SELECT 1 FROM person_profile_accounts WHERE organization_id = $1 "
        "AND person_profile_id = $2 AND social_account_id = $3 AND detached_at IS NULL",
        plan.organization_id,
        profile_id,
        plan.account_id,
    )
    if attached is None:
        await conn.execute(
            "INSERT INTO person_profile_accounts (organization_id, workspace_id, "
            "person_profile_id, social_account_id) VALUES ($1, $2, $3, $4)",
            plan.organization_id,
            plan.workspace_id,
            profile_id,
            plan.account_id,
        )

    assigned = await conn.fetchval(
        "SELECT 1 FROM project_account_assignments WHERE organization_id = $1 "
        "AND project_id = $2 AND social_account_id = $3 AND unassigned_at IS NULL",
        plan.organization_id,
        project_id,
        plan.account_id,
    )
    if assigned is None:
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            plan.organization_id,
            plan.workspace_id,
            project_id,
            plan.account_id,
        )

    return Applied(
        project_id=project_id,
        person_profile_id=profile_id,
        attached_account=attached is None,
        assigned_to_project=assigned is None,
    )


class BridgeRefusal(Exception):
    """Plan, którego nie da się wykonać. Nic nie zostało zapisane."""


def as_json(plan: Plan, applied: Applied | None = None) -> str:
    payload: dict[str, Any] = {"plan": plan.to_json()}
    if applied is not None:
        payload["wykonano"] = applied.to_json()
    return json.dumps(payload, ensure_ascii=False, indent=2)


# -----------------------------------------------------------------------------
# Wejście z wiersza poleceń
# -----------------------------------------------------------------------------
#
# WEJŚCIE JEST W PAKIECIE, nie w `tools/dev/`, i to jest poprawka z live 19.09: skrypt biegł
# przez `compose exec api`, a obraz api NIE ZAWIERA katalogu `tools/`, więc polecenie kończyło
# się „python3: can't open file". Wszystko, co ma się wykonać w kontenerze, musi być tym, co do
# kontenera trafia, czyli pakietem.


def _build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m control_plane.presence.bridge",
        description="Plan przeniesienia pilota do projektu; domyślnie nic nie zapisuje",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="zapisz wiersze planu (projekt, profil, przypięcie, przypisanie) w jednej transakcji",
    )
    parser.add_argument("--json", action="store_true", help="wypisz plan jako JSON")
    parser.add_argument(
        "--organization",
        help=(
            "identyfikator organizacji; domyślnie najstarsza w bazie. Na live są dwie, a most "
            "dotyczy tej, w której stoi pilot, więc wskazuj ją jawnie"
        ),
    )
    return parser


async def _run_cli(
    dsn: str, organization_id: UUID | None, *, apply: bool, as_json_out: bool
) -> int:
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if organization_id is None:
                organization_id = await conn.fetchval(
                    "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
                )
                if organization_id is None:
                    print("NIE_URUCHOMIONO: baza nie ma ani jednej organizacji")
                    return 3
            plan = await build_plan(conn, organization_id=organization_id)

            if not apply:
                print(as_json(plan) if as_json_out else plan.render())
                if plan.blockers:
                    print("\n(--apply odmówi, dopóki przeszkody nie znikną)")
                return 0

            async with conn.transaction():
                applied = await apply_plan(conn, plan)
            print(as_json(plan, applied) if as_json_out else plan.render())
            print("\nWYKONANO:")
            print(f"  projekt:      {applied.project_id}")
            print(f"  profil osoby: {applied.person_profile_id}")
            print("\n  DELEGACJA NIE ZOSTAŁA PODPISANA. Wejdź w studio, obejrzyj zakres")
            print("  i podpisz go sam: to jest zgoda, a zgody nie wystawia skrypt.")
            return 0
    except BridgeRefusal as exc:
        print(f"ODMOWA: {exc}")
        return 1
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    import asyncio
    import os

    args = _build_parser().parse_args(argv)
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("NIE_URUCHOMIONO: brak DATABASE_URL; to polecenie biegnie w kontenerze api")
        return 3
    organization = UUID(args.organization) if args.organization else None
    return asyncio.run(_run_cli(dsn, organization, apply=args.apply, as_json_out=args.json))


if __name__ == "__main__":
    raise SystemExit(main())
