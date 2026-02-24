#!/usr/bin/env python3
"""
mcp_flexric_metrics.py  (STDIO MCP server)

Fixes the #1 stdio MCP failure mode with FlexRIC:
- FlexRIC prints native logs to stdout (fd=1), which corrupts MCP JSON-RPC framing.
- This server redirects C-level stdout -> stderr while calling FlexRIC APIs.

Also:
- DOES NOT auto-start FlexRIC subscriptions at boot.
  (So ListTools works even if E2 nodes are not connected yet.)

Environment:
  FLEXRIC_INTERVAL=10
  FLEXRIC_NODE_INDEX=0
  FLEXRIC_ENABLE=mac,rlc,pdcp,gtp,slice
  FLEXRIC_DUMP_DEPTH=3
  FLEXRIC_MAX_LIST=50
"""

import os
import sys
import json
import logging
import threading
import contextlib
from datetime import datetime
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

# ---------- logging to STDERR (important: keep stdout clean for MCP JSON-RPC) ----------
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-flexric-metrics")

# ---------- import xApp SDK ----------
try:
    import xapp_sdk as ric
except Exception as e:
    raise RuntimeError(
        "Failed to import xapp_sdk. Ensure you run from the directory containing "
        "xapp_sdk.py and _xapp_sdk.so OR add that directory to PYTHONPATH."
    ) from e

# ---------- config ----------
NODE_INDEX = int(os.getenv("FLEXRIC_NODE_INDEX", "0"))
ENABLE = {x.strip().lower() for x in os.getenv("FLEXRIC_ENABLE", "mac,rlc,pdcp,gtp,slice").split(",") if x.strip()}
DUMP_DEPTH = int(os.getenv("FLEXRIC_DUMP_DEPTH", "3"))
MAX_LIST = int(os.getenv("FLEXRIC_MAX_LIST", "50"))
INTERVAL_MS = int(os.getenv("FLEXRIC_INTERVAL", "10"))

mcp = FastMCP("flexric-metrics")

_LOCK = threading.Lock()
_RUNNING = False
_NODE = None
_HANDLES: Dict[str, Any] = {}

LATEST: Dict[str, Dict[str, Any]] = {
    "mac":   {"status": "init", "ts": None, "node": None, "raw": None, "summary": None, "error": None},
    "rlc":   {"status": "init", "ts": None, "node": None, "raw": None, "summary": None, "error": None},
    "pdcp":  {"status": "init", "ts": None, "node": None, "raw": None, "summary": None, "error": None},
    "gtp":   {"status": "init", "ts": None, "node": None, "raw": None, "summary": None, "error": None},
    "slice": {"status": "init", "ts": None, "node": None, "raw": None, "summary": None, "error": None},
}


# ============================================================
# CRITICAL: keep stdout clean for stdio MCP JSON-RPC
# Redirect C-level stdout (fd=1) to stderr (fd=2) while calling FlexRIC
# ============================================================
@contextlib.contextmanager
def redirect_c_stdout_to_stderr():
    saved = os.dup(1)
    try:
        os.dup2(2, 1)   # stdout -> stderr (captures C printf)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)


# ============================================================
# SWIG -> dict converter
# ============================================================
def _safe_scalar(x: Any) -> Any:
    try:
        if x is None or isinstance(x, (int, float, str, bool)):
            return x
        return str(x)
    except Exception:
        return "<unprintable>"


def swig_to_py(x: Any, depth: int = DUMP_DEPTH, max_list: int = MAX_LIST) -> Any:
    if depth <= 0:
        return _safe_scalar(x)

    if x is None or isinstance(x, (int, float, str, bool)):
        return x

    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode(errors="ignore")
        except Exception:
            return str(x)

    if isinstance(x, (list, tuple)):
        return [swig_to_py(i, depth - 1, max_list) for i in x[:max_list]]

    if hasattr(x, "__len__") and hasattr(x, "__getitem__"):
        try:
            n = len(x)
            return [swig_to_py(x[i], depth - 1, max_list) for i in range(min(n, max_list))]
        except Exception:
            pass

    out = {}
    attrs = [a for a in dir(x) if not a.startswith("_")]
    for a in attrs[:200]:
        try:
            v = getattr(x, a)
        except Exception:
            continue
        if callable(v):
            continue
        try:
            out[a] = swig_to_py(v, depth - 1, max_list)
        except Exception:
            out[a] = _safe_scalar(v)

    return out if out else _safe_scalar(x)


