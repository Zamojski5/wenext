# =============================================================================
# api — control plane fazy E1.F1 (L5190: FastAPI, PostgreSQL, Next.js)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `services/control_plane/`.
#
# Cztery rzeczy, których nie wolno tu rozluźnić:
#
#   1. BRAK `EXPOSE` na host i brak `ports:` w compose. Control plane nie jest
#      publikowany: jego jedynym klientem jest serwerowa strona dashboardu
#      (D-016 — ciasteczko sesji nigdy nie trafia do kodu przeglądarkowego).
#   2. Non-root, uid 1000. Ten obraz montuje `./artifacts`, więc uid musi się
#      zgadzać z tym z compose'a (`user: "1000:1000"`).
#   3. `--factory`. `control_plane.app` NIE ma modułowego `app = create_app()`:
#      aplikacja powstaje przy starcie procesu, nie przy imporcie modułu.
#      Zmiana na `control_plane.app:app` wywali start kontenera — to jest
#      dokładnie ten rodzaj błędu, który w tym repo cztery razy ujawnił się
#      dopiero po przebudowie obrazu (docs/KONWENCJE.md).
#   4. KOPIOWANIE `services/mcp_sidecar`. `control_plane.policy` importuje
#      `mcp_sidecar.origins` jako jedyną kanonikalizację originów w repo (D-019).
#      Bez tego COPY obraz padał na `import control_plane.policy`, a testy na
#      hoście były zielone dzięki fallbackowi, którego kontener nie ma. Pilnują
#      tego dwa `RUN python -P -c` w obrazie finalnym — nie usuwaj ich razem
#      z „niepotrzebną" warstwą.
#
# `/healthz` jest JEDYNĄ trasą poza granicą operatora. Gdyby wymagała
# poświadczenia, healthcheck nigdy nie stałby się zielony i `make up --wait`
# wisiałby na całym stacku.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Warstwa zależności — osobno, żeby zmiana kodu control plane'u nie unieważniała instalacji.
#
# `mcp_sidecar` NIE JEST OZDOBĄ. `control_plane.policy.urlpolicy` importuje
# `mcp_sidecar.origins` jako jedyną kanonikalizację originów w repozytorium (D-019).
# Do 2026-08-31 ten Dockerfile kopiował wyłącznie `contracts/` i `control_plane/`,
# więc w obrazie `import control_plane.policy` padał głośnym ImportError-em, a testy
# na hoście były zielone tylko dzięki ścieżkowemu fallbackowi w `urlpolicy.py` —
# fallback liczy ścieżkę od `parents[3]` pakietu, czyli w obrazie od
# `/opt/venv/lib/python3.12/`, gdzie żadnego sidecara nie ma i być nie może.
# To dokładnie ten rodzaj błędu, który w tym repo cztery razy ujawnił się dopiero
# po przebudowie obrazu (docs/KONWENCJE.md).
#
# `--no-deps` przy sidecarze jest KONIECZNE, nie ostrożnościowe: jego jedyną zależnością
# jest `community-os-contracts`, nazwa workspace'owa, której na PyPI nie ma — goły pip
# nie zna workspace'ów uv i poszedłby jej szukać do indeksu. Contracts są zainstalowane
# wierszem wyżej, więc zależność jest już spełniona.
COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY contracts/src /build/contracts/src
COPY services/mcp_sidecar/pyproject.toml /build/mcp_sidecar/pyproject.toml
COPY services/mcp_sidecar/mcp_sidecar /build/mcp_sidecar/mcp_sidecar
# Outcome A: the community adapters the observe handlers run (docs/OUTCOMES.md §5). Same
# `--no-deps` reasoning as the sidecar — its only workspace dependency is contracts, installed
# above; its third-party dependencies (httpx, pydantic) are pinned by hand below.
COPY services/community_observer/pyproject.toml /build/community_observer/pyproject.toml
COPY services/community_observer/community_observer /build/community_observer/community_observer
# P1 etap 2 (D-040): połączenia Codex. `control_plane.workspace.engine` rozmawia z app-serverem
# przez wąski klient JSON-RPC z tego pakietu (SDK nie ma paginacji `model/list` ani
# `account/rateLimits/read` — services/codex_runtime/NOTES.md, errata 4), a
# `control_plane.workspace.codex_home` pisze config.toml katalogu połączenia WYŁĄCZNIE przez
# `codex_runtime.hardening`, żeby `cli_auth_credentials_store = "file"` miało jedno źródło
# (L2205). Ten sam `--no-deps`, co wyżej: jedyne zależności to contracts i openai-codex, oba
# instalowane w tym samym RUN.
COPY services/codex_runtime/pyproject.toml /build/codex_runtime/pyproject.toml
COPY services/codex_runtime/codex_runtime /build/codex_runtime/codex_runtime
COPY services/control_plane/pyproject.toml /build/pyproject.toml
RUN mkdir -p control_plane \
    && : > control_plane/__init__.py \
    && pip install /build/contracts \
    && pip install --no-deps /build/mcp_sidecar \
    && pip install --no-deps /build/community_observer \
    && pip install --no-deps /build/codex_runtime \
    && pip install --no-deps . \
    && pip install "fastapi==0.141.1" "uvicorn[standard]==0.52.4" "asyncpg==0.31.0" \
                   "argon2-cffi==25.1.0" "cryptography==50.0.1" "httpx==0.28.1" \
                   "openai-codex==0.147.0" \
    && python -c "import mcp_sidecar.origins, community_observer, openai_codex, codex_runtime.protocol"

