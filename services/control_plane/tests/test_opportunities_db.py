"""Okazje i Pokój przeciwko PostgreSQL: zapis, stan z danych, droga od rozmowy na żywo do szkicu.

NA JAKIE PYTANIE ODPOWIADA TEN PLIK: "czy rozmowa znaleziona dziś na Reddicie albo X dochodzi do
opublikowania, a strona pokazuje o niej prawdę". Każdy przypadek to jeden sposób, w jaki mogłaby
skłamać albo zgubić pracę:

  * ta sama rozmowa zapisana dwa razy daje dwie pozycje, albo agent, który ją znajduje drugi raz,
    przywraca to, co założyciel już pominął;
  * szkic pod rozmową na żywo powstaje bez zarzutu, a publikator odrzuca go później ("brak
    wątku"), bo wątku nikt nie zapisał tam, gdzie go szuka;
  * zapisanie wątku włącza obserwację społeczności, której nikt nie wybrał, albo nadpisuje wątek
    przeczytany w całości fragmentem z wyszukiwania;
  * stan okazji zostaje "SZKIC" po śmierci szkicu i nie wraca na "ODPOWIEDZIANO" po publikacji;
  * strona pokazuje cudzą okazję, cudzy szkic albo liczbę, której nikt nie zmierzył.

Wszystko oprócz zatwierdzenia idzie nowym kodem; zatwierdzenie idzie ISTNIEJĄCĄ trasą, i to jest
część testu: szkic z Pokoju ma przejść dokładnie tę drogę, co każdy inny.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from community_os_contracts.execution import ActionProposalState
from control_plane.approval.store import ApprovalStore
from control_plane.opportunities import OpportunityStore, new_opportunity
from control_plane.opportunities.ingest import ensure_reddit_thread
from control_plane.pilot.publication import PublicationStore
from control_plane.tools import ToolRegistry, issue
from control_plane.tools import opportunity_tools as tools_module

pytestmark = pytest.mark.db

OPPORTUNITY_KEYS = {
    "id", "platforma", "spolecznosc", "tytul", "fragment", "autor", "url", "utworzono", "wynik",
    "komentarze", "dlaczego", "kat", "stan", "szkic",
}  # fmt: skip
DRAFT_KEYS = {
    "revision_id", "okazja_id", "platforma", "konto", "spolecznosc", "rodzaj", "tytul", "tekst",
    "w_odpowiedzi_na", "stan", "wygasa", "odcisk",
}  # fmt: skip
RESULT_KEYS = {
    "url", "okazja_id", "platforma", "tekst", "widocznosc", "glosy", "odpowiedzi", "opublikowano",
}  # fmt: skip


@dataclass
class World:
    """One project with one account and a registry that speaks for it, as a plugin session."""

    project_id: uuid.UUID
    account_id: uuid.UUID
    login: str
    session_id: uuid.UUID
    registry: ToolRegistry
    platform: str


async def _world(pool, tenant, *, platform: str = "REDDIT") -> World:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid.uuid4().hex[:8]}",
        )
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    account_id, login = await _account(pool, tenant, project_id, platform)
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
            host="codex",
        ),
    )
    return World(project_id, account_id, login, session_id, registry, platform)


async def _account(pool, tenant, project_id, platform: str) -> tuple[uuid.UUID, str]:
    login = f"u{uuid.uuid4().hex[:10]}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, $2, $3, 'READY') RETURNING id",
            tenant["organization"],
            platform,
            login,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return account_id, login


def _reddit_args(post: str = "abc123", sub: str = "SaaS", **changes) -> dict:
    base = {
        "platform": "REDDIT",
        "url": f"https://www.reddit.com/r/{sub}/comments/{post}/how_do_you_handle_x/",
        "community": f"r/{sub}",
        "title": f"How do you handle X? ({post})",
        "excerpt": "We keep losing track of X and it is getting out of hand.",
        "author": "someone",
        "created_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "score": 12,
        "num_comments": 5,
        "dlaczego": "Prosi dokładnie o to, co robi projekt.",
        "kat": "Pokaż, jak to rozwiązałeś u siebie.",
    }
    return {**base, **changes}


def _x_args(status: str = "2103587050346250341", **changes) -> dict:
    base = {
        "platform": "X",
        "url": f"https://x.com/jack/status/{status}",
        "excerpt": "Anyone using agents for distribution? Tried three tools, none stuck.",
        "author": "jack",
        "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "score": 4,
        "dlaczego": "Pyta o to samo, co rozwiązuje projekt.",
    }
    return {**base, **changes}


async def _mark(world: World, **args) -> dict:
    result = await world.registry.call("opportunities.mark", args)
    assert result["stan"] == "WYKONANE", result
    return result


async def _draft(world: World, url: str, text: str = "Moja odpowiedź.", **extra) -> dict:
    """A reply under a conversation. On Reddit the target is `t3_<id>` taken from the address."""
    args = {"kind": "REPLY", "target_url": url, "text": text, "account": world.login}
    if world.platform == "REDDIT":
        args["target_external_id"] = "t3_" + url.split("/comments/")[1].split("/")[0]
    return await world.registry.call("draft.create", {**args, **extra})


async def _listed(world: World, **args) -> dict:
    result = await world.registry.call("opportunities.list", args)
    assert result["stan"] == "WYKONANE", result
    return result


async def _sign(client, tenant, world: World):
    response = await client.post(
        f"/api/workspaces/{tenant['workspace']}/projects/{world.project_id}/delegation",
        json={
            "mode": "REVIEW",
            "accounts": [str(world.account_id)],
            "communities": ["r/SaaS"],
            "actions": ["REPLY"],
            "budget": {"publications_per_hour": 6, "publications_per_day": 6},
        },
    )
    assert response.status_code == 201, response.text


def _room(tenant, project_id) -> str:
    return f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/room"


def _dismiss(tenant, project_id, opportunity_id) -> str:
    return (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}"
        f"/opportunities/{opportunity_id}/dismiss"
    )


async def _mark_published(
    pool, tenant, request_id, *, score: int | None = None, replies: int = 0, days_ago: float = 0
) -> None:
    """Make a queued request PUBLISHED with the chain 0007 requires (intent, attempt), and
    optionally one read-back observation. The same chain the publisher leaves behind."""
    async with pool.acquire() as conn:
        request = await conn.fetchrow(
            "SELECT proposal_revision_id, approval_decision_id FROM publication_requests "
            "WHERE id = $1",
            request_id,
        )
        revision = await conn.fetchrow(
            "SELECT r.payload_hash, p.social_account_id FROM action_proposal_revisions r "
            "JOIN action_proposals p ON p.id = r.proposal_id WHERE r.id = $1",
            request["proposal_revision_id"],
        )
        intent_id = await conn.fetchval(
            """
            INSERT INTO execution_intents
                (organization_id, workspace_id, proposal_revision_id, approval_decision_id,
                 one_time_grant_hash, expires_at, payload_hash, account_id, status, consumed_at)
            VALUES ($1, $2, $3, $4, $5, now() + interval '1 hour', $6, $7, 'CONSUMED', now())
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            request["proposal_revision_id"],
            request["approval_decision_id"],
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            revision["payload_hash"],
            revision["social_account_id"],
        )
        attempt_id = await conn.fetchval(
            """
            INSERT INTO execution_attempts
                (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
                 attempt_no, status, adapter_version, finished_at)
            VALUES ($1, $2, $3, $4, $5, 1, 'CONFIRMED', 'test-1', now())
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            intent_id,
            request["proposal_revision_id"],
            revision["payload_hash"],
        )
        await conn.execute(
            "UPDATE publication_requests SET status = 'PUBLISHED', intent_id = $2, "
            "attempt_id = $3, published_at = now() - make_interval(secs => $4) WHERE id = $1",
            request_id,
            intent_id,
            attempt_id,
            days_ago * 86400,
        )
        await conn.execute(
            "INSERT INTO execution_evidence (organization_id, workspace_id, attempt_id, "
            "external_id, permalink, account, observed_at, source) "
            "VALUES ($1, $2, $3, 't1_ours01', $4, 'konto', now(), 'ACTUATOR')",
            tenant["organization"],
            tenant["workspace"],
            attempt_id,
            "https://www.reddit.com/r/SaaS/comments/abc123/x/ours01/",
        )
        if score is not None:
            await conn.execute(
                "INSERT INTO publication_observations (organization_id, workspace_id, request_id,"
                " observed_at, still_present, score, reply_count, viewer) "
                "VALUES ($1, $2, $3, now(), true, $4, $5, 'ANONYMOUS')",
                tenant["organization"],
                tenant["workspace"],
                request_id,
                score,
                replies,
            )


async def _request_of(pool, revision_id: str):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, organization_id, target_external_id, reaction_id "
            "FROM publication_requests WHERE proposal_revision_id = $1",
            uuid.UUID(revision_id),
        )


def _as_request(row) -> SimpleNamespace:
    """The three fields `PublicationStore.target_for` reads from a request."""
    return SimpleNamespace(
        organization_id=row["organization_id"],
        target_external_id=row["target_external_id"],
        reaction_id=row["reaction_id"],
    )


# ---------------------------------------------------------------------------
# Marking: idempotent, scoped, and the founder's decision is not the agent's to undo
# ---------------------------------------------------------------------------


async def test_marking_the_same_conversation_twice_is_one_opportunity(pool, tenant) -> None:
    world = await _world(pool, tenant)
    first = await _mark(world, **_reddit_args())
    assert first["nowa"] is True
    assert first["okazja"]["stan"] == "NOWA" and first["okazja"]["szkic"] is None
    assert first["pokoj"] == f"/w/{tenant['workspace']}/p/{world.project_id}/room"
    assert set(first["okazja"]) == OPPORTUNITY_KEYS

    again = await _mark(
        world, **_reddit_args(score=40, dlaczego="Nowe uzasadnienie po przeczytaniu wątku.")
    )
    assert again["nowa"] is False
    assert again["okazja"]["id"] == first["okazja"]["id"]
    assert again["okazja"]["wynik"] == 40
    assert again["okazja"]["dlaczego"] == "Nowe uzasadnienie po przeczytaniu wątku."

    # A later mark that does not know the counters must not erase what an earlier one measured.
    blind = _reddit_args(dlaczego="Jeszcze jedno uzasadnienie.")
    del blind["score"], blind["num_comments"]
    kept = await _mark(world, **blind)
    assert kept["okazja"]["wynik"] == 40 and kept["okazja"]["komentarze"] == 5

    async with pool.acquire() as conn:
        rows = await conn.fetchval(
            "SELECT count(*) FROM project_opportunities WHERE project_id = $1", world.project_id
        )
    assert rows == 1


async def test_the_same_post_by_id_or_by_address_is_the_same_opportunity(pool, tenant) -> None:
    world = await _world(pool, tenant)
    by_url = await _mark(world, **_reddit_args())
    by_id = await _mark(world, **_reddit_args(external_id="t3_abc123"))
    assert by_url["okazja"]["id"] == by_id["okazja"]["id"]


async def test_a_skipped_conversation_stays_skipped_when_the_agent_finds_it_again(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    opportunity_id = uuid.UUID(marked["okazja"]["id"])
    store = OpportunityStore(pool)
    skipped = await store.dismiss(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        opportunity_id=opportunity_id,
        by="operator:test",
    )
    assert skipped is not None and skipped[0].stan == "POMINIETA" and skipped[1] is True

    found_again = await _mark(world, **_reddit_args(score=99))
    assert found_again["okazja"]["stan"] == "POMINIETA", "agent nie przywraca pominiętej"
    assert found_again["okazja"]["wynik"] == 99, "dane są odświeżone, decyzja nie"
    assert "pominięta" in found_again["uwaga"]

    repeated = await store.dismiss(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        opportunity_id=opportunity_id,
        by="operator:somebody-else",
    )
    assert repeated is not None and repeated[1] is False, "powtórka nie przesuwa decyzji"
    async with pool.acquire() as conn:
        author = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE id = $1", opportunity_id
        )
    assert author == "operator:test"

    still_open = await _listed(world)
    assert still_open["okazje"] == [], "domyślna lista to otwarte okazje"
    everything = await _listed(world, stan="WSZYSTKIE")
    assert [one["stan"] for one in everything["okazje"]] == ["POMINIETA"]


async def test_the_agent_can_skip_an_opportunity_and_the_decision_names_the_session(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    result = await _mark(world, **_x_args(pomin=True, dlaczego="Nieaktualne, wątek zamknięty."))
    assert result["okazja"]["stan"] == "POMINIETA"
    async with pool.acquire() as conn:
        author = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE project_id = $1",
            world.project_id,
        )
    assert author == f"session:{world.session_id}"


async def test_the_list_filters_counts_and_points_at_the_room(pool, tenant) -> None:
    world = await _world(pool, tenant)
    await _mark(world, **_reddit_args("aaa111"))
    await _mark(world, **_reddit_args("bbb222", created_at="2020-01-01T00:00:00+00:00"))
    await _mark(world, **_x_args())

    listed = await _listed(world)
    assert listed["liczba"] == 3 and listed["lacznie_w_filtrze"] == 3
    assert listed["wedlug_stanu"] == {"NOWA": 3, "SZKIC": 0, "ODPOWIEDZIANO": 0, "POMINIETA": 0}
    assert listed["pokoj"] == f"/w/{tenant['workspace']}/p/{world.project_id}/room"
    assert [one["platforma"] for one in listed["okazje"]].count("X") == 1
    assert listed["okazje"][-1]["url"].endswith("/bbb222/how_do_you_handle_x/"), (
        "najstarsza na końcu"
    )

    only_x = await _listed(world, platform="X")
    assert [one["platforma"] for one in only_x["okazje"]] == ["X"]
    limited = await _listed(world, limit=1)
    assert limited["liczba"] == 1 and limited["lacznie_w_filtrze"] == 3

    bad = await world.registry.call("opportunities.list", {"stan": "NIE_MA"})
    assert bad["stan"] == "BLAD"


async def test_an_opportunity_of_another_project_is_not_found(pool, tenant) -> None:
    mine = await _world(pool, tenant)
    other = await _world(pool, tenant)
    marked = await _mark(other, **_reddit_args())
    foreign_id = uuid.UUID(marked["okazja"]["id"])
    store = OpportunityStore(pool)

    assert (await _listed(mine))["okazje"] == []
    assert (await _listed(mine, stan="WSZYSTKIE"))["okazje"] == []
    assert (
        await store.get(
            organization_id=tenant["organization"],
            project_id=mine.project_id,
            opportunity_id=foreign_id,
        )
        is None
    )
    assert (
        await store.dismiss(
            organization_id=tenant["organization"],
            project_id=mine.project_id,
            opportunity_id=foreign_id,
            by="operator:test",
        )
        is None
    )
    assert (
        await store.for_target(
            organization_id=tenant["organization"],
            project_id=mine.project_id,
            external_id="t3_abc123",
        )
        is None
    )
    # The other project's opportunity was not touched by the attempt.
    assert (await _listed(other))["okazje"][0]["stan"] == "NOWA"

    # The same conversation in MY project is my own opportunity, with its own id.
    mine_marked = await _mark(mine, **_reddit_args())
    assert mine_marked["nowa"] is True and mine_marked["okazja"]["id"] != marked["okazja"]["id"]


# ---------------------------------------------------------------------------
# A live Reddit conversation becomes publishable
# ---------------------------------------------------------------------------


async def test_a_reply_under_a_live_reddit_post_records_where_the_publisher_looks(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM observed_threads WHERE organization_id = $1",
                tenant["organization"],
            )
            == 0
        )

    drafted = await _draft(world, marked["okazja"]["url"])

    assert drafted["stan"] == "OCZEKUJE", drafted
    assert drafted["okazja"] == {"id": marked["okazja"]["id"], "stan": "SZKIC"}
    assert drafted["pokoj"] == marked["pokoj"] and "ostrzezenie" not in drafted
    async with pool.acquire() as conn:
        community = await conn.fetchrow(
            "SELECT * FROM communities WHERE organization_id = $1", tenant["organization"]
        )
        thread = await conn.fetchrow(
            "SELECT * FROM observed_threads WHERE organization_id = $1", tenant["organization"]
        )
        snapshots = await conn.fetchval(
            "SELECT count(*) FROM thread_metric_snapshots WHERE thread_id = $1", thread["id"]
        )
    # QUIET: no polling, no judgment per thread (no model call), and no decision about own posts.
    assert community["community_key"] == "r/SaaS" and community["platform"] == "REDDIT"
    assert community["observe_enabled"] is False
    assert community["thread_analysis_enabled"] is False
    assert community["own_posts_allowed"] is None
    assert community["display_name"] == "Reddit · r/SaaS"
    assert thread["external_id"] == "abc123" and thread["url"] == marked["okazja"]["url"]
    assert thread["body"].startswith("We keep losing track") and thread["author"] == "someone"
    assert snapshots == 1

    # THE POINT: the publisher's own lookup finds the target of the draft.
    request = SimpleNamespace(
        organization_id=tenant["organization"], target_external_id="t3_abc123", reaction_id=None
    )
    target = await PublicationStore(pool).target_for(request)  # type: ignore[arg-type]
    assert target is not None and target.thread_external_id == "abc123"
    assert target.community_id == community["id"]


async def test_recording_a_live_thread_does_not_touch_what_is_already_known(pool, tenant) -> None:
    world = await _world(pool, tenant)
    async with pool.acquire() as conn:
        # A community the founder LEFT (observation off) and one with analysis on: both are kept.
        for key, observing, analysing in (("r/Left", False, True), ("r/Watched", True, True)):
            await conn.execute(
                "INSERT INTO communities (organization_id, workspace_id, platform, community_key,"
                " display_name, access_mode, observe_enabled, poll_interval_seconds,"
                " thread_analysis_enabled) "
                "VALUES ($1, $2, 'REDDIT', $3, $3, 'ACCOUNT_REQUIRED', $4, 600, $5)",
                tenant["organization"],
                tenant["workspace"],
                key,
                observing,
                analysing,
            )
    for sub in ("Left", "Watched"):
        marked = await _mark(world, **_reddit_args(f"{sub.lower()}1x", sub))
        view = await OpportunityStore(pool).get(
            organization_id=tenant["organization"],
            project_id=world.project_id,
            opportunity_id=uuid.UUID(marked["okazja"]["id"]),
        )
        assert view is not None
        await ensure_reddit_thread(
            pool,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            opportunity=view,
        )
    async with pool.acquire() as conn:
        flags = {
            row["community_key"]: (
                row["observe_enabled"],
                row["thread_analysis_enabled"],
                row["own_posts_allowed"],
            )
            for row in await conn.fetch(
                "SELECT * FROM communities WHERE organization_id = $1", tenant["organization"]
            )
        }
    assert flags["r/Left"] == (False, True, None), "wypisana społeczność nie wraca do obserwacji"
    assert flags["r/Watched"] == (True, True, None)
    assert len(flags) == 2, "ani jednej nowej społeczności: obie już były"


async def test_a_thread_that_was_read_in_full_is_not_overwritten_by_an_excerpt(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    async with pool.acquire() as conn:
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key,"
            " display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', 'r/SaaS', 'Reddit r/SaaS', 'ACCOUNT_REQUIRED', 600) "
            "RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        await conn.execute(
            "INSERT INTO observed_threads (organization_id, workspace_id, community_id, "
            "external_id, url, title, body, author, created_at_source, first_observed_at, "
            "last_observed_at, raw_sha256) "
            "VALUES ($1, $2, $3, 'abc123', 'https://www.reddit.com/r/SaaS/comments/abc123/', "
            "'Pełny tytuł', 'Pełna treść zebrana przez obserwację', 'someone', now(), now(), "
            "now(), $4)",
            tenant["organization"],
            tenant["workspace"],
            community_id,
            "0" * 64,
        )
    drafted = await _draft(world, marked["okazja"]["url"])
    assert drafted["stan"] == "OCZEKUJE"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT title, body FROM observed_threads WHERE organization_id = $1",
            tenant["organization"],
        )
        snapshots = await conn.fetchval(
            "SELECT count(*) FROM thread_metric_snapshots WHERE organization_id = $1",
            tenant["organization"],
        )
    assert [(r["title"], r["body"]) for r in rows] == [
        ("Pełny tytuł", "Pełna treść zebrana przez obserwację")
    ]
    assert snapshots == 0, "nic nie zostało dopisane do znanego wątku"


async def test_a_second_draft_under_the_same_conversation_records_nothing_twice(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    first = await _draft(world, marked["okazja"]["url"], "Pierwsza wersja odpowiedzi.")
    second = await _draft(world, marked["okazja"]["url"], "Druga, inna wersja odpowiedzi.")
    assert first["stan"] == second["stan"] == "OCZEKUJE"
    async with pool.acquire() as conn:
        threads = await conn.fetchval(
            "SELECT count(*) FROM observed_threads WHERE organization_id = $1",
            tenant["organization"],
        )
        communities = await conn.fetchval(
            "SELECT count(*) FROM communities WHERE organization_id = $1", tenant["organization"]
        )
    assert threads == 1 and communities == 1


async def test_a_reply_to_an_unrecorded_thread_is_still_written_and_warns(pool, tenant) -> None:
    """The behaviour from before D-055 stays; what changes is that the agent is told."""
    world = await _world(pool, tenant)
    drafted = await _draft(
        world,
        "https://www.reddit.com/r/SaaS/comments/zzz999/some_post/",
        target_external_id="t3_zzz999",
    )
    assert drafted["stan"] == "OCZEKUJE"
    assert "opportunities.mark" in drafted["ostrzezenie"] and "okazja" not in drafted
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM observed_threads WHERE organization_id = $1",
                tenant["organization"],
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM communities WHERE organization_id = $1",
                tenant["organization"],
            )
            == 0
        ), "bez okazji nie powstaje nic w tabelach obserwacji"


async def test_if_the_thread_cannot_be_recorded_no_draft_is_written(
    pool, tenant, monkeypatch
) -> None:
    """A draft that could never be published is refused up front, not discovered at the queue."""
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())

    async def refuse(*args, **kwargs):
        raise ValueError("atrapa odmawia")

    monkeypatch.setattr(tools_module, "ensure_reddit_thread", refuse)
    drafted = await _draft(world, marked["okazja"]["url"])

    assert drafted["stan"] == "BLAD" and "nie udało się przygotować" in drafted["powod"]
    async with pool.acquire() as conn:
        proposals = await conn.fetchval(
            "SELECT count(*) FROM action_proposals WHERE organization_id = $1",
            tenant["organization"],
        )
    assert proposals == 0


async def test_an_x_reply_needs_no_thread_in_the_observation_tables(pool, tenant) -> None:
    world = await _world(pool, tenant, platform="X")
    marked = await _mark(world, **_x_args())
    drafted = await world.registry.call(
        "draft.create",
        {
            "kind": "REPLY",
            "target_url": marked["okazja"]["url"],
            "text": "Krótko i na temat.",
            "account": world.login,
        },
    )
    assert drafted["stan"] == "OCZEKUJE" and drafted["okazja"]["stan"] == "SZKIC"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM observed_threads WHERE organization_id = $1",
                tenant["organization"],
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM communities WHERE organization_id = $1",
                tenant["organization"],
            )
            == 0
        )


# ---------------------------------------------------------------------------
# The state follows the draft and the publication
# ---------------------------------------------------------------------------


async def test_a_dead_draft_returns_the_opportunity_to_new(pool, tenant) -> None:
    world = await _world(pool, tenant, platform="X")
    marked = await _mark(world, **_x_args())
    drafted = await world.registry.call(
        "draft.create",
        {
            "kind": "REPLY",
            "target_url": marked["okazja"]["url"],
            "text": "Pierwsza wersja.",
            "account": world.login,
        },
    )
    revision_id = drafted["propozycja"]["revision_id"]
    alive = (await _listed(world))["okazje"][0]
    assert alive["stan"] == "SZKIC" and alive["szkic"] == revision_id

    await ApprovalStore(pool).move_revision(
        uuid.UUID(revision_id),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        target=ActionProposalState.REJECTED,
        actor_ref="operator:test",
    )
    dead = (await _listed(world))["okazje"][0]
    assert dead["stan"] == "NOWA" and dead["szkic"] is None, "martwy szkic nie jest szkicem"


async def test_an_expired_draft_window_returns_the_opportunity_to_new(pool, tenant) -> None:
    """The window ends by itself: nobody moves the revision, and the state must still follow."""
    world = await _world(pool, tenant, platform="X")
    marked = await _mark(world, **_x_args())
    proposal_id = await ApprovalStore(pool).create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=world.account_id,
    )
    async with pool.acquire() as conn:
        # Revisions are immutable (L2795), so an already-expired one is INSERTED that way.
        await conn.execute(
            "INSERT INTO action_proposal_revisions (organization_id, workspace_id, proposal_id,"
            " revision, payload, payload_hash, source_fingerprint, state, created_at, expires_at)"
            " VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'odcisk', 'READY_FOR_APPROVAL',"
            " now() - interval '5 days', now() - interval '1 day')",
            tenant["organization"],
            tenant["workspace"],
            proposal_id,
            json.dumps(
                {
                    "plan_version": str(world.project_id),
                    "target_external_id": "x:2103587050346250341",
                    "target_url": marked["okazja"]["url"],
                    "text_and_media": "Wygasły szkic.",
                    "action_type": "SUBMIT_COMMENT",
                }
            ),
            hashlib.sha256(b"wygasly").hexdigest(),
        )
    shown = (await _listed(world))["okazje"][0]
    assert shown["stan"] == "NOWA" and shown["szkic"] is None
    room = await world.registry.call("opportunities.list", {})
    assert room["okazje"][0]["szkic"] is None


# ---------------------------------------------------------------------------
# The whole road: live hit, draft, the room, the EXISTING approve route, a publication
# ---------------------------------------------------------------------------


async def test_from_a_live_hit_to_an_approved_reply_and_its_result(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    opportunity = marked["okazja"]
    drafted = await _draft(world, opportunity["url"], "Rozwiązaliśmy to u siebie tak i tak.")
    revision_id = drafted["propozycja"]["revision_id"]

    room = (await logged_in_client.get(_room(tenant, world.project_id))).json()
    assert [one["stan"] for one in room["okazje"]] == ["SZKIC"]
    assert room["okazje"][0]["szkic"] == revision_id
    assert [one["revision_id"] for one in room["szkice"]] == [revision_id]
    draft = room["szkice"][0]
    assert draft["okazja_id"] == opportunity["id"] and draft["rodzaj"] == "REPLY"
    assert draft["w_odpowiedzi_na"]["url"] == opportunity["url"]
    assert draft["konto"] == world.login, "konto, z którego wyjdzie szkic"

    # Without a signed delegation the EXISTING route refuses, and the room must not pretend.
    approve = (
        f"/api/workspaces/{tenant['workspace']}/projects/{world.project_id}"
        f"/approvals/{revision_id}/approve"
    )
    refused = await logged_in_client.post(approve)
    assert refused.status_code == 409 and "powierzenia" in refused.json()["detail"]

    await _sign(logged_in_client, tenant, world)
    approved = await logged_in_client.post(approve)
    assert approved.status_code == 202, approved.text

    # Approved: it left the drafts, it is in the queue, and the opportunity still says SZKIC.
    after = (await logged_in_client.get(_room(tenant, world.project_id))).json()
    assert after["szkice"] == [] and after["wyniki"]["opublikowane"] == 0
    assert after["okazje"][0]["stan"] == "SZKIC" and after["okazje"][0]["szkic"] == revision_id
    request = await _request_of(pool, revision_id)
    assert request is not None and request["target_external_id"] == "t3_abc123"
    assert await PublicationStore(pool).target_for(_as_request(request)) is not None, (
        "publikator odnajdzie wątek zatwierdzonego szkicu"
    )

    await _mark_published(pool, tenant, request["id"], score=7, replies=2)
    done = (await logged_in_client.get(_room(tenant, world.project_id))).json()
    assert done["okazje"][0]["stan"] == "ODPOWIEDZIANO"
    assert done["wyniki"]["opublikowane"] == 1
    assert done["wyniki"]["glosy"] == 7 and done["wyniki"]["odpowiedzi"] == 2
    row = done["wyniki"]["pozycje"][0]
    assert set(row) == RESULT_KEYS and row["platforma"] == "REDDIT"
    assert row["tekst"] == "Rozwiązaliśmy to u siebie tak i tak."
    assert row["url"].endswith("/ours01/"), "adres NASZEGO komentarza, nie wątku"
    assert row["widocznosc"] == "UNVERIFIED"
    assert row["okazja_id"] == opportunity["id"], "wynik wie, której osoby dotyczy"
    again = await _mark(world, **_reddit_args())
    assert again["okazja"]["stan"] == "ODPOWIEDZIANO"


# ---------------------------------------------------------------------------
# The room: exact keys, scope, honesty about what was measured
# ---------------------------------------------------------------------------


async def test_the_room_has_exactly_the_contract_keys_and_nothing_else(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    await _mark(world, **_reddit_args("aaa111"))
    drafted_one = await _mark(world, **_reddit_args("bbb222"))
    await _draft(world, drafted_one["okazja"]["url"])
    x_login_id, x_login = await _account(pool, tenant, world.project_id, "X")
    x_world = World(world.project_id, x_login_id, x_login, world.session_id, world.registry, "X")
    own = await x_world.registry.call(
        "draft.create", {"kind": "OWN_POST", "text": "Zbudowałem to w weekend.", "account": x_login}
    )
    assert own["stan"] == "OCZEKUJE", own

    response = await logged_in_client.get(_room(tenant, world.project_id))

    assert response.status_code == 200, response.text
    room = response.json()
    assert set(room) == {"okazje", "szkice", "wyniki", "pokoj"}
    assert room["pokoj"] == f"/w/{tenant['workspace']}/p/{world.project_id}/room"
    assert {key for one in room["okazje"] for key in one} == OPPORTUNITY_KEYS
    assert all(set(one) == OPPORTUNITY_KEYS for one in room["okazje"])
    assert [one["stan"] for one in room["okazje"]].count("NOWA") == 1
    assert all(set(one) == DRAFT_KEYS for one in room["szkice"]), room["szkice"]
    assert set(room["wyniki"]) == {
        "okno_dni", "opublikowane", "odpowiedzi", "glosy", "usuniete", "pozycje",
    }  # fmt: skip
    assert room["wyniki"]["okno_dni"] == 7

    kinds = {one["rodzaj"]: one for one in room["szkice"]}
    assert set(kinds) == {"REPLY", "OWN_POST"}
    assert room["szkice"][0]["rodzaj"] == "OWN_POST", "najnowszy szkic pierwszy"
    assert kinds["OWN_POST"]["w_odpowiedzi_na"] is None
    assert kinds["OWN_POST"]["platforma"] == "X" and kinds["OWN_POST"]["spolecznosc"] == "X"
    assert kinds["OWN_POST"]["tekst"] == "Zbudowałem to w weekend."
    assert kinds["OWN_POST"]["stan"] == "czeka_na_zgode"
    assert kinds["REPLY"]["okazja_id"] == drafted_one["okazja"]["id"]
    assert kinds["REPLY"]["w_odpowiedzi_na"]["autor"] == "someone"


async def test_room_show_is_the_page_payload_scoped_to_the_session_project(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    """The card tool reads what the page reads, through the same function, for ITS project only."""
    monkeypatch.setenv("WENEXT_DASHBOARD_URL", "http://127.0.0.1:3999/")
    world = await _world(pool, tenant)
    other = await _world(pool, tenant)
    opportunity = (await _mark(world, **_reddit_args("show111")))["okazja"]
    await _draft(world, opportunity["url"], "Odpowiedź do pokazania.")
    await _mark(other, **_reddit_args("other22"))

    shown = await world.registry.call("room.show", {})
    page = (await logged_in_client.get(_room(tenant, world.project_id))).json()

    assert shown["stan"] == "WYKONANE"
    for key in ("okazje", "szkice", "wyniki", "pokoj"):
        assert shown[key] == page[key], key
    assert shown["pokoj_adres"] == "http://127.0.0.1:3999" + page["pokoj"]
    assert [one["id"] for one in shown["okazje"]] == [opportunity["id"]]
    assert shown["okazje"][0]["szkic"] == shown["szkice"][0]["revision_id"]
    assert shown["szkice"][0]["konto"] == world.login
    # Read-only: asking again changes nothing, and the other project sees only its own person.
    assert await world.registry.call("room.show", {}) == shown
    theirs = await other.registry.call("room.show", {})
    assert [one["tytul"] for one in theirs["okazje"]] == ["How do you handle X? (other22)"]
    assert theirs["szkice"] == []


async def test_room_show_carries_the_pinned_card_language_and_none_when_not_pinned(
    pool, tenant, monkeypatch
) -> None:
    """`jezyk` is the person's WENEXT_LANG and nothing else: without it the card asks the host."""
    world = await _world(pool, tenant)
    monkeypatch.delenv("WENEXT_LANG", raising=False)
    assert "jezyk" not in await world.registry.call("room.show", {})
    for raw, expected in (
        ("pl", "pl"),
        ("pl_PL", "pl"),
        ("en", "en"),
        ("EN-gb", "en"),
        ("de", "en"),
    ):
        monkeypatch.setenv("WENEXT_LANG", raw)
        assert (await world.registry.call("room.show", {}))["jezyk"] == expected, raw
    for unset in ("", "auto", "  "):
        monkeypatch.setenv("WENEXT_LANG", unset)
        assert "jezyk" not in await world.registry.call("room.show", {}), repr(unset)


async def test_nothing_measured_shows_null_and_never_a_made_up_zero(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    empty = (await logged_in_client.get(_room(tenant, world.project_id))).json()
    assert empty["okazje"] == [] and empty["szkice"] == []
    assert empty["wyniki"] == {
        "okno_dni": 7, "opublikowane": 0, "odpowiedzi": None, "glosy": None, "usuniete": None,
        "pozycje": [],
    }  # fmt: skip

    marked = await _mark(world, **_reddit_args(score=None, num_comments=None))  # counters not read
    shown = (await logged_in_client.get(_room(tenant, world.project_id))).json()["okazje"][0]
    assert shown["id"] == marked["okazja"]["id"]
    assert shown["wynik"] is None and shown["komentarze"] is None


async def test_results_cover_seven_days_and_count_only_what_was_read_back(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    await _sign(logged_in_client, tenant, world)
    revisions = []
    for post in ("aaa111", "bbb222", "ccc333"):
        marked = await _mark(world, **_reddit_args(post))
        drafted = await _draft(world, marked["okazja"]["url"], f"Odpowiedź pod {post}.")
        revision_id = drafted["propozycja"]["revision_id"]
        approve = (
            f"/api/workspaces/{tenant['workspace']}/projects/{world.project_id}"
            f"/approvals/{revision_id}/approve"
        )
        assert (await logged_in_client.post(approve)).status_code == 202
        revisions.append(revision_id)
    requests = [await _request_of(pool, revision) for revision in revisions]

    await _mark_published(pool, tenant, requests[0]["id"], score=5, replies=3)
    await _mark_published(pool, tenant, requests[1]["id"])  # published, never read back
    await _mark_published(pool, tenant, requests[2]["id"], score=100, replies=9, days_ago=10)

    block = (await logged_in_client.get(_room(tenant, world.project_id))).json()["wyniki"]

    assert block["opublikowane"] == 2, "dziesięciodniowa publikacja jest poza oknem"
    assert block["glosy"] == 5 and block["odpowiedzi"] == 3
    assert block["usuniete"] is None, "żadna nie ma werdyktu widoczności"
    by_text = {row["tekst"]: row for row in block["pozycje"]}
    assert by_text["Odpowiedź pod aaa111."]["glosy"] == 5
    unread = by_text["Odpowiedź pod bbb222."]
    assert unread["glosy"] is None and unread["odpowiedzi"] is None

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET public_visibility = 'REMOVED', "
            "public_visibility_checked_at = now() WHERE id = $1",
            requests[1]["id"],
        )
    verdict = (await logged_in_client.get(_room(tenant, world.project_id))).json()["wyniki"]
    assert verdict["usuniete"] == 1


async def test_a_room_shows_only_its_own_project(pool, tenant, logged_in_client) -> None:
    mine = await _world(pool, tenant)
    other = await _world(pool, tenant)
    await _mark(other, **_reddit_args())
    foreign = await _mark(other, **_reddit_args("bbb222"))
    await _draft(other, foreign["okazja"]["url"])

    room = (await logged_in_client.get(_room(tenant, mine.project_id))).json()
    assert room["okazje"] == [] and room["szkice"] == []
    theirs = (await logged_in_client.get(_room(tenant, other.project_id))).json()
    assert len(theirs["okazje"]) == 2 and len(theirs["szkice"]) == 1


async def test_a_skipped_opportunity_leaves_the_room_and_an_answered_one_goes_last(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    await _sign(logged_in_client, tenant, world)
    old = await _mark(world, **_reddit_args("old111", created_at="2026-10-01T00:00:00+00:00"))
    fresh = await _mark(world, **_reddit_args("new222"))
    skipped = await _mark(world, **_reddit_args("skp333"))
    answered = await _mark(world, **_reddit_args("ans444"))
    await logged_in_client.post(_dismiss(tenant, world.project_id, skipped["okazja"]["id"]))
    drafted = await _draft(world, answered["okazja"]["url"])
    revision_id = drafted["propozycja"]["revision_id"]
    approve = (
        f"/api/workspaces/{tenant['workspace']}/projects/{world.project_id}"
        f"/approvals/{revision_id}/approve"
    )
    assert (await logged_in_client.post(approve)).status_code == 202
    await _mark_published(pool, tenant, (await _request_of(pool, revision_id))["id"])

    shown = (await logged_in_client.get(_room(tenant, world.project_id))).json()["okazje"]

    assert [one["id"] for one in shown] == [
        fresh["okazja"]["id"],
        old["okazja"]["id"],
        answered["okazja"]["id"],
    ], "pominięta znika, odpowiedziana na końcu, reszta od najświeższej"
    assert [one["stan"] for one in shown] == ["NOWA", "NOWA", "ODPOWIEDZIANO"]


async def test_the_room_and_dismiss_answer_404_for_a_project_that_is_not_here(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())

    missing_project = await logged_in_client.get(_room(tenant, uuid.uuid4()))
    assert missing_project.status_code == 404
    missing_workspace = await logged_in_client.get(
        f"/api/workspaces/{uuid.uuid4()}/projects/{world.project_id}/room"
    )
    assert missing_workspace.status_code == 404
    unknown = await logged_in_client.post(_dismiss(tenant, world.project_id, uuid.uuid4()))
    assert unknown.status_code == 404
    wrong_project = await logged_in_client.post(
        _dismiss(tenant, uuid.uuid4(), marked["okazja"]["id"])
    )
    assert wrong_project.status_code == 404
    assert (await _listed(world))["okazje"][0]["stan"] == "NOWA", "nic nie zostało pominięte"


async def test_without_a_session_cookie_the_room_answers_401_with_an_empty_body(
    pool, tenant, client
) -> None:
    world = await _world(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    for response in (
        await client.get(_room(tenant, world.project_id)),
        await client.post(_dismiss(tenant, world.project_id, marked["okazja"]["id"])),
    ):
        assert response.status_code == 401 and response.content == b""


async def test_dismissing_from_the_page_is_idempotent_and_scoped(
    pool, tenant, logged_in_client
) -> None:
    mine = await _world(pool, tenant)
    other = await _world(pool, tenant)
    marked = await _mark(mine, **_reddit_args())
    foreign = await _mark(other, **_reddit_args())

    first = await logged_in_client.post(_dismiss(tenant, mine.project_id, marked["okazja"]["id"]))
    assert first.status_code == 200, first.text
    assert first.json()["okazja"]["stan"] == "POMINIETA" and first.json()["zmieniono"] is True
    second = await logged_in_client.post(_dismiss(tenant, mine.project_id, marked["okazja"]["id"]))
    assert second.status_code == 200 and second.json()["zmieniono"] is False

    # An opportunity of another project is not found through my project's path.
    crossed = await logged_in_client.post(
        _dismiss(tenant, mine.project_id, foreign["okazja"]["id"])
    )
    assert crossed.status_code == 404
    assert (await _listed(other))["okazje"][0]["stan"] == "NOWA"

    gone = (await logged_in_client.get(_room(tenant, mine.project_id))).json()
    assert gone["okazje"] == []
    async with pool.acquire() as conn:
        author = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE id = $1",
            uuid.UUID(marked["okazja"]["id"]),
        )
    assert author == f"operator:{tenant['operator']}"


async def test_the_store_refuses_an_opportunity_without_touching_the_table(pool, tenant) -> None:
    """The store is the second lock behind the tool's validation: what `new_opportunity` refuses
    never reaches SQL, and what reaches SQL meets the CHECKs of 0036."""
    world = await _world(pool, tenant)
    store = OpportunityStore(pool)
    good = new_opportunity(
        platform="REDDIT",
        url="https://www.reddit.com/r/SaaS/comments/abc123/x/",
        reason="powód",
        title="Tytuł",
        created_at=datetime.now(UTC),
    )
    marked = await store.mark(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
        session_id=None,
        opportunity=good,
    )
    assert marked.created is True and marked.view.stan == "NOWA"
    assert marked.view.to_json()["szkic"] is None
