# =============================================================================
# Makefile — jedyny interfejs do stacku E1.F0
#
# Wszystko, co da się uruchomić w tym repo, ma tu swój cel. Jeżeli piszesz
# w README „uruchom docker compose ...", to znaczy, że brakuje tu celu.
# =============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# UWAGA: NIE używamy `.ONESHELL`. macOS ma GNU Make 3.81, gdzie ta dyrektywa jeszcze
# nie istnieje — wielolinijkowe recepty rozpadają się wtedy na osobne powłoki, zmienne
# nie przeżywają między liniami, a heredoki się rozsypują. Wszystko, co potrzebuje
# więcej niż jednej linii, mieszka w tools/dev/ jako prawdziwy skrypt.

REPO_ROOT := $(CURDIR)

# -----------------------------------------------------------------------------
# DOCKER_CONFIG — to nie jest kosmetyka, to warunek działania czegokolwiek.
#
# Helper poświadczeń Dockera na hoście deweloperskim wisi: każdy `docker pull`
# bez tego obejścia zawiesza się w nieskończoność z `error getting credentials`.
# Wskazujemy Dockera na katalog w repo z pustym {"auths":{}}, czyli na brak
# jakiegokolwiek helpera. Wszystkie obrazy, których używamy, są publiczne.
#
# `$(CURDIR)` zamiast `$(PWD)`: Make definiuje CURDIR zawsze i absolutnie,
# a PWD jest tylko zmienną odziedziczoną ze środowiska powłoki — przy
# `make -C <katalog>` PWD wskazuje nie tam, gdzie trzeba.
# -----------------------------------------------------------------------------
export DOCKER_CONFIG := $(REPO_ROOT)/infra/docker-config

# Buildkit; v5 compose i tak buduje przez Bake, ale na v2 to ma znaczenie.
export DOCKER_BUILDKIT := 1
export COMPOSE_BAKE := true

COMPOSE       := docker compose
COMPOSE_TEST  := docker compose -f docker-compose.yml -f compose.test.yml

# Serwisy, które podnosi `make up`. DoD: dokładnie te cztery, healthy < 90 s.
CORE_SERVICES := db capsule gateway fixture-site
UP_TIMEOUT    := 90

# Wszystkie profile — potrzebne przy `build`, `down`, `ps` i `clean`.
#
# MUSI być powtórzoną flagą, nie listą po przecinku. `docker compose --profile a,b`
# szuka JEDNEGO profilu o dosłownej nazwie "a,b" i nie pasuje do niczego — a mimo to
# kończy się kodem 0. Listę po przecinku przyjmuje wyłącznie zmienna COMPOSE_PROFILES.
# Zmierzone na Compose v2.40.3: `--profile test,report,manual,codex config --services`
# zwracało tylko capsule/db/fixture-site/gateway, więc `make build` po cichu pomijał
# codex, gonogo, tests i mcp-sidecar — w tym obraz wykonujący pomiar GATE-3.
ALL_PROFILES  := --profile test --profile report --profile manual --profile codex --profile dashboard

PYTHON := python3
RUFF_IMAGE := ghcr.io/astral-sh/ruff:0.16.5

.PHONY: help up up-observe up-observe-reddit migrate bootstrap-operator down build test test-all test-lease test-db test-host check-docs evals evals-live evals-memory-ab evals-session-vs-turn evals-conversations evals-conversations-live pilot-start pilot-status pilot-stop pilot-report pilot-approve pilot-publish-now pilot-publish-status pilot-publish-stop pilot-publish-reconcile pilot-publish-requeue pilot-actuator-check gonogo codex-gonogo \
        dashboard fmt lint clean env doctor check-versions config logs ps db-shell capsule-shell \
        p0-regression p0-restore-check p2-live-check codex-first-live-check \
        p4-day-report p6-read-probe \
        reddit-validation-report deploy-remote user-password \
        backup restore-drill p7-week-report pilot-to-project p4-live-check \
        codex-root-harden codex-home-check

# -----------------------------------------------------------------------------
help:  ## Ta lista
	@echo "wenext — E1.F0"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Start od zera:  make env && make up"

# -----------------------------------------------------------------------------
# Konfiguracja lokalna
# -----------------------------------------------------------------------------
env: $(REPO_ROOT)/.env $(REPO_ROOT)/infra/docker-config/config.json  ## Przygotuj .env i konfigurację Dockera

$(REPO_ROOT)/.env:
	@cp $(REPO_ROOT)/.env.example $(REPO_ROOT)/.env
	@echo "Utworzono .env z .env.example."
	@echo "Hasło Postgresa w .env.example jest DEWELOPERSKIE. Zmień je, zanim"
	@echo "cokolwiek z tego stacku dotknie danych, na których ci zależy."

$(REPO_ROOT)/infra/docker-config/config.json:
	@$(PYTHON) $(REPO_ROOT)/tools/dev/make_docker_config.py

doctor:  ## Sprawdź, czy host nadaje się do uruchomienia stacku
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/doctor.sh

check-versions:  ## Sprawdź, że compose i Dockerfile'e nie odjechały od versions.lock.toml
	@$(PYTHON) $(REPO_ROOT)/tools/dev/check_versions.py

check-docs:  ## Sprawdź, że dokumentacja nie rozjechała się z kodem
	@$(PYTHON) $(REPO_ROOT)/tools/dev/check_docs.py --details

# -----------------------------------------------------------------------------
config: env  ## Zwaliduj i wypisz rozwiązaną konfigurację compose
	@$(COMPOSE) config

# -----------------------------------------------------------------------------
# Stack
# -----------------------------------------------------------------------------
build: env check-versions  ## Zbuduj wszystkie obrazy (łącznie z profilami)
	@$(COMPOSE) $(ALL_PROFILES) build

