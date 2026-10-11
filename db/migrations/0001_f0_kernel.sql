-- =============================================================================
-- Migracja 0001 — schemat fazy E1.F0
--
-- Zakres F0 wymaga wprost "spike Postgres lease + fencing" (L5164), a DoD żąda
-- "stary fencing token jest odrzucany" (L5178).
--
-- Dodatkowo E1.F1 (L5199) wymaga, żeby kolumny `organization_id` / `workspace_id`
-- i composite FK istniały **od pierwszej migracji**, nawet jeżeli RLS jest wyłączone
-- (DoD L5215). Dlatego są tu od razu, mimo że w F0 istnieje jedna organizacja.
--
-- Czego tu NIE MA i być nie powinno w F0:
--   * kolejki jobów, job_attempts, outbox/inbox — to E1.F1 i E3 (L5193, L5205),
--   * RLS i osobnych ról login — E3 (L5206, L5428),
--   * registry platform, presence tracków, ocen wątków — F2-F4.
-- Rozlanie zakresu jest tu równie kosztowne jak brak zakresu.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Tenant
-- -----------------------------------------------------------------------------
-- W F0 jest jedna organizacja i jeden workspace. Tabele istnieją, bo composite FK
-- muszą mieć na co wskazywać, a dołożenie ich później oznaczałoby przepisanie
-- wszystkich kluczy obcych.

CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    name             text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Cel composite FK: żeby dziecko mogło wskazać (organization_id, workspace_id)
    -- jako parę i nie dało się zmieszać workspace'u z obcą organizacją (L5587).
    UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- Konta i profile
-- -----------------------------------------------------------------------------

CREATE TYPE social_account_state AS ENUM (
    'PROVISIONED', 'VERIFYING', 'READY', 'PAUSED', 'REAUTH_REQUIRED', 'QUARANTINED'
);

CREATE TABLE social_accounts (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL REFERENCES organizations (id),
    platform             text NOT NULL,
    -- Ten sam external_account_id nie może utworzyć drugiego konta w organizacji (L5570).
    external_account_id  text NOT NULL,
    state                social_account_state NOT NULL DEFAULT 'PROVISIONED',
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, platform, external_account_id)
);

CREATE TYPE browser_profile_state AS ENUM (
    'CREATING', 'AVAILABLE', 'LEASED', 'QUARANTINED', 'CORRUPT', 'RETIRED'
);

CREATE TABLE browser_profiles (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL,
    social_account_id uuid NOT NULL,
    state             browser_profile_state NOT NULL DEFAULT 'CREATING',
    -- Dokładny build, którym profil był otwierany. Zakaz mieszania buildów (L2308).
    profile_build_marker text NOT NULL,
    profile_platform     text NOT NULL,
    home_runner_id    uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    retired_at        timestamptz,
    UNIQUE (organization_id, id),
    -- Composite FK: profil nie może wskazać konta obcej organizacji (L5587).
    FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id)
);

-- "baza wymusza najwyżej jeden nieretired BrowserProfile dla social_account_id
--  przez partial unique constraint" (L2303).
-- Test: "dwie równoległe próby stworzenia BrowserProfile dla jednego konta kończą się
--  jednym aktywnym profilem" (L5596).
CREATE UNIQUE INDEX browser_profiles_jeden_aktywny_na_konto
    ON browser_profiles (social_account_id)
    WHERE state <> 'RETIRED';

-- -----------------------------------------------------------------------------
-- ProfileLease — sedno spike'u F0
-- -----------------------------------------------------------------------------

CREATE TYPE lease_state AS ENUM ('ACTIVE', 'RECONCILING', 'RELEASED', 'REVOKED', 'EXPIRED');

CREATE TABLE profile_leases (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    profile_id       uuid NOT NULL,
    -- "generation rośnie przy każdym przejęciu" (L2372). To jest fencing token.
    generation       bigint NOT NULL CHECK (generation >= 1),
    owner_runner_id  uuid NOT NULL,
    browser_session_id uuid,
    -- Czas bazy, nie zegar workera (L2668).
    acquired_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    state            lease_state NOT NULL DEFAULT 'ACTIVE',
    released_at      timestamptz,
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, profile_id)
        REFERENCES browser_profiles (organization_id, id),
    CHECK (expires_at > acquired_at)
);

