-- =============================================================================
-- Migracja 0024 — znacznik ostatniej wizyty w projekcie
--
-- P4 i P5, przepływ „Powrót po nieobecności". Człowiek zamyka laptop i wraca po dwóch dniach.
-- Pytanie, na które ma odpowiedzieć ekran, brzmi „co się wydarzyło, ODKĄD MNIE NIE BYŁO", a nie
-- „co się wydarzyło przez ostatnie 24 godziny". Te dwa okna są różne i tylko pierwsze odpowiada
-- na pytanie, które człowiek naprawdę ma.
--
-- PER UŻYTKOWNIK, NIE PER PROJEKT. Dwie osoby w jednej organizacji wracają w innych momentach;
-- wspólny znacznik znaczyłby, że wejście wspólnika kasuje moje okno i tracę to, czego nie
-- widziałem. `user_id` jest NULLowalne dla jednego przypadku: sesja otwarta wspólnym hasłem
-- operatora sprzed P7. Indeks ma NULLS NOT DISTINCT, więc te sesje dzielą JEDEN wiersz, co jest
-- prawdą o nich: to jest jeden wspólny operator, a nie wielu nierozróżnialnych ludzi.
--
-- DWA ZNACZNIKI, NIE JEDEN. `last_seen_at` to ta wizyta, `previous_seen_at` to początek okna,
-- które pokazujemy. Gdyby był jeden, odświeżenie strony kasowałoby okno w sekundę po wejściu
-- i człowiek zobaczyłby „nic się nie wydarzyło" zamiast tego, po co przyszedł. Przesunięcie
-- okna dzieje się dopiero po przerwie dłuższej niż karencja z `returns/store.py`.
--
-- CZEGO TU NIE MA: treści. Ani jednej kolumny z tym, co człowiek zobaczył. Widok powrotu liczy
-- się z danych, które i tak są w bazie; zapisywanie migawki byłoby drugą kopią prawdy, która
-- rozjeżdża się z pierwszą.
-- =============================================================================

BEGIN;

CREATE TABLE project_visits (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES organizations (id),
    workspace_id       uuid NOT NULL,
    project_id         uuid NOT NULL,
    user_id            uuid REFERENCES users (id),
    -- Początek okna „od Twojej ostatniej wizyty". NULL znaczy „pierwsza wizyta w tym projekcie",
    -- a wtedy widok pokazuje całą historię projektu i mówi to wprost.
    previous_seen_at   timestamptz,
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    -- Ile razy ten człowiek tu wracał. Liczba dla raportu walidacji, nie dla ekranu.
    visits             integer NOT NULL DEFAULT 1 CHECK (visits >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, workspace_id, id),
    CONSTRAINT project_visits_window_is_ordered CHECK (
        previous_seen_at IS NULL OR previous_seen_at <= last_seen_at
    ),
    CONSTRAINT project_visits_point_at_project
        FOREIGN KEY (organization_id, workspace_id, project_id)
        REFERENCES projects (organization_id, workspace_id, id)
);

-- NULLS NOT DISTINCT, bo bez tego każda wizyta sesji bez użytkownika zakładałaby NOWY wiersz
-- (NULL <> NULL), a znacznik przestałby cokolwiek znaczyć dokładnie dla tego jednego przypadku,
-- dla którego jest NULLowalny. Ta sama pułapka, co przy `invitations` w 0018.
CREATE UNIQUE INDEX project_visits_one_per_person_and_project
    ON project_visits (organization_id, project_id, user_id) NULLS NOT DISTINCT;

COMMIT;
