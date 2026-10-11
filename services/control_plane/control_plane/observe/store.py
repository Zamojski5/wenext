"""Reads and writes of the eight tables of migration 0004. asyncpg, SQL in one place.

Every method takes the tenant explicitly (`organization_id`, and `workspace_id` where the table
is workspace-scoped) and binds it in SQL. With RLS deliberately off (E3), this `WHERE` is the
only thing separating organizations — the same finding the F1 commit message records for the
older stores, so it is written the same way here and tested with a second tenant.

Two shapes worth knowing before touching this file:

* **Observations are upserts, snapshots are inserts.** A thread read twice is one row whose
  `last_observed_at`, text and digest move forward (`ON CONFLICT … DO UPDATE`); its metrics at
  each read are a NEW `thread_metric_snapshots` row, and the trigger from 0004 refuses to touch
  an old one. `first_observed_at` never moves.
* **`Observed[int]` is two columns.** `_observed_columns` splits one into `(value, reason)` on
  the way in and `_observed` joins them on the way out. Nothing in between ever sees a bare 0
  standing for "unknown".
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from community_observer import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
)
from community_os_contracts import (
    AccessMode,
    JudgmentDecision,
    JudgmentFeedbackVerdict,
    JudgmentRecord,
    ObservationPlatform,
    Observed,
    UnknownReason,
)

from .context import CommentContext, ThreadContext
from .text_rules import assert_no_long_dashes
from .voice import CommentVoice


@dataclass(frozen=True)
class CommunityRow:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    platform: ObservationPlatform
    community_key: str
    display_name: str
    access_mode: AccessMode
    observe_enabled: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None
    watermark_created_at: datetime | None
    created_at: datetime
    #: 0011 (D-032): whether every thread here earns a judgment and metric snapshots (a model call
    #: per thread), or the community is read to learn how people write in it, which costs the
    #: listing plus the daily picture and voice card. False for communities entered to learn.
    thread_analysis_enabled: bool = True
    #: 0025: czy CZŁOWIEK dopuścił zakładanie tu własnych wątków. `None` znaczy „nikt nie
    #: zdecydował" i jest odmową, tak samo jak `False`, ale mówi co innego: przy `None` człowiek
    #: ma zdecydować, przy `False` nie ma nic do roboty.
    own_posts_allowed: bool | None = None
    own_posts_decided_by: UUID | None = None
    own_posts_decided_at: datetime | None = None

    @property
    def ref(self) -> str:
        return f"{self.platform.value}:{self.community_key}"


@dataclass(frozen=True)
class ThreadRow:
    id: UUID
    community_id: UUID
    external_id: str
    url: str
    link_url: str | None
    title: str
    body: str | None
    author: str | None
    created_at_source: datetime
    first_observed_at: datetime
    last_observed_at: datetime
    raw_sha256: str


@dataclass(frozen=True)
class ProfileRow:
    id: UUID
    version: int
    knows_deeply: str
    positions: str
    would_never_claim: str
    voice_samples: str
    created_at: datetime

    def as_mapping(self) -> dict[str, str]:
        return {
            "knows_deeply": self.knows_deeply,
            "positions": self.positions,
            "would_never_claim": self.would_never_claim,
            "voice_samples": self.voice_samples,
        }


@dataclass(frozen=True)
class PictureRow:
    id: UUID
    community_id: UUID
    version: int
    window_start: datetime
    window_end: datetime
    content: dict[str, Any]
    thread_count: int
    invalid_ref_count: int
    model: str
    effort: str
    prompt_name: str
    prompt_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class VoiceCardRow:
    """One version of a community's voice card (0005) — the picture's shape, comments instead of
    threads: `comment_count` went in, `invalid_quote_count` citations pointed outside the sample."""

    id: UUID
    community_id: UUID
    version: int
    window_start: datetime
    window_end: datetime
    content: dict[str, Any]
    comment_count: int
    invalid_quote_count: int
    model: str
    effort: str
    prompt_name: str
    prompt_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class JudgmentRow:
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    thread_ref: str
    thread_title: str
    thread_url: str
    community_display_name: str
    decision: JudgmentDecision
    reasoning: str
    what_user_could_add: str | None
    confidence: float
    user_profile_version: int
    model: str
    effort: str
    prompt_name: str
    prompt_sha256: str
    created_at: datetime
    latest_feedback: JudgmentFeedbackVerdict | None
    latest_feedback_note: str | None
    latest_feedback_at: datetime | None


def _observed_columns(observed: Observed[int]) -> tuple[int | None, str | None]:
    if observed.is_known:
        return observed.value, None
    assert observed.unknown_reason is not None
    return None, observed.unknown_reason.value


def _observed(value: int | None, reason: str | None) -> Observed[int]:
    if value is not None:
        return Observed[int].known(value)
    if reason is None:
        # Cannot happen under the 0004 CHECK; if it does, say so rather than invent a reason.
        raise ValueError("snapshot bez wartości i bez powodu — CHECK z migracji 0004 nie zadziałał")
    return Observed[int].unknown(UnknownReason(reason))


def _community(row: asyncpg.Record) -> CommunityRow:
    return CommunityRow(
        id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        platform=ObservationPlatform(row["platform"]),
        community_key=row["community_key"],
        display_name=row["display_name"],
        access_mode=AccessMode(row["access_mode"]),
        observe_enabled=row["observe_enabled"],
        poll_interval_seconds=row["poll_interval_seconds"],
        last_polled_at=row["last_polled_at"],
        watermark_created_at=row["watermark_created_at"],
        created_at=row["created_at"],
        thread_analysis_enabled=row["thread_analysis_enabled"],
        own_posts_allowed=row["own_posts_allowed"],
        own_posts_decided_by=row["own_posts_decided_by"],
        own_posts_decided_at=row["own_posts_decided_at"],
    )


def _thread(row: asyncpg.Record) -> ThreadRow:
    return ThreadRow(
        id=row["id"],
        community_id=row["community_id"],
        external_id=row["external_id"],
        url=row["url"],
        link_url=row["link_url"],
        title=row["title"],
        body=row["body"],
        author=row["author"],
        created_at_source=row["created_at_source"],
        first_observed_at=row["first_observed_at"],
        last_observed_at=row["last_observed_at"],
        raw_sha256=row["raw_sha256"],
    )


def _voice_card(row: asyncpg.Record) -> VoiceCardRow:
    content = row["content"]
    return VoiceCardRow(
        id=row["id"],
        community_id=row["community_id"],
        version=row["version"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        content=json.loads(content) if isinstance(content, str) else dict(content),
        comment_count=row["comment_count"],
        invalid_quote_count=row["invalid_quote_count"],
        model=row["model"],
        effort=row["effort"],
        prompt_name=row["prompt_name"],
        prompt_sha256=row["prompt_sha256"],
        created_at=row["created_at"],
    )


def _picture(row: asyncpg.Record) -> PictureRow:
    content = row["content"]
    return PictureRow(
        id=row["id"],
        community_id=row["community_id"],
        version=row["version"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        content=json.loads(content) if isinstance(content, str) else dict(content),
        thread_count=row["thread_count"],
        invalid_ref_count=row["invalid_ref_count"],
        model=row["model"],
        effort=row["effort"],
        prompt_name=row["prompt_name"],
        prompt_sha256=row["prompt_sha256"],
        created_at=row["created_at"],
    )


_COMMUNITY_COLUMNS = """
    id, organization_id, workspace_id, platform, community_key, display_name, access_mode,
    observe_enabled, poll_interval_seconds, last_polled_at, watermark_created_at, created_at,
    thread_analysis_enabled, own_posts_allowed, own_posts_decided_by, own_posts_decided_at
