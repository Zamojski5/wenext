"""The memory of a workspace: what we read, and what we concluded. Migration 0016.

THIS IS THE INTERFACE THE MCP SERVER WRAPS (P2 owns `tools/`, this module owns the data). Four
methods, named for what a model asks rather than for what SQL does:

    await store.search(query, scope)          -> matching items and sources, each with provenance
    await store.read(source_id, offset=...)   -> the raw material, in slices, saying when it cut
    await store.write(item)                   -> one finding, refused when its kind's rule fails
    await store.list(scope)                   -> what currently holds in this scope

`Scope` is the tenant plus an optional project, and EVERY method takes one. There is no method
that reads across workspaces, because a tool exposed to a model must not have one: the model
decides what to ask, and the boundary has to hold whatever it asks for.

THREE RULES THE DATABASE ENFORCES AND THIS MODULE DOES NOT RE-IMPLEMENT (0016): a `FACT` needs a
`source_id`, a `PREFERENCE` must be `stated_by='USER'`, and an `EXPERIENCE` needs a payload with
`situation`, `decision` and `outcome`. `write` lets the CHECK refuse and turns the violation into
a `KnowledgeRefused` with the sentence — a pre-check in Python would be a second copy of a rule
that has to hold against every writer, including the ones that do not exist yet.

NOTHING READ HERE IS AN INSTRUCTION. P3: "materiały z zewnątrz nie stają się instrukcjami
systemowymi". `read` returns bytes with their locator, their fetch time and their hash, and the
caller passes them on as DATA. That is a property of how the result is used, not something this
module can enforce, so it is stated here and repeated where the tools are built.

TWO THINGS ETAP B ADDS, BOTH ABOUT NOT BEING SURE WHEN THERE IS NO GROUND TO BE:

  * **a finding whose material is old, gone or superseded comes back FLAGGED** (`staleness`).
    The flag is computed from the row, not stored, so it cannot go out of date on its own. The
    failure it exists to prevent is a confident answer read off a note nobody has checked in
    three months;
  * **two findings that hold at once and say different things come back AS A PAIR**
    (`conflicts`). Neither is chosen here. A store that picked one would be inventing an answer
    the data does not contain, and whichever it picked would look like the truth.

WHOSE FINDING IT IS: `person_profile_id` scopes a finding to one person, and a project sees it
only when an account of that person is assigned to that project (`_PERSON_VISIBLE_SQL`). This is
what keeps one person's style, preferences and experience out of another person's session. The
rule is in the query rather than in the caller, because a caller that has to remember it is a
caller that will one day forget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg


class KnowledgeRefused(Exception):
    """A write the domain refuses, with a sentence the operator can read (D-010)."""


@dataclass(frozen=True)
class Scope:
    """Whose knowledge, and how narrow.

    `project_id = None` means the whole workspace. It is NOT "any project": a read with no
    project returns the workspace's own material plus every project's, which is what an agent
    asking "what do we know" should see, while a read WITH a project returns that project's
    material plus the workspace-wide one and nothing from a sibling project.

    `project_id` HAS NO DEFAULT, and that is the point. The widest scope this type can express
    must be typed out, because a default of `None` means `Scope(organization_id=…,
    workspace_id=…)` quietly asks for everything — the broadest possible reading, obtained by
    forgetting an argument. Raised by the P2 session on 2026-09-19 while wiring the memory tools,
    and it was right. The deliberate case has a name: `Scope.whole_workspace(...)`.
    """

    organization_id: UUID
    workspace_id: UUID
    project_id: UUID | None

    @classmethod
    def whole_workspace(cls, organization_id: UUID, workspace_id: UUID) -> Scope:
        """Every project of this workspace, and the workspace's own material. Said out loud."""
        return cls(organization_id=organization_id, workspace_id=workspace_id, project_id=None)


