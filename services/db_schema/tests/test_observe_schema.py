"""Migration 0004 — observation and judgment (Outcome A, `docs/OUTCOMES.md` §3, §5).

What this file proves, and in each case what the failing shape would have been:

* the eight tables exist with exactly the columns `expected_schema` names — a column renamed
  in the migration fails here and not in a control-plane query at 3 a.m.;
* `thread_metric_snapshots` and `thread_judgments` are APPEND-ONLY by trigger, measured by
  trying to UPDATE and DELETE a real row, not by reading `pg_trigger`;
* an `Observed[int]` column pair refuses "both" and "neither": a snapshot with `score NULL` and
  no reason would be the "we do not know whether we know" this schema exists to forbid;
* `ENGAGE` without `what_user_could_add` is refused BY THE DATABASE, independently of the
  `JudgmentRecord` validator in contracts — two locks on the same door;
* a child pointing at a parent from another organization is refused by the triple key, the same
  test D-020 requires of every workspace table;
* the same thread read twice is the same row (`UNIQUE (…, community_id, external_id)`).

The tenant fixture is local to this file on purpose, like `world` in `test_f1_schema.py`: the
package's `conftest.py` belongs to the schema suite as a whole and grows nothing per migration.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    OBSERVE_APPEND_ONLY_TABLES,
    OBSERVE_TABLE_COLUMNS,
    OBSERVE_TRIPLE_PARENTS,
)

pytestmark = pytest.mark.db

SHA = "0" * 64


@pytest_asyncio.fixture
async def tenant(pool):
    """One organization and one workspace, fresh per test."""
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-0004') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization_id,
        )
    return {"organization": organization_id, "workspace": workspace_id}


async def _community(connection, tenant, key: str = "ask") -> uuid.UUID:
    return await connection.fetchval(
        """
        INSERT INTO communities
            (organization_id, workspace_id, platform, community_key, display_name, access_mode,
             poll_interval_seconds)
        VALUES ($1, $2, 'HACKER_NEWS', $3, 'Hacker News · Ask HN', 'PUBLIC_API_NO_ACCOUNT', 600)
        RETURNING id
        """,
        tenant["organization"],
        tenant["workspace"],
        key,
    )


async def _thread(connection, tenant, community_id, external_id: str = "1") -> uuid.UUID:
    return await connection.fetchval(
        """
        INSERT INTO observed_threads
            (organization_id, workspace_id, community_id, external_id, url, title,
             created_at_source, first_observed_at, last_observed_at, raw_sha256)
        VALUES ($1, $2, $3, $4, 'https://news.ycombinator.com/item?id=1', 'Ask HN: x',
                now(), now(), now(), $5)
        RETURNING id
        """,
        tenant["organization"],
        tenant["workspace"],
        community_id,
        external_id,
        SHA,
    )


async def _profile(connection, tenant, version: int = 1) -> uuid.UUID:
    return await connection.fetchval(
        """
        INSERT INTO user_profile_versions
            (organization_id, workspace_id, version, knows_deeply, positions, would_never_claim,
             voice_samples)
        VALUES ($1, $2, $3, 'distributed queues', 'boring tech wins', 'benchmarks I never ran', '')
        RETURNING id
        """,
        tenant["organization"],
        tenant["workspace"],
        version,
    )


async def _judgment(connection, tenant, thread_id, profile_id, *, decision: str, contribution):
    return await connection.fetchval(
        """
        INSERT INTO thread_judgments
            (organization_id, workspace_id, thread_id, decision, reasoning, what_user_could_add,
             confidence, user_profile_version_id, model, effort, prompt_name, prompt_sha256)
        VALUES ($1, $2, $3, $4, 'the author asks exactly what the user ran for a year', $5,
                0.7, $6, 'gpt-5.6-luna', 'low', 'judge_thread', $7)
        RETURNING id
        """,
        tenant["organization"],
        tenant["workspace"],
        thread_id,
        decision,
        contribution,
        profile_id,
        SHA,
    )


async def _columns(connection, table: str) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def test_the_observe_tables_have_exactly_the_declared_columns(pool) -> None:
    """Eight tables from 0004, the voice card from 0005, three pilot tables from 0006 — twelve,
    counted before compared."""
    assert len(OBSERVE_TABLE_COLUMNS) == 15, sorted(OBSERVE_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, declared in OBSERVE_TABLE_COLUMNS.items():
            assert len(declared) >= 7, f"{table}: lista kolumn podejrzanie krótka ({len(declared)})"
            actual = await _columns(connection, table)
            assert actual, f"tabela {table} nie istnieje albo nie ma kolumn"
            assert actual == set(declared), (
                f"{table}: tylko w bazie {sorted(actual - set(declared))}, "
                f"tylko w deklaracji {sorted(set(declared) - actual)}"
            )


async def test_the_triple_parents_are_declared_for_every_child_table(pool) -> None:
    """Count first (`all([])`), then every FK really is a three-column key to the named parent.
    Six children in 0004, the voice card in 0005, three pilot tables in 0006 make ten; 0007 adds
    three publication tables and 0008 the judge's verdicts: fourteen."""
    assert len(OBSERVE_TRIPLE_PARENTS) == 14, OBSERVE_TRIPLE_PARENTS
    async with pool.acquire() as connection:
        for table, parent in OBSERVE_TRIPLE_PARENTS.items():
            rows = await connection.fetch(
                """
                SELECT c.conname,
                       (SELECT array_agg(a.attname ORDER BY k.ordinality)
                          FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
                          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
                         AS columns,
                       c.confrelid::regclass::text AS parent
                FROM pg_constraint c
                WHERE c.contype = 'f' AND c.conrelid = ('public.' || $1)::regclass
                """,
                table,
            )
            triples = [
                r
                for r in rows
                if r["parent"] == parent
                and len(r["columns"]) == 3
                and list(r["columns"][:2]) == ["organization_id", "workspace_id"]
            ]
            assert triples, (
                f"{table} nie wskazuje na {parent} trójką; klucze: "
                f"{[(r['conname'], list(r['columns']), r['parent']) for r in rows]}"
            )


