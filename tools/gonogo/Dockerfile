# =============================================================================
# gonogo — generator raportu Go/No-Go fazy E1.F0 (D19)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `tools/gonogo/`.
#
# Obraz jest jednostopniowy i celowo pusty: `report.py` nie ma ŻADNEJ zależności
# (własny minimalny parser YAML), bo raport ma dać się wygenerować także tam, gdzie
# nie ma pełnego środowiska. Dokładanie tu pipa z czymkolwiek byłoby cofnięciem
# tej decyzji — patrz `tools/gonogo/pyproject.toml`.
#
# UKŁAD ŚCIEŻEK JEST WIĄŻĄCY: `report.py` liczy `ROOT = parents[2]`, więc musi leżeć
# w `/app/tools/gonogo/`, a `docs/f0-limits/README.md` (sekcja „Czego ten raport
# NIE dowodzi") w `/app/docs/f0-limits/`.
#
# Artefakty przychodzą z zewnątrz — compose montuje `./artifacts:/var/wenext/artifacts`.
# Obraz NIE zawiera artefaktów: raport ma opisywać to, co faktycznie zmierzono w tym
# przebiegu, a nie to, co przypadkiem wpadło do kontekstu builda.
# =============================================================================

FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext gonogo" \
      org.opencontainers.image.description="Generator raportu Go/No-Go fazy E1.F0: trzy bramki, brak dowodu != GO" \
      org.opencontainers.image.licenses="proprietary"

# Te same nazwy zmiennych, które ustawia docker-compose.yml. Wartości domyślne są
# tu po to, żeby `docker run --rm -v ./artifacts:/var/wenext/artifacts wenext-gonogo`
# działał także bez compose'a.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_ARTIFACTS_DIR=/var/wenext/artifacts \
    WENEXT_VERSIONS_LOCK=/app/versions.lock.toml \
    WENEXT_CRITERIA=/app/tools/gonogo/criteria.yaml

RUN groupadd --gid 1000 gonogo \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin gonogo \
    && mkdir -p /app /var/wenext/artifacts \
    && chown -R 1000:1000 /app /var/wenext

WORKDIR /app

COPY --chown=1000:1000 tools/gonogo /app/tools/gonogo
COPY --chown=1000:1000 versions.lock.toml /app/versions.lock.toml
COPY --chown=1000:1000 docs/f0-limits /app/docs/f0-limits

USER 1000:1000

# Kontrakt obrazu: kryteria muszą się parsować i muszą zawierać trzy bramki.
# Pusty katalog artefaktów NIE może dać ani jednego GO — to ta sama własność,
# której pilnuje `tests/test_repo_consistency.py`, sprawdzona już przy buildzie.
RUN python3 tools/gonogo/report.py --artifacts /nonexistent --out /tmp/kontrakt \
    && python3 -c "\
import json;\
d = json.load(open('/tmp/kontrakt/gonogo.json'));\
w = {b['id']: b['werdykt'] for b in d['bramki']};\
assert len(w) == 3, w;\
assert set(w.values()) == {'NIE_URUCHOMIONO'}, w;\
print('gonogo image OK:', w)" \
    && rm -rf /tmp/kontrakt

# `report.py` nie czyta zmiennych środowiskowych — i nie ma czytać, to nie jego rola.
# Przekazujemy je jako argumenty tutaj. Powłoka jest tu potrzebna właśnie po to,
# żeby te zmienne rozwinąć; forma exec bez `sh -c` przekazałaby dosłowne `$WENEXT_…`.
CMD ["sh", "-c", "exec python3 /app/tools/gonogo/report.py --artifacts \"$WENEXT_ARTIFACTS_DIR\" --criteria \"$WENEXT_CRITERIA\" --out \"$WENEXT_ARTIFACTS_DIR/gonogo\""]
