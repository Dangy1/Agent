#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ACTION="${DEFAULT_ACTION:-once}"
ACTION="${1:-$DEFAULT_ACTION}"

exec "$ROOT_DIR/scripts/bootstrap_all.sh" "$ACTION"
