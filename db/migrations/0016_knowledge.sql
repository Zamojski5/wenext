-- =============================================================================
-- Migracja 0016 — źródła i wiedza z pochodzeniem (P3 etap A, ROADMAP-PIVOT)
--
-- P3: „agent szuka w internecie i materiałach, wraca do źródeł według potrzeby, a kolejna sesja
-- stosuje ustalenie bez ponownego wklejania całości”. Warunek ukończenia mówi też, czego NIE
-- wolno: „wynik rozróżnia fakt, interpretację, preferencję człowieka i doświadczenie z działań”
-- oraz „materiały z zewnątrz nie stają się instrukcjami systemowymi”.
--
-- DWIE TABELE I ROZDZIAŁ, KTÓRY JE UZASADNIA. `knowledge_sources` to MIEJSCE, z którego coś
-- wzięliśmy: adres, moment pobrania, suma kontrolna bajtów i wskazanie surowego materiału.
-- `knowledge_items` to ZDANIE, które z tego wynika, z jawnym rodzajem i z tym, kto je wypowiedział.
-- Trzymanie obu w jednej tabeli kasowałoby różnicę między „przeczytaliśmy stronę” a „uważamy, że
-- z niej wynika X”, czyli dokładnie tę, o którą prosi kryterium.
--
-- CZEGO TA MIGRACJA NIE DUBLUJE. `user_profile_versions` (0004) zostaje głosem osoby, a
-- `judgment_feedback` (0004) oceną konkretnego osądu. `knowledge_items` ich nie kopiuje i nie
-- migruje; wnosi warstwę, której żadna z nich nie ma — `EXPERIENCE`, wiążące sytuację, decyzję
-- i wynik. Jeden widok na wszystko, gdy będzie potrzebny, jest odczytem łączącym, nie kopią.
--
-- `knowledge_items` JEST JEDYNĄ PAMIĘCIĄ PROJEKTU. Migracja 0015 miała własną `project_memory`
-- o tym samym przeznaczeniu; kolizję wychwycono przed pierwszym trwałym zastosowaniem obu i
-- koordynator rozstrzygnął na rzecz tej tabeli, bo tylko tutaj „fakt ma źródło", „preferencja
-- pochodzi od człowieka" i „doświadczenie ma wynik" są WIĘZAMI, a nie deklaracją w wolnym polu
-- tekstowym. `session_id` poniżej jest tym, co 0015 traciło przy skreśleniu swojej tabeli.
--
-- REGUŁY SĄ CHECK-AMI, NIE UMOWĄ. Fakt bez źródła, preferencja postawiona przez agenta w imieniu
-- człowieka i doświadczenie bez wyniku to trzy sposoby, na które ta tabela mogłaby zacząć
-- kłamać, a każdy z nich wygląda w kodzie zupełnie zwyczajnie. Baza ich nie przyjmuje.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Surowy materiał jest artefaktem
-- -----------------------------------------------------------------------------
-- Pobrana strona ma wylądować tam, gdzie repozytorium trzyma bajty: w `artifacts`, z retencją,
-- ACL, sumą kontrolną i zamiataczem, które ta tabela ma od 0003. Zamknięta lista `kind` nie ma
-- dziś legalnej wartości dla źródła, więc ją rozszerzamy — DROP + ADD z pełną listą, tak jak
-- 0010 zrobiła z ruchami w `pilot_reactions`, bo `ALTER ... ADD` nie istnieje dla CHECK-a.
ALTER TABLE artifacts
    DROP CONSTRAINT artifacts_kind_is_known,
    ADD CONSTRAINT artifacts_kind_is_known CHECK (kind IN (
        'TRACE', 'SCREENSHOT', 'HAR', 'LOG', 'REPORT', 'SOURCE'
    ));

-- -----------------------------------------------------------------------------
-- Źródło
-- -----------------------------------------------------------------------------

