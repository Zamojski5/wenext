-- =============================================================================
-- Migracja 0015: sesja agenta i jej zdarzenia (P2, ROADMAP-PIVOT)
--
-- P2: „piszę »sprawdź, dlaczego odzew jest mały, i zaproponuj zmianę«. Agent uruchamia sesję
-- Codex, odkrywa dostępne możliwości, doczytuje brakujący kontekst, wykonuje potrzebne operacje
-- i daje wynik z odnośnikami. W trakcie mogę dopowiedzieć kontekst, przerwać i wznowić pracę.”
--
-- JEDNOSTKĄ PRACY JEST SESJA Z CELEM, NIE POJEDYNCZE WYWOŁANIE MODELU. Dotychczasowa ścieżka
-- (observe/turns.py) zakłada nowy wątek `ephemeral=True` przy każdej turze i wejście złożone
-- w całości przez aplikację. To działa dla jednego osądu wątku i nie daje się użyć do pracy,
-- która ma trwać, wracać i dać się skorygować. Stąd dwie tabele:
--
--   1. `agent_sessions`: cel, stan, budżet i JEDEN identyfikator wątku silnika
--      (`engine_thread_id`). Przy pierwszej turze zakładamy wątek, przy każdej następnej
--      wznawiamy TEN SAM. Dzięki temu model ma swoją historię, a nie kolejny prompt sklejony
--      przez nas.
--   2. `session_events`: append-only ślad rozmowy i narzędzi, numerowany `seq`. To z niego
--      odtwarza się ekran po restarcie i po powrocie użytkownika.
--
-- DLACZEGO `engine_thread_id` JEST TEKSTEM I MOŻE BYĆ NULL. To identyfikator NADANY PRZEZ SILNIK,
-- nie przez nas. NULL znaczy „sesja istnieje, wątku silnika jeszcze nie ma”, czyli stan między
-- utworzeniem sesji a pierwszą turą, i on naprawdę występuje: sesję tworzy żądanie HTTP, a wątek
-- zakłada dopiero job. Przy zmianie połączenia Codex wątek może być trzeba założyć od nowa;
-- historia domenowa zostaje tutaj, bo mieszka w `session_events`, a nie w silniku.
--
-- DLACZEGO `session_events` JEST APPEND-ONLY. Ten sam powód, co przy `thread_metric_snapshots`
-- i `thread_judgments` w 0004: zapis tego, co się wydarzyło, poprawiony po fakcie nie jest już
-- zapisem tego, co się wydarzyło. Odpowiedź modelu, wywołanie narzędzia i przerwanie człowieka
-- są zdarzeniami; korekta to NOWE zdarzenie, nie edycja starego.
--
-- CZEGO TA MIGRACJA NIE ROBI: nie zapisuje treści narzędzi ani materiałów, na które agent się
-- powołuje. `result_refs` trzyma ODNOŚNIKI do obiektów, które już mają swoje tabele (wątki,
-- publikacje, propozycje), a nie ich kopie. Kopia rozjechałaby się z oryginałem i to ona
-- trafiłaby do raportu.
--
-- NIE MA TU TEŻ PAMIĘCI PROJEKTU. Pierwsza wersja tej migracji zakładała `project_memory`
-- z trzema rodzajami ustaleń. Równolegle powstała `knowledge_items` (0016), która te same trzy
-- rodzaje trzyma WIĘZAMI: fakt bez klucza do źródła nie wejdzie, preferencję może wypowiedzieć
-- tylko człowiek, a doświadczenie musi mieć sytuację, decyzję i wynik. Dwie tabele pamięci to
-- dwa miejsca, w których agent szuka tego samego, więc ta odpadła. Narzędzia `memory.*` sesji
-- wołają `control_plane/knowledge/store.py`.
--
-- Wzorce odziedziczone: trójka tenantowa w FK (0004, 0013), zamknięte listy jako text + nazwany
-- CHECK, trigger append-only jak w 0004.
-- =============================================================================

BEGIN;

