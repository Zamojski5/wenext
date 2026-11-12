"""Migracja 0040: pętla uczenia, czyli opublikowane rzeczy, ich liczniki w czasie i wnioski.

O CO TU CHODZI. Wniosek "odpowiedzi pod młodymi postami mają więcej wyświetleń" jest wart tyle, ile
dane pod nim. Sześć sposobów, żeby je zepsuć, i każdy ma tu swój przypadek:

  * ta sama rzecz dwa razy (z wniosku i z dziennika), czyli dwa głosy tej samej odpowiedzi;
  * dwie migawki tego samego horyzontu, czyli średnia z jednego pomiaru liczona podwójnie;
  * migawka bez żadnego licznika, czyli "ktoś spojrzał i nic nie zobaczył" przedstawione jako dana;
  * haczyk spoza listy albo bez autora etykiety (reguła czy dziennik);
  * wniosek bez dowodu: bez klucza porównania albo bez `n` po którejś stronie, albo wpisany jako
    słowo człowieka;
  * migawka wisząca pod rzeczą z innej organizacji.
"""

from __future__ import annotations

import json
import re
import uuid

import asyncpg
import pytest
from db_schema.expected_schema import (
    KNOWLEDGE_ITEM_KINDS,
    LEARNING_POST_HOOKS,
    LEARNING_POSTS_COLUMNS,
    LEARNING_SNAPSHOT_HORIZONS,
    LEARNING_SNAPSHOT_SOURCES,
    LEARNING_SNAPSHOTS_COLUMNS,
)

pytestmark = pytest.mark.db


