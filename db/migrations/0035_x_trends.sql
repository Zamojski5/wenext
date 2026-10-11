-- =============================================================================
-- 0035. CO SIĘ NIESIE NA X: ZAPYTANIA AGENTA I ZEBRANE POSTY.
--
-- Założyciel 27.09: agent ma się uczyć, jak piszą konta z dużym zaangażowaniem, i robić to
-- całą dobę. Model uruchamiany bez przerwy zużyłby budżet powierzenia w kilka godzin, więc
-- praca dzieli się na dwie części. Tanie zbieranie bez modelu co godzinę czyta feed konta
-- projektu i zapytania, które wybrał agent, a zapisuje posty z licznikami. Model w swoich
-- turach czyta z tego historię doby zamiast pojedynczego obrazu z chwili.
--
-- DWIE TABELE, BO DWA RÓŻNE FAKTY. `x_watch_queries` to decyzja agenta (co obserwować),
-- z sesją, która ją podjęła, i z chwilą jej cofnięcia; zapytanie cofnięte zostaje w historii.
-- `x_trend_posts` to pomiar: jeden wiersz na post w jednym odczycie. Ten sam post w kolejnych
-- godzinach to kolejne wiersze, bo prędkość (przyrost polubień i wyświetleń) jest tym, czego
-- szukamy. Licznik, którego X nie pokazał, zostaje NULL, nigdy 0 (ta sama zasada co w odczytach
-- publikacji).
--
-- CZEGO TU NIE MA: odpowiedzi ani czegokolwiek, co wychodzi na X. To są wyłącznie odczyty.
-- Zasady automatyzacji X zabraniają automatycznych odpowiedzi pod postami z wyszukiwania; ta
-- tabela niczego nie wywołuje, a zebrane posty nie budzą agenta.
-- =============================================================================

BEGIN;

CREATE TABLE x_watch_queries (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    project_id         uuid NOT NULL,
    query              text NOT NULL CHECK (length(btrim(query)) BETWEEN 1 AND 200),
    mode               text NOT NULL DEFAULT 'top' CHECK (mode IN ('top', 'live')),
    -- Sesja agenta, która zdecydowała. NULL dla wpisu spoza sesji (operator, test).
    created_by_session uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    removed_at         timestamptz,
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT x_watch_queries_removed_after_created CHECK (
        removed_at IS NULL OR removed_at >= created_at
    ),
    CONSTRAINT x_watch_queries_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Jedno aktywne zapytanie o danej treści na projekt: dopisanie tego samego drugi raz jest
-- tym samym zapytaniem, nie drugim źródłem głosów.
CREATE UNIQUE INDEX x_watch_queries_one_active
    ON x_watch_queries (organization_id, project_id, lower(btrim(query)))
    WHERE removed_at IS NULL;

CREATE TABLE x_trend_posts (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    project_id         uuid NOT NULL,
    collected_at       timestamptz NOT NULL,
    -- `home` to feed konta projektu, `search` to jedno z zapytań z `x_watch_queries`.
    source             text NOT NULL CHECK (source IN ('home', 'search')),
    query              text CHECK (query IS NULL OR length(query) <= 200),
    post_id            text NOT NULL CHECK (post_id ~ '^[0-9]{1,25}$'),
    author             text,
    body               text CHECK (body IS NULL OR length(body) <= 4000),
    posted_at          timestamptz,
    permalink          text,
    likes              integer CHECK (likes IS NULL OR likes >= 0),
    replies            integer CHECK (replies IS NULL OR replies >= 0),
    reposts            integer CHECK (reposts IS NULL OR reposts >= 0),
    views              bigint CHECK (views IS NULL OR views >= 0),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT x_trend_posts_search_has_its_query CHECK (
        (source = 'search') = (query IS NOT NULL)
    ),
    CONSTRAINT x_trend_posts_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- Jeden wiersz na post, źródło i odczyt: ten sam post w feedzie i w wyszukiwaniu to dwa
-- spojrzenia, a dwa zapisy jednego odczytu to błąd.
CREATE UNIQUE INDEX x_trend_posts_one_per_read
    ON x_trend_posts (organization_id, project_id, post_id, source, collected_at);

CREATE INDEX x_trend_posts_by_project_and_time
    ON x_trend_posts (organization_id, project_id, collected_at DESC);

COMMIT;