up: env check-versions  ## Podnieś db + capsule + gateway + fixture-site i poczekaj na healthy
	@$(COMPOSE) up -d --wait --wait-timeout $(UP_TIMEOUT) $(CORE_SERVICES)
	@echo
	@$(COMPOSE) ps
	@echo
	@echo "Gateway: http://127.0.0.1:$${WENEXT_GATEWAY_HOST_PORT:-8760}/"
	@echo "CDP i VNC są celowo nieosiągalne z hosta. Do wnętrza kapsuły: make capsule-shell"

down:  ## Zatrzymaj wszystko (razem z profilami), zostaw dane
	@$(COMPOSE) $(ALL_PROFILES) down --remove-orphans

ps:  ## Stan serwisów
	@$(COMPOSE) $(ALL_PROFILES) ps

logs:  ## Logi (S=nazwa_serwisu, domyślnie wszystkie)
	@$(COMPOSE) logs -f --tail=200 $(S)

db-shell:  ## psql w bazie
	@$(COMPOSE) exec db psql -U $${POSTGRES_USER:-wenext} -d $${POSTGRES_DB:-wenext}

capsule-shell:  ## Powłoka w kapsule jako pwuser (jedyna droga do CDP)
	@$(COMPOSE) exec capsule bash

# -----------------------------------------------------------------------------
# Testy i evale
# -----------------------------------------------------------------------------
test: env check-versions  ## Suity akceptacyjne w kontenerze (profil test)
	@$(COMPOSE_TEST) --profile test up -d --wait --wait-timeout $(UP_TIMEOUT) $(CORE_SERVICES)
	@$(COMPOSE_TEST) --profile test run --rm --build tests

test-all:  ## Wszystkie suity uruchamialne na hoście, ze zbiorczym podsumowaniem
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/run_all_tests.sh

test-lease:  ## Same testy lease'u i fencingu (podnosi własny kontener Postgresa)
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/run_lease_tests.sh

# Suity bazodanowe fazy E1.F1. Każda dostaje WŁASNY, jednorazowy kontener Postgresa
# (wersja z versions.lock.toml), własny komplet migracji i własny werdykt czytany
# z --junitxml: przebieg z `passed == 0` albo z choćby jednym `skipped` jest PORAŻKĄ.
#
# Osobny cel, a nie część `test-all`, bo `run_all_tests.sh` jest sam pod testem
# (tests/test_all_tests_runner.py) i uruchamiany tam przeciwko atrapie repozytorium,
# w której każdy wołany skrypt musi mieć podstawkę wpisaną z nazwy.
#
# Każda suita w osobnej linii recepty — GNU Make 3.81 na macOS nie ma `.ONESHELL`,
# więc pętla wielolinijkowa rozpadłaby się na osobne powłoki.
test-db:  ## Suity wymagające PostgreSQL (db_schema, control_plane) — własny kontener na suitę
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/run_db_tests.sh services/db_schema
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/run_db_tests.sh services/control_plane

# Dashboard siedzi w profilu `dashboard`. Od 2026-09-03 profil JEST w ALL_PROFILES, więc
# `make build` buduje też obraz Next.js (apps/dashboard/Dockerfile istnieje od P13) — wcześniej
# nie był, bo `docker compose build` nad profilem bez Dockerfile'a przerywa cały build.
dashboard: env check-versions  ## Podnieś dashboard (profil dashboard, 127.0.0.1:3000)
	@$(COMPOSE) --profile dashboard up -d --wait --wait-timeout $(UP_TIMEOUT) dashboard
	@echo
	@echo "Dashboard: http://127.0.0.1:$${WENEXT_DASHBOARD_HOST_PORT:-3000}/"
	@echo "Control plane NIE jest publikowany na host — dashboard rozmawia z nim po stronie serwera (D-016)."

# Outcome A na VPS (docs/OUTCOMES.md §8): baza, control plane i dashboard, BEZ kapsuły Chrome,
# gatewaya i fixture-site. Obserwacja czyta przez publiczne/oficjalne API (D-023), więc
# przeglądarka nie jest potrzebna, a mały Linux z Dockerem wystarcza. Dashboard nadal
# słucha tylko na pętli zwrotnej hosta — na VPS wchodzi się przez tunel SSH.
# Migracji NIE stosuje (patrz docs/STAN-PRAC.md 8.3 pkt 1): `make migrate` osobno.
up-observe: env check-versions  ## Outcome A: db + api + dashboard bez kapsuły (VPS)
	@$(COMPOSE) --profile dashboard up -d --wait --wait-timeout $(UP_TIMEOUT) db api dashboard
	@echo
	@$(COMPOSE) --profile dashboard ps
	@echo
	@echo "Dashboard: http://127.0.0.1:$${WENEXT_DASHBOARD_HOST_PORT:-3000}/"
	@echo "Na VPS: ssh -L 3000:127.0.0.1:3000 <host>, potem ten sam adres w przeglądarce."
	@echo "Kapsuła, gateway i fixture-site NIE chodzą — Outcome A czyta przez API (D-023)."

