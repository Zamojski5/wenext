-- =============================================================================
-- Migracja 0018 — tożsamość użytkownika, organizacje samoobsługowe, eksport i usunięcie
--
-- P7 etap A (ROADMAP-PIVOT: „usługa działa dla kolejnego klienta"). Do tej pory produkt miał
-- JEDNEGO operatora na organizację (`operators`, 0002) i jedną organizację w praktyce. To za
-- mało na drugiego klienta z dwóch powodów naraz: nie da się zaprosić drugiej osoby do tej samej
-- przestrzeni, i nie da się założyć własnej przestrzeni bez konsoli założyciela.
--
-- CO TA MIGRACJA ZMIENIA, A CZEGO NIE RUSZA:
--
--   * `operators` ZOSTAJE i dalej działa. Wiersz założyciela jest przenoszony na `users` jako
--     OWNER swojej organizacji, ale sama tabela i jej klucze obce (`approval_decisions`
--     wskazuje operatora, który podpisał zgodę) pozostają nietknięte. Usunięcie jej byłoby
--     przepisaniem historii zgód, a nie sprzątaniem;
--   * granica dostępu nie zmienia semantyki: obcy workspace dalej daje 404, nie 403. Dochodzi
--     tylko jeden warunek wcześniej — czy pytający ma AKTYWNE członkostwo w organizacji;
--   * trójka tenantowa wszystkich tabel zostaje bez zmian. Nowe tabele są organizacyjne, nie
--     workspace'owe, bo użytkownik należy do organizacji, a nie do przestrzeni roboczej.
--
-- E-MAIL ZAŁOŻYCIELA JEST NIEZNANY BAZIE. Dziś istnieje jeden wiersz `operators` z samym
-- hashem hasła; nikt nigdy nie zapisał, do kogo należy. Dlatego `users.email` jest NULLowalne:
-- przeniesiony operator dostaje konto BEZ e-maila, loguje się jak dotąd hasłem, a interfejs
-- prosi go o uzupełnienie adresu. Wpisanie tu zmyślonego adresu (`zalozyciel@example.com`)
-- byłoby wpisaniem do bazy danych, których nikt nie podał.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Użytkownicy
-- -----------------------------------------------------------------------------

CREATE TABLE users (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL tylko dla kont przeniesionych z `operators` (patrz nagłówek). Każde nowe konto
    -- powstaje z adresem, bo powstaje przez rejestrację albo przez zaproszenie.
    email            text,
    -- Ta sama postać, co w `operators`: Argon2id. CHECK jest tu po to samo, po co tam —
    -- plaintext albo bcrypt nie ma jak wejść.
    credential_hash  text NOT NULL CHECK (credential_hash LIKE '$argon2id$%'),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Wyłączenie zamiast DELETE, jak przy operatorach: użytkownik, który zapraszał albo
    -- podpisał eksport, musi zostać celem klucza obcego.
    disabled_at      timestamptz,
    last_login_at    timestamptz,
    -- Adres jest tożsamością przy logowaniu, więc małe litery są warunkiem, a nie zwyczajem:
    -- „Anna@Firma.pl" i „anna@firma.pl" jako dwa konta to dwa razy ta sama osoba i jedna
    -- z nich nie widzi swoich danych.
    CONSTRAINT users_email_is_lowercase CHECK (email IS NULL OR email = lower(email)),
    CONSTRAINT users_email_looks_like_one CHECK (email IS NULL OR email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    CONSTRAINT users_disabled_after_created CHECK (disabled_at IS NULL OR disabled_at >= created_at)
);

-- Jeden adres, jedno konto. Wiersze bez adresu (przeniesiony operator) nie kolidują ze sobą,
-- bo NULL w indeksie unikalnym jest domyślnie odrębny.
CREATE UNIQUE INDEX users_one_account_per_email ON users (email);

-- -----------------------------------------------------------------------------
-- Członkostwo
-- -----------------------------------------------------------------------------

CREATE TABLE memberships (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users (id),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    role             text NOT NULL,
    -- Kto zaprosił. NULL dla pierwszego właściciela: organizację zakłada się samemu.
    invited_by       uuid REFERENCES users (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Odebranie dostępu zostawia wiersz. „Kto miał dostęp i do kiedy” to pytanie, które pada
    -- po incydencie, a DELETE odpowiada na nie ciszą.
    revoked_at       timestamptz,
    UNIQUE (organization_id, id),
    CONSTRAINT memberships_role_is_known CHECK (role IN ('OWNER', 'MEMBER')),
    CONSTRAINT memberships_revoked_after_created CHECK (
        revoked_at IS NULL OR revoked_at >= created_at
    )
);

-- Jedno AKTYWNE członkostwo na parę. Indeks częściowy, a nie CHECK i nie kod: dwa równoległe
-- zaproszenia tej samej osoby wchodzą w dwa różne wiersze i tylko baza widzi oba naraz.
CREATE UNIQUE INDEX memberships_one_active_per_pair
    ON memberships (user_id, organization_id)
    WHERE revoked_at IS NULL;

CREATE INDEX memberships_by_organization ON memberships (organization_id) WHERE revoked_at IS NULL;

-- -----------------------------------------------------------------------------
-- Zaproszenia
-- -----------------------------------------------------------------------------

CREATE TABLE invitations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    email            text NOT NULL CHECK (email = lower(email) AND length(email) > 3),
    -- SHA-256 tokenu, nigdy sam token. Wyciek bazy nie ma wtedy jak stać się wejściem do cudzej
    -- organizacji, a ten sam powód stoi za `SessionStore` trzymającym skrót ciasteczka.
    token_hash       text NOT NULL CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    expires_at       timestamptz NOT NULL,
    accepted_at      timestamptz,
    created_by       uuid NOT NULL REFERENCES users (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    CONSTRAINT invitations_expire_after_they_are_made CHECK (expires_at > created_at),
    CONSTRAINT invitations_accepted_before_expiry CHECK (
        accepted_at IS NULL OR accepted_at <= expires_at
    )
);

CREATE UNIQUE INDEX invitations_one_live_per_email
    ON invitations (organization_id, email)
    WHERE accepted_at IS NULL;

CREATE INDEX invitations_by_token ON invitations (token_hash) WHERE accepted_at IS NULL;

-- -----------------------------------------------------------------------------
-- Eksport i usunięcie danych
-- -----------------------------------------------------------------------------
--
-- Dwa osobne rodzaje jobów, nie jeden z polem `kind`. Eksport i usunięcie różnią się wszystkim,
-- co po nich zostaje: pierwszy tworzy artefakt do pobrania, drugi kasuje dane i ma okres
-- karencji, po którym nie ma odwrotu.

CREATE TABLE data_export_jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    requested_by     uuid NOT NULL REFERENCES users (id),
    status           text NOT NULL DEFAULT 'REQUESTED',
    -- Wypełniane, gdy plik naprawdę powstał. Gotowy eksport bez artefaktu byłby linkiem
    -- donikąd, więc CHECK niżej tego pilnuje.
    artifact_id      uuid,
    requested_at     timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    -- Po tej dacie plik jest kasowany razem z artefaktem. Eksport to kopia CAŁEJ organizacji;
    -- kopia bez terminu ważności leży w artefaktach tak długo, aż ktoś ją znajdzie.
    purge_after      timestamptz,
    detail           text,
    UNIQUE (organization_id, id),
    CONSTRAINT data_export_jobs_status_is_known CHECK (status IN (
        'REQUESTED', 'RUNNING', 'READY', 'FAILED', 'PURGED'
    )),
    CONSTRAINT data_export_jobs_ready_have_a_file CHECK (
        status <> 'READY' OR (artifact_id IS NOT NULL AND finished_at IS NOT NULL
                              AND purge_after IS NOT NULL)
    ),
    CONSTRAINT data_export_jobs_failed_say_why CHECK (
        status <> 'FAILED' OR length(btrim(coalesce(detail, ''), E' \t\r\n')) > 0
    )
);

CREATE INDEX data_export_jobs_by_organization
    ON data_export_jobs (organization_id, requested_at DESC);

CREATE TABLE data_deletion_jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    requested_by     uuid NOT NULL REFERENCES users (id),
    status           text NOT NULL DEFAULT 'REQUESTED',
    requested_at     timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    -- Karencja. Do tej chwili dane jeszcze są, a logowanie już nie działa: człowiek, który
    -- kliknął przez pomyłkę, ma gdzie się zatrzymać, a ten, kto kliknął świadomie, nie musi
    -- czekać na odcięcie dostępu.
    purge_after      timestamptz NOT NULL,
    detail           text,
    UNIQUE (organization_id, id),
    CONSTRAINT data_deletion_jobs_status_is_known CHECK (status IN (
        'REQUESTED', 'PURGED', 'CANCELLED', 'FAILED'
    )),
    CONSTRAINT data_deletion_jobs_purge_after_the_request CHECK (purge_after >= requested_at),
    CONSTRAINT data_deletion_jobs_finished_when_done CHECK (
        status IN ('REQUESTED') OR finished_at IS NOT NULL
    )
);

-- Jedno żywe żądanie usunięcia na organizację: dwa naraz to dwa różne terminy karencji dla
-- tych samych danych.
CREATE UNIQUE INDEX data_deletion_jobs_one_live_per_organization
    ON data_deletion_jobs (organization_id)
    WHERE status = 'REQUESTED';

-- -----------------------------------------------------------------------------
-- Artefakt eksportu
-- -----------------------------------------------------------------------------
-- `artifacts.kind` jest listą zamkniętą od 0003 (0016 dołożyło 'SOURCE'). Eksport organizacji
-- jest artefaktem jak każdy inny: ma sumę kontrolną, ACL, retencję i sprzątacz.

-- Lista przepisana z 0016 w całości plus 'EXPORT'. Przepisanie, a nie „mniej więcej to samo":
-- zgubiona wartość znika z CHECK-a po cichu i wychodzi dopiero jako odmowa zapisu w module,
-- który z tą migracją nie ma nic wspólnego. Tak właśnie zniknęło stąd raz 'LOG'.
ALTER TABLE artifacts DROP CONSTRAINT artifacts_kind_is_known;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_kind_is_known CHECK (kind IN (
    'TRACE', 'SCREENSHOT', 'HAR', 'LOG', 'REPORT', 'SOURCE', 'EXPORT'
));

-- -----------------------------------------------------------------------------
-- Przeniesienie założyciela
-- -----------------------------------------------------------------------------
-- Jeden wiersz na żywo i dokładnie jeden powód, żeby to zrobić migracją, a nie ręcznie: po tej
-- migracji dostęp wymaga członkostwa, więc operator bez konta użytkownika straciłby dostęp do
-- własnych danych. Hasło jest przenoszone jako ten sam hash, więc założyciel loguje się tym,
-- czym dotąd. Adresu nie znamy, więc go nie wymyślamy.

INSERT INTO users (email, credential_hash, created_at)
SELECT NULL, o.credential_hash, o.created_at
  FROM operators o
 WHERE o.disabled_at IS NULL;

INSERT INTO memberships (user_id, organization_id, role, created_at)
SELECT u.id, o.organization_id, 'OWNER', o.created_at
  FROM operators o
  JOIN users u ON u.credential_hash = o.credential_hash AND u.email IS NULL
 WHERE o.disabled_at IS NULL;

COMMIT;
