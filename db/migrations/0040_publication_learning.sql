-- =============================================================================
-- 0040. PĘTLA UCZENIA: CO SIĘ SPRAWDZIŁO W TYM, CO OPUBLIKOWANE (13.10).
--
-- Założyciel: "powinno się uczyć". Wenext publikuje odpowiedzi i posty, a ich wynik jest
-- zapisywany tylko jako ostatni odczyt (`publication_observations`, okno 26 godzin, bez
-- kontekstu). Z samych liczb nie wynika nic, bo nie wiadomo, pod czym i kiedy odpowiedź padła.
-- Ta migracja dokłada trzy rzeczy, każda z innego powodu.
--
-- 1. `learning_posts`: JEDEN WIERSZ NA OPUBLIKOWANĄ RZECZ, razem z tym, co o niej wiemy z chwili
--    publikacji: wiek posta, pod którym odpowiedzieliśmy, liczba obserwujących jego autora, ile
--    odpowiedzi już tam było, jaki to haczyk, czy padło "Wenext", czy był link, o której godzinie.
--    Wiersz NIE ZASTĘPUJE wniosku publikacyjnego: wskazuje go (`request_id`), albo
--    ręczną publikację (`manual_publication_id`), albo nie wskazuje niczego, gdy rzecz znamy
--    tylko z dziennika postów założyciela (`origin = 'DIARY'`). Klucz to platforma i
--    identyfikator NASZEJ rzeczy (`x:<id>`, `t1_<id>`), więc ta sama rzecz z dwóch źródeł jest
--    jednym wierszem. Cecha, której nikt nie ustalił, to NULL, nigdy zero (reguła z 0035).
--
-- 2. `learning_snapshots`: LICZNIKI W USTALONYCH MOMENTACH, około 1, 6, 24 i 72 godziny po
--    publikacji (`horizon_hours`). Jedna migawka na rzecz i horyzont (indeks częściowy), wiek
--    zapisany co do minuty, źródło nazwane (kapsuła, publiczne lustro fxtwitter, odczyt
--    obserwacji, dziennik). Odczyt spoza okna żadnego horyzontu zostaje z `horizon_hours = NULL`
--    jako dowód, a statystyka go nie liczy: dwie liczby z różnych godzin życia posta to nie jest
--    porównanie. Licznik, którego platforma nie pokazała, to NULL, a migawka bez żadnego
--    licznika nie istnieje (CHECK), bo to byłby wpis o tym, że ktoś spojrzał i nic nie zobaczył.
--
-- 3. Rodzaj `LESSON` w `knowledge_items`: WNIOSEK POLICZONY Z TYCH MIGAWEK, np. "odpowiedzi pod
--    postami młodszymi niż 30 min miały medianę 140 wyświetleń (n=6), pod starszymi niż 2 h
--    25 (n=5)". Zapisuje go produkt, nie model (`stated_by = 'AGENT'`, a narzędzie `memory.write`
--    nie przyjmuje tego rodzaju), i baza pilnuje jedynego, co ma sens: wniosek bez dowodu nie
--    istnieje. Payload musi nieść klucz wniosku oraz po obu stronach porównania liczebność `n`.
--    Liczba bez `n` to anegdota z pewnym siebie głosem. Jedyny wyjątek jest nazwany: zasiana
--    REGUŁA (`payload.rule = true` i `payload.source`), np. zasada założyciela o tonie odpowiedzi.
--    Nie jest pomiarem i nie udaje go; pamięć i `playbook.read` pokazują ją osobno.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Rodzaj LESSON w pamięci projektu
-- -----------------------------------------------------------------------------
-- DROP + ADD z pełną listą, tak jak 0016 zrobiła z `artifacts_kind_is_known`: `ALTER ... ADD`
-- nie istnieje dla CHECK-a.
ALTER TABLE knowledge_items
    DROP CONSTRAINT knowledge_items_kind_is_known,
    ADD CONSTRAINT knowledge_items_kind_is_known CHECK (kind IN (
        'FACT', 'INTERPRETATION', 'PREFERENCE', 'EXPERIENCE', 'LESSON'
    )),
    -- WNIOSEK Z POMIARU MA DOWÓD. Klucz porównania (po nim wniosek jest znajdowany i
    -- poprawiany) oraz dwie strony porównania, każda z `n`. Pochodzi z produktu, nie od
    -- człowieka ani ze źródła zewnętrznego. JEDEN WYJĄTEK, NAZWANY: zasiana REGUŁA
    -- (`payload.rule = true`), np. zasada założyciela o tonie odpowiedzi. Nie jest pomiarem i nie
    -- udaje go: zamiast stron porównania musi wskazać, skąd pochodzi (`payload.source`).
    ADD CONSTRAINT knowledge_items_lesson_keeps_its_evidence CHECK (
        kind <> 'LESSON'
        OR (
            stated_by = 'AGENT'
            AND payload ? 'lesson_key'
            AND (
                (
                    coalesce(payload -> 'rule', 'false'::jsonb) = 'true'::jsonb
                    AND length(coalesce(payload ->> 'source', '')) > 0
                )
                OR (
                    -- `coalesce`, bo brak klucza daje NULL, a CHECK z wynikiem NULL PRZECHODZI.
                    coalesce(jsonb_typeof(payload -> 'winner'), '') = 'object'
                    AND coalesce((payload -> 'winner') ? 'n', false)
                    AND coalesce(jsonb_typeof(payload -> 'loser'), '') = 'object'
                    AND coalesce((payload -> 'loser') ? 'n', false)
                )
            )
        )
    );

