-- =============================================================================
-- Migracja 0011 — po co czytamy tę społeczność (D-032, incydent 16.09 23:0x)
--
-- Włączenie jedenastu społeczności „do nauki” zleciło w kilka minut 676 osądów wątków (każdy to
-- wywołanie modelu) i 2196 odczytów metryk, bo przegląd osądów jest workspace'owy: każdy poll
-- dowolnej społeczności bierze WSZYSTKIE nieosądzone wątki z ostatnich 48 h. Cykle pilota zaczęły
-- czekać w kolejce za tym. Anulowane ręcznie, ale sam poll odtworzyłby to przy następnym biegu.
--
-- Rozróżnienie, którego brakowało: społeczność czytana po to, żeby wiedzieć, JAK się tam pisze
-- (listing + dzienny obraz i karta głosu, kilka wywołań na dobę), a społeczność, w której
-- pracujemy i każdy wątek zasługuje na osąd ENGAGE/IGNORE/WAIT (wywołanie na wątek).
--
-- `true` zachowuje dotychczasowe zachowanie dla r/kubernetes, r/b2b_sales i HN ask.
-- =============================================================================

BEGIN;

ALTER TABLE communities
    ADD COLUMN thread_analysis_enabled boolean NOT NULL DEFAULT true;

COMMIT;