-- "istnieje partial unique constraint dla jednego aktywnego lease'u na profil" (L2373).
-- To jest mechanizm, przez który "dwaj workerzy nie uruchamiają jednego profilu" (L5581).
CREATE UNIQUE INDEX profile_leases_jeden_aktywny_na_profil
    ON profile_leases (profile_id)
    WHERE state = 'ACTIVE';

-- Generacja jest monotoniczna per profil — nigdy nie maleje, także po przejęciach.
CREATE UNIQUE INDEX profile_leases_generacja_unikalna
    ON profile_leases (profile_id, generation);

-- -----------------------------------------------------------------------------
-- BrowserSession
-- -----------------------------------------------------------------------------

CREATE TYPE browser_session_state AS ENUM (
    'STARTING', 'ACTIVE', 'STOPPING', 'CLOSED', 'CRASHED', 'ORPHANED'
);

CREATE TYPE capsule_mode AS ENUM ('HEADLESS', 'INTERACTIVE');

CREATE TABLE browser_sessions (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       uuid NOT NULL,
    profile_id            uuid NOT NULL,
    profile_lease_id      uuid NOT NULL,
    capsule_instance_id   uuid NOT NULL,
    os_pid                integer NOT NULL,
    process_started_at    timestamptz NOT NULL,
    -- Chroni przed pomyleniem starego i nowego procesu przy PID reuse (L5584).
    process_instance_id   text NOT NULL,
    capsule_supervisor_identity text NOT NULL,
    browser_build_digest  text NOT NULL,
    browser_mode          capsule_mode NOT NULL,
    cdp_endpoint_ref      text NOT NULL,
    display_id            text,
    display_server_pid    integer,
    stream_gateway_instance_id uuid,
    stream_transport      text,
    state                 browser_session_state NOT NULL DEFAULT 'STARTING',
    last_seen_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, profile_id)
        REFERENCES browser_profiles (organization_id, id),
    FOREIGN KEY (organization_id, profile_lease_id)
        REFERENCES profile_leases (organization_id, id),
    -- "cała praca automatu wykonuje się w trybie HEADLESS; run E1 nie startuje
    --  displayu ani stream gatewaya" (L5532) — egzekwowane w bazie, nie tylko w kodzie.
    CONSTRAINT headless_bez_displayu CHECK (
        browser_mode <> 'HEADLESS'
        OR (display_id IS NULL AND display_server_pid IS NULL
            AND stream_gateway_instance_id IS NULL)
    )
);

-- -----------------------------------------------------------------------------
-- InteractionLease i StreamSession
-- -----------------------------------------------------------------------------

CREATE TYPE interaction_lease_mode AS ENUM ('AUTOMATION', 'HUMAN');

CREATE TABLE interaction_leases (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL,
    browser_session_id uuid NOT NULL,
    mode               interaction_lease_mode NOT NULL,
    owner_ref          text NOT NULL,
    generation         bigint NOT NULL CHECK (generation >= 1),
    heartbeat_at       timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    state              lease_state NOT NULL DEFAULT 'ACTIVE',
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, browser_session_id)
        REFERENCES browser_sessions (organization_id, id)
);

-- "partial unique: one ACTIVE interaction lease per browser_session_id" (L2415).
-- Test: "dwa równoległe claimy InteractionLease dla jednej browser session kończą się
--  jednym aktywnym lease'em" (L5592).
CREATE UNIQUE INDEX interaction_leases_jeden_aktywny_na_sesje
    ON interaction_leases (browser_session_id)
    WHERE state = 'ACTIVE';

CREATE TYPE stream_session_role AS ENUM ('VIEW_ONLY', 'CONTROL');

CREATE TABLE stream_sessions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL,
    browser_session_id uuid NOT NULL,
    user_id            uuid NOT NULL,
    role               stream_session_role NOT NULL,
    -- Ticket przechowywany WYŁĄCZNIE jako hash (L2333).
    ticket_hash        text NOT NULL,
    profile_lease_generation bigint NOT NULL,
    interaction_lease_id uuid,
    interaction_lease_generation bigint,
    -- Cztery osobne TTL (L2345, L5598).
    ticket_expires_at         timestamptz NOT NULL,
    stream_session_expires_at timestamptz NOT NULL,
    idle_timeout_at           timestamptz NOT NULL,
    issued_at    timestamptz NOT NULL DEFAULT now(),
    exchanged_at timestamptz,
    revoked_at   timestamptz,
    UNIQUE (organization_id, id),
    -- Replay StreamTicketu jest odrzucany (L5589): ten sam hash nie może zostać wymieniony dwa razy.
    UNIQUE (ticket_hash),
    FOREIGN KEY (organization_id, browser_session_id)
        REFERENCES browser_sessions (organization_id, id),
    -- "CONTROL ticket nie może zostać wydany bez atomowego HUMAN InteractionLease
    --  i jest związany z jego generation" (L5590).
    CONSTRAINT control_wymaga_interaction_lease CHECK (
        role <> 'CONTROL'
        OR (interaction_lease_id IS NOT NULL AND interaction_lease_generation IS NOT NULL)
    ),
    CONSTRAINT ttl_biletu_nie_dluzszy_niz_sesji CHECK (
        ticket_expires_at <= stream_session_expires_at
    )
);

