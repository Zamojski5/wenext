-- =============================================================================
-- Migracja 0019: współpracujące sesje i liczby o zasięgu z pochodzeniem (P6, ROADMAP-PIVOT)
--
-- P6: „wiele platform i współpracujące sesje”. Ta migracja robi dwie rzeczy, które wyglądają na
-- niezwiązane, a są dwiema połowami tego samego zdania z roadmapy: agent ma umieć podzielić pracę
-- między sesje, a człowiek ma po powrocie zobaczyć liczby, o których wiadomo, skąd są.
--
-- 1. `agent_sessions.parent_session_id`: KTO KOMU PRZEKAZAŁ ZADANIE.
--
--    Sesja potomna powstaje z `session.handoff` i wraca wynikiem przez `session.result`. Bez tej
--    kolumny „co się stało z tym, co zleciłem” trzeba by składać z tekstu celów, a cel jest prozą.
--    Kolumna, nie tabela relacji: przekazanie ma DOKŁADNIE jednego rodzica (to jest zlecenie, a
--    nie graf zależności), a osobna tabela pozwalałaby zapisać dwóch i nikt by tego nie odrzucił.
--
--    CHECK-a „rodzic w tym samym projekcie” NIE MA W BAZIE, bo wymagałby podzapytania, a CHECK w
--    PostgreSQL nie może odpytywać innej tabeli. Pilnuje tego klucz obcy po trójce tenantowej
--    (organizacja i workspace muszą się zgadzać) plus sprawdzenie projektu w `sessions/store.py`.
--    To jest świadomy kompromis i jest opisany, a nie przemilczany.
--
-- 2. `audience_snapshots`: LICZBY O ZASIĘGU, KAŻDA ZE ŹRÓDŁEM I MOMENTEM ODCZYTU.
--
--    Roadmapa żąda „metryk wzrostu”, a ZAKRES-FUNKCJONALNY §6 zasada 2 żąda, żeby brak danych był
--    brakiem danych, a nie zerem. Dlatego tabela jest pusta do czasu adaptera i nic jej dziś nie
--    zapisuje: ekran „Profile i konta” pokazuje „brak danych”, bo naprawdę ich nie ma.
--
--    KAŻDY WIERSZ NIESIE `source`. Liczba bez pochodzenia jest nie do obrony pół roku później:
--    „2400 obserwujących” odczytane ze strony profilu i to samo z API platformy to dwie różne
--    jakości dowodu, a wpisane ręcznie to trzecia. Bez tej kolumny wszystkie trzy wyglądają tak
--    samo, a różnica wychodzi dopiero wtedy, gdy ktoś na nich oprze decyzję.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie dodaje adaptera X ani LinkedIn i nie zmienia macierzy
-- możliwości z `workspace/platforms.py`. Macierz jest w kodzie, nie w bazie, i to się nie zmienia
-- (wiersz „REDDIT umie komentować” w bazie przeżyłby usunięcie adaptera).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Przekazanie zadania między sesjami
-- -----------------------------------------------------------------------------
ALTER TABLE agent_sessions
    ADD COLUMN parent_session_id uuid,
    -- Sesja nie jest własnym rodzicem. Bez tego jedna literówka w `session.handoff` tworzy
    -- wiersz, który przy czytaniu drzewa przekazań zapętla czytającego.
    ADD CONSTRAINT agent_sessions_parent_is_another_session CHECK (
        parent_session_id IS NULL OR parent_session_id <> id
    ),
    ADD CONSTRAINT agent_sessions_parent_in_same_workspace
        FOREIGN KEY (organization_id, workspace_id, parent_session_id)
        REFERENCES agent_sessions (organization_id, workspace_id, id);

-- Pytanie brzmi „co powstało z tej sesji”, więc indeks prowadzi od rodzica do dzieci.
CREATE INDEX agent_sessions_by_parent
    ON agent_sessions (organization_id, parent_session_id, created_at)
    WHERE parent_session_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Liczby o zasięgu
-- -----------------------------------------------------------------------------
CREATE TABLE audience_snapshots (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    -- NIE MA TU `workspace_id`, i to jest decyzja, a nie przeoczenie. Liczba należy do KONTA,
    -- a konto w tym schemacie jest własnością ORGANIZACJI (`social_accounts` nie ma kolumny
    -- workspace'u). Dopisanie tu workspace'u dałoby kolumnę bez rodzica na tym poziomie: klucz
    -- obcy musiałby i tak iść parą (organization_id, social_account_id), a `workspace_id`
    -- siedziałby obok, niepilnowany przez nic, i przy pierwszym przypisaniu konta do drugiego
    -- workspace'u trzeba by zgadywać, czyje są te obserwujące.
    --
    -- Liczba należy do konta także dlatego, że konto bywa przypisane do wielu projektów i
    -- odpięte od jednego z nich, a liczba obserwujących nie znika wtedy ani się nie dzieli.
    social_account_id uuid NOT NULL,
    metric           text NOT NULL,
    -- bigint, bo liczba obserwujących bywa większa niż int, i NIEUJEMNA: wartość ujemna tutaj
    -- znaczyłaby, że odczyt się nie udał, a na to jest brak wiersza, nie minus.
    value            bigint NOT NULL CHECK (value >= 0),
    -- Czego dotyczy liczba: stan na moment czy suma za okres. „1200 wyświetleń” bez tego pola
    -- nie mówi, czy to dziś, czy od zawsze.
    period           text NOT NULL,
    -- SKĄD TA LICZBA. Odczyt ze strony profilu, z API platformy albo wpis ręczny to trzy różne
    -- jakości dowodu i pół roku później nie da się ich odróżnić inaczej niż po tej kolumnie.
    source           text NOT NULL,
    observed_at      timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Ten sam odczyt zapisany dwa razy to ten sam wiersz, nie dwa. Bez tego ponowiony job
    -- podwajałby historię, a wykres rysowany z tej historii pokazywałby wzrost, którego nie było.
    UNIQUE (organization_id, social_account_id, metric, period, observed_at),
    CONSTRAINT audience_snapshots_metric_is_known CHECK (metric IN (
        'FOLLOWERS', 'POSTS', 'IMPRESSIONS', 'ENGAGEMENTS'
    )),
    CONSTRAINT audience_snapshots_period_is_known CHECK (period IN (
        'POINT_IN_TIME', 'DAY', 'WEEK', 'MONTH'
    )),
    CONSTRAINT audience_snapshots_source_is_known CHECK (source IN (
        'PLATFORM_API', 'PLATFORM_PAGE', 'MANUAL'
    )),
    -- FOLLOWERS to stan, nie suma za okres. „1200 obserwujących w tym tygodniu” jest zdaniem bez
    -- znaczenia, a wykres złożony z takich wierszy sugerowałby przyrost tam, gdzie był stan.
    CONSTRAINT audience_snapshots_followers_are_a_state CHECK (
        metric <> 'FOLLOWERS' OR period = 'POINT_IN_TIME'
    ),
    CONSTRAINT audience_snapshots_belong_to_account
        FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id)
);

CREATE INDEX audience_snapshots_by_account
    ON audience_snapshots (organization_id, social_account_id, metric, observed_at DESC);

COMMIT;