-- Szukanie wniosku po kluczu porównania (`LearningStore.lessons_in_force`), tylko obowiązujących.
CREATE INDEX knowledge_items_lesson_by_key
    ON knowledge_items (organization_id, workspace_id, (payload ->> 'lesson_key'))
    WHERE kind = 'LESSON' AND superseded_by IS NULL AND retracted_at IS NULL;

-- -----------------------------------------------------------------------------
-- Opublikowana rzecz i jej kontekst
-- -----------------------------------------------------------------------------

CREATE TABLE learning_posts (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       uuid NOT NULL REFERENCES organizations (id),
    workspace_id          uuid NOT NULL,
    -- NULL dla publikacji pilota sprzed projektów i dla wpisu bez projektu. MATCH SIMPLE, więc
    -- NULL przechodzi bez sprawdzania; przy wartości cała trójka musi się zgadzać.
    project_id            uuid,
    platform              text NOT NULL,
    -- `REPLY` odpowiedź pod cudzym postem, `OWN_POST` nasz własny post.
    kind                  text NOT NULL,
    -- Skąd wiemy, że to wyszło: wniosek publikacyjny, ręczna publikacja albo dziennik.
    origin                text NOT NULL,
    request_id            uuid,
    manual_publication_id uuid REFERENCES manual_publications (id),
    -- NASZA rzecz na platformie: `x:<id statusu>` albo `t1_<id>` / `t3_<id>`.
    external_id           text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 100),
    permalink             text NOT NULL CHECK (permalink ~ '^https://' AND length(permalink) <= 2000),
    account               text CHECK (account IS NULL OR length(account) <= 100),
    -- Treść, żeby najlepsze odpowiedzi dało się pokazać bez łączenia z rewizją.
    body                  text NOT NULL DEFAULT '' CHECK (length(body) <= 4000),
    published_at          timestamptz NOT NULL,
    -- Cel odpowiedzi: `x:<id>` albo `t3_<id>` / `t1_<id>`. NULL dla własnego posta.
    target_external_id    text CHECK (target_external_id IS NULL OR length(target_external_id) <= 100),
    target_url            text CHECK (target_url IS NULL OR (target_url ~ '^https://' AND length(target_url) <= 2000)),
    target_author         text CHECK (target_author IS NULL OR length(target_author) <= 100),
    -- Obserwujący autora celu, z chwili odczytu (`facts` mówi skąd i kiedy). NULL: nie odczytano.
    target_followers      integer CHECK (target_followers IS NULL OR target_followers >= 0),
    target_created_at     timestamptz,
    -- Wiek celu w chwili naszej odpowiedzi, w minutach. NULL, gdy nie znamy czasu celu.
    target_age_minutes    integer CHECK (target_age_minutes IS NULL OR target_age_minutes >= 0),
    -- Ile odpowiedzi było pod celem, gdy odpowiedzieliśmy. NULL, gdy nie mamy pomiaru sprzed
    -- publikacji; odczyt zrobiony PO naszej odpowiedzi nie jest tym pomiarem.
    position_replies      integer CHECK (position_replies IS NULL OR position_replies >= 0),
    -- Haczyk z zamkniętej listy i to, kto go nadał: reguła tekstowa albo dziennik założyciela.
    hook                  text,
    hook_source           text,
    mentions_wenext       boolean NOT NULL DEFAULT false,
    has_link              boolean NOT NULL DEFAULT false,
    -- Skąd pochodzi każda cecha i kiedy ją ustalono (`followers_source`, `position_source`...).
    facts                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    -- Ta sama rzecz z dwóch źródeł to jeden wiersz.
    CONSTRAINT learning_posts_one_per_item UNIQUE (organization_id, platform, external_id),
    CONSTRAINT learning_posts_platform_is_known CHECK (platform IN ('X', 'REDDIT')),
    CONSTRAINT learning_posts_kind_is_known CHECK (kind IN ('REPLY', 'OWN_POST')),
    CONSTRAINT learning_posts_origin_is_known CHECK (origin IN ('PUBLICATION', 'MANUAL', 'DIARY')),
    CONSTRAINT learning_posts_hook_is_known CHECK (hook IS NULL OR hook IN (
        'BOL', 'PROWOKACJA', 'DEMO', 'LICZBA', 'PYTANIE', 'NEWS', 'POMOC', 'OPINIA', 'ZART',
        'INNE'
    )),
    CONSTRAINT learning_posts_hook_source_is_known CHECK (
        hook_source IS NULL OR hook_source IN ('CLASSIFIER', 'DIARY')
    ),
    -- Haczyk bez autora etykiety albo autor bez haczyka to wpis, któremu nie wiadomo ufać.
    CONSTRAINT learning_posts_hook_has_its_source CHECK ((hook IS NULL) = (hook_source IS NULL)),
    -- Własny post nie ma celu, a odpowiedź go ma; wiersz z dziennika może go nie znać.
    CONSTRAINT learning_posts_own_post_has_no_target CHECK (
        kind <> 'OWN_POST' OR target_external_id IS NULL
    ),
    CONSTRAINT learning_posts_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    CONSTRAINT learning_posts_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id) MATCH SIMPLE,
    CONSTRAINT learning_posts_point_at_request
        FOREIGN KEY (organization_id, workspace_id, request_id)
        REFERENCES publication_requests (organization_id, workspace_id, id) MATCH SIMPLE
);

