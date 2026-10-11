"""Kto decyduje, czy wolno nam założyć własny wątek w cudzej społeczności (0025).

DLACZEGO TO MA WŁASNY PLIK. Do 19.09 bramka własnych postów czytała klucz z karty głosu, czyli z
artefaktu, który pisze MODEL na podstawie przeczytanych komentarzy. Nic tego klucza nie
zapisywało, więc ścieżka własnych postów była zbudowana i zamknięta na głucho; a gdyby klucz
zaczął powstawać, zgodę na publikowanie od siebie wystawiałby model, i to bez podpisu, który
następna wersja karty i tak by skasowała.

CO JEST MIERZONE:

  * trzy stany, nie dwa: „nikt nie zdecydował" odmawia INNYM zdaniem niż „nie wolno", bo co
    innego ma po nim zrobić człowiek;
  * decyzja ma autora i moment, a cofnięcie zabiera oba, bo podpis pod brakiem decyzji jest
    podpisem pod niczym;
  * decyzja z sąsiedniej przestrzeni nie otwiera mojej;
  * karta głosu, cokolwiek mówi, NIE rozstrzyga.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.observe.store import ObservationStore
from control_plane.presence.own_posts import may_post_in

pytestmark = [pytest.mark.db, pytest.mark.p4]

COMMUNITY = "r/kubernetes"


def now() -> datetime:
    return datetime.now(UTC)


async def _community(pool, tenant, *, key: str = COMMUNITY, workspace=None, card=None):
    async with pool.acquire() as conn:
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            workspace or tenant["workspace"],
            key,
        )
        if card is not None:
            await conn.execute(
                """
                INSERT INTO community_voice_cards
                    (organization_id, workspace_id, community_id, version, window_start,
                     window_end, content, comment_count, invalid_quote_count, model, effort,
                     prompt_name, prompt_sha256)
                VALUES ($1, $2, $3, 1, now() - interval '7 days', now(), $4::jsonb, 10, 0,
                        'test', 'low', 'voice', $5)
                """,
                tenant["organization"],
                workspace or tenant["workspace"],
                community_id,
                json.dumps(card),
                "0" * 64,
            )
    return community_id


async def _verdict(pool, tenant, *, workspace=None, key: str = COMMUNITY):
    async with pool.acquire() as conn:
        return await may_post_in(
            conn,
            organization_id=tenant["organization"],
            workspace_id=workspace or tenant["workspace"],
            platform="REDDIT",
            community_key=key,
        )


async def test_nobody_decided_is_a_refusal_that_asks_a_question(pool, tenant) -> None:
    await _community(pool, tenant)
    verdict = await _verdict(pool, tenant)
    assert verdict.allowed is False
    assert "nikt jeszcze nie zdecydował" in verdict.reason
    # Zdanie ma powiedzieć, GDZIE się decyduje, bo inaczej odmowa jest ślepą uliczką.
    assert "w rozmowie" in verdict.reason and "delegation.propose" in verdict.reason


async def test_a_person_can_allow_it_and_the_row_says_who_and_when(pool, tenant) -> None:
    community_id = await _community(pool, tenant)
    store = ObservationStore(pool)
    moment = now()

    after = await store.decide_own_posts(
        community_id,
        organization_id=tenant["organization"],
        allowed=True,
        operator_id=tenant["operator"],
        now=moment,
    )

    assert after is not None and after.own_posts_allowed is True
    assert after.own_posts_decided_by == tenant["operator"]
    assert after.own_posts_decided_at is not None
    verdict = await _verdict(pool, tenant)
    assert verdict.allowed is True, verdict.reason


async def test_no_is_a_different_sentence_than_nobody_decided(pool, tenant) -> None:
    """Przy „nie wolno" człowiek nie ma nic do zrobienia; przy braku decyzji ma zdecydować.
    Jedno zdanie na oba stany kazałoby agentowi prosić o decyzję, która już zapadła."""
    community_id = await _community(pool, tenant)
    await ObservationStore(pool).decide_own_posts(
        community_id,
        organization_id=tenant["organization"],
        allowed=False,
        operator_id=tenant["operator"],
        now=now(),
    )

    verdict = await _verdict(pool, tenant)

    assert verdict.allowed is False
    assert "nie dopuścił" in verdict.reason
    assert "nikt jeszcze nie zdecydował" not in verdict.reason


async def test_undoing_a_decision_takes_the_signature_with_it(pool, tenant) -> None:
    community_id = await _community(pool, tenant)
    store = ObservationStore(pool)
    await store.decide_own_posts(
        community_id,
        organization_id=tenant["organization"],
        allowed=True,
        operator_id=tenant["operator"],
        now=now(),
    )

    after = await store.decide_own_posts(
        community_id,
        organization_id=tenant["organization"],
        allowed=None,
        operator_id=tenant["operator"],
        now=now(),
    )

    assert after is not None
    assert after.own_posts_allowed is None
    assert after.own_posts_decided_by is None and after.own_posts_decided_at is None
    verdict = await _verdict(pool, tenant)
    assert verdict.allowed is False and "nikt jeszcze nie zdecydował" in verdict.reason


async def test_a_decision_in_another_workspace_does_not_open_mine(pool, tenant) -> None:
    async with pool.acquire() as conn:
        other = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            tenant["organization"],
            f"sasiednia-{uuid4().hex[:8]}",
        )
    await _community(pool, tenant)
    other_community = await _community(pool, tenant, workspace=other)
    await ObservationStore(pool).decide_own_posts(
        other_community,
        organization_id=tenant["organization"],
        allowed=True,
        operator_id=tenant["operator"],
        now=now(),
    )

    mine = await _verdict(pool, tenant)
    theirs = await _verdict(pool, tenant, workspace=other)

    assert mine.allowed is False, mine.reason
    assert theirs.allowed is True, theirs.reason


async def test_the_voice_card_is_context_and_never_a_permission(pool, tenant) -> None:
    """Karta mówiąca `own_posts: true` niczego nie otwiera, a karta mówiąca `false` niczego nie
    zamyka ponad to, co już zamyka brak decyzji. Zgodę wydaje człowiek albo nikt."""
    permissive = await _community(pool, tenant, card={"own_posts": True})
    assert (await _verdict(pool, tenant)).allowed is False

    await ObservationStore(pool).decide_own_posts(
        permissive,
        organization_id=tenant["organization"],
        allowed=True,
        operator_id=tenant["operator"],
        now=now(),
    )
    assert (await _verdict(pool, tenant)).allowed is True
