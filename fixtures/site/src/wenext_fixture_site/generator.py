"""Deterministic content generator.

Three rules that hold determinism in place:

1. The only input is `seed`. No `datetime.now()`, no `random` without a seed,
   no `uuid4()`, no `set`/`dict` iterated in an order that depends on hashes.
2. The seed of every thread is derived from `blake2b(seed:key)`, not from a counter —
   adding a thread in the middle of the list does not shift the content of the rest.
3. Time runs from the constant `FIXTURE_EPOCH`, not from the clock.

The metrics are NOT copied over from the seed: `median_reply_chars`, `depth_levels`,
`unique_participants` and `substantive_reply_ratio` are computed from the tree actually
built. The seed declares the intent, the generator has to realise it and show the result.
"""

from __future__ import annotations

import hashlib
import random
import statistics
import unicodedata
from datetime import timedelta
from functools import lru_cache

from .config import FIXTURE_EPOCH
from .models import (
    Amplification,
    Comment,
    Community,
    Reaction,
    Repository,
    Thread,
    ThreadMetrics,
)
from .seed_data import ALL_SPECS, COMMUNITIES, ThreadSpec, validate

# ---------------------------------------------------------------------------
# Text pools
# ---------------------------------------------------------------------------

FIRST_NAMES = (
    "Anna",
    "Marek",
    "Katarzyna",
    "Piotr",
    "Zofia",
    "Tomasz",
    "Magdalena",
    "Rafał",
    "Julia",
    "Krzysztof",
    "Ewa",
    "Michał",
    "Alicja",
    "Bartosz",
    "Iwona",
    "Damian",
    "Natalia",
    "Grzegorz",
    "Weronika",
    "Łukasz",
    "Paulina",
    "Adam",
    "Monika",
    "Jakub",
    "Karolina",
    "Wojciech",
    "Agnieszka",
    "Sebastian",
    "Dorota",
    "Filip",
    "Barbara",
    "Kamil",
    "Renata",
    "Norbert",
    "Sylwia",
    "Artur",
    "Beata",
    "Marcin",
    "Justyna",
    "Igor",
    "Aleksandra",
    "Dawid",
    "Elżbieta",
    "Patryk",
    "Hanna",
    "Konrad",
    "Emilia",
    "Mateusz",
    "Klaudia",
    "Radosław",
    "Marta",
    "Szymon",
    "Gabriela",
    "Przemysław",
    "Lidia",
    "Oskar",
    "Wiktoria",
    "Cezary",
    "Sandra",
    "Miłosz",
    "Nina",
    "Jarosław",
    "Olga",
    "Robert",
    "Aneta",
    "Paweł",
    "Teresa",
    "Andrzej",
    "Kinga",
    "Dariusz",
    "Malwina",
    "Witold",
    "Roksana",
    "Leszek",
    "Judyta",
    "Ignacy",
    "Sabina",
    "Borys",
    "Halina",
    "Maciej",
    "Melania",
    "Tadeusz",
    "Nikola",
    "Antoni",
    "Urszula",
    "Bogdan",
    "Liliana",
    "Emil",
)

LAST_NAMES = (
    "Kowalska",
    "Nowak",
    "Wiśniewski",
    "Wójcik",
    "Kowalczyk",
    "Kamińska",
    "Lewandowski",
    "Zielińska",
    "Szymański",
    "Woźniak",
    "Dąbrowska",
    "Kozłowski",
    "Jankowska",
    "Mazur",
    "Krawczyk",
    "Piotrowska",
    "Grabowski",
    "Nowicka",
    "Pawłowski",
    "Michalska",
    "Adamczyk",
    "Dudek",
    "Zając",
    "Wieczorek",
    "Jabłońska",
    "Król",
    "Majewski",
    "Olszewska",
    "Jaworski",
    "Malinowska",
    "Pawlak",
    "Witkowski",
    "Walczak",
    "Stępień",
    "Górska",
    "Rutkowski",
    "Michalak",
    "Sikora",
    "Ostrowska",
    "Baran",
    "Duda",
    "Szewczyk",
    "Tomaszewska",
    "Pietrzak",
    "Marciniak",
    "Wróbel",
    "Zalewska",
    "Jakubowski",
    "Jasińska",
    "Zawadzki",
    "Sadowska",
    "Bąk",
    "Chmielewski",
    "Borkowska",
    "Sokołowski",
    "Szczepańska",
    "Kucharski",
    "Wesołowska",
    "Lis",
    "Mazurek",
    "Wysocka",
    "Czarnecki",
    "Sawicka",
    "Kołodziej",
)

