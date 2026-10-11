"""`GET …/documents` i `GET …/documents/{id}`: pamięć projektu jako dokumenty (P8 etap A).

DWIE TRASY, OBIE TYLKO DO ODCZYTU. Zlecenie przepisania dochodzi w etapie B jako osobne `POST`;
etap A ma pokazać, co generator zbudował z rejestru, i nic więcej. Trasa, która potrafiłaby
zlecić przebudowę, byłaby trasą, którą da się wywołać przez pomyłkę na ekranie tylko do odczytu.

DOKUMENT NIE JEST DRUGĄ PAMIĘCIĄ. `knowledge_items` zostaje jedynym źródłem prawdy (D-043),
a dokument jest jego czytelnym widokiem: każde zdanie niesie `podstawa`, czyli identyfikatory
ustaleń, z których wynika, i baza pilnuje, żeby ta lista nie była pusta. Dlatego ta trasa oddaje
podstawę RAZEM ze zdaniem, a nie na życzenie: zdanie bez wskazania, skąd się wzięło, jest
twierdzeniem, którego nie da się sprawdzić, i po kilku tygodniach nikt nie odróżni go od
wymyślonego.

PUSTA LISTA, NIGDY 404. Projekt bez dokumentów to normalny stan pierwszego dnia, a 404 kazałoby
ekranowi zgadywać, czy projekt nie istnieje, czy tylko jeszcze nic nie domknięto. Brak WERSJI
to co innego i jest 404 ze zdaniem, która wersja jest najnowsza: pytanie o wersję 7 tam, gdzie
są trzy, to pomyłka, na którą trzeba odpowiedzieć liczbą, a nie pustką.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["dokumenty"])

DOCUMENTS_PATH = "/workspaces/{workspace_id}/projects/{project_id}/documents"

#: Kolejność ról na liście. Nie alfabetyczna i nie po dacie: taka, w jakiej człowiek czyta projekt.
#: Karta projektu mówi, po co to wszystko; „co działa" jest pierwszą rzeczą, o którą pyta się po
#: powrocie; decyzje i społeczności są tłem; podręcznik platformy dotyczy przestrzeni, nie projektu,
#: więc stoi na końcu.
ROLE_ORDER: tuple[str, ...] = (
    "PROJECT_CARD",
    "WHAT_WORKS",
    "DECISIONS",
    "COMMUNITIES",
    "PLATFORM_HANDBOOK",
)


async def _project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> None:
    """Granica jak w pozostałych trasach studia: przestrzeń tej organizacji i projekt w niej.

    Dwa osobne sprawdzenia, bo odpowiadają na dwa różne pytania, a projekt z cudzej organizacji
    ma być nie do odróżnienia od nieistniejącego.
    """
    store = WorkspaceStore(await pool_for(request.app))
    workspace = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    project = await store.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")


def _entry(row: asyncpg.Record) -> dict[str, Any]:
    """Jeden wpis indeksu. Wypisany polami, tak jak w `api/workspaces.py`: kolumna dołożona do
    tabeli nie ma zacząć po cichu jeździć do przeglądarki."""
    return {
        "document_id": str(row["document_id"]),
        "rola": row["role"],
        "platforma": row["platform"],
        "tytul": row["title"],
        "wersja": row["version"],
        "wygenerowano": row["generated_at"].isoformat(),
        "zrodlo": row["generated_by"],
        "liczba_zdan": int(row["liczba_zdan"]),
    }


#: Najnowsza wersja każdego dokumentu projektu wraz z liczbą zdań. Indeks NIE jest tabelą
#: (D-043): liczy się zapytaniem, bo trzecie miejsce z tą samą prawdą rozjeżdża się z dwoma
#: pozostałymi przy pierwszej zmianie.
_INDEX_SQL = """
SELECT d.id AS document_id, d.role, d.platform,
       v.version, v.title, v.generated_at, v.generated_by,
       (SELECT count(*) FROM project_document_sentences s
         WHERE s.version_id = v.id) AS liczba_zdan
  FROM project_documents d
  JOIN LATERAL (
      SELECT id, version, title, generated_at, generated_by
        FROM project_document_versions
       WHERE document_id = d.id
       ORDER BY version DESC
       LIMIT 1
  ) v ON TRUE
 WHERE d.organization_id = $1 AND d.workspace_id = $2 AND d.project_id = $3
