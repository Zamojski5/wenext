-- =============================================================================
-- 0039. OKAZJA NIESIE POST, NA KTÓRY ODPOWIADA SZKIC: AUTOR, AWATAR, LICZNIKI (v11, 13.10).
--
-- Założyciel po dniu prawdziwej pracy z kartą: szkic odpowiedzi zrobiony przez `draft.create`
-- bez zaznaczonej okazji pokazywał tylko "Odpowiedź na X": nie wiedział, czyj to post, co mówi
-- ani czy się rozchodzi. Okazja jest już jednym wierszem na rozmowę w projekcie (0036) i to ją
-- czyta `room.show`, więc migawka celu mieszka TU, a nie w nowej tabeli: `draft.create` dla
-- odpowiedzi tworzy albo odświeża okazję tej rozmowy i wpisuje do niej to, co odczytał.
--
-- CO JUŻ BYŁO, A CO DOCHODZI. Były: `author` (uchwyt), `excerpt` (treść posta), `created_at_source`,
-- `score` i `num_comments`. Dla Reddita to punkty i komentarze; dla X `score` to polubienia, a
-- `num_comments` odpowiedzi (jedno słownictwo liczników, `opportunities/ranking.py`). Dochodzą:
--   * `author_name`   nazwa wyświetlana autora (X: "Jack Dorsey"), obok uchwytu;
--   * `avatar_url`    adres obrazka profilu, https, na hoście platformy (karta ładuje go tylko z
--                     listy dozwolonych hostów i pokazuje inicjały, gdy się nie załaduje);
--   * `views`, `reposts`  liczniki X, których `score` i `num_comments` nie niosą;
--   * `metrics_at`    kiedy liczniki odczytano. Liczniki starzeją się w godzinach, więc odczyt bez
--                     daty byłby liczbą, o której nie wiadomo, ile jest warta;
--   * `gone_at`       kiedy odczyt powiedział, że posta już nie ma (usunięty, zablokowany);
--   * `author_verified`  znaczek weryfikacji autora, gdy odczyt go zna (NULL: nie wiadomo, nie
--                     "nie ma"). Karta rysuje post tak, jak X, więc znaczek jest jego częścią;
--   * `media`         pierwszy obraz albo miniatura wideo posta: obiekt `{typ, url, szer, wys}`,
--                     `typ` to photo, video albo gif, a `url` adres miniatury na hoście
--                     platformy (karta pobiera go przez narzędzie karty i wkleja jako obraz
--                     inline, bo jej polityka treści jest pusta). Cały post to jeden wiersz, a
--                     "pierwszy obraz" wystarcza do rozpoznania, o czym on jest.
--
-- LICZNIK, KTÓREGO NIKT NIE ODCZYTAŁ, TO NULL, NIGDY ZERO (ta sama zasada co w 0035 i 0036): karta
-- mówi "brak pomiaru". Kolumny są opcjonalne i bez DEFAULT, więc wiersze sprzed migracji są tym,
-- czym były, a nic nie czyta tych kolumn bez sprawdzenia, czy są.
-- =============================================================================

BEGIN;

ALTER TABLE project_opportunities
    ADD COLUMN author_name text
        CHECK (author_name IS NULL OR length(author_name) <= 200),
    ADD COLUMN avatar_url text
        CHECK (avatar_url IS NULL OR (avatar_url ~ '^https://' AND length(avatar_url) <= 1000)),
    ADD COLUMN views bigint CHECK (views IS NULL OR views >= 0),
    ADD COLUMN reposts bigint CHECK (reposts IS NULL OR reposts >= 0),
    ADD COLUMN metrics_at timestamptz,
    ADD COLUMN gone_at timestamptz,
    ADD COLUMN author_verified boolean,
    ADD COLUMN media jsonb
        CHECK (media IS NULL OR (
            jsonb_typeof(media) = 'object'
            AND media ->> 'typ' IN ('photo', 'video', 'gif')
            AND media ->> 'url' ~ '^https://'
            AND length(media ->> 'url') <= 1000
        ));

COMMIT;
