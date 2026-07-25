#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required: https://docs.docker.com/engine/install/" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Engine is not reachable. Start Docker and retry." >&2
  exit 1
fi

VERSION=${1:-}
if [ -n "$VERSION" ]; then
  VERSION=${VERSION#v}
  case "$VERSION" in
    *[!0-9A-Za-z.+-]*|"")
      echo "Invalid version: $VERSION" >&2
      exit 1
      ;;
  esac
fi

DATA_DIR="$SCRIPT_DIR/data"
mkdir -p "$DATA_DIR"
chmod 0700 "$DATA_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
fi

TMP_ENV=".env.tmp.$$"
awk -v data_dir="$DATA_DIR" -v version="$VERSION" '
  BEGIN { saw_data = 0; saw_version = 0 }
  /^ASTRA_DATA_DIR=/ { print "ASTRA_DATA_DIR=" data_dir; saw_data = 1; next }
  /^ASTRA_VERSION=/ {
    if (version != "") print "ASTRA_VERSION=" version
    else print
    saw_version = 1
    next
  }
  { print }
  END {
    if (!saw_data) print "ASTRA_DATA_DIR=" data_dir
    if (!saw_version && version != "") print "ASTRA_VERSION=" version
  }
' .env > "$TMP_ENV"
mv "$TMP_ENV" .env
chmod 0600 .env

docker compose pull
docker compose up -d

echo "Astra is starting at http://127.0.0.1:$(awk -F= '/^ASTRA_PORT=/{print $2}' .env | tail -1)"
echo "Run 'docker compose ps' to inspect readiness."
