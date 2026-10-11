-- 0029: nazwy społeczności bez długiej pauzy (KONWENCJE, zgłoszenie z e2e 20.09).
--
-- CZEGO DOTYCZY. `communities.display_name` jest TEKSTEM PRODUKTU: strona projektu renderuje go
-- wprost (`components/studio/results.tsx`), więc obowiązuje go zakaz długich pauz. Adaptery
-- składały tę nazwę jako „Reddit — r/devops" i „Hacker News — Ask HN"; poprawka w kodzie
-- (`community_observer/reddit.py`, `reddit_browser.py`, `hackernews.py`) naprawia NOWE wiersze.
-- Stare zostają z pauzą, łącznie z tymi, które ogląda założyciel na pilocie, więc ta migracja
-- przepisuje dokładnie ten jeden wzorzec.
--
-- DLACZEGO PRZEPISUJEMY DANE, a nie liczymy nazwy przy odczycie. Liczenie przy odczycie byłoby
-- lepsze i jest osobną decyzją o modelu (nazwa dałaby się złożyć z platformy i klucza); ta
-- migracja nie przesądza jej i nie utrudnia. Robi jedną rzecz: usuwa znak, którego w produkcie
-- nie wolno pokazywać, z wierszy, które już istnieją.
--
-- ZAKRES JEST WĄSKI CELOWO. Warunek `LIKE` dopasowuje tylko nazwy wygenerowane przez adaptery,
-- więc nazwa wpisana kiedykolwiek ręcznie nie zostanie tknięta. Zamiana jest zawężona do
-- przedrostka: pauza wewnątrz nazwy społeczności (gdyby taka istniała) zostaje nietknięta.

UPDATE communities
   SET display_name = 'Reddit · ' || substring(display_name from 10)
 WHERE display_name LIKE 'Reddit — %';

UPDATE communities
   SET display_name = 'Hacker News · ' || substring(display_name from 15)
 WHERE display_name LIKE 'Hacker News — %';
