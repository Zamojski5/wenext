-- =============================================================================
-- Migracja 0002 — naprawy migracji 0001 i tabela operatora
--
-- 0001 JEST JUŻ ZASTOSOWANA na bazach deweloperskich. Nie wolno jej edytować
-- w miejscu: plik przepisany rozjeżdża się z każdą bazą, która wykonała wersję
-- poprzednią, a `apply_migrations.sh` nie odtwarza schematu od zera. Wszystko,
-- co 0001 zrobiła źle, naprawia się TUTAJ.
--
-- Zawartość, punkt po punkcie, każdy z powodem:
--
--  1. Dwanaście polskich identyfikatorów → angielskie (D-010). To są CZTERY
--     różne katalogi systemowe, a `ALTER TABLE ... RENAME CONSTRAINT` nie dotyka
--     ani indeksów, ani funkcji, ani triggerów. Wersja tej migracji operująca
--     wyłącznie na constraintach zostawiłaby OSIEM z dwunastu nazw na miejscu.
--  2. `capability_snapshots.required_browser_mode` → NOT NULL. Dziś kolumna jest
--     nullable dwie linie POD komentarzem twierdzącym, że nie jest, a CHECK
--     `manual_only_is_not_headless` przy NULL-u zwraca NULL — czyli PRZEPUSZCZA
--     wiersz. Kontrola DoD F0 „każda capability ma zapisany required_browser_mode”
--     (L5177) była więc liczona nad wierszami, które mogły trybu nie mieć.
--  3. Statement-level BEFORE TRUNCATE na `audit_events`. Trigger z 0001 jest
--     FOR EACH ROW, a TRUNCATE nie odpala triggerów wierszowych — dziennik
--     „append-only” dawał się wyczyścić jednym poleceniem, bez błędu.
--  4. Composite FK dla `audit_events.workspace_id`, dziś gołego uuid bez klucza.
--     MATCH SIMPLE, bo kolumna jest nullable i taka zostaje: D-020 zakazuje
--     retrofitu trójki tenantowej na tabele z 0001, a event organizacyjny musi
--     dać się zapisać (0001 je zapisuje — L2448).
--  5. `browser_profiles.encrypted_storage_ref` (L3730) — kolumny w 0001 nie ma.
--  6. Tabela `operators` (D-016): hash Argon2id i JEDEN operator wymuszony
--     SCHEMATEM przez partial unique, nie kodem aplikacji.
--
-- Czego tu NIE MA i być nie może: kolejki jobów i łańcucha zgody (to 0003),
-- RLS i ról login (E3, L5206), retrofitu `workspace_id` na tabele z 0001 (D-020).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1a. pg_constraint — cztery CHECK-i
-- -----------------------------------------------------------------------------
-- Nazwy constraintów nie występują w żadnym warunku SQL ani w żadnym `match=`
-- w testach (sprawdzone grepem po całym repo przed zmianą): testy F0 łapią klasy
-- wyjątków asyncpg, nie nazwy. Jedyne pozostałe odwołania są w dokumentach
-- `docs/threat-model/` i w komentarzu w `services/profile_lease/tests/` — pliki
-- spoza tej migracji, zgłoszone orkiestratorowi.

ALTER TABLE browser_sessions
    RENAME CONSTRAINT headless_bez_displayu TO headless_has_no_display;

ALTER TABLE stream_sessions
    RENAME CONSTRAINT control_wymaga_interaction_lease TO control_requires_interaction_lease;

ALTER TABLE stream_sessions
    RENAME CONSTRAINT ttl_biletu_nie_dluzszy_niz_sesji TO ticket_ttl_within_session_ttl;

ALTER TABLE capability_snapshots
    RENAME CONSTRAINT manual_only_nie_jest_headless TO manual_only_is_not_headless;

-- -----------------------------------------------------------------------------
-- 1b. pg_class (relkind 'i') — sześć indeksów
-- -----------------------------------------------------------------------------
-- Indeksy to osobny katalog. `RENAME CONSTRAINT` powyżej nie zmieniło ani jednej
-- z tych nazw, mimo że dwa z tych indeksów realizują niezmienniki opisane
-- w tych samych zdaniach dokumentu.