async def test_a_child_from_another_organization_is_refused_by_the_database(pool, tenant) -> None:
    """D-020's test, applied to 0004: the DATABASE refuses, not application code."""
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        other_org = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('intruder') RETURNING id"
        )
        other_ws = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            other_org,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.fetchval(
                """
                INSERT INTO observed_threads
                    (organization_id, workspace_id, community_id, external_id, url, title,
                     created_at_source, first_observed_at, last_observed_at, raw_sha256)
                VALUES ($1, $2, $3, '1', 'https://x', 't', now(), now(), now(), $4)
                RETURNING id
                """,
                other_org,
                other_ws,
                community_id,  # a community of the FIRST organization
                SHA,
            )


async def test_the_same_thread_read_twice_is_one_row(pool, tenant) -> None:
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        await _thread(connection, tenant, community_id, "42")
        with pytest.raises(asyncpg.UniqueViolationError):
            await _thread(connection, tenant, community_id, "42")
        # The same external id in ANOTHER community is a different thread.
        other = await _community(connection, tenant, key="show")
        assert await _thread(connection, tenant, other, "42")


async def test_a_thread_digest_must_be_a_full_sha256(pool, tenant) -> None:
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO observed_threads
                    (organization_id, workspace_id, community_id, external_id, url, title,
                     created_at_source, first_observed_at, last_observed_at, raw_sha256)
                VALUES ($1, $2, $3, '1', 'https://x', 't', now(), now(), now(), 'abc')
                """,
                tenant["organization"],
                tenant["workspace"],
                community_id,
            )


async def test_a_snapshot_metric_is_observed_or_unknown_never_both_never_neither(
    pool, tenant
) -> None:
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        thread_id = await _thread(connection, tenant, community_id)
        sql = """
            INSERT INTO thread_metric_snapshots
                (organization_id, workspace_id, thread_id, observed_at,
                 score, score_unknown_reason, comment_count, comment_count_unknown_reason)
            VALUES ($1, $2, $3, now(), $4, $5, $6, $7)
        """
        args = (tenant["organization"], tenant["workspace"], thread_id)
        # known score, unknown comment count — the honest shape
        await connection.execute(sql, *args, 12, None, None, "NOT_EXPOSED_BY_PLATFORM")
        # neither
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(sql, *args, None, None, 3, None)
        # both
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(sql, *args, 5, "NOT_EXPOSED_BY_PLATFORM", 3, None)
        # a reason that is not an `UnknownReason`
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(sql, *args, None, "DUNNO", 3, None)


@pytest.mark.parametrize("table", OBSERVE_APPEND_ONLY_TABLES)
async def test_append_only_tables_refuse_update_and_delete(pool, tenant, table: str) -> None:
    assert len(OBSERVE_APPEND_ONLY_TABLES) == 2
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        thread_id = await _thread(connection, tenant, community_id)
        if table == "thread_metric_snapshots":
            row_id = await connection.fetchval(
                """
                INSERT INTO thread_metric_snapshots
                    (organization_id, workspace_id, thread_id, observed_at, score, comment_count)
                VALUES ($1, $2, $3, now(), 1, 0) RETURNING id
                """,
                tenant["organization"],
                tenant["workspace"],
                thread_id,
            )
            update = f"UPDATE {table} SET score = 999 WHERE id = $1"
        else:
            profile_id = await _profile(connection, tenant)
            row_id = await _judgment(
                connection, tenant, thread_id, profile_id, decision="IGNORE", contribution=None
            )
            update = f"UPDATE {table} SET decision = 'ENGAGE' WHERE id = $1"
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(update, row_id)
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(f"DELETE FROM {table} WHERE id = $1", row_id)
        assert await connection.fetchval(f"SELECT count(*) FROM {table} WHERE id = $1", row_id) == 1


async def test_engage_without_a_contribution_is_refused_by_the_database(pool, tenant) -> None:
    """The second lock on the door `JudgmentRecord._engage_names_the_contribution` guards."""
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        thread_id = await _thread(connection, tenant, community_id)
        profile_id = await _profile(connection, tenant)
        with pytest.raises(asyncpg.CheckViolationError):
            await _judgment(
                connection, tenant, thread_id, profile_id, decision="ENGAGE", contribution=None
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await _judgment(
                connection, tenant, thread_id, profile_id, decision="ENGAGE", contribution=""
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await _judgment(
                connection, tenant, thread_id, profile_id, decision="MAYBE", contribution=None
            )
        assert await _judgment(
            connection,
            tenant,
            thread_id,
            profile_id,
            decision="ENGAGE",
            contribution="numbers from running this in production",
        )
        # IGNORE is a full row too — negative data (§9).
        assert await _judgment(
            connection, tenant, thread_id, profile_id, decision="IGNORE", contribution=None
        )


async def test_feedback_keeps_history_and_refuses_unknown_verdicts(pool, tenant) -> None:
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        thread_id = await _thread(connection, tenant, community_id)
        profile_id = await _profile(connection, tenant)
        judgment_id = await _judgment(
            connection, tenant, thread_id, profile_id, decision="IGNORE", contribution=None
        )
        sql = """
            INSERT INTO judgment_feedback (organization_id, workspace_id, judgment_id, verdict)
            VALUES ($1, $2, $3, $4)
        """
        args = (tenant["organization"], tenant["workspace"], judgment_id)
        await connection.execute(sql, *args, "DISAGREE")
        await connection.execute(sql, *args, "AGREE")  # changed their mind: a second row
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM judgment_feedback WHERE judgment_id = $1", judgment_id
            )
            == 2
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(sql, *args, "MEH")


async def test_pictures_are_versioned_per_community_and_carry_the_prompt_digest(
    pool, tenant
) -> None:
    async with pool.acquire() as connection:
        community_id = await _community(connection, tenant)
        sql = """
            INSERT INTO community_pictures
                (organization_id, workspace_id, community_id, version, window_start, window_end,
                 content, thread_count, invalid_ref_count, model, effort, prompt_name, prompt_sha256)
            VALUES ($1, $2, $3, $4, now() - interval '1 day', now(), $5::jsonb, 3, 0,
                    'gpt-5.6-terra', 'medium', 'community_picture', $6)
        """
        args = (tenant["organization"], tenant["workspace"], community_id)
        content = json.dumps({"summary": "quiet day"})
        await connection.execute(sql, *args, 1, content, SHA)
        await connection.execute(sql, *args, 2, content, SHA)
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(sql, *args, 2, content, SHA)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(sql, *args, 3, content, "short")
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO community_pictures
                    (organization_id, workspace_id, community_id, version, window_start, window_end,
                     content, thread_count, invalid_ref_count, model, effort, prompt_name,
                     prompt_sha256)
                VALUES ($1, $2, $3, 4, now(), now() - interval '1 hour', '{}'::jsonb, 0, 0,
                        'm', 'e', 'p', $4)
                """,
                *args,
                SHA,
            )