# ============================================================
# Summaries (best-effort)
# ============================================================
def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _summarize_mac(raw: dict) -> dict:
    hdr = _pick(raw, "hdr", "ind_hdr", "header", default={}) if isinstance(raw, dict) else {}
    msg = _pick(raw, "msg", "ind_msg", "message", default={}) if isinstance(raw, dict) else {}
    data = _pick(msg, "data", "ind_data", default=msg) if isinstance(msg, dict) else {}

    summary = {
        "tstamp": _pick(raw, "tstamp", default=None),
        "hdr_keys": list(hdr.keys())[:20] if isinstance(hdr, dict) else None,
        "msg_keys": list(msg.keys())[:20] if isinstance(msg, dict) else None,
    }

    ue_stats = None
    if isinstance(data, dict):
        for cand in ["ue_stats", "ues", "ue", "ue_lst", "ue_list", "mac_ue_stats", "mac_ue_stats_lst"]:
            if cand in data:
                ue_stats = data[cand]
                break

    if isinstance(ue_stats, list):
        compact = []
        for u in ue_stats[:10]:
            if isinstance(u, dict):
                compact.append({
                    "rnti": _pick(u, "rnti", "rnti_hex", default=None),
                    "dl": _pick(u, "dl", "dl_bytes", "dl_thr", default=None),
                    "ul": _pick(u, "ul", "ul_bytes", "ul_thr", default=None),
                    "cqi": _pick(u, "cqi", default=None),
                    "mcs": _pick(u, "mcs", default=None),
                })
            else:
                compact.append(str(u))
        summary["ue_stats_sample"] = compact
        summary["ue_stats_count"] = len(ue_stats)

    return summary


def _summarize_slice(raw: dict) -> dict:
    summary = {
        "tstamp": _pick(raw, "tstamp", default=None),
        "keys": list(raw.keys())[:40] if isinstance(raw, dict) else None,
    }
    ss = _pick(raw, "slice_stats", default=None)
    uess = _pick(raw, "ue_slice_stats", default=None)

    if isinstance(ss, dict):
        dl = _pick(ss, "dl", default=None)
        if isinstance(dl, dict):
            summary["dl_len_slices"] = _pick(dl, "len_slices", default=None)
            summary["dl_sched_name"] = _pick(dl, "sched_name", default=None)

    if isinstance(uess, dict):
        summary["ue_len"] = _pick(uess, "len_ue_slice", default=None)

    return summary


def _summarize_generic(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {"type": str(type(raw))}
    return {"keys": list(raw.keys())[:60], "tstamp": _pick(raw, "tstamp", default=None)}


def _translate(sm: str, ind_obj: Any) -> Dict[str, Any]:
    raw = swig_to_py(ind_obj, depth=DUMP_DEPTH, max_list=MAX_LIST)
    if sm == "mac":
        summary = _summarize_mac(raw if isinstance(raw, dict) else {})
    elif sm == "slice":
        summary = _summarize_slice(raw if isinstance(raw, dict) else {})
    else:
        summary = _summarize_generic(raw if isinstance(raw, dict) else {})
    return {"raw": raw, "summary": summary}


# ============================================================
# Callbacks
# ============================================================
class _BaseCb:
    SM = "unknown"
    def _store(self, payload: Dict[str, Any]):
        with _LOCK:
            LATEST[self.SM]["status"] = "ok"
            LATEST[self.SM]["ts"] = datetime.now().isoformat()
            LATEST[self.SM]["node"] = str(_NODE) if _NODE is not None else None
            LATEST[self.SM]["raw"] = payload["raw"]
            LATEST[self.SM]["summary"] = payload["summary"]
            LATEST[self.SM]["error"] = None


class MACCallback(ric.mac_cb, _BaseCb):
    SM = "mac"
    def __init__(self): ric.mac_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("mac", ind))
        except Exception as e:
            with _LOCK:
                LATEST["mac"]["status"] = "error"
                LATEST["mac"]["error"] = str(e)


class RLCCallback(ric.rlc_cb, _BaseCb):
    SM = "rlc"
    def __init__(self): ric.rlc_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("rlc", ind))
        except Exception as e:
            with _LOCK:
                LATEST["rlc"]["status"] = "error"
                LATEST["rlc"]["error"] = str(e)


class PDCPCallback(ric.pdcp_cb, _BaseCb):
    SM = "pdcp"
    def __init__(self): ric.pdcp_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("pdcp", ind))
        except Exception as e:
            with _LOCK:
                LATEST["pdcp"]["status"] = "error"
                LATEST["pdcp"]["error"] = str(e)


class GTPCallback(ric.gtp_cb, _BaseCb):
    SM = "gtp"
    def __init__(self): ric.gtp_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("gtp", ind))
        except Exception as e:
            with _LOCK:
                LATEST["gtp"]["status"] = "error"
                LATEST["gtp"]["error"] = str(e)


class SLICECallback(ric.slice_cb, _BaseCb):
    SM = "slice"
    def __init__(self): ric.slice_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("slice", ind))
        except Exception as e:
            with _LOCK:
                LATEST["slice"]["status"] = "error"
                LATEST["slice"]["error"] = str(e)


