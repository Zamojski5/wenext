-- =============================================================================
-- 0036. OKAZJE: ROZMOWY NA ŻYWO, KTÓRE AGENT UZNAŁ ZA WARTE UWAGI ZAŁOŻYCIELA (D-055).
--
-- Założyciel o wtyczce 10.10: "napisał coś, ale nie dał realnej wartości, nie rozwiązał mojego
-- problemu z dystrybucją". Wartość to prawdziwi ludzie, którzy TERAZ mają problem, który rozwiązuje
-- jego kod, i gotowa odpowiedź w jego głosie. Agent szuka ich na żywo (`reddit.search`, `x.search`),
-- a to, co uzna za wartę uwagi, zapisuje tutaj, żeby strona "Pokój" miała co pokazać i żeby
-- następna sesja nie szukała tych samych rozmów od nowa.
--
-- JEDNA TABELA, I TYLKO FAKTY O ROZMOWIE PLUS DWA ZDANIA AGENTA. Rozmowa to platforma, adres,
-- identyfikator, społeczność, tytuł, fragment, autor, czas, liczniki. Dwa zdania agenta to
-- `reason` (czemu to ważne, w JSON-ie `dlaczego`) i `angle` (pod jakim kątem odpowiedzieć,
-- w JSON-ie `kat`). Nic więcej: szkic jest zwykłą propozycją z istniejącej ścieżki zgody,
-- a nie polem tej tabeli.
--
-- STAN OKAZJI NIE MA SWOJEJ KOLUMNY, i to jest decyzja, nie przeoczenie. NOWA, SZKIC i ODPOWIEDZIANO
-- wynikają z tego, co już jest w bazie: czy istnieje żywa rewizja szkicu o tym samym celu i czy
-- istnieje opublikowany wniosek o tym samym celu. Kolumna `status` byłaby drugim źródłem tej samej
-- prawdy, a publikator (którego ta migracja nie dotyka) nigdy by jej nie zaktualizował. Jedyny stan,
-- którego z danych wyprowadzić się nie da, to decyzja człowieka albo agenta "pomijam", więc tylko
-- ona jest zapisana (`dismissed_at` razem z autorem decyzji).
--
-- IDENTYFIKATOR TO CEL ODPOWIEDZI W TEJ POSTACI, W JAKIEJ STOI W `publication_requests`:
-- `t3_<id>` dla posta Reddita, `x:<id statusu>` dla posta X. Dzięki temu okazja łączy się ze
-- szkicem i z publikacją zwykłą równością napisów, tak samo jak łączy je `target_for` i
-- `_draft_already_proposed`, a nie drugim słownikiem, który rozjechałby się przy pierwszej zmianie.
--
-- LICZNIKI TO POMIAR, NIE ZAŁOŻENIE. `score` i `num_comments` bywają NULL: platforma nie pokazała.
-- NULL znaczy "nie odczytano", nigdy zero (ta sama zasada co w odczytach publikacji i w 0035).
-- `created_at_source` bywa NULL z tego samego powodu: post X odczytany bez daty nie dostaje
-- daty wymyślonej przez nas.
-- =============================================================================

BEGIN;

CREATE TABLE project_opportunities (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES organizations (id),
    workspace_id        uuid NOT NULL,
    project_id          uuid NOT NULL,
    platform            text NOT NULL,
    -- Cel odpowiedzi: `t3_<id>` (Reddit) albo `x:<id statusu>` (X). Kształt pilnuje CHECK niżej.
    external_id         text NOT NULL,
    url                 text NOT NULL CHECK (url ~ '^https://' AND length(url) <= 2000),
    -- Reddit: `r/<nazwa>`. X nie ma społeczności, więc tam stoi `X`.
    community           text NOT NULL CHECK (length(btrim(community)) BETWEEN 1 AND 100),
    -- X nie ma tytułów, więc pusty tytuł jest dopuszczony; ekran pokazuje wtedy początek treści.
    title               text NOT NULL DEFAULT '' CHECK (length(title) <= 500),
    excerpt             text NOT NULL DEFAULT '' CHECK (length(excerpt) <= 4000),
    author              text CHECK (author IS NULL OR length(author) <= 100),
    created_at_source   timestamptz,
    score               integer,
    num_comments        integer CHECK (num_comments IS NULL OR num_comments >= 0),
    -- Dwa zdania agenta. `reason` jest obowiązkowe: okazja bez uzasadnienia to link, a nie wybór.
    reason              text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
    angle               text NOT NULL DEFAULT '' CHECK (length(angle) <= 1000),
    -- Sesja, która ją zaznaczyła; NULL dla wpisu spoza sesji (test, operator).
    marked_by_session   uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- Decyzja "pomijam", i kto ją podjął: `operator:<id>` (przycisk na stronie) albo
    -- `session:<id>` (agent). Para jest wszystko albo nic: pominięcie bez autora to anonimowa decyzja.
    dismissed_at        timestamptz,
    dismissed_by        text CHECK (dismissed_by IS NULL OR length(dismissed_by) BETWEEN 1 AND 200),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT project_opportunities_platform_is_known CHECK (platform IN ('REDDIT', 'X')),
    CONSTRAINT project_opportunities_id_matches_platform CHECK (
        CASE platform
            WHEN 'REDDIT' THEN external_id ~ '^t3_[a-z0-9]{1,12}$'
            ELSE external_id ~ '^x:[0-9]{1,25}$'
        END
    ),
    CONSTRAINT project_opportunities_dismissal_names_its_author CHECK (
        (dismissed_at IS NULL) = (dismissed_by IS NULL)
    ),
    -- Jedna rozmowa to jedna okazja w projekcie: zaznaczenie jej drugi raz jest tą samą okazją,
    -- a nie drugą pozycją na liście założyciela.
    CONSTRAINT project_opportunities_one_per_conversation
        UNIQUE (organization_id, project_id, platform, external_id),
    CONSTRAINT project_opportunities_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Strona "Pokój" czyta najświeższe rozmowy projektu.
CREATE INDEX project_opportunities_by_project_and_time
    ON project_opportunities (organization_id, project_id, created_at_source DESC NULLS LAST);

COMMIT;
