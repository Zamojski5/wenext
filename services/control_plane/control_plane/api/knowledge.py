"""`/api/workspaces/{workspace_id}/knowledge` - materials the agent read, and what it concluded.

P3 etap A in one sentence: the agent may learn things, and everything it learned has to say where
it came from. These routes are the human half of that. What they enforce:

  * **every answer carries provenance.** An item that came from a material is served WITH that
    material: its address, when it was read and the hash of the bytes we kept. An item with no
    source says `source: null` rather than borrowing somebody else's;
  * **a failed read is a state, not silence.** Adding an address that cannot be reached creates
    the row with `status = "UNAVAILABLE"` and the reason, answered with 201. The person sees what
    happened to the thing they added, in the same list as everything that worked. Nothing here
    ever invents content for an address it could not read;
  * **corrections and withdrawals never delete.** `POST .../correct` writes the new finding and
    points the old one at it; `POST .../retract` marks it withdrawn with a reason. Both stay
    readable, which is what makes a correction visible a week later;
  * **material from outside is DATA.** Titles, snippets and page text travel to the browser and
    to the model as quoted material with a locator attached. Nothing in this module follows an
    instruction found inside a fetched page (P3: "materiały z zewnątrz nie stają się
    instrukcjami systemowymi").

THE SCOPE IS THE WORKSPACE FROM THE PATH, verified against the session exactly as in
`api/workspaces.py`, and `project_id` narrows it further. The organization never comes from the
request.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.knowledge.experience import VERDICTS, measure_one, preference_of
from control_plane.knowledge.handlers import (
    SWEEP_INTERVAL,
    schedule_experience_sweep,
    schedule_refresh,
    schedule_sweep,
)
from control_plane.knowledge.ingest import (
    KIND_DOCUMENT,
    KIND_NEWSLETTER,
    KIND_RSS,
    KIND_WEB_PAGE,
    Ingestor,
    artifact_volume,
)
from control_plane.knowledge.store import (
    ABOUT_KEY,
    DEFAULT_SLICE_BYTES,
    Conflict,
    ItemRow,
    KnowledgeRefused,
    KnowledgeStore,
    NewItem,
    Scope,
    SearchHit,
    Slice,
    SourceRow,
    group_conflicts,
    staleness,
)
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["knowledge"])

KNOWLEDGE_PATH = "/api/workspaces/{workspace_id}/knowledge"

MAX_LOCATOR_LENGTH = 2000
MAX_TITLE_LENGTH = 500
MAX_STATEMENT_LENGTH = 2000
MAX_NOTE_LENGTH = 200_000
MAX_DOCUMENT_LENGTH = 2_000_000
MAX_QUERY_LENGTH = 300
MAX_REASON_LENGTH = 1000
MAX_ABOUT_LENGTH = 200

#: What a person may add by hand. `WEB_SEARCH` is absent on purpose: a search is made by
#: searching, not by declaring that one happened.
ADDABLE_KINDS: tuple[str, ...] = (
    KIND_WEB_PAGE,
    KIND_RSS,
    KIND_NEWSLETTER,
    "USER_NOTE",
    KIND_DOCUMENT,
)

ITEM_KINDS: tuple[str, ...] = ("FACT", "INTERPRETATION", "PREFERENCE", "EXPERIENCE")
STATED_BY: tuple[str, ...] = ("USER", "AGENT", "SOURCE")


class AddSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    #: The address, for everything except a note. A note carries `text` instead.
    locator: str | None = Field(default=None, max_length=MAX_LOCATOR_LENGTH)
    title: str = Field(default="", max_length=MAX_TITLE_LENGTH)
    text: str | None = Field(default=None, max_length=MAX_DOCUMENT_LENGTH)
    project_id: UUID | None = None


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    project_id: UUID | None = None
    #: A search records what it found as a material. `false` looks without writing anything down,
    #: which is what a person trying out a phrase wants.
    remember: bool = True


class WriteItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    statement: str = Field(min_length=1, max_length=MAX_STATEMENT_LENGTH)
    stated_by: str = "USER"
    project_id: UUID | None = None
    #: Whose finding this is. A finding tied to a person is visible only in projects where an
    #: account of that person works, which is what keeps one person's style and preferences out
    #: of another person's session.
    person_profile_id: UUID | None = None
    source_id: UUID | None = None
    source_span: str | None = Field(default=None, max_length=MAX_STATEMENT_LENGTH)
    #: What question this finding answers. Two findings sharing it are two answers to one
    #: question, and the product reports them as a conflict instead of choosing.
    about: str | None = Field(default=None, max_length=MAX_ABOUT_LENGTH)
    payload: dict[str, Any] = Field(default_factory=dict)


class CorrectBody(WriteItemBody):
    pass


class FeedbackBody(BaseModel):
    """The person's short correction under one publication. Four buttons and a note."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    verdict: str
    note: str = Field(default="", max_length=MAX_STATEMENT_LENGTH)
    project_id: UUID | None = None


class RetractBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)
    project_id: UUID | None = None


async def knowledge_store(request: Request) -> KnowledgeStore:
    return KnowledgeStore(await pool_for(request.app))


async def ingestor_for(request: Request) -> Ingestor:
    """The ingestor of this deployment.

    `app.state.knowledge_ingestor` is honoured when set, which is how the end-to-end suite points
    the fetcher at its own local server and reaches no external network. Nothing sets it in
    production, and then the real fetcher applies with its address checks.
    """
    existing = getattr(request.app.state, "knowledge_ingestor", None)
    if existing is not None:
        return existing
    return Ingestor(pool=await pool_for(request.app), volume_factory=artifact_volume)


async def _scope(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID | None
) -> Scope:
    """The workspace of this request, verified against the session, or 404. THE gate."""
    store = WorkspaceStore(await pool_for(request.app))
    found = await store.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    return Scope(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def _checked(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"nieznany {what} {value!r}. Dozwolone: {', '.join(allowed)}",
        )
    return value


# ---------------------------------------------------------------------------
# Payloads, written out by hand so a column added to the schema does not start travelling.
# ---------------------------------------------------------------------------


def source_payload(row: SourceRow) -> dict[str, Any]:
    return {
        "source_id": str(row.id),
        "project_id": str(row.project_id) if row.project_id else None,
        "kind": row.kind,
        "locator": row.locator,
        "title": row.title,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "content_hash": row.content_hash,
        "status": row.status,
        "unavailable_reason": row.unavailable_reason,
        "has_raw": row.raw_artifact_id is not None,
        "created_at": row.created_at.isoformat(),
    }


def item_payload(row: ItemRow, source: SourceRow | None = None) -> dict[str, Any]:
    state = staleness(row, source)
    return {
        "item_id": str(row.id),
        "project_id": str(row.project_id) if row.project_id else None,
        "person_profile_id": str(row.person_profile_id) if row.person_profile_id else None,
        "kind": row.kind,
        "statement": row.statement,
        "stated_by": row.stated_by,
        "source_span": row.source_span,
        "about": row.payload.get(ABOUT_KEY),
        "payload": row.payload,
        "valid_from": row.valid_from.isoformat(),
        "superseded_by": str(row.superseded_by) if row.superseded_by else None,
        "retracted_at": row.retracted_at.isoformat() if row.retracted_at else None,
        "retracted_reason": row.retracted_reason,
        "in_force": row.superseded_by is None and row.retracted_at is None,
        # Computed here and sent, rather than left to the screen. The rule for "this stands on
        # something old" lives in one place (`knowledge/store.py::staleness`), so the dashboard
        # and the model's tools cannot disagree about which findings are shaky.
        "stale": state.stale,
        "stale_reason": state.reason or None,
        "source_age_days": state.source_age_days,
        # Provenance travels WITH the finding. A screen that has to make a second request for it
        # is a screen that will eventually render a statement with nothing beside it.
        "source": source_payload(source) if source is not None else None,
    }


def conflict_payload(conflict: Conflict) -> dict[str, Any]:
    """Two findings that hold at once and disagree, AS A PAIR. Neither is chosen here."""
    return {
        "about": conflict.about,
        "kind": conflict.kind,
        "certain": conflict.certain,
        "advice": conflict.sentence(),
        "items": [
            {
                "item_id": str(item.id),
                "statement": item.statement,
                "stated_by": item.stated_by,
                "created_at": item.created_at.isoformat(),
            }
            for item in conflict.items
        ],
    }


def hit_payload(hit: SearchHit) -> dict[str, Any]:
    return item_payload(hit.item, hit.source)


def slice_payload(window: Slice) -> dict[str, Any]:
    return {
        "source_id": str(window.source_id),
        "locator": window.locator,
        "fetched_at": window.fetched_at.isoformat() if window.fetched_at else None,
        "content_hash": window.content_hash,
        "offset": window.offset,
        "length": window.length,
        "total_bytes": window.total_bytes,
        "truncated": window.truncated,
        "text": window.text,
    }


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    workspace_id: UUID,
    subject_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            subject_ref=subject_ref,
            actor_ref=f"operator:{operator.operator_id}",
            payload=payload,
        )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/knowledge")