# Plan B Reddita (D-025, docs/OUTCOMES.md §6 pkt 6): jak `up-observe`, plus kapsuła Chrome, bo
# Reddit jest czytany przez jej przeglądarkę (czytnik za tokenem). `fixture-site` wstaje z zależności
# kapsuły. Gateway nadal nie — ścieżka logowania człowieka przez ekran to osobne zadanie.
# Wymaga WENEXT_BROWSER_READER_TOKEN w .env (np. `openssl rand -hex 32`); bez niego kapsuła nie startuje.
up-observe-reddit: env check-versions  ## Outcome A + Reddit przez kapsułę: db + api + dashboard + capsule
	@test -n "$$(grep -E '^WENEXT_BROWSER_READER_TOKEN=.+' $(REPO_ROOT)/.env)" || { echo "ustaw WENEXT_BROWSER_READER_TOKEN w .env (np. openssl rand -hex 32) — czytnik kapsuły bez tokenu nie startuje"; exit 2; }
	@test -n "$$(grep -E '^WENEXT_BROWSER_READER_URL=.+' $(REPO_ROOT)/.env)" || { echo "ustaw WENEXT_BROWSER_READER_URL=http://capsule:8765 w .env — adapter przeglądarkowy jest opt-in (D-025)"; exit 2; }
	@test -n "$$(grep -E '^WENEXT_GATEWAY_ISSUER_TOKEN=.+' $(REPO_ROOT)/.env)" || { echo "ustaw WENEXT_GATEWAY_ISSUER_TOKEN w .env (np. openssl rand -hex 32) — bez niego gateway nie wystawia ticketów na ekran kapsuły (D-026)"; exit 2; }
	@$(COMPOSE) --profile dashboard up -d --wait --wait-timeout $(UP_TIMEOUT) db capsule gateway api dashboard
	@echo
	@$(COMPOSE) --profile dashboard ps
	@echo
	@echo "Dashboard: http://127.0.0.1:$${WENEXT_DASHBOARD_HOST_PORT:-3000}/"
	@echo "Reddit czyta Chrome kapsuły przez czytnik (D-025). Sesja konta: dopóki nikt nie zaloguje"
	@echo "Reddita w kapsule, joby REDDIT stoją w WAITING_CAPABILITY (WENEXT_REDDIT_REQUIRE_LOGIN=true)."
	@echo "Dowód odczytu: $(COMPOSE) exec api python -m community_observer.reddit_browser_probe r/<sub>"

migrate: env  ## Zastosuj wszystkie migracje na działającym kontenerze db (make up / make up-observe)
	@REPO_ROOT="$(REPO_ROOT)" WENEXT_PG_CONTAINER="$$($(COMPOSE) ps -q db)" \
	 bash $(REPO_ROOT)/tools/dev/apply_migrations.sh

# Hasło idzie ZE ZMIENNEJ, nigdy z argumentu: argument ląduje w historii powłoki i w `ps`.
#   WENEXT_OPERATOR_PASSWORD='…' make bootstrap-operator
#   WENEXT_OPERATOR_PASSWORD='…' make bootstrap-operator ROTATE=1   # zmiana hasła
bootstrap-operator: env  ## Załóż jedyną organizację, workspace i operatora (hasło z WENEXT_OPERATOR_PASSWORD)
	@test -n "$${WENEXT_OPERATOR_PASSWORD:-}" || { echo "ustaw WENEXT_OPERATOR_PASSWORD w środowisku (nie w argumencie)"; exit 2; }
	@$(COMPOSE) exec -T -e WENEXT_OPERATOR_PASSWORD api python -m control_plane.bootstrap \
	 --organization "$${WENEXT_ORGANIZATION:-wenext}" --workspace "$${WENEXT_WORKSPACE:-default}" \
	 $(if $(ROTATE),--rotate-password,)

# -----------------------------------------------------------------------------
# DEMO do filmu premierowego: fikcyjna organizacja "tallymark" w lokalnej bazie.
#
# Skrypt scripts/demo/seed_tallymark.py dotyka WYŁĄCZNIE organizacji demo (stałe id
# dddddddd-0000-4000-8000-000000000001), nigdy Twojej. Idzie do kontenera `api` przez stdin,
# bo plików z repo tam nie ma, i ODMAWIA, dopóki obraz `api` nie zna control_plane.demo:
# starszy obraz dałby organizacji demo dispatchera spiętego z prawdziwymi kapsułami.
# Kolejność przed filmem: make build, restart api, make demo-dry-run, make demo-seed.
# -----------------------------------------------------------------------------
DEMO_REPO   ?= $(HOME)/Dev/demo/tallymark
DEMO_SCRIPT := $(REPO_ROOT)/scripts/demo/seed_tallymark.py

.PHONY: demo-dry-run demo-seed demo-reset demo-watch demo-posted demo-status demo-codex

demo-dry-run: env  ## Demo: co zrobi demo-seed (tylko odczyt, nic nie zapisuje)
	@$(COMPOSE) exec -T api python - seed --dry-run < $(DEMO_SCRIPT)

demo-seed: env  ## Demo: załóż albo odśwież organizację demo (tallymark, 4 rozmowy, 4 szkice w EN)
	@$(COMPOSE) exec -T api python - seed < $(DEMO_SCRIPT)

demo-reset: env  ## Demo: wróć do początku ujęcia między dublami (to samo co demo-seed)
	@$(COMPOSE) exec -T api python - reset < $(DEMO_SCRIPT)

demo-watch: env  ## Demo: zostaw w drugim oknie na czas ujęcia; "Tak, wyślij" staje się "Posted" po ok. 3 s (45 min)
	@$(COMPOSE) exec -T api python - post --watch < $(DEMO_SCRIPT)

demo-posted: env  ## Demo: raz, od razu: wysłany szkic w organizacji demo staje się "Posted"
	@$(COMPOSE) exec -T api python - post < $(DEMO_SCRIPT)

demo-status: env  ## Demo: co jest teraz w organizacji demo (okazje, wnioski, liczby wierszy)
	@$(COMPOSE) exec -T api python - status < $(DEMO_SCRIPT)

demo-codex:  ## Demo: skieruj folder tallymark (DEMO_REPO=...) na projekt demo: .codex/config.toml, AGENTS.md
	@$(PYTHON) $(REPO_ROOT)/scripts/demo/codex_project.py --repo "$(DEMO_REPO)"

