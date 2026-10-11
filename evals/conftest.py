"""Pytest configuration for the evals.

The default runtime is `StubRuntime`: no network, no ChatGPT account, deterministic.
`pytest evals -q -m "not live"` must pass on a clean machine without compose.

Records collected during the session land in a single JSON for `tools/gonogo` (D19).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `evals` is a package at the repo root, so `evals.*` imports need the root
# on `sys.path`. Pytest in `prepend` mode usually does this itself; the insert below
# makes it work also with `--import-mode=importlib` and when launched
# from a different working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.harness.report import Recorder  # noqa: E402
from evals.harness.runtime import StubRuntime  # noqa: E402


@pytest.fixture(scope="session")
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture(scope="session")
def stub_runtime() -> StubRuntime:
    return StubRuntime()


@pytest.fixture(scope="session")
def codex_runtime():
    """The real runtime. Requires D12 and a logged-in ChatGPT account.

    A skip is NOT a positive result: GATE-3 without `evals_live` yields `NO_DATA`.
    """
    import os

    from evals.harness.codex_runtime import CodexRuntime, CodexRuntimeUnavailable

    model = os.environ.get("WENEXT_EVALS_MODEL")
    effort = os.environ.get("WENEXT_EVALS_EFFORT")
    if not model or not effort:
        pytest.skip(
            "Brak WENEXT_EVALS_MODEL / WENEXT_EVALS_EFFORT. Model i effort muszą być "
            "podane jawnie — harness nie zgaduje ID modelu (D12: bez hardcode'u)."
        )
    try:
        return CodexRuntime(model=model, effort=effort)
    except CodexRuntimeUnavailable as exc:
        pytest.skip(str(exc))


def pytest_sessionfinish(session, exitstatus) -> None:
    """Report written at the end of the session — one place, one file."""
    rec: Recorder | None = getattr(session, "_wenext_recorder", None)
    if rec is None or not rec.records:
        return
    kind = rec.records[0].runtime.kind
    path = rec.write(kind)
    if path is not None:
        print(f"\n[evals] raport: {path}")


@pytest.fixture(scope="session", autouse=True)
def _attach_recorder(request, recorder: Recorder):
    """Exposes the session recorder to the `pytest_sessionfinish` hook."""
    request.session._wenext_recorder = recorder
    return recorder
