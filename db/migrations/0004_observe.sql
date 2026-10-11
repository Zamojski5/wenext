-- =============================================================================
-- Migracja 0004 — obserwacja community i osąd (Outcome A, docs/OUTCOMES.md §3, §5)
--
-- Pierwsza migracja PO resecie kierunku (docs/KIERUNEK-PRODUKTU.md, D-022). Zakres
-- bierze się z outcome'u, nie z roadmapy: osiem tabel i po jednym zdaniu, dlaczego
-- każda tu jest.
--
--   * `communities` — jedno prawdziwe community, które użytkownik kazał obserwować:
--     platforma + klucz (`ask`, `r/devops`), tryb dostępu, interwał odczytu, watermark.
--   * `observed_threads`, `observed_comments` — to, co przeczytano, z dowodem:
--     URL na platformie, `first/last_observed_at`, SHA-256 surowego payloadu (§6:
--     „wiedza powinna pochodzić z obserwacji i zachowywać evidence").
--   * `thread_metric_snapshots` — APPEND-ONLY liczniki wątku w kolejnych odczytach;
--     wartość nieznana to para (NULL, powód), nigdy 0 (22.2 pkt 7 roadmapy).
--   * `user_profile_versions` — cztery pola o użytkowniku (co wie, co uważa, czego
--     nigdy by nie twierdził, próbki głosu). Wersjonowane, bo osąd jest porównywalny
--     tylko z osądem na tym samym profilu (§5).
--   * `community_pictures` — dobowy obraz community (JSONB) z wersją promptu, modelem
--     i effortem. Wersjonowany; poprzednie zostają (§6: „produkt jest pamięcią").
--   * `thread_judgments` — decyzja ENGAGE / IGNORE / WAIT z uzasadnieniem, APPEND-ONLY.
--     IGNORE jest pełnym wierszem — negative data z §9 kierunku.
--   * `judgment_feedback` — co powiedział operator (AGREE / DISAGREE / IRRELEVANT).
--     Obok decyzji, nigdy zamiast niej (§8: każdy human feedback zostaje).
--
-- CZEGO TU NIE MA, świadomie (docs/OUTCOMES.md §3 „czego A nie zawiera"):
--   * `thread_quality_assessments`, presence track, budżety dzienne, registry platform,
--     `action_proposals` dla wątków — nic z tego nie jest potrzebne, żeby obraz
--     community i lista ENGAGE pojawiły się na ekranie.
--
-- WZORCE Z 0003 OBOWIĄZUJĄ:
--  1. Zamknięte listy wartości to `text` + NAZWANY `CHECK`, nie `CREATE TYPE`
--     (`services/db_schema/tests/test_enum_parity.py` szuka CHECK-ów PO NAZWIE
--     i porównuje wartości z enumami w `contracts`).
--  2. Każda tabela tutaj jest workspace'owa: `organization_id NOT NULL`,
--     `workspace_id NOT NULL` i klucz obcy w TRÓJCE
--     `(organization_id, workspace_id, parent_id) -> parent(organization_id, workspace_id, id)`
--     (L3874-3876). Para przepuściłaby dziecko z workspace'u A pod rodzica z B.
--  3. `Observed[int]` w kolumnach to DWIE kolumny i CHECK „dokładnie jedno":
--     `(x IS NULL) <> (x_unknown_reason IS NULL)`. Sam NULL znaczyłby „nie wiemy,
--     czy nie wiemy".
-- =============================================================================

BEGIN;

-- =============================================================================
-- communities
-- =============================================================================
CREATE TABLE communities (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       uuid NOT NULL REFERENCES organizations (id),
    workspace_id          uuid NOT NULL,
    platform              text NOT NULL,
    community_key         text NOT NULL CHECK (length(community_key) > 0),
    display_name          text NOT NULL CHECK (length(display_name) > 0),
    access_mode           text NOT NULL,
    observe_enabled       boolean NOT NULL DEFAULT true,
    -- Minimum 60 s: częstszy odczyt publicznego API jednego community to nie
    -- obserwacja, to hałas — i limit platformy.
    poll_interval_seconds integer NOT NULL CHECK (poll_interval_seconds >= 60),
    last_polled_at        timestamptz,
    -- Najpóźniejszy `created_at` wątku, jaki widzieliśmy. Następny odczyt pyta
    -- o wątki nowsze. NULL = jeszcze nic nie czytano.
    watermark_created_at  timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, platform, community_key),
    CONSTRAINT communities_platform_is_known CHECK (platform IN ('HACKER_NEWS', 'REDDIT')),
    CONSTRAINT communities_access_mode_is_known CHECK (access_mode IN (
        'PUBLIC_API_NO_ACCOUNT', 'PUBLIC_WEB_NO_ACCOUNT', 'ACCOUNT_REQUIRED',
        'MEMBERSHIP_REQUIRED', 'UNKNOWN'
    )),
    CONSTRAINT communities_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

-- =============================================================================
-- observed_threads
-- =============================================================================
CREATE TABLE observed_threads (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    community_id       uuid NOT NULL,
    external_id        text NOT NULL CHECK (length(external_id) > 0),
    url                text NOT NULL CHECK (length(url) > 0),
    link_url           text,
    title              text NOT NULL CHECK (length(title) > 0),
    body               text,
    author             text,
    created_at_source  timestamptz NOT NULL,
    first_observed_at  timestamptz NOT NULL,
    last_observed_at   timestamptz NOT NULL,
    raw_sha256         text NOT NULL,
    UNIQUE (organization_id, workspace_id, id),
    -- Ten sam wątek czytany drugi raz to ten sam wiersz (upsert), nie drugi wiersz.
    UNIQUE (organization_id, workspace_id, community_id, external_id),
    CONSTRAINT observed_threads_digest_is_sha256 CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT observed_threads_observed_after_first
        CHECK (last_observed_at >= first_observed_at),
    CONSTRAINT observed_threads_belong_to_community
        FOREIGN KEY (organization_id, workspace_id, community_id)
        REFERENCES communities (organization_id, workspace_id, id)
);

-- =============================================================================
-- observed_comments
-- =============================================================================
CREATE TABLE observed_comments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES organizations (id),
    workspace_id        uuid NOT NULL,
    thread_id           uuid NOT NULL,
    external_id         text NOT NULL CHECK (length(external_id) > 0),
    -- NULL = komentarz najwyższego poziomu (rodzicem jest wątek).
    parent_external_id  text,
    -- NULL = platforma pokazuje autora/treść jako usunięte. Wiersz zostaje: usunięty
    -- komentarz to nadal dowód, że ktoś odpowiedział, a moderacja jest sygnałem.
    author              text,
    body                text,
    created_at_source   timestamptz NOT NULL,
    observed_at         timestamptz NOT NULL,
    depth               integer NOT NULL CHECK (depth >= 0),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, thread_id, external_id),
    CONSTRAINT observed_comments_top_level_has_depth_zero
        CHECK ((parent_external_id IS NULL) = (depth = 0)),
    CONSTRAINT observed_comments_belong_to_thread
        FOREIGN KEY (organization_id, workspace_id, thread_id)
        REFERENCES observed_threads (organization_id, workspace_id, id)
);

-- =============================================================================
-- thread_metric_snapshots — APPEND-ONLY
-- =============================================================================
CREATE TABLE thread_metric_snapshots (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id               uuid NOT NULL REFERENCES organizations (id),
    workspace_id                  uuid NOT NULL,
    thread_id                     uuid NOT NULL,
    observed_at                   timestamptz NOT NULL,
    score                         integer,
    score_unknown_reason          text,
    comment_count                 integer,
    comment_count_unknown_reason  text,
    UNIQUE (organization_id, workspace_id, id),
    -- `Observed[int]`: wartość ALBO powód, nigdy oba, nigdy żadne.
    CONSTRAINT thread_metric_snapshots_score_observed_or_unknown
        CHECK ((score IS NULL) <> (score_unknown_reason IS NULL)),
    CONSTRAINT thread_metric_snapshots_comment_count_observed_or_unknown
        CHECK ((comment_count IS NULL) <> (comment_count_unknown_reason IS NULL)),
    -- Powód nieznajomości z `contracts.UnknownReason` — parytet sprawdza test.
    CONSTRAINT thread_metric_snapshots_score_unknown_reason_is_known
        CHECK (score_unknown_reason IS NULL OR score_unknown_reason IN (
            'NOT_EXPOSED_BY_PLATFORM', 'NOT_VISIBLE_TO_THIS_ACCOUNT', 'NO_CONNECTED_ACCOUNT',
            'ACCOUNT_NOT_READY', 'SESSION_LOST', 'READ_BUDGET_EXHAUSTED', 'CAPABILITY_EXPIRED',
            'ADAPTER_COULD_NOT_PARSE', 'AMBIGUOUS_EXTERNAL_RESULT'
        )),
    CONSTRAINT thread_metric_snapshots_comment_count_unknown_reason_is_known
        CHECK (comment_count_unknown_reason IS NULL OR comment_count_unknown_reason IN (
            'NOT_EXPOSED_BY_PLATFORM', 'NOT_VISIBLE_TO_THIS_ACCOUNT', 'NO_CONNECTED_ACCOUNT',
            'ACCOUNT_NOT_READY', 'SESSION_LOST', 'READ_BUDGET_EXHAUSTED', 'CAPABILITY_EXPIRED',
            'ADAPTER_COULD_NOT_PARSE', 'AMBIGUOUS_EXTERNAL_RESULT'
        )),
    CONSTRAINT thread_metric_snapshots_belong_to_thread
        FOREIGN KEY (organization_id, workspace_id, thread_id)
        REFERENCES observed_threads (organization_id, workspace_id, id)
);

CREATE INDEX thread_metric_snapshots_by_thread_time
    ON thread_metric_snapshots (thread_id, observed_at);

-- Snapshot jest pomiarem w czasie. Pomiar poprawiony po fakcie to nie pomiar —
-- ten sam wzorzec, co `audit_events` w 0001 i rewizje w 0003.
CREATE FUNCTION thread_metric_snapshots_are_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'thread_metric_snapshots: snapshoty metryk są append-only (docs/OUTCOMES.md §3 pkt 2). '
        'Poprawiony pomiar to nie pomiar; nowy odczyt to nowy wiersz';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER thread_metric_snapshots_no_update_or_delete
    BEFORE UPDATE OR DELETE ON thread_metric_snapshots
    FOR EACH ROW EXECUTE FUNCTION thread_metric_snapshots_are_append_only();

-- =============================================================================
-- user_profile_versions
-- =============================================================================
-- Cztery pola i ani jednego więcej (docs/OUTCOMES.md §3: „wystarczy profil z czterech
-- pól"). Siedemnaście pól `WorkspaceIntelligenceProfile` z roadmapy to Outcome C+.
CREATE TABLE user_profile_versions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    version            integer NOT NULL CHECK (version >= 1),
    knows_deeply       text NOT NULL CHECK (length(knows_deeply) > 0),
    positions          text NOT NULL CHECK (length(positions) > 0),
    would_never_claim  text NOT NULL CHECK (length(would_never_claim) > 0),
    voice_samples      text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, version),
    CONSTRAINT user_profile_versions_belong_to_workspace
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id)
);

