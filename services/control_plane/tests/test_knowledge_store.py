"""`control_plane.knowledge.store` — the four calls the MCP tools wrap, against PostgreSQL.

WHAT THESE TESTS ARE ABOUT. Not "the SQL runs": whether a model asking this store a question can
be misled by the answer. Four ways it could be, one case each:

  * a finding comes back without what it stands on, so "fakt" is a word;
  * a correction is invisible, so the next session repeats what the user already fixed;
  * a slice of a source does not say it was cut, so the model concludes from half a page;
  * a scope leaks, so one project's material shows up in another's context.

The database enforces the three kind rules (0016) and `test_knowledge_schema.py` proves that;
here the subject is what the store does with the refusal — a sentence a tool can show, not a
PostgreSQL error code.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from control_plane.knowledge import (
    KnowledgeRefused,
    KnowledgeStore,
    NewItem,
    Scope,
)
from control_plane.storage.artifacts import ArtifactStore, ArtifactVolume
from control_plane.storage.config import ArtifactAcl, ArtifactKind

pytestmark = pytest.mark.db


@pytest_asyncio.fixture
async def world(pool, tenant):
    """One workspace, two projects, so "scope" is a claim a test can actually break."""
    async with pool.acquire() as connection:
        first = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'A')"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        second = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'B')"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
    return {**tenant, "project_a": first, "project_b": second}


@pytest.fixture
def store(pool) -> KnowledgeStore:
    return KnowledgeStore(pool)


def scope_of(world, project: str | None = None) -> Scope:
    return Scope(
        organization_id=world["organization"],
        workspace_id=world["workspace"],
        project_id=world[project] if project else None,
    )


async def add_source(pool, world, *, text: bytes, project=None, volume=None, locator=None):
    """A source with its raw material really in `artifacts`, as the fetcher will write it."""
    artifact_id = None
    if volume is not None:
        async with pool.acquire() as connection, connection.transaction():
            record = await ArtifactStore().record(
                connection,
                organization_id=world["organization"],
                workspace_id=world["workspace"],
                kind=ArtifactKind.SOURCE,
                acl=ArtifactAcl.OPERATOR_ONLY,
                content=text,
                encrypted=False,
                retention_until=datetime.now(UTC) + timedelta(days=30),
                volume=volume,
            )
        artifact_id = record.artifact_id

    store = KnowledgeStore(pool)
    return await store.upsert_source(
        Scope(
            organization_id=world["organization"],
            workspace_id=world["workspace"],
            project_id=world[project] if project else None,
        ),
        kind="WEB_PAGE",
        locator=locator or f"https://example.test/{uuid.uuid4()}",
        title="Strona testowa",
        status="FETCHED",
        fetched_at=datetime.now(UTC),
        content_hash="sha256:" + hashlib.sha256(text).hexdigest(),
        raw_artifact_id=artifact_id,
        unavailable_reason=None,
    )


# ---------------------------------------------------------------------------
# write: the rules are refusals with sentences
# ---------------------------------------------------------------------------


async def test_a_fact_needs_a_source_and_the_refusal_says_so(store, world, pool) -> None:
    """The message is the deliverable here: a tool shows it to a model that can act on it."""
    with pytest.raises(KnowledgeRefused) as refused:
        await store.write(
            NewItem(kind="FACT", statement="kolejki na Postgresie skalują się", stated_by="AGENT"),
            scope_of(world),
        )
    assert "source_id" in str(refused.value), str(refused.value)
    assert "INTERPRETATION" in str(refused.value), str(refused.value)

    source = await add_source(pool, world, text=b"tresc")
    written = await store.write(
        NewItem(
            kind="FACT",
            statement="kolejki na Postgresie skalują się",
            stated_by="SOURCE",
            source_id=source.id,
            source_span="akapit 3",
        ),
        scope_of(world),
    )
    assert written.kind == "FACT" and written.source_id == source.id
    assert written.in_force is True


async def test_an_agent_cannot_write_a_preference_for_the_user(store, world) -> None:
    with pytest.raises(KnowledgeRefused, match="człowiek"):
        await store.write(
            NewItem(kind="PREFERENCE", statement="woli krótkie odpowiedzi", stated_by="AGENT"),
            scope_of(world),
        )
    ok = await store.write(
        NewItem(kind="PREFERENCE", statement="woli krótkie odpowiedzi", stated_by="USER"),
        scope_of(world),
    )
    assert ok.stated_by == "USER"


async def test_an_experience_needs_situation_decision_and_outcome(store, world) -> None:
    with pytest.raises(KnowledgeRefused, match="outcome"):
        await store.write(
            NewItem(
                kind="EXPERIENCE",
                statement="komentarz zniknął",
                stated_by="AGENT",
                payload={"situation": "nowa społeczność", "decision": "komentarz"},
            ),
            scope_of(world),
        )
    ok = await store.write(
        NewItem(
            kind="EXPERIENCE",
            statement="komentarz zniknął po godzinie",
            stated_by="AGENT",
            payload={
                "situation": "nowa społeczność, konto bez historii",
                "decision": "komentarz z liczbą",
                "outcome": "zdjęty przez automoderację w godzinę",
            },
        ),
        scope_of(world),
    )
    assert ok.payload["outcome"].startswith("zdjęty")


async def test_a_write_pointing_outside_the_scope_is_refused_with_a_sentence(
    store, world, pool
) -> None:
    """A foreign source is a foreign-key violation; the caller gets words, not `23503`."""
    with pytest.raises(KnowledgeRefused, match="spoza tej przestrzeni"):
        await store.write(
            NewItem(
                kind="FACT",
                statement="skądinąd",
                stated_by="SOURCE",
                source_id=uuid.uuid4(),
            ),
            scope_of(world),
        )


# ---------------------------------------------------------------------------
# search and list: provenance, and what currently holds
# ---------------------------------------------------------------------------


async def test_search_returns_the_finding_together_with_its_source(store, world, pool) -> None:
    source = await add_source(pool, world, text=b"o kolejkach")
    await store.write(
        NewItem(
            kind="FACT",
            statement="kolejka na Postgresie wystarcza do tej skali",
            stated_by="SOURCE",
            source_id=source.id,
            source_span="akapit 2",
        ),
        scope_of(world),
    )
    hits = await store.search("kolejka", scope_of(world))
    assert len(hits) == 1, hits
    assert hits[0].source is not None, "trafienie bez pochodzenia to samo zdanie"
    assert hits[0].source.locator == source.locator
    assert hits[0].source.content_hash == source.content_hash
    assert hits[0].item.source_span == "akapit 2"


async def test_a_corrected_finding_leaves_the_search_and_stays_readable(store, world) -> None:
    """P3: the user corrects, and the correction has to be visible AND effective."""
    old = await store.write(
        NewItem(kind="INTERPRETATION", statement="odzew jest mały przez temat", stated_by="AGENT"),
        scope_of(world),
    )
    new = await store.correct(
        old.id,
        NewItem(
            kind="INTERPRETATION", statement="odzew jest mały przez porę dnia", stated_by="USER"
        ),
        scope_of(world),
    )

    in_force = await store.list(scope_of(world))
    assert [row.id for row in in_force] == [new.id], in_force
    # Nothing was deleted and the trail leads from the old statement to the new one.
    everything = await store.list(scope_of(world), include_retired=True)
    assert len(everything) == 2, everything
    superseded = next(row for row in everything if row.id == old.id)
    assert superseded.statement == "odzew jest mały przez temat"
    assert superseded.superseded_by == new.id
    assert superseded.in_force is False


async def test_a_retracted_finding_leaves_a_trace_with_its_reason(store, world) -> None:
    item = await store.write(
        NewItem(kind="INTERPRETATION", statement="to już nieaktualne", stated_by="AGENT"),
        scope_of(world),
    )
    assert await store.retract(item.id, "źródło wycofane przez autora", scope_of(world)) is True
    assert await store.list(scope_of(world)) == ()

    kept = await store.list(scope_of(world), include_retired=True)
    assert len(kept) == 1 and kept[0].retracted_reason == "źródło wycofane przez autora"
    assert kept[0].in_force is False
    # A second retraction of the same row is False, not a cheerful True over nothing.
    assert await store.retract(item.id, "jeszcze raz", scope_of(world)) is False


async def test_a_retraction_without_a_reason_is_refused_before_the_database(store, world) -> None:
    item = await store.write(
        NewItem(kind="INTERPRETATION", statement="cokolwiek", stated_by="AGENT"), scope_of(world)
    )
    for reason in ("", "   ", "\t"):
        with pytest.raises(KnowledgeRefused, match="powodu"):
            await store.retract(item.id, reason, scope_of(world))


async def test_project_scopes_do_not_leak_into_each_other(store, world, pool) -> None:
    """One project's material must not reach another's context.

    And the workspace-wide finding reaches BOTH, which is the other half of the rule: a scope
    that hid it would make "what do we know here" answer less than the truth.
    """
    shared = await store.write(
        NewItem(kind="INTERPRETATION", statement="wspólne ustalenie o tonie", stated_by="USER"),
        scope_of(world),
    )
    only_a = await store.write(
        NewItem(kind="INTERPRETATION", statement="ustalenie tylko dla A", stated_by="USER"),
        scope_of(world, "project_a"),
    )
    only_b = await store.write(
        NewItem(kind="INTERPRETATION", statement="ustalenie tylko dla B", stated_by="USER"),
        scope_of(world, "project_b"),
    )

    in_a = {row.id for row in await store.list(scope_of(world, "project_a"))}
    in_b = {row.id for row in await store.list(scope_of(world, "project_b"))}
    assert in_a == {shared.id, only_a.id}, in_a
    assert in_b == {shared.id, only_b.id}, in_b
    # The whole workspace sees everything, which is what an agent asking "what do we know" wants.
    assert {row.id for row in await store.list(scope_of(world))} == {
        shared.id,
        only_a.id,
        only_b.id,
    }


def test_the_widest_scope_cannot_be_reached_by_forgetting_an_argument() -> None:
    """`project_id` has no default, so "everything" has to be typed out.

    Raised by the P2 session while wiring the memory tools: a default of `None` means the
    broadest possible reading is what a caller gets for omitting a field. The named constructor
    is the same value with the intent visible at the call site.
    """
    with pytest.raises(TypeError):
        Scope(organization_id=uuid.uuid4(), workspace_id=uuid.uuid4())  # type: ignore[call-arg]
    wide = Scope.whole_workspace(uuid.uuid4(), uuid.uuid4())
    assert wide.project_id is None


async def test_another_workspace_sees_nothing_of_this_one(store, world, pool) -> None:
    await store.write(
        NewItem(kind="INTERPRETATION", statement="tajemnica przestrzeni", stated_by="USER"),
        scope_of(world),
    )
    async with pool.acquire() as connection:
        other = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obca') RETURNING id",
            world["organization"],
        )
    elsewhere = Scope.whole_workspace(world["organization"], other)
    assert await store.list(elsewhere) == ()
    assert await store.search("tajemnica", elsewhere) == ()


# ---------------------------------------------------------------------------
# read: the slice says when it cut
# ---------------------------------------------------------------------------


async def test_read_returns_the_material_and_says_whether_it_cut(
    store, world, pool, tmp_path
) -> None:
    """`truncated` is the field this whole method exists for.

    A tool that returned the first N bytes silently would let a model conclude from half a page
    and never learn that it had half.
    """
    volume = ArtifactVolume(tmp_path)
    body = ("akapit " * 5000).encode("utf-8")
    source = await add_source(pool, world, text=body, volume=volume)

    head = await store.read(source.id, scope_of(world), length=100, volume=volume)
    assert head is not None
    assert head.total_bytes == len(body)
    assert head.length == 100
    assert head.truncated is True, "przycięty odczyt nie powiedział, że przyciął"
    assert head.text.startswith("akapit")
    # The provenance travels with the bytes, so the caller can cite them.
    assert head.locator == source.locator
    assert head.content_hash == source.content_hash
    assert head.fetched_at is not None

    tail = await store.read(
        source.id, scope_of(world), offset=len(body) - 10, length=100, volume=volume
    )
    assert tail is not None and tail.truncated is False and tail.length == 10


async def test_reading_a_source_with_no_stored_bytes_is_not_an_empty_source(
    store, world, pool
) -> None:
    """A note has no raw material. That is a fact about the row, not a source with no content."""
    source = await add_source(pool, world, text=b"nieistotne")  # no volume, so no artifact
    window = await store.read(source.id, scope_of(world))
    assert window is not None
    assert window.total_bytes == 0
    assert window.truncated is False
    assert window.locator == source.locator


async def test_reading_a_source_of_another_scope_is_none(store, world, pool) -> None:
    """`None` means "no such source here" — the same answer as an identifier that never existed,
    so a caller cannot map another project's material by trying."""
    private = await add_source(pool, world, text=b"tylko A", project="project_a")
    assert await store.read(private.id, scope_of(world, "project_b")) is None
    assert await store.read(uuid.uuid4(), scope_of(world)) is None
    assert await store.read(private.id, scope_of(world, "project_a")) is not None


