-- =============================================================================
-- Migracja 0007 — publikacja zatwierdzonych szkiców pilota (R4 minimalny; docs/ZGODY-PUBLIKACJI-
-- 2026-09-07.md, docs/ROADMAP-JAKOSC-ROZMOW.md R4, D-027, D-028)
--
-- Zgoda człowieka trafia do ISTNIEJĄCYCH tabel (action_proposals, action_proposal_revisions,
-- approval_decisions, execution_intents, execution_attempts z 0003). Ta migracja dodaje tylko to,
-- czego tamte nie mają, a czego wymaga publikacja szkicu z pilota:
--
--   1. `publication_requests` — kolejka publikacji: jeden wiersz na zatwierdzony szkic
--      (reakcja pilota → rewizja propozycji → zgoda → intent → próba), w kolejności zgód,
--      z odciskiem sha256 treści szkicu z dokumentu zgody i statusem z zamkniętej listy.
--      Publikuje wyłącznie worker przez ExecutionIntent (D-027); ta tabela tylko mówi CO i W JAKIM
--      PORZĄDKU, i pamięta, co z tego wyszło.
--   2. `execution_evidence` — dowód skutku: id komentarza na platformie, permalink, konto, moment
--      odczytu, źródło (aktuator / odczyt kontrolny / rekoncyliacja) i surowa odpowiedź, 1:1 z
--      `execution_attempts`. Brak wiersza po SUBMIT_STARTED = „nie wiadomo", nie „nie opublikowano".
--   3. `publication_observations` — odczyty kontrolne naszego komentarza po publikacji: czy nadal
--      jest, ile ma punktów, ile odpowiedzi (jedyny sygnał „rezonuje", o który pytał założyciel).
--      Odpowiedzi ludzi same lądują w `observed_comments` (parent_external_id = nasz t1_…).
--   4. W `pilot_runs`: tempo publikacji jako parametry manifestu (1 na godzinę, 6 na dobę) i
--      `publishing_stopped_at` (publish-stop / pilot-stop zatrzymują kolejkę natychmiast).
--
-- Wzorce odziedziczone: tenant triple w FK, zamknięte listy jako text + nazwany CHECK.
-- =============================================================================

BEGIN;

ALTER TABLE pilot_runs
    ADD COLUMN max_publications_per_hour integer NOT NULL DEFAULT 1
        CHECK (max_publications_per_hour >= 0),
    ADD COLUMN max_publications_per_day integer NOT NULL DEFAULT 6
        CHECK (max_publications_per_day >= 0),
    ADD COLUMN publishing_stopped_at timestamptz;

CREATE TABLE publication_requests (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       uuid NOT NULL REFERENCES organizations (id),
    workspace_id          uuid NOT NULL,
    run_id                uuid NOT NULL,
    reaction_id           uuid NOT NULL,
    proposal_revision_id  uuid NOT NULL,
    approval_decision_id  uuid NOT NULL,
    intent_id             uuid,
    attempt_id            uuid,
    -- Kolejność zgód w dokumencie: kolejka publikuje od najmniejszej pozycji.
    position              integer NOT NULL CHECK (position >= 1),
    status                text NOT NULL DEFAULT 'QUEUED',
    consent_source        text NOT NULL CHECK (length(consent_source) > 0),
    -- sha256 TREŚCI szkicu z dokumentu zgody (nie payload_hash rewizji: ten liczy 14 pól).
    draft_sha256          text NOT NULL CHECK (draft_sha256 ~ '^[0-9a-f]{64}$'),
    target_external_id    text NOT NULL CHECK (target_external_id ~ '^t[13]_[a-z0-9]{1,12}$'),
    target_url            text NOT NULL CHECK (target_url ~ '^https://'),
    queued_at             timestamptz NOT NULL DEFAULT now(),
    not_before            timestamptz,
    last_checked_at       timestamptz,
    detail                text,
    published_at          timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (reaction_id),
    UNIQUE (proposal_revision_id),
    CONSTRAINT publication_requests_status_is_known CHECK (status IN (
        'QUEUED', 'PUBLISHING', 'PUBLISHED', 'UNKNOWN', 'BLOCKED', 'SKIPPED', 'STOPPED'
    )),
    CONSTRAINT publication_requests_published_have_attempt CHECK (
        status <> 'PUBLISHED' OR attempt_id IS NOT NULL
    ),
    CONSTRAINT publication_requests_belong_to_run
        FOREIGN KEY (organization_id, workspace_id, run_id)
        REFERENCES pilot_runs (organization_id, workspace_id, id),
    CONSTRAINT publication_requests_point_at_reaction
        FOREIGN KEY (organization_id, workspace_id, reaction_id)
        REFERENCES pilot_reactions (organization_id, workspace_id, id),
    CONSTRAINT publication_requests_point_at_revision
        FOREIGN KEY (organization_id, workspace_id, proposal_revision_id)
        REFERENCES action_proposal_revisions (organization_id, workspace_id, id),
    CONSTRAINT publication_requests_point_at_decision
        FOREIGN KEY (organization_id, workspace_id, approval_decision_id)
        REFERENCES approval_decisions (organization_id, workspace_id, id),
    CONSTRAINT publication_requests_point_at_intent
        FOREIGN KEY (organization_id, workspace_id, intent_id)
        REFERENCES execution_intents (organization_id, workspace_id, id),
    CONSTRAINT publication_requests_point_at_attempt
        FOREIGN KEY (organization_id, workspace_id, attempt_id)
        REFERENCES execution_attempts (organization_id, workspace_id, id)
);

CREATE INDEX publication_requests_queue
    ON publication_requests (organization_id, status, position);

CREATE TABLE execution_evidence (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    attempt_id       uuid NOT NULL,
    -- Identyfikator skutku na platformie (Reddit: t1_<id> komentarza).
    external_id      text NOT NULL CHECK (length(external_id) > 0),
    permalink        text NOT NULL CHECK (permalink ~ '^https://'),
    account          text NOT NULL CHECK (length(account) > 0),
    observed_at      timestamptz NOT NULL,
    source           text NOT NULL,
    raw              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (attempt_id),
    CONSTRAINT execution_evidence_source_is_known CHECK (source IN (
        'ACTUATOR', 'READBACK', 'RECONCILIATION'
    )),
    CONSTRAINT execution_evidence_belongs_to_attempt
        FOREIGN KEY (organization_id, workspace_id, attempt_id)
        REFERENCES execution_attempts (organization_id, workspace_id, id)
);

CREATE TABLE publication_observations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    request_id       uuid NOT NULL,
    observed_at      timestamptz NOT NULL,
    still_present    boolean NOT NULL,
    score            integer,
    reply_count      integer NOT NULL DEFAULT 0 CHECK (reply_count >= 0),
    raw              jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT publication_observations_belong_to_request
        FOREIGN KEY (organization_id, workspace_id, request_id)
        REFERENCES publication_requests (organization_id, workspace_id, id)
);

CREATE INDEX publication_observations_by_request
    ON publication_observations (request_id, observed_at DESC);

COMMIT;
