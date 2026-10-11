-- =============================================================================
-- Migracja 0026: ile rozmowy przysługuje jednej sesji, ustawiane w projekcie
--
-- UWAGA ZAŁOŻYCIELA Z 01:02, 20.09. Rozmowa skończyła się po TRZECH turach zdaniem „Budżet
-- sesji wyczerpany: tool_calls 5, turns 3", a on odczytał je jako limit swojego konta Codex:
-- „mam limity, czemu pokazuje, że nie mam". Dwie rzeczy naraz były nie tak. Pierwsza: domyślny
-- budżet dwunastu tur był ustawiony pod jednorazowe zlecenie z P2, a nie pod rozmowę, w której
-- człowiek dopisuje zdanie po zdaniu. Druga, gorsza: limit NASZEGO produktu przedstawiał się
-- liczbami bez zdania, więc wyglądał na limit cudzy. Pierwszą naprawia ta kolumna, drugą tekst
-- na ekranie.
--
-- DLACZEGO W PROJEKCIE, A NIE W SESJI. W sesji już jest (`agent_sessions.budget`) i tam ma
-- zostać: budżet, na którym sesja ruszyła, jest częścią jej historii i zmiana ustawień nie może
-- przepisać przeszłości. Kolumna w projekcie odpowiada na inne pytanie: „ile ma dostawać
-- KAŻDA następna rozmowa w tym projekcie". Jedno miejsce, w którym człowiek to ustawia, i
-- żadnego kopiowania liczby do kolejnych sesji ręcznie.
--
-- NULL ZNACZY „NIE USTAWIANO", a nie „zero" i nie „bez limitu". Wtedy obowiązuje wartość
-- domyślna z kodu (`sessions/handlers.py::DEFAULT_BUDGET`), a ekran pokazuje ją jawnie, razem
-- ze zdaniem, że nikt jej nie wybrał. Limit, którego nie widać, jest tym samym problemem, co
-- limit zmyślony.
--
-- JSONB, NIE TRZY KOLUMNY. Klucze są te same, co w `agent_sessions.budget` (`max_turns`,
-- `max_tool_calls`, `max_minutes`), więc jedna wartość przenosi się do sesji bez tłumaczenia
-- nazw. CHECK pilnuje, że to obiekt i że każda wartość jest dodatnią liczbą całkowitą: zero tur
-- znaczyłoby rozmowę, która nie może się zacząć, a to jest ustawienie, którego nikt nie chce
-- wybrać świadomie.
-- =============================================================================

BEGIN;

-- BEZ PODZAPYTAŃ, i to nie jest kwestia stylu: PostgreSQL odrzuca `SELECT` w CHECK
-- („cannot use subquery in check constraint"), bo więz musi dać się sprawdzić na samym wierszu.
-- Pierwsza wersja tej migracji używała `NOT EXISTS (SELECT ... jsonb_each ...)` i padła przy
-- zakładaniu bazy testowej. Zamiast tego: operator `-` z listą kluczy usuwa te, które znamy, i
-- jeśli coś zostanie, to znaczy, że przyszedł klucz spoza listy; wartości sprawdzamy po kolei,
-- bo klucze są trzy i zamknięte, więc wypisanie ich wprost jest krótsze niż jakakolwiek pętla.
ALTER TABLE projects
    ADD COLUMN conversation_budget jsonb,
    ADD CONSTRAINT projects_conversation_budget_is_an_object CHECK (
        conversation_budget IS NULL OR jsonb_typeof(conversation_budget) = 'object'
    ),
    ADD CONSTRAINT projects_conversation_budget_has_only_known_keys CHECK (
        conversation_budget IS NULL
        OR conversation_budget - ARRAY['max_turns', 'max_tool_calls', 'max_minutes']
           = '{}'::jsonb
    ),
    ADD CONSTRAINT projects_conversation_budget_holds_positive_whole_numbers CHECK (
        conversation_budget IS NULL
        OR (
            (conversation_budget ? 'max_turns') IS NOT TRUE
            OR (jsonb_typeof(conversation_budget -> 'max_turns') = 'number'
                AND (conversation_budget ->> 'max_turns')::numeric >= 1
                AND (conversation_budget ->> 'max_turns')::numeric
                    = trunc((conversation_budget ->> 'max_turns')::numeric))
        )
    ),
    ADD CONSTRAINT projects_conversation_budget_tool_calls_is_a_whole_number CHECK (
        conversation_budget IS NULL
        OR (conversation_budget ? 'max_tool_calls') IS NOT TRUE
        OR (jsonb_typeof(conversation_budget -> 'max_tool_calls') = 'number'
            AND (conversation_budget ->> 'max_tool_calls')::numeric >= 1
            AND (conversation_budget ->> 'max_tool_calls')::numeric
                = trunc((conversation_budget ->> 'max_tool_calls')::numeric))
    ),
    ADD CONSTRAINT projects_conversation_budget_minutes_is_a_whole_number CHECK (
        conversation_budget IS NULL
        OR (conversation_budget ? 'max_minutes') IS NOT TRUE
        OR (jsonb_typeof(conversation_budget -> 'max_minutes') = 'number'
            AND (conversation_budget ->> 'max_minutes')::numeric >= 1
            AND (conversation_budget ->> 'max_minutes')::numeric
                = trunc((conversation_budget ->> 'max_minutes')::numeric))
    );

COMMIT;
