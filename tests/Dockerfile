# =============================================================================
# tests — runner suit akceptacyjnych i evali (D18/D17)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `tests/`.
#
# Dlaczego ten obraz w ogóle istnieje: `capsulenet` jest siecią `internal: true`,
# więc z hosta NIE da się dosięgnąć kapsuły, gatewaya ani fixture-site po nazwie.
# Suity muszą uruchamiać się WEWNĄTRZ tej sieci, a to znaczy: we własnym obrazie.
#
# CO MUSI BYĆ W OBRAZIE I DLACZEGO — `tests/test_repo_consistency.py` liczy
# `KORZEN = parents[1]` od swojego pliku, więc leży w `/app/tests/`, a `/app` jest
# korzeniem repo z punktu widzenia testów. Testy spójności czytają realne pliki repo:
#
#   versions.lock.toml         piny wersji (test_check_versions_przechodzi)
#   docker-compose.yml         publikacja portów, obraz Postgresa
#   compose.test.yml           to samo, dla przebiegu testowego
#   Makefile                   skanowany na literały wersji przeglądarki
#   infra/capsule/Dockerfile   test_dockerfile_kapsuly_nie_zawiera_wersji_chrome
#   infra/browser/…json        check_versions.py liczy sha256 profilu seccomp
#   tools/dev/check_versions.py  uruchamiany jako podproces
#   tools/gonogo/              report.py + criteria.yaml, uruchamiane jako podproces
#   docs/adr/                  każdy ADR musi mieć status i datę
#   */pyproject.toml           każdy member workspace'u musi go mieć
#
# Evale (`evals/`) importują `community_os_contracts` jako PAKIET i `wenext_fixture_site`
# PO ŚCIEŻCE (`fixtures/site/src` wstawiane na sys.path przez harness/dataset.py).
# Dlatego kopiujemy oba drzewa, a nie tylko zainstalowane koła.
#
# Czego tu celowo NIE MA:
#   - Playwrighta i przeglądarki. Suity dotykające CDP idą przez kapsułę
#     (`docker compose exec capsule`), a nie stąd — druga kopia przeglądarki w innym
#     buildzie unieważniałaby capability snapshot (L2580).
#   - literału wersji Chrome. Ten plik jest skanowany przez własne testy repo.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Warstwa runnera. Wersje odpowiadają temu, co rozwiązał `uv.lock` w korzeniu —
# obraz i host mają uruchamiać ten sam pytest, inaczej „u mnie przechodzi" przestaje
# cokolwiek znaczyć. asyncpg jest dla suit z bazą (D18), httpx dla suit HTTP.
RUN pip install \
        "pytest==9.1.1" \
        "pytest-asyncio==1.4.0" \
        "asyncpg==0.31.0" \
        "httpx==0.28.1"

# Warstwa kontraktów (D21). Osobno, bo zmiana treści evali nie ma unieważniać
# instalacji pydantica.
COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY contracts/src /build/contracts/src
RUN pip install /build/contracts

# Warstwa fixture site'u (D16). Evale importują z niego generator po ścieżce, ale
# pakiet instalujemy w całości, żeby jego PINY (fastapi, jinja2, python-multipart,
# uvicorn) miały jedno źródło prawdy — `fixtures/site/pyproject.toml` — zamiast być
# przepisane tutaj. Najpierw sam pyproject i pusty pakiet, żeby zmiana treści
# fixture'a nie ciągnęła ponownej instalacji zależności.
COPY fixtures/site/pyproject.toml /build/fixture/pyproject.toml
RUN mkdir -p /build/fixture/src/wenext_fixture_site \
    && : > /build/fixture/src/wenext_fixture_site/__init__.py \
    && pip install /build/fixture
COPY fixtures/site/src /build/fixture/src
RUN pip install --no-deps --force-reinstall /build/fixture


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext tests" \
      org.opencontainers.image.description="Runner suit akceptacyjnych i evali fazy E1.F0" \
      org.opencontainers.image.licenses="proprietary"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_ARTIFACTS_DIR=/var/wenext/artifacts \
    WENEXT_EVALS_REPORT_DIR=/var/wenext/artifacts/evals

