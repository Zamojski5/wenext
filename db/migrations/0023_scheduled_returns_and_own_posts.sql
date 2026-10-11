-- =============================================================================
-- Migracja 0023: zaplanowany powrót agenta i własny post (P4 etap C, ROADMAP-PIVOT)
--
-- DWA BRAKI WSKAZANE PRZEZ NIEZALEŻNY AUDYT Z 19.09, oba o tym samym: agent umiał reagować, a
-- nie umiał NIC ZAPLANOWAĆ ANI ZACZĄĆ.
--
--   1. `wakeups.due_at` — POWRÓT, KTÓRY AGENT SAM SOBIE ZAPLANOWAŁ. Do tej pory obudzenie
--      powstawało wyłącznie w reakcji na cudzy ruch: nowy wątek, odpowiedź, moderacja. Agent,
--      który stwierdza „wrócę do tego za trzy dni, bo teraz nie ma o czym pisać”, nie miał gdzie
--      tego zapisać, więc albo pisał od razu, albo zapominał. Wiersz z `due_at` w przyszłości
--      jest tą notatką, a tik obecności traktuje go jak zdarzenie, gdy termin minie.
--
--      NULL ZNACZY „WYMAGALNE OD RAZU” i tak wygląda każdy dotychczasowy wiersz: obudzenie
--      powstałe z obserwacji jest wymagalne w chwili powstania. Kolumna nie ma DEFAULT now(),
--      bo „zaplanowane na teraz” i „nie zaplanowane wcale” to dwie różne rzeczy i tylko jedną
--      z nich widać potem na ekranie jako zaplanowany powrót.
--
--   2. `publication_requests.kind` — CZY TO ODPOWIEDŹ, CZY WŁASNY POST. Cała ścieżka publikacji
--      od 0007 zakładała odpowiedź pod cudzym wątkiem: cel jest komentarzem, dowodem jest
--      komentarz, odczyt skutku szuka komentarza. Własny post ma inny cel (społeczność, nie
--      wątek), inny dowód i inny koszt, i bez tej kolumny publikator nie umiałby ich odróżnić
--      inaczej niż zgadując po kształcie `target_external_id`.
--
--      DEFAULT 'REPLY', więc wszystkie 53 dotychczasowe wiersze zachowują znaczenie bez
--      dotykania. To nie jest przepisanie historii, tylko nazwanie tego, czym zawsze była.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie dodaje budżetu własnych postów. Budżet delegacji jest w jsonb
-- (0017) i nowy klucz nie potrzebuje zmiany schematu; lista dozwolonych kluczy żyje w
-- `presence/scope.py`, gdzie i tak musi być sprawdzana.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Zaplanowany powrót
-- -----------------------------------------------------------------------------
ALTER TABLE wakeups
    ADD COLUMN due_at timestamptz,
    -- Odwołany powrót ZOSTAJE w tabeli. Skasowanie wiersza zabrałoby odpowiedź na pytanie
    -- „dlaczego agent nie wrócił, skoro obiecał”, a to jest dokładnie to pytanie, które ktoś
    -- zada tydzień później. Ekran pokazuje odwołane osobno.
    ADD COLUMN cancelled_at timestamptz,
    -- Odwołać można tylko to, co było zaplanowane. Obudzenie z obserwacji nie ma czego odwoływać:
    -- ono już się wydarzyło.
    ADD CONSTRAINT wakeups_only_planned_can_be_cancelled CHECK (
        cancelled_at IS NULL OR due_at IS NOT NULL
    );

-- Tik szuka powrotów, których termin minął. Indeks prowadzi dokładnie po nich i pomija zarówno
-- odwołane, jak i już obsłużone.
CREATE INDEX wakeups_due_and_waiting
    ON wakeups (organization_id, project_id, due_at)
    WHERE due_at IS NOT NULL AND cancelled_at IS NULL AND consumed_at IS NULL;

-- -----------------------------------------------------------------------------
-- Rodzaj publikacji
-- -----------------------------------------------------------------------------
ALTER TABLE publication_requests
    ADD COLUMN kind text NOT NULL DEFAULT 'REPLY',
    ADD CONSTRAINT publication_requests_kind_is_known CHECK (kind IN ('REPLY', 'OWN_POST')),
    -- Własny post celuje w SPOŁECZNOŚĆ, odpowiedź w wątek albo komentarz. `target_external_id`
    -- niesie jedno albo drugie i bez tego więzu dałoby się zapisać własny post wskazujący cudzy
    -- komentarz, czyli rzecz, której aktuator nie umiałby wykonać, a kolejka przyjęłaby.
    ADD CONSTRAINT publication_requests_target_matches_kind CHECK (
        CASE kind
            WHEN 'REPLY' THEN target_external_id ~ '^t[13]_[a-z0-9]{1,12}$'
            -- Klucz społeczności w postaci, w jakiej trzyma go `communities.community_key`:
            -- „r/nazwa”. Bez `r/` nie zapiszemy, bo to jest ta sama nazwa, z którą zakres
            -- delegacji się porównuje, a dwa zapisy tej samej społeczności znaczyłyby, że
            -- porównanie czasem nie trafia.
            -- Kształt jest redditowy, bo Reddit jest dziś jedyną platformą, na której coś
            -- zakładamy (`workspace/platforms.py`). Druga platforma z własnymi postami rozpisze
            -- ten warunek na swój kształt; dopuszczenie wszystkiego „na zapas” znaczyłoby, że
            -- kolejka przyjmuje cel, którego żaden aktuator nie umie wykonać.
            ELSE target_external_id ~ '^r/[A-Za-z0-9_]{2,21}$'
        END
    );

-- WIĘZ Z 0007 ODCHODZI, bo mówił o świecie, w którym każda publikacja była odpowiedzią:
-- `target_external_id ~ '^t[13]_...'` dla KAŻDEGO wiersza. Własny post nie ma fullname'u,
-- dopóki nie powstanie, więc ten więz odrzucał całą kolejkę własnych postów. Zastępuje go
-- `publication_requests_target_matches_kind` powyżej, który mówi to samo o odpowiedziach i
-- osobno o postach; nie jest to poluzowanie, tylko ten sam warunek rozpisany na dwa rodzaje.
ALTER TABLE publication_requests
    DROP CONSTRAINT publication_requests_target_external_id_check;

COMMIT;