test-host:  ## Suity uruchamiane z hosta (wymaga uv >= 0.12.7 i działającego stacku)
	@echo "UWAGA: capsulenet jest siecią internal — z hosta NIE dosięgniesz kapsuły,"
	@echo "bazy ani fixture-site po nazwie. Suity dotykające kapsuły muszą iść przez"
	@echo "'docker compose exec capsule ...'. Ten cel jest dla suit czysto lokalnych."
	@uv run --frozen pytest tests -q $(PYTEST_ARGS)

evals: env  ## Fixture evals na modelu-stubie (bez sieci, bez konta ChatGPT)
	@mkdir -p $(REPO_ROOT)/artifacts/evals
	@$(COMPOSE_TEST) --profile test run --rm --no-deps --build tests \
		pytest evals -q -m "not live" \
		--junitxml=/var/wenext/artifacts/evals/evals-stub.xml
	@echo "Wynik: artifacts/evals/evals-stub.xml"

evals-live: env  ## Fixture evals na prawdziwym modelu (wymaga zalogowanego Codexa z make codex-gonogo)
	@mkdir -p $(REPO_ROOT)/artifacts/evals
	@$(COMPOSE_TEST) --profile test --profile codex run --rm --build tests \
		pytest evals -q -m live \
		--junitxml=/var/wenext/artifacts/evals/evals-live.xml
	@echo "Wynik: artifacts/evals/evals-live.xml"

# Porównanie z pamięcią i bez niej (P5 etap A, evals/memory-ab/).
#
# BEZ STACKU I BEZ MODELU, w odróżnieniu od dwóch celów wyżej. To, co da się sprawdzić offline,
# to kompletność odłożonych przypadków i to, że kryteria zapisano PRZED pomiarem; samo
# porównanie A/B wymaga zalogowanego Codexa i ma werdykt NIE_URUCHOMIONO. Cel wypisuje jedno
# i drugie, bo zero w raporcie wygląda jak pomiar.
evals-memory-ab:  ## Porównanie „z pamięcią / bez pamięci": część offline + werdykt NIE_URUCHOMIONO
	@test -x $(REPO_ROOT)/evals/.venv/bin/python || { \
		echo "BRAK ŚRODOWISKA: nie ma evals/.venv — suita NIE została uruchomiona." >&2; \
		echo "                 Zbuduj je: cd evals && uv sync" >&2; exit 2; }
	@cd $(REPO_ROOT) && evals/.venv/bin/python -m evals.harness.memory_ab

# Porównanie starej tury z nową sesją (P2 etap B, evals/session-vs-turn/).
#
# Ten sam wątek, ten sam cel, ten sam model; różni się TYLKO środowisko. Offline sprawdzamy, że
# zadania są kompletne i symetryczne (żadne nie jest napisane pod ścieżkę z narzędziami) i że
# kryteria zapisano przed pomiarem; samo porównanie wymaga zalogowanego Codeksa i ma werdykt
# NIE_URUCHOMIONO. Raport ląduje w artifacts/evals/, tam gdzie pozostałe.
evals-session-vs-turn:  ## Porównanie „stara tura / nowa sesja": część offline + NIE_URUCHOMIONO
	@test -x $(REPO_ROOT)/evals/.venv/bin/python || { \
		echo "BRAK ŚRODOWISKA: nie ma evals/.venv — suita NIE została uruchomiona." >&2; \
		echo "                 Zbuduj je: cd evals && uv sync" >&2; exit 2; }
	@cd $(REPO_ROOT) && evals/.venv/bin/python -m evals.harness.session_vs_turn --report

# Evale rozmów (R1, docs/ROADMAP-JAKOSC-ROZMOW.md; karty w docs/STAN-JAKOSCI-ROZMOW.md).
# Offline: nagrane odpowiedzi z evals/conversations/recorded/, epizody dev + syntetyczne,
# raport do przejrzenia bez etykiety modelu. Live: prawdziwy Codex W KONTENERZE api (auth.json
# w wolumenie codex-home), persona z bazy; pakiet evals jest kopiowany do kontenera, bo obraz
# api go nie zawiera; pliki z `docker compose cp` mają uid hosta, więc każdy przebieg dostaje własny
# katalog roboczy tworzony przez użytkownika api (bez sprzątania). Kontrakty i control_plane bierze
# z OBRAZU api, więc po zmianie tych
# pakietów najpierw `docker compose build api && docker compose up -d --no-deps api`.
# EVALS_CONV_ARGS dokleja własne flagi (np. --split all --only-synthetic).
evals-conversations:  ## Evale rozmów offline (nagrane odpowiedzi): raport do przejrzenia w artifacts/conversation-quality/
	@$(REPO_ROOT)/evals/.venv/bin/python -m evals.conversations.runner \
		--runtime recorded --split dev --include-synthetic $(EVALS_CONV_ARGS)
	@echo "Wynik: artifacts/conversation-quality/<run-id>/report.md"

evals-conversations-live: env  ## Evale rozmów na prawdziwym Codexie w kontenerze api (wymaga działającego stacku i zalogowanego Codexa)
	@STAMP=$$(date -u +%Y%m%dT%H%M%SZ); WORK=/tmp/wenext-evals-$$STAMP; \
	 $(COMPOSE) exec -T api mkdir -p $$WORK && \
	 $(COMPOSE) cp $(REPO_ROOT)/evals api:$$WORK/evals && \
	 $(COMPOSE) exec -T -e PYTHONPATH=$$WORK api python -m evals.conversations.runner \
		--runtime codex --codex-home /var/wenext/codex-home --persona-db \
		--split dev --out $$WORK/out $(EVALS_CONV_ARGS); STATUS=$$?; \
	 mkdir -p $(REPO_ROOT)/artifacts/conversation-quality && \
	 $(COMPOSE) cp api:$$WORK/out/. $(REPO_ROOT)/artifacts/conversation-quality/ ; \
	 echo "Wynik: artifacts/conversation-quality/<run-id>/ (runtime codex, katalog roboczy $$WORK w kontenerze)"; \
	 exit $$STATUS