# Użytkownik non-root o stałym uid — tak jak w pozostałych obrazach tego repo.
# `/app` musi być zapisywalny: pytest trzyma tam `.pytest_cache`, a testy piszą
# artefakty do zamontowanego `/var/wenext/artifacts`.
RUN groupadd --gid 1000 tests \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin tests \
    && mkdir -p /app /var/wenext/artifacts \
    && chown -R 1000:1000 /app /var/wenext

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Korzeń repo widziany przez testy. Struktura katalogów MUSI być zachowana —
# `KORZEN = parents[1]` i `ROOT = parents[2]` w skryptach liczą się po ścieżkach.
COPY --chown=1000:1000 pyproject.toml versions.lock.toml docker-compose.yml compose.test.yml Makefile /app/
COPY --chown=1000:1000 contracts /app/contracts
COPY --chown=1000:1000 evals /app/evals
# services/ — ADDED 2026-08-31, and its absence was a defect rather than an omission.
#
# The consistency suites scan the repository tree: `test_build_files_contain_no_chrome_version`
# walks every Dockerfile, `test_no_sandbox_appears_nowhere_in_the_code` every `*.py`,
# `test_every_all_entry_is_actually_bound` every `__init__.py`, `check_versions.py` every
# Dockerfile again. Without this line every one of them ran INSIDE THE CONTAINER over a tree
# with no service code in it — and came out green over nothing. Phase E1.F1 adds two service
# packages, so the blind spot was about to grow rather than shrink.
#
# `.venv`, `__pycache__` and `.pytest_cache` never enter the build context (see .dockerignore),
# so this copies source, not environments.
COPY --chown=1000:1000 services /app/services
COPY --chown=1000:1000 plugins /app/plugins
COPY --chown=1000:1000 fixtures/site/pyproject.toml /app/fixtures/site/pyproject.toml
COPY --chown=1000:1000 fixtures/site/src /app/fixtures/site/src
COPY --chown=1000:1000 tests /app/tests
COPY --chown=1000:1000 tools/dev/check_versions.py /app/tools/dev/check_versions.py
COPY --chown=1000:1000 tools/gonogo /app/tools/gonogo
COPY --chown=1000:1000 db /app/db
COPY --chown=1000:1000 docs/adr /app/docs/adr
COPY --chown=1000:1000 docs/f0-limits /app/docs/f0-limits
COPY --chown=1000:1000 infra/browser/seccomp_profile.json /app/infra/browser/seccomp_profile.json
COPY --chown=1000:1000 infra/capsule/Dockerfile /app/infra/capsule/Dockerfile

# apps/ — the Next.js dashboard (D-014). NOT copied yet, and the line is commented out rather
# than written, because `COPY` of a path that does not exist is a hard build error: adding it
# before `apps/dashboard/` lands would break the image for everybody.
#
# This comment is not the guard. `tests/test_repo_consistency.py` has
# `test_the_tests_image_copies_every_source_tree_the_suites_read`, which starts failing the
# moment `apps/` appears in the repository — because a convention nobody tests stops applying
# in silence, which is how this repository lost its port invariant for months.
COPY --chown=1000:1000 apps /app/apps

USER 1000:1000

# Kontrakt obrazu: jeżeli te trzy rzeczy nie dadzą się zaimportować, żadna suita
# nie ruszy, a błąd zobaczylibyśmy dopiero w `docker compose run`. Lepiej tutaj.
RUN python -c "\
import community_os_contracts, sys;\
sys.path.insert(0, '/app/fixtures/site/src');\
from wenext_fixture_site import generator, seed_data;\
from evals.harness import dataset;\
print('tests image OK:', len(dataset.observations()), 'watkow fixture')"

# `-m \"not live\"` jest domyślne, bo bramka GATE-3 wymaga człowieka z kontem ChatGPT
# (patrz tools/gonogo/criteria.yaml). Przebieg bez tej flagi udawałby, że jej brak
# jest wynikiem — a nie jest.
CMD ["pytest", "tests", "evals", "-q", "-m", "not live"]
