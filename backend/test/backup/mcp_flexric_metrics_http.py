#!/usr/bin/env python3
"""
mcp_flexric_metrics_http.py

FastMCP server exposing FlexRIC xApp SDK metrics via MCP tools,
served over Streamable HTTP (persistent server) using an ASGI app + uvicorn.

Requires in SAME folder:
  - xapp_sdk.py
  - _xapp_sdk.so

Run:
  python3 mcp_flexric_metrics_http.py

Env (.env):
  MCP_HOST=127.0.0.1
  MCP_PORT=8000
  MCP_PATH=/mcp

  FLEXRIC_NODE_INDEX=0
  FLEXRIC_ENABLE=mac,rlc,pdcp,gtp,slice
  FLEXRIC_INTERVAL=10
"""

import os
import logging
import threading
from datetime import datetime
from typing import Any, Dict, Callable, Optional

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP

# ---------- HTTP config ----------
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1").strip()
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp").strip()
if not MCP_PATH.startswith("/"):
    MCP_PATH = "/" + MCP_PATH

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-flexric-http")

# ---------- import xApp SDK (same folder) ----------
try:
    import xapp_sdk as ric
except Exception as e:
    raise RuntimeError(
        "Failed to import xapp_sdk. Run from the directory containing "
        "xapp_sdk.py and _xapp_sdk.so (or add it to PYTHONPATH)."
    ) from e

# ---------- FlexRIC config ----------
NODE_INDEX = int(os.getenv("FLEXRIC_NODE_INDEX", "0"))
ENABLE = {x.strip().lower() for x in os.getenv("FLEXRIC_ENABLE", "mac,rlc,pdcp,gtp,slice").split(",") if x.strip()}
INTERVAL_MS = int(os.getenv("FLEXRIC_INTERVAL", "10"))

mcp = FastMCP("flexric-metrics-http")

_LOCK = threading.Lock()
_RUNNING = False
_NODE = None
_HANDLES: Dict[str, Any] = {}

LATEST: Dict[str, Dict[str, Any]] = {
    "mac":   {"status": "init", "ts": None, "error": None, "data": None},
    "rlc":   {"status": "init", "ts": None, "error": None, "data": None},
    "pdcp":  {"status": "init", "ts": None, "error": None, "data": None},
    "gtp":   {"status": "init", "ts": None, "error": None, "data": None},
    "slice": {"status": "init", "ts": None, "error": None, "data": None},
}

# ---------- small "make JSON-safe" helper ----------
def to_jsonable(x: Any, depth: int = 3, max_list: int = 50) -> Any:
    if depth <= 0:
        return str(x)
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode(errors="ignore")
        except Exception:
            return str(x)
    if isinstance(x, dict):
        out = {}
        for k, v in list(x.items())[:200]:
            out[str(k)] = to_jsonable(v, depth - 1, max_list)
        return out
    if isinstance(x, (list, tuple)):
        return [to_jsonable(i, depth - 1, max_list) for i in list(x)[:max_list]]

    out = {}
    try:
        attrs = [a for a in dir(x) if not a.startswith("_")]
        for a in attrs[:200]:
            try:
                v = getattr(x, a)
            except Exception:
                continue
            if callable(v):
                continue
            out[a] = to_jsonable(v, depth - 1, max_list)
    except Exception:
        pass

    return out if out else str(x)

def _interval_enum():
    cand = f"Interval_ms_{INTERVAL_MS}"
    if hasattr(ric, cand):
        return getattr(ric, cand)
    for fallback in ["Interval_ms_10", "Interval_ms_5", "Interval_ms_1", "Interval_ms_100"]:
        if hasattr(ric, fallback):
            return getattr(ric, fallback)
    return None

def _store(sm: str, ind_obj: Any):
    with _LOCK:
        LATEST[sm]["status"] = "ok"
        LATEST[sm]["ts"] = datetime.now().isoformat()
        LATEST[sm]["error"] = None
        LATEST[sm]["data"] = to_jsonable(ind_obj)

class _BaseCb:
    SM = "unknown"
    def _ok(self, ind): _store(self.SM, ind)
    def _err(self, e):
        with _LOCK:
            LATEST[self.SM]["status"] = "error"
            LATEST[self.SM]["error"] = str(e)

class MACCallback(ric.mac_cb, _BaseCb):
    SM = "mac"
    def __init__(self): ric.mac_cb.__init__(self)
    def handle(self, ind):
        try: self._ok(ind)
        except Exception as e: self._err(e)

class RLCCallback(ric.rlc_cb, _BaseCb):
    SM = "rlc"
    def __init__(self): ric.rlc_cb.__init__(self)
    def handle(self, ind):
        try: self._ok(ind)
        except Exception as e: self._err(e)

class PDCPCallback(ric.pdcp_cb, _BaseCb):
    SM = "pdcp"
    def __init__(self): ric.pdcp_cb.__init__(self)
    def handle(self, ind):
        try: self._ok(ind)
        except Exception as e: self._err(e)

class GTPCallback(ric.gtp_cb, _BaseCb):
    SM = "gtp"
    def __init__(self): ric.gtp_cb.__init__(self)
    def handle(self, ind):
        try: self._ok(ind)
        except Exception as e: self._err(e)

class SLICECallback(ric.slice_cb, _BaseCb):
    SM = "slice"
    def __init__(self): ric.slice_cb.__init__(self)
    def handle(self, ind):
        try: self._ok(ind)
        except Exception as e: self._err(e)

