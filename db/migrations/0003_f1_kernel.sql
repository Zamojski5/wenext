-- =============================================================================
-- Migracja 0003 — schemat correctness kernela E1.F1 (zakres L5184-5219)
--
-- Trzy grupy tabel i po jednym zdaniu, dlaczego każda tu jest:
--
--   * kolejka — `jobs`, `job_attempts`, `job_leases`. „PostgreSQL jobs jako
--     canonical state; dispatcher w tym samym procesie na SELECT ... FOR UPDATE
--     SKIP LOCKED” (L5193) plus „job leases, heartbeat, reaper, attempt limit
--     i terminalizacja ostatniej próby” (L5194). Broker jest odroczony do E3
--     i wozi wyłącznie `job_id`, dlatego stan mieszka tutaj, a nie w brokerze.
--   * ścieżka zgody — `action_proposals`, `action_proposal_revisions`,
--     `approval_decisions`, `execution_intents`, `execution_attempts`.
--     „immutable schematy ActionProposal / ExecutionIntent / AutomationRun
--     z payload hash” (L5198), a DoD dokłada dwa zdania, których nie da się
--     spełnić w kodzie aplikacji: „żaden zapisujący side effect nie omija
--     ExecutionIntent” (L5217) i „approval wskazuje dokładny payload hash;
--     edycja treści unieważnia zgodę” (L5218).
--   * dane wrażliwe — `artifacts`, `profile_deks`. „encrypted profile storage,
--     artifacts ACL i retention” (L5196), osobne DEK per profil (L3914, D-018).
--
-- CZEGO TU NIE MA, świadomie (L5500-5501 zakazuje rozlania zakresu):
--   * `outbox_events` i `inbox_receipts` — E3 (L5205), transport, nie stan;
--   * RLS, `FORCE ROW LEVEL SECURITY`, role login `dispatcher_login` i spółka —
--     E3 (L5206, L3878-3884). DoD L5215 mówi wprost: kolumny tenantowe
--     i composite FK istnieją NAWET GDY RLS jest wyłączone, i to jest cała
--     izolacja, jaką ma E1;
--   * `tasks`, `task_dependencies`, `publications`, `action_budget_*`,
--     `thread_quality_assessments` — F3/F4/F5;
--   * `automation_runs` — D-020: tego łańcucha nie ma w dokumencie ani razu,
--     model danych zna wyłącznie `execution_attempts` (L3129). `AutomationRun`
--     jest TYPEM w `contracts` nad tą tabelą, nie osobną tabelą.
--
-- DWA WZORCE OBOWIĄZUJĄCE W CAŁYM PLIKU:
--
--  1. Zamknięte listy wartości to `text` + NAZWANY `CHECK`, nie `CREATE TYPE`.
--     `ALTER TYPE ... ADD VALUE` nie działa w bloku transakcyjnym i jest
--     nieodwracalny, więc native enum zamienia „źle nazwaliśmy stan” w migrację
--     jednokierunkową. Wzorzec jest już w 0001 — `capsule_mode_transitions.reason`
--     w okolicach linii 320 tego pliku. CHECK-i są NAZWANE, żeby
--     `tests/test_enum_parity.py` mógł ich SZUKAĆ po nazwie i PAŚĆ, gdy nie ma:
--     „CHECK wspominający tę kolumnę” po literówce nie znajduje nic i milczy.
--
--  2. Tabela workspace'owa ma `organization_id NOT NULL`, `workspace_id NOT NULL`
--     i klucz obcy w kanonicznej TRÓJCE
--     `(organization_id, workspace_id, parent_id) -> parent(organization_id, workspace_id, id)`
--     (L3874-3876) — TRÓJCE, nie parze. Para `(organization_id, parent_id)`
--     przepuściłaby dziecko z workspace'u A pod rodzica z workspace'u B tej samej
--     organizacji. Tabela, której rodzicem jest sam workspace, ma parę
--     `(organization_id, workspace_id) -> workspaces(organization_id, id)`.
--     Tabele Z MIGRACJI 0001 nie dostają retrofitu `workspace_id` (D-020).
-- =============================================================================

BEGIN;

-- =============================================================================
-- Kolejka jobów
-- =============================================================================

