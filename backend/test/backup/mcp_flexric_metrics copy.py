#!/usr/bin/env python3
"""
mcp_flexric_metrics.py

A **simple MCP server** (FastMCP) that exposes FlexRIC Python xApp SDK metrics
(MAC / RLC / PDCP / GTP / SLICE) via tools.

Key features:
- Works with your existing SWIG module: xapp_sdk.py + _xapp_sdk.so
- Starts FlexRIC SDK (ric.init), connects E2 nodes, subscribes to SM indications
- Stores latest indication per SM in memory
- Provides tools like: list_e2_nodes(), get_mac_metrics(), get_slice_metrics(), ...
- Includes a robust SWIG -> dict converter (best-effort JSON serialization)
- Includes a tiny “message translation” layer:
  - raw: full SWIG-to-dict dump (depth-limited)
  - summary: lightweight extracted fields (best-effort, safe fallbacks)

Run:
  python3 mcp_flexric_metrics.py
or (for inspector/dev):
  mcp dev mcp_flexric_metrics.py

Environment:
  FLEXRIC_INTERVAL=10           # ms, best-effort mapping
  FLEXRIC_NODE_INDEX=0          # pick conn_e2_nodes()[idx]
  FLEXRIC_ENABLE=mac,rlc,pdcp,gtp,slice   # comma list
  FLEXRIC_DUMP_DEPTH=3
  FLEXRIC_MAX_LIST=50
"""

import os
import time
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional, List

from mcp.server.fastmcp import FastMCP

# --------- logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-flexric-metrics")

# --------- import xApp SDK ----------
try:
    import xapp_sdk as ric
except Exception as e:
    raise RuntimeError(
        "Failed to import xapp_sdk. Ensure you run from the directory containing "
        "xapp_sdk.py and _xapp_sdk.so OR add that directory to PYTHONPATH."
    ) from e

# --------- config ----------
NODE_INDEX = int(os.getenv("FLEXRIC_NODE_INDEX", "0"))
ENABLE = {x.strip().lower() for x in os.getenv("FLEXRIC_ENABLE", "mac,rlc,pdcp,gtp,slice").split(",") if x.strip()}
DUMP_DEPTH = int(os.getenv("FLEXRIC_DUMP_DEPTH", "3"))
MAX_LIST = int(os.getenv("FLEXRIC_MAX_LIST", "50"))
INTERVAL_MS = int(os.getenv("FLEXRIC_INTERVAL", "10"))  # best-effort mapping

mcp = FastMCP("flexric-metrics")

# --------- globals ----------
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

# =========================
# SWIG -> dict converter
# =========================
def swig_to_py(x: Any, depth: int = DUMP_DEPTH, max_list: int = MAX_LIST) -> Any:
    """
    Best-effort conversion of SWIG structs into JSON-serializable Python.
    Depth-limited to avoid massive dumps.
    """
    if depth <= 0:
        return _safe_scalar(x)

    if x is None or isinstance(x, (int, float, str, bool)):
        return x

    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode(errors="ignore")
        except Exception:
            return str(x)

    # list/tuple
    if isinstance(x, (list, tuple)):
        return [swig_to_py(i, depth - 1, max_list) for i in x[:max_list]]

    # SWIG vectors/arrays often support len + getitem
    if hasattr(x, "__len__") and hasattr(x, "__getitem__"):
        try:
            n = len(x)
            return [swig_to_py(x[i], depth - 1, max_list) for i in range(min(n, max_list))]
        except Exception:
            pass

    # struct-like: walk attributes (skip callables)
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

    if out:
        return out

    return _safe_scalar(x)


def _safe_scalar(x: Any) -> Any:
    """Fallback stringification."""
    try:
        if x is None or isinstance(x, (int, float, str, bool)):
            return x
        return str(x)
    except Exception:
        return "<unprintable>"


