"""Agreement of the fixture's literals with the enums from `contracts/` (D21).

The fixture deliberately does NOT depend on the `community-os-contracts` package — the
dependency would go the wrong way and would drag pydantic into an image that is meant to be
small. Instead this test loads the enums file by path and compares the values. Name drift
will surface here, and not in D17.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from wenext_fixture_site import models
from wenext_fixture_site.generator import build_repository

ENUMS_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "src"
    / "community_os_contracts"
    / "enums.py"
)


def _load_enums() -> ModuleType:
    if not ENUMS_PATH.exists():
        pytest.skip(f"brak {ENUMS_PATH} — kontrakty D21 jeszcze nie istnieją")
    spec = importlib.util.spec_from_file_location("_contracts_enums", ENUMS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archetypes_have_values_from_contracts() -> None:
    enums = _load_enums()
    assert enums.ChannelArchetype.THREADED_COMMUNITY.value == models.ARCHETYPE_THREADED
    assert enums.ChannelArchetype.PROFESSIONAL_GRAPH.value == models.ARCHETYPE_GRAPH


def test_access_mode_has_values_from_contracts() -> None:
    enums = _load_enums()
    assert enums.AccessMode.ACCOUNT_REQUIRED.value == models.ACCESS_ACCOUNT_REQUIRED
    assert enums.AccessMode.PUBLIC_API_NO_ACCOUNT.value == models.ACCESS_PUBLIC_API


def test_thread_window_state_comes_from_contract_enum() -> None:
    enums = _load_enums()
    allowed = {outcome.value for outcome in enums.WindowState}
    used = {t.window_state for t in build_repository(20260830).threads}
    assert used <= allowed
    assert used == allowed, "fixture ma pokrywać wszystkie cztery stany okna życia"


def test_negative_signals_include_hard_stop_classes() -> None:
    """`LOCK` and `NEGATIVE_MODERATION` from `NEGATIVE_STOP_CLASSES` must be in the fixture."""

    enums = _load_enums()
    stop = {c.value for c in enums.SignalClass} & {"LOCK", "NEGATIVE_MODERATION", "BAN"}
    used = {s for t in build_repository(20260830).threads for s in t.negative_signals}
    assert {"LOCK", "NEGATIVE_MODERATION"} <= stop
    assert {"LOCK", "NEGATIVE_MODERATION"} <= used
