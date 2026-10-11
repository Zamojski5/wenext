# =============================================================================
# codex — runtime modeli (D12)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `services/codex_runtime/`.
#
# CZEGO W TYM OBRAZIE NIE MA I BYĆ NIE MOŻE (threat model L3894-3896, sekcja 13.3):
#   - żadnego mountu profili Chromium,
#   - żadnego klienta bazy i żadnych sekretów bazy,
#   - Docker socketa,
#   - narzędzi powłoki ponad to, co daje obraz bazowy (git, curl, wget nie są
#     instalowane celowo — runtime ma wyłączony shell tool i nie ma czego nimi robić).
# Compose dokłada resztę: brak wolumenu `profiles`, brak `capsulenet`, brak DATABASE_URL.
# Dopisanie któregokolwiek z nich unieważnia GATE-3.
#
# Wersje `openai-codex` i `openai-codex-cli-bin` idą PARAMI — protokół app-servera jest
# wersjonowany razem z CLI, a SDK zakłada zgodność 1:1. Piny są w versions.lock.toml
# [codex] i w services/codex_runtime/pyproject.toml.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Warstwa zależności — osobno, bo `openai-codex-cli-bin` to kilkadziesiąt megabajtów
# binarki i nie chcemy jej pobierać przy każdej zmianie kodu adaptera.
COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY contracts/src /build/contracts/src
COPY services/codex_runtime/pyproject.toml /build/pyproject.toml
RUN mkdir -p codex_runtime \
    && : > codex_runtime/__init__.py \
    && pip install /build/contracts \
    && pip install --no-deps . \
    && pip install "openai-codex==0.147.0"

# Warstwa kodu adaptera.
COPY services/codex_runtime/codex_runtime ./codex_runtime
COPY services/codex_runtime/gate3.py ./gate3.py
RUN pip install --no-deps --force-reinstall .


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext codex-runtime" \
      org.opencontainers.image.description="Codex app-server adapter: hardening, cztery izolowane thread roles (E1.F0)" \
      org.opencontainers.image.licenses="proprietary"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CODEX_HOME=/var/wenext/codex-home

RUN groupadd --gid 1000 codex \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin codex \
    && mkdir -p /var/wenext/codex-home /srv \
    && chown -R 1000:1000 /var/wenext /srv \
    && chmod 700 /var/wenext/codex-home

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/gate3.py /srv/gate3.py

# `codex` na PATH — healthcheck w docker-compose.yml wywołuje `codex --version`,
# a binarka mieszka wewnątrz pakietu `codex_cli_bin`, nie w `bin/` venva.
RUN ln -s "$(/opt/venv/bin/python -c 'import codex_cli_bin, pathlib; print(pathlib.Path(codex_cli_bin.__file__).parent / "bin" / "codex")')" \
        /usr/local/bin/codex \
    && codex --version

USER 1000:1000
WORKDIR /srv

# Ten sam warunek, co healthcheck w compose — obraz ma być używalny też samodzielnie.
HEALTHCHECK --interval=10s --timeout=10s --retries=6 --start-period=10s \
    CMD ["codex", "--version"]

# Domyślnie NIE startujemy app-servera: bez konta ChatGPT i tak nie wykona turnu,
# a `make up` ma podnosić dokładnie db + capsule + gateway + fixture-site.
# Serwis `codex` jest w profilu `codex` i uruchamiany jawnie.
CMD ["python", "-c", "import codex_runtime, sys; print('wenext codex-runtime gotowy;', 'metod w allowliscie:', len(codex_runtime.ALLOWED_METHODS)); sys.stdout.flush()"]
