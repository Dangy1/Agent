#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.postgis-source.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy .env.example first."
  exit 1
fi

if [[ ! -e "${ROOT_DIR}/vendor/postgis/.git" ]]; then
  echo "Missing postgis submodule at ${ROOT_DIR}/vendor/postgis"
  echo "Run: git submodule update --init --depth 1 backend/airspace_faa/vendor/postgis"
  exit 1
fi

echo "Building source-level PostGIS image..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build postgis-source

echo "Starting source-level PostGIS container..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d postgis-source

echo "Waiting for PostgreSQL readiness..."
for i in $(seq 1 90); do
  if docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps | grep -q "healthy"; then
    echo "Source-level PostGIS is healthy."
    break
  fi
  sleep 2
  if [[ "${i}" -eq 90 ]]; then
    echo "Timed out waiting for source-level PostGIS health."
    exit 1
  fi
done

echo "Source-level PostGIS bootstrap complete."
