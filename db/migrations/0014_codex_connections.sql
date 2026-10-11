-- =============================================================================
-- Migracja 0014 — połączenia Codex (P1 etap 2, D-040)
--
-- WIZJA-WORKSPACE: „Dashboard ma pozwalać podłączyć kolejne konto z subskrypcją Codex/ChatGPT
-- oraz wskazać połączenie dostępne dla danego workspace'u lub sesji.” Połączenie jest dostępem
-- do SILNIKA, nie kontem społecznościowym; te dwie rzeczy nigdy nie mogą wyglądać jak jedna
-- (ZAKRES-FUNKCJONALNY §2, rozróżnienie nr 1).
--
-- DLACZEGO `workspace_id` JEST NULLOWALNE, wbrew trójce tenantowej z D-020.
-- „Połączenie może służyć wielu sesjom zgodnie z dostępnymi limitami; limity konta nie stają się
-- osobnym przydziałem dla każdego workspace'u.” NULL znaczy „dostępne dla każdego workspace'u tej
-- organizacji”, wartość zawęża do jednego. Izolację kontekstu robi sesja, nie połączenie. To ten
-- sam wzorzec, co `jobs` i `audit_events`: tabela o MIESZANYM scope, wpisana do
-- `MIXED_SCOPE_TABLES` z powodem, a nie do `WORKSPACE_TABLES`. Klucz złożony do workspace'u jest
-- MATCH SIMPLE, więc NULL przechodzi bez sprawdzania, a `organization_id` i tak ma własny klucz.
--
-- CO TRZYMAMY, A CZEGO NIE. Tu nie ma ani jednego pola z poświadczeniem. `auth.json` zostaje tam,
-- gdzie umie go czytać Codex: w katalogu połączenia, z prawami 0600, przy
-- `cli_auth_credentials_store = "file"`. Baza trzyma WSKAŹNIK na ten katalog (`home_ref`) i to,
-- co app-server o koncie powiedział. Token w kolumnie byłby tokenem w kopii zapasowej i w każdym
-- zrzucie diagnostycznym.
--
-- `home_ref` JEST ŚCIEŻKĄ WZGLĘDNĄ POD KORZENIEM WOLUMENU i CHECK ogranicza ją do dwóch kształtów:
-- `'.'` (korzeń, czyli zastane konto założyciela, na którym stoi pilot) albo UUID (podkatalog
-- nowego połączenia). To jest granica przed wyjściem ze ścieżki: bez niej wiersz z `../..`
-- kazałby runtime'owi otworzyć katalog spoza wolumenu.
--
-- Migracja zakłada WYŁĄCZNIE schemat. Ani jednego wiersza: połączenie zerowe powstaje dopiero
-- wtedy, gdy człowiek naciśnie „Przyjmij jako połączenie”, bo wiersz utworzony automatycznie
-- twierdziłby, że ktoś to połączenie zarejestrował.
-- =============================================================================

BEGIN;

CREATE TABLE codex_connections (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    -- NULL = dostępne dla każdego workspace'u organizacji. Patrz nagłówek.
    workspace_id     uuid,
    -- Jak w 0013: goły `btrim` obcina tylko spacje, więc `E'\t'` byłby „nazwą".
    label            text NOT NULL CHECK (length(btrim(label, E' \t\r\n')) > 0),
    home_ref         text NOT NULL,
    status           text NOT NULL DEFAULT 'PENDING_LOGIN',
    -- Odczytane z `account/read`. NULL znaczy „nie odczytano” albo „app-server nie ujawnił”:
    -- schemat przypiętej binarki dopuszcza `email: null` PRZY ZALOGOWANYM koncie, więc pusta
    -- kolumna nie dowodzi braku logowania i UI nie ma prawa tak jej czytać.
    account_email    text,
    plan_type        text,
    -- Z katalogu połączenia (`installation_id`). Różne per CODEX_HOME — zmierzone 19.09.
    installation_id  text,
    -- Snapshot `model/list` RAZEM z flagą kompletności: paginacja musi dojść do
    -- `nextCursor = null`, inaczej routing nie ma prawa niczego wybrać (L2189).
    models           jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- `account/rateLimits/read` per pole. W przypiętym kontrakcie jedynym wymaganym polem okna
    -- jest `usedPercent`; `resetsAt` i `windowDurationMins` bywają nieobecne, więc „nie wiemy”
    -- jest stanem POJEDYNCZEGO pola, nie tylko całego połączenia.
    rate_limits      jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Otwarta próba logowania device-code: `{login_id, verification_url, user_code, started_at,
    -- deadline_at}`, albo `{}` gdy żadna nie trwa. TRZY powody, dla których to musi być w bazie,
    -- a nie w pamięci procesu:
    --   * `login_id` jest jedynym argumentem `account/login/cancel`. Start logowania robi job,
    --     a anulowanie naciska człowiek w innym żądaniu HTTP; bez trwałego `login_id` anulowania
    --     nie da się wykonać w ogóle,
    --   * kod i adres są tym, CO CZŁOWIEK MA PRZEPISAĆ. Przepływ, w którym znikają po
    --     odświeżeniu strony, jest nie do użycia,
    --   * `deadline_at` pozwala pokazać, ile czasu zostało, zamiast kazać zgadywać.
    -- To NIE jest poświadczenie: kod jest jednorazowy, wygasa i bez człowieka kończącego
    -- logowanie w przeglądarce nie otwiera niczego. Poświadczenie zostaje w auth.json.
    login_ticket     jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_read_at     timestamptz,
    -- Treść odmowy, np. „device code login wyłączony na tym koncie”. Pokazujemy ją wprost,
    -- zamiast zgadywać powód.
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    disconnected_at  timestamptz,
    UNIQUE (organization_id, id),
    CONSTRAINT codex_connections_status_is_known CHECK (status IN (
        'PENDING_LOGIN', 'CONNECTED', 'RATE_LIMITED', 'DISCONNECTED', 'ERROR'
    )),
    -- Korzeń wolumenu albo podkatalog o nazwie UUID. Nic innego, żeby `home_ref` nie mógł
    -- wyprowadzić runtime'u poza wolumen.
    CONSTRAINT codex_connections_home_ref_is_safe CHECK (
        home_ref = '.'
        OR home_ref ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    -- MATCH SIMPLE: przy `workspace_id IS NULL` klucz jest spełniony bez sprawdzania, i o to
    -- chodzi. Przy wartości nie wolno wskazać workspace'u obcej organizacji.
    CONSTRAINT codex_connections_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

-- Dwa ŻYWE połączenia nie mogą wskazywać tego samego katalogu. L2159 zabrania dwóch App Serverów
-- na jednym CODEX_HOME, a `supervisor.py` pilnuje tego plikiem lease dopiero w trakcie pracy —
-- ten indeks nie dopuszcza do powstania takiej pary w ogóle. Bez organizacji w kluczu celowo:
-- korzeń wolumenu jest jeden na całe wdrożenie, więc dwie organizacje z `'.'` wskazywałyby
-- fizycznie ten sam katalog.
CREATE UNIQUE INDEX codex_connections_one_live_per_home
    ON codex_connections (home_ref)
    WHERE disconnected_at IS NULL;

CREATE INDEX codex_connections_by_organization
    ON codex_connections (organization_id, created_at);

COMMIT;
