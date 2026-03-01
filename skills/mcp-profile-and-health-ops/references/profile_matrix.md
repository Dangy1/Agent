# MCP Profile Matrix

## Profiles

- `suites-stdio`: FlexRIC suites over stdio.
- `suites-http`: FlexRIC suites over HTTP.
- `uav-utm-procedures-stdio`: procedure-first UAV/UTM tools.
- `uav-utm-strict-ops-stdio`: strict DSS/conformance/security tools.

## Presets

- `procedures` -> `uav-utm-procedures-stdio`
- `strict-ops` -> `uav-utm-strict-ops-stdio`

## Health Endpoints

- `GET http://127.0.0.1:2024/openapi.json`
- `GET http://127.0.0.1:8010/api/mcp/config`
- `GET http://127.0.0.1:8020/api/uav/sim/fleet`
- `GET http://127.0.0.1:8021/api/utm/sync` (Bearer token)
- `GET http://127.0.0.1:8022/api/network/mission/state`
- `GET http://127.0.0.1:8023/api/mission`
- `GET http://127.0.0.1:8024/api/dss/state`
- `GET http://127.0.0.1:8025/api/uss/state`
- `HEAD http://127.0.0.1:5173`
