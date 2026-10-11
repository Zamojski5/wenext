"""Tests of the harness itself: catalog coverage, determinism, prompt versioning, threshold.

These are NOT evals. This is the proof that the evals mean what they are meant to mean.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import dataset
from evals.harness.cases import (
    ARCHETYPE_GRAPH,
    ARCHETYPE_THREADED,
    CASE_COUNT_BY_FAMILY,
    DISCOVERY_CASES,
    DRAFT_CASES,
    QUALITY_CASES,
    SIGNAL_PAIR_CASES,
    TOTAL_CASES,
)
from evals.harness.report import build_summary, eval_criteria
from evals.harness.runner import run_case
from evals.harness.runtime import StubRuntime
from evals.harness.types import EvalFamily

ALL_CASES = (*QUALITY_CASES, *DISCOVERY_CASES, *SIGNAL_PAIR_CASES, *DRAFT_CASES)


# ---------------------------------------------------------------------------
# Catalog coverage
# ---------------------------------------------------------------------------


def test_case_count_is_within_the_L5168_range() -> None:
    """L5168: "30-50 fixture evals". The range is also frozen in criteria.yaml (D19)."""
    assert 30 <= TOTAL_CASES <= 50, f"katalog ma {TOTAL_CASES} przypadków"


def test_case_count_matches_the_frozen_D19_criteria() -> None:
    kryteria = eval_criteria()
    assert kryteria, "sekcja `evale` w tools/gonogo/criteria.yaml jest pusta"
    assert kryteria["minimalna_liczba_przypadkow"] <= TOTAL_CASES
    assert kryteria["maksymalna_liczba_przypadkow"] >= TOTAL_CASES


def test_zero_pass_threshold_in_F0_is_a_decision() -> None:
    """`prog_zdawalnosci: 0.0` is NOT an oversight.

    In F0 there is no ground truth (L5514). The evals prove that the harness works and that
    the quality definition is encoded — not that the model is good. The threshold for
    predictive quality belongs to F3 and F6. If someone raised this threshold in F0, this
    test is to light up first and force a deliberate decision, not let the change through
    quietly.
    """
    assert eval_criteria()["prog_zdawalnosci"] == 0.0


def test_all_three_L5168_families_are_non_empty() -> None:
    assert CASE_COUNT_BY_FAMILY[EvalFamily.DISCOVERY] > 0
    assert CASE_COUNT_BY_FAMILY[EvalFamily.QUALITY] > 0
    assert CASE_COUNT_BY_FAMILY[EvalFamily.DRAFT] > 0


@pytest.mark.parametrize("family", list(EvalFamily), ids=lambda f: f.value)
def test_every_family_covers_both_archetypes(family: EvalFamily) -> None:
    """A single-archetype fixture makes DoD F3 unverifiable (L4304, L5347)."""
    archetypes = {c.archetype for c in ALL_CASES if c.family is family}
    assert archetypes == {ARCHETYPE_THREADED, ARCHETYPE_GRAPH}, (
        f"rodzina {family.value} pokrywa tylko {archetypes}"
    )


def test_case_ids_are_unique() -> None:
    identyfikatory = [c.case_id for c in ALL_CASES]
    assert len(identyfikatory) == len(set(identyfikatory))


def test_every_case_points_at_an_existing_fixture_thread() -> None:
    known_keys = set(dataset.by_spec_key())
    for case in ALL_CASES:
        for attribute in ("spec_key", "base_spec_key"):
            value = getattr(case, attribute, None)
            if value is not None:
                assert value in known_keys, f"{case.case_id} → brak wątku {value}"
        for key in getattr(case, "spec_keys", ()):
            assert key in known_keys, f"{case.case_id} → brak wątku {key}"


def test_fixture_has_48_threads_across_two_archetypes() -> None:
    obserwacje = dataset.observations()
    assert len(obserwacje) == 48
    assert len(dataset.for_archetype(ARCHETYPE_THREADED)) == 24
    assert len(dataset.for_archetype(ARCHETYPE_GRAPH)) == 24


def test_every_fixture_negative_label_maps_to_a_SignalClass() -> None:
    """An unknown negative label must not vanish quietly — that would be a lost stop."""
    negative_labels = {e for o in dataset.observations() for e in o.negative_signal_labels}
    assert negative_labels <= set(dataset.FIXTURE_NEGATIVE_SIGNAL_TO_CLASS), (
        f"bez mapowania: {sorted(negative_labels - set(dataset.FIXTURE_NEGATIVE_SIGNAL_TO_CLASS))}"
    )


def test_fixture_contains_the_edge_cases_required_by_the_DoD() -> None:
    obserwacje = dataset.observations()
    assert any(
        o.negative_signal_classes and (o.votes > 500 or o.reaction_total > 500) for o in obserwacje
    ), "brak wątku z negatywem przy bardzo wysokim engagemencie"
    assert any(o.hidden_metrics for o in obserwacje), "brak wątku z ukrytą metryką"
    assert any(o.fake_engagement_flags for o in obserwacje), "brak wątku z flagą fake engagementu"
    assert any(o.window_state.value == "CLOSED" for o in obserwacje), "brak wątku CLOSED"
    assert any(o.amplification_count > 0 for o in obserwacje), "brak amplifikacji z intencją"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_stub_gives_the_same_result_regardless_of_run_count() -> None:
    """Requirement A: the same result for the stub regardless of order and number of runs."""
    runtime_a = StubRuntime()
    runtime_b = StubRuntime()

    first_run = {c.case_id: run_case(c, runtime_a).as_dict() for c in ALL_CASES}
    # A second pass on a FRESH instance and in reversed case order.
    drugi = {c.case_id: run_case(c, runtime_b).as_dict() for c in reversed(ALL_CASES)}
    # A third pass on the same instance as the first — no cache or state may have an effect.
    third_run = {c.case_id: run_case(c, runtime_a).as_dict() for c in ALL_CASES}

    assert json.dumps(first_run, sort_keys=True) == json.dumps(drugi, sort_keys=True)
    assert json.dumps(first_run, sort_keys=True) == json.dumps(third_run, sort_keys=True)


def test_assessing_a_single_thread_is_idempotent() -> None:
    from evals.harness.runner import prompt_for

    runtime = StubRuntime()
    prompt = prompt_for(EvalFamily.QUALITY)
    obs = dataset.observation("f-depth-deep")
    a = runtime.assess_thread(prompt, obs)
    b = runtime.assess_thread(prompt, obs)
    assert a == b
    assert a.surface_id == b.surface_id, "identyfikatory muszą być stabilne (uuid5, nie uuid4)"


def test_harness_compares_no_quality_scalar() -> None:
    """DoD D17: "the harness compares no scalar".

    `ThreadQualityAssessment` and the discovery result have no `score` field. `confidence`
    is the quality of the READ, not the quality of the thread, and does not enter the ordering.
    """
    from community_os_contracts import ThreadQualityAssessment

    from evals.harness.types import DiscoveryResult, RankedCandidate

    assert "score" not in ThreadQualityAssessment.model_fields
    assert "score" not in RankedCandidate.__dataclass_fields__
    assert "score" not in DiscoveryResult.__dataclass_fields__


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_report_is_serializable_and_carries_no_threshold() -> None:
    runtime = StubRuntime()
    records = [run_case(c, runtime) for c in ALL_CASES[:5]]
    summary = build_summary(records)
    text = json.dumps(summary, ensure_ascii=False)
    assert summary["wynik"]["prog_zdawalnosci_zastosowany"] is None
    assert summary["heuristic_policy_version"]
    assert summary["prompty"], "raport musi nieść wersje promptów (L5308)"
    assert len(text) > 0


def test_every_record_carries_prompt_version_model_and_effort() -> None:
    """L5308 applied to every eval, not only to the draft."""
    runtime = StubRuntime()
    for case in ALL_CASES:
        record = run_case(case, runtime)
        assert record.prompt_name
        assert record.prompt_version
        assert len(record.prompt_sha256) == 64
        assert record.runtime.model
        assert record.runtime.effort
        assert record.heuristic_policy_version