async def read_knowledge(
    workspace_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
    project_id: UUID | None = None,
    include_retired: bool = False,
    kind: str | None = None,
    community: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Everything this scope knows: the materials, and the findings with their provenance.

    `kind`, `community` and `account` narrow the findings the way P5 needs them narrowed: what
    happened when we did this HERE, under THIS account. Filtering in the query and not on the
    screen, because a limit applied before a filter cuts the rows that were being asked for.
    """
    scope = await _scope(request, operator, workspace_id, project_id)
    sources = await store.sources(scope)
    by_id = {row.id: row for row in sources}
    items = await store.list(
        scope,
        include_retired=include_retired,
        kind=_checked(kind, ITEM_KINDS, "rodzaj ustalenia") if kind else None,
        community=community,
        account=account,
    )
    rendered = [
        item_payload(row, by_id.get(row.source_id) if row.source_id else None) for row in items
    ]
    conflicts = group_conflicts(items)
    return {
        "workspace_id": str(workspace_id),
        "project_id": str(project_id) if project_id else None,
        "sources": [source_payload(row) for row in sources],
        "items": rendered,
        # Disagreements come back as pairs and nothing here resolves them. A screen that showed
        # one of two conflicting findings would be making the choice on the person's behalf,
        # invisibly.
        "conflicts": [conflict_payload(conflict) for conflict in conflicts],
        "counts": {
            "sources": len(sources),
            "items": len(items),
            "unavailable": sum(1 for row in sources if row.status == "UNAVAILABLE"),
            # Findings standing on material that is old, unreadable or already corrected.
            "stale": sum(1 for row in rendered if row["stale"]),
            "conflicts": len(conflicts),
        },
        "kinds": {"source": list(ADDABLE_KINDS), "item": list(ITEM_KINDS)},
    }


@router.get("/workspaces/{workspace_id}/knowledge/sources/{source_id}")
async def read_source(
    workspace_id: UUID,
    source_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
    ingestor: Annotated[Ingestor, Depends(ingestor_for)],
    project_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    length: int = Query(default=DEFAULT_SLICE_BYTES, ge=1, le=DEFAULT_SLICE_BYTES * 8),
) -> dict[str, Any]:
    """A window onto what a material actually said, SAYING whether it was cut.

    The volume comes from the ingestor, so the directory a source was WRITTEN to is the one it is
    read back from. Two independently resolved roots is how a deployment gets a store that
    reports every source as empty.
    """
    scope = await _scope(request, operator, workspace_id, project_id)
    volume = ingestor.resolved_volume() or artifact_volume()
    window = await store.read(source_id, scope, offset=offset, length=length, volume=volume)
    if window is None:
        raise HTTPException(status_code=404, detail=f"nie ma materiału {source_id}")
    if window.total_bytes == 0:
        raise HTTPException(
            status_code=409, detail="Wpis materiału istnieje, ale jego plik jest niedostępny."
        )
    return {"slice": slice_payload(window)}


@router.get("/workspaces/{workspace_id}/knowledge/search")
async def search_knowledge(
    workspace_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
    q: str = Query(min_length=1, max_length=MAX_QUERY_LENGTH),
    project_id: UUID | None = None,
) -> dict[str, Any]:
    """What we already know, searched. The web is `POST .../knowledge/search`."""
    scope = await _scope(request, operator, workspace_id, project_id)
    hits = await store.search(q, scope)
    return {"query": q, "hits": [hit_payload(hit) for hit in hits], "count": len(hits)}


# ---------------------------------------------------------------------------
# Adding material
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/knowledge/sources", status_code=201)
async def add_source(
    workspace_id: UUID,
    body: AddSourceBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    ingestor: Annotated[Ingestor, Depends(ingestor_for)],
) -> dict[str, Any]:
    """Add a page, a feed or a note. 201 EVEN WHEN THE READ FAILED.

    The row exists either way and says which happened, and that is the answer the person needs:
    "dodałem adres, którego nie dało się przeczytać, i oto powód". A 502 here would leave them
    with an error page and no record of what they tried to add.
    """
    kind = _checked(body.kind, ADDABLE_KINDS, "rodzaj materiału")
    scope = await _scope(request, operator, workspace_id, body.project_id)

    if kind in {"USER_NOTE", KIND_DOCUMENT}:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="notatka bez treści nie jest materiałem")
        if kind == "USER_NOTE" and len(text) > MAX_NOTE_LENGTH:
            raise HTTPException(status_code=400, detail="notatka jest za długa")
        if kind == KIND_DOCUMENT:
            if not body.title.strip():
                raise HTTPException(status_code=400, detail="dokument wymaga tytułu")
            ingested = await ingestor.add_document(scope, title=body.title.strip(), text=text)
        else:
            ingested = await ingestor.add_note(scope, title=body.title.strip(), text=text)
    else:
        locator = (body.locator or "").strip()
        if not locator:
            raise HTTPException(status_code=400, detail="materiał bez adresu nie ma czego czytać")
        if kind == KIND_RSS:
            ingested = await ingestor.add_feed(scope, locator)
        else:
            ingested = await ingestor.add_url(scope, locator, kind=kind)

    if ingested.source.kind == KIND_RSS:
        # A feed is a thing that keeps changing, so adding one starts the sweep chain
        # (`knowledge/handlers.py`). The key is per due hour, so adding five feeds in a row
        # schedules one sweep, and with no feeds left the chain ends on its own.
        pool = await pool_for(request.app)
        await schedule_sweep(
            PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id),
            organization_id=operator.organization_id,
            delay_seconds=SWEEP_INTERVAL.total_seconds(),
        )

    await _audit(
        request,
        kind="KNOWLEDGE_SOURCE_ADDED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"knowledge-source:{ingested.source.id}",
        payload={
            "source_id": str(ingested.source.id),
            "kind": ingested.source.kind,
            "status": ingested.source.status,
            "project_id": str(body.project_id) if body.project_id else None,
        },
    )
    return {
        "source": source_payload(ingested.source),
        "items": [item_payload(row, ingested.source) for row in ingested.items],
        "ok": ingested.ok,
    }


@router.post("/workspaces/{workspace_id}/knowledge/sources/{source_id}/refresh", status_code=202)
async def refresh_source(
    workspace_id: UUID,
    source_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
    ingestor: Annotated[Ingestor, Depends(ingestor_for)],
    project_id: UUID | None = None,
) -> dict[str, Any]:
    """Read this material again, whatever kind it is.

    TWO PATHS, AND THE DIFFERENCE IS VISIBLE IN THE ANSWER. A feed goes to the queue, because a
    feed is read on a schedule anyway and a worker is where that belongs; the answer says
    `queued: true` and the row changes when the job runs. A page is fetched HERE, inside the
    request, because the person pressed a button about one address and wants the result on the
    screen they are looking at; the answer carries the refreshed row, new hash and all.

    A note has no address to re-read, and saying so is better than a button that appears to work.
    """
    scope = await _scope(request, operator, workspace_id, project_id)
    sources = {row.id: row for row in await store.sources(scope, limit=1000)}
    row = sources.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"nie ma materiału {source_id}")
    if row.kind == "USER_NOTE":
        raise HTTPException(
            status_code=400,
            detail="notatka nie ma adresu, z którego dałoby się ją przeczytać jeszcze raz",
        )

    if row.kind == KIND_RSS:
        pool = await pool_for(request.app)
        queue = PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)
        await schedule_refresh(
            queue,
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            source_id=source_id,
        )
        refreshed = None
    else:
        # THE MATERIAL'S OWN SCOPE, not the request's. Measured by the end-to-end suite: a
        # refresh sent without `project_id` re-ingested the page at workspace scope, and because
        # the unique index treats a NULL project as its own scope (`NULLS NOT DISTINCT`), that
        # INSERTED A SECOND COPY visible in every project instead of updating the one row.
        ingested = await ingestor.add_url(
            Scope(
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                project_id=row.project_id,
            ),
            row.locator,
            kind=row.kind,
        )
        refreshed = source_payload(ingested.source)

    await _audit(
        request,
        kind="KNOWLEDGE_SOURCE_REFRESH_REQUESTED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"knowledge-source:{source_id}",
        payload={"source_id": str(source_id), "kind": row.kind},
    )
    return {
        "source_id": str(source_id),
        "queued": row.kind == KIND_RSS,
        "source": refreshed,
    }


@router.post("/workspaces/{workspace_id}/knowledge/search", status_code=201)
async def run_web_search(
    workspace_id: UUID,
    body: SearchBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    ingestor: Annotated[Ingestor, Depends(ingestor_for)],
) -> dict[str, Any]:
    """Search the web through the keyless backends, and record what answered.

    `results` is what the backends returned, each hit naming the backend it came from. When no
    backend answered, `ok` is false and `reason` says which failed and how. There is no third
    case in which this returns an empty list and calls it an answer.
    """
    scope = await _scope(request, operator, workspace_id, body.project_id)
    if body.remember:
        ingested, answer = await ingestor.run_search(scope, body.query)
        source = source_payload(ingested.source)
        await _audit(
            request,
            kind="KNOWLEDGE_SEARCH_RUN",
            operator=operator,
            workspace_id=workspace_id,
            subject_ref=f"knowledge-source:{ingested.source.id}",
            payload={
                "source_id": str(ingested.source.id),
                "status": ingested.source.status,
                "count": len(answer.results),
            },
        )
    else:
        searcher = ingestor.searcher
        answer = await (searcher.search(body.query) if searcher else _no_searcher(body.query))
        source = None
    return {
        "query": answer.query,
        "ok": answer.ok,
        "reason": answer.reason(),
        "checked": list(answer.checked),
        "unavailable": [{"backend": name, "reason": why} for name, why in answer.unavailable],
        "results": [
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet, "backend": hit.backend}
            for hit in answer.results
        ],
        "source": source,
    }


async def _no_searcher(query: str):
    from control_plane.knowledge.search import WebSearch

    return await WebSearch().search(query)


# ---------------------------------------------------------------------------
# P5: what came of what we published, and the person's correction of it
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/knowledge/experience/sweep", status_code=202)
async def request_experience_sweep(
    workspace_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Ask the queue to look at what our publications did. 202: a worker does the reading.

    A button and a chain rather than something that runs by itself from the first day: the sweep
    keeps itself going hourly while the organization has published anything, and stops when it
    has not. Nothing here calls a model, so the person pressing this is spending a query.
    """
    await _scope(request, operator, workspace_id, None)
    pool = await pool_for(request.app)
    queue = PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)
    await schedule_experience_sweep(queue, organization_id=operator.organization_id)
    await _audit(
        request,
        kind="KNOWLEDGE_EXPERIENCE_SWEEP_REQUESTED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"workspace:{workspace_id}",
        payload={"workspace_id": str(workspace_id)},
    )
    return {"queued": True}


