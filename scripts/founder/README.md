# Runbook założyciela — co musi zrobić człowiek

Wszystko w fazie E1.F0, co da się zrobić bez człowieka, jest już zrobione i uruchamia się
jednym poleceniem. Ten katalog opisuje **wyłącznie to, czego automat nie może zrobić za Was**
— i wyjaśnia, dlaczego nie może.

## Dlaczego to nie jest automatyzowalne

Trzy rzeczy wymagają Was osobiście:

1. **Konto ChatGPT.** Bramka GATE-3 pyta, czy Codex logowany kontem ChatGPT wytrzymuje
   wielogodzinną pracę bez nadzoru. Nie da się tego zasymulować. Dokument jest tu
   kategoryczny: jeżeli test nie potwierdzi wspieranego kontraktu uwierzytelnienia,
   wynikiem jest `NO_GO` dla pracy bez nadzoru — **nie** obchodzenie loginu (L2129).

2. **Logowanie do kont społecznościowych.** „Konta zakłada i loguje człowiek. System nigdy
   nie tworzy konta ani tożsamości" (L4295). Hasło nie trafia do modelu (L5243).
   To nie jest ograniczenie techniczne, tylko granica zakresu z sekcji 4.

3. **Wybór community i archetypów.** `STAN-PROJEKTU.md` wymienia to jako otwarte pytanie
   numer 1: dokument wymaga dwóch różnych archetypów, ale nie wskazuje ich za Was.

## Co jest gotowe i nie wymaga Was

```bash
make doctor        # sprawdza, czy host się nadaje
make up            # podnosi bazę, kapsułę, gateway i fixture site
make test          # suity akceptacyjne
make evals         # fixture evals na stubie, bez sieci i bez konta
make gonogo        # raport z trzema werdyktami
```

Bramki 1 i 2 są już rozstrzygnięte i mają artefakty w `artifacts/`.

## GATE-3 — co uruchamiacie sami

```bash
make codex-gonogo
```

Skrypt przeprowadzi Was przez:

1. **Logowanie do Codeksa kontem ChatGPT.** Device code — dostaniecie URL i kod do wpisania
   w przeglądarce. Uwaga: metoda device code jest w becie i może wymagać włączenia przez
   administratora workspace'u ChatGPT. To, czy jest dostępna, rozstrzyga **odpowiedź
   `account/login/start`**, a nie żaden preflight (L2187). Jeżeli metoda jest niedostępna,
   fallbackiem jest lokalne `codex login --device-auth`.

2. **Kontrakt SDK.** Sprawdzenie, czy przypięta wersja realnie wystawia model, effort
   i output schema, oraz czy `model/list` paginuje się do końca (L2123, L2189).

3. **Wielogodzinny test bez nadzoru.** To jest właściwy pomiar bramki: restart runtime'u,
   odświeżenie auth, wyczerpanie quoty, wznowienie i praca **bez otwartego dashboardu**
   (L2129). Test ma trwać tyle, ile realnie ma trwać nocna praca systemu — nie skracajcie go.

### Co uznajemy za wynik

| Sytuacja | Werdykt |
| --- | --- |
| Kontrakt potwierdzony, praca bez nadzoru działa | `GO` |
| Kontrakt potwierdzony, praca bez nadzoru **nie** działa | `NO_GO` dla pracy bez nadzoru — i to jest legalny, użyteczny wynik |
| Nie udało się zalogować | `NIE_URUCHOMIONO`, nie `NO_GO` |

**`NO_GO` na GATE-3 nie zatrzymuje projektu.** Oznacza, że runtime trzeba wymienić albo
pracować pod nadzorem. Dokument wprost przewiduje wymienialny runtime bez przebudowy domeny
(L2127). Zatrzymuje projekt dopiero `BLOCK` na bramce E1, a to jest sześć faz dalej.

## Czego skrypt NIE zrobi

- Nie zaloguje się za Was do żadnej platformy społecznościowej.
- Nie obejdzie MFA ani challenge'u. Jeżeli platforma pokaże challenge, system się **zatrzyma**
  i utworzy zadanie dla człowieka — to jest zachowanie zamierzone (L253).
- Nie wybierze community ani archetypów.

## Cztery decyzje, które musicie podjąć przed E1.F2

Z `STAN-PROJEKTU.md`, sekcja „Otwarte pytania":

1. Które konkretnie community i archetypy wchodzą do E1.
2. Czy konto z historią wchodzi do okna walidacyjnego jako baseline, czy walidujemy
   wyłącznie konta od zera.
3. Ile realnie minut dziennie operator poświęci na kolejkę approvali.
4. Kto jest właścicielem kolejki approvali w danym tygodniu, jeżeli operatorów jest dwóch.

Punkt 3 nie jest formalnością: przy dziennym capie 12 kart na operatora (L4593) i wymaganych
≥40 potwierdzonych wkładach na archetyp (L4608) to on wyznacza minimalną długość okna
pomiarowego. Wyliczenie jest w `docs/f0-limits/`.
