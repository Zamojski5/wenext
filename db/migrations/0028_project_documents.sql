-- =============================================================================
-- Migracja 0028: dokumenty projektu jako WIDOK na rejestr ustaleń (P8 etap A, D-043)
--
-- CZYM TO NIE JEST: drugą pamięcią. Rejestr `knowledge_items` (0016, 0027) pozostaje jedynym
-- źródłem prawdy i ta migracja nie zmienia w nim ani jednego więzu. Dokument jest tym, co z
-- rejestru WYNIKA, zapisanym w kolejności, w jakiej człowiek i agent mają to czytać.
--
-- PO CO W OGÓLE, skoro wszystko jest w rejestrze. Sesja dostawała na starcie liczbę ustaleń
-- („Ustaleń w pamięci projektu: 34”) i musiała sama zgadnąć, o co zapytać. Liczba bez treści nie
-- zmieniła jeszcze żadnej decyzji. Dokument o stałej roli („karta projektu”, „co działa”,
-- „decyzje”, „społeczności”) jest czymś, co da się przeczytać od indeksu i zastosować, a przy
-- każdym zdaniu widać, z czego ono wynika.
--
-- TRZY TABELE, BO TRZY RÓŻNE ŻYCIA:
--
--   1. `project_documents`, czyli TOŻSAMOŚĆ. Jeden wiersz na rolę w projekcie, żyje tak długo
--      jak projekt. To on ma stabilny identyfikator, którym posługuje się ekran i narzędzie.
--   2. `project_document_versions`, czyli TREŚĆ W CZASIE. Wersja jest NIEZMIENNA, poprawka to nowa
--      wersja. Ten sam wzór, co `person_voice_versions` (0022) i `community_voice_cards` (0005):
--      dokument, którego nie da się porównać z poprzednim, nie pokazuje, co się zmieniło.
--   3. `project_document_sentences`, czyli ZDANIA Z PODSTAWĄ. Każde zdanie wskazuje ustalenia, z
--      których wynika, i ta lista NIE MOŻE być pusta. To jest cały mechanizm, który nie pozwala
--      dokumentowi zacząć nieść treści spoza rejestru: zdanie bez podstawy po prostu nie da się
--      zapisać. Bez tego więzu „rejestr jest źródłem prawdy” byłoby obietnicą na słowo.
--
-- DLACZEGO PODSTAWA TO TABLICA uuid, A NIE TABELA ŁĄCZĄCA. Zdanie ma od jednego do kilkunastu
-- odsyłaczy i zawsze czyta się je razem ze zdaniem; osobna tabela dawałaby czwarty byt i JOIN
-- przy każdym odczycie. Cena jest zapisana wprost: PostgreSQL nie zna kluczy obcych z elementów
-- tablicy, więc istnienie ustaleń sprawdza generator (`documents/generator.py`) przy zapisie,
-- w tej samej transakcji. Tablica pusta jest zabroniona więzem, bo to jedyna część, której
-- sprawdzenie w kodzie byłoby sprawdzeniem po fakcie.
--
-- ODCISK WEJŚCIA (`input_sha256`) JEST TU OD POCZĄTKU, choć job przepisujący dokumenty wchodzi
-- dopiero w etapie B. To skrót z posortowanych identyfikatorów i dat ustaleń, z których wersja
-- powstała. Dzięki niemu dziesięć przebiegów generatora bez zmian w rejestrze daje JEDNĄ wersję,
-- a nie dziesięć. Kolumna bez joba jest tania; job bez kolumny musiałby zgadywać, czy coś się
-- zmieniło, po treści tekstu.
--
-- `project_id` NULLOWALNY, bo podręcznik platformy (etap C) jest dokumentem PRZESTRZENI, nie
-- projektu. Kolumna `platform` jest wypełniona wyłącznie dla niego. Oba stany są opisane
-- więzem, żeby nie powstał dokument projektu z platformą ani podręcznik bez niej.
-- =============================================================================

BEGIN;