ALTER INDEX browser_profiles_jeden_aktywny_na_konto
    RENAME TO browser_profiles_one_active_per_account;

ALTER INDEX profile_leases_jeden_aktywny_na_profil
    RENAME TO profile_leases_one_active_per_profile;

ALTER INDEX profile_leases_generacja_unikalna
    RENAME TO profile_leases_generation_is_unique;

ALTER INDEX interaction_leases_jeden_aktywny_na_sesje
    RENAME TO interaction_leases_one_active_per_session;

ALTER INDEX audit_events_wg_czasu RENAME TO audit_events_by_time;

ALTER INDEX audit_events_wg_rodzaju RENAME TO audit_events_by_kind;

-- -----------------------------------------------------------------------------
-- 1c. pg_proc i pg_trigger — funkcja audytu i jej trigger, przez DROP + CREATE
-- -----------------------------------------------------------------------------
-- DROP + CREATE, a nie ALTER ... RENAME TO, bo zmienia się nie tylko nazwa:
-- ta sama funkcja obsługuje teraz również TRUNCATE (punkt 3), a trigger wierszowy
-- z 0001 nie może tego robić. Kolejność jest istotna — trigger zależy od funkcji.
--
-- TREŚĆ KOMUNIKATU JEST CZĘŚCIĄ KONTRAKTU. Test
-- `services/profile_lease/tests/test_lease_fencing.py::test_audit_is_append_only`
-- dopasowuje go przez `pytest.raises(match="append-only")`, więc słowo
-- „append-only” musi w nim zostać. Komunikat po polsku zgodnie z D-010:
-- komunikaty błędów czyta operator.

DROP TRIGGER audit_events_bez_zmian ON audit_events;
DROP FUNCTION audit_events_tylko_dopisywanie();

CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events jest append-only: % jest niedozwolone', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_no_mutation
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

-- -----------------------------------------------------------------------------
-- 3. TRUNCATE na dzienniku audytu
-- -----------------------------------------------------------------------------
-- FOR EACH STATEMENT, bo TRUNCATE nie ma wierszy do obejrzenia — trigger
-- wierszowy nie odpala się w ogóle i 0001 dawało się opróżnić jednym poleceniem.
-- To jest ten sam wzorzec błędu, co kontrola bez pomiaru: nic nie protestowało.

CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();

-- -----------------------------------------------------------------------------
-- 2. required_browser_mode NOT NULL
-- -----------------------------------------------------------------------------
-- Świadomie BEZ backfillu. Wiersz z NULL-em to capability, dla której trybu
-- przeglądarki NIKT NIE ZMIERZYŁ; wpisanie tu 'HEADLESS' albo 'INTERACTIVE'
-- byłoby wyprodukowaniem dowodu na kontrolę DoD L5177, czyli dokładnie tym,
-- czego D-007 zakazuje. Jeżeli `SET NOT NULL` padnie na czyjejś bazie, to jest
-- informacja („masz wiersze bez zmierzonego trybu”), a nie usterka migracji:
-- takie wiersze trzeba usunąć albo przemierzyć ręcznie.

ALTER TABLE capability_snapshots ALTER COLUMN required_browser_mode SET NOT NULL;

-- -----------------------------------------------------------------------------
-- 4. Composite FK dla audit_events.workspace_id
-- -----------------------------------------------------------------------------
-- MATCH SIMPLE (domyślne, wpisane jawnie, żeby nikt nie „poprawił” tego na
-- MATCH FULL): przy NULL-owej kolumnie w kluczu wielokolumnowym MATCH SIMPLE
-- uznaje klucz za spełniony, więc event organizacyjny (`workspace_id IS NULL`)
-- pozostaje legalny, a workspace z OBCEJ organizacji jest odrzucany przez bazę.
-- MATCH FULL zablokowałby każdy event organizacyjny, a 0001 takie zapisuje.
--
-- Trójki tenantowej tu nie ma i być nie może — D-020 zakazuje retrofitu
-- `workspace_id` na tabele z 0001. To jest para, bo `audit_events` jest tabelą
-- o mieszanym scope, nie tabelą workspace'ową.

ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_workspace_belongs_to_organization
    FOREIGN KEY (organization_id, workspace_id)
    REFERENCES workspaces (organization_id, id) MATCH SIMPLE;

