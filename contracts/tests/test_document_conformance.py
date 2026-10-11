"""Tests binding the code to the source document.

These tests do not check logic — they check that we have not drifted away from the roadmap.
If someone changes the document, these are the ones to fail first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from community_os_contracts import (
    ADAPTER_CONTRACT_METHODS,
    E1_CAPABILITY_MAPPING,
    FORBIDDEN_PRACTICES,
    AccessMode,
    CapabilityState,
    CapsuleMode,
    CodexConnectionState,
    ExecutionAttemptState,
    ExecutionMode,
    HumanTakeoverState,
    InteractionLeaseMode,
    PresenceTrackState,
    PriorityTier,
    SocialAccountState,
    StreamSessionRole,
    ThreadRole,
    WindowState,
)

ROADMAP = Path(__file__).resolve().parents[2] / "ROADMAP_CODEX_CDP_COMMUNITY_OS.md"


@pytest.fixture(scope="module")
def document() -> str:
    assert ROADMAP.exists(), f"Nie znaleziono dokumentu źródłowego: {ROADMAP}"
    return ROADMAP.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "enum_class",
    [
        SocialAccountState,
        CapabilityState,
        ExecutionMode,
        ExecutionAttemptState,
        HumanTakeoverState,
        CodexConnectionState,
        AccessMode,
        WindowState,
        PriorityTier,
        PresenceTrackState,
        CapsuleMode,
        InteractionLeaseMode,
        StreamSessionRole,
    ],
)
def test_every_enum_value_appears_in_the_document(enum_class, document: str) -> None:
    """No enum value is invented — each one occurs verbatim in the roadmap."""
    missing = [member.value for member in enum_class if member.value not in document]
    assert not missing, (
        f"{enum_class.__name__}: wartości nieobecne w dokumencie: {missing}. "
        "Enum ma odwzorowywać dokument, nie go rozszerzać."
    )


def test_scope_boundary_quotes_the_document_verbatim(document: str) -> None:
    """The nine items from L241-251 must agree word for word."""
    assert len(FORBIDDEN_PRACTICES) == 9, "Sekcja 4 wymienia dokładnie dziewięć zakazanych praktyk"
    for spec in FORBIDDEN_PRACTICES:
        assert spec.quote_pl in document, (
            f"Cytat granicy zakresu nie występuje w dokumencie: {spec.quote_pl!r}"
        )


def test_scope_boundary_points_at_the_right_lines() -> None:
    """`source_line` must point at the line where the quote actually stands."""
    lines = ROADMAP.read_text(encoding="utf-8").splitlines()
    for spec in FORBIDDEN_PRACTICES:
        line = lines[spec.source_line - 1]
        assert spec.quote_pl in line, (
            f"L{spec.source_line} nie zawiera cytatu {spec.quote_pl!r}; jest tam: {line!r}"
        )


def test_adapter_contract_has_ten_methods(document: str) -> None:
    """The minimal adapter contract from L2551-2561."""
    assert len(ADAPTER_CONTRACT_METHODS) == 10
    for method in ADAPTER_CONTRACT_METHODS:
        assert f"{method}()" in document, f"Metoda {method}() nie występuje w dokumencie"


def test_no_writing_action_in_e1_is_automated() -> None:
    """Invariant 2 of section 22.2 (L4294): in E1 auto-publish does not exist."""
    writing = {"PUBLISH_REPLY", "REACT_TO_CONTENT", "JOIN_COMMUNITY", "PUBLISH_CONTENT"}
    for capability, execution_mode in E1_CAPABILITY_MAPPING.items():
        if capability.value in writing:
            assert execution_mode is not ExecutionMode.AUTOMATED, (
                f"{capability.value} ma tryb AUTOMATED — w E1 akcja zapisująca musi być "
                "AUTOMATED_AFTER_APPROVAL, MANUAL_ONLY albo UNSUPPORTED (L4294)."
            )


def test_four_thread_roles(document: str) -> None:
    """F0 requires four isolated thread roles (L5167, L2265)."""
    assert len(list(ThreadRole)) == 4
    assert "Planner, worker, verifier i post-verifier używają osobnych threadów" in document


def test_every_enforced_by_path_exists() -> None:
    """`enforced_by` is to point at CODE that really exists.

    Pointing at a non-existent directory is worse than an empty `enforced_by`: it looks
    like a safeguard while it is not one. The threat model review found this — three of the
    nine items pointed at directories that are not in the repo.
    """
    root = ROADMAP.parent
    braki = [
        f"{spec.practice.value} → {enforced_path}"
        for spec in FORBIDDEN_PRACTICES
        for enforced_path in spec.enforced_by
        if not (root / enforced_path).exists()
    ]
    assert not braki, "enforced_by wskazuje nieistniejące ścieżki: " + ", ".join(braki)


def test_unenforced_item_has_an_explicit_phase() -> None:
    """A lack of enforcement is admissible, but it must be explicit and have a deadline."""
    without_phase = [
        spec.practice.value
        for spec in FORBIDDEN_PRACTICES
        if not spec.enforced_by and not spec.planned_in
    ]
    assert not without_phase, (
        "pozycje granicy zakresu bez egzekwowania i bez wskazanej fazy: " + ", ".join(without_phase)
    )
