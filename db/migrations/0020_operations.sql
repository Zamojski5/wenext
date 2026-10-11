-- =============================================================================
-- Migracja 0020: gotowość operacyjna usługi (P7 etap B, ROADMAP-PIVOT)
--
-- P7: „usługa działa niezależnie od laptopa autora". Cztery tabele, każda odpowiada na inne
-- pytanie, którego dziś nie da się zadać danym:
--
--   1. `service_heartbeats` — CZY TO W OGÓLE CHODZIŁO. Bez tego „usługa działała" jest zdaniem
--      opartym na tym, że nikt się nie skarżył. Wiersz per komponent per interwał, pisany przez
--      sam komponent. Dostępność liczy się potem jako „ile interwałów miało wiersz", a nie jako
--      „czy teraz odpowiada": to drugie mierzy chwilę, a nie tydzień.
--   2. `incidents` — CO BYŁO NIE TAK I JAK DŁUGO. Zamykane automatycznie, gdy komponent wraca,
--      bo incydent, którego nikt nie zamknął, po tygodniu wygląda jak trwająca awaria.
--   3. `cost_ledger` — ILE TO KOSZTOWAŁO, w jednostkach, nie w złotówkach. Ceny zmieniają się
--      bez naszego udziału, a „12 tur modelu" zostaje prawdą na zawsze; przeliczenie na pieniądze
--      jest sprawą raportu, nie zapisu.
--   4. `org_limits` — SUFIT NA CAŁĄ ORGANIZACJĘ. `project_delegations.budget` z 0017 ogranicza
--      JEDEN projekt; dziesięć projektów z limitem sześciu publikacji to sześćdziesiąt publikacji
--      i nikt tego nie podpisywał.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie zapisuje kopii zapasowych ani nie wie o nich. Kopia jest
-- plikiem poza bazą (`tools/dev/backup.sh`), a tabela „nasze kopie" w bazie, którą właśnie
-- odtwarzamy, byłaby spisem, którego przy odtwarzaniu nie ma.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Czy to chodziło
-- -----------------------------------------------------------------------------
CREATE TABLE service_heartbeats (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    component        text NOT NULL,
    -- POCZĄTEK INTERWAŁU, nie moment zapisu. Dzięki temu „ile interwałów miało wiersz" jest
    -- zwykłym liczeniem, a nie kubełkowaniem znaczników czasu przy każdym raporcie, i dwa
    -- procesy tego samego komponentu w jednym interwale dają jeden wiersz, nie dwa.
    interval_start   timestamptz NOT NULL,
    -- Co komponent o sobie wie: wersja, kolejka, cokolwiek pomocnego przy czytaniu awarii.
    -- Nie jest to stan zdrowia: wiersz JEST stanem zdrowia, a jego brak jest jego brakiem.
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, component, interval_start),
    CONSTRAINT service_heartbeats_component_is_known CHECK (component IN (
        'DISPATCHER', 'COLLECTOR', 'PUBLISHER', 'CAPSULE', 'CODEX_CONNECTION'
    ))
);

CREATE INDEX service_heartbeats_by_component
    ON service_heartbeats (organization_id, component, interval_start DESC);