-- =============================================================================
-- community_pictures
-- =============================================================================
CREATE TABLE community_pictures (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    community_id     uuid NOT NULL,
    version          integer NOT NULL CHECK (version >= 1),
    window_start     timestamptz NOT NULL,
    window_end       timestamptz NOT NULL,
    -- Struktura sprawdzana w kodzie (control_plane.observe.context), nie tutaj:
    -- kształt obrazu ma się zmieniać z promptem, a schemat nie.
    content          jsonb NOT NULL,
    thread_count     integer NOT NULL CHECK (thread_count >= 0),
    -- Ile odwołań do wątków w treści NIE wskazuje na wątek z okna. Zero to
    -- oczekiwane; każde więcej to halucynacja modelu, którą trzeba widzieć.
    invalid_ref_count integer NOT NULL CHECK (invalid_ref_count >= 0),
    model            text NOT NULL CHECK (length(model) > 0),
    effort           text NOT NULL CHECK (length(effort) > 0),
    prompt_name      text NOT NULL CHECK (length(prompt_name) > 0),
    prompt_sha256    text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, community_id, version),
    CONSTRAINT community_pictures_window_is_ordered CHECK (window_end > window_start),
    CONSTRAINT community_pictures_prompt_digest_is_sha256 CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT community_pictures_belong_to_community
        FOREIGN KEY (organization_id, workspace_id, community_id)
        REFERENCES communities (organization_id, workspace_id, id)
);

