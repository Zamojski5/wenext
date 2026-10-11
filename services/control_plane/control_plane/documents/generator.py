"""Z rejestru ustaleń w dokumenty, regułami, bez modelu (P8 etap A, D-043).

CO TEN MODUŁ ROBI, A CZEGO NIE ROBI. Bierze ustalenia (`knowledge_items`) i układa z nich zdania
w stałych rolach. Nie pisze zdań od siebie, nie streszcza i nie wnioskuje: tekst każdego zdania
powstaje z SZABLONU wypełnionego treścią ustalenia, a lista podstaw wskazuje wprost, z których
wierszy ono wynika. Dzięki temu dokument da się przeczytać jako twierdzenie produktu i sprawdzić
co do zdania, a rejestr pozostaje jedynym źródłem prawdy.

DLACZEGO BEZ MODELU NA TYM ETAPIE. Model wolno tu będzie zawołać (etap C) do JEDNEJ rzeczy: gdy
ustaleń o jednym temacie jest więcej, niż człowiek przeczyta, skróci je do akapitu. Wszystko
poza tym da się policzyć regułami, a reguła, w przeciwieństwie do modelu, nie doda zdania, którego
nikt nie ustalił. Generator regułowy jest też tym, co zostaje, gdy model jest niedostępny: wtedy
dokument jest dłuższy, a nie pusty.

ODCISK WEJŚCIA. `input_digest` liczy skrót z posortowanych identyfikatorów i dat ustaleń, które
weszły do dokumentu. Dwa przebiegi bez zmian w rejestrze dają ten sam odcisk, więc drugi nie
tworzy nowej wersji. Job przepisujący dokumenty (etap B) opiera na tym całą swoją idempotencję;
kolumna jest w 0028 od początku, żeby nie musiał tego zgadywać z treści tekstu.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

#: Role dokumentów w kolejności, w jakiej czyta się je od góry: czym jest ten projekt, co nam
#: w nim wychodzi, co postanowiliśmy, gdzie jesteśmy. Podręcznik platformy (dokument przestrzeni)
#: dochodzi w etapie C i dlatego nie ma go w tej krotce.
ROLE_PROJECT_CARD = "PROJECT_CARD"
ROLE_WHAT_WORKS = "WHAT_WORKS"
ROLE_DECISIONS = "DECISIONS"
ROLE_COMMUNITIES = "COMMUNITIES"
ROLE_PLATFORM_HANDBOOK = "PLATFORM_HANDBOOK"
DOCUMENT_ROLES: tuple[str, ...] = (
    ROLE_PROJECT_CARD,
    ROLE_WHAT_WORKS,
    ROLE_DECISIONS,
    ROLE_COMMUNITIES,
)

#: Tytuły, po polsku, bo czyta je człowiek i model w prompcie.
TITLES: dict[str, str] = {
    ROLE_PROJECT_CARD: "Karta projektu",
    ROLE_WHAT_WORKS: "Co działa, a co nie",
    ROLE_DECISIONS: "Decyzje i powody",
    ROLE_COMMUNITIES: "Społeczności i ich zwyczaje",
    ROLE_PLATFORM_HANDBOOK: "Podręcznik platformy",
}

#: Klucz, którym CZŁOWIEK oznacza hipotezę przy zapisie ustalenia (poprawka koordynatora z
#: 20.09). Generator go tylko CZYTA: nic tutaj nie zgaduje, co jest hipotezą, bo hipoteza
#: postawiona przez maszynę nie jest hipotezą nikogo.
HYPOTHESIS_KEY = "hypothesis"

#: Rodzaje ustaleń z 0016.
KIND_FACT = "FACT"
KIND_INTERPRETATION = "INTERPRETATION"
KIND_PREFERENCE = "PREFERENCE"
KIND_EXPERIENCE = "EXPERIENCE"

#: Wyniki publikacji z `knowledge/experience.py`. „Zadziałało” to odpowiedź albo punkty, „nie
#: zadziałało” to usunięcie albo cisza. Trzeciej kategorii nie ma, bo ustalenie bez zmierzonego
#: wyniku w ogóle nie powstaje (tamten moduł zwraca wtedy `None`).
OUTCOME_WORKED = ("REPLIED", "SCORED")
OUTCOME_DID_NOT = ("REMOVED", "NO_REACTION")

#: Ile zdań jednej sekcji wchodzi do dokumentu. Reszta jest w rejestrze i narzędzie ją poda;
#: dokument ma być czytelny, a nie kompletny, i to jest różnica między widokiem a kopią.
MAX_SENTENCES_PER_SECTION = 25


@dataclass(frozen=True)
class Sentence:
    """Jedno zdanie dokumentu wraz z tym, z czego wynika.

    `basis` NIGDY nie jest puste: więz w 0028 nie przyjmie takiego wiersza, a i tutaj zdanie bez
    podstawy nie miałoby czego twierdzić.
    """

    section: str
    text: str
    basis: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if not self.basis:
            raise ValueError(f"zdanie bez podstawy: {self.text[:60]!r}")


@dataclass(frozen=True)
class Draft:
    """Dokument gotowy do zapisania: rola, tytuł, zdania i odcisk wejścia."""

    role: str
    title: str
    sentences: tuple[Sentence, ...]
    input_sha256: str
    item_count: int
    platform: str | None = None
    #: Ustalenia, które weszły do tego dokumentu. Store sprawdza ich istnienie przed zapisem.
    items: tuple[UUID, ...] = field(default_factory=tuple)


def input_digest(items: list[Any] | tuple[Any, ...]) -> str:
    """Skrót z tego, CO weszło do dokumentu, a nie z tego, co z tego wyszło.

    Liczony z par (identyfikator, `valid_from`) posortowanych po identyfikatorze, więc kolejność
    odczytu z bazy go nie zmienia, a korekta ustalenia zmienia (bo zmienia się `valid_from`
    nowego wiersza). Skrót z TEKSTU dokumentu byłby gorszy: zmiana szablonu zdania wyglądałaby
    wtedy jak zmiana wiedzy i przepisywałaby wszystkie dokumenty bez powodu.
    """
    parts = sorted(f"{item.id}:{item.valid_from.isoformat()}" for item in items)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _moment(value: datetime) -> str:
    return value.date().isoformat()


def _sentences_of(
    items: list[Any], section: str, template: Any, limit: int = MAX_SENTENCES_PER_SECTION
) -> list[Sentence]:
    out: list[Sentence] = []
    for item in items[:limit]:
        text = template(item)
        if not text or not text.strip():
            continue
        out.append(Sentence(section=section, text=text.strip(), basis=(item.id,)))
    return out


def _is_hypothesis(item: Any) -> bool:
    return bool((item.payload or {}).get(HYPOTHESIS_KEY) is True)


def _part(item: Any, key: str) -> dict[str, Any]:
    """Część `payload` o zadanym kluczu, ale TYLKO gdy jest obiektem; inaczej pusty słownik.

    POWÓD JEST ZMIERZONY, nie teoretyczny (zasiew e2e, 20.09): więz `knowledge_items_experience_
    is_complete` z 0016 sprawdza OBECNOŚĆ kluczy `situation`, `decision` i `outcome`, a nie ich
    kształt, więc baza przyjmie `{"situation": "rozmawialiśmy o cenach"}` bez mrugnięcia. Wpisać
    to może model przez `memory.write`, bo nic mu nie mówi, że to ma być obiekt. Generator
    czytający takie ustalenie bez tej osłony przewracał się na `'str' object has no attribute
    'get'`.

    DLACZEGO TO WAŻNE AKURAT TUTAJ: od etapu B dokumenty przepisuje job w tle. Job, który padnie
    na JEDNYM ustaleniu, przestaje przepisywać WSZYSTKIE dokumenty projektu, a widać to dopiero
    po tym, że dokumenty przestały się zmieniać. Cisza jest tu gorsza niż brak sekcji.

    Ustalenie w złym kształcie nie znika: zostaje w rejestrze i wchodzi do dokumentów wszędzie
    tam, gdzie liczy się samo zdanie. Nie wchodzi tylko tam, gdzie potrzebne było pole, którego
    nie ma, i to jest ta sama zasada, co „brak danych to nie zero".
    """
    part = (item.payload or {}).get(key)
    return part if isinstance(part, dict) else {}


def _who(item: Any) -> str:
    return {"USER": "człowiek", "AGENT": "agent", "SOURCE": "pomiar"}.get(
        item.stated_by, item.stated_by
    )


def build_documents(
    items: list[Any],
    *,
    project_name: str,
    project_goal: str,
    project_mode: str,
    communities: tuple[str, ...] = (),
    accounts: tuple[str, ...] = (),
) -> tuple[Draft, ...]:
    """Cztery dokumenty projektu z tego, co w rejestrze OBOWIĄZUJE.

    Ustalenia wycofane i poprawione nie wchodzą: dokument mówi, co wiemy teraz, a historię widać
    w rejestrze. Dokument bez ani jednego zdania też powstaje, i to jest celowe. „Nikt jeszcze
    nie postawił hipotezy” oraz „nie mamy zmierzonego wyniku żadnej publikacji” to zdania, które
    człowiek ma zobaczyć, a nie braki, które ekran ma przemilczeć.
    """
    live = [one for one in items if one.superseded_by is None and one.retracted_at is None]
    return (
        _project_card(live, project_name, project_goal, project_mode, communities, accounts),
        _what_works(live),
        _decisions(live),
        _communities(live, communities),
    )


def _draft(role: str, sentences: list[Sentence], used: list[Any]) -> Draft:
    return Draft(
        role=role,
        title=TITLES[role],
        sentences=tuple(sentences),
        input_sha256=input_digest(used),
        item_count=len(used),
        items=tuple(one.id for one in used),
    )


def _project_card(
    items: list[Any],
    project_name: str,
    project_goal: str,
    project_mode: str,
    communities: tuple[str, ...],
    accounts: tuple[str, ...],
) -> Draft:
    """Czym jest ten projekt i co w nim SPRAWDZAMY.

    SEKCJA „HIPOTEZY” JEST ZAWSZE, także pusta, i to jest poprawka koordynatora z 20.09:
    projekt bez postawionej hipotezy to stan, który człowiek ma zobaczyć. Hipotezę stawia
    człowiek, jawnie (`payload.hypothesis = true`); generator niczego tu nie domyśla się z
    treści, bo hipoteza wywnioskowana przez maszynę nie jest niczyją hipotezą.
    """
    hypotheses = [one for one in items if _is_hypothesis(one)]
    facts = [
        one
        for one in items
        if one.kind == KIND_FACT and not _is_hypothesis(one) and one.project_id is not None
    ]
    sentences: list[Sentence] = []
    used: list[Any] = []

    if hypotheses:
        sentences += _sentences_of(
            hypotheses,
            "Hipotezy",
            lambda one: f"{one.statement} (postawił {_who(one)}, {_moment(one.valid_from)})",
        )
        used += hypotheses[:MAX_SENTENCES_PER_SECTION]

    if facts:
        sentences += _sentences_of(facts, "Co wiemy o projekcie", lambda one: one.statement)
        used += facts[:MAX_SENTENCES_PER_SECTION]

    return _draft(ROLE_PROJECT_CARD, sentences, used)


def _what_works(items: list[Any]) -> Draft:
    """Co wyszło, a co nie, po ZMIERZONYCH wynikach publikacji.

    Bierze wyłącznie ustalenia rodzaju EXPERIENCE, bo tylko one mają w `payload` wynik pomiaru
    (`knowledge/experience.py`). Zdanie modelu o tym, co „chyba działa”, nie jest wynikiem i nie
    wchodzi tu nawet wtedy, gdy brzmi mądrze.
    """
    worked: list[Any] = []
    failed: list[Any] = []
    for one in items:
        if one.kind != KIND_EXPERIENCE:
            continue
        kind = str(_part(one, "outcome").get("kind") or "")
        if kind in OUTCOME_WORKED:
            worked.append(one)
        elif kind in OUTCOME_DID_NOT:
            failed.append(one)

    sentences: list[Sentence] = []
    used: list[Any] = []
    if worked:
        sentences += _sentences_of(worked, "Zadziałało", lambda one: one.statement)
        used += worked[:MAX_SENTENCES_PER_SECTION]
    if failed:
        sentences += _sentences_of(failed, "Nie zadziałało", lambda one: one.statement)
        used += failed[:MAX_SENTENCES_PER_SECTION]
    return _draft(ROLE_WHAT_WORKS, sentences, used)


def _decisions(items: list[Any]) -> Draft:
    """Co postanowiliśmy i dlaczego: preferencje człowieka i interpretacje, które nie są hipotezą.

    Hipotezy są w karcie projektu i NIE POWTARZAJĄ SIĘ tutaj: to samo zdanie w dwóch dokumentach
    czyta się jak dwa ustalenia, a jest jednym.
    """
    preferences = [one for one in items if one.kind == KIND_PREFERENCE and not _is_hypothesis(one)]
    readings = [one for one in items if one.kind == KIND_INTERPRETATION and not _is_hypothesis(one)]
    sentences: list[Sentence] = []
    used: list[Any] = []
    if preferences:
        sentences += _sentences_of(
            preferences,
            "Tak chcemy pracować",
            lambda one: f"{one.statement} (ustalił {_who(one)}, {_moment(one.valid_from)})",
        )
        used += preferences[:MAX_SENTENCES_PER_SECTION]
    if readings:
        sentences += _sentences_of(
            readings,
            "Jak to czytamy",
            lambda one: f"{one.statement} ({_who(one)}, {_moment(one.valid_from)})",
        )
        used += readings[:MAX_SENTENCES_PER_SECTION]
    return _draft(ROLE_DECISIONS, sentences, used)


def _communities(items: list[Any], communities: tuple[str, ...]) -> Draft:
    """Gdzie jesteśmy i czego się tam nauczyliśmy.

    Grupuje po społeczności z `payload.situation.community`, czyli po tym samym polu, którym
    posługuje się rejestr doświadczeń. Ustalenie bez społeczności nie trafia tu wcale, zamiast
    lądować w worku „inne”: worek jest miejscem, w którym wiedza przestaje być znajdowana.
    """
    by_community: dict[str, list[Any]] = {}
    for one in items:
        where = str(_part(one, "situation").get("community") or "").strip()
        if where:
            by_community.setdefault(where, []).append(one)

    sentences: list[Sentence] = []
    used: list[Any] = []
    for where in sorted(by_community):
        group = by_community[where]
        sentences += _sentences_of(group, where, lambda one: one.statement)
        used += group[:MAX_SENTENCES_PER_SECTION]
    return _draft(ROLE_COMMUNITIES, sentences, used)