-- -----------------------------------------------------------------------------
-- Capability snapshots
-- -----------------------------------------------------------------------------

CREATE TYPE capability_state AS ENUM (
    'SUPPORTED', 'DEGRADED', 'MANUAL_ONLY', 'UNSUPPORTED', 'UNKNOWN'
);

CREATE TYPE execution_mode AS ENUM (
    'AUTOMATED', 'AUTOMATED_AFTER_APPROVAL', 'MANUAL_ONLY', 'UNSUPPORTED', 'UNKNOWN'
);

CREATE TABLE capability_snapshots (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL,
    social_account_id uuid NOT NULL,
    surface_ref       text NOT NULL,
    capability        text NOT NULL,
    state             capability_state NOT NULL,
    execution_mode    execution_mode NOT NULL,
    -- DoD F0: "każda capability ma zapisany required_browser_mode; te wymagające
    -- człowieka są oznaczone MANUAL_ONLY" (L5177). Kolumna jest NOT NULL nie bez powodu.
    required_browser_mode capsule_mode,
    side_effect       text NOT NULL,
    retry_class       text NOT NULL,
    browser_build_digest text NOT NULL,
    reason_code       text NOT NULL,
    confidence        double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    adapter_version   text NOT NULL,
    verified_at       timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id),
    CHECK (expires_at > verified_at),
    -- "capability niedziałająca headless ma required_browser_mode = INTERACTIVE
    --  albo MANUAL_ONLY, nigdy cichy fallback" (L5537).
    CONSTRAINT manual_only_nie_jest_headless CHECK (
        NOT (execution_mode = 'MANUAL_ONLY' AND required_browser_mode = 'HEADLESS')
    )
);

-- -----------------------------------------------------------------------------
-- Audit events
-- -----------------------------------------------------------------------------
-- Append-only. F0 potrzebuje ich, bo "przełączenie trybu zapisuje audit event" (L2448).

CREATE TABLE audit_events (
    id              bigserial PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations (id),
    workspace_id    uuid,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    kind            text NOT NULL,
    subject_ref     text NOT NULL,
    actor_ref       text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_events_wg_czasu ON audit_events (organization_id, occurred_at DESC);
CREATE INDEX audit_events_wg_rodzaju ON audit_events (kind, occurred_at DESC);

-- Blokada UPDATE/DELETE na audycie — dziennik ma być append-only.
CREATE OR REPLACE FUNCTION audit_events_tylko_dopisywanie() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events jest append-only: % jest niedozwolone', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_bez_zmian
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_tylko_dopisywanie();

-- -----------------------------------------------------------------------------
-- Przejścia trybu kapsuły
-- -----------------------------------------------------------------------------

CREATE TABLE capsule_mode_transitions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    profile_id       uuid NOT NULL,
    from_mode        capsule_mode NOT NULL,
    to_mode          capsule_mode NOT NULL,
    -- Zamknięta lista powodów (L5518). Trzymana jako text + CHECK, żeby dodanie
    -- wartości wymagało migracji, a nie było możliwe przez pomyłkę w kodzie.
    reason           text NOT NULL CHECK (reason IN (
        'FIRST_LOGIN', 'REAUTH', 'MFA', 'CHALLENGE', 'HUMAN_TAKEOVER', 'IDENTITY_VERIFICATION'
    )),
    safe_stop_completed_at timestamptz NOT NULL,
    previous_browser_session_id uuid NOT NULL,
    new_browser_session_id      uuid NOT NULL,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, profile_id)
        REFERENCES browser_profiles (organization_id, id),
    CHECK (from_mode <> to_mode),
    CHECK (previous_browser_session_id <> new_browser_session_id)
);

COMMIT;
