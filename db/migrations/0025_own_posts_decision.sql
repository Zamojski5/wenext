-- =============================================================================
-- Migracja 0025: czy w tej społeczności wolno zakładać własne wątki (P4 etap C)
--
-- DECYZJA CZŁOWIEKA MA SWOJE MIEJSCE, ODDZIELNE OD OBSERWACJI MODELU. Do tej pory bramka
-- własnych postów czytała klucz `own_posts` z karty głosu (`community_voice_cards.content`).
-- Karta głosu jest WNIOSKIEM MODELU z przeczytanych komentarzy: ma wersję, okno, nazwę promptu
-- i nazwę modelu, bo to jest artefakt obserwacji. Dopisanie do niej rozstrzygnięcia człowieka
-- zrobiłoby z niej dokument dwóch autorów, z których jeden jest podpisany, a drugi nie. Gorzej:
-- następna wersja karty, wygenerowana przez model, kasowałaby decyzję człowieka bez śladu.
--
-- Stąd kolumna na `communities`, i stąd jej trzy stany:
--
--   NULL   nikt nie zdecydował. To jest stan początkowy KAŻDEJ społeczności i to jest odmowa:
--          brak zasady nie znaczy, że wolno wszystko. Ekran ma o to pytać, a nie zakładać.
--   false  człowiek powiedział NIE. Odmowa, z innym zdaniem niż przy NULL, bo co innego ma
--          zrobić człowiek: przy NULL ma zdecydować, przy false nie ma nic do roboty.
--   true   człowiek powiedział TAK. Dopiero wtedy `draft.create` z kind=OWN_POST ma szansę.
--
-- BOOLEAN NULLOWALNY, NIE TRZY NAPISY. Trzeci stan jest tu brakiem wartości i dokładnie tym
-- jest w rzeczywistości; kolumna tekstowa z 'UNDECIDED' wymagałaby CHECK-a i dawałaby czwarty
-- stan (pusty napis) za darmo.
--
-- KTO I KIEDY: `own_posts_decided_by` i `own_posts_decided_at`. Nie po to, żeby kogoś rozliczać,
-- tylko dlatego, że to jest zgoda na publikowanie w czyjejś społeczności i za pół roku ktoś
-- zapyta, skąd się wzięła. Wpis w `audit_events` powstaje osobno, przy każdej zmianie; te dwie
-- kolumny odpowiadają na pytanie „jaki jest stan i czyj", bez czytania dziennika.
-- =============================================================================

BEGIN;

ALTER TABLE communities
    ADD COLUMN own_posts_allowed boolean,
    -- Operator, nie użytkownik, i to jest ta sama tożsamość, którą podpisuje się powierzenie
    -- (`project_delegations.signed_by`): sesja założyciela otwarta hasłem operatora nie ma
    -- `user_id`, a decyzję o własnych postach ma prawo podjąć. Para z organizacją w kluczu
    -- obcym, żeby operator z innej organizacji nie mógł stać pod tą decyzją.
    ADD COLUMN own_posts_decided_by uuid,
    ADD COLUMN own_posts_decided_at timestamptz,
    ADD CONSTRAINT communities_own_posts_decided_by_operator
        FOREIGN KEY (organization_id, own_posts_decided_by)
        REFERENCES operators (organization_id, id),
    -- Rozstrzygnięcie ma autora i moment albo nie ma go wcale. Wiersz mówiący „wolno", o którym
    -- nie wiadomo, kto tak powiedział, jest zgodą bez podpisu.
    ADD CONSTRAINT communities_own_posts_decision_has_an_author CHECK (
        (own_posts_allowed IS NULL AND own_posts_decided_by IS NULL
             AND own_posts_decided_at IS NULL)
        OR (own_posts_allowed IS NOT NULL AND own_posts_decided_by IS NOT NULL
             AND own_posts_decided_at IS NOT NULL)
    );

COMMIT;
