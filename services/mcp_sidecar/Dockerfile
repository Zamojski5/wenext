# =============================================================================
# mcp-sidecar — task-scoped read-only MCP (D13)
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda
# ścieżka w COPY jest liczona od korzenia, nie od `services/mcp_sidecar/`.
#
# W produkcji to NIE jest długo żyjący serwis: Control Plane startuje sidecar per turn
# i zabija go po turnie (L2501, L2505). Obraz istnieje po to, żeby dało się obejrzeć
# manifest sześciu narzędzi i uruchomić testy — dlatego domyślnym CMD jest wypisanie
# manifestu, a nie pętla protokołu. Pętla wymaga capability od Control Plane
# (WENEXT_MCP_CAPABILITY_FILE) i bez niej kończy się kodem 2, czyli fail-closed.
#
# Obraz nie ma żadnych zależności zewnętrznych poza `community-os-contracts`:
# powierzchnia sidecara JEST granicą bezpieczeństwa, więc każda dodatkowa biblioteka
# to kod, który tę granicę może przesunąć.
# =============================================================================

FROM python:3.12-slim-trixie AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY contracts/pyproject.toml /build/contracts/pyproject.toml
COPY contracts/src /build/contracts/src
COPY services/mcp_sidecar/pyproject.toml /build/pyproject.toml
RUN mkdir -p mcp_sidecar \
    && : > mcp_sidecar/__init__.py \
    && pip install /build/contracts \
    && pip install --no-deps .

COPY services/mcp_sidecar/mcp_sidecar ./mcp_sidecar
RUN pip install --no-deps --force-reinstall .


FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="wenext mcp-sidecar" \
      org.opencontainers.image.description="Task-scoped read-only MCP: sześć narzędzi, fail-closed (E1.F0)" \
      org.opencontainers.image.licenses="proprietary"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WENEXT_MCP_FAIL_CLOSED=true

RUN groupadd --gid 1000 sidecar \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin sidecar \
    && mkdir -p /run/wenext /srv \
    && chown -R 1000:1000 /run/wenext /srv \
    && chmod 700 /run/wenext

COPY --from=builder /opt/venv /opt/venv

USER 1000:1000
WORKDIR /srv

# Kontrakt obrazu: manifest ma DOKŁADNIE sześć narzędzi i żadnego click/fill/press.
# Jeżeli ten warunek przestanie być prawdą, kontener nie zdąży się nawet uruchomić.
RUN python -c "\
from mcp_sidecar import TOOL_NAMES, FORBIDDEN_TOOLS, manifest;\
names = tuple(t['name'] for t in manifest());\
assert names == TOOL_NAMES and len(names) == 6, names;\
assert not (set(names) & FORBIDDEN_TOOLS);\
print('manifest OK:', names)"

CMD ["python", "-c", "import json, sys; from mcp_sidecar import manifest; json.dump(manifest(), sys.stdout, indent=2, ensure_ascii=False); print()"]
