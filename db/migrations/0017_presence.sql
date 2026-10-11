-- =============================================================================
-- Migracja 0017: obecność bez otwartego dashboardu (P4, ROADMAP-PIVOT)
--
-- P4: „powierzam cel oraz konta, wybieram tryb, zamykam aplikację. System zbiera zmiany, a agent
-- podejmuje sensowną pracę: komentuje, przygotowuje i publikuje własne posty, odpowiada lub
-- świadomie czeka. Po powrocie widzę wyniki.”
--
-- TRZY RZECZY, KTÓRYCH DOTĄD NIE BYŁO, I PO CO KAŻDA:
--
--   1. `project_delegations`: TRWAŁE POWIERZENIE ZAKRESU. Do tej pory zgodą był albo podpis pod
--      konkretnym tekstem (dokument zgód), albo podpisana polityka pilota. Delegacja jest tym
--      samym pomysłem na poziomie projektu: człowiek raz podpisuje, CZEGO agent może dotykać
--      (konta, społeczności, rodzaje działań) i ILE mu wolno (budżet), a system przestaje pytać
--      o każdy tekst. Techniczne sprawdzenia zostają: tożsamość konta, rewizja, pojedyncze
--      wykonanie, widoczność po publikacji.
--   2. `wakeups`: POWÓD I KOSZT KAŻDEGO URUCHOMIENIA MODELU. Roadmapa żąda tego wprost, a bez
--      osobnej tabeli odpowiedź na „dlaczego model się obudził i ile to kosztowało” trzeba by
--      składać z logów. Tik BEZ zdarzeń też zapisuje wiersz, z `skipped_reason`, bo „nie było po
--      co” jest wynikiem, nie brakiem danych.
--   3. `wakeup_events`: pojedyncze zdarzenia, po jednym wierszu, z KLUCZEM DEDUPLIKACJI pod
--      więzem UNIQUE.
--
-- DLACZEGO ZDARZENIA SĄ TABELĄ, A NIE POLEM `events jsonb` W `wakeups`. Deduplikacja po kluczu
-- zdarzenia musi być WIĘZEM, nie zapytaniem: dwa kolektory pytające „czy taki klucz już jest”
-- przepuszczą oba wstawienia, a skutkiem jest druga sesja modelu o tym samym poście. Ten sam
-- argument, którym 0013 wymusza „jeden aktywny profil na konto” indeksem, a nie kodem. Trzymanie
-- listy RÓWNIEŻ w jsonb dałoby dwie reprezentacje tego samego i one by się rozjechały, więc
-- `wakeups` nie ma pola `events`; lista to wiersze dzieci.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie dotyka polityki pilota ani jego biegów. P4 buduje ścieżkę
-- projektową OBOK pilota; pilot z podpisaną polityką chodzi dalej na swoich tabelach z 0008.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Rodzaj sesji: rozmowa z człowiekiem albo praca bez niego
-- -----------------------------------------------------------------------------
-- DEFAULT 'CONVERSATION', więc żadna sesja z P2 nie zmienia znaczenia. PRESENCE to NOWY rodzaj,
-- nie przemianowanie starego: różni się tym, że nikt nie patrzy, budżet pochodzi z delegacji,
-- a powód uruchomienia jest w `wakeups`.
ALTER TABLE agent_sessions
    ADD COLUMN kind text NOT NULL DEFAULT 'CONVERSATION',
    ADD CONSTRAINT agent_sessions_kind_is_known CHECK (kind IN ('CONVERSATION', 'PRESENCE'));

-- -----------------------------------------------------------------------------
-- Delegacja: co i ile wolno agentowi w tym projekcie
-- -----------------------------------------------------------------------------
CREATE TABLE project_delegations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    project_id       uuid NOT NULL,
    mode             text NOT NULL,
    -- Konta, społeczności i rodzaje działań, na które człowiek się zgodził. Kształt sprawdza
    -- kod (`presence/scope.py`), bo lista identyfikatorów w CHECK-u nie da się utrzymać.
    scope            jsonb NOT NULL,
    -- Limity doby i godziny. Osobno od `scope`, bo odpowiadają na inne pytanie: zakres mówi
    -- CZEGO wolno dotykać, budżet ILE razy.
    budget           jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Odcisk PODPISANEGO zakresu i budżetu. Trafia do `approval_decisions.reason` razem z id
    -- delegacji, więc decyzja wydana na podstawie powierzenia wskazuje dokładnie tę wersję
    -- zakresu, która obowiązywała. Bez odcisku „delegacja X pozwoliła” nie mówi NA CO pozwoliła:
    -- zakres jest edytowalny tylko przez nowy podpis, ale dziennik ma być czytelny sam z siebie.
    scope_sha256     text NOT NULL CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
    signed_at        timestamptz NOT NULL DEFAULT now(),
    signed_by        uuid NOT NULL,
    -- Wstrzymanie i odwołanie to DWIE różne rzeczy. Wstrzymana wraca tym samym podpisem;
    -- odwołana nie wraca nigdy i wymaga nowego. Jedna kolumna zlałaby „na chwilę” z „koniec”.
    paused_at        timestamptz,
    revoked_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT project_delegations_mode_is_known CHECK (mode IN (
        'AUTONOMOUS', 'REVIEW', 'RESEARCH'
    )),
    CONSTRAINT project_delegations_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id),
    CONSTRAINT project_delegations_signed_by_operator
        FOREIGN KEY (organization_id, signed_by)
        REFERENCES operators (organization_id, id)
);