async def test_looking_at_the_same_address_again_updates_one_row(store, world, pool) -> None:
    """Refreshing a feed must not grow the list of materials by one every time."""
    first = await add_source(pool, world, text=b"wersja 1", locator="https://example.test/feed")
    second = await add_source(pool, world, text=b"wersja 2", locator="https://example.test/feed")
    assert first.id == second.id, "drugi odczyt tego samego adresu utworzył nowy wiersz"
    assert second.content_hash != first.content_hash
    assert len(await store.sources(scope_of(world))) == 1


async def test_the_same_statement_written_twice_is_one_row_with_one_id(pool, tenant) -> None:
    """0027: powtórzona tura nie tworzy drugiego ustalenia o tej samej treści.

    Zmierzony powód: worker przerwany między `TOOL_CALL` a `TOOL_RESULT` powtarza turę, a
    strażnik powtórzeń rozpoznaje powtórkę dopiero po tej parze. Sprawdzenie przed zapisem
    przegrałoby ten wyścig; rozstrzyga indeks unikalny, a zapis oddaje istniejący wiersz.
    """
    store = KnowledgeStore(pool)
    scope = Scope(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=None,
    )
    statement = "Autorzy tego wątku nie odpowiadają na linki bez kontekstu."
    first = await store.write(
        NewItem(kind="INTERPRETATION", statement=statement, stated_by="AGENT"), scope
    )
    # Druga próba z inną wielkością liter i innym odstępem: dla człowieka to jest to samo zdanie.
    second = await store.write(
        NewItem(
            kind="INTERPRETATION",
            statement="  autorzy tego wątku NIE odpowiadają   na linki bez kontekstu.  ",
            stated_by="AGENT",
        ),
        scope,
    )

    assert second.id == first.id, "powtórzony zapis założył drugie ustalenie"
    async with pool.acquire() as conn:
        ile = await conn.fetchval(
            "SELECT count(*) FROM knowledge_items WHERE organization_id = $1 "
            "AND statement ILIKE $2 AND retracted_at IS NULL",
            tenant["organization"],
            "%nie odpowiadają%",
        )
    assert ile == 1, ile