# ============================================================
# FlexRIC helpers
# ============================================================
def _interval_enum():
    cand = f"Interval_ms_{INTERVAL_MS}"
    if hasattr(ric, cand):
        return getattr(ric, cand)
    for fallback in ["Interval_ms_10", "Interval_ms_5", "Interval_ms_1", "Interval_ms_100"]:
        if hasattr(ric, fallback):
            return getattr(ric, fallback)
    return None


def _ensure_started():
    """
    Initializes FlexRIC + subscribes, but redirects C stdout -> stderr so MCP stdout remains JSON-only.
    """
    global _RUNNING, _NODE
    with _LOCK:
        if _RUNNING:
            return

    with redirect_c_stdout_to_stderr():
        logger.info("Initializing FlexRIC Python SDK...")
        ric.init()

        conn = ric.conn_e2_nodes()
        if not conn or len(conn) == 0:
            raise RuntimeError("No E2 nodes connected (conn_e2_nodes() returned empty).")

        idx = NODE_INDEX if NODE_INDEX < len(conn) else 0
        _NODE = conn[idx].id

        interval = _interval_enum()
        if interval is None:
            raise RuntimeError("Could not find any Interval_ms_* enum in xapp_sdk.")

        logger.info("Using E2 node index=%d interval=%s enable=%s", idx, str(interval), sorted(ENABLE))

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

    with redirect_c_stdout_to_stderr():
        for sm, h in handles.items():
            try:
                if sm == "mac":
                    ric.rm_report_mac_sm(h)
                elif sm == "rlc":
                    ric.rm_report_rlc_sm(h)
                elif sm == "pdcp":
                    ric.rm_report_pdcp_sm(h)
                elif sm == "gtp":
                    ric.rm_report_gtp_sm(h)
                elif sm == "slice":
                    ric.rm_report_slice_sm(h)
            except Exception as e:
                logger.warning("Failed to rm_report_%s_sm: %s", sm, e)

    with _LOCK:
        _HANDLES.clear()
        _RUNNING = False


# ============================================================
# MCP tools
# ============================================================
@mcp.tool()
def start() -> Dict[str, Any]:
    """Initialize FlexRIC SDK and subscribe to enabled service models."""
    try:
        _ensure_started()
        return {"status": "success", "node_index": NODE_INDEX, "enabled": sorted(ENABLE)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def stop() -> Dict[str, Any]:
    """Unsubscribe all and stop local subscriptions."""
    try:
        _stop_all()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_e2_nodes() -> Dict[str, Any]:
    """List connected E2 nodes (IDs)."""
    try:
        with redirect_c_stdout_to_stderr():
            ric.init()
            conn = ric.conn_e2_nodes()
        nodes = [{"index": i, "id_str": str(c.id)} for i, c in enumerate(conn)]
        return {"status": "success", "count": len(nodes), "nodes": nodes}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _get_latest(sm: str, mode: str) -> Dict[str, Any]:
    with _LOCK:
        snap = dict(LATEST.get(sm, {}))
        running = _RUNNING
    if not snap:
        return {"status": "error", "error": f"Unknown SM '{sm}'."}
    out = {"status": snap.get("status"), "ts": snap.get("ts"), "node": snap.get("node"), "running": running, "error": snap.get("error")}
    if mode == "raw":
        out["raw"] = snap.get("raw")
    else:
        out["summary"] = snap.get("summary")
    return out


@mcp.tool()
def get_mac_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest MAC metrics. mode: 'summary' or 'raw'."""
    return _get_latest("mac", mode)


@mcp.tool()
def get_rlc_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest RLC metrics. mode: 'summary' or 'raw'."""
    return _get_latest("rlc", mode)


@mcp.tool()
def get_pdcp_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest PDCP metrics. mode: 'summary' or 'raw'."""
    return _get_latest("pdcp", mode)


@mcp.tool()
def get_gtp_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest GTP metrics. mode: 'summary' or 'raw'."""
    return _get_latest("gtp", mode)


@mcp.tool()
def get_slice_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest SLICE metrics. mode: 'summary' or 'raw'."""
    return _get_latest("slice", mode)


@mcp.tool()
def health() -> Dict[str, Any]:
    """Quick status of subscriptions + whether we are receiving indications."""
    with _LOCK:
        latest = {k: {"status": v["status"], "ts": v["ts"], "error": v["error"]} for k, v in LATEST.items()}
        return {
            "status": "success",
            "running": _RUNNING,
            "enabled": sorted(ENABLE),
            "subscribed": sorted(_HANDLES.keys()),
            "latest": latest,
        }


# ============================================================
# main (NO AUTOSTART!)
# ============================================================
if __name__ == "__main__":
    # IMPORTANT for stdio MCP:
    # - Do NOT call _ensure_started() here (may print native logs and/or fail if E2 not ready).
    # - Let the client list tools cleanly; call start() later when you want subscriptions.
    mcp.run()