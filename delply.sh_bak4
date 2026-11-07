#!/usr/bin/env bash
# AmSaJ Used Laptops - local deployment script (macOS, Linux, Git Bash, WSL)
#
# Usage (run from the project folder, next to docker-compose.yml):
#   ./deploy.sh                 backup, build, start, migrate, check
#   ./deploy.sh --pull          also run "git pull" first
#   ./deploy.sh --test          also run the automated tests
#   ./deploy.sh --no-backup     skip the database backup
#   ./deploy.sh --update PATH   copy new project files (a zip or a folder) into this folder
#                               first. Your docker-compose.yml, .env and media are kept.
#                               If the "orders" folder (Phase 4) is missing, the script asks
#                               for the path automatically.
#
# This script NEVER deletes the database. It does not touch docker-compose.yml, so your
# PostgreSQL port setting for DBeaver stays exactly as you configured it.

set -euo pipefail
cd "$(dirname "$0")"

DO_PULL=0; DO_TEST=0; DO_BACKUP=1; UPDATE_PATH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) DO_PULL=1 ;;
    --test) DO_TEST=1 ;;
    --no-backup) DO_BACKUP=0 ;;
    --update) shift; UPDATE_PATH="${1:-}"; [ -n "$UPDATE_PATH" ] || { echo "--update needs a path"; exit 1; } ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Copy new project files (from a zip or an unzipped folder) into the current folder.
# Your own docker-compose.yml, .env, override file, media and backups are never overwritten.
install_update() {
  local path="$1" tmp="" root manage src
  path="${path%\"}"; path="${path#\"}"
  [ -e "$path" ] || fail "Path not found: $path"
  if [ -d "$path" ]; then
    root="$path"
  else
    case "$path" in *.zip) ;; *) fail "Please give a .zip file or a folder." ;; esac
    command -v unzip >/dev/null 2>&1 || fail "'unzip' is not installed. Unzip the file yourself and give me the folder."
    tmp="$(mktemp -d)"; unzip -q "$path" -d "$tmp"; root="$tmp"
  fi
  manage="$(find "$root" -name manage.py -print -quit)"
  [ -n "$manage" ] || fail "manage.py was not found there. Is it the AmSaJ project zip or folder?"
  src="$(cd "$(dirname "$manage")" && pwd)"
  [ "$src" != "$(pwd)" ] || fail "That is the folder you are already in."
  (cd "$src" && tar -cf - --exclude=docker-compose.yml --exclude=.env --exclude=docker-compose.override.yml \
      --exclude=db.sqlite3 --exclude=deploy.sh --exclude=deploy.ps1 --exclude=media --exclude=backups \
      --exclude=.git --exclude=__pycache__ .) | tar -xvf - | sed 's/^/  /'
  [ -z "$tmp" ] || rm -rf "$tmp"
  echo "Project files updated. Your docker-compose.yml, .env and media were kept."
}

# Phase 4 needs a few extra settings in the web service of docker-compose.yml. Offer to add them.
ensure_compose_settings() {
  grep -q "BANK_ACCOUNT_NAME" docker-compose.yml && return 0
  echo
  echo "WARNING: your docker-compose.yml does not have the Phase 4 settings (payment gateway and bank details)."
  if ! grep -Eq '^[[:space:]]*POSTGRES_HOST:[[:space:]]*db[[:space:]]*$' docker-compose.yml; then
    echo "Add these lines under 'environment:' of the web service, then run this script again:"
    printf '      %s\n' 'SITE_URL: ${SITE_URL:-http://localhost:8000}' 'PAYMENT_GATEWAY: ${PAYMENT_GATEWAY:-test}' \
      'BANK_ACCOUNT_NAME: ${BANK_ACCOUNT_NAME:-}' 'BANK_NAME: ${BANK_NAME:-}' \
      'BANK_ACCOUNT_NUMBER: ${BANK_ACCOUNT_NUMBER:-}' 'BANK_IFSC: ${BANK_IFSC:-}' 'BANK_UPI_ID: ${BANK_UPI_ID:-}'
    return 0
  fi
  read -r -p "Add them to the web service now? A copy of your file is saved as docker-compose.yml.bak (Y/n) " answer
  case "$answer" in [nN]*) echo "Skipped. Without them the bank transfer details will not show."; return 0 ;; esac
  cp docker-compose.yml docker-compose.yml.bak
  awk '{ print }
       /^[[:space:]]*POSTGRES_HOST:[[:space:]]*db[[:space:]]*$/ {
         match($0, /^[[:space:]]*/); ind = substr($0, 1, RLENGTH)
         print ind "SITE_URL: ${SITE_URL:-http://localhost:8000}"
         print ind "PAYMENT_GATEWAY: ${PAYMENT_GATEWAY:-test}"
         print ind "BANK_ACCOUNT_NAME: ${BANK_ACCOUNT_NAME:-}"
         print ind "BANK_NAME: ${BANK_NAME:-}"
         print ind "BANK_ACCOUNT_NUMBER: ${BANK_ACCOUNT_NUMBER:-}"
         print ind "BANK_IFSC: ${BANK_IFSC:-}"
         print ind "BANK_UPI_ID: ${BANK_UPI_ID:-}"
       }' docker-compose.yml.bak > docker-compose.yml
  echo "docker-compose.yml updated (backup: docker-compose.yml.bak)."
}

