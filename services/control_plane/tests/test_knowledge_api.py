"""`/api/workspaces/{w}/knowledge` end to end: a real page, a real feed, PostgreSQL, no internet.

THE COMPLETION CRITERION OF P3 ETAP A, ONE CASE EACH:

  * a material added by address is stored WITH the date it was read and the hash of the bytes,
    and those bytes come back;
  * a finding found by searching carries its provenance;
  * a correction is visible afterwards, and so is a withdrawal;
  * an address that could not be read is `UNAVAILABLE` with a reason and NO invented content;
  * a second workspace sees none of it.

NOTHING HERE TOUCHES THE INTERNET. The page and the feed are served by a local HTTP server
started inside the test and named in `WENEXT_KNOWLEDGE_FIXTURE_ORIGINS`, which is the only reason
the fetcher will speak plaintext to a loopback address at all. The web search backends are a stub
fetcher: their URLs are fixed hostnames, so the alternative would be a test that silently starts
depending on Algolia being up.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from control_plane.domain.models import ClaimedJob
from control_plane.knowledge.fetch import FIXTURE_ORIGINS_VARIABLE, Fetched, fetch_url
from control_plane.knowledge.handlers import KnowledgeHandlers, source_ref
from control_plane.knowledge.ingest import Ingestor
from control_plane.knowledge.search import HN_ALGOLIA, WebSearch, _Throttle
from control_plane.storage.artifacts import ArtifactVolume

pytestmark = pytest.mark.db

PAGE = b"""<!doctype html><html><head><title>Kolejki bez ack</title></head>
<body><h1>Kolejki</h1><p>Zgubione wiadomosci: 12 tysiecy na dobe.</p></body></html>"""

FEED_ONE = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Notatki o kolejkach</title>
<item><title>Pierwszy wpis</title><link>https://example.test/1</link>
<pubDate>Fri, 19 Sep 2026 10:00:00 GMT</pubDate><description>Opis</description></item>
</channel></rss>"""

FEED_TWO = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Notatki o kolejkach</title>
<item><title>Drugi wpis</title><link>https://example.test/2</link>
<pubDate>Sat, 20 Sep 2026 10:00:00 GMT</pubDate><description>Nowszy</description></item>
<item><title>Pierwszy wpis</title><link>https://example.test/1</link>
<pubDate>Fri, 19 Sep 2026 10:00:00 GMT</pubDate><description>Opis</description></item>
</channel></rss>"""


class _Site(BaseHTTPRequestHandler):
    #: Flipped by a test to make the same feed address answer with a newer feed.
    feed = FEED_ONE

    def log_message(self, *args):
        return

    def do_GET(self):
        bodies = {
            "/artykul": ("text/html; charset=utf-8", PAGE),
            "/kanal.xml": ("application/rss+xml", type(self).feed),
            "/nie-kanal": ("text/html", b"<html><body>Zaloguj sie</body></html>"),
        }
        if self.path in bodies:
            content_type, body = bodies[self.path]
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()


@pytest.fixture
def site():
    _Site.feed = FEED_ONE
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://{httpd.server_address[0]}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


HITS = json.dumps(
    {
        "hits": [
            {
                "objectID": "1",
                "title": "Kolejka bez ack gubi wiadomosci",
                "url": "https://example.test/hn",
                "points": 42,
                "num_comments": 7,
            }
        ]
    }
).encode()


def stub_search(payload: bytes | None = HITS) -> WebSearch:
    """The backends, answered locally. `None` makes every backend fail, which is its own case."""

    async def fetcher(url: str) -> Fetched:
        if payload is None:
            return Fetched(
                ok=False,
                url=url,
                status=None,
                content=b"",
                content_type="",
                title="",
                text="",
                reason="brak sieci w teście",
                truncated=False,
            )
        return Fetched(
            ok=True,
            url=url,
            status=200,
            content=payload,
            content_type="application/json",
            title="",
            text=payload.decode(),
            reason=None,
            truncated=False,
        )

    return WebSearch(fetcher=fetcher, backends=(HN_ALGOLIA,), throttle=_Throttle(interval_s=0.0))


@pytest.fixture
def volume(tmp_path) -> ArtifactVolume:
    return ArtifactVolume(tmp_path / "artifacts")


@pytest.fixture
def ingestor(app, pool, volume, site) -> Ingestor:
    """The real fetcher, pointed at the local server through the fixture-origin list.

    Not a stub: the address checks, the redirect handling and the byte ceiling are part of what
    "dodaj materiał" has to do, and a stubbed fetcher would test the code around them instead.
    """

    async def fetcher(url: str) -> Fetched:
        return await fetch_url(url, env={FIXTURE_ORIGINS_VARIABLE: site})

    built = Ingestor(pool=pool, volume=volume, fetcher=fetcher, searcher=stub_search())
    app.state.knowledge_ingestor = built
    return built


@pytest_asyncio.fixture
async def workspaces(pool, tenant) -> dict[str, uuid.UUID]:
    """The tenant's workspace, plus a SECOND one, so "scope" is a claim a test can break."""
    async with pool.acquire() as connection:
        other = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'Druga przestrzeń')"
            " RETURNING id",
            tenant["organization"],
        )
        project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'Projekt')"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
    return {"first": tenant["workspace"], "second": other, "project": project}


