"""D-031, the learning loop's reading side: what a published comment earned, per move and against
its thread, as rows the generator and the morning table consume. Scripted source, fake port, no
model: the numbers come from `publication_observations` and `observed_comments`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from control_plane.pilot.cli import render_outcomes
from control_plane.pilot.publication import PublicationStore
from control_plane.pilot.publisher import OUTCOME_PUBLISHED
from control_plane.pilot.store import PilotStore
from test_pilot_publisher import approved, thread_json, world

pytestmark = pytest.mark.db


async def test_move_outcomes_and_outcome_rows_read_points_against_the_thread(pool, tenant) -> None:
    (reaction,) = await approved(pool, tenant, ("1out001", "One number: 40 percent."))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pilot_reactions SET move = 'NUMBER', requested_move = 'IRONY' WHERE id = $1",
            reaction.id,
        )
    w = await world(pool, tenant)
    # The thread has three other top-level comments with 1, 3 and 5 points (median 3).
    w.source.threads["1out001"] = thread_json(
        "1out001",
        comments=[
            {"id": "k1", "score": 1},
            {"id": "k2", "score": 3},
            {"id": "k3", "score": 5},
        ],
    )
    now = datetime.now(UTC)
    result = await w.handlers.run_once(now)
    assert result.outcome == OUTCOME_PUBLISHED
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None
    # The observation the tick records for a fresh publication; here a later one with points.
    run = await PilotStore(pool).get_run(reaction.run_id)
    assert run is not None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO publication_observations
                (organization_id, workspace_id, request_id, observed_at, still_present, score,
                 reply_count, raw)
            VALUES ($1, $2, $3, $4, true, 7, 2, '{}'::jsonb)
            """,
            tenant["organization"],
            tenant["workspace"],
            request.id,
            now + timedelta(hours=1),
        )

    outcomes = await PilotStore(pool).move_outcomes(
        run.community_id, organization_id=tenant["organization"]
    )
    assert outcomes == (
        {
            "move": "NUMBER",
            "published": 1,
            "points_vs_thread_median": 4.0,
            "replied_share": 1.0,
        },
    ), "7 punktów przy medianie wątku 3, jedna odpowiedź"
    rows = await PilotStore(pool).outcome_rows(organization_id=tenant["organization"])
    assert len(rows) == 1
    row = rows[0]
    assert row["move"] == "NUMBER" and row["requested_move"] == "IRONY"
    assert row["score"] == 7 and row["reply_count"] == 2
    assert float(row["thread_median"]) == 3.0 and row["thread_best"] == 5
    assert row["thread_top_level"] == 3 and row["published_at"] is not None
    assert row["post_created_at"] is not None and row["consent_source"] == "test consent"
    assert row["verdict"] is None, "zgoda ręczna, bez werdyktu sędziego"

    text = render_outcomes(rows, now=now)
    assert "| NUMBER |" in text or "| IRONY | NUMBER |" in text
    assert "r/kubernetes · NUMBER" in text and "r/kubernetes · proszono IRONY" in text
    assert "6 h i więcej" in text, "post z 2 h przed NOW testów, publikacja teraz: wiek ponad 6 h"
    assert "| 7 | 4.0 | 2 |" in text, "punkty, delta wobec mediany wątku, odpowiedzi w wierszu"
    assert PublicationStore is not None