@dataclass(frozen=True)
class SourceRow:
    id: UUID
    project_id: UUID | None
    kind: str
    locator: str
    title: str
    fetched_at: datetime | None
    content_hash: str | None
    status: str
    raw_artifact_id: UUID | None
    unavailable_reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class ItemRow:
    id: UUID
    project_id: UUID | None
    person_profile_id: UUID | None
    kind: str
    statement: str
    source_id: UUID | None
    source_span: str | None
    stated_by: str
    session_id: UUID | None
    payload: dict[str, Any]
    valid_from: datetime
    superseded_by: UUID | None
    retracted_at: datetime | None
    retracted_reason: str | None
    created_at: datetime

    @property
    def in_force(self) -> bool:
        """Whether this is what currently holds. Derived, never stored twice."""
        return self.superseded_by is None and self.retracted_at is None


@dataclass(frozen=True)
class NewItem:
    """One finding to write. The kind decides which other fields are required — see 0016."""

    kind: str
    statement: str
    stated_by: str
    project_id: UUID | None = None
    person_profile_id: UUID | None = None
    source_id: UUID | None = None
    source_span: str | None = None
    session_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Slice:
    """A window onto one source's raw material.

    `truncated` is the whole point of this type. A tool that returned the first N bytes and said
    nothing would let a model conclude from half a page and never know it had half — so the
    answer carries how much there is, where this piece started, and whether more follows.
    """

    source_id: UUID
    locator: str
    fetched_at: datetime | None
    content_hash: str | None
    offset: int
    length: int
    total_bytes: int
    truncated: bool
    text: str


#: How much of a source one `read` returns. Large enough for an article, small enough that a tool
#: result does not become the whole context window by itself.
DEFAULT_SLICE_BYTES = 20_000

_SOURCE_COLUMNS = """
    id, project_id, kind, locator, title, fetched_at, content_hash, status, raw_artifact_id,
    unavailable_reason, created_at
"""

_ITEM_COLUMNS = """
    id, project_id, person_profile_id, kind, statement, source_id, source_span, stated_by,
    session_id, payload, valid_from, superseded_by, retracted_at, retracted_reason, created_at
"""


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _source(row: asyncpg.Record) -> SourceRow:
    return SourceRow(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        locator=row["locator"],
        title=row["title"],
        fetched_at=row["fetched_at"],
        content_hash=row["content_hash"],
        status=row["status"],
        raw_artifact_id=row["raw_artifact_id"],
        unavailable_reason=row["unavailable_reason"],
        created_at=row["created_at"],
    )