-- =============================================================================
-- thread_judgments — APPEND-ONLY
-- =============================================================================
CREATE TABLE thread_judgments (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id          uuid NOT NULL REFERENCES organizations (id),
    workspace_id             uuid NOT NULL,
    thread_id                uuid NOT NULL,
    decision                 text NOT NULL,
    reasoning                text NOT NULL CHECK (length(reasoning) > 0),
    what_user_could_add      text,
    confidence               double precision NOT NULL
                             CHECK (confidence >= 0.0 AND confidence <= 1.0),
    user_profile_version_id  uuid NOT NULL,
    -- NULL = osąd wydany przed pierwszym obrazem community. Z trójką i MATCH SIMPLE
    -- NULL pomija sprawdzenie klucza — tu to zamierzone, bo rodzic jest opcjonalny,
    -- a `organization_id`/`workspace_id` są NOT NULL, więc nic nie ucieka między najemców.
    community_picture_id     uuid,
    model                    text NOT NULL CHECK (length(model) > 0),
    effort                   text NOT NULL CHECK (length(effort) > 0),
    prompt_name              text NOT NULL CHECK (length(prompt_name) > 0),
    prompt_sha256            text NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT thread_judgments_decision_is_known
        CHECK (decision IN ('ENGAGE', 'IGNORE', 'WAIT')),
    -- Ta sama reguła, co `JudgmentRecord` w contracts: ENGAGE musi nazwać wkład.
    -- Dwie niezależne blokady na tych samych drzwiach (kod i baza), bo przez te
    -- drzwi przeszłoby „wejdźmy, bo możemy" — slop o krok wcześniej (§4).
    CONSTRAINT thread_judgments_engage_names_the_contribution
        CHECK (decision <> 'ENGAGE'
               OR (what_user_could_add IS NOT NULL AND length(what_user_could_add) > 0)),
    CONSTRAINT thread_judgments_prompt_digest_is_sha256 CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT thread_judgments_belong_to_thread
        FOREIGN KEY (organization_id, workspace_id, thread_id)
        REFERENCES observed_threads (organization_id, workspace_id, id),
    CONSTRAINT thread_judgments_grounded_in_profile
        FOREIGN KEY (organization_id, workspace_id, user_profile_version_id)
        REFERENCES user_profile_versions (organization_id, workspace_id, id),
    CONSTRAINT thread_judgments_read_picture
        FOREIGN KEY (organization_id, workspace_id, community_picture_id)
        REFERENCES community_pictures (organization_id, workspace_id, id) MATCH SIMPLE
);

