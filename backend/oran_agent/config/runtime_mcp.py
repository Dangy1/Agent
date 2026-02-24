import json
import shlex
from typing import Any, Dict, Optional

from .settings import (
    MCP_HTTP_AUTH_TOKEN,
    MCP_HTTP_URL,
    MCP_SERVER_ARGS,
    MCP_SERVER_CMD,
    MCP_SERVER_NAME,
    MCP_TRANSPORT,
)

_OVERRIDES: Dict[str, Any] = {}

MCP_PROFILES: Dict[str, Dict[str, Any]] = {
    "suites-stdio": {
        "transport": "stdio",
    },
    "suites-http": {
        "transport": "http",
    },
}


def _normalize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in (
        "transport",
        "server_cmd",
        "server_args",
        "server_name",
        "http_url",
        "http_auth_token",
    ):
        if key in payload and payload[key] is not None:
            out[key] = str(payload[key]).strip() if key != "transport" else str(payload[key]).strip().lower()
    return out


def get_mcp_runtime_config() -> Dict[str, Any]:
    cfg = {
        "transport": MCP_TRANSPORT,
        "server_cmd": MCP_SERVER_CMD,
        "server_args": MCP_SERVER_ARGS,
        "server_name": MCP_SERVER_NAME,
        "http_url": MCP_HTTP_URL,
        "http_auth_token": MCP_HTTP_AUTH_TOKEN,
    }
    cfg.update(_OVERRIDES)
    return cfg


def set_mcp_runtime_overrides(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _OVERRIDES.clear()
    _OVERRIDES.update(_normalize_payload(payload))
    return get_mcp_runtime_config()


def patch_mcp_runtime_overrides(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _OVERRIDES.update(_normalize_payload(payload))
    return get_mcp_runtime_config()


def clear_mcp_runtime_overrides() -> Dict[str, Any]:
    _OVERRIDES.clear()
    return get_mcp_runtime_config()


def list_mcp_profiles() -> Dict[str, Dict[str, Any]]:
    return MCP_PROFILES.copy()


def apply_mcp_profile(profile: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if profile not in MCP_PROFILES:
        raise ValueError(f"Unknown MCP profile '{profile}'.")
    merged = dict(MCP_PROFILES[profile])
    if isinstance(extra, dict):
        merged.update(extra)
    return set_mcp_runtime_overrides(merged)


def build_server_cmd_list() -> list[str]:
    cfg = get_mcp_runtime_config()
    cmd = [str(cfg.get("server_cmd", "")).strip()]
    args = str(cfg.get("server_args", "") or "").strip()
    if args:
        cmd += shlex.split(args)
    return cmd


def runtime_snapshot_for_ui() -> Dict[str, Any]:
    cfg = get_mcp_runtime_config()
    return {
        "transport": cfg["transport"],
        "http": {"url": cfg["http_url"], "has_auth_token": bool(cfg["http_auth_token"])},
        "spawn": {"cmd": cfg["server_cmd"], "args": cfg["server_args"], "name": cfg["server_name"]},
    }


def parse_json_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            return decoded
    return {}