def _item(row: asyncpg.Record) -> ItemRow:
    return ItemRow(
        id=row["id"],
        project_id=row["project_id"],
        person_profile_id=row["person_profile_id"],
        kind=row["kind"],
        statement=row["statement"],
        source_id=row["source_id"],
        source_span=row["source_span"],
        stated_by=row["stated_by"],
        session_id=row["session_id"],
        payload=_json(row["payload"]),
        valid_from=row["valid_from"],
        superseded_by=row["superseded_by"],
        retracted_at=row["retracted_at"],
        retracted_reason=row["retracted_reason"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class SearchHit:
    """One match, with what it stands on. Never a bare sentence."""

    item: ItemRow
    source: SourceRow | None


# ---------------------------------------------------------------------------
# Staleness: how old the ground under a finding is
# ---------------------------------------------------------------------------

#: A material older than this makes every finding standing on it suspect. Thirty days is a
#: judgement call and it is written down here rather than spread through the callers: a number
#: nobody can find is a number nobody can change.
STALE_AFTER = timedelta(days=30)


@dataclass(frozen=True)
class Staleness:
    """Whether a finding still stands on solid ground, and why not when it does not.

    `stale=False` with an empty reason is the ordinary case and says nothing reassuring on
    purpose: it means "nothing about this looks out of date", not "this was verified".
    """

    stale: bool
    reason: str
    #: Days since the material was read. `None` when there is no material or it was never read.
    source_age_days: float | None


def staleness(
    item: ItemRow,
    source: SourceRow | None,
    *,
    now: datetime | None = None,
    after: timedelta = STALE_AFTER,
) -> Staleness:
    """Four ways a finding can be standing on something that moved. Each says which.

    Computed, never stored. A stored flag would need somebody to come back and unset it, and
    "the job that marks things stale did not run" is indistinguishable from "nothing is stale".
    """
    moment = now or datetime.now(UTC)
    age = None
    if source is not None and source.fetched_at is not None:
        age = round((moment - source.fetched_at).total_seconds() / 86400, 2)

    if item.superseded_by is not None:
        return Staleness(True, "to ustalenie zostało poprawione; obowiązuje nowsze", age)
    if item.retracted_at is not None:
        reason = item.retracted_reason or "bez podanego powodu"
        return Staleness(True, f"ustalenie wycofane: {reason}", age)
    if source is None:
        return Staleness(False, "", None)
    if source.status == "UNAVAILABLE":
        why = source.unavailable_reason or "bez podanego powodu"
        return Staleness(True, f"materiału nie da się już przeczytać: {why}", age)
    if source.status == "STALE":
        return Staleness(True, "materiał oznaczony jako nieaktualny", age)
    if source.fetched_at is None:
        return Staleness(True, "materiał nigdy nie został przeczytany", None)
    if moment - source.fetched_at > after:
        return Staleness(
            True,
            f"materiał czytany {int(age or 0)} dni temu, próg to {after.days}; sprawdź, "
            "czy nadal jest aktualny",
            age,
        )
    return Staleness(False, "", age)


# ---------------------------------------------------------------------------
# Conflict: two findings that hold at once and disagree
# ---------------------------------------------------------------------------

#: The payload key that says what a finding is ABOUT. Two findings sharing it are two answers to
#: one question, which is the only pairing this module treats as certain.
ABOUT_KEY = "about"


@dataclass(frozen=True)
class Conflict:
    """Findings that all hold and do not agree. NOTHING here picks one.

    `about` is the key they share. `certain` is True when that key was written down by whoever
    recorded the findings, and False when the pairing was inferred by a caller (a query that
    matched both). The distinction travels to the model, because "these answer the same declared
    question" and "these both came back for the same phrase" are different claims.
    """

    about: str
    kind: str
    items: tuple[ItemRow, ...]
    certain: bool = True

    def sentence(self) -> str:
        return (
            f"Dwa ustalenia obowiązują naraz i mówią co innego o '{self.about}'. "
            "Nie wybieraj jednego po cichu: powiedz człowiekowi, że są sprzeczne, i zapytaj, "
            "które jest aktualne."
        )


def group_conflicts(items: tuple[ItemRow, ...] | list[ItemRow]) -> tuple[Conflict, ...]:
    """In-force findings that share an `about` key and do not say the same thing.

    Grouping by a DECLARED key rather than by similarity of wording. A heuristic over statements
    would be wrong in both directions at once, and a model told "these two contradict" by a
    guess would be misled more confidently than by no signal at all.
    """
    groups: dict[tuple[str, str], list[ItemRow]] = {}
    for item in items:
        if not item.in_force:
            continue
        about = str(item.payload.get(ABOUT_KEY) or "").strip().lower()
        if not about:
            continue
        groups.setdefault((about, item.kind), []).append(item)

    found: list[Conflict] = []
    for (about, kind), group in groups.items():
        if len({row.statement.strip().lower() for row in group}) < 2:
            continue
        found.append(
            Conflict(
                about=about,
                kind=kind,
                items=tuple(sorted(group, key=lambda row: row.created_at)),
            )
        )
    return tuple(sorted(found, key=lambda conflict: conflict.about))


#: Findings tied to a person are visible only where that person works: a project sees them when
#: an account of that profile is ASSIGNED to it and still attached to the profile. Written once,
#: as SQL, and shared by every read below — a rule each caller had to remember would be a rule
#: somebody eventually forgets, and the thing that leaks is one person's biography into another
#: person's session.
_PERSON_VISIBLE_SQL = """
    (
      i.person_profile_id IS NULL
      OR $PROJECT::uuid IS NULL
      OR EXISTS (
          SELECT 1
            FROM person_profile_accounts ppa
            JOIN project_account_assignments paa
              ON paa.organization_id = ppa.organization_id
             AND paa.workspace_id = ppa.workspace_id
             AND paa.social_account_id = ppa.social_account_id
             AND paa.unassigned_at IS NULL
             AND paa.project_id = $PROJECT
           WHERE ppa.organization_id = i.organization_id
             AND ppa.workspace_id = i.workspace_id
             AND ppa.person_profile_id = i.person_profile_id
             AND ppa.detached_at IS NULL
      )
    )
"""


def _person_visible(parameter: str) -> str:
    """The visibility rule with its placeholder bound to one query's parameter number."""
    return _PERSON_VISIBLE_SQL.replace("$PROJECT", parameter)


class KnowledgeStore:
    """Reads and writes of migration 0016. One pool, no cache, no cross-workspace query."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- the four methods the tools wrap ------------------------------------------

    async def search(
        self, query: str, scope: Scope, *, limit: int = 20, include_retired: bool = False
    ) -> tuple[SearchHit, ...]:
        """Findings matching `query` in this scope, newest first, each WITH its source.

        Plain case-insensitive containment over the statement, and that is a deliberate floor
        rather than a placeholder to be embarrassed about: full-text ranking would make "why did
        this come back" a question nobody can answer from the row, and P3 is judged on provenance,
        not on recall. When the corpus outgrows it, the replacement is a measurement away.

        `include_retired=False` means superseded and retracted findings stay out. An agent
        building context must not read a statement the user already corrected.
        """
        pattern = f"%{query.strip()}%"
        person = _person_visible("$3")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {", ".join("i." + name.strip() for name in _ITEM_COLUMNS.split(","))},
                       s.id AS s_id, s.project_id AS s_project_id, s.kind AS s_kind,
                       s.locator AS s_locator, s.title AS s_title, s.fetched_at AS s_fetched_at,
                       s.content_hash AS s_content_hash, s.status AS s_status,
                       s.raw_artifact_id AS s_raw_artifact_id,
                       s.unavailable_reason AS s_unavailable_reason,
                       s.created_at AS s_created_at
                FROM knowledge_items i
                LEFT JOIN knowledge_sources s
                       ON s.id = i.source_id
                      AND s.organization_id = i.organization_id
                      AND s.workspace_id = i.workspace_id
                WHERE i.organization_id = $1 AND i.workspace_id = $2
                  AND ($3::uuid IS NULL OR i.project_id IS NULL OR i.project_id = $3)
                  AND i.statement ILIKE $4
                  AND ($5 OR (i.superseded_by IS NULL AND i.retracted_at IS NULL))
                  AND {person}
                ORDER BY i.created_at DESC, i.id
                LIMIT $6
                """,
                scope.organization_id,
                scope.workspace_id,
                scope.project_id,
                pattern,
                include_retired,
                limit,
            )
        hits = []
        for row in rows:
            source = None
            if row["s_id"] is not None:
                source = SourceRow(
                    id=row["s_id"],
                    project_id=row["s_project_id"],
                    kind=row["s_kind"],
                    locator=row["s_locator"],
                    title=row["s_title"],
                    fetched_at=row["s_fetched_at"],
                    content_hash=row["s_content_hash"],
                    status=row["s_status"],
                    raw_artifact_id=row["s_raw_artifact_id"],
                    unavailable_reason=row["s_unavailable_reason"],
                    created_at=row["s_created_at"],
                )
            hits.append(SearchHit(item=_item(row), source=source))
        return tuple(hits)

    async def read(
        self,
        source_id: UUID,
        scope: Scope,
        *,
        offset: int = 0,
        length: int = DEFAULT_SLICE_BYTES,
        volume: Any = None,
    ) -> Slice | None:
        """One window onto a source's raw material, SAYING WHETHER IT CUT.

        `None` means there is no such source in this scope — a fact, not an error. A source whose
        bytes were never stored (a note, or a fetch that failed) comes back with `total_bytes = 0`
        and an empty text, which is different again: the row exists and says what happened.

        `volume` is an `ArtifactVolume`; passing it in rather than building one keeps this module
        free of deployment configuration and lets the tests point it at a temporary directory.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SOURCE_COLUMNS} FROM knowledge_sources"
                " WHERE organization_id = $1 AND workspace_id = $2 AND id = $3"
                " AND ($4::uuid IS NULL OR project_id IS NULL OR project_id = $4)",
                scope.organization_id,
                scope.workspace_id,
                source_id,
                scope.project_id,
            )
            if row is None:
                return None
            source = _source(row)
            content = b""
            if source.raw_artifact_id is not None and volume is not None:
                storage_ref = await conn.fetchval(
                    "SELECT storage_ref FROM artifacts WHERE organization_id = $1 AND id = $2",
                    scope.organization_id,
                    source.raw_artifact_id,
                )
                if storage_ref is not None:
                    try:
                        content = volume.read(storage_ref)
                    except OSError:
                        # The row says the bytes exist and they do not. That is worth reporting as
                        # an empty slice with the total it claims, never as "the source is empty".
                        content = b""

        total = len(content)
        start = max(0, offset)
        window = content[start : start + max(0, length)]
        return Slice(
            source_id=source.id,
            locator=source.locator,
            fetched_at=source.fetched_at,
            content_hash=source.content_hash,
            offset=start,
            length=len(window),
            total_bytes=total,
            truncated=start + len(window) < total,
            text=window.decode("utf-8", errors="replace"),
        )

    async def write(self, item: NewItem, scope: Scope) -> ItemRow:
        """Record one finding. The kind's rule is the database's, and a violation is a sentence.

        `CheckViolationError` is translated rather than propagated because the caller is often a
        tool answering a model: "fakt bez źródła" is something the model can act on, while a
        PostgreSQL error code is not.

        TO SAMO ZDANIE ZAPISANE DWA RAZY DAJE JEDEN WIERSZ i ten sam identyfikator (0027).
        Powód jest zmierzony: worker przerwany między `TOOL_CALL` a `TOOL_RESULT` powtarza turę,
        a strażnik powtórzeń w rejestrze narzędzi rozpoznaje powtórkę dopiero po tej parze, więc
        `memory.write` wykonywał się drugi raz. Sprawdzenie „czy już jest" PRZED zapisem
        przegrywa ten wyścig z definicji: dwie tury sprawdzają w tej samej chwili i obie widzą
        pustkę. Rozstrzyga indeks unikalny, a `ON CONFLICT DO NOTHING` plus odczyt istniejącego
        wiersza zamienia kolizję w idempotencję, nie w błąd. Powtórny zapis nie jest awarią:
        to jest ta sama myśl, zapisana drugi raz.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO knowledge_items
                        (organization_id, workspace_id, project_id, person_profile_id, kind,
                         statement, source_id, source_span, stated_by, session_id, payload)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    ON CONFLICT DO NOTHING
                    RETURNING {_ITEM_COLUMNS}
                    """,
                    scope.organization_id,
                    scope.workspace_id,
                    item.project_id if item.project_id is not None else scope.project_id,
                    item.person_profile_id,
                    item.kind,
                    item.statement,
                    item.source_id,
                    item.source_span,
                    item.stated_by,
                    item.session_id,
                    json.dumps(item.payload),
                )
                if row is None:
                    # Kolizja z indeksem 0027: ta treść już tu jest. Oddajemy istniejący wiersz,
                    # bo wołający (narzędzie odpowiadające modelowi) ma dostać identyfikator
                    # ustalenia, a nie wyjątek o czymś, co się udało komuś innemu sekundę temu.
                    row = await conn.fetchrow(
                        f"""
                        SELECT {_ITEM_COLUMNS} FROM knowledge_items
                         WHERE organization_id = $1 AND workspace_id = $2
                           AND project_id IS NOT DISTINCT FROM $3
                           AND person_profile_id IS NOT DISTINCT FROM $4
                           AND kind = $5
                           AND md5(lower(regexp_replace(btrim(statement), '\\s+', ' ', 'g')))
                               = md5(lower(regexp_replace(btrim($6::text), '\\s+', ' ', 'g')))
                           AND retracted_at IS NULL
                         ORDER BY created_at
                         LIMIT 1
                        """,
                        scope.organization_id,
                        scope.workspace_id,
                        item.project_id if item.project_id is not None else scope.project_id,
                        item.person_profile_id,
                        item.kind,
                        item.statement,
                    )
                if row is None:
                    # Kolizja bez wiersza do oddania znaczy, że wiersz zniknął między jednym
                    # zapytaniem a drugim (wycofanie w tej samej chwili). To jest stan rzadki
                    # i nazwany, a nie `None` oddane wołającemu jako „zapisano".
                    raise KnowledgeRefused(
                        "to ustalenie zostało w tej samej chwili zapisane i wycofane przez kogoś "
                        "innego; spróbuj jeszcze raz"
                    )
        except asyncpg.CheckViolationError as exc:
            raise KnowledgeRefused(_refusal(exc, item)) from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise KnowledgeRefused(
                "wpis wskazuje źródło, projekt, profil albo sesję spoza tej przestrzeni"
            ) from exc
        written = _item(row)
        await self._request_document_rebuild(scope, written.project_id)
        return written

    async def list(
        self,
        scope: Scope,
        *,
        include_retired: bool = False,
        source_id: UUID | None = None,
        kind: str | None = None,
        community: str | None = None,
        account: str | None = None,
        person_profile_id: UUID | None = None,
        limit: int = 200,
    ) -> tuple[ItemRow, ...]:
        """What currently holds in this scope, newest first.

        With `include_retired=True` the corrected and retracted rows come too, which is how the
        screen shows a history. They are never mixed in silently: `ItemRow.in_force` says which
        is which and the caller has to look.

        `source_id` narrows to what one material produced. A feed refresh uses it to see what it
        already wrote; without that it would write the same entries again every time it runs.

        `kind`, `community`, `account` and `person_profile_id` are the filters P5 needs: the
        question a session asks before acting is "what happened when we did this HERE, under THIS
        account", and answering it by reading everything and filtering in Python would mean the
        limit cuts the rows that matter before the filter sees them. `community` and `account`
        are read out of an EXPERIENCE payload, which is where the sweep puts them.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {", ".join("i." + name.strip() for name in _ITEM_COLUMNS.split(","))}
                FROM knowledge_items i
                WHERE i.organization_id = $1 AND i.workspace_id = $2
                  AND ($3::uuid IS NULL OR i.project_id IS NULL OR i.project_id = $3)
                  AND ($4 OR (i.superseded_by IS NULL AND i.retracted_at IS NULL))
                  AND ($5::uuid IS NULL OR i.source_id = $5)
                  AND ($6::text IS NULL OR i.kind = $6)
                  AND ($7::text IS NULL OR i.payload->'situation'->>'community' = $7)
                  AND ($8::text IS NULL OR i.payload->'decision'->>'account' = $8)
                  AND ($9::uuid IS NULL OR i.person_profile_id = $9)
                  AND {_person_visible("$3")}
                ORDER BY i.created_at DESC, i.id
                LIMIT $10
                """,
                scope.organization_id,
                scope.workspace_id,
                scope.project_id,
                include_retired,
                source_id,
                kind,
                community,
                account,
                person_profile_id,
                limit,
            )
        return tuple(_item(row) for row in rows)

    # -- correction and retraction, which never delete ----------------------------

    async def correct(self, item_id: UUID, replacement: NewItem, scope: Scope) -> ItemRow:
        """Write the new finding and point the old one at it. NOTHING IS OVERWRITTEN.

        P3: "użytkownik poprawia lub wycofuje ustalenie" and the correction has to be visible
        later, because "inaczej po tygodniu przestanie wierzyć, że rozmowa cokolwiek zmienia".
        Editing the statement in place would make the correction unobservable — the new text
        would simply have always been there.
        """
        written = await self.write(replacement, scope)
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE knowledge_items SET superseded_by = $3"
                " WHERE organization_id = $1 AND workspace_id = $2 AND id = $4"
                " AND superseded_by IS NULL RETURNING id",
                scope.organization_id,
                scope.workspace_id,
                written.id,
                item_id,
            )
        if updated is None:
            raise KnowledgeRefused(
                "nie ma takiego obowiązującego ustalenia do poprawienia w tej przestrzeni"
            )
        return written

    async def retract(self, item_id: UUID, reason: str, scope: Scope) -> bool:
        """Mark a finding as withdrawn, with a reason. The row stays and stays readable."""
        if not reason.strip():
            raise KnowledgeRefused(
                "wycofanie bez powodu nie mówi następnej sesji, czy ustalenie było błędne, "
                "czy się zdezaktualizowało"
            )
        async with self._pool.acquire() as conn:
            updated = await conn.fetchrow(
                "UPDATE knowledge_items SET retracted_at = now(), retracted_reason = $3"
                " WHERE organization_id = $1 AND workspace_id = $2 AND id = $4"
                " AND retracted_at IS NULL RETURNING id, project_id",
                scope.organization_id,
                scope.workspace_id,
                reason.strip(),
                item_id,
            )
        if updated is None:
            return False
        # Wycofanie ZMIENIA dokument tak samo jak zapis: zdanie, które z tego ustalenia wynikało,
        # ma z dokumentu zniknąć. Bez tego dokument dalej twierdziłby coś, co ktoś właśnie odwołał.
        await self._request_document_rebuild(scope, updated["project_id"])
        return True

    async def _request_document_rebuild(self, scope: Scope, project_id: UUID | None) -> None:
        """Zamów przepisanie dokumentów projektu, którego dotyczy ta zmiana (P8 etap B).

        JEDEN WYZWALACZ NA WSZYSTKICH PISARZY, i to jest cała decyzja. Do rejestru piszą dziś
        narzędzia modelu, trasa człowieka, zaciąganie RSS i przemiatanie doświadczeń; wołanie
        zamówienia w każdym z tych miejsc osobno znaczyłoby, że piąty pisarz zapomni, a dokument
        po cichu zostanie stary. Tutaj przechodzi każdy zapis, więc niezmiennik „zmiana w rejestrze
        zamawia przepisanie" jest prawdą z budowy, a nie z pamięci autora.

        Nie rzuca. `request_rebuild` łapie wszystko i loguje ostrzeżenie: rejestr jest źródłem
        prawdy i zapis ustalenia nie ma prawa paść przez kolejkę.
        """
        from control_plane.documents.handlers import request_rebuild

        await request_rebuild(
            self._pool,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            project_id=project_id if project_id is not None else scope.project_id,
        )

    async def conflicts(self, scope: Scope, *, limit: int = 500) -> tuple[Conflict, ...]:
        """Declared disagreements that hold right now in this scope.

        Reads the in-force findings and groups them by their `about` key. The grouping is a pure
        function (`group_conflicts`) so the same rule can be applied to a list somebody already
        has — a tool result, a screen — without a second query and without a second definition of
        what counts as a conflict.
        """
        return group_conflicts(await self.list(scope, limit=limit))

    async def experience_for(self, scope: Scope, request_id: str) -> ItemRow | None:
        """The EXPERIENCE that currently holds for one publication, if there is one.

        Keyed by `payload->>'request_id'` rather than by a column, because a publication is not a
        tenant concept in this table: what the memory holds is a finding, and the publication is
        part of what the finding is about. The sweep uses this to decide between "write the first
        one" and "supersede the one that no longer matches what we see".
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_ITEM_COLUMNS} FROM knowledge_items
                WHERE organization_id = $1 AND workspace_id = $2 AND kind = 'EXPERIENCE'
                  AND payload->>'request_id' = $3
                  AND superseded_by IS NULL AND retracted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                scope.organization_id,
                scope.workspace_id,
                request_id,
            )
        return _item(row) if row is not None else None

    async def replaced(self, scope: Scope, item_ids: list[UUID] | tuple[UUID, ...]) -> dict:
        """For each id, the finding it REPLACED, when it replaced one. `{new_id: old_row}`.

        The pointer only exists in one direction in the schema (`superseded_by` on the old row),
        and that is right for enforcing "nothing is overwritten". But a session reading what
        holds today needs the other direction: "this is the corrected version of what you were
        told last time" is the sentence that makes a correction visible, and without it the new
        statement looks like it was always there.
        """
        if not item_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_ITEM_COLUMNS}, superseded_by AS replaced_id FROM knowledge_items"
                " WHERE organization_id = $1 AND workspace_id = $2"
                " AND superseded_by = ANY($3::uuid[])",
                scope.organization_id,
                scope.workspace_id,
                list(item_ids),
            )
        return {row["replaced_id"]: _item(row) for row in rows}

    async def item(self, item_id: UUID, scope: Scope) -> ItemRow | None:
        """One finding, or None when it is not in this scope. A fact, not an error."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_ITEM_COLUMNS} FROM knowledge_items"
                " WHERE organization_id = $1 AND workspace_id = $2 AND id = $3",
                scope.organization_id,
                scope.workspace_id,
                item_id,
            )
        return _item(row) if row is not None else None

    # -- sources ------------------------------------------------------------------

    async def sources(
        self, scope: Scope, *, limit: int = 200, offset: int = 0
    ) -> tuple[SourceRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SOURCE_COLUMNS} FROM knowledge_sources
                WHERE organization_id = $1 AND workspace_id = $2
                  AND ($3::uuid IS NULL OR project_id IS NULL OR project_id = $3)
                ORDER BY created_at DESC, id
                LIMIT $4 OFFSET $5
                """,
                scope.organization_id,
                scope.workspace_id,
                scope.project_id,
                limit,
                max(0, offset),
            )
        return tuple(_source(row) for row in rows)

    async def upsert_source(
        self,
        scope: Scope,
        *,
        kind: str,
        locator: str,
        title: str,
        status: str,
        fetched_at: datetime | None,
        content_hash: str | None,
        raw_artifact_id: UUID | None,
        unavailable_reason: str | None,
    ) -> SourceRow:
        """One row per address in a scope, updated when we look again.

        `ON CONFLICT` against `knowledge_sources_one_per_locator` rather than a read-then-write:
        two refreshes of the same feed arriving together would both find nothing and both insert.
        Looking again is an UPDATE of when and what, so the list of materials does not grow by one
        every time a job runs.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO knowledge_sources
                        (organization_id, workspace_id, project_id, kind, locator, title, status,
                         fetched_at, content_hash, raw_artifact_id, unavailable_reason)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (organization_id, workspace_id, project_id, locator)
                    DO UPDATE SET
                        kind = EXCLUDED.kind,
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        fetched_at = EXCLUDED.fetched_at,
                        content_hash = EXCLUDED.content_hash,
                        raw_artifact_id = COALESCE(
                            EXCLUDED.raw_artifact_id, knowledge_sources.raw_artifact_id
                        ),
                        unavailable_reason = EXCLUDED.unavailable_reason
                    RETURNING {_SOURCE_COLUMNS}
                    """,
                    scope.organization_id,
                    scope.workspace_id,
                    scope.project_id,
                    kind,
                    locator,
                    title,
                    status,
                    fetched_at,
                    content_hash,
                    raw_artifact_id,
                    unavailable_reason,
                )
        except asyncpg.CheckViolationError as exc:
            raise KnowledgeRefused(f"źródło odrzucone przez bazę: {exc}") from exc
        return _source(row)