CREATE TABLE agent_sessions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL REFERENCES organizations (id),
    workspace_id         uuid NOT NULL,
    project_id           uuid NOT NULL,
    -- Połączenie Codex, na którym biegnie ta sesja. NULL znaczy „jeszcze nie wybrano”, a nie
    -- „dowolne”: bez połączenia sesja nie ruszy i job powie to wprost.
    codex_connection_id  uuid,
    -- Cel własnymi słowami użytkownika. Pusty jest odmową, nie sesją: sesja bez celu nie ma
    -- czego wykonać, a jej stan nie dałby się ocenić.
    --
    -- `btrim(goal)` BEZ DRUGIEGO ARGUMENTU OBCINA WYŁĄCZNIE SPACJE. Zmierzone, nie założone:
    -- cel złożony z tabulatora i nowej linii przechodził taki CHECK i lądował w bazie jako
    -- sesja, którą ekran renderuje pustym wierszem. Zbiór znaków jest więc podany wprost.
    goal                 text NOT NULL CHECK (btrim(goal, E' \t\n\r\f\v') <> ''),
    status               text NOT NULL DEFAULT 'QUEUED',
    engine_thread_id     text,
    -- Granice jednego powierzenia: max_turns, max_minutes, max_tool_calls. Budżet jest
    -- WARUNKIEM startu, nie raportem po fakcie, więc leży przy sesji, a nie w zdarzeniach.
    budget               jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_summary       text,
    -- Odnośniki do obiektów produktu, na które powołuje się wynik. Lista, nie kopia treści.
    result_refs          jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT agent_sessions_status_is_known CHECK (status IN (
        'QUEUED', 'RUNNING', 'WAITING_USER', 'WAITING_EVENT', 'PAUSED',
        'DONE', 'FAILED', 'BUDGET_EXHAUSTED'
    )),
    -- Stan terminalny ma moment zakończenia, a nieterminalny go nie ma. Bez tego „zakończone”
    -- byłoby słowem w kolumnie, a nie faktem z datą, i nie dałoby się policzyć, ile trwała praca.
    CONSTRAINT agent_sessions_finished_when_terminal CHECK (
        (status IN ('DONE', 'FAILED', 'BUDGET_EXHAUSTED')) = (finished_at IS NOT NULL)
    ),
    CONSTRAINT agent_sessions_belong_to_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id),
    -- Połączenie jest zasobem ORGANIZACJI (0014), więc klucz jest parą, nie trójką.
    CONSTRAINT agent_sessions_point_at_connection
        FOREIGN KEY (organization_id, codex_connection_id)
        REFERENCES codex_connections (organization_id, id)
);

CREATE INDEX agent_sessions_by_project
    ON agent_sessions (organization_id, workspace_id, project_id, created_at DESC);

-- Sesji CZYNNYCH szuka dispatcher i ekran projektu, więc indeks prowadzi po samym stanie.
CREATE INDEX agent_sessions_live
    ON agent_sessions (organization_id, status, updated_at DESC)
    WHERE finished_at IS NULL;

CREATE TABLE session_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    workspace_id     uuid NOT NULL,
    session_id       uuid NOT NULL,
    -- Numer w obrębie sesji, od 1. To po nim klient wznawia odczyt („daj mi wszystko po 42”),
    -- więc musi być gęsty i rosnący, a nie tylko unikalny.
    seq              integer NOT NULL CHECK (seq >= 1),
    kind             text NOT NULL,
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    UNIQUE (session_id, seq),
    CONSTRAINT session_events_kind_is_known CHECK (kind IN (
        'USER_MESSAGE', 'ASSISTANT_MESSAGE', 'TOOL_CALL', 'TOOL_RESULT',
        'STATUS', 'ERROR', 'INTERRUPT'
    )),
    CONSTRAINT session_events_belong_to_session
        FOREIGN KEY (organization_id, workspace_id, session_id)
        REFERENCES agent_sessions (organization_id, workspace_id, id)
);

CREATE INDEX session_events_by_session
    ON session_events (session_id, seq);

CREATE FUNCTION session_events_are_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'session_events: ślad sesji jest append-only (ROADMAP-PIVOT P2). '
        'Poprawka to NOWE zdarzenie, nie edycja starego: inaczej ekran po restarcie '
        'pokazywałby przebieg, którego nie było';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER session_events_no_update_or_delete
    BEFORE UPDATE OR DELETE ON session_events
    FOR EACH ROW EXECUTE FUNCTION session_events_are_append_only();

COMMIT;
