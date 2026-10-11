-- =============================================================================
-- Migracja 0008 — tryb 7 dni (D-029): polityka publikacji podpisana raz przez założyciela
-- i osąd szkicu przez system przed zatwierdzeniem.
--
-- Zgoda na każdy komentarz nadal powstaje w ISTNIEJĄCYCH tabelach approvali (rewizja, decyzja,
-- intent, próba, dowód z 0003 i 0007). Ta migracja dodaje tylko:
--
--   1. `publication_policies` — co założyciel podpisał: zakres (społeczności, konto, rodzaje
--      reakcji, próg pewności, limit znaków, tempo), okno ważności (najwyżej 7 dni), słowa
--      założyciela jako źródło, prompt sędziego przypięty hashem, stan i powód zatrzymania.
--      Worker zatwierdza wyłącznie w ramach polityki ACTIVE w oknie ważności.
--   2. `draft_judgments` — werdykt na szkic: PASS / REJECT (sędzia modelowy) albo POLICY_REJECT
--      (reguła polityki bez wywołania modelu), z odpowiedziami na pytania dotychczasowego arkusza
--      ocen, uzasadnieniem dla założyciela i odciskami promptu i wejścia. Jeden werdykt na szkic:
--      REJECT jest ostateczny dla tego brzmienia (nowa wersja rozmowy = nowy szkic).
--
-- Wzorce odziedziczone: tenant triple w FK, zamknięte listy jako text + nazwany CHECK.
-- =============================================================================

BEGIN;

CREATE TABLE publication_policies (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL REFERENCES organizations (id),
    workspace_id         uuid NOT NULL,
    operator_id          uuid NOT NULL,
    status               text NOT NULL DEFAULT 'ACTIVE',
    signed_at            timestamptz NOT NULL DEFAULT now(),
    valid_from           timestamptz NOT NULL,
    valid_until          timestamptz NOT NULL,
    community_ids        uuid[] NOT NULL,
    account_handle       text NOT NULL CHECK (length(account_handle) > 0),
    allowed_kinds        text[] NOT NULL,
    min_confidence       double precision NOT NULL CHECK (min_confidence >= 0 AND min_confidence <= 1),
    max_chars            integer NOT NULL CHECK (max_chars > 0),
    max_per_hour         integer NOT NULL CHECK (max_per_hour >= 0),
    max_per_day          integer NOT NULL CHECK (max_per_day >= 0),
    judge_prompt_name    text NOT NULL CHECK (length(judge_prompt_name) > 0),
    judge_prompt_sha256  text NOT NULL CHECK (judge_prompt_sha256 ~ '^[0-9a-f]{64}$'),
    -- Słowa założyciela i odwołanie do dokumentu, którym podpisał politykę.
    source_text          text NOT NULL CHECK (length(source_text) > 0),
    stopped_at           timestamptz,
    stop_reason          text,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT publication_policies_status_is_known CHECK (
        status IN ('ACTIVE', 'REVOKED', 'STOPPED', 'EXPIRED')
    ),
    CONSTRAINT publication_policies_window_is_sane CHECK (
        valid_until > valid_from AND valid_until <= valid_from + interval '7 days'
    ),
    CONSTRAINT publication_policies_have_scope CHECK (
        cardinality(community_ids) >= 1 AND cardinality(allowed_kinds) >= 1
    ),
    CONSTRAINT publication_policies_stop_has_moment CHECK (
        (status IN ('STOPPED', 'REVOKED')) = (stopped_at IS NOT NULL)
    ),
    -- Tenant pair straight to the workspace: the policy hangs off the workspace itself, like
    -- `communities` and `user_profile_versions` (0004).
    CONSTRAINT publication_policies_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

CREATE INDEX publication_policies_active
    ON publication_policies (organization_id, status, valid_until DESC);

CREATE TABLE draft_judgments (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    policy_id        uuid NOT NULL,
    reaction_id      uuid NOT NULL,
    verdict          text NOT NULL,
    -- Odpowiedzi na pytania arkusza (kind_fits, adds_something, stays_within_profile,
    -- sounds_like_them, safe_to_post) z jednozdaniowym powodem każda.
    checks           jsonb NOT NULL DEFAULT '{}'::jsonb,
    rationale        text NOT NULL CHECK (length(rationale) > 0),
    model            text,
    effort           text,
    prompt_name      text,
    prompt_sha256    text,
    input_sha256     text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (reaction_id),
    CONSTRAINT draft_judgments_verdict_is_known CHECK (
        verdict IN ('PASS', 'REJECT', 'POLICY_REJECT')
    ),
    CONSTRAINT draft_judgments_model_verdicts_carry_prompt CHECK (
        (verdict = 'POLICY_REJECT') = (prompt_sha256 IS NULL)
    ),
    CONSTRAINT draft_judgments_digests_are_sha256 CHECK (
        (prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$')
        AND (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT draft_judgments_belong_to_policy
        FOREIGN KEY (organization_id, workspace_id, policy_id)
        REFERENCES publication_policies (organization_id, workspace_id, id),
    CONSTRAINT draft_judgments_point_at_reaction
        FOREIGN KEY (organization_id, workspace_id, reaction_id)
        REFERENCES pilot_reactions (organization_id, workspace_id, id)
);

CREATE INDEX draft_judgments_by_policy
    ON draft_judgments (policy_id, created_at DESC);

COMMIT;