async def _project(connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    organization_id = await connection.fetchval(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"Firma {uuid.uuid4()}"
    )
    workspace_id = await connection.fetchval(
        "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
        organization_id,
    )
    project_id = await connection.fetchval(
        "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'projekt')"
        " RETURNING id",
        organization_id,
        workspace_id,
    )
    return organization_id, workspace_id, project_id


async def _columns(connection, table: str) -> list[str]:
    rows = await connection.fetch(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return sorted(row["column_name"] for row in rows)


_POST = (
    "INSERT INTO learning_posts (organization_id, workspace_id, project_id, platform, kind,"
    " origin, external_id, permalink, published_at, target_external_id, hook, hook_source)"
    " VALUES ($1, $2, $3, $4, $5, 'PUBLICATION', $6, $7, now(), $8, $9, $10) RETURNING id"
)


async def _post(
    connection,
    ids,
    *,
    external_id: str | None = None,
    platform: str = "X",
    kind: str = "REPLY",
    target: str | None = "x:1",
    hook: str | None = None,
    hook_source: str | None = None,
):
    organization_id, workspace_id, project_id = ids
    external = external_id or f"x:{uuid.uuid4().int % 10**18}"
    return await connection.fetchval(
        _POST,
        organization_id,
        workspace_id,
        project_id,
        platform,
        kind,
        external,
        f"https://x.com/someone/status/{external.removeprefix('x:')}",
        target if kind == "REPLY" else None,
        hook,
        hook_source,
    )


_SNAPSHOT = (
    "INSERT INTO learning_snapshots (organization_id, post_id, horizon_hours, measured_at,"
    " age_minutes, views, likes, replies, reposts, source)"
    " VALUES ($1, $2, $3, now(), $4, $5, $6, $7, $8, $9) RETURNING id"
)


async def test_both_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(LEARNING_POSTS_COLUMNS) == 28 and len(LEARNING_SNAPSHOTS_COLUMNS) == 13
    async with pool.acquire() as connection:
        posts = await _columns(connection, "learning_posts")
        snapshots = await _columns(connection, "learning_snapshots")
    assert posts, "nie ma tabeli learning_posts, migracja 0040 nie została zastosowana"
    assert posts == sorted(LEARNING_POSTS_COLUMNS), posts
    assert snapshots == sorted(LEARNING_SNAPSHOTS_COLUMNS), snapshots


async def test_the_closed_lists_in_the_database_are_the_declared_ones(pool) -> None:
    async def definition(table: str, name: str) -> str:
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = $1 AND conrelid = $2::regclass",
                name,
                table,
            )
        assert value, f"{table}: brak ograniczenia {name}"
        return value

    def quoted(text: str) -> set[str]:
        return set(re.findall(r"'([A-Z_]+)'", text))

    assert quoted(await definition("learning_posts", "learning_posts_hook_is_known")) == set(
        LEARNING_POST_HOOKS
    )
    assert quoted(
        await definition("learning_snapshots", "learning_snapshots_source_is_known")
    ) == set(LEARNING_SNAPSHOT_SOURCES)
    horizons = await definition("learning_snapshots", "learning_snapshots_horizon_is_known")
    assert {int(number) for number in re.findall(r"\b(\d+)\b", horizons)} == set(
        LEARNING_SNAPSHOT_HORIZONS
    )
    kinds = await definition("knowledge_items", "knowledge_items_kind_is_known")
    assert quoted(kinds) == set(KNOWLEDGE_ITEM_KINDS) and "LESSON" in quoted(kinds)


async def test_the_same_item_from_two_sources_is_one_row(pool) -> None:
    async with pool.acquire() as connection:
        ids = await _project(connection)
        await _post(connection, ids, external_id="x:2109722713741029594")
        with pytest.raises(asyncpg.UniqueViolationError) as twice:
            await _post(connection, ids, external_id="x:2109722713741029594")
        assert "learning_posts_one_per_item" in str(twice.value)
        # The same id on the OTHER platform is another item: ids of two platforms never meet.
        await _post(connection, ids, external_id="x:2109722713741029594", platform="REDDIT")


async def test_a_hook_is_from_the_list_and_has_a_labeller(pool) -> None:
    async with pool.acquire() as connection:
        ids = await _project(connection)
        await _post(connection, ids, hook="OPINIA", hook_source="DIARY")
        await _post(connection, ids)  # neither: the hook was not established
        with pytest.raises(asyncpg.CheckViolationError) as outside:
            await _post(connection, ids, hook="VIRAL", hook_source="CLASSIFIER")
        assert "learning_posts_hook_is_known" in str(outside.value)
        with pytest.raises(asyncpg.CheckViolationError) as unlabelled:
            await _post(connection, ids, hook="DEMO")
        assert "learning_posts_hook_has_its_source" in str(unlabelled.value)
        with pytest.raises(asyncpg.CheckViolationError) as nobody:
            await _post(connection, ids, hook="DEMO", hook_source="MODEL")
        assert "learning_posts_hook_source_is_known" in str(nobody.value)


async def test_an_own_post_has_no_target_and_counters_are_not_negative(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = ids = await _project(connection)
        with pytest.raises(asyncpg.CheckViolationError) as target:
            await connection.fetchval(
                _POST,
                organization_id,
                workspace_id,
                project_id,
                "X",
                "OWN_POST",
                "x:5",
                "https://x.com/a/status/5",
                "x:4",
                None,
                None,
            )
        assert "learning_posts_own_post_has_no_target" in str(target.value)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.fetchval(
                "INSERT INTO learning_posts (organization_id, workspace_id, platform, kind,"
                " origin, external_id, permalink, published_at, target_followers)"
                " VALUES ($1, $2, 'X', 'REPLY', 'DIARY', 'x:9', 'https://x.com/a/status/9',"
                " now(), -1)",
                organization_id,
                workspace_id,
            )
        assert ids


async def test_one_snapshot_per_horizon_and_an_empty_reading_is_not_a_snapshot(pool) -> None:
    async with pool.acquire() as connection:
        ids = await _project(connection)
        post = await _post(connection, ids)
        organization_id = ids[0]
        await connection.fetchval(
            _SNAPSHOT, organization_id, post, 24, 1500, 140, 2, 1, 0, "FXTWITTER"
        )
        with pytest.raises(asyncpg.UniqueViolationError) as twice:
            await connection.fetchval(
                _SNAPSHOT, organization_id, post, 24, 1600, 150, 3, 1, 0, "CAPSULE"
            )
        assert "learning_snapshots_one_per_horizon" in str(twice.value)
        # Readings outside every horizon are evidence, and there may be several of them.
        for age in (1080, 2500):
            await connection.fetchval(
                _SNAPSHOT, organization_id, post, None, age, 100 + age, None, None, None, "DIARY"
            )
        with pytest.raises(asyncpg.CheckViolationError) as empty:
            await connection.fetchval(
                _SNAPSHOT, organization_id, post, 6, 400, None, None, None, None, "CAPSULE"
            )
        assert "learning_snapshots_read_something" in str(empty.value)
        # Zero is a measurement, and a Reddit score may be negative.
        await connection.fetchval(
            _SNAPSHOT, organization_id, post, 1, 70, 0, -3, 0, 0, "OBSERVATION"
        )


async def test_a_snapshot_has_a_known_horizon_and_source_and_a_post_of_its_organization(
    pool,
) -> None:
    async with pool.acquire() as connection:
        ids = await _project(connection)
        other = await _project(connection)
        post = await _post(connection, ids)
        organization_id = ids[0]
        with pytest.raises(asyncpg.CheckViolationError) as horizon:
            await connection.fetchval(
                _SNAPSHOT, organization_id, post, 12, 700, 1, None, None, None, "CAPSULE"
            )
        assert "learning_snapshots_horizon_is_known" in str(horizon.value)
        with pytest.raises(asyncpg.CheckViolationError) as source:
            await connection.fetchval(
                _SNAPSHOT, organization_id, post, 24, 1500, 1, None, None, None, "GUESS"
            )
        assert "learning_snapshots_source_is_known" in str(source.value)
        with pytest.raises(asyncpg.ForeignKeyViolationError) as foreign:
            await connection.fetchval(
                _SNAPSHOT, other[0], post, 24, 1500, 1, None, None, None, "CAPSULE"
            )
        assert "learning_snapshots_belong_to_post" in str(foreign.value)


async def test_deleting_a_post_takes_its_snapshots_with_it(pool) -> None:
    async with pool.acquire() as connection:
        ids = await _project(connection)
        post = await _post(connection, ids)
        await connection.fetchval(_SNAPSHOT, ids[0], post, 24, 1500, 5, 0, 0, 0, "CAPSULE")
        await connection.execute("DELETE FROM learning_posts WHERE id = $1", post)
        left = await connection.fetchval(
            "SELECT count(*) FROM learning_snapshots WHERE post_id = $1", post
        )
    assert left == 0


_LESSON = (
    "INSERT INTO knowledge_items (organization_id, workspace_id, project_id, kind, statement,"
    " stated_by, payload) VALUES ($1, $2, $3, 'LESSON', $4, $5, $6::jsonb) RETURNING id"
)

_EVIDENCE = {
    "lesson_key": "X:target_age",
    "winner": {"bucket": "<30 min", "n": 6, "median_views": 140},
    "loser": {"bucket": ">2 h", "n": 5, "median_views": 25},
}


async def test_a_lesson_carries_its_evidence_and_comes_from_the_product(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        first = await connection.fetchval(
            _LESSON,
            organization_id,
            workspace_id,
            project_id,
            "Replies under posts younger than 30 min got a median of 140 views (n=6).",
            "AGENT",
            json.dumps(_EVIDENCE),
        )
        assert first
        # No key to find it by, no `n` on a side, or a person's word: all refused by name.
        for broken in (
            {key: value for key, value in _EVIDENCE.items() if key != "lesson_key"},
            {**_EVIDENCE, "winner": {"bucket": "<30 min", "median_views": 140}},
            {**_EVIDENCE, "loser": "25 views"},
            {key: value for key, value in _EVIDENCE.items() if key != "winner"},
        ):
            with pytest.raises(asyncpg.CheckViolationError) as refused:
                await connection.fetchval(
                    _LESSON,
                    organization_id,
                    workspace_id,
                    project_id,
                    f"Broken {uuid.uuid4()}",
                    "AGENT",
                    json.dumps(broken),
                )
            assert "knowledge_items_lesson_keeps_its_evidence" in str(refused.value)
        with pytest.raises(asyncpg.CheckViolationError) as person:
            await connection.fetchval(
                _LESSON,
                organization_id,
                workspace_id,
                project_id,
                "A person's lesson is a preference, not a measurement.",
                "USER",
                json.dumps(_EVIDENCE),
            )
        assert "knowledge_items_lesson_keeps_its_evidence" in str(person.value)


async def test_a_seeded_rule_is_allowed_only_as_a_named_rule_with_its_source(pool) -> None:
    """The one lesson without a measurement is a RULE, and it says so and says where it is from."""
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        rule = {
            "lesson_key": "rule:humor-never-pitches",
            "rule": True,
            "measured": False,
            "source": "founder, 13.10.2026",
        }
        assert await connection.fetchval(
            _LESSON,
            organization_id,
            workspace_id,
            project_id,
            "A joke never pivots to the product.",
            "AGENT",
            json.dumps(rule),
        )
        # A rule with no source, a rule that is not `true`, and a rule nobody stated are all
        # lessons with no evidence, so they are refused like any other.
        for broken, stated_by in (
            ({key: value for key, value in rule.items() if key != "source"}, "AGENT"),
            ({**rule, "rule": False}, "AGENT"),
            ({**rule, "source": ""}, "AGENT"),
            (rule, "USER"),
        ):
            with pytest.raises(asyncpg.CheckViolationError) as refused:
                await connection.fetchval(
                    _LESSON,
                    organization_id,
                    workspace_id,
                    project_id,
                    f"Broken rule {uuid.uuid4()}",
                    stated_by,
                    json.dumps(broken),
                )
            assert "knowledge_items_lesson_keeps_its_evidence" in str(refused.value)
