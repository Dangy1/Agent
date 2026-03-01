# Agent-System Skills (LangGraph/LangChain)

This project includes an internal skill layer for your own agent system.
These are not Codex skills and do not depend on `$CODEX_HOME`.

## What "skill" means here

A skill is a reusable mission recipe:

- request trigger patterns
- domain hint (`uav_mission`, `cross_domain`, `dss_ops`, `uss_ops`)
- deterministic plan template (`domain`, `op`, `params`, `resource_keys`)

The mission supervisor uses skill matching to select and render plan steps before execution.

## Implementation

- Skill catalog: `backend/mission_supervisor_agent/skill_catalog.py`
- Matching + plan rendering: `backend/mission_supervisor_agent/planner.py`
- Skill selection during intent parsing: `backend/mission_supervisor_agent/graph.py`
- API exposure:
  - `GET /api/mission/skills`
  - `POST /api/mission/skills/match`

## Built-in skills

- `uav_utm_standard_mission`
- `cross_domain_network_assured`
- `dss_conflict_and_subscription`
- `uss_publication_and_watch`

## Add your own skill

1. Edit `backend/mission_supervisor_agent/skill_catalog.py`.
2. Add one new object to `SKILL_CATALOG`:
   - `skill_id`, `name`, `description`
   - `domain_hint`
   - `triggers` (keyword list)
   - `plan_template` (plan steps)
3. Use placeholders in params/resource keys:
   - `{uav_id}`, `{route_id}`, `{airspace_segment}`
4. Restart backend service.

## Example test

```bash
curl -sS -X POST http://127.0.0.1:8023/api/mission/skills/match \
  -H "Content-Type: application/json" \
  -d '{"request_text":"publish operational intent from uss and watch notifications"}' | jq
```

Then run a mission with that request text via `/api/mission/start` and inspect:

```bash
curl -sS http://127.0.0.1:8023/api/mission/<mission_id>/state | jq
curl -sS "http://127.0.0.1:8023/api/mission/<mission_id>/protocol-trace?limit=200" | jq
```