@router.post("/workspaces/{workspace_id}/knowledge/feedback", status_code=201)
async def record_feedback(
    workspace_id: UUID,
    body: FeedbackBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
) -> dict[str, Any]:
    """One short correction under one publication, kept as a PREFERENCE the next session reads.

    WHY A PREFERENCE AND NOT A NEW KIND. What the person is saying is "next time, do it this
    way"; the schema already has a kind for the person's will, with the rule that only a person
    may state it (0016). A separate `FEEDBACK` kind would be that rule written a second time,
    and the agent would eventually be allowed to write it.

    IT IS TIED TO THE PUBLICATION IT CORRECTS: the payload carries the request id and, when the
    sweep has already recorded one, the id of the EXPERIENCE. That is what lets a session answer
    "co zmieniłeś i dlaczego" with two identifiers rather than with a story.

    `judgment_feedback` is NOT touched here. That table is the operator's verdict on a judgement
    of a THREAD; this is a correction of a published text. Two different objects, and merging
    them would make both unreadable.
    """
    scope = await _scope(request, operator, workspace_id, body.project_id)
    verdict = _checked(body.verdict, tuple(VERDICTS), "rodzaj korekty")
    pool = await pool_for(request.app)
    measured = await measure_one(
        pool, organization_id=operator.organization_id, request_id=body.request_id
    )
    if measured is None or measured.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"nie ma publikacji {body.request_id}")

    experience = await store.experience_for(
        Scope(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=measured.project_id,
        ),
        str(body.request_id),
    )
    statement, payload = preference_of(
        measured,
        verdict,
        note=body.note,
        experience_id=experience.id if experience else None,
    )
    try:
        written = await store.write(
            NewItem(
                kind="PREFERENCE",
                statement=statement,
                # USER, always. This route is the person pressing a button; an agent reaching it
                # would be writing the person's will, which 0016 refuses anyway.
                stated_by="USER",
                project_id=measured.project_id or scope.project_id,
                person_profile_id=measured.person_profile_id,
                payload=payload,
            ),
            Scope(
                organization_id=operator.organization_id,
                workspace_id=workspace_id,
                project_id=measured.project_id or scope.project_id,
            ),
        )
    except KnowledgeRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        request,
        kind="KNOWLEDGE_FEEDBACK_RECORDED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"publication:{body.request_id}",
        payload={
            "item_id": str(written.id),
            "request_id": str(body.request_id),
            "verdict": verdict,
        },
    )
    return {
        "item": item_payload(written),
        "experience_id": str(experience.id) if experience else None,
        "verdicts": {name: spec["label"] for name, spec in VERDICTS.items()},
    }