"""

_THREAD_COLUMNS = """
    id, community_id, external_id, url, link_url, title, body, author, created_at_source,
    first_observed_at, last_observed_at, raw_sha256
"""

_JUDGMENT_SQL = """
    SELECT j.id, j.workspace_id, j.thread_id, j.decision, j.reasoning, j.what_user_could_add,
           j.confidence,
           j.model, j.effort, j.prompt_name, j.prompt_sha256, j.created_at,
           t.title AS thread_title, t.url AS thread_url, t.external_id,
           c.platform, c.community_key, c.display_name AS community_display_name,
           p.version AS user_profile_version,
           f.verdict AS latest_feedback, f.note AS latest_feedback_note,
           f.created_at AS latest_feedback_at
    FROM thread_judgments j
    JOIN observed_threads t ON t.id = j.thread_id AND t.organization_id = j.organization_id
    JOIN communities c ON c.id = t.community_id AND c.organization_id = t.organization_id
    JOIN user_profile_versions p ON p.id = j.user_profile_version_id
    LEFT JOIN LATERAL (
        SELECT verdict, note, created_at FROM judgment_feedback
        WHERE judgment_id = j.id ORDER BY created_at DESC LIMIT 1
    ) f ON true
    WHERE j.organization_id = $1
"""


def _judgment(row: asyncpg.Record) -> JudgmentRow:
    return JudgmentRow(
        id=row["id"],
        workspace_id=row["workspace_id"],
        thread_id=row["thread_id"],
        thread_ref=f"{row['platform']}:{row['community_key']}:{row['external_id']}",
        thread_title=row["thread_title"],
        thread_url=row["thread_url"],
        community_display_name=row["community_display_name"],
        decision=JudgmentDecision(row["decision"]),
        reasoning=row["reasoning"],
        what_user_could_add=row["what_user_could_add"],
        confidence=row["confidence"],
        user_profile_version=row["user_profile_version"],
        model=row["model"],
        effort=row["effort"],
        prompt_name=row["prompt_name"],
        prompt_sha256=row["prompt_sha256"],
        created_at=row["created_at"],
        latest_feedback=(
            JudgmentFeedbackVerdict(row["latest_feedback"]) if row["latest_feedback"] else None
        ),
        latest_feedback_note=row["latest_feedback_note"],
        latest_feedback_at=row["latest_feedback_at"],
    )


class ObservationStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- tenant -------------------------------------------------------------------

    async def sole_workspace(self, organization_id: UUID) -> UUID | None:
        """The one workspace of E1's one organization, or `None` when there is not exactly one."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM workspaces WHERE organization_id = $1 ORDER BY created_at LIMIT 2",
                organization_id,
            )
        return rows[0]["id"] if len(rows) == 1 else None

    # -- communities -----------------------------------------------------------------

    async def upsert_community(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        ref: CommunityRef,
        poll_interval_seconds: int,
        thread_analysis_enabled: bool = True,
    ) -> CommunityRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, observe_enabled, poll_interval_seconds,
                     thread_analysis_enabled)
                VALUES ($1, $2, $3, $4, $5, $6, true, $7, $8)
                ON CONFLICT (organization_id, workspace_id, platform, community_key) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        access_mode = EXCLUDED.access_mode,
                        observe_enabled = true,
                        poll_interval_seconds = EXCLUDED.poll_interval_seconds,
                        thread_analysis_enabled = EXCLUDED.thread_analysis_enabled
                RETURNING {_COMMUNITY_COLUMNS}
                """,
                organization_id,
                workspace_id,
                ref.platform.value,
                ref.key,
                ref.display_name,
                ref.access_mode.value,
                poll_interval_seconds,
                thread_analysis_enabled,
            )
        return _community(row)

    async def list_communities(self, *, organization_id: UUID) -> tuple[CommunityRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_COMMUNITY_COLUMNS} FROM communities WHERE organization_id = $1 "
                "ORDER BY created_at",
                organization_id,
            )
        return tuple(_community(r) for r in rows)

    async def collector_state(self, *, organization_id: UUID, workspace_id: UUID) -> dict[str, Any]:
        """Czy KOLEKTOR tej przestrzeni czyta, i kiedy czytał ostatnio (E6).

        PO CO TO JEST. Stan „24/7" projektu odpowiadał dotąd czterema słowami o AGENCIE
        (niepowierzony, wstrzymany, pracuje, czuwa) i nie miał słowa na to, że w tym samym czasie
        kolektor pobiera wątki. Człowiek czytał „czuwa" i rozumiał „nic się nie dzieje", choć
        działo się to, po co produkt w ogóle stoi (audyt UX 20.09, punkt 6).

        KOLEKTOR NALEŻY DO PRZESTRZENI, NIE DO PROJEKTU, i dlatego ta liczba wraca pod własną
        nazwą, a nie w stanie projektu: jedna obserwowana społeczność karmi wszystkie projekty
        przestrzeni, więc dopisanie jej pracy do jednego z nich byłoby tą samą pomyłką, co
        pokazywanie 38 cudzych publikacji pod nazwą nowego projektu.

        `ostatni_odczyt` równe `None` przy `obserwowanych > 0` znaczy „włączono i jeszcze nic nie
        pobrano", czyli trzecią odpowiedź, nie zero.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE observe_enabled) AS observed,
                       count(*) AS known,
                       max(last_polled_at) FILTER (WHERE observe_enabled) AS last_polled
                  FROM communities
                 WHERE organization_id = $1 AND workspace_id = $2
                """,
                organization_id,
                workspace_id,
            )
        last = row["last_polled"] if row is not None else None
        return {
            "obserwowanych": int(row["observed"] or 0) if row is not None else 0,
            "znanych": int(row["known"] or 0) if row is not None else 0,
            "ostatni_odczyt": last.isoformat() if last else None,
        }

    async def set_poll_interval(self, community_id: UUID, seconds: int) -> None:
        """The pilot slows the community's poll for its window and puts it back after."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE communities SET poll_interval_seconds = $2 WHERE id = $1",
                community_id,
                seconds,
            )

    async def community_by_key(
        self, *, organization_id: UUID, workspace_id: UUID, platform: str, community_key: str
    ) -> CommunityRow | None:
        """Ta społeczność w tym workspace, albo nic.

        Po KLUCZU, nie po identyfikatorze, bo tak o społeczności mówi człowiek i tak mówi o niej
        agent w rozmowie: „r/SaaS". Identyfikator jest wewnętrzny i nikt go nie wpisze.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COMMUNITY_COLUMNS} FROM communities "
                "WHERE organization_id = $1 AND workspace_id = $2 AND platform = $3 "
                "AND community_key = $4",
                organization_id,
                workspace_id,
                platform,
                community_key,
            )
        return _community(row) if row else None

    async def set_observing(
        self, *, organization_id: UUID, workspace_id: UUID, community_id: UUID, enabled: bool
    ) -> CommunityRow:
        """Włącz albo wyłącz obserwację. NIE kasuje tego, co już przeczytane.

        Wyłączenie to „przestań tu zaglądać", a nie „zapomnij, co tu było": wątki i komentarze
        zostają, bo publikacje i ustalenia, które na nich stoją, muszą mieć na czym stać.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE communities SET observe_enabled = $4 "
                "WHERE organization_id = $1 AND workspace_id = $2 AND id = $3 "
                f"RETURNING {_COMMUNITY_COLUMNS}",
                organization_id,
                workspace_id,
                community_id,
                enabled,
            )
        if row is None:
            raise LookupError(f"społeczność {community_id} nie istnieje w tym workspace")
        return _community(row)

    async def get_community(
        self, community_id: UUID, *, organization_id: UUID
    ) -> CommunityRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COMMUNITY_COLUMNS} FROM communities "
                "WHERE id = $1 AND organization_id = $2",
                community_id,
                organization_id,
            )
        return _community(row) if row else None

    async def decide_own_posts(
        self,
        community_id: UUID,
        *,
        organization_id: UUID,
        allowed: bool | None,
        operator_id: UUID,
        now: datetime,
        user_id: UUID | None = None,
    ) -> CommunityRow | None:
        """Zapisz decyzję człowieka o własnych wątkach w tej społeczności (0025).

        `allowed=None` COFA decyzję do stanu „nikt nie zdecydował" i razem z nią znika autor,
        bo więz w bazie nie dopuszcza podpisu pod brakiem decyzji. To nie jest to samo, co
        „nie wolno": cofnięcie mówi „przemyślmy to jeszcze raz", a odmowa mówi „przemyślane".

        Zwraca wiersz po zmianie albo `None`, gdy społeczności nie ma w tej organizacji. Wpis do
        dziennika robi wywołujący, bo tylko on wie, kto klikał i z jakiego ekranu.
        """
        from control_plane.approval.signers import resolve_signer

        async with self._pool.acquire() as conn, conn.transaction():
            operator_id, membership_id = await resolve_signer(
                conn, organization_id=organization_id, operator_id=operator_id, user_id=user_id,
            )
            row = await conn.fetchrow(
                f"""
                UPDATE communities
                   SET own_posts_allowed = $3::boolean,
                       -- RZUTOWANIA SĄ KONIECZNE, nie ozdobne: w `CASE` z gołym NULL-em w
                       -- drugiej gałęzi PostgreSQL wnioskuje typ z tego NULL-a i wychodzi mu
                       -- `text`, a kolumna jest `uuid`. Zmierzone: „column is of type uuid but
                       -- expression is of type text" na pięciu testach naraz.
                       own_posts_decided_by =
                           CASE WHEN $3::boolean IS NULL THEN NULL::uuid ELSE $4::uuid END,
                       own_posts_membership_id =
                           CASE WHEN $3::boolean IS NULL THEN NULL::uuid ELSE $6::uuid END,
                       own_posts_decided_at =
                           CASE
                               WHEN $3::boolean IS NULL THEN NULL::timestamptz
                               ELSE $5::timestamptz
                           END
                 WHERE id = $1 AND organization_id = $2
                RETURNING {_COMMUNITY_COLUMNS}
                """,
                community_id,
                organization_id,
                allowed,
                operator_id,
                now,
                membership_id,
            )
        return _community(row) if row else None

    async def set_polled(
        self,
        community_id: UUID,
        *,
        organization_id: UUID,
        polled_at: datetime,
        watermark: datetime | None,
    ) -> None:
        """Advance the watermark. It never moves backwards — `greatest` keeps the later one."""
        async with self._pool.acquire() as conn:
            updated = await conn.execute(
                """
                UPDATE communities
                SET last_polled_at = $3,
                    watermark_created_at = greatest(watermark_created_at, $4::timestamptz)
                WHERE id = $1 AND organization_id = $2
                """,
                community_id,
                organization_id,
                polled_at,
                watermark,
            )
        if updated != "UPDATE 1":
            raise LookupError(
                f"community {community_id} nie istnieje w organizacji {organization_id}"
            )

    # -- threads, comments, snapshots ------------------------------------------------

    async def record_thread(
        self, *, community: CommunityRow, thread: ObservedThread
    ) -> tuple[UUID, bool]:
        """Upsert one thread and append its metrics. Returns `(thread_id, created)`."""
        if (
            thread.platform is not community.platform
            or thread.community_key != community.community_key
        ):
            raise ValueError(
                f"wątek {thread.ref} nie należy do community {community.ref} — odmowa zapisu"
            )
        score, score_reason = _observed_columns(thread.metrics.score)
        count, count_reason = _observed_columns(thread.metrics.comment_count)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO observed_threads
                    (organization_id, workspace_id, community_id, external_id, url, link_url, title,
                     body, author, created_at_source, first_observed_at, last_observed_at,
                     raw_sha256)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11, $12)
                ON CONFLICT (organization_id, workspace_id, community_id, external_id) DO UPDATE
                    SET url = EXCLUDED.url,
                        link_url = EXCLUDED.link_url,
                        title = EXCLUDED.title,
                        body = EXCLUDED.body,
                        author = EXCLUDED.author,
                        last_observed_at = greatest(observed_threads.last_observed_at,
                                                    EXCLUDED.last_observed_at),
                        raw_sha256 = EXCLUDED.raw_sha256
                RETURNING id, (xmax = 0) AS created
                """,
                community.organization_id,
                community.workspace_id,
                community.id,
                thread.external_id,
                thread.url,
                thread.link_url,
                thread.title,
                thread.body,
                thread.author,
                thread.created_at,
                thread.observed_at,
                thread.raw_sha256,
            )
            await conn.execute(
                """
                INSERT INTO thread_metric_snapshots
                    (organization_id, workspace_id, thread_id, observed_at,
                     score, score_unknown_reason, comment_count, comment_count_unknown_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                community.organization_id,
                community.workspace_id,
                row["id"],
                thread.observed_at,
                score,
                score_reason,
                count,
                count_reason,
            )
        return row["id"], bool(row["created"])

    async def record_comments(
        self, *, community: CommunityRow, thread_id: UUID, comments: Sequence[ObservedComment]
    ) -> int:
        """Upsert the comments of one thread. Returns how many rows were written."""
        if not comments:
            return 0
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.executemany(
                """
                INSERT INTO observed_comments
                    (organization_id, workspace_id, thread_id, external_id, parent_external_id,
                     author, body, created_at_source, observed_at, depth,
                     score, score_unknown_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (organization_id, workspace_id, thread_id, external_id) DO UPDATE
                    SET parent_external_id = EXCLUDED.parent_external_id,
                        author = EXCLUDED.author,
                        body = EXCLUDED.body,
                        observed_at = EXCLUDED.observed_at,
                        depth = EXCLUDED.depth,
                        score = EXCLUDED.score,
                        score_unknown_reason = EXCLUDED.score_unknown_reason
                """,
                [
                    (
                        community.organization_id,
                        community.workspace_id,
                        thread_id,
                        c.external_id,
                        c.parent_external_id,
                        c.author,
                        c.body,
                        c.created_at,
                        c.observed_at,
                        c.depth,
                        c.score.value,
                        c.score.unknown_reason.value if c.score.unknown_reason else None,
                    )
                    for c in comments
                ],
            )
        return len(comments)

    async def append_snapshot(
        self,
        *,
        community: CommunityRow,
        thread_id: UUID,
        observed_at: datetime,
        metrics: ThreadMetrics,
    ) -> None:
        score, score_reason = _observed_columns(metrics.score)
        count, count_reason = _observed_columns(metrics.comment_count)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO thread_metric_snapshots
                    (organization_id, workspace_id, thread_id, observed_at,
                     score, score_unknown_reason, comment_count, comment_count_unknown_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                community.organization_id,
                community.workspace_id,
                thread_id,
                observed_at,
                score,
                score_reason,
                count,
                count_reason,
            )

    async def get_thread(self, thread_id: UUID, *, organization_id: UUID) -> ThreadRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_THREAD_COLUMNS} FROM observed_threads "
                "WHERE id = $1 AND organization_id = $2",
                thread_id,
                organization_id,
            )
        return _thread(row) if row else None

    async def _contexts(
        self, conn: asyncpg.Connection, *, organization_id: UUID, rows: Sequence[asyncpg.Record]
    ) -> tuple[ThreadContext, ...]:
        if not rows:
            return ()
        thread_ids = [r["id"] for r in rows]
        comment_rows = await conn.fetch(
            """
            SELECT thread_id, external_id, parent_external_id, author, body,
                   created_at_source, depth, score, score_unknown_reason
            FROM observed_comments
            WHERE organization_id = $1 AND thread_id = ANY($2::uuid[])
            ORDER BY created_at_source, depth, external_id
            """,
            organization_id,
            thread_ids,
        )
        snapshot_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (thread_id) thread_id, score, score_unknown_reason,
                   comment_count, comment_count_unknown_reason
            FROM thread_metric_snapshots
            WHERE organization_id = $1 AND thread_id = ANY($2::uuid[])
            ORDER BY thread_id, observed_at DESC
            """,
            organization_id,
            thread_ids,
        )
        comments: dict[UUID, list[CommentContext]] = {tid: [] for tid in thread_ids}
        for c in comment_rows:
            comments[c["thread_id"]].append(
                CommentContext(
                    external_id=c["external_id"],
                    parent_external_id=c["parent_external_id"],
                    author=c["author"],
                    body=c["body"],
                    created_at=c["created_at_source"],
                    depth=c["depth"],
                    score=_observed(c["score"], c["score_unknown_reason"]),
                )
            )
        latest = {s["thread_id"]: s for s in snapshot_rows}
        contexts = []
        for r in rows:
            snapshot = latest.get(r["id"])
            if snapshot is None:
                score = Observed[int].unknown(
                    UnknownReason.ADAPTER_COULD_NOT_PARSE, "brak snapshotu"
                )
                count = score
            else:
                score = _observed(snapshot["score"], snapshot["score_unknown_reason"])
                count = _observed(
                    snapshot["comment_count"], snapshot["comment_count_unknown_reason"]
                )
            contexts.append(
                ThreadContext(
                    ref=f"{r['platform']}:{r['community_key']}:{r['external_id']}",
                    external_id=r["external_id"],
                    url=r["url"],
                    link_url=r["link_url"],
                    title=r["title"],
                    body=r["body"],
                    author=r["author"],
                    created_at=r["created_at_source"],
                    score=score,
                    comment_count=count,
                    comments=tuple(comments[r["id"]]),
                )
            )
        return tuple(contexts)

    _THREAD_WITH_COMMUNITY_SQL = """
        SELECT t.id, t.external_id, t.url, t.link_url, t.title, t.body, t.author,
               t.created_at_source,
               c.platform, c.community_key
        FROM observed_threads t
        JOIN communities c ON c.id = t.community_id AND c.organization_id = t.organization_id
        WHERE t.organization_id = $1
    """

    async def thread_context(
        self, thread_id: UUID, *, organization_id: UUID
    ) -> ThreadContext | None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                self._THREAD_WITH_COMMUNITY_SQL + " AND t.id = $2", organization_id, thread_id
            )
            contexts = await self._contexts(conn, organization_id=organization_id, rows=rows)
        return contexts[0] if contexts else None

    async def threads_in_window(
        self, *, community: CommunityRow, window_start: datetime, window_end: datetime
    ) -> tuple[ThreadContext, ...]:
        """Threads CREATED on the platform inside the window, with comments and latest metrics."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                self._THREAD_WITH_COMMUNITY_SQL
                + " AND t.community_id = $2 AND t.created_at_source >= $3"
                + " AND t.created_at_source < $4"
                " ORDER BY t.created_at_source DESC",
                community.organization_id,
                community.id,
                window_start,
                window_end,
            )
            return await self._contexts(conn, organization_id=community.organization_id, rows=rows)

    async def liveliest_threads(
        self,
        community_id: UUID,
        *,
        organization_id: UUID,
        since: datetime,
        limit: int,
    ) -> tuple[str, ...]:
        """External ids of the community's threads created since `since`, the ones the platform's
        listing says drew the most comments first (D-033). Used to pick the few threads worth
        reading in full for a community we only listen to: a thread nobody answered teaches
        nothing about how people write here."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.external_id
                FROM observed_threads t
                WHERE t.community_id = $1 AND t.organization_id = $2
                  AND t.created_at_source >= $3
                ORDER BY COALESCE((
                    SELECT s.comment_count FROM thread_metric_snapshots s
                    WHERE s.thread_id = t.id AND s.comment_count IS NOT NULL
                    ORDER BY s.observed_at DESC LIMIT 1
                ), 0) DESC, t.created_at_source DESC
                LIMIT $4
                """,
                community_id,
                organization_id,
                since,
                limit,
            )
        return tuple(r["external_id"] for r in rows)

    async def unjudged_thread_ids(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        first_observed_since: datetime,
        exclude_community_ids: Sequence[UUID] = (),
    ) -> tuple[UUID, ...]:
        """Threads seen since `first_observed_since` that no judgment mentions yet, outside the
        communities in `exclude_community_ids` (a community with an active 24 h pilot: its model
        calls belong to the pilot's budget, not to the judge) and outside communities whose
        `thread_analysis_enabled` is false (0011, D-032: entered to learn how people write there,
        so they cost a listing and a daily card, never a model call per thread; without this the
        workspace-wide sweep turned eleven new communities into 676 judgments in one poll)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.id FROM observed_threads t
                JOIN communities c
                  ON c.id = t.community_id AND c.organization_id = t.organization_id
                WHERE t.organization_id = $1 AND t.workspace_id = $2
                  AND c.observe_enabled
                  AND c.thread_analysis_enabled
                  AND t.first_observed_at >= $3
                  AND NOT (t.community_id = ANY($4::uuid[]))
                  AND NOT EXISTS (SELECT 1 FROM thread_judgments j WHERE j.thread_id = t.id)
                ORDER BY t.first_observed_at
                """,
                organization_id,
                workspace_id,
                first_observed_since,
                list(exclude_community_ids),
            )
        return tuple(r["id"] for r in rows)

    async def counts(self, *, organization_id: UUID, community_id: UUID) -> dict[str, int]:
        """How much has been observed — for the screen, so "nothing yet" is a measured zero."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM observed_threads
                    WHERE organization_id = $1 AND community_id = $2) AS threads,
                  (SELECT count(*) FROM observed_comments oc
                    JOIN observed_threads t ON t.id = oc.thread_id
                    WHERE oc.organization_id = $1 AND t.community_id = $2) AS comments,
                  (SELECT count(*) FROM thread_metric_snapshots s
                    JOIN observed_threads t ON t.id = s.thread_id
                    WHERE s.organization_id = $1 AND t.community_id = $2) AS snapshots,
                  (SELECT count(*) FROM thread_judgments j
                    JOIN observed_threads t ON t.id = j.thread_id
                    WHERE j.organization_id = $1 AND t.community_id = $2) AS judgments,
                  (SELECT count(*) FROM community_pictures
                    WHERE organization_id = $1 AND community_id = $2) AS pictures
                """,
                organization_id,
                community_id,
            )
        return {
            k: int(row[k]) for k in ("threads", "comments", "snapshots", "judgments", "pictures")
        }

    # -- pictures -------------------------------------------------------------------

    async def latest_picture(
        self, community_id: UUID, *, organization_id: UUID
    ) -> PictureRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, community_id, version, window_start, window_end, content, thread_count,
                       invalid_ref_count, model, effort, prompt_name, prompt_sha256, created_at
                FROM community_pictures
                WHERE community_id = $1 AND organization_id = $2
                ORDER BY version DESC LIMIT 1
                """,
                community_id,
                organization_id,
            )
        return _picture(row) if row else None

    async def save_picture(
        self,
        *,
        community: CommunityRow,
        window_start: datetime,
        window_end: datetime,
        content: dict[str, Any],
        thread_count: int,
        invalid_ref_count: int,
        model: str,
        effort: str,
        prompt_name: str,
        prompt_sha256: str,
    ) -> PictureRow:
        """The next version of the community's picture. Versions come from the database, in one
        statement, so two pictures drawn at once cannot both be `version 3`."""
        assert_no_long_dashes(content, where="community_pictures.content")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO community_pictures
                    (organization_id, workspace_id, community_id, version, window_start, window_end,
                     content, thread_count, invalid_ref_count, model, effort, prompt_name,
                     prompt_sha256)
                VALUES ($1, $2, $3,
                        (SELECT coalesce(max(version), 0) + 1 FROM community_pictures
                          WHERE organization_id = $1 AND workspace_id = $2 AND community_id = $3),
                        $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
                RETURNING id, community_id, version, window_start, window_end, content,
                          thread_count,
                          invalid_ref_count, model, effort, prompt_name, prompt_sha256, created_at
                """,
                community.organization_id,
                community.workspace_id,
                community.id,
                window_start,
                window_end,
                json.dumps(content, ensure_ascii=False),
                thread_count,
                invalid_ref_count,
                model,
                effort,
                prompt_name,
                prompt_sha256,
            )
        return _picture(row)

    # -- voice card (0005, etap 3 M0) ---------------------------------------------------

    _VOICE_CARD_COLUMNS = (
        "id, community_id, version, window_start, window_end, content, comment_count, "
        "invalid_quote_count, model, effort, prompt_name, prompt_sha256, created_at"
    )

    async def latest_voice_card(
        self, community_id: UUID, *, organization_id: UUID
    ) -> VoiceCardRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {self._VOICE_CARD_COLUMNS}
                FROM community_voice_cards
                WHERE community_id = $1 AND organization_id = $2
                ORDER BY version DESC LIMIT 1
                """,
                community_id,
                organization_id,
            )
        return _voice_card(row) if row else None

    async def save_voice_card(
        self,
        *,
        community: CommunityRow,
        window_start: datetime,
        window_end: datetime,
        content: dict[str, Any],
        comment_count: int,
        invalid_quote_count: int,
        model: str,
        effort: str,
        prompt_name: str,
        prompt_sha256: str,
    ) -> VoiceCardRow:
        """The next version of the community's voice card; the version comes from the database in
        the same statement, like the picture's."""
        assert_no_long_dashes(content, where="community_voice_cards.content")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO community_voice_cards
                    (organization_id, workspace_id, community_id, version, window_start, window_end,
                     content, comment_count, invalid_quote_count, model, effort, prompt_name,
                     prompt_sha256)
                VALUES ($1, $2, $3,
                        (SELECT coalesce(max(version), 0) + 1 FROM community_voice_cards
                          WHERE organization_id = $1 AND workspace_id = $2 AND community_id = $3),
                        $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
                RETURNING {self._VOICE_CARD_COLUMNS}
                """,
                community.organization_id,
                community.workspace_id,
                community.id,
                window_start,
                window_end,
                json.dumps(content, ensure_ascii=False),
                comment_count,
                invalid_quote_count,
                model,
                effort,
                prompt_name,
                prompt_sha256,
            )
        return _voice_card(row)

    async def comments_in_window(
        self, *, community: CommunityRow, window_start: datetime, window_end: datetime
    ) -> tuple[CommentVoice, ...]:
        """Every comment WITH A BODY posted on the platform inside the window, in threads of this
        community, with the number of direct replies each drew (as observed so far). Deleted
        comments are evidence for the picture, not for the voice: nobody writes like a
        `[deleted]`."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.external_id, c.parent_external_id, c.author, c.body, c.created_at_source,
                       c.depth, c.score, c.score_unknown_reason,
                       t.external_id AS thread_external_id, t.title AS thread_title,
                       (SELECT count(*) FROM observed_comments r
                         WHERE r.thread_id = c.thread_id
                           AND r.parent_external_id = c.external_id) AS reply_count
                FROM observed_comments c
                JOIN observed_threads t ON t.id = c.thread_id
                WHERE c.organization_id = $1 AND t.community_id = $2
                  AND c.created_at_source >= $3 AND c.created_at_source < $4
                  AND c.body IS NOT NULL
                ORDER BY c.created_at_source, c.external_id
                """,
                community.organization_id,
                community.id,
                window_start,
                window_end,
            )
        return tuple(
            CommentVoice(
                external_id=r["external_id"],
                thread_ref=f"{community.platform.value}:{community.community_key}:"
                f"{r['thread_external_id']}",
                thread_title=r["thread_title"],
                parent_external_id=r["parent_external_id"],
                author=r["author"],
                body=r["body"],
                created_at=r["created_at_source"],
                depth=r["depth"],
                score=_observed(r["score"], r["score_unknown_reason"]),
                reply_count=int(r["reply_count"]),
            )
            for r in rows
        )

    # -- exemplars for the reaction prompt (v3) -------------------------------------------

    async def top_comments(
        self,
        community_id: UUID,
        *,
        organization_id: UUID,
        limit: int = 6,
        min_chars: int = 60,
        max_chars: int = 500,
        exclude_authors: Sequence[str] = (),
    ) -> tuple[str, ...]:
        """The community's most upvoted top-level comments, one per thread, as text only: no
        authors, no ids, clipped to `max_chars`. The register that gets read here, handed to the
        generator as `community.exemplars` (prompt v3). Comments with an unknown score, deleted
        bodies, very short bodies and the accounts in `exclude_authors` (ours) are left out."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (c.thread_id) c.body, c.score
                FROM observed_comments c
                JOIN observed_threads t ON t.id = c.thread_id
                WHERE t.community_id = $1 AND c.organization_id = $2
                  AND c.score IS NOT NULL AND c.depth = 0
                  AND c.body IS NOT NULL AND length(c.body) >= $3
                  AND NOT (lower(coalesce(c.author, '')) = ANY($4::text[]))
                ORDER BY c.thread_id, c.score DESC
                """,
                community_id,
                organization_id,
                min_chars,
                [a.lower() for a in exclude_authors],
            )
        best = sorted(rows, key=lambda r: int(r["score"]), reverse=True)[:limit]
        out = []
        for r in best:
            body = " ".join(str(r["body"]).split())
            out.append(body if len(body) <= max_chars else body[: max_chars - 1].rstrip() + "…")
        return tuple(out)

    # -- user profile ---------------------------------------------------------------

    async def latest_profile(
        self, *, organization_id: UUID, workspace_id: UUID
    ) -> ProfileRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, version, knows_deeply, positions, would_never_claim, voice_samples,
                       created_at
                FROM user_profile_versions
                WHERE organization_id = $1 AND workspace_id = $2
                ORDER BY version DESC LIMIT 1
                """,
                organization_id,
                workspace_id,
            )
        return ProfileRow(**dict(row)) if row else None

    async def save_profile(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        knows_deeply: str,
        positions: str,
        would_never_claim: str,
        voice_samples: str,
    ) -> ProfileRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_profile_versions
                    (organization_id, workspace_id, version, knows_deeply, positions,
                     would_never_claim, voice_samples)
                VALUES ($1, $2,
                        (SELECT coalesce(max(version), 0) + 1 FROM user_profile_versions
                          WHERE organization_id = $1 AND workspace_id = $2),
                        $3, $4, $5, $6)
                RETURNING id, version, knows_deeply, positions, would_never_claim, voice_samples,
                          created_at
                """,
                organization_id,
                workspace_id,
                knows_deeply,
                positions,
                would_never_claim,
                voice_samples,
            )
        return ProfileRow(**dict(row))

    # -- judgments and feedback -----------------------------------------------------

    async def save_judgment(
        self,
        *,
        community: CommunityRow,
        thread_id: UUID,
        record: JudgmentRecord,
        user_profile_version_id: UUID,
        community_picture_id: UUID | None,
    ) -> UUID:
        assert_no_long_dashes(
            [record.reasoning, record.what_user_could_add], where="thread_judgments"
        )
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO thread_judgments
                    (organization_id, workspace_id, thread_id, decision, reasoning,
                     what_user_could_add,
                     confidence, user_profile_version_id, community_picture_id, model, effort,
                     prompt_name, prompt_sha256, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                RETURNING id
                """,
                community.organization_id,
                community.workspace_id,
                thread_id,
                record.decision.value,
                record.reasoning,
                record.what_user_could_add,
                record.confidence,
                user_profile_version_id,
                community_picture_id,
                record.model,
                record.effort,
                record.prompt_name,
                record.prompt_sha256,
                record.created_at,
            )

    async def list_judgments(
        self, *, organization_id: UUID, decision: JudgmentDecision | None, limit: int
    ) -> tuple[JudgmentRow, ...]:
        sql = _JUDGMENT_SQL
        args: list[Any] = [organization_id]
        if decision is not None:
            sql += " AND j.decision = $2"
            args.append(decision.value)
        sql += f" ORDER BY j.created_at DESC LIMIT ${len(args) + 1}"
        args.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return tuple(_judgment(r) for r in rows)

    async def get_judgment(self, judgment_id: UUID, *, organization_id: UUID) -> JudgmentRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _JUDGMENT_SQL + " AND j.id = $2", organization_id, judgment_id
            )
        return _judgment(row) if row else None

    async def record_feedback(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        judgment_id: UUID,
        verdict: JudgmentFeedbackVerdict,
        note: str | None,
        connection: asyncpg.Connection | None = None,
    ) -> UUID:
        """One more feedback row. `connection` lets the API write it in the same transaction as
        the audit entry — a feedback without its audit row, or the reverse, is the asymmetry
        `AuditWriter` exists to prevent."""
        sql = """
            INSERT INTO judgment_feedback
                (organization_id, workspace_id, judgment_id, verdict, note)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        """
        args = (organization_id, workspace_id, judgment_id, verdict.value, note)
        if connection is not None:
            return await connection.fetchval(sql, *args)
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def agreement_summary(self, *, organization_id: UUID) -> dict[str, int]:
        """Counts for the screen: judgments per decision, feedback per verdict. Measured zeros."""
        async with self._pool.acquire() as conn:
            decisions = await conn.fetch(
                "SELECT decision, count(*) AS n FROM thread_judgments WHERE organization_id = $1 "
                "GROUP BY decision",
                organization_id,
            )
            verdicts = await conn.fetch(
                """
                SELECT f.verdict, count(*) AS n
                FROM judgment_feedback f
                WHERE f.organization_id = $1
                  AND f.created_at = (SELECT max(created_at) FROM judgment_feedback
                                       WHERE judgment_id = f.judgment_id)
                GROUP BY f.verdict
                """,
                organization_id,
            )
        summary = {f"decision_{d.value}": 0 for d in JudgmentDecision}
        summary.update({f"feedback_{v.value}": 0 for v in JudgmentFeedbackVerdict})
        for r in decisions:
            summary[f"decision_{r['decision']}"] = int(r["n"])
        for r in verdicts:
            summary[f"feedback_{r['verdict']}"] = int(r["n"])
        return summary