-- -----------------------------------------------------------------------------
-- jobs — trzynaście kolumn z L3823-3835
-- -----------------------------------------------------------------------------
-- `workspace_id` jest NULLABLE, bo tak stoi w dokumencie (L3825: „workspace_id
-- nullable”) i bo L3884 na tym się opiera: „Job organizacyjny z `workspace_id
-- IS NULL` przechodzi wyłącznie przez org-scope policy”. To czyni `jobs` tabelą
-- o MIESZANYM scope, nie tabelą workspace'ową — i dlatego nie ma tu trójki
-- tenantowej, tylko para z MATCH SIMPLE, dokładnie jak w `audit_events` (0002).
--
-- Czego dokument dla tej tabeli NIE definiuje, a co jest tu wypisane jako brak:
-- L3871 wymaga od rekordów o mieszanym scope „constraint zgodności
-- scope_level/workspace_id”, ale kolumny `scope_level` nie ma na liście
-- trzynastu i dołożenie jej byłoby wymyśleniem pola. NIE JEST WYEGZEKWOWANE.

CREATE TABLE jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES organizations (id),
    workspace_id      uuid,
    job_type          text NOT NULL CHECK (length(job_type) > 0),
    routing_key       text NOT NULL CHECK (length(routing_key) > 0),
    -- Kolejność dispatchu. Weighted fairness jest POZA F1 (L5501) — tu jest
    -- wyłącznie liczba, po której dispatcher sortuje.
    priority          integer NOT NULL,
    -- Payload powyżej limitu treści idzie referencją, nie wierszem (D-017: 1 MiB).
    -- Limit mieszka w jednym module konfiguracji control plane, nie w CHECK-u tutaj:
    -- próg zduplikowany w dwóch miejscach rozjeżdża się bez ostrzeżenia.
    payload_ref       text CHECK (payload_ref IS NULL OR length(payload_ref) > 0),
    -- Fencing: `dispatch_version` rośnie przy każdym ponownym wystawieniu joba,
    -- `lock_version` obsługuje optimistic locking komend domenowych (L5192).
    dispatch_version  bigint NOT NULL DEFAULT 1 CHECK (dispatch_version >= 1),
    status            text NOT NULL,
    not_before        timestamptz NOT NULL DEFAULT now(),
    -- BEZ DEFAULT-u. D-017 zamraża wartość 5, ale trzyma ją w module konfiguracji
    -- control plane — DEFAULT tutaj byłby drugim źródłem tego samego progu.
    attempt_limit     integer NOT NULL CHECK (attempt_limit >= 1),
    idempotency_key   text NOT NULL CHECK (length(idempotency_key) > 0),
    lock_version      bigint NOT NULL DEFAULT 1 CHECK (lock_version >= 1),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    -- „internal deduplication” (L2820): ten sam klucz nie tworzy drugiego joba.
    UNIQUE (organization_id, idempotency_key),
    -- Dziewięć statusów: pięć cyklu życia plus cztery WAITING_* z `contracts`
    -- (L2144). Joby w stanach WAITING_* nie trzymają lease'u runtime'u
    -- (L2147, L5288). Parytet z kontraktami pilnuje `test_enum_parity.py`.
    CONSTRAINT jobs_status_is_known CHECK (status IN (
        'READY', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED',
        'WAITING_AUTH', 'WAITING_QUOTA', 'WAITING_CAPABILITY', 'WAITING_RETRY'
    )),
    CONSTRAINT jobs_workspace_belongs_to_organization
        FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id) MATCH SIMPLE
);

-- Indeks pod dokładnie jedno zapytanie: `SELECT ... FOR UPDATE SKIP LOCKED`
-- dispatchera (L5193). Partial, bo joby terminalne nigdy nie wracają do puli.
CREATE INDEX jobs_ready_for_dispatch
    ON jobs (priority, not_before)
    WHERE status = 'READY';

-- -----------------------------------------------------------------------------
-- job_attempts — pola z L3838
-- -----------------------------------------------------------------------------
-- Tabela ORGANIZACYJNA, nie workspace'owa: próba należy do joba, a job może być
-- organizacyjny. Klucz obcy jest więc parą `(organization_id, job_id)` i jest
-- w pełni egzekwowany — obie kolumny są NOT NULL. Trójka z NULL-owym
-- `workspace_id` i MATCH SIMPLE byłaby GORSZA niż para: przy NULL-u MATCH SIMPLE
-- uznaje klucz za spełniony BEZ SPRAWDZANIA `job_id`, czyli próba mogłaby
-- wskazywać na nieistniejący job.

