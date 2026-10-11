"""Głos osoby: odczyt, historia i zapis nowej wersji (migracja 0022).

OSOBNY PLIK, A NIE DOPISEK DO `api/workspaces.py`. Tamten plik ma dziś ponad osiemset linii
i jest współdzielony; własny router z własnym prefiksem to jeden import mniej do pomylenia
i zero ryzyka, że dwie sesje edytujące jednocześnie ten sam plik zjedzą sobie zmiany.
Wyszukiwanie routerów w `control_plane.app.discover_routers` podnosi go samo.

CZTERY POLA I NIC WIĘCEJ. Kształt głosu jest taki sam, jak profilu pilota z 0004, bo to ten
sam zestaw pytań: co ta osoba zna głęboko, jakie ma stanowiska, czego nigdy nie powie i jak
brzmi. Piąte pole dodane tylko tutaj byłoby polem, którego nie widzi ani narzędzie sesji,
ani ekran, ani prompt.

ZAPIS ZAWSZE TWORZY NOWĄ WERSJĘ. Nie ma tu PATCH-a i nie będzie: baza odmawia UPDATE
(wyzwalacz z 0022), więc trasa, która by go próbowała, byłaby trasą zwracającą 500.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.workspace import WorkspaceStore
from control_plane.workspace.voice import (
    PersonVoiceStore,
    PilotVoiceRow,
    VoiceRefusal,
    VoiceVersionRow,
)

router = APIRouter(prefix="/api", tags=["głos osoby"])

#: Ile znaków najwyżej przyjmuje jedno pole głosu. Tyle, co pola profilu pilota w praktyce:
#: to są akapity, nie eseje, a limit istnieje po to, żeby wklejona książka nie wjechała do
#: kontekstu modelu przy każdej turze sesji.
MAX_FIELD_LENGTH = 4_000


class WriteVoiceBody(BaseModel):
    """Nowa wersja głosu. Trzy pola wymagane, próbki mogą być puste.

    `voice_samples` wolno zostawić puste, bo próbek cudzego pisania można nie mieć, a wymuszanie
    ich kończy się wpisaniem czegokolwiek. Pozostałe trzy puste znaczyłyby głos, który nie mówi
    nic, i baza odmawia ich tak samo jak ta klasa.
    """

    model_config = ConfigDict(extra="forbid")

    knows_deeply: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    positions: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    would_never_claim: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    voice_samples: str = Field(default="", max_length=MAX_FIELD_LENGTH)


async def workspace_store(request: Request) -> WorkspaceStore:
    return WorkspaceStore(await pool_for(request.app))


async def voice_store(request: Request) -> PersonVoiceStore:
    return PersonVoiceStore(await pool_for(request.app))


def version_payload(row: VoiceVersionRow) -> dict[str, Any]:
    return {
        "voice_version_id": str(row.id),
        "person_profile_id": str(row.person_profile_id),
        "version": row.version,
        "knows_deeply": row.knows_deeply,
        "positions": row.positions,
        "would_never_claim": row.would_never_claim,
        "voice_samples": row.voice_samples,
        # NULL znaczy sesja otwarta wspólnym hasłem operatora sprzed P7. Ekran pisze wtedy
        # „operator”, zamiast podstawiać czyjekolwiek nazwisko.
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat(),
    }


def pilot_payload(row: PilotVoiceRow) -> dict[str, Any]:
    return {
        "user_profile_version_id": str(row.id),
        "version": row.version,
        "knows_deeply": row.knows_deeply,
        "positions": row.positions,
        "would_never_claim": row.would_never_claim,
        "voice_samples": row.voice_samples,
        "created_at": row.created_at.isoformat(),
    }


def _trimmed(value: str, what: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{what} nie może być pustym polem")
    return cleaned


async def _profile_in_workspace(
    store: WorkspaceStore, operator: OperatorSession, workspace_id: UUID, profile_id: UUID
) -> None:
    """Brama każdej trasy: przestrzeń tej organizacji i profil w tej przestrzeni, albo 404.

    Dwa osobne sprawdzenia, bo dają dwie różne odpowiedzi na dwa różne pytania, a profil
    z innej organizacji ma być nie do odróżnienia od nieistniejącego.
    """
    workspace = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    profile = await store.get_profile(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        profile_id=profile_id,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail=f"nie ma profilu osoby {profile_id}")


@router.get("/workspaces/{workspace_id}/profiles/{profile_id}/voice")
async def read_voice(
    workspace_id: UUID,
    profile_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
    voices: Annotated[PersonVoiceStore, Depends(voice_store)],
) -> dict[str, Any]:
    """Najnowsza wersja, cała historia i ewentualna propozycja przejęcia głosu pilota.

    Propozycja wraca TYLKO wtedy, gdy ta osoba nie ma jeszcze żadnej wersji, ma dziś przypięte
    konto i przestrzeń ma profil pilota. Trzy warunki, bo każdy odpowiada na inne pytanie:
    czy jest co proponować, czy jest komu, i czy nie proponujemy nadpisania czyjejś pracy.
    Propozycja to DANE do kliknięcia, nie zapis: dopóki człowiek nie wyśle formularza, w bazie
    nie ma ani jednej wersji.
    """
    await _profile_in_workspace(store, operator, workspace_id, profile_id)
    history = await voices.versions(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        person_profile_id=profile_id,
    )
    proposal: dict[str, Any] | None = None
    if not history:
        attached = await voices.has_active_account(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            person_profile_id=profile_id,
        )
        pilot = await voices.pilot_voice(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        if attached and pilot is not None:
            proposal = pilot_payload(pilot)
    return {
        "person_profile_id": str(profile_id),
        "voice": version_payload(history[0]) if history else None,
        "history": [version_payload(row) for row in history],
        "proposal": proposal,
    }


@router.post("/workspaces/{workspace_id}/profiles/{profile_id}/voice", status_code=201)
async def write_voice(
    workspace_id: UUID,
    profile_id: UUID,
    body: WriteVoiceBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[WorkspaceStore, Depends(workspace_store)],
    voices: Annotated[PersonVoiceStore, Depends(voice_store)],
) -> dict[str, Any]:
    await _profile_in_workspace(store, operator, workspace_id, profile_id)
    try:
        written = await voices.write(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            person_profile_id=profile_id,
            knows_deeply=_trimmed(body.knows_deeply, "co ta osoba zna głęboko"),
            positions=_trimmed(body.positions, "stanowiska"),
            would_never_claim=_trimmed(body.would_never_claim, "czego ta osoba nigdy nie powie"),
            voice_samples=body.voice_samples.strip(),
            created_by=operator.user_id,
        )
    except VoiceRefusal as refusal:
        raise HTTPException(status_code=409, detail=str(refusal)) from refusal

    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="PERSON_VOICE_VERSION_WRITTEN",
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=f"person_profile:{profile_id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"person_profile_id": str(profile_id), "version": written.version},
        )
    return {"voice": version_payload(written)}
