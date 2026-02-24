#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

PYTHONPATH=backend python -m unittest \
  backend/test/test_mission_supervisor_policy.py \
  backend/test/test_mission_supervisor_planner.py \
  -v

