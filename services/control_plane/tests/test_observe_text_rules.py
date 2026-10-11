"""The founder's rule about long dashes, measured on the module that enforces it (2026-09-04).

`text_rules` is the second line of defence after the prompt: whatever the model produced, no
U+2014 and no U+2013 reaches the database. These tests pin the rewrite rules listed in the module
docstring, one case each, and the count the handlers log.
"""

from __future__ import annotations

import pytest
from control_plane.observe.text_rules import (
    EM_DASH,
    EN_DASH,
    assert_no_long_dashes,
    clean_payload,
    clean_text,
    count_long_dashes,
    has_long_dash,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Autor pyta o kolejki — dokładnie to, co robił.",
            "Autor pyta o kolejki, dokładnie to, co robił.",
        ),
        ("to — jak zwykle — działa", "to, jak zwykle, działa"),
        ("bez spacji—też", "bez spacji, też"),
        ("krótki myślnik \u2013 też pauza", "krótki myślnik, też pauza"),
        ("2\u20135 zdań", "2-5 zdań"),
        ("lata 2014—2020", "lata 2014-2020"),
        ("koniec zdania —", "koniec zdania."),
        ("— zaczyna od pauzy", "zaczyna od pauzy"),
        ("pauza przed kropką —.", "pauza przed kropką."),
        ("pauza przed nawiasem —)", "pauza przed nawiasem)"),
        ("Ask HN: Kubernetes — czy warto?", "Ask HN: Kubernetes, czy warto?"),
    ],
)
def test_every_long_dash_is_rewritten_by_a_listed_rule(text: str, expected: str) -> None:
    cleaned = clean_text(text)
    assert cleaned == expected
    assert not has_long_dash(cleaned)


def test_a_text_without_a_long_dash_comes_back_as_the_same_object() -> None:
    text = "Zwykły myślnik-łącznik i minus - zostają."
    assert clean_text(text) is text
    assert count_long_dashes(text) == 0


def test_clean_payload_walks_nested_structures_and_counts_but_leaves_keys_alone() -> None:
    payload = {
        "summary": f"Spokojny dzień {EM_DASH} dwa pytania.",
        "topics": [
            {"name": f"MCP {EN_DASH} narzędzia", "thread_refs": ["HACKER_NEWS:ask:1"], "n": 3},
            {"name": "bez pauzy", "thread_refs": ["HACKER_NEWS:ask:2"]},
        ],
        f"klucz{EM_DASH}z pauzą": ("krotka", f"z {EN_DASH} pauzą"),
        "confidence": 0.9,
        "nothing": None,
    }

    cleaned, found = clean_payload(payload)

    assert found == 3, "jedna w summary, jedna w topics, jedna w krotce; klucz się nie liczy"
    assert cleaned["summary"] == "Spokojny dzień, dwa pytania."
    assert cleaned["topics"][0]["name"] == "MCP, narzędzia"
    assert cleaned["topics"][0]["n"] == 3 and cleaned["confidence"] == 0.9
    assert cleaned["nothing"] is None
    assert f"klucz{EM_DASH}z pauzą" in cleaned, "klucze są schematem, nie prozą — zostają"
    assert cleaned[f"klucz{EM_DASH}z pauzą"] == ("krotka", "z, pauzą")
    assert payload["summary"].count(EM_DASH) == 1, "wejście nie jest modyfikowane"


def test_the_store_side_assertion_names_the_count_and_the_rule() -> None:
    assert_no_long_dashes({"ok": ["czysto", {"x": "też"}]}, where="test")
    with pytest.raises(ValueError, match=r"test: 2 długich pauz.*clean_payload"):
        assert_no_long_dashes({"a": f"x {EM_DASH} y", "b": [f"p {EN_DASH} q"]}, where="test")