CREATE TABLE knowledge_sources (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    workspace_id     uuid NOT NULL,
    -- NULL = materiał całego workspace'u, wartość = materiał jednego projektu. MATCH SIMPLE,
    -- więc NULL przechodzi bez sprawdzania; przy wartości cała trójka musi się zgadzać.
    project_id       uuid,
    kind             text NOT NULL,
    -- Adres, spod którego to wzięliśmy. Dla notatki człowieka nie ma adresu, więc jest nim
    -- `note:sha256:<hash treści>` — stabilny, powtarzalny i sam z siebie odróżnia dwie notatki.
    locator          text NOT NULL CHECK (length(btrim(locator, E' \t\r\n')) > 0),
    title            text NOT NULL DEFAULT '',
    -- Kiedy to przeczytaliśmy. NULL wyłącznie dla źródła, którego nie udało się pobrać.
    fetched_at       timestamptz,
    -- sha256 BAJTÓW, które wtedy przyszły. To jest jedyna rzecz, która odróżnia „to samo źródło”
    -- od „ten sam adres, inna treść”, i dlatego ma wymuszony format.
    content_hash     text CHECK (content_hash IS NULL OR content_hash ~ '^sha256:[0-9a-f]{64}$'),
    status           text NOT NULL DEFAULT 'FETCHED',
    -- Surowy materiał w `artifacts`. NULL, gdy nie było czego zapisać (UNAVAILABLE, notatka).
    raw_artifact_id  uuid,
    -- Powód niedostępności, słowami. „Nie udało się" bez powodu każe zgadywać, czy to literówka
    -- w adresie, czy padł serwis.
    unavailable_reason text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT knowledge_sources_kind_is_known CHECK (kind IN (
        'WEB_PAGE', 'WEB_SEARCH', 'RSS', 'NEWSLETTER', 'DOCUMENT', 'USER_NOTE'
    )),
    CONSTRAINT knowledge_sources_status_is_known CHECK (status IN (
        'FETCHED', 'UNAVAILABLE', 'STALE'
    )),
    -- Źródło oznaczone jako pobrane MUSI mieć kiedy i co. Bez tego „pobrane” jest słowem, nie
    -- pomiarem, a ZAKRES-FUNKCJONALNY §6 wymaga daty i pochodzenia przy każdej liczbie.
    CONSTRAINT knowledge_sources_fetched_have_evidence CHECK (
        status <> 'FETCHED' OR (fetched_at IS NOT NULL AND content_hash IS NOT NULL)
    ),
    -- I odwrotnie: niedostępne nie udaje, że ma treść.
    CONSTRAINT knowledge_sources_unavailable_have_reason CHECK (
        status <> 'UNAVAILABLE'
        OR (content_hash IS NULL AND length(btrim(coalesce(unavailable_reason, ''), E' \t\r\n')) > 0)
    ),
    CONSTRAINT knowledge_sources_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    CONSTRAINT knowledge_sources_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id) MATCH SIMPLE,
    CONSTRAINT knowledge_sources_point_at_artifact
        FOREIGN KEY (organization_id, workspace_id, raw_artifact_id)
        REFERENCES artifacts (organization_id, workspace_id, id) MATCH SIMPLE
);

-- Jeden wiersz na adres w danym zakresie. `NULLS NOT DISTINCT` (PostgreSQL 15+, tu 18.6) jest tu
-- konieczne: bez niego dwa materiały workspace'owe (`project_id IS NULL`) pod tym samym adresem
-- są dla indeksu różne, więc „dodaj to samo jeszcze raz" cicho tworzyłoby duplikat, a lista
-- materiałów rosłaby przy każdym odświeżeniu.
CREATE UNIQUE INDEX knowledge_sources_one_per_locator
    ON knowledge_sources (organization_id, workspace_id, project_id, locator)
    NULLS NOT DISTINCT;