CREATE TABLE job_attempts (
    attempt_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    job_id           uuid NOT NULL,
    attempt_no       integer NOT NULL CHECK (attempt_no >= 1),
    dispatch_version bigint NOT NULL CHECK (dispatch_version >= 1),
    -- Liczy się od 1 jak generacja lease'u profilu (L2372); 0 znaczyłoby „bez lease'u”.
    lease_generation bigint NOT NULL CHECK (lease_generation >= 1),
    lease_owner      uuid NOT NULL,
    status           text NOT NULL,
    not_before       timestamptz,
    heartbeat_at     timestamptz,
    expires_at       timestamptz,
    started_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    failure_class    text,
    UNIQUE (organization_id, attempt_id),
    -- „Obowiązuje UNIQUE(job_id, attempt_no)” — L3838 dosłownie. To jest schemat
    -- limitu prób: gdyby dwa wiersze mogły twierdzić, że są próbą nr 3, liczenie
    -- prób wobec `attempt_limit` liczyłoby wiersze bez znaczenia (L5214).
    UNIQUE (job_id, attempt_no),
    CONSTRAINT job_attempts_status_is_known CHECK (status IN (
        -- LEASE_EXPIRED jest osobno od FAILED świadomie: reaper zamykający próbę
        -- z wygasłym lease'em to nie to samo zdarzenie, co worker raportujący
        -- porażkę, a limit prób z L5214 liczy oba. Zlanie ich w jedno zabrałoby
        -- dowód dla „restart komponentu nie gubi taska” (L5212).
        'RUNNING', 'SUCCEEDED', 'FAILED', 'LEASE_EXPIRED'
    )),
    -- Próba w toku nie ma czasu zakończenia, a zakończona go ma. Wiersz łamiący
    -- tę równoważność to dokładnie ta księgowość, która w GATE-3 kazała
    -- `successful_cycles` rosnąć przed testem resume.
    CONSTRAINT running_attempt_is_not_completed CHECK (
        (status = 'RUNNING') = (completed_at IS NULL)
    ),
    CONSTRAINT job_attempts_completed_after_started CHECK (
        completed_at IS NULL OR completed_at >= started_at
    ),
    FOREIGN KEY (organization_id, job_id) REFERENCES jobs (organization_id, id)
);

CREATE INDEX job_attempts_by_job ON job_attempts (job_id, attempt_no);

-- -----------------------------------------------------------------------------
-- job_leases — pola z L3838
-- -----------------------------------------------------------------------------
-- Ta tabela jest sednem DoD „dwa workery nie przejmują tego samego rekordu”
-- (L5211). Mechanizmem jest partial unique, nie kod dispatchera: CHECK ani
-- trigger liczący wiersze nie wytrzymują dwóch równoległych INSERT-ów, a partial
-- unique tak (ta sama lekcja co `profile_leases_one_active_per_profile` w 0001).
--
-- UNIQUE(job_id, attempt_no) NIE jest tu powtarzane, i to jest decyzja, nie
-- przeoczenie: przejęcie lease'u dla TEJ SAMEJ próby podbija `lease_generation`,
-- więc unikalność po numerze próby zablokowałaby przejęcie. Zdanie z L3838
-- egzekwuje `job_attempts` powyżej; tutaj monotoniczność generacji pilnuje
-- UNIQUE(job_id, lease_generation).

CREATE TABLE job_leases (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    job_id           uuid NOT NULL,
    attempt_id       uuid NOT NULL,
    attempt_no       integer NOT NULL CHECK (attempt_no >= 1),
    dispatch_version bigint NOT NULL CHECK (dispatch_version >= 1),
    lease_generation bigint NOT NULL CHECK (lease_generation >= 1),
    lease_owner      uuid NOT NULL,
    status           text NOT NULL,
    not_before       timestamptz,
    -- Czas bazy, nie zegar workera (L2668).
    acquired_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    started_at       timestamptz,
    completed_at     timestamptz,
    failure_class    text,
    UNIQUE (organization_id, id),
    UNIQUE (job_id, lease_generation),
    CONSTRAINT job_leases_status_is_known CHECK (status IN (
        -- Te same pięć słów, co `lease_state` w 0001, bo to to samo pojęcie
        -- o poziom wyżej: utrata heartbeatu zaczyna reconciliation, a nie
        -- natychmiastowy drugi start (L2371-2379, D-004).
        'ACTIVE', 'RECONCILING', 'RELEASED', 'REVOKED', 'EXPIRED'
    )),
    -- Lease już wygasły w momencie zapisu to lease, który reaper odbiera
    -- natychmiast — czyli redispatch bez końca zamiast limitu prób (L5214).
    CHECK (expires_at > acquired_at),
    FOREIGN KEY (organization_id, job_id) REFERENCES jobs (organization_id, id),
    FOREIGN KEY (organization_id, attempt_id)
        REFERENCES job_attempts (organization_id, attempt_id)
);

