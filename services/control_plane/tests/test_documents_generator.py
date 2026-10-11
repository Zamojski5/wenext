"""Dokumenty projektu jako widok na rejestr, mierzone bez bazy (P8 etap A).

CZEGO PILNUJĄ TE TESTY. Dokument ma być czymś, co da się SPRAWDZIĆ co do zdania, więc każdy
przypadek to jeden sposób, w jaki mógłby przestać nim być:

  * zdanie bez podstawy, czyli twierdzenie produktu, którego nie ma w rejestrze;
  * hipoteza wywnioskowana przez maszynę, czyli hipoteza, której nikt nie postawił;
  * „co działa" zbudowane z opinii zamiast ze zmierzonych wyników;
  * ta sama treść w dwóch dokumentach, czytana potem jak dwa niezależne ustalenia;
  * odcisk wejścia liczony z tekstu, przez co zmiana szablonu wyglądałaby jak zmiana wiedzy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_plane.documents import (
    ROLE_COMMUNITIES,
    ROLE_DECISIONS,
    ROLE_PROJECT_CARD,
    ROLE_WHAT_WORKS,
    Sentence,
    build_documents,
    input_digest,
)

NOW = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
PROJECT = uuid4()


@dataclass
class Item:
    """Tyle `knowledge/store.ItemRow`, ile czyta generator."""

    kind: str
    statement: str
    stated_by: str = "AGENT"
    payload: dict[str, Any] = field(default_factory=dict)
    project_id: UUID | None = PROJECT
    id: UUID = field(default_factory=uuid4)
    valid_from: datetime = NOW
    superseded_by: UUID | None = None
    retracted_at: datetime | None = None


def _documents(items: list[Item]):
    return {
        one.role: one
        for one in build_documents(
            items,
            project_name="Obecność devops",
            project_goal="zbuduj obecność",
            project_mode="AUTONOMOUS",
        )
    }


def test_every_sentence_points_at_findings_it_came_from() -> None:
    docs = _documents(
        [
            Item("PREFERENCE", "piszemy krótko", stated_by="USER"),
            Item("INTERPRETATION", "wieczorem odzew jest mniejszy"),
        ]
    )
    for document in docs.values():
        for sentence in document.sentences:
            assert sentence.basis, (document.role, sentence.text)


def test_a_sentence_without_a_basis_cannot_be_built_at_all() -> None:
    """Więz jest i w bazie (0028), i tutaj: obiekt bez podstawy nie powstaje, więc nie ma jak
    dojechać do zapisu i zostać odrzuconym dopiero tam."""
    with pytest.raises(ValueError):
        Sentence(section="Hipotezy", text="coś", basis=())


def test_a_hypothesis_is_what_a_person_marked_as_one() -> None:
    marked = Item(
        "INTERPRETATION",
        "wierzymy, że ludzie wolą konkret",
        stated_by="USER",
        payload={"hypothesis": True},
    )
    looks_like_one = Item("INTERPRETATION", "wydaje się, że ludzie wolą konkret")

    card = _documents([marked, looks_like_one])[ROLE_PROJECT_CARD]

    sections = {one.section for one in card.sentences}
    assert "Hipotezy" in sections
    hypotheses = [one for one in card.sentences if one.section == "Hipotezy"]
    assert len(hypotheses) == 1
    assert "konkret" in hypotheses[0].text and "postawił człowiek" in hypotheses[0].text


def test_a_project_with_no_hypothesis_says_so_instead_of_hiding_the_section() -> None:
    """Projekt bez postawionej hipotezy to stan, który człowiek ma zobaczyć."""
    card = _documents([Item("FACT", "społeczność ma 40 tysięcy osób")])[ROLE_PROJECT_CARD]
    assert [one.section for one in card.sentences] == ["Co wiemy o projekcie"]
    # Dokument istnieje i jest pusty w części hipotez; pustka jest odpowiedzią, nie brakiem.
    assert card.item_count == 1


def test_what_works_is_built_only_from_measured_outcomes() -> None:
    replied = Item(
        "EXPERIENCE",
        "odpowiedź w r/devops dostała odpowiedź autora",
        stated_by="SOURCE",
        payload={"outcome": {"kind": "REPLIED"}, "situation": {"community": "r/devops"}},
    )
    removed = Item(
        "EXPERIENCE",
        "komentarz w r/kubernetes zniknął z platformy",
        stated_by="SOURCE",
        payload={"outcome": {"kind": "REMOVED"}, "situation": {"community": "r/kubernetes"}},
    )
    opinion = Item("INTERPRETATION", "chyba działa pisanie rano")

    works = _documents([replied, removed, opinion])[ROLE_WHAT_WORKS]

    sections = [one.section for one in works.sentences]
    assert sections == ["Zadziałało", "Nie zadziałało"]
    assert all("chyba działa" not in one.text for one in works.sentences)


def test_a_hypothesis_does_not_appear_twice_in_two_documents() -> None:
    """To samo zdanie w dwóch dokumentach czyta się jak dwa ustalenia, a jest jednym."""
    hypothesis = Item(
        "PREFERENCE", "testujemy krótkie odpowiedzi", stated_by="USER", payload={"hypothesis": True}
    )
    docs = _documents([hypothesis])

    in_card = [one.text for one in docs[ROLE_PROJECT_CARD].sentences]
    in_decisions = [one.text for one in docs[ROLE_DECISIONS].sentences]
    assert any("krótkie odpowiedzi" in one for one in in_card)
    assert in_decisions == []


def test_retired_findings_do_not_reach_any_document() -> None:
    corrected = Item("INTERPRETATION", "stara wersja", superseded_by=uuid4())
    withdrawn = Item("INTERPRETATION", "wycofane", retracted_at=NOW)
    docs = _documents([corrected, withdrawn])
    for document in docs.values():
        assert document.sentences == ()
        assert document.item_count == 0


def test_communities_are_grouped_and_nothing_lands_in_a_bag_called_other() -> None:
    here = Item(
        "EXPERIENCE",
        "w r/devops czytają wieczorem",
        stated_by="SOURCE",
        payload={"outcome": {"kind": "SCORED"}, "situation": {"community": "r/devops"}},
    )
    nowhere = Item("INTERPRETATION", "ustalenie bez społeczności")

    document = _documents([here, nowhere])[ROLE_COMMUNITIES]

    assert [one.section for one in document.sentences] == ["r/devops"]
    assert document.item_count == 1


def test_the_digest_follows_the_register_not_the_wording() -> None:
    """Odcisk liczony z tekstu kazałby przepisać wszystkie dokumenty po zmianie szablonu."""
    first = Item("INTERPRETATION", "jedno")
    second = Item("INTERPRETATION", "drugie", valid_from=NOW + timedelta(hours=1))

    assert input_digest([first, second]) == input_digest([second, first])
    assert input_digest([first]) != input_digest([first, second])

    moved = Item("INTERPRETATION", "jedno", id=first.id, valid_from=NOW + timedelta(days=1))
    assert input_digest([first]) != input_digest([moved]), "korekta zmienia odcisk"


def test_two_runs_over_an_unchanged_register_give_the_same_digest() -> None:
    items = [Item("PREFERENCE", "piszemy krótko", stated_by="USER"), Item("FACT", "40 tysięcy")]
    first = {
        one.role: one.input_sha256
        for one in build_documents(
            items, project_name="P", project_goal="c", project_mode="AUTONOMOUS"
        )
    }
    second = {
        one.role: one.input_sha256
        for one in build_documents(
            items, project_name="P", project_goal="c", project_mode="AUTONOMOUS"
        )
    }
    assert first == second


def test_a_finding_shaped_wrong_does_not_take_the_whole_generator_down() -> None:
    """Zmierzone przez wenext-ce na zasiewie e2e (20.09): `payload.situation` bywa NAPISEM.

    Więz z 0016 sprawdza obecność kluczy, nie ich kształt, więc baza przyjmuje takie ustalenie,
    a model nie ma skąd wiedzieć, że ma tam być obiekt. Generator musi to przeżyć, bo od etapu B
    dokumenty przepisuje job w tle: job padający na jednym ustaleniu przestaje przepisywać
    WSZYSTKIE dokumenty projektu, a widać to dopiero po tym, że przestały się zmieniać.
    """
    crooked = Item(
        "EXPERIENCE",
        "rozmawialiśmy o cenach i poszło dobrze",
        stated_by="SOURCE",
        payload={"situation": "rozmawialiśmy o cenach", "decision": "x", "outcome": "dobrze"},
    )
    sound = Item(
        "EXPERIENCE",
        "odpowiedź w r/devops dostała odpowiedź autora",
        stated_by="SOURCE",
        payload={"outcome": {"kind": "REPLIED"}, "situation": {"community": "r/devops"}},
    )

    docs = _documents([crooked, sound])

    # Generator nie przewraca się i nadal buduje komplet dokumentów.
    assert set(docs) == {ROLE_PROJECT_CARD, ROLE_WHAT_WORKS, ROLE_DECISIONS, ROLE_COMMUNITIES}
    # Ustalenie w złym kształcie nie wchodzi tam, gdzie potrzebne było pole, którego nie ma.
    works = [one.text for one in docs[ROLE_WHAT_WORKS].sentences]
    assert any("r/devops" in one for one in works)
    assert all("o cenach" not in one for one in works)
    communities = [one.section for one in docs[ROLE_COMMUNITIES].sentences]
    assert communities == ["r/devops"]


def test_a_new_interpretation_moves_decisions_and_leaves_the_project_card_alone() -> None:
    """KTÓRY DOKUMENT ZMIENIA SIĘ OD KTÓREGO USTALENIA, przypięte tu, a nie domyślane w teście z bazą.

    Powód jest zmierzony: test rutyny domykania pytał o wersję KARTY PROJEKTU po dopisaniu
    interpretacji i padł na `1 > 1` dopiero w przebiegu z bazą 21.09. Karta powstaje z hipotez
    i faktów, a interpretacja idzie do „Decyzji i powodów"; dokument, którego wejście się nie
    zmieniło, NIE MA prawa dostać nowej wersji, bo o tym właśnie jest odcisk wejścia. To zdanie
    jest tanie do sprawdzenia tutaj i kosztowne do odkrycia tam.
    """
    first = _documents([Item("INTERPRETATION", "wieczorem odzew jest mniejszy")])
    second = _documents(
        [
            Item("INTERPRETATION", "wieczorem odzew jest mniejszy"),
            Item("INTERPRETATION", "pytania o koszty idą lepiej rano"),
        ]
    )

    assert second[ROLE_DECISIONS].input_sha256 != first[ROLE_DECISIONS].input_sha256
    assert second[ROLE_PROJECT_CARD].input_sha256 == first[ROLE_PROJECT_CARD].input_sha256
    assert second[ROLE_WHAT_WORKS].input_sha256 == first[ROLE_WHAT_WORKS].input_sha256, (
        "dokument o tym, co działa, bierze wyłącznie zmierzone wyniki, więc opinia go nie rusza"
    )
