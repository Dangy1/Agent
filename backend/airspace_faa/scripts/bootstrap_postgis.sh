#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy .env.example first."
  exit 1
fi

echo "Starting PostGIS container..."
docker compose \
  --env-file "${ENV_FILE}" \
  -f "${ROOT_DIR}/docker-compose.postgis.yml" \
  up -d

echo "Waiting for PostgreSQL readiness..."
for i in $(seq 1 60); do
  if docker compose --env-file "${ENV_FILE}" -f "${ROOT_DIR}/docker-compose.postgis.yml" ps | grep -q "healthy"; then
    echo "PostGIS is healthy."
    break
  fi
  sleep 2
  if [[ "${i}" -eq 60 ]]; then
    echo "Timed out waiting for PostGIS health."
    exit 1
  fi
done

echo "PostGIS bootstrap complete."
