# Multi-Agent A2A + MCP + LangGraph Supervision

This runbook describes how to structure your current stack as a multi-agent system with:

- `UTM/USS/DSS` agent responsibilities
- `UAV` agent responsibilities
- `Network/O-RAN` agent responsibilities
- Supervisor-driven agent-to-agent messaging
- Deterministic execution + task memory

## 1. Current repo mapping

You already have domain-specific agents:

- `backend/utm_agent` (UTM, regulatory checks, DSS artifacts)
- `backend/uav_agent` (route planning, simulator/control adapter, launch/step/hold/rth/land)
- `backend/network_agent` (network mission state)
- `backend/oran_agent` (FlexRIC/MCP tool bridge)
- `backend/dss_agent` (separated DSS API/tools/graph)
- `backend/uss_agent` (separated USS API/tools/graph)
- `backend/mission_supervisor_agent` (LangGraph supervisor + policy + dispatch)

LangGraph exposes all graphs in `backend/langgraph.json`.

## 2. Agent roles and ownership

Use strict domain boundaries:

- UTM/USS/DSS agent: authorization, conformance, subscription/intent lifecycle
- UAV agent: flight-control actions and UAV state transitions
- Network/O-RAN agent: slice/tc/kpm monitoring and policy-controlled tuning
- Mission supervisor: intent classification, planning, approvals, lock management, rollback

Only the supervisor should orchestrate cross-domain plans.

## 3. Agent-to-agent protocol (Google A2A standardized envelope)

Implemented in:

- `backend/mission_supervisor_agent/a2a_protocol.py`

Each dispatched command now emits an A2A JSON-RPC request (`message/send`) with a `Message` payload:

```json
{
  "protocol": "A2A",
  "version": "0.2.6",
  "jsonrpc": "2.0",
  "id": "a2a-...",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "a2a-...",
      "contextId": "mission-...",
      "taskId": "task-...",
      "role": "agent",
      "parts": [
        {"kind": "text", "text": "[mission_supervisor -> uav] actuate uav.launch"},
        {"kind": "data", "data": {"domain": "uav", "op": "launch", "params": {"uav_id": "uav-1"}}}
      ]
    }
  }
}
```

Legacy aliases (`message_id`, `correlation_id`, `mission_id`, `intent_type`) are still included for backward-compatible trace consumers.

## 4. MCP protocol bridging

Also implemented in:

- `backend/mission_supervisor_agent/a2a_protocol.py`

For each command, the supervisor generates an MCP invocation trace:

- `protocol`: `modelcontextprotocol.io/jsonrpc`
- `tool`: mapped tool name (for example `mcp_slice_apply_profile_and_verify`)
- `arguments`: normalized command params

Today this is traced and audited in the command bus; it can be used as the transport contract if you later switch from in-process calls to remote MCP endpoints.

## 5. Deterministic tools + task memory

Implemented in:

- `backend/mission_supervisor_agent/task_memory.py`
- `backend/mission_supervisor_agent/command_bus.py`

Rules:

- `observe` commands are replayable by default within mission scope
- `actuate` commands replay only when `params._idempotent=true`
- cached results are keyed by a command fingerprint (`domain/op/params`)

This avoids repeated non-deterministic tool execution while keeping actuation safe.

You can also store scoped mission facts via `TaskMemoryStore.set_fact/get_fact`.

## 6. LangGraph supervision flow

Main graph:

- `backend/mission_supervisor_agent/graph.py`

Flow summary:

1. Ingest request and classify intent/domain
2. Refresh UAV/UTM/Network snapshots
3. Build phase-specific plan
4. Run approval and policy gates
5. Acquire resource locks
6. Dispatch command via auditable command bus
7. Verify outcome, progress, or recover/rollback

This is your control-plane for cross-agent mission execution.

## 7. Split status: UTM + DSS + USS

The split is now implemented:

1. `utm_agent` remains policy/compliance authority.
2. `dss_agent` exposes DSS lifecycle routes:
`/api/dss/operational-intents`, `/api/dss/subscriptions`, `/api/dss/participants`, `/api/dss/notifications`.
3. `uss_agent` exposes USS-facing routes:
`/api/uss/intents/publish`, `/api/uss/subscriptions`, `/api/uss/notifications`.
4. Mission supervisor dispatch supports `domain=dss` and `domain=uss`.
5. Same A2A envelope and MCP trace contract are preserved.

## 8. Quick verification

Run tests:

```bash
python -m unittest backend/test/test_mission_supervisor_a2a_protocol.py
python -m unittest backend/test/test_mission_supervisor_command_bus_memory.py
```

Start stack and run a mission:

```bash
./bash.sh start
curl -sS -X POST http://127.0.0.1:8023/api/mission/start \
  -H "Content-Type: application/json" \
  -d '{"request_text":"Plan and launch UAV with network slice verification"}' | jq
```

Inspect mission state/events for command bus protocol traces.

Dedicated protocol-trace endpoint:

```bash
curl -sS "http://127.0.0.1:8023/api/mission/<mission_id>/protocol-trace?limit=200&include_replayed=true" | jq
```
