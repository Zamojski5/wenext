#!/usr/bin/env bash
# Wdrożenie usługi na serwer poza laptopem założyciela (P7 etap C).
#
#   make deploy-remote DEPLOY_ARGS="--host wenext.example --user wenext --dry-run"
#   REPO_ROOT=$PWD bash tools/dev/deploy_remote.sh --host wenext.example --user wenext
#
# CO TEN SKRYPT ROBI, w tej kolejności i nie w innej:
#   1. wysyła repozytorium przez rsync, z wykluczeniami z .gitignore i BEZ `.env`;
#   2. sprawdza, czy `.env` NA SERWERZE ma wszystkie wymagane klucze (lista niżej);
#   3. buduje obrazy na serwerze;
#   4. stawia bazę i stosuje migracje POJEDYNCZO, skryptem z §8.30;
#   5. podnosi stack z nakładką produkcyjną;
#   6. uruchamia `p0-restore-check`, czyli dowód, że schemat z migracji zgadza się z live
#      i że zrzut da się odtworzyć;
#   7. wypisuje, co człowiek ma zrobić ręcznie (logowanie kapsuły do Reddita, wyniesienie kopii).
#
# `.env` NIE JEDZIE RSYNCIEM I NIGDY NIE POJECHAŁ. Plik z hasłem bazy, tokenami i kluczem
# modelu ma powstać na serwerze raz, ręką człowieka. Skrypt, który wysyła lokalny `.env`,
# przenosi na serwer poświadczenia z laptopa i cofa cały sens tego wdrożenia.
#
# `--dry-run` WYPISUJE POLECENIA I NIE WYKONUJE NICZEGO. To nie jest wygoda, tylko jedyny
# sposób, żeby ten skrypt dało się przetestować bez serwera, a bez testu skrypt wdrożeniowy
# jest sprawdzany pierwszy raz w dniu, w którym coś już nie działa.
#
# REGUŁA O DOWODZIE (D-007): każdy krok, który nie przeszedł, zatrzymuje wdrożenie z kodem
# wyjścia i zdaniem po polsku. Nie ma tu „kontynuuję mimo błędu".
set -uo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

HOST=""
USER_NAME="wenext"
DIRECTORY="/srv/wenext"
DRY_RUN=0
SKIP_BUILD=0

#: Klucze, bez których stack na serwerze nie wstanie albo wstanie i będzie kłamał. Pierwsze pięć
#: to `make up-observe-reddit` (hasło bazy, token czytnika, jego adres, token wystawcy ticketów
#: i hasło operatora), dwa ostatnie są nowe dla serwera: domena, na którą Caddy bierze
#: certyfikat, i adres, na który Let's Encrypt wyśle ostrzeżenie o wygaśnięciu.
REQUIRED_KEYS=(
  POSTGRES_PASSWORD
  WENEXT_BROWSER_READER_TOKEN
  WENEXT_BROWSER_READER_URL
  WENEXT_GATEWAY_ISSUER_TOKEN
  OPENAI_API_KEY
  WENEXT_PUBLIC_DOMAIN
  WENEXT_TLS_EMAIL
)

usage() {
  cat <<'USAGE'
Użycie: deploy_remote.sh --host <adres> [--user <login>] [--dir <katalog>] [--dry-run] [--skip-build]

  --host       adres serwera (wymagany)
  --user       login na serwerze (domyślnie: wenext)
  --dir        katalog na serwerze (domyślnie: /srv/wenext)
  --dry-run    wypisz polecenia i nie wykonuj żadnego
  --skip-build pomiń budowę obrazów (gdy zmieniła się tylko konfiguracja)
USAGE
}

fail() {
  echo "BŁĄD: $*" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) USER_NAME="${2:-}"; shift 2 ;;
    --dir) DIRECTORY="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "nieznany argument: $1" ;;
  esac
done

[ -n "$HOST" ] || { usage >&2; fail "--host jest wymagany"; }

TARGET="${USER_NAME}@${HOST}"
COMPOSE_FILES="-f docker-compose.yml -f compose.prod.yml"

# Jedno miejsce, w którym decyduje się „wykonać czy tylko pokazać". Każdy krok idzie przez to
# i dlatego `--dry-run` nie może przez pomyłkę czegoś jednak zrobić.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  $ %s\n' "$*"
    return 0
  fi
  "$@"
}

