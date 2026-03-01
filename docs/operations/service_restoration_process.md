# Service Restoration Process

## Objectives
Restore service within profile RTO while preserving safety controls.

## Steps
1. Enter degraded mode.
2. Disable risky paths and keep deterministic gates active.
3. Recover dependencies and re-run conformance smoke checks.
4. Exit degraded mode only after validation and approval.