# ---------------------------------------------------------------------------
# Findings: writing, correcting, withdrawing
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/knowledge/items", status_code=201)
async def write_item(
    workspace_id: UUID,
    body: WriteItemBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
) -> dict[str, Any]:
    """Write one finding. A refusal by the rules comes back as 400 with the sentence."""
    scope = await _scope(request, operator, workspace_id, body.project_id)
    item = _new_item(body, scope)
    try:
        written = await store.write(item, scope)
    except KnowledgeRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        request,
        kind="KNOWLEDGE_ITEM_WRITTEN",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"knowledge-item:{written.id}",
        payload={"item_id": str(written.id), "kind": written.kind},
    )
    return {"item": item_payload(written)}


@router.post("/workspaces/{workspace_id}/knowledge/items/{item_id}/correct", status_code=201)
async def correct_item(
    workspace_id: UUID,
    item_id: UUID,
    body: CorrectBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
) -> dict[str, Any]:
    """The new finding, with the old one pointing at it. NOTHING is overwritten."""
    scope = await _scope(request, operator, workspace_id, body.project_id)
    try:
        written = await store.correct(item_id, _new_item(body, scope), scope)
    except KnowledgeRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        request,
        kind="KNOWLEDGE_ITEM_CORRECTED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"knowledge-item:{item_id}",
        payload={"item_id": str(written.id), "kind": written.kind},
    )
    return {"item": item_payload(written), "replaced": str(item_id)}


