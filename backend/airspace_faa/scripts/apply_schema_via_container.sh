#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.postgis-source.yml"
SERVICE="postgis-source"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy .env.example first."
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

POSTGRES_DB="${POSTGRES_DB:-faa_airspace}"
POSTGRES_USER="${POSTGRES_USER:-faa}"

echo "Applying 001_airspace_core.sql via psql..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T "${SERVICE}" \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -f /docker-entrypoint-initdb.d/001_airspace_core.sql

echo "Applying 002_ingest_pipeline.sql via psql..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T "${SERVICE}" \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -f /docker-entrypoint-initdb.d/002_ingest_pipeline.sql

echo "Schema apply complete."
