"""Sufit na całą organizację i to, co pod nim wydano (P7 etap B).

DLACZEGO TO NIE WYSTARCZY, ŻE DELEGACJA MA BUDŻET. `project_delegations.budget` z 0017 ogranicza
JEDEN projekt. Dziesięć projektów z limitem sześciu publikacji dziennie to sześćdziesiąt
publikacji dziennie, i nikt takiego zdania nie podpisywał. Sufit organizacyjny jest drugą,
grubszą kreską: projekt nie może wydać więcej, niż mu podpisano, ORAZ organizacja nie może wydać
więcej, niż ustawiono jej łącznie.

DWA LIMITY, DWA ZDANIA, NIGDY SUMA. Gdy któryś jest wyczerpany, odmowa mówi KTÓRY, bo „budżet
wyczerpany" nie podpowiada, co zrobić, a „sufit organizacji: 20 z 20 tur modelu w ostatniej
dobie" podpowiada dokładnie: podnieść sufit albo poczekać.

LIMIT KONTA SILNIKA JEST TRZECIĄ, OSOBNĄ LINIĄ i nie jest tu sumowany ani porównywany. Codex ma
własne limity konta (odczytane przy połączeniu, P1 etap 2); są cudze, liczone w cudzych
jednostkach i wyczerpują się niezależnie. Zsumowanie ich z budżetem Wenextu dałoby liczbę, która
nie znaczy nic, a wygląda na sufit.

OKNO JEST KROCZĄCE, jak wszędzie tutaj: „dwadzieścia na dobę" resetowane o północy pozwala na
czterdzieści między 23:00 a 01:00.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

DAY = timedelta(hours=24)

#: What counts as a model turn and what counts as a publication, in the ledger's vocabulary.
KIND_MODEL_TURN = "MODEL_TURN"
KIND_TOOL_CALL = "TOOL_CALL"
KIND_PUBLICATION = "PUBLICATION"
KIND_FETCH = "FETCH"
COST_KINDS = (KIND_MODEL_TURN, KIND_TOOL_CALL, KIND_PUBLICATION, KIND_FETCH)


@dataclass(frozen=True)
class OrgLimits:
    """The organization-wide ceiling. `None` means no ceiling of that kind, and that is NOT zero.

    Unlike a delegation's budget, an absent organization limit gets no conservative default: the
    per-project budget already applies its own, and adding a second invisible ceiling here would
    make "why did it stop" unanswerable from the screen.
    """

    model_turns_per_day: int | None = None
    publications_per_day: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "tury_modelu_doba": self.model_turns_per_day,
            "publikacje_doba": self.publications_per_day,
        }


@dataclass(frozen=True)
class OrgSpend:
    """What the whole organization used in the rolling day, against its ceiling."""

    model_turns_today: int
    publications_today: int
    limits: OrgLimits

    def exhausted(self) -> str | None:
        """The first ceiling that is spent, NAMED, or `None`."""
        if (
            self.limits.model_turns_per_day is not None
            and self.model_turns_today >= self.limits.model_turns_per_day
        ):
            return (
                f"sufit organizacji, tury modelu: {self.model_turns_today} z "
                f"{self.limits.model_turns_per_day} w ostatniej dobie"
            )
        if (
            self.limits.publications_per_day is not None
            and self.publications_today >= self.limits.publications_per_day
        ):
            return (
                f"sufit organizacji, publikacje: {self.publications_today} z "
                f"{self.limits.publications_per_day} w ostatniej dobie"
            )
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "tury_modelu_doba": self.model_turns_today,
            "publikacje_doba": self.publications_today,
            "limity": self.limits.to_json(),
            "wyczerpany": self.exhausted(),
        }


async def org_limits_for(conn: asyncpg.Connection, *, organization_id: UUID) -> OrgLimits:
    """The ceiling, or an empty one. A missing row means nobody set a ceiling."""
    row = await conn.fetchrow(
        "SELECT model_turns_per_day, publications_per_day FROM org_limits "
        "WHERE organization_id = $1",
        organization_id,
    )
    if row is None:
        return OrgLimits()
    return OrgLimits(
        model_turns_per_day=row["model_turns_per_day"],
        publications_per_day=row["publications_per_day"],
    )


async def org_spend(
    conn: asyncpg.Connection, *, organization_id: UUID, now: datetime | None = None
) -> OrgSpend:
    """What the organization spent in the rolling day, READ FROM THE LEDGER.

    A connection, not a pool, for the reason `presence/budget.py` gives at the project level: the
    caller may need to count and write inside one transaction, and two ticks counting on separate
    connections both find room for the last turn.

    The ledger and not the source tables, deliberately: `cost_ledger` is where every kind of
    spending meets, so this function does not have to know that model turns live in `wakeups`
    while publications live in `execution_attempts`. Feeding the ledger is `ledger.py`'s job.
    """
    moment = now or datetime.now(UTC)
    row = await conn.fetchrow(
        """
        SELECT
            coalesce(sum(units) FILTER (WHERE kind = $2), 0) AS turns,
            coalesce(sum(units) FILTER (WHERE kind = $3), 0) AS publications
          FROM cost_ledger
         WHERE organization_id = $1 AND occurred_at >= $4
        """,
        organization_id,
        KIND_MODEL_TURN,
        KIND_PUBLICATION,
        moment - DAY,
    )
    return OrgSpend(
        model_turns_today=int(row["turns"]),
        publications_today=int(row["publications"]),
        limits=await org_limits_for(conn, organization_id=organization_id),
    )
