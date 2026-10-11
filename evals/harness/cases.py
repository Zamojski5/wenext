"""Case catalog — 46 evals in three families (L5168: "30-50 fixture evals").

The split follows directly from the sentence in the F0 scope: "fixture evals for
**discovery**, **discussion quality classification** and **reply drafting**". Every family
has both archetypes — a single-archetype fixture makes DoD F3 unverifiable (L4304, L5347).

The cases are DATA, not code. The runner (`runner.py`) and the scoring (`scoring/checks.py`)
do not know which thread they concern — they know only what the definition from 22.7 requires.

What is deliberately not here:
- `CONVERSATION_FOLLOW_UP` cases — in E1 the conversation follow-up is written by a human
  (L4569-4571), so the model has nothing to generate here;
- a percentage pass threshold — see `NOTES.md`, section „Próg".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from community_os_contracts import PriorityTier, SignalClass

from .types import Claim, ClaimStatus, DeclineReason, DraftBrief, EvalFamily, ProhibitedTopic

ARCHETYPE_THREADED = "THREADED_COMMUNITY"
ARCHETYPE_GRAPH = "PROFESSIONAL_GRAPH"


# ---------------------------------------------------------------------------
# Draft briefs
# ---------------------------------------------------------------------------

BRIEF_MIXED = DraftBrief(
    brief_id="brief-mixed",
    entry_thesis="Pokazujemy własne pomiary zamiast opinii; liczby zawsze z odsyłaczem.",
    claims=(
        Claim(
            id="c-import-time",
            text="U nas profilowanie importów skróciło start procesu z 4,1 s do 0,6 s.",
            status=ClaimStatus.VERIFIED,
            marker="4,1 s",
            evidence_ref="canonical://benchmark/import-time#v3",
        ),
        Claim(
            id="c-egress",
            text="Policzyliśmy koszt egressu dla trzech dostawców i różnica wyniosła 2,1 raza.",
            status=ClaimStatus.VERIFIED,
            marker="2,1 raza",
            evidence_ref="canonical://koszty/egress#v7",
        ),
        Claim(
            id="c-latency",
            text="Nasza kolejka trzyma opóźnienie sub-50ms przy 3,4x ruchu.",
            status=ClaimStatus.UNVERIFIED,
            marker="sub-50ms",
        ),
    ),
    prohibited_topics=(),
    approved_ctas=("zapytaj o szczegóły w wątku",),
)

BRIEF_ONLY_UNVERIFIED = DraftBrief(
    brief_id="brief-only-unverified",
    entry_thesis="Ten sam głos, ale wiedza jeszcze niepotwierdzona.",
    claims=(
        Claim(
            id="c-latency",
            text="Nasza kolejka trzyma opóźnienie sub-50ms przy 3,4x ruchu.",
            status=ClaimStatus.UNVERIFIED,
            marker="sub-50ms",
        ),
        Claim(
            id="c-savings",
            text="Klienci oszczędzają 97,3% czasu wdrożenia.",
            status=ClaimStatus.UNVERIFIED,
            marker="97,3%",
        ),
    ),
    prohibited_topics=(),
)

BRIEF_EXPIRED_RETRACTED = DraftBrief(
    brief_id="brief-expired-retracted",
    entry_thesis="Wiedza była zweryfikowana, ale straciła ważność.",
    claims=(
        Claim(
            id="c-old-benchmark",
            text="Wersja 1.2 była o 18% szybsza od poprzedniej.",
            status=ClaimStatus.EXPIRED,
            marker="18%",
        ),
        Claim(
            id="c-retracted",
            text="Zmierzyliśmy 12 tysięcy zdarzeń na sekundę na jednym węźle.",
            status=ClaimStatus.RETRACTED,
            marker="12 tysięcy",
        ),
    ),
    prohibited_topics=(),
)

BRIEF_PROHIBITED_PRICING = DraftBrief(
    brief_id="brief-prohibited-pricing",
    entry_thesis="Rozmawiamy o inżynierii, nigdy o cenach i rabatach.",
    claims=(
        Claim(
            id="c-import-time",
            text="U nas profilowanie importów skróciło start procesu z 4,1 s do 0,6 s.",
            status=ClaimStatus.VERIFIED,
            marker="4,1 s",
            evidence_ref="canonical://benchmark/import-time#v3",
        ),
    ),
    # The thread `f-icp-high` has the word „wyceniacie" in its title — the draft is to stop
    # on that instead of quoting the title by reflex.
    prohibited_topics=(
        ProhibitedTopic(id="pricing", keywords=("wycen", "cennik", "rabat", "zniżk")),
    ),
)

BRIEF_PROHIBITED_CONTRACT = DraftBrief(
    brief_id="brief-prohibited-contract",
    entry_thesis="Nie doradzamy w sprawach umów ani zamówień publicznych.",
    claims=(
        Claim(
            id="c-egress",
            text="Policzyliśmy koszt egressu dla trzech dostawców i różnica wyniosła 2,1 raza.",
            status=ClaimStatus.VERIFIED,
            marker="2,1 raza",
            evidence_ref="canonical://koszty/egress#v7",
        ),
    ),
    # The thread `g-icp-high` has „kontraktu" and „sektorze publicznym" in its title.
    prohibited_topics=(
        ProhibitedTopic(id="legal", keywords=("kontrakt", "przetarg", "sektorze publicznym")),
    ),
)

BRIEFS: dict[str, DraftBrief] = {
    b.brief_id: b
    for b in (
        BRIEF_MIXED,
        BRIEF_ONLY_UNVERIFIED,
        BRIEF_EXPIRED_RETRACTED,
        BRIEF_PROHIBITED_PRICING,
        BRIEF_PROHIBITED_CONTRACT,
    )
}


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    archetype: str
    spec_key: str
    expected_tier: PriorityTier
    note: str
    #: Assessment fields that MUST come out as UNKNOWN, because the platform hides them.
    expected_unknown: tuple[str, ...] = ()
    family: EvalFamily = EvalFamily.QUALITY


@dataclass(frozen=True)
class DiscoveryCase:
    case_id: str
    archetype: str
    note: str
    #: Empty = all threads of the given archetype.
    spec_keys: tuple[str, ...] = ()
    must_not_shortlist: tuple[str, ...] = ()
    must_be_last: tuple[str, ...] = ()
    must_precede: tuple[tuple[str, str], ...] = ()
    family: EvalFamily = EvalFamily.DISCOVERY


@dataclass(frozen=True)
class SignalPairCase:
    """A pair of candidates differing ONLY in signal class (L4471-4479)."""

    case_id: str
    archetype: str
    base_spec_key: str
    stronger: SignalClass
    weaker: SignalClass
    note: str
    tie: bool = False
    family: EvalFamily = EvalFamily.DISCOVERY


@dataclass(frozen=True)
class DraftCase:
    case_id: str
    archetype: str
    spec_key: str
    brief_id: str
    note: str
    expect_declined: bool = False
    expected_decline_reason: DeclineReason | None = None
    family: EvalFamily = EvalFamily.DRAFT


# ---------------------------------------------------------------------------
# 20 cases: discussion quality classification
# ---------------------------------------------------------------------------

QUALITY_CASES: tuple[QualityCase, ...] = (
    # --- THREADED_COMMUNITY ---
    QualityCase(
        case_id="q-f-neg-locked-high-engagement",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-neg-locked-high-engagement",
        expected_tier=PriorityTier.REJECT,
        note="641 głosów + lock. DoD: żadna liczba sygnałów pozytywnych nie kompensuje.",
    ),
    QualityCase(
        case_id="q-f-neg-removed",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-neg-removed",
        expected_tier=PriorityTier.REJECT,
        note="Usunięcie moderacyjne + lock + ujemne głosy.",
    ),
    QualityCase(
        case_id="q-f-neg-ratio",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-neg-ratio",
        expected_tier=PriorityTier.REJECT,
        note="Ratio i oskarżenie o spam przy 47 odpowiedziach — nadal stop.",
    ),
    QualityCase(
        case_id="q-f-icp-hidden",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-icp-hidden",
        expected_tier=PriorityTier.B,
        expected_unknown=("icp_share",),
        note="Społeczność zamknięta: profile rozmówców ukryte → UNKNOWN, nie zero.",
    ),
    QualityCase(
        case_id="q-f-calib-hidden",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-calib-hidden",
        expected_tier=PriorityTier.B,
        note="Brak statystyk zasięgu obniża data_quality, nie tier.",
    ),
    QualityCase(
        case_id="q-f-window-fake-burst",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-window-fake-burst",
        expected_tier=PriorityTier.C,
        note="894 głosy, 3 odpowiedzi, flagi fake. Log-only: tier bez zmian, wątek nieodrzucony.",
    ),
    QualityCase(
        case_id="q-f-depth-deep",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-depth-deep",
        expected_tier=PriorityTier.A,
        note="Wzorzec dodatni: 6 poziomów, 19 rozmówców, autor obecny, wysoki ICP.",
    ),
    QualityCase(
        case_id="q-f-substantive-low",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-substantive-low",
        expected_tier=PriorityTier.C,
        note="402 głosy i 64 odpowiedzi bez treści — ekspozycja, nie rozmowa.",
    ),
    QualityCase(
        case_id="q-f-author-absent",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-author-absent",
        expected_tier=PriorityTier.B,
        note="Autor wrzucił i zniknął — wątek żyje, ale nie jest dobrym miejscem wejścia.",
    ),
    QualityCase(
        case_id="q-f-amp-newsletter",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-amp-newsletter",
        expected_tier=PriorityTier.A,
        note="Amplifikacja z intencją: ktoś wziął na siebie koszt podania dalej.",
    ),
    # --- PROFESSIONAL_GRAPH ---
    QualityCase(
        case_id="q-g-neg-locked-high-engagement",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-neg-locked-high-engagement",
        expected_tier=PriorityTier.REJECT,
        note="2 771 reakcji + wyłączone komentarze. Twardy stop.",
    ),
    QualityCase(
        case_id="q-g-neg-removed",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-neg-removed",
        expected_tier=PriorityTier.REJECT,
        note="Usunięcie moderacyjne w sieci zawodowej.",
    ),
    QualityCase(
        case_id="q-g-neg-ratio",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-neg-ratio",
        expected_tier=PriorityTier.REJECT,
        note="Oskarżenie o bota + ratio przy 52 komentarzach.",
    ),
    QualityCase(
        case_id="q-g-icp-hidden",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-icp-hidden",
        expected_tier=PriorityTier.B,
        expected_unknown=("icp_share",),
        note="Post widoczny tylko dla połączeń pierwszego stopnia → ICP UNKNOWN.",
    ),
    QualityCase(
        case_id="q-g-calib-hidden",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-calib-hidden",
        expected_tier=PriorityTier.B,
        note="Statystyki tylko dla autora — brak zasięgu nie jest zerem zasięgu.",
    ),
    QualityCase(
        case_id="q-g-window-fake-burst",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-window-fake-burst",
        expected_tier=PriorityTier.C,
        note="137 tys. wyświetleń, 4 komentarze, flagi fake. Tier taki sam jak bez flag.",
    ),
    QualityCase(
        case_id="q-g-substantive-high",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-substantive-high",
        expected_tier=PriorityTier.A,
        note="Wzorzec dodatni w sieci zawodowej: konkret, autor w komentarzach, ICP.",
    ),
    QualityCase(
        case_id="q-g-substantive-low",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-substantive-low",
        expected_tier=PriorityTier.C,
        note="88 komentarzy w stylu 'miłego tygodnia' — 80 unikalnych osób i zero treści.",
    ),
    QualityCase(
        case_id="q-g-author-absent",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-author-absent",
        expected_tier=PriorityTier.C,
        note="Raport z linkiem w komentarzu, autor nieobecny, udział konkretu poniżej progu.",
    ),
    QualityCase(
        case_id="q-g-window-closed",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-window-closed",
        expected_tier=PriorityTier.C,
        note="Okno zamknięte bez sygnału negatywnego — kontekst wygasł, wątek nie jest zły.",
    ),
)


# ---------------------------------------------------------------------------
# 14 cases: discovery (6 rankings + 8 signal class pairs)
# ---------------------------------------------------------------------------

DISCOVERY_CASES: tuple[DiscoveryCase, ...] = (
    DiscoveryCase(
        case_id="d-f-pelny-feed",
        archetype=ARCHETYPE_THREADED,
        note="Wszystkie 24 wątki forum. Pełny porządek + niezależność od kolejności wejścia.",
    ),
    DiscoveryCase(
        case_id="d-g-pelny-feed",
        archetype=ARCHETYPE_GRAPH,
        note="Wszystkie 24 posty sieci zawodowej.",
    ),
    DiscoveryCase(
        case_id="d-f-negatyw-vs-engagement",
        archetype=ARCHETYPE_THREADED,
        spec_keys=("f-neg-locked-high-engagement", "f-depth-shallow", "f-icp-high"),
        must_not_shortlist=("f-neg-locked-high-engagement",),
        must_be_last=("f-neg-locked-high-engagement",),
        note="641 głosów przegrywa z lockiem. DoD: negatyw to stop, nie waga.",
    ),
    DiscoveryCase(
        case_id="d-g-negatyw-vs-engagement",
        archetype=ARCHETYPE_GRAPH,
        spec_keys=("g-neg-locked-high-engagement", "g-depth-flat", "g-icp-high"),
        must_not_shortlist=("g-neg-locked-high-engagement",),
        must_be_last=("g-neg-locked-high-engagement",),
        note="2 771 reakcji przegrywa z wyłączonymi komentarzami.",
    ),
    DiscoveryCase(
        case_id="d-f-unknown-nie-jest-zerem",
        archetype=ARCHETYPE_THREADED,
        spec_keys=("f-icp-hidden", "f-substantive-low", "f-icp-low"),
        must_precede=(("f-icp-hidden", "f-substantive-low"),),
        note="Wątek bez widocznego ICP nie spada na koniec z powodu braku odczytu.",
    ),
    DiscoveryCase(
        case_id="d-g-unknown-nie-jest-zerem",
        archetype=ARCHETYPE_GRAPH,
        spec_keys=("g-icp-hidden", "g-substantive-low", "g-icp-low"),
        must_precede=(("g-icp-hidden", "g-substantive-low"),),
        note="To samo w sieci zawodowej: UNKNOWN ląduje na końcu swojej klasy, nie listy.",
    ),
)


SIGNAL_PAIR_CASES: tuple[SignalPairCase, ...] = (
    SignalPairCase(
        case_id="p-f-negatyw-vs-amplifikacja",
        archetype=ARCHETYPE_THREADED,
        base_spec_key="f-depth-deep",
        stronger=SignalClass.INTENTIONAL_AMPLIFICATION,
        weaker=SignalClass.NEGATIVE_MODERATION,
        note="Dwa rzędy wielkości: negatyw wypycha na koniec ten sam wątek.",
    ),
    SignalPairCase(
        case_id="p-g-lock-vs-substantive",
        archetype=ARCHETYPE_GRAPH,
        base_spec_key="g-depth-thread",
        stronger=SignalClass.SUBSTANTIVE_REPLY,
        weaker=SignalClass.LOCK,
        note="Lock jest klasą negatywną, więc przegrywa z każdym sygnałem pozytywnym.",
    ),
    SignalPairCase(
        case_id="p-f-amplifikacja-vs-repost",
        archetype=ARCHETYPE_THREADED,
        base_spec_key="f-substantive-high",
        stronger=SignalClass.INTENTIONAL_AMPLIFICATION,
        weaker=SignalClass.REPOST,
        note="Cytat z komentarzem waży więcej niż repost bez komentarza (L4475).",
    ),
    SignalPairCase(
        case_id="p-g-substantive-vs-reply",
        archetype=ARCHETYPE_GRAPH,
        base_spec_key="g-substantive-high",
        stronger=SignalClass.SUBSTANTIVE_REPLY,
        weaker=SignalClass.REPLY,
        note="Odpowiedź z treścią waży więcej niż odpowiedź krótka (L4476).",
    ),
    SignalPairCase(
        case_id="p-f-repost-vs-reply",
        archetype=ARCHETYPE_THREADED,
        base_spec_key="f-amp-crosspost",
        stronger=SignalClass.REPOST,
        weaker=SignalClass.REPLY,
        note="Repost bez komentarza wyżej niż krótka odpowiedź (L4475-4476).",
    ),
    SignalPairCase(
        case_id="p-g-reply-vs-follow",
        archetype=ARCHETYPE_GRAPH,
        base_spec_key="g-amp-repost",
        stronger=SignalClass.REPLY,
        weaker=SignalClass.FOLLOW,
        note="Odpowiedź wyżej niż obserwowanie (L4476-4477).",
    ),
    SignalPairCase(
        case_id="p-f-subscribe-vs-upvote",
        archetype=ARCHETYPE_THREADED,
        base_spec_key="f-calib-above",
        stronger=SignalClass.SUBSCRIBE,
        weaker=SignalClass.UPVOTE,
        note="Subskrypcja wyżej niż głos w górę (L4477-4478).",
    ),
    SignalPairCase(
        case_id="p-g-amplifikacja-remis-substantive",
        archetype=ARCHETYPE_GRAPH,
        base_spec_key="g-calib-above",
        stronger=SignalClass.INTENTIONAL_AMPLIFICATION,
        weaker=SignalClass.SUBSTANTIVE_REPLY,
        tie=True,
        note="L4474: te dwie klasy są RÓWNORZĘDNE. Remis jest wynikiem, nie brakiem wyniku.",
    ),
)


# ---------------------------------------------------------------------------
# 12 cases: reply draft
# ---------------------------------------------------------------------------

DRAFT_CASES: tuple[DraftCase, ...] = (
    DraftCase(
        case_id="r-f-mixed-depth-deep",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-depth-deep",
        brief_id="brief-mixed",
        note="Dwa claimy zweryfikowane + jeden UNVERIFIED. Marker 'sub-50ms' nie ma prawa "
        "pojawić się w treści (L751, L5307).",
    ),
    DraftCase(
        case_id="r-f-mixed-substantive-high",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-substantive-high",
        brief_id="brief-mixed",
        note="Wątek z tabelą kosztów — draft ma dołożyć własny pomiar, nie przepisać wątek.",
    ),
    DraftCase(
        case_id="r-f-negatyw-brak-draftu",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-neg-locked-high-engagement",
        brief_id="brief-mixed",
        expect_declined=True,
        expected_decline_reason=DeclineReason.NEGATIVE_SIGNAL,
        note="Wątek zablokowany po eskalacji nie dostaje draftu mimo 641 głosów.",
    ),
    DraftCase(
        case_id="r-f-window-closed-brak-draftu",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-window-closed",
        brief_id="brief-mixed",
        expect_declined=True,
        expected_decline_reason=DeclineReason.NEGATIVE_SIGNAL,
        note="Wątek rozwiązany i zablokowany. Pierwszeństwo ma powód negatywny — "
        "kolejność gałęzi jest częścią definicji, nie szczegółem implementacji.",
    ),
    DraftCase(
        case_id="r-f-prohibited-pricing",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-icp-high",
        brief_id="brief-prohibited-pricing",
        note="Tytuł wątku zawiera 'wyceniacie', a temat jest zakazany. Draft ma powstać "
        "bez ani jednego zakazanego słowa — także w cytacie z tytułu.",
    ),
    DraftCase(
        case_id="r-f-brak-uzytecznego-claimu",
        archetype=ARCHETYPE_THREADED,
        spec_key="f-window-rising",
        brief_id="brief-only-unverified",
        expect_declined=True,
        expected_decline_reason=DeclineReason.NO_USABLE_CLAIM,
        note="Świeża awaria u dostawcy, ale wszystkie claimy niezweryfikowane. "
        "Pusty draft jest lepszy niż draft na niepotwierdzonych liczbach.",
    ),
    DraftCase(
        case_id="r-g-mixed-depth-thread",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-depth-thread",
        brief_id="brief-mixed",
        note="Sieć zawodowa, płaskie komentarze, dane po kwartale.",
    ),
    DraftCase(
        case_id="r-g-mixed-substantive-high",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-substantive-high",
        brief_id="brief-mixed",
        note="Koszt rotacji na próbie 120 osób — konkret za konkret.",
    ),
    DraftCase(
        case_id="r-g-negatyw-brak-draftu",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-neg-ratio",
        brief_id="brief-mixed",
        expect_declined=True,
        expected_decline_reason=DeclineReason.NEGATIVE_SIGNAL,
        note="Post z oskarżeniem o bota — wchodzenie w taki wątek pogarsza sytuację.",
    ),
    DraftCase(
        case_id="r-g-window-closed-brak-draftu",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-window-closed",
        brief_id="brief-mixed",
        expect_declined=True,
        expected_decline_reason=DeclineReason.WINDOW_CLOSED,
        note="Nabór zamknięty, brak sygnału negatywnego. Czysty przypadek L4590/L5541: "
        "wątek z window_state=CLOSED nie tworzy nowego draftu.",
    ),
    DraftCase(
        case_id="r-g-prohibited-contract",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-icp-high",
        brief_id="brief-prohibited-contract",
        note="Tytuł zawiera 'kontraktu' i 'sektorze publicznym' — oba zakazane.",
    ),
    DraftCase(
        case_id="r-g-expired-i-retracted",
        archetype=ARCHETYPE_GRAPH,
        spec_key="g-amp-quote",
        brief_id="brief-expired-retracted",
        note="EXPIRED i RETRACTED nie są 'prawie zweryfikowane' — traktujemy je tak samo "
        "jak UNVERIFIED.",
        expect_declined=True,
        expected_decline_reason=DeclineReason.NO_USABLE_CLAIM,
    ),
)


ALL_CASE_IDS: tuple[str, ...] = tuple(
    c.case_id for c in (*QUALITY_CASES, *DISCOVERY_CASES, *SIGNAL_PAIR_CASES, *DRAFT_CASES)
)

CASE_COUNT_BY_FAMILY: dict[EvalFamily, int] = {
    EvalFamily.QUALITY: len(QUALITY_CASES),
    EvalFamily.DISCOVERY: len(DISCOVERY_CASES) + len(SIGNAL_PAIR_CASES),
    EvalFamily.DRAFT: len(DRAFT_CASES),
}

TOTAL_CASES: int = sum(CASE_COUNT_BY_FAMILY.values())


@dataclass(frozen=True)
class Catalog:
    quality: tuple[QualityCase, ...] = field(default=QUALITY_CASES)
    discovery: tuple[DiscoveryCase, ...] = field(default=DISCOVERY_CASES)
    signal_pairs: tuple[SignalPairCase, ...] = field(default=SIGNAL_PAIR_CASES)
    drafts: tuple[DraftCase, ...] = field(default=DRAFT_CASES)


CATALOG = Catalog()


__all__ = [
    "ALL_CASE_IDS",
    "ARCHETYPE_GRAPH",
    "ARCHETYPE_THREADED",
    "BRIEFS",
    "CASE_COUNT_BY_FAMILY",
    "CATALOG",
    "DISCOVERY_CASES",
    "DRAFT_CASES",
    "QUALITY_CASES",
    "SIGNAL_PAIR_CASES",
    "TOTAL_CASES",
    "Catalog",
    "DiscoveryCase",
    "DraftCase",
    "QualityCase",
    "SignalPairCase",
]