# =========================
# Best-effort "translation" to compact summaries
# =========================
def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _summarize_mac(raw: dict) -> dict:
    """
    Best-effort MAC summary.
    Your exact fields depend on SWIG bindings; we keep it defensive.
    """
    # Try common structural patterns
    hdr = _pick(raw, "hdr", "ind_hdr", "header", default={}) if isinstance(raw, dict) else {}
    msg = _pick(raw, "msg", "ind_msg", "message", default={}) if isinstance(raw, dict) else {}
    data = _pick(msg, "data", "ind_data", default=msg) if isinstance(msg, dict) else {}

    # Generic fields we might find
    summary = {
        "tstamp": _pick(raw, "tstamp", default=None),
        "hdr_keys": list(hdr.keys())[:20] if isinstance(hdr, dict) else None,
        "msg_keys": list(msg.keys())[:20] if isinstance(msg, dict) else None,
    }

    # Try to detect UE stats list-like fields
    ue_stats = None
    if isinstance(data, dict):
        for cand in ["ue_stats", "ues", "ue", "ue_lst", "ue_list", "mac_ue_stats", "mac_ue_stats_lst"]:
            if cand in data:
                ue_stats = data[cand]
                break

    # If UE stats is list of dicts, show small projection
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
    """
    Slice summary: tries to surface slice counts + UE associations.
    Your provided slice script writes JSON itself; here we do best-effort.
    """
    summary = {
        "tstamp": _pick(raw, "tstamp", default=None),
        "keys": list(raw.keys())[:40] if isinstance(raw, dict) else None,
    }

    # Try typical patterns from FlexRIC slice indication
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
    """For RLC/PDCP/GTP where we don’t know exact field shapes."""
    if not isinstance(raw, dict):
        return {"type": str(type(raw))}
    return {
        "keys": list(raw.keys())[:60],
        "tstamp": _pick(raw, "tstamp", default=None),
    }


def _translate(sm: str, ind_obj: Any) -> Dict[str, Any]:
    raw = swig_to_py(ind_obj, depth=DUMP_DEPTH, max_list=MAX_LIST)
    if sm == "mac":
        summary = _summarize_mac(raw if isinstance(raw, dict) else {})
    elif sm == "slice":
        summary = _summarize_slice(raw if isinstance(raw, dict) else {})
    else:
        summary = _summarize_generic(raw if isinstance(raw, dict) else {})
    return {"raw": raw, "summary": summary}


# =========================
# Callbacks
# =========================
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
    def __init__(self):
        ric.mac_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("mac", ind))
        except Exception as e:
            with _LOCK:
                LATEST["mac"]["status"] = "error"
                LATEST["mac"]["error"] = str(e)


class RLCCallback(ric.rlc_cb, _BaseCb):
    SM = "rlc"
    def __init__(self):
        ric.rlc_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("rlc", ind))
        except Exception as e:
            with _LOCK:
                LATEST["rlc"]["status"] = "error"
                LATEST["rlc"]["error"] = str(e)


class PDCPCallback(ric.pdcp_cb, _BaseCb):
    SM = "pdcp"
    def __init__(self):
        ric.pdcp_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("pdcp", ind))
        except Exception as e:
            with _LOCK:
                LATEST["pdcp"]["status"] = "error"
                LATEST["pdcp"]["error"] = str(e)


class GTPCallback(ric.gtp_cb, _BaseCb):
    SM = "gtp"
    def __init__(self):
        ric.gtp_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("gtp", ind))
        except Exception as e:
            with _LOCK:
                LATEST["gtp"]["status"] = "error"
                LATEST["gtp"]["error"] = str(e)


class SLICECallback(ric.slice_cb, _BaseCb):
    SM = "slice"
    def __init__(self):
        ric.slice_cb.__init__(self)
    def handle(self, ind):
        try:
            self._store(_translate("slice", ind))
        except Exception as e:
            with _LOCK:
                LATEST["slice"]["status"] = "error"
                LATEST["slice"]["error"] = str(e)


# =========================
# FlexRIC init + subscribe helpers
# =========================
def _interval_enum():
    """
    Map ms to an Interval_ms_X enum if present in your SWIG module.
    Falls back to Interval_ms_10 then Interval_ms_5.
    """
    cand = f"Interval_ms_{INTERVAL_MS}"
    if hasattr(ric, cand):
        return getattr(ric, cand)
    for fallback in ["Interval_ms_10", "Interval_ms_5", "Interval_ms_1", "Interval_ms_100"]:
        if hasattr(ric, fallback):
            return getattr(ric, fallback)
    return None