# 1. Pre-flight checks ------------------------------------------------------
step "Checking Docker"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed or not in PATH."
docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and try again."
docker compose version >/dev/null 2>&1 || fail "'docker compose' is not available. Update Docker Desktop."
[ -f docker-compose.yml ] || fail "docker-compose.yml not found. Run this script from the project folder."

# 1b. Make sure the project files are complete -------------------------------
if [ -n "$UPDATE_PATH" ] || [ ! -d orders ]; then
  if [ -z "$UPDATE_PATH" ]; then
    step "Phase 4 files not found"
    echo "This folder does not contain the 'orders' app (cart, checkout and orders)."
    echo "Enter the path to amsaj-phase4.zip (or to the unzipped amsaj folder) and I will copy the files here."
    echo "Your docker-compose.yml, .env and media will not be touched."
    read -r -p "Path (press Enter to skip): " UPDATE_PATH
  fi
  if [ -n "$UPDATE_PATH" ]; then
    step "Copying project files"
    install_update "$UPDATE_PATH"
    ensure_compose_settings
  else
    echo "Skipped. The shop will run without cart, checkout and orders."
  fi
fi

# 2. Optional: get the latest code ------------------------------------------
if [ "$DO_PULL" -eq 1 ]; then
  step "Pulling the latest code from Git"
  if [ -n "$(git status --porcelain)" ]; then
    fail "You have uncommitted changes. Commit or stash them first (git status shows them)."
  fi
  git pull --ff-only
fi

# 3. Back up the database before changing anything --------------------------
if [ "$DO_BACKUP" -eq 1 ]; then
  step "Backing up the database"
  if [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
    mkdir -p backups
    DB_USER="$(docker compose exec -T db printenv POSTGRES_USER | tr -d '\r')"
    DB_NAME="$(docker compose exec -T db printenv POSTGRES_DB | tr -d '\r')"
    STAMP="$(date +%Y%m%d_%H%M%S)"
    docker compose exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" -f "/tmp/backup_$STAMP.sql"
    docker compose cp "db:/tmp/backup_$STAMP.sql" "backups/amsaj_$STAMP.sql"
    docker compose exec -T db rm -f "/tmp/backup_$STAMP.sql"
    echo "Saved backups/amsaj_$STAMP.sql"
    # keep only the 10 newest backups
    ls -1t backups/amsaj_*.sql 2>/dev/null | tail -n +11 | while read -r old; do rm -f -- "$old"; done
  else
    echo "Database container is not running yet, nothing to back up."
  fi
fi

# 4. Build and start --------------------------------------------------------
step "Building and starting the containers"
docker compose up -d --build

# 5. Wait until the website answers -----------------------------------------
step "Waiting for the website to be ready"
READY=0
for i in $(seq 1 40); do
  if docker compose exec -T web python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/', timeout=3).read()==b'ok' else 1)" >/dev/null 2>&1; then
    READY=1; break
  fi
  printf '.'; sleep 3
done
echo
if [ "$READY" -ne 1 ]; then
  echo "The website did not become ready. Last log lines:"
  docker compose logs --tail 40 web
  fail "Deployment failed. Your data is safe (see the backups folder)."
fi
echo "Website is up."

# 6. Database setup (safe to run every time) --------------------------------
step "Applying database migrations and starter data"
docker compose exec -T web python manage.py migrate --noinput
# The starter-data steps are optional: a failure here is shown as a warning and does not stop the deployment.
for seed in seed_brands seed_shipping; do
  docker compose exec -T web python manage.py "$seed" \
    || echo "WARNING: '$seed' failed or does not exist in this version of the project. Skipping it."
done

if [ "$DO_TEST" -eq 1 ]; then
  step "Running automated tests"
  docker compose exec -T web python manage.py test
fi

# 7. PostgreSQL access for DBeaver ------------------------------------------
step "Checking PostgreSQL access for DBeaver"
DB_USER="$(docker compose exec -T db printenv POSTGRES_USER | tr -d '\r')"
DB_NAME="$(docker compose exec -T db printenv POSTGRES_DB | tr -d '\r')"
docker compose exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME"
MAPPING="$(docker compose port db 5432 2>/dev/null || true)"
if [ -z "$MAPPING" ]; then
  echo
  echo "WARNING: PostgreSQL is not published to your computer, so DBeaver cannot connect."
  echo "Add this to the db service in docker-compose.yml, then run this script again:"
  echo '    ports:'
  echo '      - "127.0.0.1:5432:5432"'
else
  HOST_PORT="${MAPPING##*:}"
  if (echo > "/dev/tcp/127.0.0.1/$HOST_PORT") >/dev/null 2>&1; then
    echo "Port $HOST_PORT is open on this computer."
  else
    echo "WARNING: port $HOST_PORT is mapped but not answering yet."
  fi
  echo
  echo "DBeaver connection (PostgreSQL):"
  echo "  Host:      localhost"
  echo "  Port:      $HOST_PORT"
  echo "  Database:  $DB_NAME"
  echo "  Username:  $DB_USER"
  echo "  Password:  the POSTGRES_PASSWORD from your .env or docker-compose.yml"
fi

# 8. Summary ----------------------------------------------------------------
step "Containers"
docker compose ps
cat <<MSG

Deployment finished.
  Shop:   http://localhost:8000/
  Admin:  http://localhost:8000/admin/
  Logs:   docker compose logs -f web
  Stop:   docker compose down        (keeps your data; never add -v)
MSG