CREATE TABLE project_documents (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    -- NULL = dokument przestrzeni (podręcznik platformy). Nie „dowolny projekt”.
    project_id       uuid,
    role             text NOT NULL,
    -- Tylko dla PLATFORM_HANDBOOK. Nazwa platformy z tej samej listy, co `communities.platform`.
    platform         text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT project_documents_role_is_known CHECK (role IN (
        'PROJECT_CARD', 'WHAT_WORKS', 'DECISIONS', 'COMMUNITIES', 'PLATFORM_HANDBOOK'
    )),
    -- Podręcznik platformy: bez projektu, z platformą. Każdy inny dokument: z projektem, bez
    -- platformy. Trzeci układ nie znaczy nic i nie ma go jak czytać.
    CONSTRAINT project_documents_handbook_is_a_workspace_document CHECK (
        (role = 'PLATFORM_HANDBOOK' AND project_id IS NULL AND platform IS NOT NULL)
        OR (role <> 'PLATFORM_HANDBOOK' AND project_id IS NOT NULL AND platform IS NULL)
    ),
    CONSTRAINT project_documents_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    CONSTRAINT project_documents_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Jedna tożsamość na rolę. NULLS NOT DISTINCT, bo inaczej dokumenty przestrzeni (project_id
-- NULL) omijałyby ten indeks w całości i podręczników jednej platformy dałoby się założyć wiele.
CREATE UNIQUE INDEX project_documents_one_per_role
    ON project_documents (organization_id, workspace_id, project_id, role, platform)
    NULLS NOT DISTINCT;

CREATE TABLE project_document_versions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    document_id      uuid NOT NULL,
    version          integer NOT NULL CHECK (version >= 1),
    title            text NOT NULL CHECK (length(btrim(title, E' \t\r\n')) > 0),
    -- Kto to napisał: reguły czy model. Model wolno wołać wyłącznie do skracania, a wtedy wersja
    -- niesie nazwę i odcisk promptu, tak jak obraz społeczności i karta głosu (0005, 0011).
    generated_by     text NOT NULL,
    prompt_name      text,
    prompt_sha256    text,
    -- Skrót z posortowanych identyfikatorów i dat ustaleń, z których ta wersja powstała.
    input_sha256     text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    -- Ile ustaleń weszło do tej wersji. Liczba jest tu, bo ekran pokazuje ją bez czytania zdań.
    item_count       integer NOT NULL CHECK (item_count >= 0),
    generated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, document_id, version),
    CONSTRAINT project_document_versions_generated_by_is_known CHECK (
        generated_by IN ('RULES', 'MODEL')
    ),
    -- Wersja z modelu MUSI powiedzieć, jakim promptem; wersja z reguł nie ma czego podawać.
    CONSTRAINT project_document_versions_model_names_its_prompt CHECK (
        (generated_by = 'MODEL' AND prompt_name IS NOT NULL AND prompt_sha256 ~ '^[0-9a-f]{64}$')
        OR (generated_by = 'RULES' AND prompt_name IS NULL AND prompt_sha256 IS NULL)
    ),
    CONSTRAINT project_document_versions_belong_to_document
        FOREIGN KEY (organization_id, workspace_id, document_id)
        REFERENCES project_documents (organization_id, workspace_id, id)
);

CREATE INDEX project_document_versions_newest_first
    ON project_document_versions (organization_id, document_id, version DESC);

CREATE TABLE project_document_sentences (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    version_id       uuid NOT NULL,
    position         integer NOT NULL CHECK (position >= 0),
    -- Nagłówek, pod którym zdanie stoi. Sekcja pusta jest dozwolona: nie każdy dokument dzieli
    -- się na części, a wymuszony nagłówek byłby napisem wymyślonym po to, żeby wypełnić pole.
    section          text NOT NULL DEFAULT '',
    text             text NOT NULL CHECK (length(btrim(text, E' \t\r\n')) > 0),
    -- Ustalenia, z których to zdanie wynika. NIEPUSTA, i to jest najważniejszy więz tej migracji.
    basis            uuid[] NOT NULL,
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, version_id, position),
    -- `cardinality`, NIE `array_length`, i to jest pułapka, która kosztowała pierwszy przebieg
    -- testu schematu: `array_length('{}', 1)` zwraca NULL, a `NULL >= 1` to NULL, czyli CHECK
    -- PRZEPUSZCZA pustą tablicę. Najważniejszy więz tej migracji nie pilnowałby niczego.
    -- `cardinality` oddaje 0 dla pustej tablicy i porównanie jest wtedy fałszem, czyli odmową.
    CONSTRAINT project_document_sentences_have_a_basis CHECK (
        cardinality(basis) >= 1
    ),
    CONSTRAINT project_document_sentences_belong_to_version
        FOREIGN KEY (organization_id, workspace_id, version_id)
        REFERENCES project_document_versions (organization_id, workspace_id, id)
);

CREATE INDEX project_document_sentences_in_order
    ON project_document_sentences (organization_id, version_id, position);

COMMIT;