def path(workspace_id) -> str:
    return f"/api/workspaces/{workspace_id}/knowledge"


async def add(client, workspace_id, **body):
    response = await client.post(f"{path(workspace_id)}/sources", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Adding material
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_page_is_stored_with_the_date_the_hash_and_the_bytes(
    logged_in_client, workspaces, ingestor, site
):
    """Provenance is four facts. A source missing any of them cannot be checked later."""
    added = await add(
        logged_in_client, workspaces["first"], kind="WEB_PAGE", locator=f"{site}/artykul"
    )
    source = added["source"]
    assert source["status"] == "FETCHED"
    assert source["fetched_at"] is not None
    assert source["content_hash"].startswith("sha256:")
    assert source["title"] == "Kolejki bez ack"

    window = await logged_in_client.get(
        f"{path(workspaces['first'])}/sources/{source['source_id']}"
    )
    assert window.status_code == 200
    body = window.json()["slice"]
    assert "12 tysiecy" in body["text"]
    assert body["total_bytes"] == len(PAGE)
    assert body["truncated"] is False
    assert body["content_hash"] == source["content_hash"]


@pytest.mark.asyncio
async def test_a_slice_that_was_cut_says_so(logged_in_client, workspaces, ingestor, site):
    """A model concluding from half a page must at least learn that it had half."""
    added = await add(
        logged_in_client, workspaces["first"], kind="WEB_PAGE", locator=f"{site}/artykul"
    )
    window = await logged_in_client.get(
        f"{path(workspaces['first'])}/sources/{added['source']['source_id']}?length=20"
    )
    body = window.json()["slice"]
    assert body["truncated"] is True
    assert body["length"] == 20
    assert body["total_bytes"] == len(PAGE)


@pytest.mark.asyncio
async def test_an_address_that_cannot_be_read_is_recorded_with_its_reason_and_no_content(
    logged_in_client, workspaces, ingestor, site
):
    """THE case P3 names: "nie udało się" jest stanem materiału, nie brakiem materiału."""
    added = await add(
        logged_in_client, workspaces["first"], kind="WEB_PAGE", locator=f"{site}/nie-ma"
    )
    source = added["source"]
    assert added["ok"] is False
    assert source["status"] == "UNAVAILABLE"
    assert "404" in source["unavailable_reason"]
    assert source["content_hash"] is None
    assert source["has_raw"] is False

    window = await logged_in_client.get(
        f"{path(workspaces['first'])}/sources/{source['source_id']}"
    )
    # Metadata stays visible, but missing content is not a successful read.
    assert window.status_code == 409
    assert "niedostępny" in window.json()["detail"]


@pytest.mark.asyncio
async def test_an_address_the_product_may_not_visit_is_refused_and_the_reason_is_recorded(
    logged_in_client, workspaces, ingestor
):
    """A pasted `http://169.254.169.254/` is not fetched, and the person is told why."""
    added = await add(
        logged_in_client,
        workspaces["first"],
        kind="WEB_PAGE",
        locator="http://169.254.169.254/latest/meta-data/",
    )
    assert added["source"]["status"] == "UNAVAILABLE"
    assert added["source"]["has_raw"] is False
    assert "https" in added["source"]["unavailable_reason"]


@pytest.mark.asyncio
async def test_a_note_is_a_material_with_a_hash_and_no_fetch(
    logged_in_client, workspaces, ingestor
):
    added = await add(
        logged_in_client,
        workspaces["first"],
        kind="USER_NOTE",
        title="Co powiedział klient",
        text="Kolejka gubi wiadomości przy restarcie.",
    )
    source = added["source"]
    assert source["kind"] == "USER_NOTE"
    assert source["status"] == "FETCHED"
    assert source["content_hash"].startswith("sha256:")
    window = await logged_in_client.get(
        f"{path(workspaces['first'])}/sources/{source['source_id']}"
    )
    assert "restarcie" in window.json()["slice"]["text"]


@pytest.mark.asyncio
async def test_a_note_with_no_content_is_refused(logged_in_client, workspaces, ingestor):
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/sources",
        json={"kind": "USER_NOTE", "title": "puste", "text": "   "},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_feed_becomes_a_source_and_its_entries_become_findings_with_provenance(
    logged_in_client, workspaces, ingestor, site
):
    added = await add(
        logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml"
    )
    assert added["source"]["title"] == "Notatki o kolejkach"
    assert len(added["items"]) == 1
    item = added["items"][0]
    assert item["kind"] == "FACT"
    assert item["stated_by"] == "SOURCE"
    assert item["source"]["source_id"] == added["source"]["source_id"]
    assert item["payload"]["link"] == "https://example.test/1"


@pytest.mark.asyncio
async def test_bytes_that_are_not_a_feed_do_not_become_an_empty_but_healthy_source(
    logged_in_client, workspaces, ingestor, site
):
    added = await add(
        logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/nie-kanal"
    )
    assert added["source"]["status"] == "UNAVAILABLE"
    assert "kanał" in added["source"]["unavailable_reason"] or "rss" in (
        added["source"]["unavailable_reason"].lower()
    )
    assert added["items"] == []


@pytest.mark.asyncio
async def test_refreshing_a_feed_adds_only_what_is_new(
    logged_in_client, workspaces, ingestor, pool, tenant, site
):
    """A job that runs four times a day must not write the same entry four times a day."""
    added = await add(
        logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml"
    )
    source_id = uuid.UUID(added["source"]["source_id"])

    _Site.feed = FEED_TWO
    handlers = KnowledgeHandlers(
        pool=pool, ingestor=ingestor, organization_id=tenant["organization"]
    )
    await handlers.refresh_feed(
        ClaimedJob(
            job_id=uuid.uuid4(),
            organization_id=tenant["organization"],
            workspace_id=workspaces["first"],
            job_type="knowledge.rss.refresh",
            routing_key="knowledge.rss",
            priority=20,
            payload_ref=source_ref(source_id),
            dispatch_version=1,
            attempt_limit=3,
            attempt_id=uuid.uuid4(),
            attempt_no=1,
            lease_generation=1,
            lease_owner=uuid.uuid4(),
            expires_at=None,
        )
    )

    listing = (await logged_in_client.get(path(workspaces["first"]))).json()
    statements = sorted(item["statement"] for item in listing["items"])
    assert statements == ["Drugi wpis", "Pierwszy wpis"]
    assert listing["counts"]["sources"] == 1


@pytest.mark.asyncio
async def test_adding_a_feed_starts_the_sweep_chain_and_a_second_feed_does_not_double_it(
    logged_in_client, workspaces, ingestor, pool, tenant, site
):
    """Nothing else schedules this. Without it, "kanały odświeżają się same" would be a label."""
    await add(logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml")
    async with pool.acquire() as connection:
        after_first = await connection.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            "knowledge.rss.sweep",
        )
    assert after_first == 1

    await add(logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml?b=2")
    async with pool.acquire() as connection:
        after_second = await connection.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            "knowledge.rss.sweep",
        )
    # Keyed by the hour it is due in, so the second feed joins the sweep already scheduled.
    assert after_second == 1


@pytest.mark.asyncio
async def test_the_sweep_enqueues_a_refresh_for_a_due_feed_and_chains_itself(
    logged_in_client, workspaces, ingestor, pool, tenant, site
):
    from datetime import UTC, datetime, timedelta

    from control_plane.domain.models import ClaimedJob
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    await add(logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml")
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE knowledge_sources SET fetched_at = $1",
            datetime.now(UTC) - timedelta(days=2),
        )
        await connection.execute(
            "DELETE FROM jobs WHERE organization_id = $1", tenant["organization"]
        )

    handlers = KnowledgeHandlers(
        pool=pool,
        ingestor=ingestor,
        organization_id=tenant["organization"],
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
    )
    await handlers.sweep(
        ClaimedJob(
            job_id=uuid.uuid4(),
            organization_id=tenant["organization"],
            workspace_id=None,
            job_type="knowledge.rss.sweep",
            routing_key="knowledge.rss",
            priority=20,
            payload_ref=None,
            dispatch_version=1,
            attempt_limit=1,
            attempt_id=uuid.uuid4(),
            attempt_no=1,
            lease_generation=1,
            lease_owner=uuid.uuid4(),
            expires_at=None,
        )
    )
    async with pool.acquire() as connection:
        types = [
            row["job_type"]
            for row in await connection.fetch(
                "SELECT job_type FROM jobs WHERE organization_id = $1 ORDER BY job_type",
                tenant["organization"],
            )
        ]
    assert types == ["knowledge.rss.refresh", "knowledge.rss.sweep"]


@pytest.mark.asyncio
async def test_the_chain_ends_when_there_is_no_feed_left(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    """A self-chaining job with no stop condition is a job nobody can end."""
    import uuid as uuid_module

    from control_plane.domain.models import ClaimedJob
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    handlers = KnowledgeHandlers(
        pool=pool,
        ingestor=ingestor,
        organization_id=tenant["organization"],
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
    )
    assert await handlers.feed_count() == 0
    await handlers.sweep(
        ClaimedJob(
            job_id=uuid_module.uuid4(),
            organization_id=tenant["organization"],
            workspace_id=None,
            job_type="knowledge.rss.sweep",
            routing_key="knowledge.rss",
            priority=20,
            payload_ref=None,
            dispatch_version=1,
            attempt_limit=1,
            attempt_id=uuid_module.uuid4(),
            attempt_no=1,
            lease_generation=1,
            lease_owner=uuid_module.uuid4(),
            expires_at=None,
        )
    )
    async with pool.acquire() as connection:
        remaining = await connection.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1", tenant["organization"]
        )
    assert remaining == 0


@pytest.mark.asyncio
async def test_the_sweep_finds_a_feed_nobody_has_read_yet(
    logged_in_client, workspaces, ingestor, pool, tenant, site
):
    from datetime import UTC, datetime, timedelta

    await add(logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml")
    handlers = KnowledgeHandlers(
        pool=pool, ingestor=ingestor, organization_id=tenant["organization"]
    )
    # Just read, so nothing is due.
    assert await handlers.due_sources() == ()

    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE knowledge_sources SET fetched_at = $1",
            datetime.now(UTC) - timedelta(days=2),
        )
    due = await handlers.due_sources()
    assert len(due) == 1 and due[0][1] == workspaces["first"]


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refreshing_a_page_updates_the_one_row_instead_of_making_a_second_copy(
    logged_in_client, workspaces, ingestor, site
):
    """A refresh with no `project_id` in the query must still refresh THAT material.

    Found end to end: the re-fetch used the request's scope, and with no project in the query
    that scope is the whole workspace. Under `NULLS NOT DISTINCT` a workspace-scoped row is a
    different row from a project-scoped one, so the page was inserted a second time and showed
    up in every project of the workspace.
    """
    project = workspaces["project"]
    added = await add(
        logged_in_client,
        workspaces["first"],
        kind="WEB_PAGE",
        locator=f"{site}/artykul",
        project_id=str(project),
    )
    source_id = added["source"]["source_id"]
    assert added["source"]["project_id"] == str(project)

    # No `project_id` in the query, exactly as the dashboard's button sends it.
    refreshed = await logged_in_client.post(
        f"{path(workspaces['first'])}/sources/{source_id}/refresh"
    )
    assert refreshed.status_code == 202, refreshed.text
    assert refreshed.json()["queued"] is False
    assert refreshed.json()["source"]["source_id"] == source_id
    assert refreshed.json()["source"]["project_id"] == str(project)

    listing = (
        await logged_in_client.get(f"{path(workspaces['first'])}?project_id={project}")
    ).json()
    assert listing["counts"]["sources"] == 1
    # The material stayed in its project: a second, workspace-wide copy would surface here.
    whole_workspace = (await logged_in_client.get(path(workspaces["first"]))).json()
    assert [row["project_id"] for row in whole_workspace["sources"]] == [str(project)]


@pytest.mark.asyncio
async def test_refreshing_a_note_is_refused_because_there_is_nothing_to_read_again(
    logged_in_client, workspaces, ingestor
):
    added = await add(
        logged_in_client, workspaces["first"], kind="USER_NOTE", title="Notatka", text="treść"
    )
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/sources/{added['source']['source_id']}/refresh"
    )
    assert response.status_code == 400
    assert "nie ma adresu" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_search_records_what_answered_and_every_hit_names_its_backend(
    logged_in_client, workspaces, ingestor
):
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/search", json={"query": "kolejki bez ack"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["checked"] == [HN_ALGOLIA]
    assert body["results"][0]["backend"] == HN_ALGOLIA
    assert body["source"]["kind"] == "WEB_SEARCH"
    assert body["source"]["content_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_a_search_that_nobody_answered_is_not_an_empty_result_set(
    logged_in_client, workspaces, app, pool, volume
):
    """An empty list reads as "the web has nothing". That is a claim we did not make."""
    app.state.knowledge_ingestor = Ingestor(
        pool=pool, volume=volume, searcher=stub_search(payload=None)
    )
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/search", json={"query": "cokolwiek"}
    )
    body = response.json()
    assert body["ok"] is False
    assert body["results"] == []
    assert "brak sieci w teście" in body["reason"]
    assert body["source"]["status"] == "UNAVAILABLE"
    assert body["source"]["unavailable_reason"]