def _ensure_started():
    global _RUNNING, _NODE
    with _LOCK:
        if _RUNNING:
            return

    log.info("FlexRIC init() ...")
    ric.init()

    conn = ric.conn_e2_nodes()
    if not conn or len(conn) == 0:
        raise RuntimeError("No E2 nodes connected (conn_e2_nodes() returned empty).")

    idx = NODE_INDEX if NODE_INDEX < len(conn) else 0
    _NODE = conn[idx].id

    interval = _interval_enum()
    if interval is None:
        raise RuntimeError("Could not find any Interval_ms_* enum in xapp_sdk.")

    log.info("Using node_index=%d interval=%s enable=%s", idx, str(interval), sorted(ENABLE))

    if "mac" in ENABLE:
        _HANDLES["mac"] = ric.report_mac_sm(_NODE, interval, MACCallback())
    if "rlc" in ENABLE:
        _HANDLES["rlc"] = ric.report_rlc_sm(_NODE, interval, RLCCallback())
    if "pdcp" in ENABLE:
        _HANDLES["pdcp"] = ric.report_pdcp_sm(_NODE, interval, PDCPCallback())
    if "gtp" in ENABLE:
        _HANDLES["gtp"] = ric.report_gtp_sm(_NODE, interval, GTPCallback())
    if "slice" in ENABLE:
        _HANDLES["slice"] = ric.report_slice_sm(_NODE, interval, SLICECallback())

    with _LOCK:
        _RUNNING = True

def _stop_all():
    global _RUNNING
    with _LOCK:
        if not _RUNNING:
            return
        handles = dict(_HANDLES)

    for sm, h in handles.items():
        try:
            if sm == "mac":   ric.rm_report_mac_sm(h)
            if sm == "rlc":   ric.rm_report_rlc_sm(h)
            if sm == "pdcp":  ric.rm_report_pdcp_sm(h)
            if sm == "gtp":   ric.rm_report_gtp_sm(h)
            if sm == "slice": ric.rm_report_slice_sm(h)
        except Exception as e:
            log.warning("rm_report_%s_sm failed: %s", sm, e)

    with _LOCK:
        _HANDLES.clear()
        _RUNNING = False

# ---------------- MCP tools ----------------
@mcp.tool()
def start() -> Dict[str, Any]:
    """Start FlexRIC subscriptions (idempotent)."""
    try:
        _ensure_started()
        return {"status": "success", "enabled": sorted(ENABLE), "node_index": NODE_INDEX}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@mcp.tool()
def stop() -> Dict[str, Any]:
    """Stop/unsubscribe."""
    try:
        _stop_all()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@mcp.tool()
def list_e2_nodes() -> Dict[str, Any]:
    """List connected E2 nodes."""
    try:
        ric.init()
        conn = ric.conn_e2_nodes()
        nodes = [{"index": i, "id_str": str(c.id)} for i, c in enumerate(conn)]
        return {"status": "success", "count": len(nodes), "nodes": nodes}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _get_latest(sm: str) -> Dict[str, Any]:
    with _LOCK:
        snap = dict(LATEST.get(sm, {}))
        running = _RUNNING
    if not snap:
        return {"status": "error", "error": f"Unknown SM '{sm}'"}
    return {"status": snap.get("status"), "ts": snap.get("ts"), "running": running, "error": snap.get("error"), "data": snap.get("data")}

@mcp.tool()
def get_mac_metrics() -> Dict[str, Any]:   return _get_latest("mac")
@mcp.tool()
def get_rlc_metrics() -> Dict[str, Any]:   return _get_latest("rlc")
@mcp.tool()
def get_pdcp_metrics() -> Dict[str, Any]:  return _get_latest("pdcp")
@mcp.tool()
def get_gtp_metrics() -> Dict[str, Any]:   return _get_latest("gtp")
@mcp.tool()
def get_slice_metrics() -> Dict[str, Any]: return _get_latest("slice")

@mcp.tool()
def health() -> Dict[str, Any]:
    """Basic health snapshot."""
    with _LOCK:
        return {
            "status": "success",
            "running": _RUNNING,
            "enabled": sorted(ENABLE),
            "subscribed": sorted(_HANDLES.keys()),
            "latest": {k: {"status": v["status"], "ts": v["ts"], "error": v["error"]} for k, v in LATEST.items()},
        }

# --------- ASGI export (version-robust) ----------
def _get_mcp_asgi_app(server: FastMCP) -> Callable:
    """
    Different mcp versions expose the ASGI app differently.
    Try common patterns.
    """
    # 1) attribute
    for attr in ("app", "asgi_app"):
        a = getattr(server, attr, None)
        if callable(a):
            return a

    # 2) method
    for meth in ("get_app", "create_app", "build_app"):
        fn = getattr(server, meth, None)
        if callable(fn):
            a = fn()
            if callable(a):
                return a

    raise RuntimeError(
        "Cannot find an ASGI app on FastMCP instance. "
        "Your mcp version may not support HTTP serving this way."
    )

if __name__ == "__main__":
    # Optional auto-start so metrics are populated
    try:
        _ensure_started()
        log.info("FlexRIC subscriptions started.")
    except Exception as e:
        log.warning("Auto-start failed (you can call start() later): %s", e)

    # Serve MCP over HTTP using uvicorn, mounting at MCP_PATH
    log.info("Starting MCP HTTP server at http://%s:%d%s", MCP_HOST, MCP_PORT, MCP_PATH)

    # These imports are here so you only need them for HTTP mode
    from fastapi import FastAPI
    import uvicorn

    api = FastAPI()
    mcp_app = _get_mcp_asgi_app(mcp)
    api.mount(MCP_PATH, mcp_app)

    uvicorn.run(
            api,
            host=MCP_HOST,
            port=MCP_PORT,
            workers=1,
            loop="asyncio",
            access_log=False,
            reload=False,
            log_level="info",
        )