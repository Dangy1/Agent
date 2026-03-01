# Ollama Skills Quick Commands

This project can run these skills with local tooling and local models (Ollama). No OpenAI API is required for the commands below.

Run from repo root: `/home/dang/agent_test`

1. `uav-utm-procedures-automation`
```bash
./skills/uav-utm-procedures-automation/scripts/procedure_preflight.sh
```

2. `uav-utm-strict-ops-automation`
```bash
./skills/uav-utm-strict-ops-automation/scripts/strict_ops_smoke.sh
```

3. `mcp-profile-and-health-ops`
```bash
./skills/mcp-profile-and-health-ops/scripts/profile_health_check.sh --preset procedures
```

4. `mission-trace-debugger`
```bash
./skills/mission-trace-debugger/scripts/trace_summary.py --mission-id <mission_id>
```

5. `cross-domain-network-policy`
```bash
./skills/cross-domain-network-policy/scripts/network_policy_guard.sh --mode balanced --coverage-target 95 --max-latency-ms 60 --max-high-risk 1
```

Optional: keep `agents/openai.yaml` files as metadata only for future OpenAI/Codex switching; Ollama flows can ignore them.