CREATE INDEX thread_judgments_by_decision_time
    ON thread_judgments (organization_id, decision, created_at DESC);

CREATE FUNCTION thread_judgments_are_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'thread_judgments: osąd jest append-only (docs/KIERUNEK-PRODUKTU.md §8, §9). '
        'Zmiana zdania to nowy osąd albo feedback operatora, nie edycja starego';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER thread_judgments_no_update_or_delete
    BEFORE UPDATE OR DELETE ON thread_judgments
    FOR EACH ROW EXECUTE FUNCTION thread_judgments_are_append_only();

-- =============================================================================
-- judgment_feedback
-- =============================================================================
CREATE TABLE judgment_feedback (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    judgment_id      uuid NOT NULL,
    verdict          text NOT NULL,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT judgment_feedback_verdict_is_known
        CHECK (verdict IN ('AGREE', 'DISAGREE', 'IRRELEVANT')),
    CONSTRAINT judgment_feedback_belongs_to_judgment
        FOREIGN KEY (organization_id, workspace_id, judgment_id)
        REFERENCES thread_judgments (organization_id, workspace_id, id)
);

-- Operator może zmienić zdanie: kolejny wiersz, nie UPDATE. „Ostatni” liczy odczyt.
CREATE INDEX judgment_feedback_by_judgment_time
    ON judgment_feedback (judgment_id, created_at DESC);

COMMIT;