#: Replies with substance: a number, a source, code, own experience, a counterargument (L4439).
SUBSTANTIVE_TEMPLATES = (
    (
        "Mierzyliśmy to u siebie na {n} instancjach: mediana spadła z {a} ms do {b} ms, "
        "ale ogon p99 urósł o {c}%. Bez tego ogona liczby wyglądają lepiej, niż jest."
    ),
    (
        "W dokumentacji jest to opisane inaczej — sekcja {sec} mówi wprost, że limit dotyczy "
        "całego procesu, nie pojedynczego połączenia. Sprawdziłem to na wersji {ver}."
    ),
    (
        "Kontrargument: to działa tylko dopóki masz mniej niż {n} równoległych zapisów. "
        "Powyżej tego progu blokada tabeli zaczyna dominować i cały zysk znika."
    ),
    (
        "U nas ta sama zmiana kosztowała {n} godzin pracy dwóch osób i {b} zł miesięcznie "
        "więcej na transferze. Zwróciło się po {c} miesiącach, ale nie było oczywiste."
    ),
    (
        "Konkret z produkcji: przy {n} tys. zdarzeń na minutę bufor {a} MB wystarczał, "
        "przy {b} tys. już nie. Przełączyliśmy się na wariant strumieniowy i problem zniknął."
    ),
    (
        "Zrobiłem to inaczej: zamiast osobnego procesu zostawiłem jeden wątek i limit {n}. "
        "Kod ma {a} linii zamiast {b} i nie trzeba pilnować kolejności zamykania."
    ),
    (
        "Warto dodać źródło: raport branżowy z {ver} podaje medianę {n} dni dla tej samej "
        "wielkości zespołu. Wasze {a} dni to wynik znacznie powyżej przeciętnej."
    ),
    (
        "Nie zgadzam się z wnioskiem. Ten sam wykres da się wytłumaczyć sezonowością — "
        "u nas identyczny skok pojawia się co roku w {sec} tygodniu i nie ma związku z wdrożeniem."
    ),
    (
        "Uzupełnienie z własnego podwórka: prowadziliśmy to przez {n} miesięcy i największym "
        "kosztem nie było wdrożenie, tylko utrzymanie dwóch ścieżek naraz przez {a} tygodni."
    ),
    (
        "Sprawdziłem na swoich danych: przy progu {a} fałszywe alarmy stanowiły {c}% wszystkich, "
        "przy {b} spadły do {n}%, ale przegapiliśmy dwa realne incydenty."
    ),
)

THIN_TEMPLATES = (
    "+1",
    "Dokładnie tak.",
    "Zgadzam się w stu procentach.",
    "Świetny post, dzięki.",
    "Zapisuję na później.",
    "To.",
    "Podbijam, też mnie to interesuje.",
    "Dobra robota!",
    "Ciekawe.",
    "Śledzę wątek.",
    "Mocne.",
    "Warto przeczytać.",
)

AUTHOR_TEMPLATES = (
    (
        "Dopisuję kontekst: przy {n} użytkownikach to zachowanie faktycznie się zmienia, "
        "masz rację. Poprawiłem opis w poście."
    ),
    (
        "Dzięki za strzał w metodologię — policzyłem jeszcze raz z twoim założeniem i wyszło {a}, "
        "czyli o {c}% mniej. Zaktualizuję tabelę."
    ),
    (
        "Odpowiadam na krytykę wprost: tak, pominęliśmy koszt migracji danych. To był błąd "
        "w liczeniu, nie w tezie, i nie zmienia kierunku wniosku."
    ),
    "Nie mam na to twardych danych, więc nie będę zgadywał. Sprawdzę i wrócę z liczbą.",
    "Uzupełniam: w wersji {ver} to zachowanie jest już domyślne, więc pytanie odpada.",
    "To jest dobra uwaga i nie mam na nią odpowiedzi. Zostawiam otwarte.",
)

