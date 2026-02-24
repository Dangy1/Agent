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


def _reload_bridge() -> None:
    _MCP.reset()


@app.get("/api/mcp/config")
def get_mcp_config() -> Dict[str, Any]:
    return {
        "status": "success",
        "config": get_mcp_runtime_config(),
        "ui": runtime_snapshot_for_ui(),
        "profiles": list_mcp_profiles(),
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
