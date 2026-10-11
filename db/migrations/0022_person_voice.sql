-- =============================================================================
-- Migracja 0022 — głos osoby w modelu P1, wersjonowany
--
-- Pilot pisał komentarze z profilem założyciela: `user_profile_versions`, cztery pola
-- (co zna głęboko, jakie ma stanowiska, czego nigdy nie powie, próbki głosu). W modelu P1
-- jedna przestrzeń ma WIELE osób, a tamta tabela ma po jednym profilu na workspace. Sesja
-- pisząca za Annę i sesja pisząca za Janem dostawałyby ten sam głos, więc albo
-- komentarz byłby nie jego, albo sesja pisałaby bez głosu. Stąd głos przypięty do
-- `person_profiles`.
--
-- WERSJE, NIE EDYCJA W MIEJSCU. Głos to zapis tego, kim ktoś jest dzisiaj; zmiana nadpisująca
-- poprzednią wersję kasuje dowód, z jakim głosem powstał komentarz sprzed tygodnia. Wersje są
-- dopisywane i pilnuje tego wyzwalacz, nie tylko dobre intencje kodu, bo jedna migracja danych
-- albo jedna pomyłka w psql wystarczy, żeby „append-only" przestało być prawdą (tę lekcję
-- zapisano już w 0002 dla `audit_events`).
--
-- BEZ KOPIOWANIA DANYCH TĄ MIGRACJĄ. Nie przenosimy tu treści z `user_profile_versions` do
-- żadnego profilu osoby, choć technicznie byłoby to jedno `INSERT ... SELECT`. Profil pilota
-- należy do konkretnego człowieka i to on ma zdecydować, czy jego głos staje się głosem
-- profilu w nowym modelu. Ekran „Profile i konta" proponuje przejęcie treści na kliknięcie,
-- a tabela zostaje pusta do pierwszej decyzji człowieka.
--
-- KTO ZAPISAŁ. `created_by` wskazuje użytkownika z 0018. NULL jest dopuszczony dla jednego
-- przypadku: sesja otwarta wspólnym hasłem operatora sprzed P7 nie zna użytkownika, a wtedy
-- ekran i narzędzie mówią „operator", zamiast podstawiać czyjeś nazwisko.
-- =============================================================================

BEGIN;

CREATE TABLE person_voice_versions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    person_profile_id  uuid NOT NULL,
    version            integer NOT NULL CHECK (version >= 1),
    -- Te same cztery pola, co w `user_profile_versions` (0004, L192-207) i pod tymi samymi
    -- nazwami. Inna nazwa tego samego pola zmusiłaby prompt i narzędzie do tłumaczenia jednego
    -- na drugie, a to jest miejsce, w którym po cichu gubi się „czego nigdy nie powiem".
    knows_deeply       text NOT NULL CHECK (length(btrim(knows_deeply, E' \t\r\n')) > 0),
    positions          text NOT NULL CHECK (length(btrim(positions, E' \t\r\n')) > 0),
    would_never_claim  text NOT NULL CHECK (length(btrim(would_never_claim, E' \t\r\n')) > 0),
    -- Jedyne pole, które wolno zostawić puste: próbek cudzego pisania można nie mieć.
    voice_samples      text NOT NULL,
    created_by         uuid REFERENCES users (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    -- Numeracja per OSOBA, nie per workspace: druga osoba zaczyna od wersji 1. Unikalność jest
    -- też wyścigiem załatwionym w bazie — dwa równoczesne zapisy tej samej wersji nie przejdą
    -- oba, a przegrany dostanie błąd zamiast po cichu nadpisać numer.
    UNIQUE (organization_id, workspace_id, person_profile_id, version),
    CONSTRAINT person_voice_versions_point_at_profile
        FOREIGN KEY (organization_id, workspace_id, person_profile_id)
        REFERENCES person_profiles (organization_id, workspace_id, id)
);

CREATE INDEX person_voice_versions_newest_first
    ON person_voice_versions (organization_id, workspace_id, person_profile_id, version DESC);

-- -----------------------------------------------------------------------------
-- Append-only
-- -----------------------------------------------------------------------------
-- Ten sam wzorzec i ten sam powód, co `audit_events_append_only` w 0002: wyzwalacz wierszowy
-- na UPDATE i DELETE plus wyzwalacz na TRUNCATE, bo TRUNCATE nie ma wierszy do obejrzenia
-- i sam wierszowy przepuściłby wyczyszczenie tabeli bez jednego protestu. Słowo „append-only"
-- zostaje w komunikacie: testy dopasowują je wprost.

CREATE FUNCTION person_voice_versions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'person_voice_versions jest append-only: % jest niedozwolone', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER person_voice_versions_no_mutation
    BEFORE UPDATE OR DELETE ON person_voice_versions
    FOR EACH ROW EXECUTE FUNCTION person_voice_versions_append_only();

CREATE TRIGGER person_voice_versions_no_truncate
    BEFORE TRUNCATE ON person_voice_versions
    FOR EACH STATEMENT EXECUTE FUNCTION person_voice_versions_append_only();

COMMIT;
