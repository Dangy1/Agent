---
name: mission-trace-debugger
description: Diagnose mission execution failures by collecting and analyzing mission state, events, and protocol-trace records from the mission supervisor API. Use when requests mention failed/partial missions, replayed commands, A2A/MCP trace inspection, or evidence-driven root-cause analysis.
---

# Mission Trace Debugger

## Overview
Triage mission failures using deterministic trace extraction and summary output. Focus on protocol-trace and command-level status to identify where execution diverged.

## Workflow

1. Capture mission state.
2. Pull protocol trace (include and exclude replayed entries as needed).
3. Summarize failures by domain/op/status.
4. Return concrete next action for the failing domain.

## Quick Start
From repo root:

```bash
./skills/mission-trace-debugger/scripts/trace_summary.py --mission-id <mission_id>
```

Useful variants:

```bash
./skills/mission-trace-debugger/scripts/trace_summary.py --mission-id <mission_id> --include-replayed false
./skills/mission-trace-debugger/scripts/trace_summary.py --mission-id <mission_id> --limit 500 --show-failures 10
```

## Required Evidence
Always include:

1. Mission status from `/api/mission/<id>/state`.
2. Trace count and replayed count.
3. Failing domain/op/status tuples.
4. Top failing MCP tools (if present).

## Triage Heuristics

- If failures cluster in `utm` and mention conflict/geofence, route through replan flow.
- If failures cluster in `network`, inspect `networkKpis` and optimization mode.
- If failures cluster in `oran/mcp`, verify profile and MCP runtime config.

## References

- `references/trace_triage.md`: triage order and failure taxonomy.
- `backend/mission_supervisor_agent/api.py`: mission APIs.
- `backend/mission_supervisor_agent/runtime.py`: protocol trace row fields.