-- Jedna ŻYWA delegacja na projekt. Indeks, nie CHECK i nie kod: z trzech mechanizmów tylko
-- indeks przeżywa dwa równoległe podpisy, a dwie żywe delegacje to dwa różne zakresy, na które
-- ta sama publikacja mogłaby się powołać.
CREATE UNIQUE INDEX project_delegations_one_live_per_project
    ON project_delegations (project_id)
    WHERE revoked_at IS NULL;

CREATE INDEX project_delegations_by_project
    ON project_delegations (organization_id, workspace_id, project_id, signed_at DESC);

-- -----------------------------------------------------------------------------
-- Obudzenia: dlaczego model ruszył i ile to kosztowało
-- -----------------------------------------------------------------------------
CREATE TABLE wakeups (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    project_id       uuid NOT NULL,
    reason           text NOT NULL,
    -- Sesja, którą to obudzenie otworzyło. NULL znaczy „modelu NIE uruchomiono”, i to jest
    -- najczęstszy wiersz w tej tabeli: pusty cykl obserwacji też się zapisuje.
    session_id       uuid,
    -- Dlaczego nie uruchomiono modelu. Wypełnione dokładnie wtedy, gdy `session_id` jest NULL
    -- i tik się zakończył; para tych dwóch kolumn jest odpowiedzią na „czy coś się stało”.
    -- ZAMKNIĘTA LISTA KODÓW (`presence/store.py`), nie zdanie: raport dnia grupuje po tej
    -- kolumnie i „12 tików bez zdarzeń” musi dać się policzyć bez czytania tekstu.
    skipped_reason   text,
    -- Zdanie dla człowieka do kodu wyżej, np. „publikacje: 6 z 6 w ostatniej dobie”. Osobna
    -- kolumna, bo liczby z chwili pominięcia są inne niż te same liczby policzone wieczorem
    -- przy raporcie, a raport ma mówić, co było wtedy.
    skipped_detail   text,
    -- Tury, wywołania narzędzi i czas. NULL do czasu zakończenia sesji; zero to NIE to samo,
    -- co „jeszcze nie wiadomo”.
    cost             jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    consumed_at      timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT wakeups_reason_is_known CHECK (reason IN (
        'NEW_EVENT', 'DUE', 'USER_MESSAGE', 'RETRY'
    )),
    -- Obudzenie ma albo sesję, albo powód jej braku, nigdy obu naraz. Wiersz z jednym i drugim
    -- twierdziłby, że model i ruszył, i nie ruszył.
    CONSTRAINT wakeups_ran_or_said_why CHECK (
        session_id IS NULL OR skipped_reason IS NULL
    ),
    -- Szczegół bez kodu byłby wyjaśnieniem pominięcia, którego nie było.
    CONSTRAINT wakeups_detail_explains_a_skip CHECK (
        skipped_detail IS NULL OR skipped_reason IS NOT NULL
    ),
    CONSTRAINT wakeups_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id),
    CONSTRAINT wakeups_point_at_session
        FOREIGN KEY (organization_id, workspace_id, session_id)
        REFERENCES agent_sessions (organization_id, workspace_id, id)
);

CREATE INDEX wakeups_by_project
    ON wakeups (organization_id, workspace_id, project_id, created_at DESC);

-- Tiki szukają obudzeń jeszcze nieobsłużonych, więc indeks prowadzi dokładnie po nich.
CREATE INDEX wakeups_pending
    ON wakeups (organization_id, project_id, created_at)
    WHERE consumed_at IS NULL;

