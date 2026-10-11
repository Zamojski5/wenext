"""The same case catalog against the REAL Codex.

Run: `pytest evals -m live` or `make evals-live`. Requires:
- the D12 package (`services/codex_runtime/`),
- a logged-in ChatGPT account,
- explicit `WENEXT_EVALS_MODEL` and `WENEXT_EVALS_EFFORT` (the harness does not guess
  the model ID).

Missing any of these conditions gives a `skip` with a recorded reason. **A skip is not
a positive result**: GATE-3 without `evals_live` is `NO_DATA`, never a default GO
(criteria.yaml).

The assertions are EXACTLY the same as on the stub. If something fails here and not on the
stub — the difference is on the model's side, not the harness's, and that is the whole
information GATE-3 needs.
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

pytestmark = pytest.mark.live

ALL_CASES = (*QUALITY_CASES, *DISCOVERY_CASES, *SIGNAL_PAIR_CASES, *DRAFT_CASES)


def _report_failures(record) -> None:
    failed = [c for c in record.checks if not c.passed]
    if failed:
        description = "\n".join(f"  - {c.name}: {c.detail}" for c in failed)
        pytest.fail(
            f"{record.case_id} [{record.family.value}/{record.archetype}] "
            f"prompt={record.prompt_name}@{record.prompt_version}+{record.prompt_sha256[:12]} "
            f"runtime={record.runtime.kind}/{record.runtime.model}/{record.runtime.effort}\n"
            f"{len(failed)} z {len(record.checks)} kontroli niezdanych:\n{description}"
        )


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.case_id)
def test_eval_on_codex(case, codex_runtime, recorder: Recorder) -> None:
    _report_failures(recorder.add(run_case(case, codex_runtime)))
