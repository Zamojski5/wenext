-- =============================================================================
-- Migracja 0013 — projekty, profile osób i przypisania kont (P1, D-040)
--
-- ROADMAP-PIVOT P1: „tworzę workspace i projekt, dodaję profile »Anna« i »Jan«,
-- podpinam pod nie konkretne konta platform i przypisuję wybrane konta do projektu”.
-- Cztery tabele i ani jednej więcej; połączenia Codex są osobnym zasobem (migracja 0014).
--
-- Trzy rzeczy, których ta migracja świadomie NIE robi:
--
--   1. Nie dotyka `social_accounts`. Konto platformy jest zasobem ORGANIZACJI od 0001
--      (`UNIQUE (organization_id, platform, external_account_id)`), a nie workspace'u.
--      Do workspace'u przywiązuje je dopiero aktywne przypięcie w `person_profile_accounts`.
--      Retrofit `workspace_id` na tabelę z 0001 jest wprost zabroniony (D-020).
--   2. Nie zapisuje w bazie macierzy możliwości platform. „REDDIT czyta i komentuje, HN tylko
--      czyta, X i LINKEDIN nie mają integracji” to fakt o KODZIE tego repozytorium, nie o danych
--      użytkownika: wiersz w bazie mówiłby, że integracja istnieje, także wtedy, gdy adaptera
--      nie ma. Macierz mieszka w `control_plane/workspace/platforms.py`.
--   3. Nic nie usuwa. Odpięcie konta od profilu i zdjęcie przypisania z projektu ustawiają
--      znacznik czasu; wiersz zostaje. P1 wymaga wprost: „odłączenie nie usuwa historii”.
--
-- Aktywność jest wymuszona PARTIAL UNIQUE INDEX-em, nie CHECK-iem i nie kodem. Z trzech
-- mechanizmów tylko indeks przeżywa dwa równoległe INSERT-y — ten sam powód, dla którego
-- 0001 pilnuje tak „najwyżej jeden nieretired BrowserProfile na konto”.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Projekt — cel z trybem pracy
-- -----------------------------------------------------------------------------
-- Rodzicem jest sam workspace, więc klucz tenantowy jest PARĄ
-- `(organization_id, workspace_id) -> workspaces (organization_id, id)`, tak jak w
-- `communities` z 0004. Trójka obowiązuje dzieci mające rodzica workspace'owego.

