"""Prompt versioning (requirement B).

Every prompt is a file with a hash of its content. An eval records WHICH PROMPT VERSION and
WHICH model/effort it was executed with (L5308). A change of a prompt is to be visible —
the frozen manifest `prompts/PROMPTS.lock` guards that.
"""

from __future__ import annotations

import hashlib

import pytest

from evals.harness import prompts
from evals.harness.runner import PROMPT_FOR_FAMILY, prompt_for
from evals.harness.types import EvalFamily


def test_every_family_has_its_own_prompt() -> None:
    for family in EvalFamily:
        prompt = prompt_for(family)
        assert prompt.name == PROMPT_FOR_FAMILY[family]


def test_hash_is_the_hash_of_the_whole_file() -> None:
    for prompt in prompts.load_all().values():
        expected = hashlib.sha256(prompt.path.read_bytes()).hexdigest()
        assert prompt.sha256 == expected


def test_manifest_matches_the_files() -> None:
    """Changing a prompt without updating the manifest stops the evals.

    This is not a formality: if a prompt could be swapped without a trace, the eval result
    would stop being tied to a specific version of the instructions — and that is exactly
    the information L5308 requires with every draft.
    """
    frozen_hashes = prompts.read_lock()
    current_hashes = {p.name: p.sha256 for p in prompts.load_all().values()}
    assert current_hashes == frozen_hashes, (
        "Hasze promptów rozjechały się z PROMPTS.lock.\n"
        "Jeżeli zmiana promptu jest zamierzona: `python -m evals.harness.prompts` "
        "i zacommituj diff manifestu razem z promptem."
    )


def test_every_prompt_declares_a_name_and_version() -> None:
    for prompt in prompts.load_all().values():
        assert prompt.name
        assert prompt.version
        assert prompt.short.startswith(f"{prompt.name}@{prompt.version}+")


def test_prompt_without_a_header_is_an_error(tmp_path) -> None:
    path = tmp_path / "zly.md"
    path.write_text("Sam tekst bez nagłówka.", encoding="utf-8")
    with pytest.raises(prompts.PromptError):
        prompts._parse(path)


def test_changing_the_content_changes_the_version(tmp_path) -> None:
    """Direct proof: adding one character gives a different sha256, so a different record."""
    baza = "---\nname: probny\nversion: v1\n---\n\nTreść."
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(baza, encoding="utf-8")
    b.write_text(baza + " ", encoding="utf-8")
    assert prompts._parse(a).sha256 != prompts._parse(b).sha256


def test_prompts_encode_the_non_negotiable_rules() -> None:
    """A prompt is also a place where the definition from the document is to be written out."""
    quality = prompts.get("thread_quality").text
    assert "REJECT" in quality
    assert "UNKNOWN" in quality
    assert "log-only" in quality
    assert "score" in quality  # the ban on summing into one score

    discovery = prompts.get("discovery").text
    assert "INTENTIONAL_AMPLIFICATION" in discovery
    assert "SUBSTANTIVE_REPLY" in discovery
    assert "LIKE / UPVOTE" in discovery

    draft = prompts.get("draft_reply").text
    assert "UNVERIFIED" in draft
    assert "prohibited_topics" in draft
    assert "CONVERSATION_FOLLOW_UP" in draft  # explicitly excluded from the E1 scope
