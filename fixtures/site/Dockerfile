# =============================================================================
# fixture-site — deterministyczna imitacja dwóch archetypów kanału (D16)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `fixtures/site/`.
#
# Obraz jest celowo mały i bez zależności od reszty repo: fixture nie importuje
# `community-os-contracts` ani nie czyta `versions.lock.toml`. Piny bibliotek żyją
# w `fixtures/site/pyproject.toml`, bo to one decydują o odtwarzalności HTML-a,
# a nie wersja przeglądarki.
#
# Kontener działa jako użytkownik non-root (compose dokłada `cap_drop: [ALL]`
# i `no-new-privileges`). Port 8000 jest > 1024, więc nie potrzeba CAP_NET_BIND_SERVICE.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Warstwa zależności. Kopiujemy sam `pyproject.toml` i pusty pakiet, żeby zmiana
# treści fixture'a nie unieważniała warstwy z instalacją FastAPI i uvicorna.
COPY fixtures/site/pyproject.toml ./pyproject.toml
RUN mkdir -p src/wenext_fixture_site \
    && : > src/wenext_fixture_site/__init__.py \
    && pip install .

# Warstwa treści. `--no-deps`, bo zależności są już zainstalowane powyżej —
# gdyby pyproject je zmienił, unieważni się warstwa wyżej i pip zainstaluje nowe.
COPY fixtures/site/src ./src
RUN pip install --no-deps --force-reinstall .


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext fixture-site" \
      org.opencontainers.image.description="Fixture E1.F0: dwa archetypy kanału, osiem wymiarów 22.7, login wall i MFA" \
      org.opencontainers.image.licenses="proprietary"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_FIXTURE_BIND=0.0.0.0:8000 \
    WENEXT_FIXTURE_SEED=20260830

# Użytkownik non-root o stałym uid — profil i wolumeny w tym projekcie zakładają 1000.
RUN groupadd --gid 1000 fixture \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin fixture

COPY --from=builder /opt/venv /opt/venv

USER 1000:1000
WORKDIR /srv
EXPOSE 8000

# Ten sam warunek, co healthcheck w docker-compose.yml — obraz ma być używalny również
# uruchomiony samodzielnie, bez compose.
HEALTHCHECK --interval=2s --timeout=3s --retries=20 --start-period=5s \
    CMD ["python3", "-c", "import sys,urllib.request;\nimport urllib.error\ntry:\n    r=urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)\n    sys.exit(0 if r.status==200 else 1)\nexcept Exception:\n    sys.exit(1)"]

CMD ["python", "-m", "wenext_fixture_site"]
