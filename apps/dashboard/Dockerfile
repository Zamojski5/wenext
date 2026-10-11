# =============================================================================
# dashboard — cienki ekran operatora fazy E1.F1 (L5190 „FastAPI, PostgreSQL, Next.js")
#
# Zbudowany na wzorcu services/stream_gateway/Dockerfile: dwa stopnie, gotowe drzewo
# zależności kopiowane ze stopnia budującego, użytkownik non-root o uid 1000.
#
# KONTEKST BUILDU TO KORZEŃ REPO (`context: .` w docker-compose.yml), więc każda ścieżka
# w COPY jest liczona od korzenia, nie od `apps/dashboard/`.
#
# Obraz bazowy jest PRZYPIĘTY PO DIGEŚCIE z versions.lock.toml [stack.js]. Kontrole nie są
# symetryczne i wcześniejsza wersja tego komentarza kłamała, że są:
#
#   - TAG (`node:<wersja>`) pilnują DWIE kontrole: `tools/dev/check_versions.py` (czyta
#     `node:(\d+\.\d+\.\d+)` w każdym Dockerfile'u) i test w tym pakiecie;
#   - DIGEST pilnuje WYŁĄCZNIE `apps/dashboard/tests/version_pins.test.ts`
#     (`every_stage_of_the_dockerfile_starts_from_the_locked_image_by_digest`).
#     `check_versions.py` o digeście Node'a nie wie nic — zmierzone 2026-08-31: po usunięciu
#     `@sha256:…` z pierwszego stopnia kończy się kodem 0 i wypisuje „check-versions: OK".
#
# Nie usuwaj więc tego testu „bo jest druga strona" — dla digestu nie ma drugiej strony.
# Host tego projektu ma Node v24.12.0, czyli NIE tę wersję — dlatego cały łańcuch narzędzi JS
# jedzie z obrazu, a nie z PATH-u (apps/dashboard/NOTES.md).
#
# Trzy rzeczy, których nie wolno tu rozluźnić:
#
#   1. Ten kontener rozmawia z control plane WYŁĄCZNIE po stronie serwera (D-016). Nie ma tu
#      ani jednej zmiennej `NEXT_PUBLIC_*` — wartość z tym prefiksem trafia do bundla
#      przeglądarki, a wtedy klient rozmawiałby z API bezpośrednio i ciasteczko sesji
#      operatora przestałoby być niedostępne dla kodu przeglądarkowego.
#   2. Nie ma tu `npm` w stopniu runtime i nie ma źródeł. Leci wyłącznie wynik
#      `output: "standalone"` — tak samo jak gateway kopiuje gotowe `/opt/venv`.
#   3. uid 1000. Obraz `node:*-slim` MA JUŻ użytkownika `node` o uid 1000 i gid 1000
#      (sprawdzone: `docker run --rm node:24.20.0-trixie-slim id node` →
#      `uid=1000(node) gid=1000(node)`), więc — inaczej niż w gatewayu, gdzie obraz
#      `python:*-slim` żadnego nie ma — `groupadd --gid 1000` PADŁOBY tutaj na „GID already
#      exists". Nie dodawaj tworzenia użytkownika; `user: "1000:1000"` w compose i `USER`
#      poniżej wskazują na tego, który już jest.
# =============================================================================

FROM node:24.20.0-trixie-slim@sha256:50c3b2f6988dfc307b86e5301d69611af31f4789bdf232863b07d3b02fe55ae0 AS builder

ENV NEXT_TELEMETRY_DISABLED=1 \
    CI=1

WORKDIR /build

# Warstwa zależności — osobno, żeby zmiana kodu ekranów nie unieważniała instalacji.
# `.npmrc` idzie razem z manifestami, bo zawiera `engine-strict=true`: gdyby obraz bazowy
# kiedykolwiek rozjechał się z `engines` w package.json, `npm ci` ma paść tutaj, a nie
# wyprodukować artefakt zbudowany innym Node'em.
COPY apps/dashboard/package.json apps/dashboard/package-lock.json apps/dashboard/.npmrc ./
RUN npm ci --include=dev

# Warstwa kodu. Ścieżki wypisane pojedynczo, nie `COPY apps/dashboard/ ./`: przy kopiowaniu
# całego katalogu do obrazu weszłoby hostowe `node_modules` (zbudowane dla darwin-arm64,
# z inną binarką @next/swc) oraz `.next` i `.npm-cache`. Zapomniany plik daje tu głośny błąd
# builda; zapomniane wykluczenie dałoby obraz, który działa przypadkiem.
COPY apps/dashboard/tsconfig.json apps/dashboard/next.config.ts ./
COPY apps/dashboard/app ./app
COPY apps/dashboard/components ./components
COPY apps/dashboard/lib ./lib
COPY apps/dashboard/public ./public

RUN npm run build


FROM node:24.20.0-trixie-slim@sha256:50c3b2f6988dfc307b86e5301d69611af31f4789bdf232863b07d3b02fe55ae0

LABEL org.opencontainers.image.title="wenext dashboard" \
      org.opencontainers.image.description="Panel operatora E1.F1: komponenty serwerowe, zero wywołań API z przeglądarki (D-016)" \
      org.opencontainers.image.licenses="proprietary"

# PORT i HOSTNAME czyta `server.js` z wyniku `output: standalone`. 0.0.0.0 jest adresem
# WEWNĄTRZ kontenera — na host wychodzi wyłącznie `127.0.0.1:3000` z compose'a (D-014).
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /srv

# Wynik `output: "standalone"`: server.js, minimalne node_modules i manifesty. `static`
# i `public` stoją obok, bo standalone ich nie wciąga (tak działa Next, nie przeoczenie).
COPY --from=builder --chown=1000:1000 /build/.next/standalone ./
COPY --from=builder --chown=1000:1000 /build/.next/static ./.next/static
COPY --from=builder --chown=1000:1000 /build/public ./public

USER 1000:1000
EXPOSE 3000

# Ten sam warunek, co healthcheck w compose — obraz ma być używalny też samodzielnie.
# `/healthz` sprawdza TYLKO żywotność tego procesu; o control plane nie mówi nic (D-016).
HEALTHCHECK --interval=3s --timeout=5s --retries=20 --start-period=15s \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/healthz').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