NEGATIVE_TEMPLATES = (
    "To wygląda na reklamę przebraną za dyskusję.",
    "Trzeci taki post w tym tygodniu z tego konta. Zgłaszam.",
    "Konto założone wczoraj, same posty promocyjne. Bot?",
    "Brak jakichkolwiek danych, same hasła. Minus.",
    "Nie odpowiedziałeś na żadne pytanie z komentarzy.",
    "Ten wykres nie ma podpisanych osi i nie da się go zweryfikować.",
)

QUESTION_TEMPLATES = (
    "A jak to wygląda przy mniejszej skali? U nas to jest {n} rekordów, nie {a}.",
    "Możesz podać wersję, na której to testowałeś?",
    "Czy próbowaliście wariantu bez osobnego procesu?",
    "Co z kosztem po stronie utrzymania?",
    "Jak długo trwało wdrożenie od decyzji do produkcji?",
    "Który krok okazał się najtrudniejszy?",
)


def _derive_rng(seed: int, key: str) -> random.Random:
    digest = hashlib.blake2b(f"{seed}:{key}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _short_hash(value: str) -> str:
    return hashlib.blake2b(value.encode(), digest_size=3).hexdigest()


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    out: list[str] = []
    for char in ascii_text:
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:48] or "watek"


def _pick(rng: random.Random, pool: tuple[str, ...]) -> str:
    return pool[rng.randrange(len(pool))]


def _fill(rng: random.Random, template: str) -> str:
    return template.format(
        n=rng.randrange(3, 900),
        a=rng.randrange(4, 4000),
        b=rng.randrange(4, 4000),
        c=rng.randrange(2, 80),
        sec=rng.randrange(2, 19),
        ver=f"{rng.randrange(2, 9)}.{rng.randrange(0, 12)}",
    )


def _people(rng: random.Random, count: int) -> tuple[str, ...]:
    """Unique names without `sample()` — we want to depend on `randrange` alone."""

    chosen: list[str] = []
    seen: set[str] = set()
    guard = 0
    while len(chosen) < count and guard < count * 40:
        guard += 1
        name = f"{_pick(rng, FIRST_NAMES)} {_pick(rng, LAST_NAMES)}"
        if name not in seen:
            seen.add(name)
            chosen.append(name)
    while len(chosen) < count:
        i = len(chosen)
        chosen.append(f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[i % len(LAST_NAMES)]} {i}")
    return tuple(chosen)


def _handle(name: str) -> str:
    return _slugify(name).replace("-", ".")


# ---------------------------------------------------------------------------
# Building the comment tree
# ---------------------------------------------------------------------------


class _Node:
    __slots__ = ("children", "index", "level", "parent")

    def __init__(self, index: int, parent: int | None, level: int) -> None:
        self.index = index
        self.parent = parent
        self.level = level
        self.children: list[int] = []


def _build_shape(rng: random.Random, count: int, max_depth: int) -> list[_Node]:
    """Shape of the tree: first the spine down to the given depth, then the rest.

    A parent always has a lower index than a child — thanks to that the growing timestamps
    assigned by index cannot produce a child older than its parent.
    """

    nodes: list[_Node] = []
    if count <= 0:
        return nodes

    spine = min(max_depth, count)
    for i in range(spine):
        parent = i - 1 if i > 0 else None
        node = _Node(i, parent, i + 1)
        if parent is not None:
            nodes[parent].children.append(i)
        nodes.append(node)

    for i in range(spine, count):
        candidates = [n.index for n in nodes if n.level < max_depth]
        # Some of the replies always go to the first level — otherwise the thread becomes
        # artificially chain-like and the number of unique branches does not grow.
        if not candidates or rng.random() < 0.42:
            node = _Node(i, None, 1)
        else:
            parent = candidates[rng.randrange(len(candidates))]
            node = _Node(i, parent, nodes[parent].level + 1)
            nodes[parent].children.append(i)
        nodes.append(node)
    return nodes