def _ensure_started():
    global _RUNNING, _NODE
    with _LOCK:
        if _RUNNING:
            return

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

    # Subscribe to enabled SMs
    if "mac" in ENABLE:
        cb = MACCallback()
        _HANDLES["mac"] = ric.report_mac_sm(_NODE, interval, cb)
        logger.info("Subscribed MAC SM")

    if "rlc" in ENABLE:
        cb = RLCCallback()
        _HANDLES["rlc"] = ric.report_rlc_sm(_NODE, interval, cb)
        logger.info("Subscribed RLC SM")

    if "pdcp" in ENABLE:
        cb = PDCPCallback()
        _HANDLES["pdcp"] = ric.report_pdcp_sm(_NODE, interval, cb)
        logger.info("Subscribed PDCP SM")

    if "gtp" in ENABLE:
        cb = GTPCallback()
        _HANDLES["gtp"] = ric.report_gtp_sm(_NODE, interval, cb)
        logger.info("Subscribed GTP SM")

    if "slice" in ENABLE:
        cb = SLICECallback()
        _HANDLES["slice"] = ric.report_slice_sm(_NODE, interval, cb)
        logger.info("Subscribed SLICE SM")

    with _LOCK:
        _RUNNING = True


def _stop_all():
    global _RUNNING
    with _LOCK:
        if not _RUNNING:
            return
        handles = dict(_HANDLES)

    # Unsubscribe
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


# =========================
# MCP tools
# =========================
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
        ric.init()
        conn = ric.conn_e2_nodes()
        out = []
        for i, c in enumerate(conn):
            # best-effort: represent id as string
            out.append({"index": i, "id_str": str(c.id)})
        return {"status": "success", "count": len(out), "nodes": out}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _get_latest(sm: str, mode: str) -> Dict[str, Any]:
    with _LOCK:
        snap = dict(LATEST.get(sm, {}))
    if not snap:
        return {"status": "error", "error": f"Unknown SM '{sm}'."}
    if mode == "raw":
        return {"status": snap.get("status"), "ts": snap.get("ts"), "node": snap.get("node"), "raw": snap.get("raw"), "error": snap.get("error")}
    return {"status": snap.get("status"), "ts": snap.get("ts"), "node": snap.get("node"), "summary": snap.get("summary"), "error": snap.get("error")}


@mcp.tool()
def get_mac_metrics(mode: str = "summary") -> Dict[str, Any]:
    """
    Get latest MAC metrics.
    mode: 'summary' (default) or 'raw'
    """
    return _get_latest("mac", mode)


@mcp.tool()
def get_rlc_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest RLC metrics (summary/raw)."""
    return _get_latest("rlc", mode)


@mcp.tool()
def get_pdcp_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest PDCP metrics (summary/raw)."""
    return _get_latest("pdcp", mode)


@mcp.tool()
def get_gtp_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest GTP metrics (summary/raw)."""
    return _get_latest("gtp", mode)


@mcp.tool()
def get_slice_metrics(mode: str = "summary") -> Dict[str, Any]:
    """Get latest SLICE metrics (summary/raw)."""
    return _get_latest("slice", mode)


@mcp.tool()
def health() -> Dict[str, Any]:
    """Quick status of subscriptions + whether we are receiving indications."""
    with _LOCK:
        running = _RUNNING
        handles = list(_HANDLES.keys())
        latest = {k: {"status": v["status"], "ts": v["ts"], "error": v["error"]} for k, v in LATEST.items()}
    return {
        "status": "success",
        "running": running,
        "enabled": sorted(ENABLE),
        "subscribed": handles,
        "latest": latest,
    }


# =========================
# main
# =========================
if __name__ == "__main__":
    # If you run directly, start subscriptions immediately (so the server has data).
    try:
        _ensure_started()
        logger.info("FlexRIC subscriptions started. Launching MCP server...")
    except Exception as e:
        logger.error("Failed to start subscriptions at boot: %s", e)
        logger.error("You can still start later via MCP tool `start()`.")

    # FastMCP run (stdio)
    mcp.run()