-- „partial unique constraint dla jednego aktywnego lease'u” — wzorzec L2373,
-- tu na poziomie joba. To jest L5211 zapisane w schemacie.
CREATE UNIQUE INDEX job_leases_one_active_per_job
    ON job_leases (job_id)
    WHERE status = 'ACTIVE';

-- Pętla reapera (D-017: co 10 s) pyta wyłącznie o to.
CREATE INDEX job_leases_expiring
    ON job_leases (expires_at)
    WHERE status = 'ACTIVE';

-- =============================================================================
-- Ścieżka zgody: propozycja → rewizja → decyzja → intent → próba wykonania
-- =============================================================================

-- -----------------------------------------------------------------------------
-- action_proposals
-- -----------------------------------------------------------------------------
-- Tabela workspace'owa. Nie ma kolumny `state`: stan mieszka na REWIZJI, bo tam
-- go trzyma `contracts.ActionProposalRevision`, a maszyna L2773-2775 opisuje
-- właśnie rewizję („APPROVED → SUPERSEDED, jeżeli powstanie nowa rewizja”).
-- Druga kolumna stanu tutaj byłaby drugim źródłem tej samej prawdy.
--
-- D-013: w F1 NIE MA producenta propozycji. Ten schemat istnieje, bo bez niego
-- nie da się udowodnić DoD L5217-5218; wiersze wstawia wyłącznie ścieżka
-- deweloperska i nie wolno jej nazywać danymi produkcyjnymi.