@pytest.mark.asyncio
async def test_searching_what_we_already_know_returns_the_finding_with_its_material(
    logged_in_client, workspaces, ingestor, site
):
    await add(logged_in_client, workspaces["first"], kind="RSS", locator=f"{site}/kanal.xml")
    found = await logged_in_client.get(f"{path(workspaces['first'])}/search?q=Pierwszy")
    hits = found.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["source"]["locator"].endswith("/kanal.xml")
    assert hits[0]["source"]["fetched_at"] is not None


# ---------------------------------------------------------------------------
# Findings: correcting and withdrawing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P5: the person's short correction under a publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_correction_becomes_a_preference_tied_to_the_publication(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    """Four buttons, and what the person pressed has to survive as something usable later."""
    from test_knowledge_experience import claim, handlers_for, observe, publish_one

    request_id = await publish_one(pool, tenant, external_id="1p5b01", draft="Długa odpowiedź.")
    await observe(pool, tenant, request_id, score=3, replies=1)
    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/feedback",
        json={"request_id": str(request_id), "verdict": "TOO_LONG", "note": "wystarczy akapit"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    item = body["item"]
    assert item["kind"] == "PREFERENCE"
    # USER, always: an agent writing this would be inventing the person's will.
    assert item["stated_by"] == "USER"
    assert item["about"] == "długość wypowiedzi"
    assert "wystarczy akapit" in item["statement"]
    assert item["payload"]["request_id"] == str(request_id)
    # And it points at the experience of that publication, so "co zmieniłeś i dlaczego" has two
    # identifiers to stand on rather than a story.
    assert body["experience_id"] is not None
    assert item["payload"]["experience_id"] == body["experience_id"]


@pytest.mark.asyncio
async def test_two_corrections_that_disagree_come_back_as_a_conflict(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    from test_knowledge_experience import observe, publish_one

    request_id = await publish_one(pool, tenant, external_id="1p5b02", draft="Tekst.")
    await observe(pool, tenant, request_id, score=2, replies=0)
    for verdict in ("NOT_LIKE_THIS", "GOOD"):
        answer = await logged_in_client.post(
            f"{path(workspaces['first'])}/feedback",
            json={"request_id": str(request_id), "verdict": verdict},
        )
        assert answer.status_code == 201, answer.text

    listing = (await logged_in_client.get(path(workspaces["first"]))).json()

    assert listing["counts"]["conflicts"] == 1
    conflict = listing["conflicts"][0]
    assert conflict["kind"] == "PREFERENCE"
    assert len(conflict["items"]) == 2
    assert all(entry["stated_by"] == "USER" for entry in conflict["items"])


@pytest.mark.asyncio
async def test_a_correction_under_a_publication_that_is_not_here_is_a_404(
    logged_in_client, workspaces, ingestor
):
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/feedback",
        json={"request_id": str(uuid.uuid4()), "verdict": "GOOD"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_verdict_is_refused_with_the_list_of_known_ones(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    from test_knowledge_experience import publish_one

    request_id = await publish_one(pool, tenant, external_id="1p5b03", draft="Tekst.")
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/feedback",
        json={"request_id": str(request_id), "verdict": "SUPER"},
    )
    assert response.status_code == 400
    assert "TOO_LONG" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_correction_and_the_sweep_leave_audit_entries_of_their_own(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    from test_knowledge_experience import publish_one

    request_id = await publish_one(pool, tenant, external_id="1p5b04", draft="Tekst.")
    await logged_in_client.post(
        f"{path(workspaces['first'])}/feedback",
        json={"request_id": str(request_id), "verdict": "GOOD"},
    )
    queued = await logged_in_client.post(f"{path(workspaces['first'])}/experience/sweep")
    assert queued.status_code == 202

    async with pool.acquire() as connection:
        kinds = [
            row["kind"]
            for row in await connection.fetch(
                "SELECT kind FROM audit_events WHERE organization_id = $1 AND kind LIKE 'KNOWLEDGE%'"
                " ORDER BY id",
                tenant["organization"],
            )
        ]
    assert kinds == ["KNOWLEDGE_FEEDBACK_RECORDED", "KNOWLEDGE_EXPERIENCE_SWEEP_REQUESTED"]


@pytest.mark.asyncio
async def test_findings_can_be_narrowed_to_one_community_and_one_kind(
    logged_in_client, workspaces, ingestor, pool, tenant
):
    """The question before acting is "what happened when we did this HERE"."""
    from test_knowledge_experience import claim, handlers_for, observe, publish_one

    request_id = await publish_one(pool, tenant, external_id="1p5b05", draft="Gdzieś tam.")
    await observe(pool, tenant, request_id, score=3, replies=2)
    await handlers_for(pool, tenant).sweep_experience(claim(tenant))
    await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "INTERPRETATION", "statement": "coś zupełnie innego", "stated_by": "USER"},
    )

    everything = (await logged_in_client.get(path(workspaces["first"]))).json()
    assert len(everything["items"]) == 2

    only_experience = (
        await logged_in_client.get(f"{path(workspaces['first'])}?kind=EXPERIENCE")
    ).json()
    assert [row["kind"] for row in only_experience["items"]] == ["EXPERIENCE"]
    community = only_experience["items"][0]["payload"]["situation"]["community"]

    here = (await logged_in_client.get(f"{path(workspaces['first'])}?community={community}")).json()
    assert len(here["items"]) == 1
    elsewhere = (
        await logged_in_client.get(f"{path(workspaces['first'])}?community=r/nie-ma-takiej")
    ).json()
    assert elsewhere["items"] == []


@pytest.mark.asyncio
async def test_a_correction_leaves_both_the_old_finding_and_the_pointer_to_the_new_one(
    logged_in_client, workspaces, ingestor
):
    """P3: inaczej po tygodniu człowiek przestanie wierzyć, że rozmowa cokolwiek zmienia."""
    written = await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "INTERPRETATION", "statement": "Klient chce szybciej", "stated_by": "USER"},
    )
    assert written.status_code == 201, written.text
    item_id = written.json()["item"]["item_id"]

    corrected = await logged_in_client.post(
        f"{path(workspaces['first'])}/items/{item_id}/correct",
        json={
            "kind": "INTERPRETATION",
            "statement": "Klient chce taniej, nie szybciej",
            "stated_by": "USER",
        },
    )
    assert corrected.status_code == 201, corrected.text

    in_force = (await logged_in_client.get(path(workspaces["first"]))).json()["items"]
    assert [item["statement"] for item in in_force] == ["Klient chce taniej, nie szybciej"]

    history = (
        await logged_in_client.get(f"{path(workspaces['first'])}?include_retired=true")
    ).json()["items"]
    old = next(item for item in history if item["item_id"] == item_id)
    assert old["superseded_by"] == corrected.json()["item"]["item_id"]
    assert old["in_force"] is False