CREATE TABLE wakeup_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    project_id       uuid NOT NULL,
    wakeup_id        uuid NOT NULL,
    kind             text NOT NULL,
    -- Klucz deduplikacji, budowany przez kolektor z rodzaju i identyfikatora platformy
    -- (np. `NEW_THREAD:t3_abc123`). To on, a nie treść, decyduje, czy to samo zdarzenie już
    -- obudziło agenta.
    event_key        text NOT NULL CHECK (length(btrim(event_key)) > 0),
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    -- SEDNO DEDUPLIKACJI. Jedno zdarzenie budzi agenta raz w danym projekcie. Więz, nie
    -- zapytanie: dwa kolektory sprawdzające „czy jest” przepuszczą oba wstawienia, a skutkiem
    -- jest druga sesja modelu o tym samym poście i drugi komentarz pod nim.
    UNIQUE (organization_id, project_id, event_key),
    CONSTRAINT wakeup_events_kind_is_known CHECK (kind IN (
        'NEW_THREAD', 'REPLY', 'MODERATION', 'DUE'
    )),
    CONSTRAINT wakeup_events_belong_to_wakeup
        FOREIGN KEY (organization_id, workspace_id, wakeup_id)
        REFERENCES wakeups (organization_id, workspace_id, id)
);

CREATE INDEX wakeup_events_by_wakeup ON wakeup_events (wakeup_id, created_at);

-- -----------------------------------------------------------------------------
-- Jedna kolejka publikacji dla pilota I dla projektów
-- -----------------------------------------------------------------------------
-- `publication_requests` z 0007 była przywiązana do pilota dwoma NOT NULL-ami: `run_id` do
-- biegu i `reaction_id` do reakcji. Propozycja z projektu nie ma ani jednego, ani drugiego.
--
-- ROZWAŻONO TRZY DROGI I DWIE ODRZUCONO. Druga kolejka publikacji dla projektów to drugi
-- publikator, druga kolumna widoczności i dwa miejsca, w których trzeba pamiętać o odczycie
-- skutku; rozjechałyby się przy pierwszej poprawce w jednym z nich. Wykonywanie z pominięciem
-- kolejki, samym intentem, zabiera `public_visibility`, czyli cofa P0: „usunięta treść nie jest
-- raportowana jako publiczny sukces” przestałoby obowiązywać dla publikacji projektowych.
-- Zostaje rozszerzenie TEJ tabeli, addytywne.
--
-- CO TO ZNACZY DLA 52 ISTNIEJĄCYCH WIERSZY: nic. Mają `run_id` i `reaction_id` wypełnione,
-- więc spełniają nową kontrolę bez dotykania. 0007 zostaje niezmienione.
--
-- `UNIQUE (reaction_id)` z 0007 NIE jest ruszane i nie musi być: w PostgreSQL wartości NULL
-- są w indeksie unikalnym traktowane jako różne, więc dowolnie wiele wierszy projektowych
-- (reaction_id IS NULL) przechodzi, a dwa wnioski na tę samą reakcję pilota nadal nie.
ALTER TABLE publication_requests
    ALTER COLUMN run_id DROP NOT NULL,
    ALTER COLUMN reaction_id DROP NOT NULL,
    ADD COLUMN project_id uuid,
    ADD COLUMN delegation_id uuid,
    -- Wniosek należy DO JEDNEGO z dwóch światów. „Ani jednego” byłoby wnioskiem, którego
    -- pochodzenia nie da się wskazać, a „obu naraz” wnioskiem, który powołuje się i na bieg
    -- pilota, i na delegację projektu, czyli na dwie różne zgody.
    ADD CONSTRAINT publication_requests_come_from_a_run_or_a_project CHECK (
        (run_id IS NOT NULL AND reaction_id IS NOT NULL
             AND project_id IS NULL AND delegation_id IS NULL)
        OR (project_id IS NOT NULL AND delegation_id IS NOT NULL
             AND run_id IS NULL AND reaction_id IS NULL)
    ),
    ADD CONSTRAINT publication_requests_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id),
    -- Do KONKRETNEJ delegacji, nie do projektu: publikacja sprzed miesiąca ma wskazywać
    -- powierzenie, pod którym powstała, także gdy projekt ma już inne.
    ADD CONSTRAINT publication_requests_point_at_delegation
        FOREIGN KEY (organization_id, workspace_id, delegation_id)
        REFERENCES project_delegations (organization_id, workspace_id, id);

CREATE INDEX publication_requests_by_project
    ON publication_requests (organization_id, workspace_id, project_id, queued_at DESC)
    WHERE project_id IS NOT NULL;

COMMIT;