# `cryptography` jest na tej liście z tego samego powodu, co `argon2-cffi`: bez niej
# `import control_plane.storage` pada w obrazie, a szyfrowanie profili (D-018) nie istnieje.
# Kontrakt obrazu poniżej sprawdza to importem.

# `argon2-cffi` MUSI stać na tej liście, mimo że jest w `pyproject.toml`: instalacja pakietu
# leci z `--no-deps`, więc zależności z pyprojectu NIE są tu instalowane — dlatego fastapi,
# uvicorn i asyncpg też są wymienione z ręki. Bez tej biblioteki `import control_plane.auth`
# pada w obrazie, a granica operatora (D-016: hasło Argon2id wobec wiersza w `operators`) nie
# istnieje. Kontrakt obrazu poniżej sprawdza to importem, żeby build padał tutaj, a nie
# kontener przy pierwszym logowaniu.

# Warstwa kodu.
COPY services/control_plane/control_plane ./control_plane
RUN pip install --no-deps --force-reinstall .


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext control-plane" \
      org.opencontainers.image.description="Control plane E1.F1: granica operatora, kolejka jobów, audyt" \
      org.opencontainers.image.licenses="proprietary"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_CONTROL_PLANE_BIND=0.0.0.0:8000 \
    WENEXT_ARTIFACTS_DIR=/var/wenext/artifacts

RUN groupadd --gid 1000 api \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin api \
    && mkdir -p /srv /var/wenext/artifacts \
    && chown -R 1000:1000 /srv /var/wenext

COPY --from=builder /opt/venv /opt/venv

# KONTRAKT OBRAZU, sprawdzany w obrazie FINALNYM, nie w builderze. `-P` nie dokłada
# katalogu roboczego do sys.path, więc `control_plane` idzie z /opt/venv — dokładnie tak,
# jak przy starcie procesu. Ten wiersz istnieje, bo `control_plane.policy` importuje
# `mcp_sidecar.origins`, a przez pierwsze dni fazy F1 tego pakietu w obrazie NIE BYŁO:
# usterka była niewidoczna dla `make test-all`, bo testy jadą na hoście i mają ścieżkowy
# fallback, którego obraz nie ma. Jeżeli ten RUN padnie, build ma padnąć razem z nim —
# a nie kontener przy pierwszym żądaniu dotykającym polityki URL.
# Outcome A: the observe module and both its outside worlds — the adapters and the Codex SDK
# with the CLI it brings. The `codex` symlink mirrors services/codex_runtime/Dockerfile so the
# SDK finds the same binary the GATE-3 login used.
RUN python -P -c "import control_plane.observe, control_plane.workspace, community_observer, openai_codex, codex_runtime.hardening" \
    && ln -s "$(/opt/venv/bin/python -c 'import codex_cli_bin, pathlib; print(pathlib.Path(codex_cli_bin.__file__).parent / "bin" / "codex")')" \
        /usr/local/bin/codex \
    && codex --version
RUN python -P -c "import control_plane.policy, mcp_sidecar.origins" \
    && python -P -c "import control_plane.policy.urlpolicy as u, mcp_sidecar.origins as o; assert u.canonical_origin is o.canonical_origin, 'dwie kanonikalizacje originu w obrazie'" \
    && python -P -c "import control_plane.auth as a; h = a.hash_credential('kontrakt-obrazu'); assert 'argon2id' in h, h[:20]; a.verify_credential" \
    && python -P -c "import control_plane.storage as s; assert s.AEAD_ALGORITHM == 'AES-256-GCM', s.AEAD_ALGORITHM"

USER 1000:1000
WORKDIR /srv

# Ten sam warunek, co healthcheck w compose — obraz ma być używalny też samodzielnie.
HEALTHCHECK --interval=3s --timeout=5s --retries=20 --start-period=10s \
    CMD ["python", "-c", "import sys,urllib.request\ntry:\n    r=urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)\n    sys.exit(0 if r.status==200 else 1)\nexcept Exception:\n    sys.exit(1)"]

CMD ["sh", "-c", "exec uvicorn control_plane.app:create_app --factory --host ${WENEXT_CONTROL_PLANE_BIND%%:*} --port ${WENEXT_CONTROL_PLANE_BIND##*:} --no-access-log"]