@pytest.mark.asyncio
async def test_a_withdrawal_keeps_the_row_and_the_reason(logged_in_client, workspaces, ingestor):
    written = await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "INTERPRETATION", "statement": "Warto pisać codziennie", "stated_by": "USER"},
    )
    item_id = written.json()["item"]["item_id"]
    retracted = await logged_in_client.post(
        f"{path(workspaces['first'])}/items/{item_id}/retract",
        json={"reason": "to był domysł, nie ustalenie"},
    )
    assert retracted.status_code == 200, retracted.text

    assert (await logged_in_client.get(path(workspaces["first"]))).json()["items"] == []
    history = (
        await logged_in_client.get(f"{path(workspaces['first'])}?include_retired=true")
    ).json()["items"]
    assert history[0]["retracted_reason"] == "to był domysł, nie ustalenie"
    assert history[0]["retracted_at"] is not None


@pytest.mark.asyncio
async def test_a_fact_without_a_material_is_refused_with_a_sentence_to_show(
    logged_in_client, workspaces, ingestor
):
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "FACT", "statement": "Rynek rośnie o 30 procent", "stated_by": "AGENT"},
    )
    assert response.status_code == 400
    assert "źródł" in response.json()["detail"]


@pytest.mark.asyncio
async def test_withdrawing_something_that_is_not_there_is_a_404_and_not_a_cheerful_ok(
    logged_in_client, workspaces, ingestor
):
    response = await logged_in_client.post(
        f"{path(workspaces['first'])}/items/{uuid.uuid4()}/retract",
        json={"reason": "cokolwiek"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_second_workspace_sees_none_of_it(logged_in_client, workspaces, ingestor, site):
    await add(logged_in_client, workspaces["first"], kind="WEB_PAGE", locator=f"{site}/artykul")
    await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "INTERPRETATION", "statement": "Ustalenie pierwszej", "stated_by": "USER"},
    )
    other = (await logged_in_client.get(path(workspaces["second"]))).json()
    assert other["sources"] == [] and other["items"] == []
    assert (await logged_in_client.get(f"{path(workspaces['second'])}/search?q=Ustalenie")).json()[
        "hits"
    ] == []


