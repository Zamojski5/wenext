# Wdrożenie usługi na serwer

Ten dokument opisuje, jak postawić Wenext na maszynie, która nie jest laptopem założyciela, i co
robić dalej. Powstał po wieczorze 19 września 2026, w którym stack padł dwa razy, bo host był
zajęty czym innym. Dopóki usługa stoi na laptopie, jej dostępność jest dostępnością laptopa.

Wszystko poniżej dotyczy nakładki `compose.prod.yml`, która NIE zastępuje `docker-compose.yml`,
tylko go zmienia w tych miejscach, w których serwer różni się od laptopa.

## 1. Czego potrzeba

- Ubuntu LTS (22.04 albo 24.04), świeża instalacja.
- Docker Engine z wtyczką `compose` (nie Docker Desktop: na serwerze nie ma go po co instalować).
- 4 rdzenie, 8 GB pamięci, 60 GB dysku. Limity w nakładce są dobrane dokładnie pod to: baza
  dostaje 2 GB, kapsuła 3 GB, reszta mieści się w pozostałych trzech.
- Domena wskazująca na adres serwera rekordem A, ustawiona ZANIM wystartuje proxy. Let's Encrypt
  sprawdza ją w chwili pierwszego startu i bez działającego DNS certyfikat nie powstanie.
- Konto z prawem do dockera i katalog na kod, domyślnie `/srv/wenext`.
- Klucz ssh dodany na serwerze. Skrypt wdrożeniowy nie pyta o hasło i nie ma gdzie go wpisać.

## 2. Pierwsze uruchomienie

1. Na serwerze utwórz katalog i plik `.env`. Tego pliku NIE wysyła się z laptopa: ma powstać tu,
   raz, ręką człowieka. Wymagane klucze (skrypt wdrożeniowy sprawdza je po nazwie):
   `POSTGRES_PASSWORD`, `WENEXT_BROWSER_READER_TOKEN`, `WENEXT_BROWSER_READER_URL`,
   `WENEXT_GATEWAY_ISSUER_TOKEN`, `OPENAI_API_KEY`, `WENEXT_PUBLIC_DOMAIN`, `WENEXT_TLS_EMAIL`.
   Do tego `WENEXT_EDGE_BIND=0.0.0.0`, bez którego proxy nasłuchuje tylko na pętli zwrotnej
   i domena nie odpowie. Tokeny generuj na serwerze: `openssl rand -hex 32`.
2. Z laptopa:
   `make deploy-remote DEPLOY_ARGS="--host <adres> --user <login> --dry-run"`
   Przeczytaj, co się wydarzy. Potem to samo bez `--dry-run`.
3. Skrypt wysyła kod, sprawdza `.env`, buduje obrazy, stawia bazę, stosuje migracje POJEDYNCZO,
   podnosi stack i uruchamia `p0-restore-check`, czyli dowód, że schemat z migracji zgadza się
   z bazą i że zrzut da się odtworzyć. Zatrzymuje się na pierwszym kroku, który nie przeszedł.
4. Załóż operatora (tylko przy pierwszym wdrożeniu):
   `ssh <serwer> "cd /srv/wenext && WENEXT_OPERATOR_PASSWORD='...' docker compose -f docker-compose.yml -f compose.prod.yml exec -T api python -m control_plane.bootstrap"`
5. Otwórz `https://<domena>/`. Jeśli przeglądarka protestuje na certyfikat, sprawdź w logach
   proxy, czy Let's Encrypt odpowiedział: `docker compose ... logs caddy`.

## 3. Logowanie kapsuły do Reddita

Agent czyta Reddita przeglądarką w kapsule (D-025), a kapsuła musi być zalogowana kontem, którym
ma się posługiwać. Logujesz się RĘCZNIE, raz, i to jest świadomie niezautomatyzowane: hasło do
konta społecznościowego nie przechodzi przez kod tego produktu.

Droga podstawowa, gdy domena już działa: zaloguj się do studia, wejdź w kartę przeglądarki agenta
i otwórz ekran kapsuły. Bilet na ten ekran wystawia control plane i jest ważny dwie minuty
(D-026); adres, na który go wystawia, to Twoja domena.

Droga zapasowa, zanim DNS zacznie działać: zestaw tunel do gatewaya, który na serwerze słucha
tylko na pętli zwrotnej.

    ssh -L 8760:127.0.0.1:8760 <login>@<adres>