# Pilot 24 h (docs/START-PILOTA-24H.md; karta w docs/STAN-JAKOSCI-ROZMOW.md). Wszystko w kontenerze
# api: baza, kolejka i zalogowany Codex są tam. `pilot-start` zapisuje manifest, spowalnia poll
# community, robi JEDNĄ próbę na prawdziwym modelu (kosztuje 1 z budżetu; wynik zostaje jako pierwsza
# reakcja runu) i zleca pierwszy cykl; bez udanej próby run jest zatrzymany i nic nie rusza.
# PILOT_PROFILE_JSON (plik JSON z czterema polami profilu głosu; BEZ wartości domyślnej, ustaw
# w środowisku albo w wywołaniu make) jest WYMAGANY: start zapisuje go jako nową wersję profilu
# (oryginał v2 zostaje); bez pliku start kończy się błędem.
# PILOT_ARGS dokleja flagi (np. --run-id …, --budget 24, --hours 24, --cycle-interval 3600 = cykl co godzinę).
PILOT_PROFILE_JSON ?=

pilot-start: env  ## Uruchom pilot 24 h na HN ask (manifest + smoke na prawdziwym modelu + pierwszy cykl)
	@test -s "$(PILOT_PROFILE_JSON)" || { echo "brak pliku profilu pilota: ustaw PILOT_PROFILE_JSON (wskazano: '$(PILOT_PROFILE_JSON)')" >&2; exit 2; }
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli start --profile-json - $(PILOT_ARGS) < "$(PILOT_PROFILE_JSON)"

pilot-status:  ## Stan ostatniego runu pilota (JSON: okno, budżet, cykle, reakcje, zdarzenia hosta)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli status $(PILOT_ARGS)

pilot-stop:  ## Zatrzymaj aktywny run pilota (przywraca poll community; wyniki zostają)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli stop $(PILOT_ARGS)

pilot-report:  ## Raport Markdown z runu pilota do artifacts/pilot/<run-id>/raport-<czas>.md
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli report $(PILOT_ARGS)

# Zgody na publikację z dokumentu (tabela: pilot_reactions.id + sha256 szkicu) do bazy przez
# istniejącą ścieżkę approvali; PILOT_CONSENTS=plik Markdown, PILOT_ACCOUNT=login konta na Reddicie.
# Oba BEZ wartości domyślnej: konto i dokument zgód są Twoje, więc ustaw je w środowisku
# (`export PILOT_ACCOUNT=...`) albo w wywołaniu (`make pilot-approve PILOT_ACCOUNT=...`).
PILOT_CONSENTS ?=
PILOT_ACCOUNT ?=

pilot-approve:  ## Zgody z dokumentu do approval_decisions + kolejka publikacji (nic nie publikuje)
	@test -s "$(PILOT_CONSENTS)" || { echo "brak dokumentu zgód: ustaw PILOT_CONSENTS (wskazano: '$(PILOT_CONSENTS)')" >&2; exit 2; }
	@test -n "$(PILOT_ACCOUNT)" || { echo "ustaw PILOT_ACCOUNT (login konta na Reddicie)" >&2; exit 2; }
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli approve-consents --consents - --account "$(PILOT_ACCOUNT)" --source "$(notdir $(PILOT_CONSENTS))" $(PILOT_ARGS) < "$(PILOT_CONSENTS)"

pilot-publish-now:  ## Jeden tik publikacji TERAZ (smoke: pierwsza zgoda z kolejki) i start łańcucha co 10 min
	@grep -Eq '^WENEXT_BROWSER_WRITER_TOKEN=.{16,}' "$(REPO_ROOT)/.env" || { echo "brak WENEXT_BROWSER_WRITER_TOKEN w .env (osobny sekret aktuatora, min. 16 znaków)" >&2; exit 2; }
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli publish-now $(PILOT_ARGS)

pilot-publish-status:  ## Kolejka publikacji: wnioski, statusy, dowody, odczyty kontrolne (JSON)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli publish-status $(PILOT_ARGS)

pilot-publish-stop:  ## Zatrzymaj kolejkę publikacji natychmiast (wnioski QUEUED -> STOPPED, flaga w manifestach)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli publish-stop $(PILOT_ARGS)

pilot-publish-reconcile:  ## Sama rekoncyliacja odczytem (UNKNOWN vs wątek, obserwacje), bez publikacji
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli publish-reconcile $(PILOT_ARGS)

pilot-publish-requeue:  ## Ponów wniosek UNKNOWN po błędzie PRZED wysłaniem: PILOT_ARGS="--request <id>"
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli publish-requeue $(PILOT_ARGS)

# Tryb 7 dni (D-029): polityka podpisana raz przez założyciela; sędzia szkiców i autozatwierdzenie
# działają w tiku publikacji (co 10 min), publikuje ten sam łańcuch w tempie manifestu runu.
PILOT_POLICY_COMMUNITIES ?= REDDIT:r/kubernetes,REDDIT:r/b2b_sales
PILOT_POLICY_SOURCE ?= D-029 (docs/DECYZJE.md), słowa założyciela 2026-09-13

pilot-policy-sign:  ## Podpisz politykę publikacji na 7 dni (sędzia szkiców; tempo domyślnie 1/h i 6/dobę): PILOT_ARGS="--hours 168 --min-confidence 0.8 --max-per-hour 2 --max-per-day 12"
	@test -n "$(PILOT_ACCOUNT)" || { echo "ustaw PILOT_ACCOUNT (login konta na Reddicie)" >&2; exit 2; }
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli policy-sign --communities "$(PILOT_POLICY_COMMUNITIES)" --account "$(PILOT_ACCOUNT)" --source "$(PILOT_POLICY_SOURCE)" $(PILOT_ARGS)