@pytest.mark.asyncio
async def test_a_workspace_of_another_organization_is_a_404_and_not_a_403(
    logged_in_client, ingestor
):
    """A 403 would confirm that the identifier is real. Same answer as "does not exist"."""
    response = await logged_in_client.get(path(uuid.uuid4()))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_nothing_here_is_reachable_without_a_session(client, workspaces):
    for method, url in (
        ("get", path(workspaces["first"])),
        ("post", f"{path(workspaces['first'])}/sources"),
        ("post", f"{path(workspaces['first'])}/search"),
        ("post", f"{path(workspaces['first'])}/items"),
    ):
        response = await getattr(client, method)(url, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401, (method, url, response.status_code)


@pytest.mark.asyncio
async def test_every_write_leaves_an_audit_entry(
    logged_in_client, workspaces, ingestor, pool, tenant, site
):
    await add(logged_in_client, workspaces["first"], kind="WEB_PAGE", locator=f"{site}/artykul")
    written = await logged_in_client.post(
        f"{path(workspaces['first'])}/items",
        json={"kind": "INTERPRETATION", "statement": "Ustalenie", "stated_by": "USER"},
    )
    item_id = written.json()["item"]["item_id"]
    await logged_in_client.post(
        f"{path(workspaces['first'])}/items/{item_id}/retract", json={"reason": "pomyłka"}
    )
    async with pool.acquire() as connection:
        kinds = [
            row["kind"]
            for row in await connection.fetch(
                "SELECT kind FROM audit_events WHERE organization_id = $1"
                " AND kind LIKE 'KNOWLEDGE%' ORDER BY id",
                tenant["organization"],
            )
        ]
    assert kinds == ["KNOWLEDGE_SOURCE_ADDED", "KNOWLEDGE_ITEM_WRITTEN", "KNOWLEDGE_ITEM_RETRACTED"]


async def test_imported_document_is_readable_without_becoming_a_user_note(
    logged_in_client, workspaces, ingestor
):
    text = "=== PDF PAGE 1 ===\nMateriał autora.\n" * 8000
    added = await add(
        logged_in_client, workspaces["first"], kind="DOCUMENT", title="Książka", text=text
    )
    assert added["source"]["kind"] == "DOCUMENT"
    assert added["source"]["has_raw"] is True
    result = await logged_in_client.get(
        f"{path(workspaces['first'])}/sources/{added['source']['source_id']}"
    )
    assert result.status_code == 200
    assert result.json()["slice"]["text"].startswith("=== PDF PAGE 1 ===")
    assert result.json()["slice"]["truncated"] is True