def _refusal(exc: asyncpg.CheckViolationError, item: NewItem) -> str:
    """The database's constraint name turned into the sentence the rule is actually about."""
    name = getattr(exc, "constraint_name", "") or ""
    if "fact_has_a_source" in name:
        return (
            f"{item.kind} bez źródła nie jest faktem. Podaj source_id wskazujące materiał, "
            "z którego to wynika, albo zapisz to jako INTERPRETATION"
        )
    if "preference_comes_from_the_user" in name:
        return (
            "preferencję zapisuje wyłącznie człowiek (stated_by='USER'). Agent piszący ją "
            "w czyimś imieniu tworzy wolę, której nikt nie wyraził"
        )
    if "experience_is_complete" in name:
        return (
            "doświadczenie wiąże trzy rzeczy: payload musi mieć 'situation', 'decision' "
            "i 'outcome'. Bez wyniku to plan, bez sytuacji anegdota"
        )
    if "kind_is_known" in name or "stated_by_is_known" in name:
        return f"nieznany rodzaj albo autor wpisu: kind={item.kind!r}, stated_by={item.stated_by!r}"
    if "statement" in name:
        return "ustalenie bez treści nie jest ustaleniem"
    return f"baza odrzuciła wpis ({name or 'bez nazwy więzu'})"
