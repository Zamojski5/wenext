# =============================================================================
# Session Capsule — jedna kapsuła = jedno konto = jeden profil = jeden proces.
#
# Kontekstem builda jest KORZEŃ repo (patrz docker-compose.yml), więc wszystkie
# ścieżki COPY są względem korzenia.
#
# Dwie decyzje, których nie wolno cofnąć bez ADR-a:
#
#   1. Obraz działa jako `pwuser` (non-root). Chrome odmawia startu jako root bez
#      `--no-sandbox`, a `--no-sandbox` jest w tym projekcie zakazany — sekcja 4
#      (granica zakresu) i 14.4 wymagają działającego sandboxa Chromium.
#      Zmierzone: "Running as root without --no-sandbox is not supported."
#      Non-root nie jest tu preferencją stylistyczną, tylko warunkiem koniecznym.
#
#   2. Chrome jest KOPIOWANY do obrazu, nie montowany z hosta. Bind mount z macOS
#      przez VirtioFS jest dla setek plików zasobów Chromium dramatycznie wolny.
#      Binarka ma ~286 MB rozpakowane — to świadomy koszt rozmiaru obrazu.
#
# Wersja Chrome NIE jest tu wpisana. Czyta ją fetch_chrome.py z versions.lock.toml,
# żeby istniało dokładnie jedno miejsce z pinem (DoD L5180).
# =============================================================================

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# -----------------------------------------------------------------------------
# Warstwa 1: pakiety systemowe.
#
# wmctrl (27.09, D-051): a person's sign-in runs without a DevTools endpoint, so that browser is
# closed the way a person closes it, by the window manager's close request, not over CDP.
#
# tigervnc-standalone-server + openbox są potrzebne WYŁĄCZNIE dla trybu INTERACTIVE
# (login, MFA, challenge, takeover). W trybie HEADLESS — czyli w całej pracy automatu —
# nie startuje ani display, ani window manager (L2365, L4365, L5532).
# Są w obrazie, a nie w osobnym, bo tryb przełącza się NA TYM SAMYM profilu i tym samym
# exact executable (L2447, L4948); dwa obrazy oznaczałyby ryzyko dwóch buildów Chrome.
# -----------------------------------------------------------------------------
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tigervnc-standalone-server \
        tigervnc-tools \
        openbox \
        wmctrl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Warstwa 2: Chrome for Testing.
#
# Osobna, cache'owalna warstwa — pobranie ~196 MB trwa u nas ok. 1,5 min i nie ma
# powodu powtarzać go przy każdej zmianie kodu.
# -----------------------------------------------------------------------------
COPY versions.lock.toml /opt/wenext/versions.lock.toml
COPY infra/browser/fetch_chrome.py /opt/wenext/fetch_chrome.py
RUN python3 /opt/wenext/fetch_chrome.py /opt/wenext/versions.lock.toml /opt/browser \
    && /opt/browser/chrome/chrome --version

# -----------------------------------------------------------------------------
# Layer 2b: Chromium for the X capsule (D-051).
#
# Google refuses a person's sign-in inside Chrome for Testing and accepts it in Chromium
# (measured 2026-09-27). Nothing is downloaded: the base image carries Chromium, and
# pin_chromium.py checks its version against versions.lock.toml and writes the build marker.
# Which capsule runs which build is WENEXT_BROWSER_BUILD in docker-compose.yml; one capsule,
# one build, one profile, as before.
# -----------------------------------------------------------------------------
COPY infra/browser/pin_chromium.py /opt/wenext/pin_chromium.py
RUN python3 /opt/wenext/pin_chromium.py /opt/wenext/versions.lock.toml

# -----------------------------------------------------------------------------
# Warstwa 3: zależności Pythona.
#
# Obraz Playwrighta ma binaria przeglądarek, ale NIE ma pakietu `playwright`
# w wariantach dla pojedynczej architektury — instalujemy go jawnie.
# `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` bo łączymy się przez CDP do WŁASNEGO Chrome,
# a nie do przeglądarki zarządzanej przez Playwright.
# -----------------------------------------------------------------------------
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY contracts /opt/wenext/contracts
COPY services/browser_supervisor /opt/wenext/services/browser_supervisor
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        playwright==1.62.0 \
        "pydantic>=2.12" \
        "asyncpg>=0.30" \
        "cryptography>=45" \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        /opt/wenext/contracts \
    && python3 -c "import playwright, community_os_contracts; print('zaleznosci OK')"

# -----------------------------------------------------------------------------
# Warstwa 4: katalogi runtime i użytkownik.
#
# `pwuser` (uid 1001) pochodzi z obrazu bazowego. Obraz startuje jako root, więc
# bez jawnego USER Chrome zażądałby --no-sandbox.
# -----------------------------------------------------------------------------
RUN mkdir -p /var/wenext/profiles /run/wenext/capsule \
    && chown -R pwuser:pwuser /var/wenext /run/wenext /opt/wenext

USER pwuser
WORKDIR /opt/wenext

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_BROWSER_BIN=/opt/browser/chrome/chrome \
    WENEXT_VERSIONS_LOCK=/opt/wenext/versions.lock.toml \
    PYTHONPATH=/opt/wenext/services/browser_supervisor

# Supervisor sam decyduje o trybie na podstawie WENEXT_BROWSER_MODE.
CMD ["python3", "-m", "browser_supervisor.main"]