pilot-policy-status:  ## Aktywna polityka, werdykty sędziego i czekający kandydaci (JSON)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli policy-status

pilot-policy-revoke:  ## Odwołaj aktywną politykę (kolejka zostaje; publish-stop zatrzymuje wszystko)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli policy-revoke

pilot-community-probe:  ## Oceń kandydatów na społeczności z samego odczytu (nic nie włącza, nic nie pisze): PILOT_ARGS="--keys r/SaaS,r/nextjs"
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli community-probe $(PILOT_ARGS)

pilot-community-enable:  ## Włącz obserwację nowej społeczności (tylko odczyt): PILOT_ARGS="REDDIT r/SaaS"
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli enable-community $(PILOT_ARGS)

pilot-outcomes:  ## Tabela badawcza D-031: wynik każdej publikacji per ruch, wiek posta, źródło (Markdown + artefakt)
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli outcomes $(PILOT_ARGS)

pilot-draft-compare:  ## Ostatnie szkice wygenerowane na nowo bieżącym promptem i profilem, stary vs nowy (bez zapisu): PILOT_ARGS="--limit 5"
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli draft-compare $(PILOT_ARGS)

pilot-profile-save:  ## Zapisz nową wersję profilu założyciela z pliku JSON (4 pola): PILOT_PROFILE_JSON=<plik>; użyją go runy przedłużane z polityki
	@test -s "$(PILOT_PROFILE_JSON)" || { echo "brak pliku profilu: ustaw PILOT_PROFILE_JSON (wskazano: '$(PILOT_PROFILE_JSON)')" >&2; exit 2; }
	@$(COMPOSE) exec -T api python -m control_plane.pilot.cli profile-save --profile-json - < "$(PILOT_PROFILE_JSON)"

# ARTEFAKT LĄDUJE NA HOŚCIE, a nie w kontenerze, i to jest poprawka z 20.09. Kontener biegnie
# z `--rm`, więc raport zapisany do jego `/tmp` znikał razem z nim: zostawał sam wydruk na
# ekranie, a kontrola, która obiecuje „artefakt na każdej ścieżce wyjścia", nie zostawiała go
# nigdzie. Katalog profilu zostaje w `/tmp` kontenera świadomie: profil przeglądarki tej kontroli
# ma zniknąć, bo nie jest dowodem, tylko śmieciem po jednorazowym Chrome.
pilot-actuator-check:  ## Aktuator komentarzy i własnych wątków PRAWDZIWYM Playwrightem przeciw stronom stub (osobny kontener, własny profil)
	@mkdir -p $(REPO_ROOT)/artifacts
	@$(COMPOSE) run --rm --no-deps \
	 -v $(REPO_ROOT)/artifacts:/var/wenext/artifacts \
	 -e WENEXT_PROFILES_DIR=/tmp/actuator-profiles \
	 -e WENEXT_ARTIFACTS_DIR=/var/wenext/artifacts \
	 -e WENEXT_READER_PORT= -e WENEXT_VNC_SOCKET=/tmp/actuator-run/vnc.sock \
	 capsule python3 -m browser_supervisor.main --actuator-check --artifacts /var/wenext/artifacts
	@echo "Wynik: artifacts/actuator/actuator-check.json"

# -----------------------------------------------------------------------------
# Bramki
# -----------------------------------------------------------------------------
gonogo: env check-versions  ## Zbuduj raport Go/No-Go z tego, co już leży w artifacts/
	@mkdir -p $(REPO_ROOT)/artifacts/gonogo
	@$(COMPOSE) --profile report run --rm --build gonogo
	@echo "Raport: artifacts/gonogo/"

codex-gonogo: env  ## JEDYNY cel wymagający człowieka: logowanie do ChatGPT + wielogodzinny test unattended
	@if [ ! -x $(REPO_ROOT)/scripts/founder/codex-gonogo.sh ]; then \
		echo "Brak scripts/founder/codex-gonogo.sh (deliverable D20)."; \
		echo "Ten cel jest wejściem do runbooka założyciela — bez D20 nie ma czego uruchomić."; \
		exit 1; \
	fi
	@REPO_ROOT="$(REPO_ROOT)" \
	 COMPOSE="$(COMPOSE)" \
	 DOCKER_CONFIG="$(DOCKER_CONFIG)" \
	 bash $(REPO_ROOT)/scripts/founder/codex-gonogo.sh

# -----------------------------------------------------------------------------
# Jakość kodu
#
# ruff jedzie z przypiętego obrazu, nie z PATH-u dewelopera — formatowanie ma
# dawać ten sam wynik u każdego i w CI. Cena: jedno `docker run`.
# -----------------------------------------------------------------------------
fmt:  ## Sformatuj kod (ruff format + autofix importów)
	@docker run --rm -v $(REPO_ROOT):/io -w /io $(RUFF_IMAGE) format .
	@docker run --rm -v $(REPO_ROOT):/io -w /io $(RUFF_IMAGE) check --fix .

lint:  ## Sprawdź kod bez zmieniania go
	@docker run --rm -v $(REPO_ROOT):/io -w /io $(RUFF_IMAGE) format --check .
	@docker run --rm -v $(REPO_ROOT):/io -w /io $(RUFF_IMAGE) check .

# -----------------------------------------------------------------------------
clean:  ## Zatrzymaj stack, skasuj wolumeny i artefakty (NIEODWRACALNE)
	@$(COMPOSE) $(ALL_PROFILES) down --remove-orphans --volumes
	@rm -rf $(REPO_ROOT)/artifacts
	@find $(REPO_ROOT) -type d -name __pycache__ -prune -exec rm -rf {} +
	@rm -rf $(REPO_ROOT)/.pytest_cache $(REPO_ROOT)/.ruff_cache
	@echo "Skasowano wolumeny (profile Chromium, baza, CODEX_HOME) i artifacts/."

