# =============================================================================
# gateway — jedyny serwis publikowany na host (D10)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `services/stream_gateway/`.
#
# Ten kontener jest granicą między przeglądarką operatora a kapsułą. Trzy rzeczy,
# których nie wolno tu rozluźnić:
#
#   1. Poświadczenie VNC czyta z wolumenu runtime kapsuły i NIGDY nie oddaje go
#      przeglądarce — handshake RFB robi serwer (L2470).
#   2. Nie ma tu klienta przeglądarki ani Playwrighta. Gateway przekazuje bajty
#      protokołu RFB, nie steruje niczym.
#   3. Nie ma dostępu do profili Chromium. Widzi wyłącznie gniazdo unix i plik
#      z hasłami w `/run/wenext/capsule`.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Warstwa zależności — osobno, żeby zmiana kodu gatewaya nie unieważniała instalacji.
COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY contracts/src /build/contracts/src
COPY services/stream_gateway/pyproject.toml /build/pyproject.toml
RUN mkdir -p stream_gateway \
    && : > stream_gateway/__init__.py \
    && pip install /build/contracts \
    && pip install --no-deps . \
    && pip install "fastapi==0.141.1" "uvicorn[standard]==0.52.4" "cryptography>=45"

# Warstwa kodu.
COPY services/stream_gateway/stream_gateway ./stream_gateway
RUN pip install --no-deps --force-reinstall .

# noVNC (poziom 2 planu B, D-026): klient RFB dla ekranu logowania człowieka. Wersja i sha256
# archiwum WYŁĄCZNIE z versions.lock.toml (jak Chrome w kapsule) — skrypt nie zna numeru wersji.
# Do obrazu trafiają tylko moduły `core/` i `vendor/` (ES modules importowane przez stronę
# /login-capsule), bez `app/` z pełnym UI noVNC i bez plików HTML — jedyna strona jest w app.py.
COPY versions.lock.toml /build/versions.lock.toml
COPY infra/novnc/fetch_novnc.py /build/fetch_novnc.py
RUN python /build/fetch_novnc.py /build/versions.lock.toml /build/novnc \
    && test -f /build/novnc/core/rfb.js


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext stream-gateway" \
      org.opencontainers.image.description="RFB-aware proxy: VIEW_ONLY egzekwowane po stronie serwera (E1.F0)" \
      org.opencontainers.image.licenses="proprietary"

# Uwaga do lintera: `WENEXT_VNC_CREDENTIALS` to ŚCIEŻKA do pliku, nie sekret.
# Sam plik zapisuje BrowserSupervisor w wolumenie runtime kapsuły, z prawami 0600,
# i nigdy nie trafia do obrazu.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_GATEWAY_BIND=0.0.0.0:8760 \
    WENEXT_VNC_SOCKET=/run/wenext/capsule/vnc.sock \
    WENEXT_VNC_CREDENTIALS=/run/wenext/capsule/vnc-credentials \
    WENEXT_NOVNC_DIR=/srv/novnc

# Non-root o uid 1001 — TEJ SAMEJ tożsamości, co pwuser w kapsule. Wolumen `capsule-run`
# jest współdzielony z kapsułą: Xtigervnc tworzy gniazdo `vnc.sock` z prawami 0600, a
# supervisor zapisuje `vnc-credentials` z 0600, oba jako uid 1001. Zmierzone 2026-09-04
# 03:40 UTC przy pierwszym prawdziwym strumieniu: gateway jako uid 1000 dostawał
# `PermissionError` na pliku poświadczeń (a `connect()` do gniazda też wymaga prawa
# zapisu na nim). Wspólny uid zamiast rozluźniania praw do 0644/0666 — poświadczenia
# zostają czytelne wyłącznie dla tej jednej tożsamości w dwóch kontenerach.
RUN groupadd --gid 1001 gateway \
    && useradd --uid 1001 --gid 1001 --no-create-home --shell /usr/sbin/nologin gateway \
    && mkdir -p /run/wenext/capsule /srv \
    && chown -R 1001:1001 /run/wenext /srv

COPY --from=builder /opt/venv /opt/venv
# noVNC core/vendor tylko do odczytu; serwowane przez app.py pod /novnc (WENEXT_NOVNC_DIR).
COPY --from=builder /build/novnc /srv/novnc

USER 1001:1001
WORKDIR /srv
EXPOSE 8760

# Ten sam warunek, co healthcheck w compose — obraz ma być używalny też samodzielnie.
HEALTHCHECK --interval=3s --timeout=5s --retries=20 --start-period=10s \
    CMD ["python", "-c", "import sys,urllib.request\ntry:\n    r=urllib.request.urlopen('http://127.0.0.1:8760/healthz',timeout=3)\n    sys.exit(0 if r.status==200 else 1)\nexcept Exception:\n    sys.exit(1)"]

CMD ["sh", "-c", "exec uvicorn stream_gateway.app:app --host ${WENEXT_GATEWAY_BIND%%:*} --port ${WENEXT_GATEWAY_BIND##*:} --no-access-log"]
