"""`GET .../projects/{id}/room` i `POST .../opportunities/{id}/dismiss`: strona "Pokój" (D-055).

DWIE TRASY, ŻADNEJ NOWEJ ŚCIEŻKI ZGODY. Odczyt składa trzy listy z tabel (okazje, szkice czekające
na zgodę, wyniki z ostatnich siedmiu dni) i niczego nie zmienia, także wizyty: to nie jest ekran
powrotu, tylko pokój, do którego agent odsyła założyciela. Zatwierdzenie szkicu zostaje na
ISTNIEJĄCEJ trasie `.../approvals/{revision_id}/approve` (`api/project_approvals.py`), z jej
sprawdzeniem powierzenia i budżetu; ta strona nie ma własnej drogi do publikacji.

POMINIĘCIE JEST JEDYNĄ ZMIANĄ, jaką ta strona robi, i nie kasuje niczego: okazja dostaje
`dismissed_at` razem z autorem (`operator:<id>`), a agent, który znajdzie tę samą rozmowę drugi
raz, nie przywraca jej na listę. Powtórzone kliknięcie nie jest błędem i nie przesuwa decyzji.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.opportunities.room import room_view
from control_plane.opportunities.store import OpportunityStore
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["pokój"])

PROJECT_PATH = "/workspaces/{workspace_id}/projects/{project_id}"
ROOM_PATH = PROJECT_PATH + "/room"
DISMISS_PATH = PROJECT_PATH + "/opportunities/{opportunity_id}/dismiss"


async def _project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> None:
    """Przestrzeń tej organizacji i projekt w niej, albo 404. Ta sama brama, co przy powrocie."""
    store = WorkspaceStore(await pool_for(request.app))
    if (
        await store.workspace(organization_id=operator.organization_id, workspace_id=workspace_id)
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")


@router.get(ROOM_PATH)
async def read_room(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Okazje, szkice czekające na zgodę i wyniki z siedmiu dni, jednym odczytem."""
    await _project(request, operator, workspace_id, project_id)
    return await room_view(
        await pool_for(request.app),
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )


@router.post(DISMISS_PATH)
async def dismiss_opportunity(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    opportunity_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Pomiń okazję. Okazja cudzego projektu jest nieznaleziona, nie zabroniona."""
    await _project(request, operator, workspace_id, project_id)
    result = await OpportunityStore(await pool_for(request.app)).dismiss(
        organization_id=operator.organization_id,
        project_id=project_id,
        opportunity_id=opportunity_id,
        by=f"operator:{operator.operator_id}",
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"nie ma okazji {opportunity_id}")
    view, changed = result
    return {"okazja": view.to_json(), "zmieniono": changed}