# -----------------------------------------------------------------------------
# P0 (docs/ROADMAP-PIVOT.md): praca nie ginie i nie dubluje się, a status odróżnia
# wykonanie, niepewny skutek i potwierdzony rezultat.
#
# Oba cele są TYLKO DO ODCZYTU wobec stacku live: stawiają własne, jednorazowe
# kontenery PostgreSQla, a z bazy live co najwyżej robią pg_dump. Recepty mają po
# jednej linii, bo Make 3.81 nie ma .ONESHELL (patrz uwaga na górze pliku).
# -----------------------------------------------------------------------------
p0-regression:  ## Zestaw regresji P0: restart, lease, wolna baza, niejednoznaczny submit, widoczność publikacji (artefakt w artifacts/p0/)
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/p0_regression.sh

p0-restore-check:  ## Test migracji i odtworzenia: schemat z migracji vs schemat live, potem pełne odtworzenie zrzutu i porównanie liczby wierszy
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/p0_restore_check.sh

# -----------------------------------------------------------------------------
# P4 (docs/ROADMAP-PIVOT.md): obecność trwa bez otwartego dashboardu.
#
# Raport dnia czyta bazę i nic w niej nie zmienia. Ciche tiki są w nim celowo: bez nich
# spokojna noc i zepsuty kolektor wyglądają tak samo.
# -----------------------------------------------------------------------------
p4-day-report:  ## Co agent robił przez ostatnią dobę, projekt po projekcie (artefakt w artifacts/p4/)
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/p4_day_report.sh

# Walidacja JEDNEGO projektu na Reddicie: te same liczby, co w widoku powrotu, plus werdykt
# wobec progów zamrożonych w tools/gonogo/criteria-p4.yaml PRZED pierwszym pomiarem.
# Okno krótsze niż doba nie dostaje werdyktu, tylko NIE_URUCHOMIONO.
reddit-validation-report:  ## Walidacja projektu na Reddicie (PILOT_ARGS="--project <id> --days N")
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/reddit_validation_report.sh $(PILOT_ARGS)

# -----------------------------------------------------------------------------
# P6 (docs/ROADMAP-PIVOT.md): wiele platform i współpracujące sesje.
#
# Sonda czytania BEZ LOGOWANIA, w jednorazowym kontenerze z własnym profilem, dokładnie jak
# pilot-actuator-check. Nie loguje się na żadne konto, nic nie publikuje i nie dotyka kapsuły
# live: `--no-deps` nie uruchamia zależności, a WENEXT_PROFILES_DIR wskazuje katalog w kontenerze,
# który znika razem z nim. Limit żądań siedzi w kodzie (read_probe.MAX_TARGETS), bo to jest pomiar
# zdolności, a nie zbieranie danych.
#
# ŹRÓDŁO JEST PODMONTOWANE NA ODCZYT, a obraz NIE jest przebudowywany. Przebudowa podmieniłaby
# obraz, na którym stoi kapsuła live, przy jej najbliższym restarcie; sonda nie ma prawa tego
# zrobić. Montaż dotyczy wyłącznie tego jednorazowego kontenera i znika razem z nim.
# -----------------------------------------------------------------------------
p6-read-probe:  ## Co da się przeczytać na X i LinkedIn bez konta (osobny kontener, artefakt w artifacts/p6/)
	@mkdir -p $(REPO_ROOT)/artifacts/p6
	@$(COMPOSE) run --rm --no-deps \
		-e WENEXT_PROFILES_DIR=/tmp/probe-profiles \
		-e WENEXT_ARTIFACTS_DIR=/tmp/artifacts \
		-e WENEXT_READER_PORT= \
		-e WENEXT_VNC_SOCKET=/tmp/probe-run/vnc.sock \
		-v $(REPO_ROOT)/artifacts/p6:/tmp/artifacts \
		-v $(REPO_ROOT)/services/browser_supervisor/browser_supervisor:/opt/wenext/services/browser_supervisor/browser_supervisor:ro \
		capsule python3 -m browser_supervisor.main --read-probe --artifacts /tmp/artifacts

# -----------------------------------------------------------------------------
# P2 (docs/ROADMAP-PIVOT.md): rozmowa prowadzi do wykonanego celu.
#
# Cała mechanika sesji jest mierzona na atrapie app-servera i biegnie w `make test-all`.
# TEN cel jest drugą połową i da się go uruchomić tylko tam, gdzie mieszka zalogowany
# Codex: w kontenerze `api`, na wolumenie codex-home. Świadomie NIE jest testem:
# wydaje limit konta i wymaga zalogowania, więc uruchamia go człowiek, nie suita.
# Nic nie publikuje: jedyne narzędzie proponujące działanie kończy się wnioskiem
# w ścieżce zgody.
# -----------------------------------------------------------------------------
p2-live-check:  ## Jedna tura sesji agenta na PRAWDZIWYM Codeksie, z dowodami (nic nie publikuje): P2_ARGS="--goal '...' --minutes 10"
	@$(COMPOSE) exec -T api python -m control_plane.sessions.livecheck $(P2_ARGS)

# D-046: to samo miejsce, inne pytanie. `p2-live-check` pyta, czy tura biegnie i woła narzędzia;
# ten pyta, czy z jednej prośby człowieka powstają KARTY, czy postęp umie nazwać etapy, czy
# konfiguracja silnika jest tą, o którą prosiła rozmowa, i czy nic nie wyszło na zewnątrz.
# Kod wyjścia 2 znaczy „nie zmierzono” (limit konta), a nie „zmierzono źle”.
# CF_GOAL (bez wartości domyślnej) to cel powierzony agentowi; bez niego działa neutralny cel z kodu.
# Chcesz wskazać konkretny profil osoby? `make codex-first-live-check CF_GOAL='Sprawdź ... konto Anny ...'`.
CF_GOAL ?=

