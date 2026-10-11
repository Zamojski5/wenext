"""Coverage of the eight 22.7 dimensions and of the edge cases required by DoD D16.

This test guards the input to the D17 evals. If someone removes a thread from the fixture,
the F0 evals will have nothing left to run on — and they will find out about it here, and
not in F3.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wenext_fixture_site.generator import build_repository
from wenext_fixture_site.seed_data import ALL_DIMENSIONS, coverage, validate


def test_seed_catalog_is_consistent() -> None:
    validate()


def test_24_threads_per_archetype() -> None:
    repo = build_repository(20260830)
    assert len(repo.for_archetype("THREADED_COMMUNITY")) == 24
    assert len(repo.for_archetype("PROFESSIONAL_GRAPH")) == 24


@pytest.mark.parametrize("dimension", ALL_DIMENSIONS)
def test_every_dimension_has_at_least_three_threads_per_archetype(dimension: str) -> None:
    per_archetype = coverage()[dimension]
    for archetype, keys in per_archetype.items():
        assert len(keys) >= 3, f"{dimension} w {archetype}: {keys}"


def test_negative_signal_stands_at_very_high_engagement() -> None:
    """DoD: a thread must exist with huge positives where the negative signal stands anyway."""

    repo = build_repository(20260830)
    for key in ("f-neg-locked-high-engagement", "g-neg-locked-high-engagement"):
        thread = repo.by_id(key)
        assert thread is not None
        assert thread.negative_signals
        assert thread.locked
        engagement = sum(r.count for r in thread.reactions) + max(thread.votes, 0)
        assert engagement > 500, "engagement za niski, żeby przypadek był ostry"


def test_hidden_reach_metric_exists_in_both_archetypes() -> None:
    repo = build_repository(20260830)
    hidden_thread_ids = [
        t.id for t in repo.threads if "reach_impressions" in t.metrics.hidden_metrics
    ]
    assert any(t.startswith("f-") for t in hidden_thread_ids)
    assert any(t.startswith("g-") for t in hidden_thread_ids)
    for tid in hidden_thread_ids:
        assert build_repository(20260830).by_id(tid).metrics.reach_impressions is None


def test_reaction_burst_without_reply_growth() -> None:
    repo = build_repository(20260830)
    burst = [t for t in repo.threads if t.metrics.reaction_burst_without_replies]
    assert {t.archetype for t in burst} == {"THREADED_COMMUNITY", "PROFESSIONAL_GRAPH"}
    for thread in burst:
        assert thread.fake_engagement_flags, "burst bez flagi jest niewykrywalny w evalu"
        assert thread.metrics.reply_count <= 5
        assert not thread.negative_signals, (
            "flaga fake engagementu nie jest sygnałem negatywnym — w E1 jest log-only "
            "i nie może odrzucić wątku (L4492, L5551)"
        )


def test_closed_window_state_occurs_in_both_archetypes() -> None:
    repo = build_repository(20260830)
    closed = {t.archetype for t in repo.threads if t.window_state == "CLOSED"}
    assert closed == {"THREADED_COMMUNITY", "PROFESSIONAL_GRAPH"}
    # A CLOSED without a negative must exist too — otherwise they cannot be told apart.
    clean_closed = [
        t for t in repo.threads if t.window_state == "CLOSED" and not t.negative_signals
    ]
    assert clean_closed


def test_intentional_amplification_has_kind_and_comment() -> None:
    repo = build_repository(20260830)
    with_amplification = [t for t in repo.threads if t.amplifications]
    assert len(with_amplification) >= 6
    kinds = {a.kind for t in with_amplification for a in t.amplifications}
    assert {"QUOTE", "CROSSPOST", "NEWSLETTER_LINK", "REPOST_WITH_COMMENT"} <= kinds
    for thread in with_amplification:
        for amp in thread.amplifications:
            assert amp.comment, "amplifikacja bez własnego komentarza to zwykły repost"


def test_forum_nesting_reaches_at_least_three_levels() -> None:
    repo = build_repository(20260830)
    levels = [t.metrics.depth_levels for t in repo.for_archetype("THREADED_COMMUNITY")]
    assert max(levels) >= 5
    assert sum(1 for p in levels if p >= 3) >= 12
    # The professional archetype has flat comments — that is a difference, not a defect.
    assert max(t.metrics.depth_levels for t in repo.for_archetype("PROFESSIONAL_GRAPH")) <= 2


def test_author_participation_is_distinguishable() -> None:
    repo = build_repository(20260830)
    without_author = [t for t in repo.threads if t.metrics.author_reply_count == 0]
    with_author = [t for t in repo.threads if t.metrics.author_reply_count >= 3]
    assert len(without_author) >= 4
    assert len(with_author) >= 20
    first_reply_times = {
        t.metrics.author_first_reply_minutes
        for t in with_author
        if t.metrics.author_first_reply_minutes is not None
    }
    assert min(first_reply_times) < 15 and max(first_reply_times) > 600, (
        "czas reakcji autora bez rozrzutu"
    )


def test_generator_delivers_declared_author_reply_count() -> None:
    """The metric is to be a result, not a copy of the seed — but it has to agree with it."""

    from wenext_fixture_site.seed_data import ALL_SPECS

    repo = build_repository(20260830)
    mismatches = [
        (spec.key, spec.author_replies, repo.by_id(spec.key).metrics.author_reply_count)
        for _, spec in ALL_SPECS
        if repo.by_id(spec.key).metrics.author_reply_count != spec.author_replies
    ]
    assert mismatches == []


def test_substantive_ratio_has_spread() -> None:
    repo = build_repository(20260830)
    ratios = sorted(t.metrics.substantive_reply_ratio for t in repo.threads)
    assert ratios[0] <= 0.05
    assert ratios[-1] >= 0.7


def test_dimensions_endpoint_publishes_coverage(client: TestClient) -> None:
    payload = client.get("/api/public/quality-dimensions").json()
    assert payload["dimensions"] == list(ALL_DIMENSIONS)
    assert payload["coverage"] == coverage()


def test_every_thread_declares_why_it_exists(client: TestClient) -> None:
    for thread in client.get("/debug/seed").json()["threads"]:
        assert thread["illustrates"], thread["id"]
        assert thread["note"], thread["id"]