def _offsets(rng: random.Random, spec: ThreadSpec) -> list[int]:
    """Reply timestamps in minutes from the thread's creation, ascending."""

    count = spec.reply_count
    if count == 0:
        return []
    first_hour = min(spec.replies_first_hour, count)
    later = count - first_hour
    horizon = max(spec.age_minutes, 61)

    values: list[int] = []
    for i in range(first_hour):
        # Spread across the first hour, ascending, with no dependence on sorting.
        base = 1 + (59 * i) // max(first_hour, 1)
        values.append(min(59, base + rng.randrange(0, 2)))
    for i in range(later):
        span = horizon - 60
        base = 60 + (span * (i + 1)) // (later + 1)
        values.append(min(horizon - 1, base + rng.randrange(0, max(2, span // 40 + 2))))
    values.sort()
    return values


def _body_for(
    rng: random.Random,
    *,
    substantive: bool,
    author: bool,
    negative: bool,
) -> str:
    if author:
        return _fill(rng, _pick(rng, AUTHOR_TEMPLATES))
    if negative:
        return _pick(rng, NEGATIVE_TEMPLATES)
    if substantive:
        return _fill(rng, _pick(rng, SUBSTANTIVE_TEMPLATES))
    if rng.random() < 0.28:
        return _fill(rng, _pick(rng, QUESTION_TEMPLATES))
    return _pick(rng, THIN_TEMPLATES)


def _build_thread(seed: int, archetype: str, spec: ThreadSpec) -> Thread:
    rng = _derive_rng(seed, spec.key)

    created_at = FIXTURE_EPOCH - timedelta(minutes=spec.age_minutes)
    author = f"{_pick(rng, FIRST_NAMES)} {_pick(rng, LAST_NAMES)}"
    author_handle = _handle(author)

    participants = _people(rng, max(spec.unique_participants, 1))
    icp_count = 0 if spec.icp_share is None else round(len(participants) * spec.icp_share)
    icp_members = set(participants[:icp_count])

    shape = _build_shape(rng, spec.reply_count, spec.depth)
    offsets = _offsets(rng, spec)

    author_slots: set[int] = set()
    if spec.author_replies and shape:
        # The author's first reply has to land exactly at the declared time.
        first_slot = 0
        for node in shape:
            if offsets[node.index] >= (spec.author_first_reply_minutes or 0):
                first_slot = node.index
                break
        # The remaining author replies are spread evenly over indices later than the first
        # one. Thanks to that `author_reply_count` in the metrics really equals what the
        # seed declares — and not what came out of the loop step.
        available = len(shape) - first_slot
        count = min(spec.author_replies, available)
        for k in range(count):
            offset = 0 if count == 1 else (k * (available - 1)) // (count - 1)
            author_slots.add(first_slot + offset)

    substantive_target = round(spec.reply_count * spec.substantive_ratio)
    substantive_slots: set[int] = set()
    idx = 0
    while len(substantive_slots) < substantive_target and idx < len(shape):
        if idx not in author_slots:
            substantive_slots.add(idx)
        idx += 2
    idx = 1
    while len(substantive_slots) < substantive_target and idx < len(shape):
        substantive_slots.add(idx)
        idx += 1

    removed_slots: set[int] = set()
    if spec.negative_comment_votes and len(shape) > 4:
        removed_slots.add(len(shape) // 3)

    negative_slots: set[int] = set()
    if spec.negative_comment_votes:
        for i in range(len(shape)):
            if i % 3 == 1:
                negative_slots.add(i)

    built: dict[int, Comment] = {}
    for node in reversed(shape):
        i = node.index
        is_author = i in author_slots
        speaker = author if is_author else participants[i % len(participants)]
        is_negative = i in negative_slots and not is_author
        is_substantive = i in substantive_slots and not is_negative
        body = _body_for(
            rng,
            substantive=is_substantive,
            author=is_author,
            negative=is_negative,
        )
        if is_negative or spec.negative_comment_votes:
            votes = rng.randrange(-34, 6)
        elif is_substantive:
            votes = rng.randrange(3, 96)
        else:
            votes = rng.randrange(-2, 18)
        built[i] = Comment(
            id=f"{spec.key}-c{i:03d}",
            author=speaker,
            body=body,
            created_at=created_at + timedelta(minutes=offsets[i]),
            depth=node.level,
            votes=votes,
            by_thread_author=is_author,
            substantive=is_substantive,
            icp=speaker in icp_members,
            removed_by_moderation=i in removed_slots,
            replies=tuple(built[c] for c in sorted(node.children)),
        )

    roots = tuple(built[n.index] for n in shape if n.parent is None)

    bodies = [c.body for c in built.values() if not c.removed_by_moderation]
    median_chars = int(statistics.median([len(b) for b in bodies])) if bodies else 0
    actual_participants = len({c.author for c in built.values()})
    actual_depth = max((c.depth for c in built.values()), default=0)
    actual_substantive = sum(1 for c in built.values() if c.substantive)
    ratio = round(actual_substantive / len(built), 4) if built else 0.0
    first_hour = sum(
        1 for c in built.values() if (c.created_at - created_at).total_seconds() < 3600
    )

    hidden: list[str] = []
    if spec.reach_impressions is None:
        hidden.append("reach_impressions")
    if spec.icp_share is None:
        hidden.append("icp_share")

    metrics = ThreadMetrics(
        reply_count=len(built),
        unique_participants=actual_participants,
        depth_levels=actual_depth,
        median_reply_chars=median_chars,
        replies_first_hour=first_hour,
        replies_after_first_hour=len(built) - first_hour,
        substantive_reply_ratio=ratio,
        author_reply_count=sum(1 for c in built.values() if c.by_thread_author),
        author_first_reply_minutes=spec.author_first_reply_minutes,
        icp_share=spec.icp_share,
        reach_impressions=spec.reach_impressions,
        reach_calibration_basis=spec.calibration_basis,
        reaction_burst_without_replies=spec.reaction_burst_without_replies,
        hidden_metrics=tuple(hidden),
    )

    amplifications = tuple(
        Amplification(
            kind=kind,
            source=source,
            comment=comment,
            created_at=created_at + timedelta(minutes=30 + 47 * i),
        )
        for i, (kind, source, comment) in enumerate(spec.amplifications)
    )

    reactions = tuple(Reaction(kind=kind, count=count) for kind, count in spec.reactions)

    return Thread(
        id=spec.key,
        slug=f"{_slugify(spec.title)}-{_short_hash(spec.key)}",
        archetype=archetype,
        community=spec.community,
        title=spec.title,
        body=spec.body,
        author=author,
        author_handle=author_handle,
        created_at=created_at,
        votes=spec.votes,
        locked=spec.locked,
        removed_by_moderation=spec.removed_by_moderation,
        window_state=spec.window_state,
        negative_signals=spec.negative_signals,
        reactions=reactions,
        amplifications=amplifications,
        comments=roots,
        metrics=metrics,
        spec_key=spec.key,
        illustrates=spec.illustrates,
        seed_note=spec.note,
        fake_engagement_flags=spec.fake_engagement_flags,
    )


@lru_cache(maxsize=4)
def build_repository(seed: int) -> Repository:
    validate()
    communities = tuple(
        Community(slug=slug, name=name, description=desc, members=members, archetype=archetype)
        for slug, name, desc, members, archetype in COMMUNITIES
    )
    threads = tuple(_build_thread(seed, archetype, spec) for archetype, spec in ALL_SPECS)
    return Repository(seed=seed, communities=communities, threads=threads)


def feed_new(threads: tuple[Thread, ...]) -> tuple[Thread, ...]:
    return tuple(sorted(threads, key=lambda t: (-t.created_at.timestamp(), t.id)))


def feed_rising(threads: tuple[Thread, ...]) -> tuple[Thread, ...]:
    """The fixture's "rising" feed order — this is not the system's ranking.

    The fixture pretends to be a platform, so it sorts by its own, explicit criterion:
    window freshness and the growth in the first hour. The system under test is to IGNORE
    this ordering and compute its own — which is why it is simple and explicit here.
    """

    window_rank = {"RISING": 0, "PEAK": 1, "DECAYING": 2, "CLOSED": 3}
    return tuple(
        sorted(
            threads,
            key=lambda t: (
                window_rank.get(t.window_state, 9),
                -t.metrics.replies_first_hour,
                t.id,
            ),
        )
    )