@router.post("/workspaces/{workspace_id}/knowledge/items/{item_id}/retract", status_code=200)
async def retract_item(
    workspace_id: UUID,
    item_id: UUID,
    body: RetractBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[KnowledgeStore, Depends(knowledge_store)],
) -> dict[str, Any]:
    """Withdraw a finding with a reason. The row stays and stays readable."""
    scope = await _scope(request, operator, workspace_id, body.project_id)
    try:
        done = await store.retract(item_id, body.reason, scope)
    except KnowledgeRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not done:
        raise HTTPException(
            status_code=404,
            detail="nie ma takiego obowiązującego ustalenia do wycofania w tej przestrzeni",
        )
    await _audit(
        request,
        kind="KNOWLEDGE_ITEM_RETRACTED",
        operator=operator,
        workspace_id=workspace_id,
        subject_ref=f"knowledge-item:{item_id}",
        payload={"item_id": str(item_id)},
    )
    return {"item_id": str(item_id), "retracted": True}


def _new_item(body: WriteItemBody, scope: Scope) -> NewItem:
    payload = dict(body.payload)
    if body.about is not None and body.about.strip():
        # `about` is a field of the form and a key of the payload. One place to read it from,
        # and `group_conflicts` is that reader.
        payload[ABOUT_KEY] = body.about.strip()
    return NewItem(
        kind=_checked(body.kind, ITEM_KINDS, "rodzaj ustalenia"),
        statement=body.statement.strip(),
        stated_by=_checked(body.stated_by, STATED_BY, "autor ustalenia"),
        project_id=body.project_id or scope.project_id,
        person_profile_id=body.person_profile_id,
        source_id=body.source_id,
        source_span=body.source_span,
        payload=payload,
    )