CREATE TABLE projects (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    -- `btrim(x)` BEZ DRUGIEGO ARGUMENTU OBCINA WYŁĄCZNIE SPACJE. Zmierzone przez sesję P2:
    -- `VALUES (E'\t')` przechodziło CHECK `length(btrim(name)) > 0` i zostawiało wiersz, który
    -- na ekranie jest pustą linią nie do odróżnienia od następnej. Lista znaków jest jawna.
    name             text NOT NULL CHECK (length(btrim(name, E' \t\r\n')) > 0),
    -- Cel powierzony agentowi, własnymi słowami użytkownika. Pusty jest dozwolony: projekt
    -- można założyć i dopowiedzieć cel rozmową (ZAKRES-FUNKCJONALNY §4 P1, kroki odkładalne).
    goal             text NOT NULL DEFAULT '',
    -- Zamknięta lista jako text + nazwany CHECK, nigdy CREATE TYPE: `ALTER TYPE ... ADD VALUE`
    -- nie działa w transakcji i jest nieodwracalny (db/migrations/README.md).
    mode             text NOT NULL DEFAULT 'AUTONOMOUS',
    -- Wstrzymanie pracy agenta. Osobne od `archived_at`: wstrzymany projekt wraca, zarchiwizowany
    -- znika z listy. ZAKRES-FUNKCJONALNY §3F żąda obu jako osobnych stanów.
    paused_at        timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    archived_at      timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT projects_mode_is_known CHECK (mode IN ('AUTONOMOUS', 'REVIEW', 'RESEARCH')),
    CONSTRAINT projects_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

CREATE INDEX projects_by_workspace
    ON projects (organization_id, workspace_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- Profil osoby lub marki — reprezentowana tożsamość
-- -----------------------------------------------------------------------------
-- WIZJA-WORKSPACE: „Anna i Jan mają osobne profile, pod nimi np. Reddit, X i LinkedIn”.
-- Profil należy do workspace'u, bo to jego wiedza i głos nie mogą się mieszać między
-- przestrzeniami. `user_profile_versions` z 0004 jest czymś innym — to głos założyciela dla
-- pilota — i ta migracja go nie rusza.

CREATE TABLE person_profiles (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    workspace_id     uuid NOT NULL,
    -- Ten sam powód, co w `projects.name` powyżej: goły `btrim` zostawia tabulator.
    name             text NOT NULL CHECK (length(btrim(name, E' \t\r\n')) > 0),
    kind             text NOT NULL DEFAULT 'PERSON',
    created_at       timestamptz NOT NULL DEFAULT now(),
    archived_at      timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT person_profiles_kind_is_known CHECK (kind IN ('PERSON', 'BRAND')),
    CONSTRAINT person_profiles_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

CREATE INDEX person_profiles_by_workspace
    ON person_profiles (organization_id, workspace_id, created_at);

-- -----------------------------------------------------------------------------
-- Przypięcie konta platformy do profilu osoby
-- -----------------------------------------------------------------------------
-- To jest miejsce, w którym konto organizacyjne staje się kontem konkretnej osoby w konkretnym
-- workspace'ie. Reguła P1: „najwyżej jeden aktywny profil na konto”. Gdyby jedno konto mogło
-- wisieć pod dwoma profilami naraz, „dwa profile nie mieszają tożsamości” przestałoby być
-- prawdą na poziomie danych, a nie tylko na ekranie.

CREATE TABLE person_profile_accounts (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL,
    workspace_id       uuid NOT NULL,
    person_profile_id  uuid NOT NULL,
    social_account_id  uuid NOT NULL,
    attached_at        timestamptz NOT NULL DEFAULT now(),
    -- Odpięcie. NULL znaczy „aktywne”; ustawienie znacznika zostawia wiersz jako historię.
    detached_at        timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT person_profile_accounts_point_at_profile
        FOREIGN KEY (organization_id, workspace_id, person_profile_id)
        REFERENCES person_profiles (organization_id, workspace_id, id),
    -- Konto jest organizacyjne, więc tu klucz jest PARĄ i obie kolumny są NOT NULL — pod MATCH
    -- SIMPLE para z kolumną NULL byłaby spełniona BEZ sprawdzenia rodzica (0003, L121-126).
    CONSTRAINT person_profile_accounts_point_at_account
        FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id)
);

-- Sedno reguły „jeden aktywny profil na konto”, w całej organizacji, także ponad workspace'ami:
-- konto reprezentuje jedną tożsamość i nie może być jednocześnie Anną w jednej przestrzeni
-- i Janem w drugiej.
CREATE UNIQUE INDEX person_profile_accounts_one_active_per_account
    ON person_profile_accounts (social_account_id)
    WHERE detached_at IS NULL;

CREATE INDEX person_profile_accounts_by_profile
    ON person_profile_accounts (person_profile_id, attached_at DESC);

-- -----------------------------------------------------------------------------
-- Przypisanie konta do projektu
-- -----------------------------------------------------------------------------
-- „Projekt może korzystać z wybranych kont” — wiele kont w jednym projekcie i to samo konto
-- w kilku projektach są oba poprawne. Unikalność dotyczy więc PARY (projekt, konto), a nie
-- samego konta: inaczej przypisanie do drugiego projektu zabierałoby konto pierwszemu.

CREATE TABLE project_account_assignments (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL,
    workspace_id       uuid NOT NULL,
    project_id         uuid NOT NULL,
    social_account_id  uuid NOT NULL,
    assigned_at        timestamptz NOT NULL DEFAULT now(),
    unassigned_at      timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT project_account_assignments_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id),
    CONSTRAINT project_account_assignments_point_at_account
        FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id)
);

CREATE UNIQUE INDEX project_account_assignments_one_active_per_pair
    ON project_account_assignments (project_id, social_account_id)
    WHERE unassigned_at IS NULL;

CREATE INDEX project_account_assignments_by_project
    ON project_account_assignments (project_id, assigned_at DESC);

COMMIT;
