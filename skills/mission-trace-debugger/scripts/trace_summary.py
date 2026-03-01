#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Dict, List


def _fetch_json(url: str, timeout_s: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError("non-object JSON response")
    return data


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _norm(s: Any, default: str) -> str:
    v = str(s).strip().lower() if s is not None else ""
    return v or default


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Summarize mission protocol trace")
    p.add_argument("--mission-id", required=True, help="Mission identifier")
    p.add_argument("--base-url", default="http://127.0.0.1:8023", help="Mission supervisor base URL")
    p.add_argument("--limit", type=int, default=200, help="Trace row limit")
    p.add_argument("--include-replayed", default="true", choices=["true", "false"], help="Include replayed rows")
    p.add_argument("--show-failures", type=int, default=5, help="Number of failure rows to print")
    p.add_argument("--timeout-s", type=float, default=6.0, help="HTTP timeout in seconds")
    return p


def main() -> int:
    args = build_parser().parse_args()
    include_replayed = args.include_replayed == "true"

    state_url = f"{args.base_url.rstrip('/')}/api/mission/{urllib.parse.quote(args.mission_id)}/state"
    trace_url = (
        f"{args.base_url.rstrip('/')}/api/mission/{urllib.parse.quote(args.mission_id)}/protocol-trace"
        f"?limit={max(1, int(args.limit))}&include_replayed={'true' if include_replayed else 'false'}"
    )

    try:
        state_payload = _fetch_json(state_url, timeout_s=args.timeout_s)
        trace_payload = _fetch_json(trace_url, timeout_s=args.timeout_s)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP error: {e.code} while fetching mission data\n")
        return 2
    except urllib.error.URLError as e:
        sys.stderr.write(f"Connection error: {e.reason}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"Failed to fetch mission data: {e}\n")
        return 2

    state_result = state_payload.get("result") if isinstance(state_payload.get("result"), dict) else {}
    mission_status = _norm(state_result.get("status"), "unknown")

    trace_result = trace_payload.get("result") if isinstance(trace_payload.get("result"), dict) else {}
    rows = [r for r in _as_list(trace_result.get("protocol_trace")) if isinstance(r, dict)]

    if not rows:
        print(f"Mission: {args.mission_id}")
        print(f"Mission status: {mission_status}")
        print("Trace rows: 0")
        return 0

    status_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    op_counts: Counter[str] = Counter()
    failing_rows: List[Dict[str, Any]] = []
    mcp_tools: Counter[str] = Counter()
    replayed_count = 0

    for row in rows:
        status = _norm(row.get("status"), "unknown")
        domain = _norm(row.get("domain"), "unknown")
        op = _norm(row.get("op"), "unknown")

        status_counts[status] += 1
        domain_counts[domain] += 1
        op_counts[f"{domain}:{op}"] += 1

        if bool(row.get("replayed")):
            replayed_count += 1

        trace = row.get("protocol_trace") if isinstance(row.get("protocol_trace"), dict) else {}
        mcp = trace.get("mcp") if isinstance(trace.get("mcp"), dict) else {}
        tool_name = str(mcp.get("tool") or mcp.get("name") or "").strip()
        if tool_name:
            mcp_tools[tool_name] += 1

        if status not in {"success", "ok", "completed"}:
            failing_rows.append(row)

    print(f"Mission: {args.mission_id}")
    print(f"Mission status: {mission_status}")
    print(f"Trace rows: {len(rows)}")
    print(f"Replayed rows: {replayed_count}")

    print("\nStatus counts:")
    for k, v in status_counts.most_common():
        print(f"- {k}: {v}")

    print("\nDomain counts:")
    for k, v in domain_counts.most_common():
        print(f"- {k}: {v}")

    print("\nTop operations:")
    for k, v in op_counts.most_common(10):
        print(f"- {k}: {v}")

    if mcp_tools:
        print("\nTop MCP tools:")
        for k, v in mcp_tools.most_common(10):
            print(f"- {k}: {v}")

    if failing_rows:
        print("\nFailure rows:")
        for row in failing_rows[: max(1, args.show_failures)]:
            print(
                "- ts={ts} domain={domain} op={op} status={status} command_id={cmd} correlation_id={cid}".format(
                    ts=row.get("ts"),
                    domain=row.get("domain"),
                    op=row.get("op"),
                    status=row.get("status"),
                    cmd=row.get("command_id"),
                    cid=row.get("correlation_id"),
                )
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
