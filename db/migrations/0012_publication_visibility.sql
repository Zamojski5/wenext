-- =============================================================================
-- Migracja 0012: widoczność publiczna publikacji (P0 ROADMAP-PIVOT, incydent STAN-PRAC §8.22)
--
-- 17.09 o 21:03 i 21:06 AutoModerator zdjął oba komentarze w nowych społecznościach, a odczyt
-- kontrolny wniosku r/ecommerce o 21:14, czyli JUŻ PO usunięciu, zwrócił `still_present = true`.
-- Przyczyna jest strukturalna, nie przypadkowa: czytamy zalogowani jako autor, a Reddit pokazuje
-- autorowi jego własne usunięte komentarze. Czyli `PUBLISHED` z dowodem ACTUATOR i obserwacją
-- `still_present = true` znaczy „wysłane i widoczne dla nas”, a nie „widoczne dla społeczności”.
--
-- Ta migracja rozdziela te dwie rzeczy w danych, bo w jednej kolumnie `still_present` rozdzielić
-- się ich nie da:
--
--   1. `publication_observations.viewer`, czyli KTO patrzył. `AUTHOR` to dotychczasowy odczyt
--      sesją konta (wszystkie istniejące wiersze, stąd DEFAULT), `ANONYMOUS` to odczyt bez
--      sesji, czyli jedyny, który odpowiada na pytanie „czy widzi to ktoś inny niż my”.
--   2. `publication_requests.public_visibility`, czyli WNIOSEK z tych odczytów, trzy wartości:
--      `CONFIRMED` (odczyt anonimowy widzi komentarz), `REMOVED` (odczyt anonimowy go nie widzi
--      albo widzi treść `[removed]`, albo skrzynka konta ma odpowiedź AutoModeratora o usunięciu)
--      oraz `UNVERIFIED`, czyli BRAK odczytu anonimowego. `UNVERIFIED` jest wartością domyślną
--      dla wszystkich dotychczasowych PUBLISHED, bo dla żadnego z nich takiego odczytu nie było.
--      To NIE jest ostrożność: to jedyny prawdziwy opis tego, co o nich wiemy.
--   3. `publication_requests.removal_reported_at` i `removal_source`, czyli drugie, niezależne
--      wejście tej samej odpowiedzi: zgłoszenie usunięcia ze skrzynki konta (AutoModerator
--      „was automatically removed”). Osobne pola, a nie obserwacja `ANONYMOUS`, bo skrzynka nie
--      jest odczytem anonimowym i zapisanie jej jako takiego byłoby dowodem, którego nie było.
--
-- Czego ta migracja świadomie NIE robi: nie przepisuje żadnego istniejącego wiersza na CONFIRMED
-- ani na REMOVED. Awans z `UNVERIFIED` wymaga odczytu, a odczytu jeszcze nie było (D-007: brak
-- dowodu to NIE_URUCHOMIONO, nigdy zaliczenie).
--
-- Wzorce odziedziczone z 0007: zamknięte listy jako text + nazwany CHECK, kolumny dokładane
-- z DEFAULT-em zachowującym dotychczasowe zachowanie.
-- =============================================================================

BEGIN;

ALTER TABLE publication_observations
    ADD COLUMN viewer text NOT NULL DEFAULT 'AUTHOR',
    ADD CONSTRAINT publication_observations_viewer_is_known CHECK (viewer IN (
        'AUTHOR', 'ANONYMOUS'
    ));

-- Widoczność liczymy z OSTATNIEJ obserwacji anonimowej, więc indeks prowadzi dokładnie po niej.
CREATE INDEX publication_observations_by_request_and_viewer
    ON publication_observations (request_id, viewer, observed_at DESC);

ALTER TABLE publication_requests
    ADD COLUMN public_visibility text NOT NULL DEFAULT 'UNVERIFIED',
    ADD COLUMN public_visibility_checked_at timestamptz,
    ADD COLUMN removal_reported_at timestamptz,
    ADD COLUMN removal_source text,
    ADD CONSTRAINT publication_requests_public_visibility_is_known CHECK (public_visibility IN (
        'CONFIRMED', 'REMOVED', 'UNVERIFIED'
    )),
    -- Werdykt bez momentu pomiaru jest werdyktem bez dowodu. `UNVERIFIED` to jedyny stan, który
    -- wolno mieć bez daty sprawdzenia, bo on właśnie mówi „nie sprawdzono”.
    ADD CONSTRAINT publication_requests_visibility_has_a_check CHECK (
        public_visibility = 'UNVERIFIED' OR public_visibility_checked_at IS NOT NULL
    ),
    -- Zgłoszenie usunięcia bez wskazania źródła jest anonimowym oskarżeniem w danych.
    ADD CONSTRAINT publication_requests_removal_names_its_source CHECK (
        (removal_reported_at IS NULL) = (removal_source IS NULL)
    ),
    ADD CONSTRAINT publication_requests_removal_source_is_known CHECK (
        removal_source IS NULL OR removal_source IN ('INBOX', 'ANONYMOUS_READ')
    );

COMMIT;
