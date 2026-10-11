# Archiwum promptów

Poprzednie wersje promptów modułu observe. `prompts.load_all()` czyta tylko `prompts/*.md`
(bez podkatalogów), więc te pliki nie wchodzą do manifestu, ale ich treść odpowiada
`prompt_sha256` w istniejących wierszach `community_pictures` i `thread_judgments`, zapisanych,
zanim wersja się zmieniła. Plik archiwum nie jest edytowany: jego hash jest dowodem.

- `community_picture.v1.md` (7570f8d1…a928) i `judge_thread.v1.md` (8c7db725…ad60): do
  2026-09-04; v2 dodaje wyłącznie regułę wyjścia „bez długich pauz” (U+2014/U+2013), semantyka bez
  zmian.
- `reaction_baseline.v1.md` (a616d5ca…): przebieg live `20260906T183929Z-codex` (21 epizodów).
  v2 dodaje pole `account_recent_drafts` (pamięć ostatnich szkiców konta, pilot 24 h), reszta bez
  zmian.
- `reaction_baseline.v3.md` (4b42a687…3bdb6): 13.09 15:0x do 13.09 wieczór (przykłady społeczności,
  jawny `move`). v4 (D-030) zmienia wyłącznie opis `WAIT`: świeży post bez komentarzy nie jest
  powodem do czekania, oraz dopowiada, względem czego liczy się `contribution`, gdy wątek to sam
  post. Reguły pisania i schemat wyjścia bez zmian.
- `reaction_baseline.v4.md` (e3a9b126…5929): 13.09 17:27 do 15.09 wieczór (D-030: `WAIT` nie dla świeżego posta).
  v5 (D-031) dodaje ruchy `IRONY` i `ONE_LINER`, pola `situation.requested_move` (eksperyment),
  `community.voice_card` i `community.move_outcomes` (wiedza z danych) oraz zasadę dla cudzego
  poradnika. Schemat wyjścia rozszerzony o dwa ruchy, reszta bez zmian.
- `draft_judge.v1.md` (6f279bc7…3a52): 13.09 do 15.09 wieczór. v2 (D-031) doprecyzowuje pytanie 5
  (nazwy technologii, o których jest wątek, nie są nazwą produktu do promocji; 8 z 15 odrzuceń
  w dwa dni było za Kubernetes, crictl, CSI, CRM) i pytanie 4 (ironia wobec sytuacji w rejestrze,
  długość mierzona próbkami głosu). Pięć pytań i werdykt bez zmian.
- `reaction_baseline.v5.md` (521605e9…3bf7) i `draft_judge.v2.md` (5fc3a1ab…0974): 15.09 23:41 do
  16.09 wieczór. v6 i v3 (D-032) dodają `situation.post_kind` (czym jest post, liczone bez modelu)
  oraz `situation.move_alternatives` (kolejność zastępników, gdy proszony ruch nie pasuje);
  sędzia czyta rodzaj reakcji względem rodzaju posta. Reguły pisania i schemat wyjścia bez zmian.
- `reaction_baseline.v6.md` (e284a4c2…e85d1) i `draft_judge.v3.md` (c2d4a5c0…a94cf): 16.09 do 17.09.
  v7 i v4 (D-036) zdejmują rusztowanie: znika `situation.post_kind` i `situation.move_alternatives`,
  znika przydzielany `requested_move`, znikają nakazy stylu (zakaz list, zakaz pytania na wejściu,
  reguły długości, definicje ośmiu rodzajów reakcji i siedmiu ruchów). Zostaje kontekst (profil,
  przykłady społeczności, karta głosu, wyniki własnych ruchów, rozmowa), kryterium ukończenia
  (komentarz albo uczciwe milczenie) i cztery reguły chroniące konto. Sędzia czyta rodzaj reakcji
  wobec tego, czym post jest, zamiast wobec pola liczonego wyrażeniem regularnym.