CREATE INDEX knowledge_sources_by_scope
    ON knowledge_sources (organization_id, workspace_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- Ustalenie
-- -----------------------------------------------------------------------------

CREATE TABLE knowledge_items (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL,
    workspace_id       uuid NOT NULL,
    project_id         uuid,
    -- Czyje to ustalenie. „Wspólny zbiór ustaleń nie przenosi stylu Jana do Anny”
    -- (P5), więc osoba jest kolumną od początku, a nie doklejeniem później.
    person_profile_id  uuid,
    kind               text NOT NULL,
    statement          text NOT NULL CHECK (length(btrim(statement, E' \t\r\n')) > 0),
    source_id          uuid,
    -- Fragment źródła, na którym to stoi: cytat albo zakres. Tekst, bo „akapit trzeci” i
    -- „bajty 1200-1480” są oba poprawnymi odpowiedziami na to samo pytanie.
    source_span        text,
    stated_by          text NOT NULL,
    -- Sesja agenta, która to zapisała (0015). NULL znaczy „zapisał człowiek albo import”, nie
    -- „nie wiadomo". Klucz jest TRÓJKĄ, a nie parą: `agent_sessions` ma
    -- `UNIQUE (organization_id, workspace_id, id)` i tylko trójka nie pozwoli ustaleniu jednego
    -- workspace'u wskazać sesji z drugiego.
    session_id         uuid,
    -- Sytuacja, decyzja i wynik dla EXPERIENCE. Pusty obiekt dla pozostałych rodzajów.
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from         timestamptz NOT NULL DEFAULT now(),
    -- Korekta NIE kasuje. Stary wiersz dostaje wskazanie na nowy i zostaje czytelny.
    superseded_by      uuid,
    retracted_at       timestamptz,
    retracted_reason   text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT knowledge_items_kind_is_known CHECK (kind IN (
        'FACT', 'INTERPRETATION', 'PREFERENCE', 'EXPERIENCE'
    )),
    CONSTRAINT knowledge_items_stated_by_is_known CHECK (stated_by IN (
        'USER', 'AGENT', 'SOURCE'
    )),
    -- FAKT BEZ ŹRÓDŁA TO NIE FAKT. To jest cała różnica między `FACT` a `INTERPRETATION`
    -- w tej tabeli i jedyne miejsce, w którym da się ją wymusić.
    CONSTRAINT knowledge_items_fact_has_a_source CHECK (
        kind <> 'FACT' OR source_id IS NOT NULL
    ),
    -- PREFERENCJA POCHODZI OD CZŁOWIEKA. Agent, który sam zapisuje „użytkownik woli X”, tworzy
    -- ustalenie, którego nikt nie wypowiedział, i kolejna sesja stosuje je jako czyjąś wolę.
    CONSTRAINT knowledge_items_preference_comes_from_the_user CHECK (
        kind <> 'PREFERENCE' OR stated_by = 'USER'
    ),
    -- DOŚWIADCZENIE WIĄŻE TRZY RZECZY. Bez wyniku jest planem, bez sytuacji anegdotą.
    CONSTRAINT knowledge_items_experience_is_complete CHECK (
        kind <> 'EXPERIENCE'
        OR (payload ? 'situation' AND payload ? 'decision' AND payload ? 'outcome')
    ),
    -- Wycofanie bez powodu nie mówi kolejnej sesji, czy ustalenie było błędne, czy się
    -- zdezaktualizowało, a to są dwie różne lekcje.
    CONSTRAINT knowledge_items_retraction_has_a_reason CHECK (
        retracted_at IS NULL
        OR length(btrim(coalesce(retracted_reason, ''), E' \t\r\n')) > 0
    ),
    CONSTRAINT knowledge_items_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    CONSTRAINT knowledge_items_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id) MATCH SIMPLE,
    CONSTRAINT knowledge_items_belong_to_person
        FOREIGN KEY (organization_id, workspace_id, person_profile_id)
        REFERENCES person_profiles (organization_id, workspace_id, id) MATCH SIMPLE,
    CONSTRAINT knowledge_items_point_at_source
        FOREIGN KEY (organization_id, workspace_id, source_id)
        REFERENCES knowledge_sources (organization_id, workspace_id, id) MATCH SIMPLE,
    -- Korekta wskazuje wiersz w TEJ SAMEJ przestrzeni. Bez trójki ustalenie jednego workspace'u
    -- mogłoby zostać „poprawione” wpisem z drugiego.
    CONSTRAINT knowledge_items_point_at_session
        FOREIGN KEY (organization_id, workspace_id, session_id)
        REFERENCES agent_sessions (organization_id, workspace_id, id) MATCH SIMPLE,
    CONSTRAINT knowledge_items_superseded_by_is_ours
        FOREIGN KEY (organization_id, workspace_id, superseded_by)
        REFERENCES knowledge_items (organization_id, workspace_id, id) MATCH SIMPLE
);

CREATE INDEX knowledge_items_by_scope
    ON knowledge_items (organization_id, workspace_id, created_at DESC);

-- Wpisy OBOWIĄZUJĄCE: nie poprawione i nie wycofane. To jest zbiór, o który pyta każda sesja,
-- i jedyny, z którego wolno budować kontekst.
CREATE INDEX knowledge_items_in_force
    ON knowledge_items (organization_id, workspace_id)
    WHERE superseded_by IS NULL AND retracted_at IS NULL;

COMMIT;