-- -----------------------------------------------------------------------------
-- Co było nie tak
-- -----------------------------------------------------------------------------
CREATE TABLE incidents (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    component        text NOT NULL,
    started_at       timestamptz NOT NULL,
    -- NULL znaczy „trwa". Zamykane automatycznie, gdy komponent znowu bije.
    ended_at         timestamptz,
    -- Zdanie dla człowieka. Wolny tekst, bo powód awarii bywa cytatem z platformy („403 z
    -- Reddita"), a zamknięta lista kazałaby go zgubić albo wcisnąć w kategorię, której nie ma.
    reason           text NOT NULL CHECK (length(btrim(reason)) > 0),
    -- KTO TO ZAUWAŻYŁ. Incydent zgłoszony przez sam komponent i incydent wywnioskowany z braku
    -- pulsu to dwa różne dowody, a po tygodniu nie da się ich odróżnić inaczej niż po tej kolumnie.
    detected_by      text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    CONSTRAINT incidents_component_is_known CHECK (component IN (
        'DISPATCHER', 'COLLECTOR', 'PUBLISHER', 'CAPSULE', 'CODEX_CONNECTION'
    )),
    CONSTRAINT incidents_detected_by_is_known CHECK (detected_by IN (
        'SELF_REPORT', 'MISSING_HEARTBEAT', 'HUMAN'
    )),
    CONSTRAINT incidents_end_after_start CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- JEDEN OTWARTY INCYDENT NA KOMPONENT. Indeks, nie kod: dwa procesy, które zauważyły tę samą
-- awarię, zgłoszą ją oba, a dwa otwarte incydenty o tym samym znaczyłyby podwójną niedostępność
-- w raporcie tygodnia. Ta sama zasada, którą 0017 wymusza jedną żywą delegację na projekt.
CREATE UNIQUE INDEX incidents_one_open_per_component
    ON incidents (organization_id, component)
    WHERE ended_at IS NULL;

CREATE INDEX incidents_by_time
    ON incidents (organization_id, started_at DESC);

-- -----------------------------------------------------------------------------
-- Ile to kosztowało
-- -----------------------------------------------------------------------------
CREATE TABLE cost_ledger (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    -- NULL znaczy „koszt organizacji, nie projektu": puls, odczyt kolektora, praca, której nie
    -- da się uczciwie przypisać do jednego celu. Zero byłoby kłamstwem, a wymuszenie projektu
    -- kazałoby zgadywać.
    project_id       uuid,
    kind             text NOT NULL,
    -- JEDNOSTKI, NIE PIENIĄDZE. Cena zmienia się bez naszego udziału i wstecz; „12 tur modelu"
    -- zostaje prawdą. Przeliczenie na złotówki robi raport, na własną odpowiedzialność.
    units            numeric(14, 4) NOT NULL CHECK (units >= 0),
    -- Na co dokładnie wskazuje ten wiersz: `wakeup:<id>`, `session:<id>`, `request:<id>`.
    -- Bez tego suma jest liczbą, której nie da się rozłożyć z powrotem na zdarzenia.
    source_ref       text NOT NULL CHECK (length(btrim(source_ref)) > 0),
    occurred_at      timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Ten sam koszt policzony dwa razy to ten sam wiersz. Zasilanie idzie z tabel, które już
    -- istnieją (wakeups, session_events, publikacje), więc ponowione zasilanie jest normalne,
    -- a bez tego więzu podwajałoby rachunek przy każdym przebiegu.
    UNIQUE (organization_id, kind, source_ref),
    CONSTRAINT cost_ledger_kind_is_known CHECK (kind IN (
        'MODEL_TURN', 'TOOL_CALL', 'PUBLICATION', 'FETCH'
    ))
);

CREATE INDEX cost_ledger_by_time
    ON cost_ledger (organization_id, occurred_at DESC);
CREATE INDEX cost_ledger_by_project
    ON cost_ledger (organization_id, project_id, occurred_at DESC)
    WHERE project_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Sufit na całą organizację
-- -----------------------------------------------------------------------------
CREATE TABLE org_limits (
    -- Jeden wiersz na organizację: to jest USTAWIENIE, nie historia. Historia zmian jest w
    -- dzienniku audytu, gdzie ma sens, bo niesie autora zmiany.
    organization_id       uuid PRIMARY KEY REFERENCES organizations (id),
    -- NULL znaczy „bez sufitu organizacyjnego", i to jest inna rzecz niż zero: zero znaczy
    -- „nic nie wolno". Ta sama zasada, co w budżecie delegacji z 0017.
    model_turns_per_day   integer CHECK (model_turns_per_day IS NULL OR model_turns_per_day >= 0),
    publications_per_day  integer CHECK (publications_per_day IS NULL OR publications_per_day >= 0),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            uuid,
    CONSTRAINT org_limits_updated_by_operator
        FOREIGN KEY (organization_id, updated_by)
        REFERENCES operators (organization_id, id)
);

COMMIT;
