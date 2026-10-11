"""46 fixture evals on a deterministic stub model. No network, no ChatGPT account.

This file is the actual D17 deliverable in the "30-50 cases" part. The same
case catalog runs against the real Codex in `test_evals_live.py`.

Every test fails with a list of NAMED checks, and not with an "assert False" — with 46
cases the message is the only thing a human reads.
"""

from __future__ import annotations

import pytest

from evals.harness.cases import (
    DISCOVERY_CASES,
    DRAFT_CASES,
    QUALITY_CASES,
    SIGNAL_PAIR_CASES,
)
from evals.harness.report import Recorder
from evals.harness.runner import run_case
from evals.harness.runtime import StubRuntime
from evals.harness.types import EvalRecord


def _report_failures(record: EvalRecord) -> None:
    failed = [c for c in record.checks if not c.passed]
    if failed:
        description = "\n".join(f"  - {c.name}: {c.detail}" for c in failed)
        pytest.fail(
            f"{record.case_id} [{record.family.value}/{record.archetype}] "
            f"prompt={record.prompt_name}@{record.prompt_version}+{record.prompt_sha256[:12]} "
            f"runtime={record.runtime.kind}/{record.runtime.model}/{record.runtime.effort}\n"
            f"{len(failed)} z {len(record.checks)} kontroli niezdanych:\n{description}"
        )


@pytest.mark.quality
@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda c: c.case_id)
def test_discussion_quality_classification(
    case, stub_runtime: StubRuntime, recorder: Recorder
) -> None:
    _report_failures(recorder.add(run_case(case, stub_runtime)))


@pytest.mark.discovery
@pytest.mark.parametrize("case", DISCOVERY_CASES, ids=lambda c: c.case_id)
def test_discovery_candidate_order(case, stub_runtime: StubRuntime, recorder: Recorder) -> None:
    _report_failures(recorder.add(run_case(case, stub_runtime)))


@pytest.mark.discovery
@pytest.mark.parametrize("case", SIGNAL_PAIR_CASES, ids=lambda c: c.case_id)
def test_signal_class_order_on_pairs(case, stub_runtime: StubRuntime, recorder: Recorder) -> None:
    """Pairs differing ONLY in signal class (L4471-4479, DoD D17)."""
    _report_failures(recorder.add(run_case(case, stub_runtime)))


@pytest.mark.draft
@pytest.mark.parametrize("case", DRAFT_CASES, ids=lambda c: c.case_id)
def test_reply_draft(case, stub_runtime: StubRuntime, recorder: Recorder) -> None:
    _report_failures(recorder.add(run_case(case, stub_runtime)))
