-- =============================================================================
-- Migracja 0005 — głos społeczności (etap 3, kamień M0; docs/OUTCOMES.md §8)
--
-- Dwie rzeczy, obie w służbie jednej decyzji założyciela: komentarze agenta nie mogą
-- być techniczne ani korporacyjne, więc zanim powstanie jakikolwiek szkic, system ma
-- zmierzyć, JAK piszą ludzie w danym community — i pokazać to na karcie.
--
--   1. `observed_comments.score` + `score_unknown_reason` — punkty komentarza tam,
--      gdzie platforma je daje (Reddit: `score` z `.json`, chyba że `score_hidden`),
--      a gdzie nie daje (Hacker News nie pokazuje punktów komentarzy) — jawny powód.
--      Ten sam wzorzec `Observed[int]`, co w `thread_metric_snapshots` z 0004:
--      dwie kolumny i CHECK „dokładnie jedno z pary". Istniejące wiersze (1754
--      komentarze HN w chwili pisania) dostają powód NOT_EXPOSED_BY_PLATFORM, bo to
--      jest prawda o HN, a nie „nie wiemy, czy nie wiemy".
--
--   2. `community_voice_cards` — wersjonowana „karta głosu" community: co odpowiedzi
--      zbierają odpowiedzi (a nie mediana), miary deterministyczne (długości, udział
--      1. osoby, pytań, linków) i tekst modelu prostymi słowami z cytatami wskazującymi
--      na konkretne komentarze (`external_id`). Ten sam kształt, co `community_pictures`:
--      wersja per community, okno czasu, `content` jsonb, model/effort/prompt z hashem.
--
-- CZEGO TU NIE MA, świadomie: żadnej tabeli szkiców ani publikacji — etap 3 pkt 1–3
-- używa istniejących `action_proposals` / `execution_intents` z 0003; żadnego
-- append-only triggera na kartach — karta jest wersjonowana, nie mierzona, jak obraz.
--
-- Wzorce odziedziczone: tenant triple (organization_id, workspace_id, id) w FK,
-- zamknięte listy jako text + nazwany CHECK (parytet z `contracts` sprawdza
-- `services/db_schema/tests/test_enum_parity.py` po NAZWIE constraintu).
-- =============================================================================

BEGIN;

-- 1. Punkty komentarza jako para Observed[int].
ALTER TABLE observed_comments
    ADD COLUMN score integer,
    ADD COLUMN score_unknown_reason text;

-- Backfill: wszystko, co leży w tabeli, pochodzi z Hacker News przez Algolię, która
-- punktów komentarzy nie zwraca. To jest fakt o platformie, więc powód, nie NULL.
UPDATE observed_comments
   SET score_unknown_reason = 'NOT_EXPOSED_BY_PLATFORM'
 WHERE score IS NULL AND score_unknown_reason IS NULL;

ALTER TABLE observed_comments
    ADD CONSTRAINT observed_comments_score_observed_or_unknown
        CHECK ((score IS NULL) <> (score_unknown_reason IS NULL)),
    ADD CONSTRAINT observed_comments_score_unknown_reason_is_known
        CHECK (score_unknown_reason IS NULL OR score_unknown_reason IN (
            'NOT_EXPOSED_BY_PLATFORM', 'NOT_VISIBLE_TO_THIS_ACCOUNT', 'NO_CONNECTED_ACCOUNT',
            'ACCOUNT_NOT_READY', 'SESSION_LOST', 'READ_BUDGET_EXHAUSTED', 'CAPABILITY_EXPIRED',
            'ADAPTER_COULD_NOT_PARSE', 'AMBIGUOUS_EXTERNAL_RESULT'
        ));

-- Liczenie odpowiedzi pod komentarzem (proxy „co zbiera rozmowę" tam, gdzie nie ma
-- punktów) idzie po (thread_id, parent_external_id) — bez indeksu to skan tabeli
-- komentarzy na każdy komentarz w oknie.
CREATE INDEX observed_comments_by_thread_parent
    ON observed_comments (thread_id, parent_external_id);

-- 2. Karta głosu community — wersjonowana jak obraz.
CREATE TABLE community_voice_cards (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    community_id     uuid NOT NULL,
    version          integer NOT NULL CHECK (version >= 1),
    window_start     timestamptz NOT NULL,
    window_end       timestamptz NOT NULL,
    -- Struktura sprawdzana w kodzie (control_plane.observe.voice), nie tutaj: kształt
    -- karty ma się zmieniać z promptem, a schemat nie.
    content          jsonb NOT NULL,
    -- Ile komentarzy z okna weszło do pomiaru i ile cytatów model wskazał na komentarz,
    -- którego w oknie nie było (odrzucone przed zapisem, ale policzone — jak
    -- `invalid_ref_count` obrazu).
    comment_count    integer NOT NULL CHECK (comment_count >= 0),
    invalid_quote_count integer NOT NULL CHECK (invalid_quote_count >= 0),
    model            text NOT NULL CHECK (length(model) > 0),
    effort           text NOT NULL CHECK (length(effort) > 0),
    prompt_name      text NOT NULL CHECK (length(prompt_name) > 0),
    prompt_sha256    text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (organization_id, workspace_id, community_id, version),
    CONSTRAINT community_voice_cards_window_is_ordered CHECK (window_end > window_start),
    CONSTRAINT community_voice_cards_prompt_digest_is_sha256 CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT community_voice_cards_belong_to_community
        FOREIGN KEY (organization_id, workspace_id, community_id)
        REFERENCES communities (organization_id, workspace_id, id)
);

COMMIT;
