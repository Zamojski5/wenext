-- =============================================================================
-- 0033. POWIERZENIE MA TERMIN, A TERMIN EGZEKWUJE PRODUKT.
--
-- CO TO ZAMYKA, zmierzone. Nadzór z 22/23.09 miał zatrzymać powierzenie jednego z projektów
-- o 09:10 UTC; zatrzymał je człowiek, godzinę po terminie, bo zewnętrzne wybudzenie przyszło
-- późno (NADZOR-12H-2026-09-22, PROBA-RDZENIA-2026-09-23 punkt 4: „Sam produkt musi zatrzymać
-- powierzenie o ustalonym czasie”). W tabeli nie było kolumny, której dotrzymanie dałoby się
-- sprawdzić: termin istniał tylko w dokumencie i w pamięci ludzi.
--
-- ZNACZENIE. `expires_at` jest częścią tego, co podpisano: wchodzi do `scope_sha256` razem
-- z trybem, zakresem i budżetem (`presence/scope.py::digest`), bo ten sam zakres na tydzień
-- i na rok to dwie różne zgody. Po terminie delegacja nie jest żywa (`DelegationRow.live`):
-- tik nie budzi modelu, decyzja nie wydaje publikacji, publikator zatrzymuje kolejkę.
-- Nic nie jest kasowane ani odwoływane; wiersz zostaje historią.
--
-- NULL zostaje dopuszczalny WYŁĄCZNIE dla podpisów sprzed tej migracji: ich digest liczono bez
-- terminu, a wpisanie terminu wstecz zmieniłoby podpisaną treść bez podpisującego. Nowe podpisy
-- przez API zawsze niosą termin (`api/delegations.py`).
-- =============================================================================

BEGIN;

ALTER TABLE project_delegations
    ADD COLUMN expires_at timestamptz,
    -- Termin wcześniejszy niż podpis byłby zgodą, która wygasła, zanim powstała.
    ADD CONSTRAINT project_delegations_expires_after_signing CHECK (
        expires_at IS NULL OR expires_at > signed_at
    );

COMMIT;
