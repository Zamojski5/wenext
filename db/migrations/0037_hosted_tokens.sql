-- =============================================================================
-- 0037. HOSTOWANY MCP: TOKENY ADMINISTRATORA I TOŻSAMOŚCI Z LOGOWANIA OAUTH.
--
-- Hostowany Wenext (11.10, PLAN-MVP) ma dwie drogi wejścia do jednego projektu.
--
-- 1. `hosted_tokens`: osobisty adres `https://<domena>/mcp/<token>`. To droga administratora,
--    testów i zaproszeń: każda osoba dostaje własny adres i wpisuje go jednym poleceniem w Codexie
--    albo Claude Code. Token jest kluczem do dokładnie jednego projektu (organizacja, przestrzeń,
--    projekt), który założyło polecenie `python -m control_plane.hosted.invite create`.
-- 2. `external_identities`: logowanie przez przeglądarkę (OAuth). Agent wysyła
--    `Authorization: Bearer <JWT>`, serwer sprawdza podpis, a para (dostawca, podmiot) wskazuje
--    projekt. Pierwsze zweryfikowane żądanie nieznanej pary zakłada organizację, przestrzeń i
--    projekt samo (bez żadnego tokenu od nas), a równoległe pierwsze żądania zakładają je raz.
--
-- W BAZIE LEŻY TYLKO SKRÓT SHA-256, NIGDY TOKEN. Ta sama zasada co przy ciasteczku sesji i przy
-- zaproszeniach (`invitations.token_hash`): kopia bazy albo zrzut z logu zapytań nie daje
-- nikomu wejścia do cudzego projektu. Token ma 256 bitów losowości, więc zwykły SHA-256 bez soli
-- wystarcza: nie ma czego zgadywać ani łamać słownikiem. Skrót jest kluczem głównym, bo serwer
-- szuka dokładnie po nim, przy każdym żądaniu.
--
-- COFNIĘCIE TO `revoked_at`, NIE DELETE. Wiersz zostaje jako ślad, że taki dostęp istniał, a
-- unieważniony token nigdy nie zacznie znowu działać przez przypadek (nowy dostęp to nowy token).
-- `last_used_at` służy operatorowi do oceny, czy ktoś w ogóle skorzystał z zaproszenia; serwer
-- odświeża go najwyżej raz na minutę, żeby odczyt nie zamieniał się w zapis przy każdym żądaniu.
--
-- TRÓJKA TENANTOWA, NIE PARA (D-020): token wskazuje projekt z organizacją, przestrzenią i
-- identyfikatorem, tak jak 0035 i 0036. Token z organizacji A nie może wskazać projektu z B.
-- =============================================================================

BEGIN;

CREATE TABLE hosted_tokens (
    token_sha256    text PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL,
    project_id      uuid NOT NULL,
    -- Dla operatora: komu wysłano (adres e-mail z zaproszenia). Nigdy sam token.
    label           text NOT NULL DEFAULT '' CHECK (length(label) <= 320),
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz,
    last_used_at    timestamptz,
    CONSTRAINT hosted_tokens_digest_is_sha256_hex CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT hosted_tokens_revoked_after_created CHECK (
        revoked_at IS NULL OR revoked_at >= created_at
    ),
    CONSTRAINT hosted_tokens_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Lista operatora i cofanie po projekcie: ile dostępów ma ten projekt, które są żywe.
CREATE INDEX hosted_tokens_by_project ON hosted_tokens (organization_id, project_id);

-- =============================================================================
-- TOŻSAMOŚĆ ZEWNĘTRZNA: KTO JEST KIM PO STRONIE DOSTAWCY LOGOWANIA.
--
-- `provider` to nazwa dostawcy z konfiguracji serwera (domyślnie adres wystawcy tokenów), a
-- `subject` to niezmienny identyfikator osoby u tego dostawcy (`sub` z JWT). Para jest kluczem
-- głównym: to ona rozstrzyga, że drugie logowanie tej samej osoby trafia do TEGO SAMEGO projektu,
-- a równoległe pierwsze żądania nie zakładają dwóch. Adres e-mail i nazwa są informacją dla
-- operatora, nie kluczem: osoba może zmienić adres u dostawcy, `sub` zostaje.
--
-- NIE MA TU WIERSZA W `users` ANI `memberships`. Osoba wchodzi przez agenta, nie przez dashboard,
-- więc nikt nie potrzebuje hasła ani członkostwa; dostęp do dashboardu dla takiej osoby będzie
-- osobną decyzją. `disabled_at` jest wyłącznikiem operatora (odpowiednik cofnięcia tokenu):
-- wiersz zostaje, a zweryfikowany JWT tej osoby przestaje otwierać cokolwiek.
--
-- TRÓJKA TENANTOWA, jak przy `hosted_tokens`: tożsamość nie wskaże projektu z cudzej organizacji.
-- =============================================================================

CREATE TABLE external_identities (
    provider        text NOT NULL CHECK (length(btrim(provider)) BETWEEN 1 AND 300),
    subject         text NOT NULL CHECK (length(btrim(subject)) BETWEEN 1 AND 255),
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid NOT NULL,
    project_id      uuid NOT NULL,
    email           text CHECK (email IS NULL OR length(email) <= 320),
    display_name    text CHECK (display_name IS NULL OR length(display_name) <= 200),
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz,
    disabled_at     timestamptz,
    PRIMARY KEY (provider, subject),
    CONSTRAINT external_identities_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Jeden projekt ma jedną tożsamość z logowania: to samo `project_id` pod dwiema parami oznaczałoby
-- dwie osoby w jednym projekcie, a hostowany projekt jest jednoosobowy.
CREATE UNIQUE INDEX external_identities_one_per_project
    ON external_identities (organization_id, project_id);

COMMIT;
