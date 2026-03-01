"""Small backend API layer for runtime MCP profile/server selection."""

from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "FastAPI API layer requires 'fastapi' and 'pydantic'. Install them to run oran_agent.api:app"
    ) from e

from .config.runtime_mcp import (
    apply_mcp_profile,
    clear_mcp_runtime_overrides,
    get_mcp_runtime_config,
    list_mcp_profiles,
    patch_mcp_runtime_overrides,
    runtime_snapshot_for_ui,
    set_mcp_runtime_overrides,
)
from .core import _MCP


class MCPConfigPayload(BaseModel):
    transport: Optional[str] = None
    server_cmd: Optional[str] = None
    server_args: Optional[str] = None
    server_name: Optional[str] = None
    http_url: Optional[str] = None
    http_auth_token: Optional[str] = None


class MCPProfilePayload(BaseModel):
    profile: str
    overrides: Optional[Dict[str, Any]] = None


app = FastAPI(title="AgentRIC O-RAN MCP Config API")


MCP_PRESETS: Dict[str, str] = {
    "procedures": "uav-utm-procedures-stdio",
    "strict-ops": "uav-utm-strict-ops-stdio",
}
_MCP_PRESET_ALIASES: Dict[str, str] = {
    "procedures": "procedures",
    "procedure": "procedures",
    "uav-utm-procedures": "procedures",
    "strict-ops": "strict-ops",
    "strict_ops": "strict-ops",
    "strictops": "strict-ops",
    "uav-utm-strict-ops": "strict-ops",
}


def _reload_bridge() -> None:
    _MCP.reset()


def _normalize_preset_name(raw: str) -> str:
    key = str(raw or "").strip().lower()
    return _MCP_PRESET_ALIASES.get(key, key)


def _current_active_profile(config: Dict[str, Any], profiles: Dict[str, Dict[str, Any]]) -> str:
    ranked_profiles = sorted(
        [(name, cfg) for name, cfg in profiles.items() if isinstance(cfg, dict)],
        key=lambda item: len(item[1]),
        reverse=True,
    )
    for profile_name, profile_cfg in ranked_profiles:
        matched = True
        for key, value in profile_cfg.items():
            if key not in config:
                matched = False
                break
            if str(config.get(key)) != str(value):
                matched = False
                break
        if matched:
            return profile_name
    return ""


@app.get("/api/mcp/config")
def get_mcp_config() -> Dict[str, Any]:
    cfg = get_mcp_runtime_config()
    profiles = list_mcp_profiles()
    active_profile = _current_active_profile(cfg, profiles)
    active_preset = ""
    for preset_name, preset_profile in MCP_PRESETS.items():
        if preset_profile == active_profile:
            active_preset = preset_name
            break
    return {
        "status": "success",
        "config": cfg,
        "ui": runtime_snapshot_for_ui(),
        "profiles": profiles,
        "presets": MCP_PRESETS,
        "active_profile": active_profile or None,
        "active_preset": active_preset or None,
    }


@app.put("/api/mcp/config")
def replace_mcp_config(payload: MCPConfigPayload) -> Dict[str, Any]:
    cfg = set_mcp_runtime_overrides(payload.model_dump(exclude_none=True))
    _reload_bridge()
    return {"status": "success", "config": cfg, "ui": runtime_snapshot_for_ui()}


@app.patch("/api/mcp/config")
def update_mcp_config(payload: MCPConfigPayload) -> Dict[str, Any]:
    cfg = patch_mcp_runtime_overrides(payload.model_dump(exclude_none=True))
    _reload_bridge()
    return {"status": "success", "config": cfg, "ui": runtime_snapshot_for_ui()}


@app.delete("/api/mcp/config")
def reset_mcp_config() -> Dict[str, Any]:
    cfg = clear_mcp_runtime_overrides()
    _reload_bridge()
    return {"status": "success", "config": cfg, "ui": runtime_snapshot_for_ui()}


@app.get("/api/mcp/profiles")
def get_mcp_profiles() -> Dict[str, Any]:
    return {"status": "success", "profiles": list_mcp_profiles()}


@app.post("/api/mcp/profile")
def select_mcp_profile(payload: MCPProfilePayload) -> Dict[str, Any]:
    try:
        cfg = apply_mcp_profile(payload.profile, payload.overrides)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _reload_bridge()
    return {"status": "success", "profile": payload.profile, "config": cfg, "ui": runtime_snapshot_for_ui()}


@app.get("/api/mcp/presets")
def get_mcp_presets() -> Dict[str, Any]:
    cfg = get_mcp_runtime_config()
    profiles = list_mcp_profiles()
    active_profile = _current_active_profile(cfg, profiles)
    active_preset = ""
    for preset_name, preset_profile in MCP_PRESETS.items():
        if preset_profile == active_profile:
            active_preset = preset_name
            break
    return {
        "status": "success",
        "presets": MCP_PRESETS,
        "active_profile": active_profile or None,
        "active_preset": active_preset or None,
        "ui": runtime_snapshot_for_ui(),
    }


@app.post("/api/mcp/preset/{preset}")
def select_mcp_preset(preset: str) -> Dict[str, Any]:
    preset_name = _normalize_preset_name(preset)
    profile_name = MCP_PRESETS.get(preset_name)
    if not profile_name:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown MCP preset '{preset}'. Available: {', '.join(sorted(MCP_PRESETS.keys()))}",
        )
    try:
        cfg = apply_mcp_profile(profile_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _reload_bridge()
    return {
        "status": "success",
        "preset": preset_name,
        "profile": profile_name,
        "config": cfg,
        "ui": runtime_snapshot_for_ui(),
    }