CREATE TABLE action_proposals (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES organizations (id),
    workspace_id      uuid NOT NULL,
    -- Konto jest zasobem ORGANIZACJI (0001), więc klucz jest parą — D-020.
    social_account_id uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    -- Cel trójki tenantowej dzieci: rodzic musi być unikalny po (org, ws, id).
    UNIQUE (organization_id, workspace_id, id),
    FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    FOREIGN KEY (organization_id, social_account_id)
        REFERENCES social_accounts (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- action_proposal_revisions — szesnaście kolumn z L3686-3703
-- -----------------------------------------------------------------------------
-- Kolumny wersji planu, profilu inteligencji, pamięci, snapshotu kontekstu,
-- promptu i wywołania modelu są NULLABLE, i to jest stan faktyczny fazy, nie
-- luźny schemat: w F1 nie ma ani silnika planowania, ani Model Routera, ani
-- pamięci (F3/F4, L5501). NOT NULL wymagałby wstawiania wymyślonych UUID-ów,
-- czyli wyprodukowania dowodu na powiązanie, którego nie ma.
--
-- `task_resource_binding_ids` jest tablicą NOT NULL z domyślną tablicą pustą,
-- a nie NULL-em: „brak przypiętych zasobów” to znany stan, a NULL w tablicy
-- byłby stanem nieodróżnialnym od „nie wiem”.

CREATE TABLE action_proposal_revisions (
    id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id                 uuid NOT NULL,
    workspace_id                    uuid NOT NULL,
    proposal_id                     uuid NOT NULL,
    revision                        integer NOT NULL CHECK (revision >= 1),
    payload                         jsonb NOT NULL,
    -- SHA-256 w hexie, dokładnie jak `ApprovalHashInput.payload_hash`. CHECK na
    -- postaci, bo „approval wskazuje dokładny payload hash” (L5218) nie znaczy
    -- nic, jeżeli kolumna przyjmuje dowolny napis.
    payload_hash                    text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    -- Fingerprint i moment odczytu źródła — wejście kontroli zmiany źródła (L2828).
    source_fingerprint              text NOT NULL CHECK (length(source_fingerprint) > 0),
    intelligence_profile_version_id uuid,
    plan_version_id                 uuid,
    memory_snapshot_id              uuid,
    planning_context_snapshot_id    uuid,
    task_resource_binding_ids       uuid[] NOT NULL DEFAULT '{}',
    prompt_version_id               uuid,
    created_by_model_call_id        uuid,
    -- Draft ma TTL i wygasa z oknem wątku (L4301, L5539-5540).
    expires_at                      timestamptz NOT NULL,
    state                           text NOT NULL DEFAULT 'DRAFT',
    created_at                      timestamptz NOT NULL DEFAULT now(),
    source_observed_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, workspace_id, id),
    -- Kolejne rewizje tej samej propozycji numerują się jednoznacznie: bez tego
    -- „nowa rewizja unieważnia poprzedni approval” (L2795) nie miałoby porządku.
    UNIQUE (proposal_id, revision),
    CONSTRAINT action_proposal_revisions_state_is_known CHECK (state IN (
        'DRAFT', 'READY_FOR_APPROVAL', 'APPROVED', 'REJECTED', 'EXPIRED', 'SUPERSEDED'
    )),
    CONSTRAINT revision_expires_after_it_was_created CHECK (expires_at > created_at),
    -- TRÓJKA, nie para (L3874-3876): rewizja workspace'u A nie może zawisnąć pod
    -- propozycją workspace'u B tej samej organizacji.
    FOREIGN KEY (organization_id, workspace_id, proposal_id)
        REFERENCES action_proposals (organization_id, workspace_id, id)
);

CREATE INDEX action_proposal_revisions_by_proposal
    ON action_proposal_revisions (proposal_id, revision DESC);

-- L2795: „Każda edycja tworzy nową rewizję i unieważnia poprzedni approval.”
--
-- W kodzie tę regułę realizuje `ActionProposalRevision.supersede`, ale to chroni
-- jeden proces trzymający jeden obiekt. UPDATE puszczony ze skryptu, z konsoli
-- albo z trasy, której nikt nie przejrzał, przemyciłby treść pod zgodę, która
-- już wskazuje hash (L5218). Trigger zamyka tę drogę w bazie.
--
-- POROWNANIE PRZEZ to_jsonb MINUS 'state', A NIE LISTA KOLUMN — to jest istotne:
-- lista kolumn nie obejmuje kolumny dołożonej przyszłym ALTER TABLE, więc taka
-- kolumna byłaby edytowalna i nikt by tego nie zauważył. Tu zmiana czegokolwiek
-- poza `state` jest odrzucana automatycznie, także dla kolumn, których dziś nie ma.
--
-- `state` MUSI zostać mutowalny: bez tego maszyna L2773-2775, w tym przejście
-- APPROVED → SUPERSEDED, nie ma gdzie się wykonać.
--
-- Komunikat po polsku (D-010: komunikaty błędów czyta operator). Słowo
-- „niezmienna” jest dopasowywane w testach — nie zmieniać bez zmiany testu.
CREATE FUNCTION action_proposal_revisions_content_is_immutable() RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state') THEN
        RAISE EXCEPTION
            'action_proposal_revisions: treść rewizji jest niezmienna (L2795). '
            'Edycja tworzy NOWĄ rewizję i unieważnia poprzednią zgodę; '
            'UPDATE wolno wyłącznie na kolumnie state';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER action_proposal_revisions_no_content_edit
    BEFORE UPDATE ON action_proposal_revisions
    FOR EACH ROW EXECUTE FUNCTION action_proposal_revisions_content_is_immutable();

-- -----------------------------------------------------------------------------
-- approval_decisions
-- -----------------------------------------------------------------------------
-- Zgoda niesie HASH, nie tylko identyfikator rewizji: sam identyfikator nie
-- chroniłby przed podmianą treści pod tym samym ID (L5218). Że treść i tak nie
-- da się podmienić, pilnuje trigger powyżej — to są dwie niezależne blokady na
-- tych samych drzwiach, bo przez te drzwia przeszłoby unieważnienie zgody.

CREATE TABLE approval_decisions (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       uuid NOT NULL,
    workspace_id          uuid NOT NULL,
    proposal_revision_id  uuid NOT NULL,
    decision              text NOT NULL,
    approved_payload_hash text NOT NULL CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$'),
    -- Operator z 0002. Rekord wyłączony (`disabled_at`) zostaje w tabeli właśnie
    -- po to, żeby stara zgoda miała na co wskazywać.
    approved_by           uuid NOT NULL,
    decided_at            timestamptz NOT NULL DEFAULT now(),
    reason                text,
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT approval_decisions_decision_is_known CHECK (decision IN (
        -- Dwie wartości i żadnej trzeciej: L2774 rysuje APPROVED | REJECTED
        -- z READY_FOR_APPROVAL, a EXPIRED jest stanem rewizji, nie decyzją człowieka.
        'APPROVED', 'REJECTED'
    )),
    -- „odrzucenie wymaga powodu, który idzie do pamięci preferencji” (L5311).
    -- REJECTED z NULL-owym powodem to decyzja, z której nikt się nie nauczy.
    CONSTRAINT rejection_carries_a_reason CHECK (
        decision <> 'REJECTED' OR (reason IS NOT NULL AND length(reason) > 0)
    ),
    FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    FOREIGN KEY (organization_id, workspace_id, proposal_revision_id)
        REFERENCES action_proposal_revisions (organization_id, workspace_id, id),
    FOREIGN KEY (organization_id, approved_by)
        REFERENCES operators (organization_id, id)
);

-- Jedna decyzja na rewizję. Zmiana zdania wymaga NOWEJ rewizji, a nie drugiej
-- decyzji nad tą samą treścią — to jest L2795 i L5310 zapisane indeksem.
CREATE UNIQUE INDEX approval_decisions_one_per_revision
    ON approval_decisions (proposal_revision_id);

-- -----------------------------------------------------------------------------
-- execution_intents — dziewięć kolumn z L3707-3719
-- -----------------------------------------------------------------------------
-- Dokument mówi „wiąże DOKŁADNIE” i wymienia dziewięć pozycji; wszystkie są tu
-- pod tymi nazwami. Dołożone są: `id` (klucz), oraz pola, które persystuje
-- `contracts.ExecutionIntent` — `payload_hash`, `account_id`,
-- `assignment_version_id`, `issued_at`, `consumed_at`.
--
-- `assignment_version_id` jest NULLABLE, bo tabeli assignmentów w F1 nie ma
-- (F3/F4). Wpisywanie tam wymyślonego UUID-a byłoby udawaniem powiązania.

CREATE TABLE execution_intents (
    id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id              uuid NOT NULL,
    workspace_id                 uuid NOT NULL,
    proposal_revision_id         uuid NOT NULL,
    approval_decision_id         uuid NOT NULL,
    task_resource_binding_ids    uuid[] NOT NULL DEFAULT '{}',
    planning_context_snapshot_id uuid,
    one_time_grant_hash          text NOT NULL CHECK (one_time_grant_hash ~ '^[0-9a-f]{64}$'),
    expires_at                   timestamptz NOT NULL,
    status                       text NOT NULL DEFAULT 'ISSUED',
    payload_hash                 text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    account_id                   uuid NOT NULL,
    assignment_version_id        uuid,
    issued_at                    timestamptz NOT NULL DEFAULT now(),
    consumed_at                  timestamptz,
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, workspace_id, id),
    -- „one-time ExecutionIntent” (L2755, L5692): grant nie da się wystawić dwa razy.
    UNIQUE (organization_id, one_time_grant_hash),
    CONSTRAINT execution_intents_status_is_known CHECK (status IN (
        'ISSUED', 'CONSUMED', 'EXPIRED', 'REVOKED'
    )),
    -- Intent oznaczony jako zużyty bez momentu zużycia jest nieodróżnialny od
    -- takiego, którego zużycia nikt nie zapisał — a ta różnica to całe L2821.
    CONSTRAINT consumed_intent_records_the_moment CHECK (
        (status = 'CONSUMED') = (consumed_at IS NOT NULL)
    ),
    CONSTRAINT intent_expires_after_it_was_issued CHECK (expires_at > issued_at),
    FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    FOREIGN KEY (organization_id, workspace_id, proposal_revision_id)
        REFERENCES action_proposal_revisions (organization_id, workspace_id, id),
    FOREIGN KEY (organization_id, workspace_id, approval_decision_id)
        REFERENCES approval_decisions (organization_id, workspace_id, id),
    FOREIGN KEY (organization_id, account_id)
        REFERENCES social_accounts (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- execution_attempts
-- -----------------------------------------------------------------------------
-- To jest tabela, nad którą `contracts.AutomationRun` jest TYPEM (D-020).
-- Mapowanie nazw, bo różnią się o jedno słowo: `AutomationRun.execution_attempt_id`
-- to tutaj `id`, `AutomationRun.revision_id` to `proposal_revision_id`
-- (nazwa z L3712, dla spójności z `execution_intents`), `AutomationRun.state`
-- to `status`.

CREATE TABLE execution_attempts (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL,
    workspace_id         uuid NOT NULL,
    intent_id            uuid NOT NULL,
    proposal_revision_id uuid NOT NULL,
    -- „immutable schematy … z payload hash” (L5198). Hash na próbie odpowiada na
    -- pytanie „jaką treść ten przebieg opublikował” bez wiary, że wiersz
    -- propozycji nie został potem zmieniony.
    payload_hash         text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    attempt_no           integer NOT NULL CHECK (attempt_no >= 1),
    status               text NOT NULL,
    -- Wejście hashu approvala (L2792) — dowód nazywa dokładny kod, który działał.
    adapter_version      text NOT NULL CHECK (length(adapter_version) > 0),
    started_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT execution_attempts_status_is_known CHECK (status IN (
        -- Osiem stanów maszyny ExecutionAttempt (L2601-2605). Po SUBMIT_STARTED
        -- nie ma automatycznego retry (L2673, L4560, L5335).
        'PREPARED', 'DISPATCHED', 'SUBMIT_STARTED', 'CONFIRMED', 'UNKNOWN',
        'FAILED_BEFORE_SIDE_EFFECT', 'CAPABILITY_BLOCKED', 'IDENTITY_BLOCKED'
    )),
    CONSTRAINT attempt_finished_after_started CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    FOREIGN KEY (organization_id, workspace_id, intent_id)
        REFERENCES execution_intents (organization_id, workspace_id, id),
    FOREIGN KEY (organization_id, workspace_id, proposal_revision_id)
        REFERENCES action_proposal_revisions (organization_id, workspace_id, id)
);

-- L2821: „maksymalnie jedna automatyczna próba submitu na ExecutionIntent”.
-- INDEKSEM, nie ścieżką kodu. `ExecutionIntent.consume` w kontraktach rzuca przy
-- drugim wywołaniu, ale to chroni jeden proces trzymający jeden obiekt; druga
-- próba zapisana przez inny proces albo przez ten sam po restarcie jest
-- zatrzymywana tutaj albo w ogóle.
CREATE UNIQUE INDEX execution_attempts_one_per_intent
    ON execution_attempts (intent_id);

-- =============================================================================
-- Artefakty i klucze profili
-- =============================================================================

-- -----------------------------------------------------------------------------
-- artifacts — „artifacts ACL i retention” (L5196)
-- -----------------------------------------------------------------------------
-- `retention_until` jest NOT NULL i BEZ DEFAULT-u. DEFAULT znaczyłby, że
-- retencję każdego artefaktu ustalono raz, w migracji — a D-017 zamraża trzydzieści
-- dni dla przypadku ogólnego i dwadzieścia cztery godziny dla trace'u po
-- zalogowaniu (L3898). Obie te wartości nie mogą być jednym DEFAULT-em, więc nie
-- ma żadnego: kto zapisuje artefakt, decyduje, jak długo on żyje.
--
-- Progu 24 h dla trace'u NIE ma w CHECK-u świadomie: D-017 trzyma progi w jednym
-- module konfiguracji control plane, a próg zduplikowany w schemacie rozjeżdża
-- się z tamtym bez ostrzeżenia.
--
-- Czego tu NIE MA: samego szyfrowania i kasowania po retencji. Ten schemat
-- zapisuje, że artefakt JEST zaszyfrowany i DO KIEDY żyje; wykonanie należy do
-- Browser Runnera i do zamiatacza retencji.

CREATE TABLE artifacts (
    id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id                 uuid NOT NULL,
    workspace_id                    uuid NOT NULL,
    kind                            text NOT NULL,
    storage_ref                     text NOT NULL CHECK (length(storage_ref) > 0),
    -- Suma kontrolna z prefiksem algorytmu. Kolumna przyjmująca dowolny napis
    -- nie dowodziłaby niczego o bajtach.
    checksum                        text NOT NULL,
    size_bytes                      bigint NOT NULL CHECK (size_bytes >= 0),
    encrypted                       boolean NOT NULL DEFAULT true,
    acl                             text NOT NULL,
    -- Opcjonalny producent. Trójka z MATCH SIMPLE: gdy kolumna jest NULL, klucz
    -- jest spełniony bez sprawdzania; gdy jest wypełniona, sprawdzany jest cały
    -- tenant, bo pozostałe dwie kolumny są NOT NULL.
    produced_by_execution_attempt_id uuid,
    created_at                      timestamptz NOT NULL DEFAULT now(),
    retention_until                 timestamptz NOT NULL,
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT artifacts_kind_is_known CHECK (kind IN (
        'TRACE', 'SCREENSHOT', 'HAR', 'LOG', 'REPORT'
    )),
    CONSTRAINT artifacts_acl_is_known CHECK (acl IN (
        -- Trzy wartości, bo w E1 są dokładnie trzy czytające strony: operator,
        -- Browser Runner i obie. Storage profilu jest dostępny tylko runnerowi
        -- (L3894) i nie ma trzeciego principala, któremu można coś nadać.
        'OPERATOR_ONLY', 'RUNNER_ONLY', 'OPERATOR_AND_RUNNER'
    )),
    CONSTRAINT artifacts_checksum_is_sha256 CHECK (checksum ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT artifacts_retention_is_in_the_future CHECK (retention_until > created_at),
    -- „trace po zalogowaniu ma krótki TTL, ACL i szyfrowanie” (L3898). Trace
    -- niezaszyfrowany nie ma prawa istnieć jako wiersz.
    CONSTRAINT trace_artifact_is_encrypted CHECK (kind <> 'TRACE' OR encrypted),
    FOREIGN KEY (organization_id, workspace_id)
        REFERENCES workspaces (organization_id, id),
    FOREIGN KEY (organization_id, workspace_id, produced_by_execution_attempt_id)
        REFERENCES execution_attempts (organization_id, workspace_id, id) MATCH SIMPLE
);

-- Zamiatacz retencji pyta wyłącznie o to.
CREATE INDEX artifacts_by_retention ON artifacts (retention_until);

-- -----------------------------------------------------------------------------
-- profile_deks — L3914, D-018
-- -----------------------------------------------------------------------------
-- „profile są szyfrowane oddzielnymi DEK; aktywna kapsuła montuje wyłącznie swój
-- odszyfrowany profil” (L3914).
--
-- Tabela ORGANIZACYJNA, nie workspace'owa, i to wprost z D-020: profile należą
-- do organizacji i są przypisywane do workspace'ów, a nie w nich tworzone.
-- Dlatego klucz obcy jest parą i nie ma tu kolumny `workspace_id`.
--
-- CZEGO TU NIE MA: klucza opakowującego. D-018 kładzie go w pliku o prawach 0600
-- POZA wolumenem bazy, montowanym wyłącznie do Browser Runnera. Gdyby leżał
-- w tej tabeli, szyfrowanie nie chroniłoby przed kopią wolumenu bazy — czyli
-- przed jedyną rzeczą, przed którą ma chronić.

CREATE TABLE profile_deks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    profile_id      uuid NOT NULL,
    -- DEK w postaci OPAKOWANEJ. Klucz opakowujący nigdy nie wchodzi do tej bazy.
    wrapped_dek     bytea NOT NULL CHECK (length(wrapped_dek) > 0),
    -- Który klucz hosta opakował ten DEK. Bez tego rotacja klucza opakowującego
    -- nie wie, które wiersze przepakować.
    wrapping_key_id text NOT NULL CHECK (length(wrapping_key_id) > 0),
    algorithm       text NOT NULL,
    generation      bigint NOT NULL CHECK (generation >= 1),
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Crypto-erasure klucza (L3903): niszczy się DEK, nie bajty profilu.
    erased_at       timestamptz,
    UNIQUE (organization_id, id),
    UNIQUE (profile_id, generation),
    CONSTRAINT profile_deks_algorithm_is_known CHECK (algorithm IN (
        -- Jedna wartość świadomie: kolumna przyjmująca algorytm, którego nikt nie
        -- zaimplementował, jest miejscem na cichy downgrade.
        'AES-256-GCM'
    )),
    CONSTRAINT dek_erased_after_created CHECK (erased_at IS NULL OR erased_at >= created_at),
    FOREIGN KEY (organization_id, profile_id)
        REFERENCES browser_profiles (organization_id, id)
);

-- Jeden ŻYWY DEK na profil. Dwa żywe znaczą, że jednym z nich profil się nie
-- odszyfruje, a schemat nie powie którym. Rotacja DEK-u nie jest w zakresie F1;
-- gdy się pojawi, to jest indeks do przemyślenia od nowa.
CREATE UNIQUE INDEX profile_deks_one_live_per_profile
    ON profile_deks (profile_id)
    WHERE erased_at IS NULL;

COMMIT;