codex-first-live-check:  ## Jedna rozmowa Codex-first na PRAWDZIWYM silniku: karty, postęp, zero publikacji: CF_ARGS="--minutes 15" CF_GOAL="..."
	@$(COMPOSE) exec -T api python -m control_plane.sessions.codex_first_livecheck $(if $(CF_GOAL),--goal '$(CF_GOAL)',) $(CF_ARGS)

# E9: dowód na to, że tura przestała pisać do wspólnego domu Codexa. Czyta same pliki na
# wolumenie, nie wchodzi do auth.json i nie pyta bazy, więc wolno go puścić kiedykolwiek, także
# przy pracującym pilocie. Kod wyjścia 1 znaczy „są wpisy do sprzątnięcia", 2 znaczy „nie było
# czego zmierzyć". Dwa przebiegi z zerem, po dwóch kolejnych wdrożeniach, są warunkiem usunięcia
# `reconcile_homes`.
codex-home-check:  ## Co leży w domach Codexa: wpisy serwera narzędzi, daty, obecność logowania
	@$(COMPOSE) exec -T api python -m control_plane.workspace.home_check $(HOME_CHECK_ARGS)

# -----------------------------------------------------------------------------
# P7 etap B (docs/ROADMAP-PIVOT.md): gotowość operacyjna usługi.
#
# Kopia CZYTA bazę i nic w niej nie zmienia. Ćwiczenie odtworzenia wgrywa kopię do NOWEGO,
# jednorazowego kontenera i porównuje liczbę wierszy; baza źródłowa jest przez cały czas tylko
# czytana. Werdykt obu czyta się z artefaktu, nie z kodu wyjścia.
# -----------------------------------------------------------------------------
# Awaryjne ustawienie hasła użytkownika, gdy człowiek zapomniał własnego i nie ma jak wrócić.
# Hasło WYŁĄCZNIE ze zmiennej: argument byłby widoczny w `ps` i zostałby w historii powłoki,
# ta sama reguła co przy `bootstrap-operator`.
#   WENEXT_NEW_PASSWORD='...' make user-password USER_EMAIL=ktos@firma.test
#   WENEXT_NEW_PASSWORD='...' make user-password USER_ORG=wenext USER_SET_EMAIL=ktos@firma.test
user-password: env  ## Ustaw hasło użytkownika (WENEXT_NEW_PASSWORD='...', USER_EMAIL= albo USER_ORG=)
	@test -n "$${WENEXT_NEW_PASSWORD:-}" || { echo "ustaw WENEXT_NEW_PASSWORD w środowisku (nie w argumencie)"; exit 2; }
	@test -n "$(USER_EMAIL)$(USER_ORG)" || { echo "podaj USER_EMAIL=<adres> albo USER_ORG=<organizacja>"; exit 2; }
	@$(COMPOSE) exec -T -e WENEXT_NEW_PASSWORD -e USER_EMAIL="$(USER_EMAIL)" -e USER_ORG="$(USER_ORG)" \
	 -e USER_SET_EMAIL="$(USER_SET_EMAIL)" api python -m control_plane.identity.password_cli

# P7 etap C: wdrożenie na serwer poza laptopem. `--dry-run` wypisuje polecenia i nic nie robi,
# więc da się to przeczytać przed pierwszym prawdziwym wdrożeniem. Runbook: docs/WDROZENIE-SERWER.md
deploy-remote:  ## Wdróż na serwer (DEPLOY_ARGS="--host <adres> --user <login> [--dry-run]")
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/deploy_remote.sh $(DEPLOY_ARGS)

backup:  ## Kopia zapasowa bazy z sumą kontrolną (artefakt w artifacts/p7/backup/)
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/backup.sh

restore-drill:  ## Odtwórz najnowszą kopię w świeżym kontenerze i porównaj liczbę wierszy
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/restore_drill.sh

p7-week-report:  ## Dostępność, incydenty, koszt i interwencje z siedmiu dób (artefakt w artifacts/p7/)
	@REPO_ROOT="$(REPO_ROOT)" bash $(REPO_ROOT)/tools/dev/p7_week_report.sh

# -----------------------------------------------------------------------------
# P4 etap B (docs/ROADMAP-PIVOT.md): most z pilota do ścieżki projektowej.
#
# Domyślnie POKAZUJE plan i nic nie zapisuje. Zapis wymaga `--apply` wpisanego świadomie, bo
# dotyczy konta, które publikuje na żywo. Delegacji nie podpisuje żaden z tych celów: zgoda jest
# kliknięciem człowieka w studiu.
# -----------------------------------------------------------------------------
pilot-to-project:  ## Plan przeniesienia pilota do projektu (PILOT_ARGS="--apply" wykonuje)
	@REPO_ROOT="$(REPO_ROOT)" PILOT_ARGS="$(PILOT_ARGS)" bash $(REPO_ROOT)/tools/dev/pilot_to_project.sh

p4-live-check:  ## Jeden tik obecności w trybie RESEARCH z prawdziwym Codexem (kontener api)
	@REPO_ROOT="$(REPO_ROOT)" PILOT_ARGS="$(PILOT_ARGS)" bash $(REPO_ROOT)/tools/dev/p4_live_check.sh

codex-root-harden:  ## Pokaż config.toml hardeningu korzenia codex-home (PILOT_ARGS="--dry-run")
	@REPO_ROOT="$(REPO_ROOT)" PILOT_ARGS="$(PILOT_ARGS)" bash $(REPO_ROOT)/tools/dev/codex_root_harden.sh