"""


@router.get(DOCUMENTS_PATH)
async def list_documents(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Indeks dokumentów projektu: rola, tytuł, wersja, data, źródło i liczba zdań."""
    await _project(request, operator, workspace_id, project_id)
    pool = await pool_for(request.app)
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            _INDEX_SQL, operator.organization_id, workspace_id, project_id
        )
    # Sortowanie po stronie serwera, nie w SQL: kolejność ról jest decyzją produktu, a nie
    # własnością danych, i ma być widoczna tam, gdzie ją podjęto.
    order = {role: index for index, role in enumerate(ROLE_ORDER)}
    entries = sorted(
        (_entry(row) for row in rows),
        key=lambda entry: (order.get(entry["rola"], len(order)), entry["rola"]),
    )
    return {"dokumenty": entries}


@router.get(DOCUMENTS_PATH + "/{document_id}")
async def read_document(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    document_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    wersja: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """Jedna wersja dokumentu ze zdaniami i podstawą każdego zdania.

    Bez `?wersja=` zwraca najnowszą. Z numerem, którego nie ma, odpowiada 404 ZE ZDANIEM mówiącym,
    która wersja jest najnowsza: pusta odpowiedź na pytanie o wersję 7 tam, gdzie są trzy, kazałaby
    ekranowi zgadywać, czy dokument zniknął, czy tylko numer jest zły.
    """
    await _project(request, operator, workspace_id, project_id)
    pool = await pool_for(request.app)
    async with pool.acquire() as connection:
        document = await connection.fetchrow(
            "SELECT id, role, platform FROM project_documents "
            "WHERE id = $1 AND organization_id = $2 AND workspace_id = $3 AND project_id = $4",
            document_id,
            operator.organization_id,
            workspace_id,
            project_id,
        )
        if document is None:
            raise HTTPException(status_code=404, detail=f"nie ma dokumentu {document_id}")

        newest = await connection.fetchval(
            "SELECT max(version) FROM project_document_versions WHERE document_id = $1",
            document_id,
        )
        if newest is None:
            # Tożsamość dokumentu bez ani jednej wersji: generator jej nie domknął. Stan rzadki
            # i nazwany, bo „dokument bez treści" i „nie ma takiego dokumentu" to dwie różne rzeczy.
            raise HTTPException(
                status_code=404,
                detail=f"dokument {document_id} nie ma jeszcze żadnej wersji",
            )
        wanted = wersja if wersja is not None else int(newest)
        version = await connection.fetchrow(
            "SELECT id, version, title, generated_at, generated_by, item_count "
            "FROM project_document_versions WHERE document_id = $1 AND version = $2",
            document_id,
            wanted,
        )
        if version is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"dokument {document_id} nie ma wersji {wanted}; najnowsza to {int(newest)}"
                ),
            )
        # Pozycja jest liczona OD ZERA (więz z 0028) i jest unikalna w wersji, więc `ORDER BY`
        # daje dokładnie kolejność, w jakiej generator ułożył dokument. `section` bywa pustym
        # napisem, a nie NULL-em: nie każdy dokument dzieli się na części.
        sentences = await connection.fetch(
            "SELECT position, section, text, basis FROM project_document_sentences "
            "WHERE version_id = $1 ORDER BY position",
            version["id"],
        )

    return {
        "document_id": str(document_id),
        "rola": document["role"],
        "platforma": document["platform"],
        "tytul": version["title"],
        "wersja": int(version["version"]),
        "wygenerowano": version["generated_at"].isoformat(),
        "zrodlo": version["generated_by"],
        "liczba_zdan": len(sentences),
        "zdania": [
            {
                "pozycja": int(row["position"]),
                # Pusta sekcja wraca jako `null`, nie jako pusty napis: ekran ma nie rysować
                # nagłówka bez tekstu, a „brak sekcji" jest stanem, nie napisem zerowej długości.
                "sekcja": row["section"] or None,
                "tekst": row["text"],
                # Podstawa jedzie CAŁA, nie jako sama liczba: ekran pokazuje liczbę, ale sesja
                # i człowiek, który chce sprawdzić zdanie, potrzebują identyfikatorów.
                "podstawa": [str(one) for one in (row["basis"] or [])],
                "liczba_podstaw": len(row["basis"] or []),
            }
            for row in sentences
        ],
    }
