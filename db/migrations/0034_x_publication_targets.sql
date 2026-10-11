-- =============================================================================
-- 0034. CEL PUBLIKACJI NA X.
--
-- Do 26.09 `publication_requests_target_matches_kind` znało tylko Reddita: odpowiedź celowała
-- w `t1_`/`t3_`, własny post w `r/<społeczność>`. Integracja X (zadanie z 26.09, rezultat 2)
-- dokłada dwa kształty, każdy z przedrostkiem platformy, żeby identyfikator X nigdy nie dał się
-- pomylić z fullname Reddita:
--
--   * odpowiedź na X celuje w JEDEN post: `x:<id statusu>` (same cyfry, do 25);
--   * własny post na X celuje w oś czasu konta: `x:@<uchwyt>` (1 do 15 znaków, reguła X).
--
-- Kształt celu nie jest tu ozdobą: po nim publikator wybiera ścieżkę (`pilot.publisher`, gałąź
-- X), a zgoda liczy odcisk z tego samego pola. Stare wiersze nie zmieniają znaczenia, bo
-- warunek Reddita zostaje dosłownie ten sam.
-- =============================================================================

BEGIN;

ALTER TABLE publication_requests
    DROP CONSTRAINT publication_requests_target_matches_kind,
    ADD CONSTRAINT publication_requests_target_matches_kind CHECK (
        CASE kind
            WHEN 'REPLY' THEN
                target_external_id ~ '^t[13]_[a-z0-9]{1,12}$'
                OR target_external_id ~ '^x:[0-9]{1,25}$'
            ELSE
                target_external_id ~ '^r/[A-Za-z0-9_]{2,21}$'
                OR target_external_id ~ '^x:@[A-Za-z0-9_]{1,15}$'
        END
    );

COMMIT;
