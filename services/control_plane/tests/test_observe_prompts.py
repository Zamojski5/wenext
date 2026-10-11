"""The observe prompts are versioned by content hash, and the manifest is frozen."""

from __future__ import annotations

import hashlib

from community_os_contracts import ThreadRole
from control_plane.observe import prompts


def test_the_manifest_matches_the_files_on_disk() -> None:
    """Editing a prompt without `python -m control_plane.observe.prompts` stops here."""
    lock = prompts.read_lock()
    loaded = prompts.load_all()
    assert set(lock) == set(prompts.PROMPT_NAMES) == set(loaded), (lock, prompts.PROMPT_NAMES)
    for name, digest in lock.items():
        assert loaded[name].sha256 == digest, (
            f"prompt {name} zmienił się bez odświeżenia PROMPTS.lock — zmiana promptu ma być "
            "widoczna w diffie, nie cicha"
        )


def test_the_digest_is_over_the_whole_file_including_the_header() -> None:
    for prompt in prompts.load_all().values():
        assert prompt.sha256 == hashlib.sha256(prompt.path.read_bytes()).hexdigest()
        assert len(prompt.sha256) == 64


def test_roles_are_what_the_handlers_expect() -> None:
    """Judging is WORKER work (high volume, cheap); the daily picture is PLANNER work."""
    assert prompts.get("judge_thread").role is ThreadRole.WORKER
    assert prompts.get("community_picture").role is ThreadRole.PLANNER


def test_the_judge_prompt_names_the_three_decisions_and_the_anti_slop_rule() -> None:
    text = prompts.get("judge_thread").text
    for token in ("ENGAGE", "IGNORE", "WAIT", "praise", "summary", "Polish"):
        assert token in text, f"prompt judge_thread nie wspomina o {token!r}"
    assert "Do not invent" in text, "prompt musi zakazywać wymyślania doświadczeń użytkownika (§5)"


def test_every_prompt_carries_the_founders_no_long_dash_rule() -> None:
    """Founder rule 2026-09-04: model text never contains U+2014 or U+2013. The prompt is the
    first line of defence (`text_rules.clean_payload` is the second); a prompt without the rule
    is a v1 that came back. Every prompt, present and future, is checked, not the two by name."""
    loaded = prompts.load_all()
    assert loaded, "brak promptów — test nic nie mierzy"
    for prompt in loaded.values():
        assert (
            "No em dashes (U+2014) and no en dashes (U+2013) anywhere in the output" in prompt.text
        ), f"prompt {prompt.short} nie ma reguły wyjścia o długich pauzach"
    # The two prompts that existed before the rule were re-issued as v2; a prompt born after it
    # starts at v1 with the rule already in. The wording above is the proof, not the version.
    assert prompts.get("community_picture").version == "v2"
    assert prompts.get("judge_thread").version == "v2"


def test_the_picture_prompt_requires_evidence_refs() -> None:
    text = prompts.get("community_picture").text
    assert "thread_refs" in text
    assert "No refs, no claim" in text
    assert "durable_culture" in text and "current_state" in text, (
        "§6: kultura trwała vs stan bieżący"
    )