CREATE UNIQUE INDEX learning_posts_one_per_request
    ON learning_posts (request_id) WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX learning_posts_one_per_manual_publication
    ON learning_posts (manual_publication_id) WHERE manual_publication_id IS NOT NULL;

CREATE INDEX learning_posts_by_project_and_time
    ON learning_posts (organization_id, project_id, published_at DESC);

-- -----------------------------------------------------------------------------
-- Liczniki w ustalonych momentach
-- -----------------------------------------------------------------------------

CREATE TABLE learning_snapshots (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    post_id          uuid NOT NULL,
    -- Około ilu godzin po publikacji. NULL: odczyt spoza okna każdego horyzontu (dowód, nie dana
    -- do porównań).
    horizon_hours    integer,
    measured_at      timestamptz NOT NULL,
    -- Wiek rzeczy w chwili odczytu, w minutach. Zapisany obok horyzontu: "około 24 h" bywa 19 h.
    age_minutes      integer NOT NULL CHECK (age_minutes >= 0),
    -- Wyświetlenia (X pokazuje je autorowi). NULL: nie odczytano, nigdy zero.
    views            bigint CHECK (views IS NULL OR views >= 0),
    -- X: polubienia. Reddit: punkty, które bywają ujemne.
    likes            integer,
    replies          integer CHECK (replies IS NULL OR replies >= 0),
    reposts          integer CHECK (reposts IS NULL OR reposts >= 0),
    source           text NOT NULL,
    raw              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT learning_snapshots_horizon_is_known CHECK (
        horizon_hours IS NULL OR horizon_hours IN (1, 6, 24, 72)
    ),
    CONSTRAINT learning_snapshots_source_is_known CHECK (
        source IN ('CAPSULE', 'FXTWITTER', 'OBSERVATION', 'DIARY')
    ),
    CONSTRAINT learning_snapshots_read_something CHECK (
        views IS NOT NULL OR likes IS NOT NULL OR replies IS NOT NULL OR reposts IS NOT NULL
    ),
    CONSTRAINT learning_snapshots_belong_to_post
        FOREIGN KEY (organization_id, post_id)
        REFERENCES learning_posts (organization_id, id) ON DELETE CASCADE
);

-- JEDNA MIGAWKA NA RZECZ I HORYZONT. Rozstrzyga ten indeks, nie sprawdzenie "czy już jest":
-- dwa przeglądy w tej samej chwili oba widzą pustkę i oba piszą.
CREATE UNIQUE INDEX learning_snapshots_one_per_horizon
    ON learning_snapshots (post_id, horizon_hours) WHERE horizon_hours IS NOT NULL;
-- I jeden odczyt jednego źródła w jednej chwili nie jest dwoma dowodami.
CREATE UNIQUE INDEX learning_snapshots_one_per_reading
    ON learning_snapshots (post_id, measured_at, source);
CREATE INDEX learning_snapshots_by_post
    ON learning_snapshots (organization_id, post_id, measured_at);

COMMIT;