remote() {
  if [ "$DRY_RUN" -eq 1 ]; then
    # Bez cytowania przez %q: to jest wydruk dla człowieka, który ma przeczytać, co się stanie,
    # a nie wiersz do skopiowania. Polecenie wykonywane naprawdę idzie niżej, nietknięte.
    printf '  $ ssh %s "%s"\n' "$TARGET" "$*"
    return 0
  fi
  ssh "$TARGET" "$@"
}

echo "== wdrożenie na ${TARGET}:${DIRECTORY} =="
[ "$DRY_RUN" -eq 1 ] && echo "   TRYB PRÓBNY: poniżej są polecenia, żadne nie zostanie wykonane"

echo
echo "== 1. wysyłka repozytorium (bez .env i artefaktów) =="
# `--filter=:- .gitignore` bierze wykluczenia z .gitignore, a trzy `--exclude` niżej dokładają
# to, czego .gitignore NIE wyklucza, a czego na serwerze być nie może albo nie powinno.
run rsync -az --delete \
  --filter=':- .gitignore' \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'artifacts/' \
  --exclude 'output/' \
  "$ROOT/" "${TARGET}:${DIRECTORY}/"

echo
echo "== 2. sprawdzenie .env na serwerze =="
for key in "${REQUIRED_KEYS[@]}"; do
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  $ ssh %s grep -qE "^%s=.+" %s/.env\n' "$TARGET" "$key" "$DIRECTORY"
    continue
  fi
  if ! ssh "$TARGET" "grep -qE '^${key}=.+' ${DIRECTORY}/.env"; then
    fail "na serwerze brakuje ${key} w ${DIRECTORY}/.env. Uzupełnij RĘCZNIE na serwerze; ten skrypt świadomie nie wysyła lokalnego .env"
  fi
done
[ "$DRY_RUN" -eq 1 ] || echo "   wszystkie ${#REQUIRED_KEYS[@]} kluczy obecne"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo
  echo "== 3. budowa obrazów na serwerze =="
  remote "cd ${DIRECTORY} && docker compose ${COMPOSE_FILES} build"
else
  echo
  echo "== 3. budowa POMINIĘTA (--skip-build) =="
fi

echo
echo "== 4. baza i migracje pojedynczo =="
remote "cd ${DIRECTORY} && docker compose ${COMPOSE_FILES} up -d --wait db"
# Migracje po jednym pliku, skryptem z §8.30: przebieg, który zatrzymuje się na pierwszej
# niedziałającej migracji i mówi, która to była. `psql -f` z pętli zrobiłby to samo po cichu.
remote "cd ${DIRECTORY} && REPO_ROOT=${DIRECTORY} WENEXT_PG_CONTAINER=\$(docker compose ${COMPOSE_FILES} ps -q db) bash tools/dev/apply_migrations.sh"

echo
echo "== 5. podniesienie stacku z nakładką produkcyjną =="
remote "cd ${DIRECTORY} && docker compose ${COMPOSE_FILES} up -d --wait --wait-timeout 300"
remote "cd ${DIRECTORY} && docker compose ${COMPOSE_FILES} ps"

echo
echo "== 6. dowód: schemat i odtworzenie =="
remote "cd ${DIRECTORY} && REPO_ROOT=${DIRECTORY} bash tools/dev/p0_restore_check.sh"

echo
echo "== 7. co dalej, RĘCZNIE =="
cat <<NEXT
  1. Zaloguj kapsułę do Reddita przez VNC z tunelem ssh (D-026):
       ssh -L 5901:127.0.0.1:5901 ${TARGET}
     i dalej według docs/WDROZENIE-SERWER.md, sekcja „Logowanie kapsuły".
  2. Załóż operatora, jeśli to pierwsze wdrożenie:
       ssh ${TARGET} "cd ${DIRECTORY} && WENEXT_OPERATOR_PASSWORD='...' docker compose ${COMPOSE_FILES} exec -T api python -m control_plane.bootstrap"
  3. Zrób pierwszą kopię i WYNIEŚ JĄ POZA SERWER:
       ssh ${TARGET} "cd ${DIRECTORY} && REPO_ROOT=${DIRECTORY} bash tools/dev/backup.sh"
     Kopia leżąca wyłącznie na serwerze, który się psuje, nie jest kopią.
  4. Sprawdź, czy domena odpowiada po https i czy certyfikat jest z Let's Encrypt.
NEXT

echo
if [ "$DRY_RUN" -eq 1 ]; then
  echo "tryb próbny: nic nie zostało wykonane."
else
  echo "wdrożenie zakończone."
fi
