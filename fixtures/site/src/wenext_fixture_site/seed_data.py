"""Catalogue of the fixture's threads — the explicit input to the D17 evals.

Every entry states outright WHICH of the eight dimensions of discussion-quality assessment
(section 22.7, L4436-4446) is to be distinguishable on it and why we put it here. This is
not decoration: the F3 DoD says that the definition of a "valuable discussion" passes the
fixture evals from E1.F0 (L5283) — if the fixture does not encode 22.7, F3 has nothing
to close itself with.

Selection principle: for every dimension there are at least three threads per archetype
that differ ABOVE ALL in that dimension. Thanks to that the ranker can be tested on pairs,
and not on a scalar value (L4468).

What is deliberately NOT here:
- the calibration to the channel background is not computed, only given as
  `calibration_basis` — in F0 there is no surface baseline (register of the infeasible,
  D04 item 4);
- `fake_engagement_flags` are log-only and are not to lower the tier (L4492, L5551), so
  threads with flags are otherwise normal.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# The eight dimensions from the table at L4438-4445
# ---------------------------------------------------------------------------

DIM_THREAD_DEPTH = "THREAD_DEPTH"
DIM_SUBSTANTIVE_REPLIES = "SUBSTANTIVE_REPLIES"
DIM_AUTHOR_PARTICIPATION = "AUTHOR_PARTICIPATION"
DIM_ICP_SHARE = "ICP_SHARE"
DIM_INTENTIONAL_AMPLIFICATION = "INTENTIONAL_AMPLIFICATION"
DIM_REACH_CALIBRATION = "REACH_CALIBRATION"
DIM_NEGATIVE_SIGNALS = "NEGATIVE_SIGNALS"
DIM_LIFE_WINDOW = "LIFE_WINDOW"

ALL_DIMENSIONS: tuple[str, ...] = (
    DIM_THREAD_DEPTH,
    DIM_SUBSTANTIVE_REPLIES,
    DIM_AUTHOR_PARTICIPATION,
    DIM_ICP_SHARE,
    DIM_INTENTIONAL_AMPLIFICATION,
    DIM_REACH_CALIBRATION,
    DIM_NEGATIVE_SIGNALS,
    DIM_LIFE_WINDOW,
)

#: Default calibration basis. Explicit input data, not the result of a computation.
DEFAULT_CALIBRATION = "mediana kanału: format tekstowy, okno 24 h, sieć autora ~1 tys."


@dataclass(frozen=True)
class ThreadSpec:
    """Declaration of a single thread. The generator is deterministic from it."""

    key: str
    community: str
    title: str
    body: str
    illustrates: tuple[str, ...]
    note: str
    depth: int
    reply_count: int
    unique_participants: int
    substantive_ratio: float
    author_replies: int = 0
    #: `None` = the author never spoke up at all.
    author_first_reply_minutes: int | None = None
    #: `None` = the platform does not show participant profiles → the consumer records UNKNOWN.
    icp_share: float | None = 0.25
    votes: int = 0
    window_state: str = "PEAK"
    age_minutes: int = 1440
    replies_first_hour: int = 0
    #: `None` = the reach metric is not visible to this account / on this surface.
    reach_impressions: int | None = 12000
    calibration_basis: str = DEFAULT_CALIBRATION
    #: (kind, source, comment)
    amplifications: tuple[tuple[str, str, str], ...] = ()
    #: (kind, count)
    reactions: tuple[tuple[str, int], ...] = ()
    locked: bool = False
    removed_by_moderation: bool = False
    negative_signals: tuple[str, ...] = ()
    fake_engagement_flags: tuple[str, ...] = ()
    reaction_burst_without_replies: bool = False
    #: Some of the comments get negative votes — needed for the "ratio" signal.
    negative_comment_votes: bool = False


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------

COMMUNITIES: tuple[tuple[str, str, str, int, str], ...] = (
    (
        "infra-praktycy",
        "Infrastruktura w praktyce",
        "Wdrożenia, awarie, koszty. Bez sponsorowanych wpisów.",
        18420,
        "THREADED_COMMUNITY",
    ),
    (
        "python-warsztat",
        "Python warsztat",
        "Kod, profilowanie, migracje wersji. Pytania mile widziane.",
        31877,
        "THREADED_COMMUNITY",
    ),
    (
        "growth-bez-sciemy",
        "Growth bez ściemy",
        "Pozyskiwanie klientów w B2B. Liczby albo nic.",
        9264,
        "THREADED_COMMUNITY",
    ),
    (
        "devrel-kuchnia",
        "DevRel od kuchni",
        "Relacje z deweloperami, dokumentacja, obecność w społecznościach.",
        5731,
        "THREADED_COMMUNITY",
    ),
    (
        "network",
        "Sieć zawodowa",
        "Feed profesjonalny. Płaskie komentarze, reakcje typowane, repost z komentarzem.",
        0,
        "PROFESSIONAL_GRAPH",
    ),
)


# ---------------------------------------------------------------------------
# Archetype 1 — THREADED_COMMUNITY (24 threads)
# ---------------------------------------------------------------------------

THREADED_SPECS: tuple[ThreadSpec, ...] = (
    # --- Thread depth ---------------------------------------------------------
    ThreadSpec(
        key="f-depth-deep",
        community="infra-praktycy",
        title="Rozbicie monolitu bez okna serwisowego — notatki z 14 miesięcy",
        body=(
            "Prowadziliśmy migrację przy ruchu produkcyjnym, bez ani jednego zaplanowanego "
            "przestoju. Poniżej kolejność kroków, trzy rzeczy, które poszły źle, i koszt."
        ),
        illustrates=(DIM_THREAD_DEPTH, DIM_SUBSTANTIVE_REPLIES),
        note="Wzorzec dodatni głębokości: 6 poziomów, 19 rozmówców, autor obecny.",
        depth=6,
        reply_count=42,
        unique_participants=19,
        substantive_ratio=0.62,
        author_replies=6,
        author_first_reply_minutes=12,
        icp_share=0.42,
        votes=318,
        window_state="PEAK",
        age_minutes=1440,
        replies_first_hour=11,
        reach_impressions=38400,
    ),
    ThreadSpec(
        key="f-depth-shallow",
        community="infra-praktycy",
        title="Czy ktoś jeszcze robi backupy cronem?",
        body="Pytanie z ciekawości. U nas nadal cron i skrypt na 40 linii.",
        illustrates=(DIM_THREAD_DEPTH,),
        note="Kontrprzykład do f-depth-deep: dużo odpowiedzi, zero zagnieżdżenia.",
        depth=1,
        reply_count=23,
        unique_participants=22,
        substantive_ratio=0.09,
        author_replies=0,
        icp_share=0.09,
        votes=47,
        window_state="DECAYING",
        age_minutes=2880,
        replies_first_hour=17,
        reach_impressions=9100,
    ),
    ThreadSpec(
        key="f-depth-two-people",
        community="infra-praktycy",
        title="Spór o retencję logów: 7 dni czy 90?",
        body="Dwa obozy w zespole. Szukam argumentów, których jeszcze nie znam.",
        illustrates=(DIM_THREAD_DEPTH, DIM_SUBSTANTIVE_REPLIES),
        note="Głęboko, ale tylko 3 rozmówców — głębokość bez szerokości.",
        depth=5,
        reply_count=28,
        unique_participants=3,
        substantive_ratio=0.75,
        author_replies=9,
        author_first_reply_minutes=6,
        icp_share=0.66,
        votes=91,
        window_state="PEAK",
        age_minutes=1080,
        replies_first_hour=8,
        reach_impressions=7300,
    ),
    # --- Substantive replies --------------------------------------------------
    ThreadSpec(
        key="f-substantive-high",
        community="infra-praktycy",
        title="Policzyłem koszt egressu dla trzech dostawców — tabela w komentarzu",
        body=(
            "12 TB miesięcznie, ten sam profil ruchu, trzy cenniki. Metodologia i arkusz "
            "w pierwszym komentarzu — proszę o strzały w metodologię."
        ),
        illustrates=(DIM_SUBSTANTIVE_REPLIES,),
        note="Wysoki udział odpowiedzi z konkretem: liczby, źródła, własne pomiary.",
        depth=3,
        reply_count=31,
        unique_participants=17,
        substantive_ratio=0.84,
        author_replies=5,
        author_first_reply_minutes=9,
        icp_share=0.53,
        votes=264,
        window_state="PEAK",
        age_minutes=960,
        replies_first_hour=9,
        reach_impressions=21500,
    ),
    ThreadSpec(
        key="f-substantive-low",
        community="python-warsztat",
        title="Wyszła nowa wersja frameworka",
        body="Link do changeloga. Ktoś już testował?",
        illustrates=(DIM_SUBSTANTIVE_REPLIES,),
        note="Kontrprzykład: 64 odpowiedzi, prawie wyłącznie „+1” i „u mnie działa”.",
        depth=2,
        reply_count=64,
        unique_participants=51,
        substantive_ratio=0.06,
        author_replies=1,
        author_first_reply_minutes=180,
        icp_share=0.18,
        votes=402,
        window_state="DECAYING",
        age_minutes=4320,
        replies_first_hour=29,
        reach_impressions=54000,
    ),
    ThreadSpec(
        key="f-substantive-counter",
        community="python-warsztat",
        title="Dlaczego zdjęliśmy feature flagi z krytycznej ścieżki",
        body=(
            "Po trzech incydentach policzyliśmy, ile kosztuje nas gałąź, której nikt nie testuje. "
            "Wynik był dla nas niewygodny."
        ),
        illustrates=(DIM_SUBSTANTIVE_REPLIES, DIM_THREAD_DEPTH),
        note="Merytoryka przez kontrargument, nie zgodę — inny kształt niż f-substantive-high.",
        depth=4,
        reply_count=26,
        unique_participants=14,
        substantive_ratio=0.58,
        author_replies=4,
        author_first_reply_minutes=21,
        icp_share=0.47,
        votes=139,
        window_state="PEAK",
        age_minutes=1320,
        replies_first_hour=6,
        reach_impressions=16800,
    ),
    # --- Author participation -------------------------------------------------
    ThreadSpec(
        key="f-author-active",
        community="python-warsztat",
        title="Autor biblioteki odpowiada na zarzuty o wydajność",
        body=(
            "Widziałem wczorajszy benchmark. Poniżej co w nim jest nie tak i co faktycznie "
            "poprawiliśmy w 3.2. Pytajcie."
        ),
        illustrates=(DIM_AUTHOR_PARTICIPATION,),
        note="Autor odpowiada szybko, wielokrotnie i również krytyce.",
        depth=4,
        reply_count=33,
        unique_participants=15,
        substantive_ratio=0.55,
        author_replies=11,
        author_first_reply_minutes=4,
        icp_share=0.51,
        votes=228,
        window_state="PEAK",
        age_minutes=720,
        replies_first_hour=13,
        reach_impressions=26700,
    ),
    ThreadSpec(
        key="f-author-absent",
        community="python-warsztat",
        title="Benchmark trzech serializatorów (wrzucam i uciekam)",
        body="Surowe wyniki, bez komentarza. Interpretujcie sami.",
        illustrates=(DIM_AUTHOR_PARTICIPATION,),
        note="Zero udziału autora mimo 38 odpowiedzi — wątek porzucony przez autora.",
        depth=3,
        reply_count=38,
        unique_participants=20,
        substantive_ratio=0.33,
        author_replies=0,
        icp_share=0.29,
        votes=176,
        window_state="DECAYING",
        age_minutes=3600,
        replies_first_hour=12,
        reach_impressions=19400,
    ),
    ThreadSpec(
        key="f-author-late",
        community="devrel-kuchnia",
        title="Wróciłem po dwóch dniach i odpowiadam na wszystko",
        body="Przepraszam za ciszę, byłem na konferencji. Odpowiadam po kolei od góry.",
        illustrates=(DIM_AUTHOR_PARTICIPATION, DIM_LIFE_WINDOW),
        note="Autor obecny z opóźnieniem 46 h — czas pierwszej odpowiedzi ma być rozróżnialny.",
        depth=3,
        reply_count=24,
        unique_participants=13,
        substantive_ratio=0.41,
        author_replies=7,
        author_first_reply_minutes=2760,
        icp_share=0.34,
        votes=83,
        window_state="DECAYING",
        age_minutes=5040,
        replies_first_hour=5,
        reach_impressions=11200,
    ),
    # --- ICP share ------------------------------------------------------------
    ThreadSpec(
        key="f-icp-high",
        community="growth-bez-sciemy",
        title="Jak wyceniacie wdrożenia dla klientów enterprise?",
        body=(
            "Interesuje mnie widełka za pilota i to, czy bierzecie za discovery. "
            "Nasz obecny model przestał się spinać przy trzecim kliencie."
        ),
        illustrates=(DIM_ICP_SHARE,),
        note="Wysoki udział rozmówców z ICP przy niewielkiej liczbie odpowiedzi.",
        depth=3,
        reply_count=19,
        unique_participants=12,
        substantive_ratio=0.68,
        author_replies=4,
        author_first_reply_minutes=15,
        icp_share=0.71,
        votes=64,
        window_state="RISING",
        age_minutes=300,
        replies_first_hour=7,
        reach_impressions=4300,
    ),
    ThreadSpec(
        key="f-icp-low",
        community="growth-bez-sciemy",
        title="Piątkowy wątek offtopowy",
        body="Wrzucajcie, co u was. Bez tematu.",
        illustrates=(DIM_ICP_SHARE, DIM_SUBSTANTIVE_REPLIES),
        note="74 odpowiedzi, prawie zero ICP — dowód, że wolumen nie zastępuje dopasowania.",
        depth=2,
        reply_count=74,
        unique_participants=60,
        substantive_ratio=0.03,
        author_replies=2,
        author_first_reply_minutes=95,
        icp_share=0.04,
        votes=512,
        window_state="DECAYING",
        age_minutes=2160,
        replies_first_hour=31,
        reach_impressions=67000,
    ),
    ThreadSpec(
        key="f-icp-hidden",
        community="growth-bez-sciemy",
        title="Wątek w społeczności zamkniętej — profile rozmówców ukryte",
        body=(
            "Regulamin tej społeczności wyłącza podgląd profili. Piszę, bo temat i tak "
            "jest istotny: kto płaci za pilota, klient czy my?"
        ),
        illustrates=(DIM_ICP_SHARE, DIM_REACH_CALIBRATION),
        note="icp_share ORAZ zasięg niewidoczne — konsument MUSI zapisać UNKNOWN, nie 0.",
        depth=4,
        reply_count=21,
        unique_participants=11,
        substantive_ratio=0.62,
        author_replies=3,
        author_first_reply_minutes=33,
        icp_share=None,
        votes=57,
        window_state="PEAK",
        age_minutes=1500,
        replies_first_hour=4,
        reach_impressions=None,
        calibration_basis="brak podstawy: społeczność zamknięta nie publikuje mediany kanału",
    ),
    # --- Intentional amplification --------------------------------------------
    ThreadSpec(
        key="f-amp-newsletter",
        community="infra-praktycy",
        title="Lista kontrolna przed migracją bazy — wersja po pięciu migracjach",
        body="21 punktów, z czego 6 dopisanych po awariach. Korzystajcie i poprawiajcie.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION,),
        note="Amplifikacja przez newsletter i cytat — ktoś poniósł koszt podania dalej.",
        depth=3,
        reply_count=27,
        unique_participants=16,
        substantive_ratio=0.59,
        author_replies=5,
        author_first_reply_minutes=18,
        icp_share=0.48,
        votes=291,
        window_state="PEAK",
        age_minutes=2040,
        replies_first_hour=8,
        reach_impressions=33100,
        amplifications=(
            (
                "NEWSLETTER_LINK",
                "Tygodnik Operacyjny #184",
                "Najlepsza lista kontrolna, jaką w tym roku widzieliśmy.",
            ),
            (
                "QUOTE",
                "wątek: „Post mortem: 6 h niedostępności”",
                "Punkt 14 z tej listy uratowałby nam ten weekend.",
            ),
        ),
    ),
    ThreadSpec(
        key="f-amp-crosspost",
        community="devrel-kuchnia",
        title="Jak mierzymy, czy dokumentacja faktycznie skraca czas wdrożenia",
        body="Trzy metryki, dwie z nich okazały się bezużyteczne. Zostawiliśmy jedną.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION, DIM_SUBSTANTIVE_REPLIES),
        note="Crosspost z komentarzem + link przychodzący z innego wątku.",
        depth=4,
        reply_count=23,
        unique_participants=14,
        substantive_ratio=0.65,
        author_replies=6,
        author_first_reply_minutes=11,
        icp_share=0.55,
        votes=147,
        window_state="PEAK",
        age_minutes=1260,
        replies_first_hour=6,
        reach_impressions=15600,
        amplifications=(
            (
                "CROSSPOST",
                "społeczność: Infrastruktura w praktyce",
                "Przenoszę tu, bo u nas ten sam problem wraca co kwartał.",
            ),
            (
                "INBOUND_THREAD_LINK",
                "wątek: „Onboarding juniorów w 2 tygodnie”",
                "Metryka nr 3 stąd jest jedyną, która nam się broni.",
            ),
        ),
    ),
    ThreadSpec(
        key="f-amp-none-high-votes",
        community="devrel-kuchnia",
        title="1200 głosów i ani jednego podania dalej",
        body="Zrzut ekranu z panelu. Bez komentarza.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION,),
        note="Wysokie liczniki, zero amplifikacji z intencją — kontrprzykład do f-amp-*.",
        depth=2,
        reply_count=9,
        unique_participants=8,
        substantive_ratio=0.11,
        author_replies=0,
        icp_share=0.12,
        votes=1204,
        window_state="DECAYING",
        age_minutes=2700,
        replies_first_hour=6,
        reach_impressions=96000,
    ),
    # --- Calibration to channel background ------------------------------------
    ThreadSpec(
        key="f-calib-above",
        community="python-warsztat",
        title="Profilowanie importów skróciło start procesu z 4,1 s do 0,6 s",
        body="Konkretne pomiary, konkretny commit. Ktoś to powtórzy u siebie?",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Wynik ok. 3 razy powyżej mediany kanału — kalibracja jako dana wejściowa.",
        depth=3,
        reply_count=29,
        unique_participants=18,
        substantive_ratio=0.61,
        author_replies=5,
        author_first_reply_minutes=13,
        icp_share=0.44,
        votes=344,
        window_state="PEAK",
        age_minutes=1140,
        replies_first_hour=10,
        reach_impressions=48900,
        calibration_basis="mediana kanału dla formatu tekstowego, 24 h: 16 200 wyświetleń",
    ),
    ThreadSpec(
        key="f-calib-below",
        community="python-warsztat",
        title="Krótkie pytanie o typowanie generyków",
        body="Czy da się to zapisać bez `cast`? Przykład w komentarzu.",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Ta sama liczba reakcji co f-calib-above, ale poniżej mediany kanału.",
        depth=2,
        reply_count=12,
        unique_participants=9,
        substantive_ratio=0.42,
        author_replies=2,
        author_first_reply_minutes=27,
        icp_share=0.31,
        votes=38,
        window_state="DECAYING",
        age_minutes=2520,
        replies_first_hour=4,
        reach_impressions=6100,
        calibration_basis="mediana kanału dla formatu tekstowego, 24 h: 16 200 wyświetleń",
    ),
    ThreadSpec(
        key="f-calib-hidden",
        community="devrel-kuchnia",
        title="Ta społeczność nie pokazuje statystyk wątków",
        body="Pytanie do moderacji: czy da się włączyć podgląd wyświetleń dla autorów?",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Brak widocznej metryki zasięgu przy poprawnej reszcie — wymusza UNKNOWN.",
        depth=3,
        reply_count=17,
        unique_participants=11,
        substantive_ratio=0.35,
        author_replies=3,
        author_first_reply_minutes=44,
        icp_share=0.27,
        votes=52,
        window_state="PEAK",
        age_minutes=1680,
        replies_first_hour=3,
        reach_impressions=None,
        calibration_basis="brak podstawy: surface nie eksponuje wyświetleń",
    ),
    # --- Negative signals -----------------------------------------------------
    ThreadSpec(
        key="f-neg-locked-high-engagement",
        community="infra-praktycy",
        title="Wątek zablokowany po eskalacji — dyskusja o licencjach",
        body=(
            "Zamykam komentarze. Wątek zszedł na personalne wycieczki, wracamy do tematu "
            "w osobnym poście z jasnymi zasadami."
        ),
        illustrates=(DIM_NEGATIVE_SIGNALS,),
        note=(
            "Najważniejszy przypadek graniczny: bardzo wysoki engagement ORAZ lock. "
            "Żadna liczba sygnałów pozytywnych nie ma tego skompensować (L4472, L5550)."
        ),
        depth=5,
        reply_count=55,
        unique_participants=24,
        substantive_ratio=0.47,
        author_replies=8,
        author_first_reply_minutes=9,
        icp_share=0.49,
        votes=641,
        window_state="CLOSED",
        age_minutes=1980,
        replies_first_hour=19,
        reach_impressions=88200,
        locked=True,
        negative_signals=("LOCK",),
        amplifications=(
            (
                "QUOTE",
                "wątek: „Zmiana licencji — co dalej”",
                "Tu jest cały kontekst sporu.",
            ),
        ),
    ),
    ThreadSpec(
        key="f-neg-removed",
        community="growth-bez-sciemy",
        title="[usunięte przez moderację]",
        body="Treść usunięta przez moderację społeczności.",
        illustrates=(DIM_NEGATIVE_SIGNALS,),
        note="Usunięcie moderacyjne: treść znika, struktura wątku zostaje widoczna.",
        depth=3,
        reply_count=18,
        unique_participants=12,
        substantive_ratio=0.22,
        author_replies=0,
        icp_share=0.21,
        votes=-12,
        window_state="CLOSED",
        age_minutes=2340,
        replies_first_hour=9,
        reach_impressions=14100,
        removed_by_moderation=True,
        locked=True,
        negative_signals=("NEGATIVE_MODERATION", "LOCK"),
        negative_comment_votes=True,
    ),
    ThreadSpec(
        key="f-neg-ratio",
        community="growth-bez-sciemy",
        title="Nasz nowy produkt rozwiązuje problem, o którym nie wiedzieliście",
        body="Wchodzimy z narzędziem, które zmienia sposób, w jaki myślicie o lejku.",
        illustrates=(DIM_NEGATIVE_SIGNALS, DIM_SUBSTANTIVE_REPLIES),
        note="Ratio: głosy poniżej zera, oskarżenia o spam i bota, dużo odpowiedzi negatywnych.",
        depth=3,
        reply_count=47,
        unique_participants=29,
        substantive_ratio=0.19,
        author_replies=3,
        author_first_reply_minutes=52,
        icp_share=0.23,
        votes=-84,
        window_state="DECAYING",
        age_minutes=1560,
        replies_first_hour=22,
        reach_impressions=27800,
        negative_signals=("DOWNVOTE_BELOW_ZERO", "SPAM_ACCUSATION"),
        negative_comment_votes=True,
    ),
    # --- Life window ----------------------------------------------------------
    ThreadSpec(
        key="f-window-rising",
        community="infra-praktycy",
        title="Awaria u dostawcy DNS — kto widzi to samo?",
        body="Od 9:40 rozjazd w rozwiązywaniu nazw w dwóch regionach. Zbieram potwierdzenia.",
        illustrates=(DIM_LIFE_WINDOW, DIM_THREAD_DEPTH),
        note="RISING: świeży wątek z gwałtownym przyrostem w pierwszej godzinie.",
        depth=3,
        reply_count=14,
        unique_participants=11,
        substantive_ratio=0.57,
        author_replies=4,
        author_first_reply_minutes=3,
        icp_share=0.46,
        votes=73,
        window_state="RISING",
        age_minutes=55,
        replies_first_hour=14,
        reach_impressions=5200,
    ),
    ThreadSpec(
        key="f-window-closed",
        community="python-warsztat",
        title="[rozwiązane] Wyciek pamięci przy asynchronicznym kliencie HTTP",
        body="Problem rozwiązany, przyczyna w komentarzu oznaczonym jako odpowiedź. Zamykam.",
        illustrates=(DIM_LIFE_WINDOW,),
        note="CLOSED bez sygnału negatywnego: wejście nie ma sensu, ale wątek nie jest zły.",
        depth=4,
        reply_count=21,
        unique_participants=13,
        substantive_ratio=0.71,
        author_replies=6,
        author_first_reply_minutes=17,
        icp_share=0.38,
        votes=186,
        window_state="CLOSED",
        age_minutes=20160,
        replies_first_hour=7,
        reach_impressions=23400,
        locked=True,
        negative_signals=("LOCK",),
    ),
    ThreadSpec(
        key="f-window-fake-burst",
        community="devrel-kuchnia",
        title="Krótka myśl o obecności w społecznościach",
        body="Obecność buduje się latami, nie kwartałami.",
        illustrates=(DIM_LIFE_WINDOW, DIM_INTENTIONAL_AMPLIFICATION),
        note=(
            "Burst reakcji bez przyrostu odpowiedzi. Flagi fake engagementu są LOG-ONLY: "
            "mają nie obniżać tieru i nie odrzucać wątku (L4492, L5551)."
        ),
        depth=1,
        reply_count=3,
        unique_participants=3,
        substantive_ratio=0.0,
        author_replies=0,
        icp_share=0.02,
        votes=894,
        window_state="PEAK",
        age_minutes=420,
        replies_first_hour=2,
        reach_impressions=71000,
        reaction_burst_without_replies=True,
        fake_engagement_flags=("REACTION_BURST_WITHOUT_REPLIES", "UNIFORM_REACTION_TIMING"),
    ),
)


# ---------------------------------------------------------------------------
# Archetype 2 — PROFESSIONAL_GRAPH (24 posts)
# ---------------------------------------------------------------------------
# Comments are flat (at most 2 levels: comment + author's reply), reactions are typed,
# and amplification takes the form of a repost with a comment. The time regime is
# shorter by an order of magnitude than on the forum — hence the different `age_minutes`.

GRAPH_SPECS: tuple[ThreadSpec, ...] = (
    # --- Depth ----------------------------------------------------------------
    ThreadSpec(
        key="g-depth-thread",
        community="network",
        title="Rozbiliśmy onboarding na 5 kroków. Dane po kwartale.",
        body=(
            "Czas do pierwszego samodzielnego wdrożenia spadł z 19 do 6 dni. "
            "Co zadziałało, a co było kosztem utopionym — w komentarzach."
        ),
        illustrates=(DIM_THREAD_DEPTH, DIM_SUBSTANTIVE_REPLIES),
        note="Maksymalna głębokość osiągalna w tym archetypie (2 poziomy) przy 21 rozmówcach.",
        depth=2,
        reply_count=34,
        unique_participants=21,
        substantive_ratio=0.58,
        author_replies=9,
        author_first_reply_minutes=8,
        icp_share=0.44,
        window_state="PEAK",
        age_minutes=480,
        replies_first_hour=14,
        reach_impressions=42800,
        reactions=(("INSIGHTFUL", 214), ("LIKE", 388), ("SUPPORT", 41)),
    ),
    ThreadSpec(
        key="g-depth-flat",
        community="network",
        title="Zmieniam rolę — od poniedziałku prowadzę zespół platformowy",
        body="Dziękuję wszystkim, z którymi pracowałem przez ostatnie cztery lata.",
        illustrates=(DIM_THREAD_DEPTH,),
        note="Zero zagnieżdżenia, 44 rozmówców, treść gratulacyjna.",
        depth=1,
        reply_count=46,
        unique_participants=44,
        substantive_ratio=0.02,
        author_replies=0,
        icp_share=0.11,
        window_state="DECAYING",
        age_minutes=1200,
        replies_first_hour=27,
        reach_impressions=51900,
        reactions=(("CELEBRATE", 612), ("LIKE", 430)),
    ),
    ThreadSpec(
        key="g-depth-two-people",
        community="network",
        title="Praca hybrydowa: dwa dni w biurze to kompromis, który nie działa",
        body="Twarda teza, chętnie posłucham kontry. Mamy dane z 3 kwartałów.",
        illustrates=(DIM_THREAD_DEPTH, DIM_SUBSTANTIVE_REPLIES),
        note="Wymiana zdań między czterema osobami — głębokość bez szerokości.",
        depth=2,
        reply_count=22,
        unique_participants=4,
        substantive_ratio=0.72,
        author_replies=8,
        author_first_reply_minutes=5,
        icp_share=0.61,
        window_state="PEAK",
        age_minutes=360,
        replies_first_hour=12,
        reach_impressions=18300,
        reactions=(("INSIGHTFUL", 96), ("LIKE", 141)),
    ),
    # --- Substantiveness ------------------------------------------------------
    ThreadSpec(
        key="g-substantive-high",
        community="network",
        title="Policzyliśmy koszt rotacji na próbie 120 osób",
        body=(
            "Wyszło 68 tys. na stanowisko przy 9-miesięcznym stażu. Metodologia i "
            "założenia w pierwszym komentarzu — czekam na strzały."
        ),
        illustrates=(DIM_SUBSTANTIVE_REPLIES,),
        note="Wysoka merytoryczność w archetypie, który zwykle jej nie ma.",
        depth=2,
        reply_count=29,
        unique_participants=19,
        substantive_ratio=0.79,
        author_replies=7,
        author_first_reply_minutes=6,
        icp_share=0.57,
        window_state="PEAK",
        age_minutes=420,
        replies_first_hour=13,
        reach_impressions=36400,
        reactions=(("INSIGHTFUL", 288), ("LIKE", 302)),
    ),
    ThreadSpec(
        key="g-substantive-low",
        community="network",
        title="Miłego tygodnia wszystkim",
        body="Poniedziałek to dobry moment, żeby zacząć od nowa.",
        illustrates=(DIM_SUBSTANTIVE_REPLIES, DIM_ICP_SHARE),
        note="Kontrprzykład: 88 odpowiedzi bez ani jednego konkretu.",
        depth=1,
        reply_count=88,
        unique_participants=80,
        substantive_ratio=0.01,
        author_replies=1,
        author_first_reply_minutes=140,
        icp_share=0.05,
        window_state="DECAYING",
        age_minutes=1560,
        replies_first_hour=48,
        reach_impressions=104000,
        reactions=(("LIKE", 1841), ("CELEBRATE", 226)),
    ),
    ThreadSpec(
        key="g-substantive-counter",
        community="network",
        title="Nie zgadzam się z tezą, że OKR-y nie działają w małych zespołach",
        body="Trzy warunki, przy których działają, i jeden, przy którym nie ma sensu próbować.",
        illustrates=(DIM_SUBSTANTIVE_REPLIES,),
        note="Merytoryka przez kontrargument — do pary z g-substantive-high.",
        depth=2,
        reply_count=26,
        unique_participants=17,
        substantive_ratio=0.63,
        author_replies=6,
        author_first_reply_minutes=11,
        icp_share=0.5,
        window_state="PEAK",
        age_minutes=540,
        replies_first_hour=10,
        reach_impressions=28900,
        reactions=(("INSIGHTFUL", 174), ("LIKE", 205), ("DISAGREE", 33)),
    ),
    # --- Author participation -------------------------------------------------
    ThreadSpec(
        key="g-author-active",
        community="network",
        title="Odpowiadam na pytania o migrację hurtowni — cały dzień w komentarzach",
        body="Zadawajcie pytania, odpowiadam na wszystkie do końca dnia.",
        illustrates=(DIM_AUTHOR_PARTICIPATION,),
        note="Autor odpowiada 12 razy, pierwsza odpowiedź po 4 minutach.",
        depth=2,
        reply_count=31,
        unique_participants=18,
        substantive_ratio=0.6,
        author_replies=12,
        author_first_reply_minutes=4,
        icp_share=0.52,
        window_state="PEAK",
        age_minutes=300,
        replies_first_hour=16,
        reach_impressions=31200,
        reactions=(("INSIGHTFUL", 141), ("LIKE", 233)),
    ),
    ThreadSpec(
        key="g-author-absent",
        community="network",
        title="Raport branżowy 2026 — link w komentarzu",
        body="Udostępniam, bo warto. Sam jeszcze nie przeczytałem w całości.",
        illustrates=(DIM_AUTHOR_PARTICIPATION,),
        note="Zero udziału autora — wątek bez gospodarza.",
        depth=1,
        reply_count=37,
        unique_participants=33,
        substantive_ratio=0.16,
        author_replies=0,
        icp_share=0.24,
        window_state="DECAYING",
        age_minutes=1080,
        replies_first_hour=19,
        reach_impressions=44700,
        reactions=(("LIKE", 501), ("INSIGHTFUL", 62)),
    ),
    ThreadSpec(
        key="g-author-late",
        community="network",
        title="Wracam do komentarzy po 30 godzinach",
        body="Post rozszedł się szerzej, niż zakładałem. Odpowiadam po kolei.",
        illustrates=(DIM_AUTHOR_PARTICIPATION, DIM_LIFE_WINDOW),
        note="Autor obecny, ale poza oknem — w tym archetypie reżim czasowy jest krótszy.",
        depth=2,
        reply_count=25,
        unique_participants=16,
        substantive_ratio=0.38,
        author_replies=3,
        author_first_reply_minutes=1800,
        icp_share=0.33,
        window_state="DECAYING",
        age_minutes=2400,
        replies_first_hour=11,
        reach_impressions=39800,
        reactions=(("LIKE", 377), ("INSIGHTFUL", 84)),
    ),
    # --- ICP ------------------------------------------------------------------
    ThreadSpec(
        key="g-icp-high",
        community="network",
        title="Szukam wzorca kontraktu na wdrożenie pilotażowe w sektorze publicznym",
        body="Konkretnie: kto ponosi koszt integracji w fazie pilota?",
        illustrates=(DIM_ICP_SHARE,),
        note="Mało odpowiedzi, ale prawie wszystkie z ICP.",
        depth=2,
        reply_count=17,
        unique_participants=11,
        substantive_ratio=0.66,
        author_replies=5,
        author_first_reply_minutes=9,
        icp_share=0.68,
        window_state="RISING",
        age_minutes=180,
        replies_first_hour=9,
        reach_impressions=9600,
        reactions=(("INSIGHTFUL", 47), ("LIKE", 58)),
    ),
    ThreadSpec(
        key="g-icp-low",
        community="network",
        title="Trzy cytaty, które zmieniły moje podejście do pracy",
        body="Zapisałem je cztery lata temu i wracam do nich co miesiąc.",
        illustrates=(DIM_ICP_SHARE,),
        note="Ogromny zasięg, rozmówcy spoza ICP.",
        depth=1,
        reply_count=63,
        unique_participants=58,
        substantive_ratio=0.02,
        author_replies=2,
        author_first_reply_minutes=75,
        icp_share=0.03,
        window_state="DECAYING",
        age_minutes=1320,
        replies_first_hour=35,
        reach_impressions=128000,
        reactions=(("LIKE", 2410), ("CELEBRATE", 318)),
    ),
    ThreadSpec(
        key="g-icp-hidden",
        community="network",
        title="Post widoczny tylko dla połączeń pierwszego stopnia",
        body="Ograniczam widoczność, bo temat dotyczy trwającego postępowania.",
        illustrates=(DIM_ICP_SHARE, DIM_REACH_CALIBRATION),
        note="Profil rozmówców i zasięg niewidoczne dla tego konta — dwa razy UNKNOWN.",
        depth=2,
        reply_count=19,
        unique_participants=12,
        substantive_ratio=0.55,
        author_replies=4,
        author_first_reply_minutes=22,
        icp_share=None,
        window_state="PEAK",
        age_minutes=600,
        replies_first_hour=7,
        reach_impressions=None,
        calibration_basis="brak podstawy: widoczność ograniczona, surface nie podaje mediany",
        reactions=(("LIKE", 74), ("INSIGHTFUL", 21)),
    ),
    # --- Amplification --------------------------------------------------------
    ThreadSpec(
        key="g-amp-repost",
        community="network",
        title="Dlaczego przestaliśmy mierzyć produktywność liczbą wdrożeń",
        body="Zamieniliśmy jedną metrykę na trzy. Druga z nich okazała się jedyną potrzebną.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION,),
        note="Dwa reposty z własnym komentarzem — koszt podania dalej poniesiony.",
        depth=2,
        reply_count=24,
        unique_participants=15,
        substantive_ratio=0.54,
        author_replies=6,
        author_first_reply_minutes=13,
        icp_share=0.47,
        window_state="PEAK",
        age_minutes=660,
        replies_first_hour=9,
        reach_impressions=47300,
        reactions=(("INSIGHTFUL", 196), ("LIKE", 271)),
        amplifications=(
            (
                "REPOST_WITH_COMMENT",
                "profil: dyrektor inżynierii, 12 tys. obserwujących",
                "Trzeci akapit opisuje dokładnie nasz zeszły rok.",
            ),
            (
                "REPOST_WITH_COMMENT",
                "profil: liderka zespołu platformowego",
                "Zabieram to na jutrzejszy przegląd kwartalny.",
            ),
        ),
    ),
    ThreadSpec(
        key="g-amp-quote",
        community="network",
        title="Trzy pytania, które zadaję na każdej rozmowie z kandydatem na SRE",
        body="Żadne z nich nie dotyczy algorytmów.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION, DIM_SUBSTANTIVE_REPLIES),
        note="Cytat w newsletterze + link przychodzący z innego postu.",
        depth=2,
        reply_count=28,
        unique_participants=20,
        substantive_ratio=0.48,
        author_replies=5,
        author_first_reply_minutes=16,
        icp_share=0.41,
        window_state="PEAK",
        age_minutes=720,
        replies_first_hour=11,
        reach_impressions=52600,
        reactions=(("INSIGHTFUL", 233), ("LIKE", 349)),
        amplifications=(
            (
                "NEWSLETTER_LINK",
                "Przegląd Rekrutacyjny #57",
                "Pytanie drugie warto ukraść.",
            ),
            (
                "INBOUND_THREAD_LINK",
                "post: „Jak zbudowaliśmy dyżury bez wypalenia”",
                "Kontekst do pytania o dyżury jest tutaj.",
            ),
        ),
    ),
    ThreadSpec(
        key="g-amp-none-high-reactions",
        community="network",
        title="Zdjęcie z konferencji — dziękuję za świetne rozmowy",
        body="Trzy dni, czterdzieści rozmów, zero slajdów.",
        illustrates=(DIM_INTENTIONAL_AMPLIFICATION,),
        note="2,3 tys. reakcji i zero podań dalej z komentarzem.",
        depth=1,
        reply_count=11,
        unique_participants=11,
        substantive_ratio=0.0,
        author_replies=0,
        icp_share=0.16,
        window_state="DECAYING",
        age_minutes=1440,
        replies_first_hour=8,
        reach_impressions=98400,
        reactions=(("LIKE", 2311), ("CELEBRATE", 402)),
    ),
    # --- Calibration ----------------------------------------------------------
    ThreadSpec(
        key="g-calib-above",
        community="network",
        title="Wykres: koszt utrzymania na jednego użytkownika, 8 kwartałów",
        body="Spadek o 41% bez zwalniania nikogo. Oś czasu i założenia pod postem.",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Zasięg ok. 4 razy powyżej mediany kanału dla formatu z grafiką.",
        depth=2,
        reply_count=27,
        unique_participants=18,
        substantive_ratio=0.52,
        author_replies=6,
        author_first_reply_minutes=10,
        icp_share=0.45,
        window_state="PEAK",
        age_minutes=540,
        replies_first_hour=12,
        reach_impressions=88700,
        calibration_basis="mediana kanału dla formatu z grafiką, 24 h: 21 400 wyświetleń",
        reactions=(("INSIGHTFUL", 341), ("LIKE", 402)),
    ),
    ThreadSpec(
        key="g-calib-below",
        community="network",
        title="Krótka refleksja po przeglądzie kwartalnym",
        body="Najtrudniejsze pytanie padło na końcu i nie miałem na nie odpowiedzi.",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Podobna liczba reakcji jak g-calib-above, ale poniżej mediany kanału.",
        depth=1,
        reply_count=13,
        unique_participants=12,
        substantive_ratio=0.15,
        author_replies=2,
        author_first_reply_minutes=48,
        icp_share=0.28,
        window_state="DECAYING",
        age_minutes=1800,
        replies_first_hour=6,
        reach_impressions=7300,
        calibration_basis="mediana kanału dla formatu tekstowego, 24 h: 21 400 wyświetleń",
        reactions=(("LIKE", 118), ("INSIGHTFUL", 24)),
    ),
    ThreadSpec(
        key="g-calib-hidden",
        community="network",
        title="Statystyki postów są dostępne tylko dla autora",
        body="Pytanie do platformy: czy da się udostępnić wyświetlenia współautorom?",
        illustrates=(DIM_REACH_CALIBRATION,),
        note="Metryka zasięgu niewidoczna dla konta czytającego (NOT_VISIBLE_TO_THIS_ACCOUNT).",
        depth=2,
        reply_count=15,
        unique_participants=10,
        substantive_ratio=0.33,
        author_replies=3,
        author_first_reply_minutes=35,
        icp_share=0.26,
        window_state="PEAK",
        age_minutes=780,
        replies_first_hour=5,
        reach_impressions=None,
        calibration_basis="brak podstawy: wyświetlenia widoczne wyłącznie dla autora posta",
        reactions=(("LIKE", 91),),
    ),
    # --- Negative signals -----------------------------------------------------
    ThreadSpec(
        key="g-neg-removed",
        community="network",
        title="[usunięte przez moderację]",
        body="Treść usunięta przez moderację platformy.",
        illustrates=(DIM_NEGATIVE_SIGNALS,),
        note="Usunięcie moderacyjne w archetypie profesjonalnym.",
        depth=1,
        reply_count=14,
        unique_participants=11,
        substantive_ratio=0.14,
        author_replies=0,
        icp_share=0.19,
        window_state="CLOSED",
        age_minutes=960,
        replies_first_hour=8,
        reach_impressions=23100,
        removed_by_moderation=True,
        locked=True,
        negative_signals=("NEGATIVE_MODERATION", "LOCK"),
        reactions=(("LIKE", 46),),
        negative_comment_votes=True,
    ),
    ThreadSpec(
        key="g-neg-locked-high-engagement",
        community="network",
        title="Komentarze wyłączone: wątek o zwolnieniach grupowych",
        body=(
            "Wyłączam komentarze. Dyskusja przeszła w ataki personalne wobec osób, "
            "które nie mają jak odpowiedzieć."
        ),
        illustrates=(DIM_NEGATIVE_SIGNALS,),
        note=(
            "Bardzo wysoki engagement ORAZ lock. Test: żadna liczba pozytywów nie "
            "kompensuje sygnału negatywnego (L4472, L5550)."
        ),
        depth=2,
        reply_count=61,
        unique_participants=38,
        substantive_ratio=0.35,
        author_replies=4,
        author_first_reply_minutes=14,
        icp_share=0.4,
        window_state="CLOSED",
        age_minutes=840,
        replies_first_hour=33,
        reach_impressions=214000,
        locked=True,
        negative_signals=("LOCK",),
        reactions=(("LIKE", 1904), ("SUPPORT", 655), ("INSIGHTFUL", 212)),
        amplifications=(
            (
                "REPOST_WITH_COMMENT",
                "profil: analityk rynku pracy",
                "Warto przeczytać, zanim komentarze zniknęły.",
            ),
        ),
    ),
    ThreadSpec(
        key="g-neg-ratio",
        community="network",
        title="Nasza platforma zastąpi wam cały dział marketingu",
        body="Zapisy do wczesnego dostępu w komentarzu. Pierwsze 100 osób bez opłaty.",
        illustrates=(DIM_NEGATIVE_SIGNALS, DIM_SUBSTANTIVE_REPLIES),
        note="Ratio: 240 odpowiedzi na 12 reakcji, oskarżenia o bota i spam.",
        depth=2,
        reply_count=52,
        unique_participants=41,
        substantive_ratio=0.12,
        author_replies=2,
        author_first_reply_minutes=64,
        icp_share=0.17,
        window_state="DECAYING",
        age_minutes=1020,
        replies_first_hour=26,
        reach_impressions=33800,
        negative_signals=("RATIO", "BOT_ACCUSATION", "SPAM_ACCUSATION"),
        reactions=(("LIKE", 12), ("DISAGREE", 174)),
        negative_comment_votes=True,
    ),
    # --- Life window ----------------------------------------------------------
    ThreadSpec(
        key="g-window-rising",
        community="network",
        title="Publikujemy wyniki testu A/B, który obalił naszą własną hipotezę",
        body="Zakładaliśmy +12%, wyszło -3%. Surowe dane pod postem.",
        illustrates=(DIM_LIFE_WINDOW,),
        note="RISING w krótkim reżimie czasowym tego archetypu (draft_ttl 30 min).",
        depth=2,
        reply_count=12,
        unique_participants=10,
        substantive_ratio=0.66,
        author_replies=4,
        author_first_reply_minutes=2,
        icp_share=0.49,
        window_state="RISING",
        age_minutes=38,
        replies_first_hour=12,
        reach_impressions=6800,
        reactions=(("INSIGHTFUL", 58), ("LIKE", 63)),
    ),
    ThreadSpec(
        key="g-window-closed",
        community="network",
        title="Nabór zamknięty — dziękujemy za 400 zgłoszeń",
        body="Post zostaje dla porządku. Nie przyjmujemy już zgłoszeń.",
        illustrates=(DIM_LIFE_WINDOW,),
        note="CLOSED bez negatywu: kontekst wygasł, wejście nie ma sensu.",
        depth=1,
        reply_count=18,
        unique_participants=17,
        substantive_ratio=0.06,
        author_replies=1,
        author_first_reply_minutes=210,
        icp_share=0.22,
        window_state="CLOSED",
        age_minutes=14400,
        replies_first_hour=9,
        reach_impressions=61200,
        reactions=(("LIKE", 344), ("CELEBRATE", 121)),
    ),
    ThreadSpec(
        key="g-window-fake-burst",
        community="network",
        title="Jedno zdanie o przywództwie",
        body="Lider bierze na siebie winę i oddaje zasługi.",
        illustrates=(DIM_LIFE_WINDOW, DIM_INTENTIONAL_AMPLIFICATION),
        note=(
            "Burst reakcji bez przyrostu odpowiedzi. Flagi są log-only i nie mogą "
            "obniżyć tieru ani odrzucić wątku (L4492, L5551)."
        ),
        depth=1,
        reply_count=4,
        unique_participants=4,
        substantive_ratio=0.0,
        author_replies=0,
        icp_share=0.03,
        window_state="PEAK",
        age_minutes=210,
        replies_first_hour=3,
        reach_impressions=137000,
        reaction_burst_without_replies=True,
        fake_engagement_flags=("REACTION_BURST_WITHOUT_REPLIES", "UNIFORM_REACTION_TIMING"),
        reactions=(("LIKE", 3126), ("CELEBRATE", 41)),
    ),
)


ALL_SPECS: tuple[tuple[str, ThreadSpec], ...] = tuple(
    [("THREADED_COMMUNITY", s) for s in THREADED_SPECS]
    + [("PROFESSIONAL_GRAPH", s) for s in GRAPH_SPECS]
)


def coverage() -> dict[str, dict[str, list[str]]]:
    """Map dimension → archetype → thread keys. Input to the evals and the coverage test."""

    result: dict[str, dict[str, list[str]]] = {
        dim: {"THREADED_COMMUNITY": [], "PROFESSIONAL_GRAPH": []} for dim in ALL_DIMENSIONS
    }
    for archetype, spec in ALL_SPECS:
        for dim in spec.illustrates:
            result[dim][archetype].append(spec.key)
    return result


def validate() -> None:
    """Catalogue consistency. Called at application start — a seed error is to be loud."""

    keys = [spec.key for _, spec in ALL_SPECS]
    if len(keys) != len(set(keys)):
        raise ValueError("klucze wątków w seedzie nie są unikalne")

    known_communities = {slug for slug, *_ in COMMUNITIES}
    for archetype, spec in ALL_SPECS:
        if spec.community not in known_communities:
            raise ValueError(f"{spec.key}: nieznana społeczność {spec.community!r}")
        for dim in spec.illustrates:
            if dim not in ALL_DIMENSIONS:
                raise ValueError(f"{spec.key}: nieznany wymiar {dim!r}")
        if spec.locked and "LOCK" not in spec.negative_signals:
            raise ValueError(f"{spec.key}: lock bez sygnału LOCK")
        if spec.removed_by_moderation and "NEGATIVE_MODERATION" not in spec.negative_signals:
            raise ValueError(f"{spec.key}: usunięcie bez sygnału NEGATIVE_MODERATION")
        if spec.replies_first_hour > spec.reply_count:
            raise ValueError(f"{spec.key}: przyrost w pierwszej godzinie > liczby odpowiedzi")
        if spec.unique_participants > spec.reply_count + 1:
            raise ValueError(f"{spec.key}: więcej rozmówców niż odpowiedzi")
        if archetype == "PROFESSIONAL_GRAPH" and spec.depth > 2:
            raise ValueError(f"{spec.key}: archetyp PROFESSIONAL_GRAPH ma płaskie komentarze")
        if spec.author_replies and spec.author_first_reply_minutes is None:
            raise ValueError(f"{spec.key}: autor odpowiada, ale brak czasu pierwszej odpowiedzi")

    for dim, per_archetype in coverage().items():
        for archetype, thread_keys in per_archetype.items():
            if len(thread_keys) < 3:
                raise ValueError(
                    f"wymiar {dim} ma tylko {len(thread_keys)} wątków w {archetype}; "
                    "ranker nie da się testować na parach"
                )