Potem otwórz na laptopie `http://127.0.0.1:8760/`. To ten sam ekran, tylko bez domeny.
Gdy skończysz, zamknij tunel: dopóki jest otwarty, ekran kapsuły jest dostępny z Twojego laptopa.

Po zalogowaniu sprawdź, czy odczyt działa:

    ssh <serwer> "cd /srv/wenext && docker compose -f docker-compose.yml -f compose.prod.yml exec api python -m community_observer.reddit_browser_probe r/<nazwa>"

## 4. Kopie zapasowe

    ssh <serwer> "cd /srv/wenext && REPO_ROOT=/srv/wenext bash tools/dev/backup.sh"

Skrypt robi zrzut bazy z sumą kontrolną i zostawia go w `artifacts/p7/backup/`. To jest połowa
roboty. Druga połowa jest Twoja: KOPIA MA OPUŚCIĆ SERWER. Kopia leżąca wyłącznie na maszynie,
która się psuje, nie jest kopią, tylko drugim plikiem na tym samym dysku.

    rsync -az <login>@<adres>:/srv/wenext/artifacts/p7/backup/ ~/kopie-wenext/

Rób to po każdej zmianie, na której Ci zależy, i przynajmniej raz na dobę, jeśli agent pracuje.
Poświadczeń kapsuły kopia świadomie nie zawiera (P7 etap B): są w wolumenie profili i po
odtworzeniu trzeba zalogować konto jeszcze raz, jak w punkcie 3.

## 5. Odtworzenie po awarii

    ssh <serwer> "cd /srv/wenext && REPO_ROOT=/srv/wenext bash tools/dev/restore_drill.sh"

Ćwiczenie odtwarza NAJNOWSZĄ kopię w świeżym kontenerze i porównuje liczbę wierszy z plikiem,
a nie z żywą bazą: porównanie z żywą bazą przechodzi także wtedy, gdy kopia jest pusta.
Rób to ćwiczenie co najmniej raz w miesiącu. Kopia, której nikt nigdy nie odtworzył, jest
założeniem, nie kopią.

Gdy serwer trzeba postawić od zera: zainstaluj Dockera, odtwórz `.env` ręcznie, uruchom
`deploy_remote.sh`, a potem wgraj najnowszy zrzut do bazy i zaloguj kapsułę.

## 6. Aktualizacja

    make deploy-remote DEPLOY_ARGS="--host <adres> --user <login>"

To samo polecenie, co przy pierwszym uruchomieniu. Migracje stosują się pojedynczo i tylko te,
których jeszcze nie ma. Gdy zmieniła się wyłącznie konfiguracja, dodaj `--skip-build`.

Po aktualizacji sprawdź trzy rzeczy: czy `docker compose ps` pokazuje wszystko jako `healthy`,
czy studio odpowiada pod domeną, i czy `p0-restore-check` przeszedł (skrypt uruchamia go sam).

## 7. Czego NIE zautomatyzowano, świadomie

- Logowania kont społecznościowych. Robi to człowiek, przez ekran kapsuły.
- Wynoszenia kopii poza serwer. Skrypt nie zna Twojego miejsca na kopie i nie powinien.
- Zakupu i konfiguracji domeny ani rekordów DNS.
- Odnowienia certyfikatu: robi to Caddy sam, ale nikt nie pilnuje, czy się udało. Ostrzeżenie
  z Let's Encrypt przyjdzie na `WENEXT_TLS_EMAIL` i wtedy trzeba zajrzeć w logi proxy.
- Przypięcia obrazu proxy po sumie kontrolnej. Przy pierwszym wdrożeniu wykonaj
  `docker image inspect caddy:2.8.4-alpine --format '{{index .RepoDigests 0}}'` i wpisz digest
  do `compose.prod.yml` oraz do `versions.lock.toml`, tak jak pozostałe obrazy.
- Wpisu portów proxy (80 i 443) do allowlisty w `versions.lock.toml`. Dopóki go nie ma,
  `make check-versions` zgłasza dwa porty spoza listy. To jest celowy sygnał do decyzji:
  publikacja czegokolwiek poza pętlą zwrotną ma być świadoma i widoczna w diffie.
- Monitoringu z powiadomieniami. Jest `make p7-week-report` i puls usług w studiu; nikt nie
  wysyła SMS-a, gdy usługa stanie.