-- Bez indeksu każdy DELETE na `workspaces` skanowałby cały dziennik. W F1 audyt
-- rośnie jako jedyna tabela bez limitu.
CREATE INDEX audit_events_by_workspace
    ON audit_events (organization_id, workspace_id, occurred_at DESC);

-- -----------------------------------------------------------------------------
-- 5. browser_profiles.encrypted_storage_ref (L3730)
-- -----------------------------------------------------------------------------
-- NULLABLE, i to jest ograniczenie zapisane wprost, nie przeoczenie. NOT NULL
-- wymagałoby albo DEFAULT-u dla kolumny, której treścią jest uchwyt do
-- zaszyfrowanego storage'u profilu (D-018) — czyli wartości wymyślonej — albo
-- przepisania osiemnastu testów F0 lease'u, których INSERT-y są starsze od tej
-- kolumny. NULL znaczy „storage jeszcze nie utworzony”.
--
-- CHECK istnieje po to, żeby pusty string nie udawał uchwytu: NULL to stan,
-- '' to nic.

ALTER TABLE browser_profiles ADD COLUMN encrypted_storage_ref text;

ALTER TABLE browser_profiles
    ADD CONSTRAINT encrypted_storage_ref_is_not_empty
    CHECK (encrypted_storage_ref IS NULL OR length(encrypted_storage_ref) > 0);

-- -----------------------------------------------------------------------------
-- 6. operators (D-016)
-- -----------------------------------------------------------------------------
-- „single-operator auth boundary” (L5191) występuje w dokumencie DOKŁADNIE RAZ
-- i nigdzie nie jest rozwinięte. D-016 wybiera mechanizm: hasło weryfikowane
-- wobec wiersza w tej tabeli, hash Argon2id, sesja jako ciasteczko HttpOnly.
--
-- Dwie rzeczy są tu wymuszone SCHEMATEM, bo w kodzie byłyby regułą do zapomnienia:
--
--   * JEDEN aktywny operator — partial unique na (organization_id) WHERE
--     disabled_at IS NULL. Nie CHECK-iem i nie triggerem liczącym wiersze:
--     partial unique jest jedynym z tych trzech, który wytrzymuje dwa równoległe
--     INSERT-y (ta sama lekcja co przy `profile_leases_one_active_per_profile`).
--   * Argon2id, nie „jakiś hash” — CHECK na prefiksie postaci zakodowanej.
--     Bez niego stub wpisujący hasło jawnym tekstem albo digest bcrypta
--     działałby, a decyzja mieszkałaby wyłącznie w dokumencie.
--
-- ŻADNEJ KOLUMNY ROLI. L4253 i L4361 zakazują RBAC w E1, a kolumna `role` jest
-- miejscem, w którym RBAC odrasta. D-016 mówi to wprost.

CREATE TABLE operators (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    -- Postać zakodowana Argon2id: `$argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>`.
    credential_hash  text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Wyłączenie zamiast DELETE: operator, którego już nie ma, ale który podpisał
    -- approvale, musi zostać celem klucza obcego z `approval_decisions`.
    disabled_at      timestamptz,
    UNIQUE (organization_id, id),
    CONSTRAINT credential_hash_is_argon2id CHECK (credential_hash LIKE '$argon2id$%'),
    CONSTRAINT disabled_after_created CHECK (disabled_at IS NULL OR disabled_at >= created_at)
);

CREATE UNIQUE INDEX operators_one_enabled_per_organization
    ON operators (organization_id)
    WHERE disabled_at IS NULL;

COMMIT;