async def test_user_profile_versions_are_unique_per_workspace(pool, tenant) -> None:
    async with pool.acquire() as connection:
        await _profile(connection, tenant, version=1)
        await _profile(connection, tenant, version=2)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _profile(connection, tenant, version=2)


async def test_a_community_key_is_unique_per_platform_and_workspace(pool, tenant) -> None:
    async with pool.acquire() as connection:
        await _community(connection, tenant, key="ask")
        with pytest.raises(asyncpg.UniqueViolationError):
            await _community(connection, tenant, key="ask")
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, poll_interval_seconds)
                VALUES ($1, $2, 'MYSPACE', 'x', 'x', 'UNKNOWN', 600)
                """,
                tenant["organization"],
                tenant["workspace"],
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, poll_interval_seconds)
                VALUES ($1, $2, 'REDDIT', 'r/devops', 'x', 'ACCOUNT_REQUIRED', 5)
                """,
                tenant["organization"],
                tenant["workspace"],
            )


async def test_the_0029_rewrite_moves_only_the_prefix(pool, tenant) -> None:
    """Wyrażenie z migracji 0029, sprawdzone na wierszach, nie w głowie.

    Migracja przepisuje nazwy społeczności z długą pauzą (zakaz z KONWENCJI) na kropkę środkową.
    Ryzyko jest w OFFSECIE `substring`: pomyłka o jeden ucina literę nazwy albo zostawia spację,
    a żaden test tego nie widzi, dopóki ktoś nie spojrzy na ekran. Tu wiersz wchodzi i wychodzi.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO communities
                (organization_id, workspace_id, platform, community_key, display_name,
                 access_mode, poll_interval_seconds)
            VALUES ($1, $2, 'REDDIT', 'r/stara', 'Reddit — r/stara', 'ACCOUNT_REQUIRED', 600),
                   ($1, $2, 'HACKER_NEWS', 'ask-stare', 'Hacker News — Ask HN',
                    'PUBLIC_API_NO_ACCOUNT', 600),
                   ($1, $2, 'REDDIT', 'r/nowa', 'Reddit · r/nowa', 'ACCOUNT_REQUIRED', 600)
            """,
            tenant["organization"],
            tenant["workspace"],
        )

        await connection.execute(
            "UPDATE communities SET display_name = 'Reddit · ' || substring(display_name from 10) "
            "WHERE display_name LIKE 'Reddit — %'"
        )
        await connection.execute(
            "UPDATE communities SET display_name = 'Hacker News · ' "
            "|| substring(display_name from 15) WHERE display_name LIKE 'Hacker News — %'"
        )

        names = {
            row["community_key"]: row["display_name"]
            for row in await connection.fetch(
                "SELECT community_key, display_name FROM communities WHERE organization_id = $1",
                tenant["organization"],
            )
        }
        assert names["r/stara"] == "Reddit · r/stara"
        assert names["ask-stare"] == "Hacker News · Ask HN"
        assert names["r/nowa"] == "Reddit · r/nowa", (
            "wiersz już poprawny nie jest ruszany drugi raz"
        )
