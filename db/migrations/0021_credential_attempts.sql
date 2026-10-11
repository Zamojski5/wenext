-- =============================================================================
-- Migracja 0021 — licznik nieudanych prób poświadczenia
--
-- P2 etap B. Luka zgłaszana od F1 w `api/session.py` („Rate limiting the credential route is NOT
-- part of this package and is not solved elsewhere either — it is reported as a gap rather than
-- implied") przestała być akademicka w chwili, gdy P7 otworzyło rejestrację: cztery trasy
-- przyjmują dziś poświadczenia od kogokolwiek, a Argon2id kosztuje atakującego czas, nie próby.
--
-- DLACZEGO W BAZIE, A NIE W PAMIĘCI PROCESU. Licznik w pamięci zeruje się przy restarcie i przy
-- drugim procesie API. Jedno i drugie jest normalnym zdarzeniem we wdrożeniu, więc limit
-- trzymany w procesie jest limitem, który znika dokładnie wtedy, gdy ktoś go testuje.
--
-- DWA KLUCZE, NIE JEDEN. Liczymy osobno po ADRESIE E-MAIL i po ADRESIE ŹRÓDŁOWYM:
--   * sam e-mail pozwala jednemu adresowi IP próbować tysiąca kont po pięć razy;
--   * sam adres źródłowy pozwala sieci maszyn próbować jednego konta bez końca.
-- Blokuje ten, który pierwszy przekroczy próg.
--
-- CZEGO TA TABELA NIE ROBI: nie mówi, czy konto istnieje. Wiersz powstaje dla KAŻDEGO
-- nieudanego poświadczenia, także dla adresu, którego nikt nigdy nie zarejestrował, więc ani
-- odpowiedź, ani zawartość tabeli nie odróżnia „złe hasło" od „nie ma takiego konta".
-- =============================================================================

BEGIN;

CREATE TABLE credential_attempts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 'EMAIL' albo 'ADDRESS'. Zamknięta lista, bo trzeci rodzaj klucza musiałby mieć własną
    -- regułę progu, a nie cichy wpis do wspólnego licznika.
    kind            text NOT NULL,
    -- Adres e-mail małymi literami albo adres źródłowy żądania. Nie hash: to nie jest sekret,
    -- a przy incydencie trzeba móc odczytać, kto i skąd próbował.
    key             text NOT NULL CHECK (length(btrim(key, E' \t\r\n')) > 0),
    failures        integer NOT NULL DEFAULT 0 CHECK (failures >= 0),
    first_failed_at timestamptz NOT NULL DEFAULT now(),
    last_failed_at  timestamptz NOT NULL DEFAULT now(),
    -- Do kiedy odmawiamy bez sprawdzania hasła. NULL znaczy „nie blokujemy".
    blocked_until   timestamptz,
    CONSTRAINT credential_attempts_kind_is_known CHECK (kind IN ('EMAIL', 'ADDRESS')),
    CONSTRAINT credential_attempts_last_after_first CHECK (last_failed_at >= first_failed_at)
);

-- Jeden licznik na klucz. Indeks unikalny, a nie sprawdzenie w kodzie: dwie równoległe próby
-- tego samego hasła wchodzą w dwa INSERT-y i tylko baza widzi oba naraz, a licznik, który
-- rozjeżdża się przy współbieżności, jest limitem tylko z nazwy.
CREATE UNIQUE INDEX credential_attempts_one_per_key ON credential_attempts (kind, key);

-- Po tym indeksie chodzi sprzątanie starych liczników i podgląd „kto teraz jest zablokowany".
CREATE INDEX credential_attempts_blocked ON credential_attempts (blocked_until)
    WHERE blocked_until IS NOT NULL;

COMMIT;
