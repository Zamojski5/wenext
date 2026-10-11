"""HTTP dla stanu usługi: czy działa, co jest niedostępne i ile wydano (P7 etap B).

JEDNA TRASA CZYTAJĄCA I JEDNA PISZĄCA, i to jest cały zakres:

  * `GET .../status` — zdanie o usłudze na poziomie WORKSPACE'U, z listą otwartych incydentów i
    historią z tygodnia. To jest pierwsza rzecz, którą ktoś otwiera, gdy coś nie działa, więc
    odpowiedź zaczyna się od zdania, a nie od tabeli liczb.
  * `PUT .../limits` — sufit organizacji. Osobna trasa od budżetu delegacji, bo to inna decyzja
    i innej wagi: delegacja dotyczy jednego projektu, sufit wszystkich naraz.

CZEGO TU NIE MA: trasy, która „naprawia" komponent. Nie ma czego naprawiać przez HTTP: komponent
wraca, gdy wraca, i sam zamyka swój incydent. Przycisk, który zamykałby incydent bez powrotu
komponentu, kasowałby dowód, a nie awarię.

STATUS JEST NA POZIOMIE WORKSPACE'U, ale incydenty i limity są ORGANIZACYJNE (0020). To nie jest
niespójność: dyspozytor, kolektor i kapsuła służą całej organizacji, a workspace jest miejscem, w
którym człowiek patrzy. Odpowiedź mówi to wprost, zamiast udawać, że awaria dotyczy tylko tego
workspace'u, który akurat jest otwarty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.ops.health import COMPONENTS, INTERVAL, HealthStore, availability
from control_plane.ops.limits import org_spend
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["ops"])

STATUS_PATH = "/workspaces/{workspace_id}/status"
LIMITS_PATH = "/workspaces/{workspace_id}/limits"

#: How far back the status screen looks. A week, the same window the report uses, so the number
#: on the screen and the number in the report cannot disagree.
WINDOW = timedelta(days=7)

#: A ceiling nobody could ever reach is not a ceiling. Bounded so a typo cannot produce one.
MAX_LIMIT = 10_000


class LimitsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: `None` means no ceiling of that kind, and it is NOT zero: zero forbids everything.
    model_turns_per_day: int | None = Field(default=None, ge=0, le=MAX_LIMIT)
    publications_per_day: int | None = Field(default=None, ge=0, le=MAX_LIMIT)


async def _resolve_workspace(
    request: Request, operator: OperatorSession, workspace_id: UUID
) -> None:
    workspaces = WorkspaceStore(await pool_for(request.app))
    if (
        await workspaces.workspace(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")


@router.get(STATUS_PATH)
async def read_status(
    request: Request,
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Czy usługa działa, a jeśli nie w całości, to co i od kiedy."""
    await _resolve_workspace(request, operator, workspace_id)
    pool = await pool_for(request.app)
    health = HealthStore(pool)
    now = datetime.now(UTC)
    since = now - WINDOW

    open_now = await health.open_incidents(organization_id=operator.organization_id)
    history = await health.incidents_since(organization_id=operator.organization_id, since=since)

    components: list[dict[str, Any]] = []
    for component in COMPONENTS:
        beats = await health.beats_since(
            organization_id=operator.organization_id, component=component, since=since
        )
        last = await health.last_beat(organization_id=operator.organization_id, component=component)
        components.append(
            {
                "komponent": component,
                # A component that never beat is NOT reported as 0% available: it is reported as
                # not running here, which is a different fact and needs a different answer.
                "uruchomiony": last is not None,
                "ostatni_puls": last.isoformat() if last else None,
                "dostepnosc": round(availability(beats, WINDOW), 4) if last else None,
                "interwalow_z_pulsem": beats,
            }
        )

    async with pool.acquire() as conn:
        spend = await org_spend(conn, organization_id=operator.organization_id, now=now)

    return {
        # The sentence first. Everything below it is detail for when the sentence is not enough.
        "stan": _sentence(open_now),
        "dziala": not open_now,
        "niedostepne": [one.to_json() for one in open_now],
        "komponenty": components,
        "incydenty_tygodnia": [one.to_json() for one in history],
        "budzet_organizacji": spend.to_json(),
        "interwal_sekund": int(INTERVAL.total_seconds()),
    }


def _sentence(open_incidents) -> str:
    """Jedno zdanie, po polsku, dla kogoś, kto właśnie otworzył laptop."""
    if not open_incidents:
        return "usługa działa"
    if len(open_incidents) == 1:
        one = open_incidents[0]
        return f"część niedostępna: {one.component} od {one.started_at.isoformat()}"
    names = ", ".join(sorted(one.component for one in open_incidents))
    return f"część niedostępna: {names}"


@router.put(LIMITS_PATH)
async def set_limits(
    request: Request,
    workspace_id: UUID,
    body: LimitsBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Ustaw sufit całej organizacji. Osobna decyzja od budżetu pojedynczego projektu."""
    await _resolve_workspace(request, operator, workspace_id)
    pool = await pool_for(request.app)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO org_limits
                (organization_id, model_turns_per_day, publications_per_day, updated_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (organization_id) DO UPDATE
               SET model_turns_per_day = excluded.model_turns_per_day,
                   publications_per_day = excluded.publications_per_day,
                   updated_at = now(),
                   updated_by = excluded.updated_by
            """,
            operator.organization_id,
            body.model_turns_per_day,
            body.publications_per_day,
            operator.operator_id,
        )
        await AuditWriter(conn).record(
            kind="ORG_LIMITS_CHANGED",
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=f"organization:{operator.organization_id}",
            actor_ref=f"operator:{operator.operator_id}",
            # Identifiers and counts only, like every payload in this table.
            payload={
                "workspace_id": str(workspace_id),
                "count": body.model_turns_per_day or 0,
            },
        )
    async with pool.acquire() as conn:
        spend = await org_spend(conn, organization_id=operator.organization_id)
    return {"budzet_organizacji": spend.to_json()}
