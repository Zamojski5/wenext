"""Zapis i odczyt dokumentów projektu (P8 etap A, 0028).

DWIE ZASADY, KTÓRE TEN MODUŁ EGZEKWUJE, bo baza sama ich nie dopilnuje:

  1. ZDANIE WSKAZUJE ISTNIEJĄCE USTALENIA. `basis` jest tablicą uuid, a PostgreSQL nie zna kluczy
     obcych z elementów tablicy (powód zapisany w nagłówku 0028). Sprawdzenie idzie więc tutaj,
     W TEJ SAMEJ TRANSAKCJI co zapis: jedno zapytanie liczy, ile z podanych identyfikatorów
     naprawdę jest w rejestrze tej przestrzeni, i różnica jest odmową. Sprawdzenie po zapisie
     albo w osobnej transakcji byłoby sprawdzeniem, które przegrywa z równoczesnym wycofaniem.
  2. TEN SAM ODCISK WEJŚCIA NIE TWORZY NOWEJ WERSJI. Generator uruchomiony dziesięć razy bez
     zmian w rejestrze ma zostawić jedną wersję. `save` zwraca wtedy wersję istniejącą i mówi
     o tym wprost (`created=False`), zamiast po cichu nic nie robić.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg


class DocumentRefused(Exception):
    """Zapis, którego nie wolno wykonać. Odmowa z powodem, nie cicha porażka."""


@dataclass(frozen=True)
class DocumentVersion:
    """Jedna wersja dokumentu wraz ze zdaniami, tak jak ją czyta ekran i narzędzie."""

    document_id: UUID
    version_id: UUID
    role: str
    platform: str | None
    title: str
    version: int
    generated_by: str
    generated_at: datetime
    input_sha256: str
    item_count: int
    sentences: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DocumentIndexEntry:
    """Wpis indeksu: tyle, ile trzeba, żeby wiedzieć, CZY warto to otworzyć."""

    document_id: UUID
    role: str
    platform: str | None
    title: str
    version: int
    generated_at: datetime
    generated_by: str
    sentence_count: int
    item_count: int


class DocumentStore:
    """Dokumenty projektu. Trzyma pulę, nigdy połączenia, jak pozostałe store'y."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def index(
        self, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> tuple[DocumentIndexEntry, ...]:
        """Najnowsza wersja każdego dokumentu tego projektu, w stałej kolejności ról.

        Kolejność jest stała i zapisana w zapytaniu, a nie zostawiona bazie: indeks czyta model
        na starcie sesji, a lista, która za każdym razem układa się inaczej, wygląda jak zmiana
        stanu projektu, choć nic się nie zmieniło.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.id AS document_id, d.role, d.platform, v.title, v.version,
                       v.generated_at, v.generated_by, v.item_count,
                       (SELECT count(*) FROM project_document_sentences s
                         WHERE s.version_id = v.id) AS sentence_count
                  FROM project_documents d
                  JOIN LATERAL (
                      SELECT * FROM project_document_versions vv
                       WHERE vv.document_id = d.id AND vv.organization_id = d.organization_id
                       ORDER BY vv.version DESC LIMIT 1
                  ) v ON TRUE
                 WHERE d.organization_id = $1 AND d.workspace_id = $2 AND d.project_id = $3
                 ORDER BY array_position(
                     ARRAY['PROJECT_CARD','WHAT_WORKS','DECISIONS','COMMUNITIES',
                           'PLATFORM_HANDBOOK'], d.role)
                """,
                organization_id,
                workspace_id,
                project_id,
            )
        return tuple(
            DocumentIndexEntry(
                document_id=row["document_id"],
                role=row["role"],
                platform=row["platform"],
                title=row["title"],
                version=row["version"],
                generated_at=row["generated_at"],
                generated_by=row["generated_by"],
                sentence_count=int(row["sentence_count"] or 0),
                item_count=int(row["item_count"] or 0),
            )
            for row in rows
        )

    async def read(
        self,
        document_id: UUID,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID | None = None,
        version: int | None = None,
    ) -> DocumentVersion | None:
        """Jedna wersja ze zdaniami. Bez `version` najnowsza; nieistniejąca wersja to `None`.

        `project_id` ZAWĘŻA ODCZYT DO JEDNEGO PROJEKTU i wołający, który zna swój projekt, ma go
        podać. Dokument należy do projektu (`project_documents.project_id`, indeks po nim), a
        odczyt po samym identyfikatorze oddawał cudzy dokument tej samej przestrzeni: narzędzie
        modelu dostało tędy drogę do ustaleń projektu, nad którym nie pracuje. Trasa HTTP miała
        ten warunek od początku, bo projekt stoi u niej w ścieżce; narzędzie nie miało.

        `None` zostaje wartością dopuszczalną dla wołającego, który naprawdę pyta o przestrzeń,
        i wtedy zawężenia nie ma. Nie jest to domyślne „wszystko wolno", tylko jedyna uczciwa
        odpowiedź na pytanie zadane bez projektu.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT v.id AS version_id, d.id AS document_id, d.role, d.platform, v.title,
                       v.version, v.generated_by, v.generated_at, v.input_sha256, v.item_count
                  FROM project_documents d
                  JOIN project_document_versions v
                    ON v.document_id = d.id AND v.organization_id = d.organization_id
                 WHERE d.organization_id = $1 AND d.workspace_id = $2 AND d.id = $3
                   AND ($4::int IS NULL OR v.version = $4)
                   AND ($5::uuid IS NULL OR d.project_id = $5)
                 ORDER BY v.version DESC LIMIT 1
                """,
                organization_id,
                workspace_id,
                document_id,
                version,
                project_id,
            )
            if row is None:
                return None
            sentences = await conn.fetch(
                """
                SELECT position, section, text, basis
                  FROM project_document_sentences
                 WHERE organization_id = $1 AND version_id = $2
                 ORDER BY position
                """,
                organization_id,
                row["version_id"],
            )
        return DocumentVersion(
            document_id=row["document_id"],
            version_id=row["version_id"],
            role=row["role"],
            platform=row["platform"],
            title=row["title"],
            version=int(row["version"]),
            generated_by=row["generated_by"],
            generated_at=row["generated_at"],
            input_sha256=row["input_sha256"],
            item_count=int(row["item_count"]),
            sentences=tuple(
                {
                    "position": int(one["position"]),
                    "section": one["section"],
                    "text": one["text"],
                    "basis": [str(value) for value in (one["basis"] or [])],
                }
                for one in sentences
            ),
        )

    async def save(
        self,
        draft: Any,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> tuple[DocumentVersion, bool]:
        """Zapisz wersję dokumentu, albo oddaj istniejącą o tym samym odcisku wejścia.

        Zwraca parę (wersja, czy powstała nowa). `False` znaczy „rejestr się nie zmienił", a nie
        „nie udało się": to jest normalny wynik przy dziesiątym przebiegu tego samego dnia.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            document_id = await conn.fetchval(
                """
                INSERT INTO project_documents (organization_id, workspace_id, project_id, role)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (organization_id, workspace_id, project_id, role, platform)
                DO UPDATE SET role = EXCLUDED.role
                RETURNING id
                """,
                organization_id,
                workspace_id,
                project_id,
                draft.role,
            )
            latest = await conn.fetchrow(
                "SELECT id, version, input_sha256 FROM project_document_versions "
                "WHERE organization_id = $1 AND document_id = $2 "
                "ORDER BY version DESC LIMIT 1",
                organization_id,
                document_id,
            )
            if latest is not None and latest["input_sha256"] == draft.input_sha256:
                existing = await self.read(
                    document_id, organization_id=organization_id, workspace_id=workspace_id
                )
                assert existing is not None  # wiersz właśnie odczytany w tej transakcji
                return existing, False

            await self._verify_basis(conn, draft, organization_id, workspace_id)
            version_id = await conn.fetchval(
                """
                INSERT INTO project_document_versions
                    (organization_id, workspace_id, document_id, version, title, generated_by,
                     input_sha256, item_count)
                VALUES ($1, $2, $3, $4, $5, 'RULES', $6, $7)
                RETURNING id
                """,
                organization_id,
                workspace_id,
                document_id,
                int(latest["version"]) + 1 if latest is not None else 1,
                draft.title,
                draft.input_sha256,
                draft.item_count,
            )
            for position, sentence in enumerate(draft.sentences):
                await conn.execute(
                    """
                    INSERT INTO project_document_sentences
                        (organization_id, workspace_id, version_id, position, section, text, basis)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    organization_id,
                    workspace_id,
                    version_id,
                    position,
                    sentence.section,
                    sentence.text,
                    list(sentence.basis),
                )
        saved = await self.read(
            document_id, organization_id=organization_id, workspace_id=workspace_id
        )
        assert saved is not None
        return saved, True

    async def rebuild_project(
        self, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> tuple[tuple[Any, bool], ...]:
        """Przepisz komplet dokumentów tego projektu z jego rejestru. Jedno wejście dla wszystkich.

        UŻYWA JEJ ZASIEW E2E, test bazodanowy i (od etapu B) job w tle, i to jest celowe: zasiew
        zbudowany surowymi INSERT-ami potrafi stworzyć stan, którego produkt nigdy nie wyprodukuje,
        a wtedy ekran mierzy się na czymś, czego użytkownik nie zobaczy. Jedna droga do zapisu
        znaczy, że to, co widać w e2e, jest tym, co powstaje naprawdę.

        Zwraca pary (wersja, czy powstała nowa), po jednej na rolę, w kolejności generatora.
        """
        from control_plane.documents.generator import build_documents
        from control_plane.knowledge.store import KnowledgeStore, Scope

        scope = Scope(
            organization_id=organization_id, workspace_id=workspace_id, project_id=project_id
        )
        items = await KnowledgeStore(self._pool).list(scope, limit=1000)
        async with self._pool.acquire() as conn:
            project = await conn.fetchrow(
                "SELECT name, goal, mode FROM projects "
                "WHERE organization_id = $1 AND workspace_id = $2 AND id = $3",
                organization_id,
                workspace_id,
                project_id,
            )
        if project is None:
            raise DocumentRefused(f"nie ma projektu {project_id}; nie ma czego przepisywać")
        drafts = build_documents(
            list(items),
            project_name=project["name"],
            project_goal=project["goal"],
            project_mode=project["mode"],
        )
        saved = []
        for draft in drafts:
            saved.append(
                await self.save(
                    draft,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
            )
        return tuple(saved)

    async def _verify_basis(
        self, conn: asyncpg.Connection, draft: Any, organization_id: UUID, workspace_id: UUID
    ) -> None:
        """Każdy identyfikator w podstawie musi być ustaleniem TEJ przestrzeni.

        Jedno zapytanie na cały dokument, nie jedno na zdanie: sprawdzenie ma być tanie, żeby
        nikomu nie przyszło do głowy go pominąć. Ustalenie z cudzej przestrzeni jest tu tym samym,
        co ustalenie nieistniejące, i dostaje tę samą odmowę.
        """
        wanted = {one for sentence in draft.sentences for one in sentence.basis}
        if not wanted:
            return
        known = await conn.fetchval(
            "SELECT count(*) FROM knowledge_items "
            "WHERE organization_id = $1 AND workspace_id = $2 AND id = ANY($3::uuid[])",
            organization_id,
            workspace_id,
            list(wanted),
        )
        if int(known or 0) != len(wanted):
            raise DocumentRefused(
                f"dokument {draft.role} powołuje się na {len(wanted)} ustaleń, a w rejestrze tej "
                f"przestrzeni jest {known}; zdanie bez istniejącej podstawy nie jest widokiem na "
                "rejestr, tylko drugą pamięcią"
            )
