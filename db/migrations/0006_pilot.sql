-- =============================================================================
-- Migracja 0006 — pilot 24 h: manifest runu, cykle, reakcje (docs/START-PILOTA-24H.md,
-- karta „Pilot 24h" w docs/STAN-JAKOSCI-ROZMOW.md)
--
-- Trzy tabele w służbie jednego wymagania: przez 24 godziny system czyta prawdziwe rozmowy
-- jednej społeczności, wybiera okazje i ZAPISUJE szkic, WAIT albo SILENCE, a rano człowiek
-- dostaje raport. Nic tu nie publikuje; publikacja idzie wyłącznie przez ExecutionIntent (D-027).
--
--   1. `pilot_runs` — manifest: okno (starts_at/ends_at stałe), budżet wywołań modelu
--      z licznikiem ZUŻYCIA W BAZIE (rezerwacja przed wywołaniem: `UPDATE … WHERE
--      model_calls_used < model_calls_budget RETURNING`, restart nic nie zeruje), wersja
--      profilu i promptu, wstrzymane automaty i konfiguracja do przywrócenia, dziennik zdarzeń
--      hosta (przerwy, awarie Dockera) dopisywany przez cykle. Jeden ACTIVE run na community.
--   2. `pilot_cycles` — jeden wiersz na slot 30 min: kiedy planowany, kiedy naprawdę ruszył
--      i skończył, z jakim wynikiem. Raport liczy cykle udane/nieudane i przerwy z tej
--      tabeli, nie wnioskuje ich z logów.
--   3. `pilot_reactions` — trwały zapis każdej reakcji generatora: cel (wątek, wiadomość),
--      decyzja z zamkniętej listy `ReactionKind` (parytet z kontraktami po NAZWIE constraintu,
--      jak w 0004), tekst albo jego brak, uzasadnienie, pewność modelu, wersja profilu
--      i promptu, hash wejścia i hash SYTUACJI (dedupe: ta sama wersja rozmowy nie dostaje
--      drugiej reakcji), czas odczytu i czy kontekst był pełny, dla WAIT powód i termin.
--      SILENCE jest pełnym wynikiem. Status oceny człowieka zawsze zaczyna od
--      HUMAN_REVIEW_PENDING; opinia modelu (confidence) to osobna kolumna.
--
-- Wzorce odziedziczone: tenant triple w FK, zamknięte listy jako text + nazwany CHECK.
-- =============================================================================

BEGIN;

CREATE TABLE pilot_runs (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id            uuid NOT NULL REFERENCES organizations (id),
    workspace_id               uuid NOT NULL,
    community_id               uuid NOT NULL,
    status                     text NOT NULL,
    starts_at                  timestamptz NOT NULL,
    ends_at                    timestamptz NOT NULL,
    stopped_at                 timestamptz,
    poll_interval_seconds      integer NOT NULL CHECK (poll_interval_seconds >= 60),
    model_calls_budget         integer NOT NULL CHECK (model_calls_budget >= 1),
    model_calls_used           integer NOT NULL DEFAULT 0 CHECK (model_calls_used >= 0),
    max_generations_per_thread integer NOT NULL CHECK (max_generations_per_thread >= 1),
    profile_version_id         uuid NOT NULL,
    prompt_name                text NOT NULL CHECK (length(prompt_name) > 0),
    prompt_sha256              text NOT NULL,
    -- Typy jobów, których poll NIE zleca dla tej community, póki run jest ACTIVE.
    paused_job_types           text[] NOT NULL,
    -- Co przywrócić po końcu runu (np. {"poll_interval_seconds": 300}).
    restore                    jsonb NOT NULL,
    -- Dziennik zdarzeń hosta dopisywany przez cykle: [{"at", "kind", "detail"}].
    host_events                jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT pilot_runs_status_is_known CHECK (status IN ('ACTIVE', 'FINISHED', 'STOPPED')),
    CONSTRAINT pilot_runs_window_is_ordered CHECK (ends_at > starts_at),
    CONSTRAINT pilot_runs_budget_not_exceeded CHECK (model_calls_used <= model_calls_budget),
    CONSTRAINT pilot_runs_prompt_digest_is_sha256 CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT pilot_runs_belong_to_community
        FOREIGN KEY (organization_id, workspace_id, community_id)
        REFERENCES communities (organization_id, workspace_id, id),
    CONSTRAINT pilot_runs_profile_exists
        FOREIGN KEY (organization_id, workspace_id, profile_version_id)
        REFERENCES user_profile_versions (organization_id, workspace_id, id)
);

-- Dwa aktywne piloty na jednej community paliłyby budżet podwójnie.
CREATE UNIQUE INDEX pilot_runs_one_active_per_community
    ON pilot_runs (community_id) WHERE status = 'ACTIVE';

CREATE TABLE pilot_cycles (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    run_id           uuid NOT NULL,
    slot             integer NOT NULL CHECK (slot >= 0),
    planned_at       timestamptz NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    outcome          text NOT NULL,
    detail           text,
    model_calls_used_after integer NOT NULL CHECK (model_calls_used_after >= 0),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (run_id, slot),
    CONSTRAINT pilot_cycles_outcome_is_known CHECK (outcome IN (
        'STARTED', 'GENERATED', 'NO_CANDIDATE', 'DUPLICATE', 'BUDGET_EXHAUSTED', 'PACING',
        'MODEL_UNAVAILABLE', 'PLATFORM_UNAVAILABLE', 'REJECTED', 'FAILED', 'RUN_ENDED'
    )),
    CONSTRAINT pilot_cycles_belong_to_run
        FOREIGN KEY (organization_id, workspace_id, run_id)
        REFERENCES pilot_runs (organization_id, workspace_id, id)
);

CREATE TABLE pilot_reactions (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id            uuid NOT NULL REFERENCES organizations (id),
    workspace_id               uuid NOT NULL,
    run_id                     uuid NOT NULL,
    thread_id                  uuid NOT NULL,
    origin                     text NOT NULL,
    target_message_external_id text NOT NULL CHECK (length(target_message_external_id) > 0),
    decision                   text NOT NULL,
    draft                      text,
    contribution               text,
    brief_rationale            text NOT NULL CHECK (length(brief_rationale) > 0),
    confidence                 double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    model                      text NOT NULL CHECK (length(model) > 0),
    effort                     text NOT NULL CHECK (length(effort) > 0),
    profile_version_id         uuid NOT NULL,
    prompt_name                text NOT NULL CHECK (length(prompt_name) > 0),
    prompt_sha256              text NOT NULL,
    input_sha256               text NOT NULL,
    situation_sha256           text NOT NULL,
    read_at                    timestamptz NOT NULL,
    context_complete           boolean NOT NULL,
    comments_seen              integer NOT NULL CHECK (comments_seen >= 0),
    wait_reason                text,
    next_check_at              timestamptz,
    review_status              text NOT NULL DEFAULT 'HUMAN_REVIEW_PENDING',
    created_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (run_id, situation_sha256),
    CONSTRAINT pilot_reactions_origin_is_known CHECK (origin IN ('SMOKE', 'CYCLE')),
    CONSTRAINT pilot_reactions_decision_is_known CHECK (decision IN (
        'HELP', 'DIAGNOSE', 'CLARIFY', 'DISAGREE', 'SHORT_ACK', 'HUMOR', 'WAIT', 'SILENCE'
    )),
    CONSTRAINT pilot_reactions_text_matches_decision CHECK (
        (decision IN ('WAIT', 'SILENCE')) = (draft IS NULL)
    ),
    CONSTRAINT pilot_reactions_wait_has_deadline CHECK (
        (decision = 'WAIT') = (next_check_at IS NOT NULL)
    ),
    CONSTRAINT pilot_reactions_review_status_is_known CHECK (
        review_status IN ('HUMAN_REVIEW_PENDING', 'REVIEWED')
    ),
    CONSTRAINT pilot_reactions_digests_are_sha256 CHECK (
        prompt_sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'
        AND situation_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT pilot_reactions_belong_to_run
        FOREIGN KEY (organization_id, workspace_id, run_id)
        REFERENCES pilot_runs (organization_id, workspace_id, id),
    CONSTRAINT pilot_reactions_belong_to_thread
        FOREIGN KEY (organization_id, workspace_id, thread_id)
        REFERENCES observed_threads (organization_id, workspace_id, id),
    CONSTRAINT pilot_reactions_profile_exists
        FOREIGN KEY (organization_id, workspace_id, profile_version_id)
        REFERENCES user_profile_versions (organization_id, workspace_id, id)
);

CREATE INDEX pilot_reactions_by_run_thread ON pilot_reactions (run_id, thread_id);
CREATE INDEX pilot_reactions_due_waits ON pilot_reactions (run_id, next_check_at)
    WHERE decision = 'WAIT';

COMMIT;
