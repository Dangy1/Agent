#!/usr/bin/env python3
"""
mcp_flexric_slice.py (STDIO MCP server)

Dedicated MCP server for FlexRIC SLICE control + monitoring.

Tools:
- ric_init
- ric_conn_e2_nodes
- set_node_index
- start
- stop
- list_e2_nodes
- create_slices
- create_example_slices
- delete_slices
- associate_ues
- reset_slices
- get_slice_state
- get_seen_ues
- health
"""

import contextlib
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

try:
    import xapp_sdk as ric
except Exception as e:
    raise RuntimeError(
        "Failed to import xapp_sdk. Run from the directory containing xapp_sdk.py/_xapp_sdk.so "
        "or add it to PYTHONPATH."
    ) from e


logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-flexric-slice")

NODE_INDEX = int(os.getenv("FLEXRIC_NODE_INDEX", "0"))
INTERVAL_MS = int(os.getenv("FLEXRIC_INTERVAL", "10"))

mcp = FastMCP("flexric-slice")

_LOCK = threading.Lock()
_OP_LOCK = threading.RLock()
_RUNNING = False
_NODE = None
_SLICE_HANDLE = None
_INITIALIZED = False
_CONN_CACHE: List[Any] = []
_CURRENT_NODE_INDEX: Optional[int] = None
_CURRENT_INTERVAL_MS: int = INTERVAL_MS

_LATEST: Dict[str, Any] = {
    "status": "init",
    "ts": None,
    "node": None,
    "summary": None,
    "raw": None,
    "error": None,
}

_SEEN_UES: List[Dict[str, Any]] = []
_LAST_RNTI: Optional[int] = None


@contextlib.contextmanager
def redirect_c_stdout_to_stderr():
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)


def _first(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _interval_enum():
    cand = f"Interval_ms_{_CURRENT_INTERVAL_MS}"
    if hasattr(ric, cand):
        return getattr(ric, cand)
    for fallback in ["Interval_ms_10", "Interval_ms_5", "Interval_ms_1", "Interval_ms_100"]:
        if hasattr(ric, fallback):
            return getattr(ric, fallback)
    return None


def _slice_alg_name(alg_type: int) -> str:
    if alg_type == 1:
        return "STATIC"
    if alg_type == 2:
        return "NVS"
    if alg_type == 4:
        return "EDF"
    return "unknown"


def _to_int_rnti(rnti: Any) -> int:
    if isinstance(rnti, int):
        return rnti
    if isinstance(rnti, str):
        return int(rnti, 16) if rnti.lower().startswith("0x") else int(rnti)
    raise ValueError(f"Invalid RNTI '{rnti}'")


def _slice_ind_to_dict(ind: Any) -> Dict[str, Any]:
    global _LAST_RNTI

    slice_stats = {
        "RAN": {
            "dl": {}
        },
        "UE": {},
    }

    dl_dict = slice_stats["RAN"]["dl"]
    dl_len = ind.slice_stats.dl.len_slices
    dl_dict["num_of_slices"] = dl_len

    if dl_len <= 0:
        dl_dict["slice_sched_algo"] = "null"
        dl_dict["ue_sched_algo"] = _first(ind.slice_stats.dl.sched_name)
    else:
        dl_dict["slice_sched_algo"] = "null"
        dl_dict["slices"] = []
        for s in ind.slice_stats.dl.slices:
            algo = _slice_alg_name(s.params.type)
            dl_dict["slice_sched_algo"] = algo
            one = {
                "index": s.id,
                "label": _first(s.label),
                "ue_sched_algo": _first(s.sched),
            }
            if algo == "STATIC":
                one["slice_algo_params"] = {
                    "pos_low": s.params.u.sta.pos_low,
                    "pos_high": s.params.u.sta.pos_high,
                }
            elif algo == "NVS":
                conf = s.params.u.nvs.conf
                if conf == 0:
                    one["slice_algo_params"] = {
                        "type": "RATE",
                        "mbps_rsvd": s.params.u.nvs.u.rate.u1.mbps_required,
                        "mbps_ref": s.params.u.nvs.u.rate.u2.mbps_reference,
                    }
                elif conf == 1:
                    one["slice_algo_params"] = {
                        "type": "CAPACITY",
                        "pct_rsvd": s.params.u.nvs.u.capacity.u.pct_reserved,
                    }
                else:
                    one["slice_algo_params"] = {"type": "unknown"}
            elif algo == "EDF":
                one["slice_algo_params"] = {
                    "deadline": s.params.u.edf.deadline,
                    "guaranteed_prbs": s.params.u.edf.guaranteed_prbs,
                    "max_replenish": s.params.u.edf.max_replenish,
                }
            dl_dict["slices"].append(one)

    ue_dict = slice_stats["UE"]
    ue_len = ind.ue_slice_stats.len_ue_slice
    ue_dict["num_of_ues"] = ue_len
    if ue_len > 0:
        ue_dict["ues"] = []
        seen = []
        for u in ind.ue_slice_stats.ues:
            assoc_dl = "null"
            if u.dl_id >= 0 and dl_dict["num_of_slices"] > 0:
                assoc_dl = u.dl_id
            ue_item = {
                "rnti": hex(u.rnti),
                "assoc_dl_slice_id": assoc_dl,
            }
            ue_dict["ues"].append(ue_item)
            seen.append(ue_item)
            _LAST_RNTI = u.rnti
        with _LOCK:
            _SEEN_UES[:] = seen
    else:
        with _LOCK:
            _SEEN_UES[:] = []

    return slice_stats


class SLICECallback(ric.slice_cb):
    def __init__(self):
        ric.slice_cb.__init__(self)

    def handle(self, ind):
        try:
            parsed = _slice_ind_to_dict(ind)
            with _LOCK:
                _LATEST["status"] = "ok"
                _LATEST["ts"] = datetime.now().isoformat()
                _LATEST["node"] = str(_NODE) if _NODE is not None else None
                _LATEST["summary"] = {
                    "dl_num_slices": parsed["RAN"]["dl"].get("num_of_slices"),
                    "slice_sched_algo": parsed["RAN"]["dl"].get("slice_sched_algo"),
                    "ue_num": parsed["UE"].get("num_of_ues"),
                }
                _LATEST["raw"] = parsed
                _LATEST["error"] = None
        except Exception as e:
            with _LOCK:
                _LATEST["status"] = "error"
                _LATEST["error"] = str(e)


def _mark_connection_lost(reason: str) -> None:
    global _RUNNING, _SLICE_HANDLE, _NODE
    with _LOCK:
        _RUNNING = False
        _SLICE_HANDLE = None
        _NODE = None
        _LATEST["status"] = "error"
        _LATEST["error"] = reason
        _LATEST["ts"] = datetime.now().isoformat()


def _should_mark_disconnected(err: BaseException) -> bool:
    msg = str(err).upper()
    disconnect_markers = [
        "SCTP_SEND_FAILED",
        "DISCONNECT",
        "BROKEN PIPE",
        "CONNECTION RESET",
        "CONNECTION ABORTED",
    ]
    return any(marker in msg for marker in disconnect_markers)


def _ensure_started():
    global _RUNNING, _NODE, _SLICE_HANDLE
    with _OP_LOCK:
        with _LOCK:
            if _RUNNING:
                return

        conn = _ensure_conn()
        with _LOCK:
            idx = _CURRENT_NODE_INDEX if _CURRENT_NODE_INDEX is not None else (NODE_INDEX if NODE_INDEX < len(conn) else 0)
            _NODE = conn[idx].id

        try:
            with redirect_c_stdout_to_stderr():
                interval = _interval_enum()
                if interval is None:
                    raise RuntimeError("Could not find Interval_ms_* enum in xapp_sdk.")

                logger.info("Subscribing SLICE indication: node_index=%d interval=%s", idx, str(interval))
                _SLICE_HANDLE = ric.report_slice_sm(_NODE, interval, SLICECallback())
        except Exception as e:
            _mark_connection_lost(f"start failed: {e}")
            raise

        with _LOCK:
            _RUNNING = True


def _require_started() -> None:
    with _LOCK:
        if _RUNNING:
            return
    raise RuntimeError("Slice subscription is not started. Call start() first.")


def _stop():
    global _RUNNING, _SLICE_HANDLE
    with _OP_LOCK:
        with _LOCK:
            if not _RUNNING:
                return
            h = _SLICE_HANDLE

        with redirect_c_stdout_to_stderr():
            try:
                ric.rm_report_slice_sm(h)
            except Exception as e:
                logger.warning("rm_report_slice_sm failed: %s", e)

        with _LOCK:
            _SLICE_HANDLE = None
            _RUNNING = False


def _do_init() -> None:
    global _INITIALIZED
    with _LOCK:
        if _INITIALIZED:
            return
    with redirect_c_stdout_to_stderr():
        ric.init()
    with _LOCK:
        _INITIALIZED = True


def _fetch_conn() -> List[Any]:
    _do_init()
    with redirect_c_stdout_to_stderr():
        conn = ric.conn_e2_nodes()
    return list(conn) if conn else []


def _ensure_conn() -> List[Any]:
    global _CONN_CACHE, _CURRENT_NODE_INDEX
    conn = _fetch_conn()
    if len(conn) == 0:
        raise RuntimeError("No E2 nodes connected (conn_e2_nodes() returned empty).")
    with _LOCK:
        _CONN_CACHE = conn
        if _CURRENT_NODE_INDEX is None:
            _CURRENT_NODE_INDEX = NODE_INDEX if NODE_INDEX < len(conn) else 0
    return conn


def _create_one_slice(slice_params: Dict[str, Any], slice_sched_algo: str):
    s = ric.fr_slice_t()
    s.id = int(slice_params["id"])
    s.label = str(slice_params["label"])
    s.len_label = len(str(slice_params["label"]))
    ue_sched = str(slice_params.get("ue_sched_algo", "PF"))
    s.sched = ue_sched
    s.len_sched = len(ue_sched)

    algo = slice_sched_algo.upper()
    if algo == "STATIC":
        s.params.type = ric.SLICE_ALG_SM_V0_STATIC
        cfg = slice_params["slice_algo_params"]
        s.params.u.sta.pos_low = int(cfg["pos_low"])
        s.params.u.sta.pos_high = int(cfg["pos_high"])
    elif algo == "NVS":
        s.params.type = ric.SLICE_ALG_SM_V0_NVS
        nvs_type = str(slice_params.get("type", "SLICE_SM_NVS_V0_RATE")).upper()
        cfg = slice_params["slice_algo_params"]
        if nvs_type in ["SLICE_SM_NVS_V0_RATE", "RATE"]:
            s.params.u.nvs.conf = ric.SLICE_SM_NVS_V0_RATE
            s.params.u.nvs.u.rate.u1.mbps_required = int(cfg["mbps_rsvd"])
            s.params.u.nvs.u.rate.u2.mbps_reference = int(cfg["mbps_ref"])
        elif nvs_type in ["SLICE_SM_NVS_V0_CAPACITY", "CAPACITY"]:
            s.params.u.nvs.conf = ric.SLICE_SM_NVS_V0_CAPACITY
            s.params.u.nvs.u.capacity.u.pct_reserved = float(cfg["pct_rsvd"])
        else:
            raise ValueError(f"Unknown NVS type '{slice_params.get('type')}'")
    elif algo == "EDF":
        s.params.type = ric.SLICE_ALG_SM_V0_EDF
        cfg = slice_params["slice_algo_params"]
        s.params.u.edf.deadline = int(cfg["deadline"])
        s.params.u.edf.guaranteed_prbs = int(cfg["guaranteed_prbs"])
        s.params.u.edf.max_replenish = int(cfg["max_replenish"])
    else:
        raise ValueError(f"Unknown slice_sched_algo '{slice_sched_algo}'")

    return s


def _fill_addmod_msg(ctrl_msg: Dict[str, Any]):
    msg = ric.slice_ctrl_msg_t()
    msg.type = ric.SLICE_CTRL_SM_V0_ADD

    dl = ric.ul_dl_slice_conf_t()
    ue_sched_algo = str(ctrl_msg.get("ue_sched_algo", "PF"))
    dl.sched_name = ue_sched_algo
    dl.len_sched_name = len(ue_sched_algo)

    slices = ctrl_msg.get("slices", [])
    num_slices = int(ctrl_msg.get("num_slices", len(slices)))
    dl.len_slices = num_slices

    arr = ric.slice_array(num_slices)
    for i in range(num_slices):
        arr[i] = _create_one_slice(slices[i], ctrl_msg["slice_sched_algo"])
    dl.slices = arr
    msg.u.add_mod_slice.dl = dl
    return msg


def _fill_del_msg(ctrl_msg: Dict[str, Any]):
    msg = ric.slice_ctrl_msg_t()
    msg.type = ric.SLICE_CTRL_SM_V0_DEL
    ids = ctrl_msg.get("delete_dl_slice_id", [])
    num = int(ctrl_msg.get("num_dl_slices", len(ids)))
    msg.u.del_slice.len_dl = num
    arr = ric.del_dl_array(num)
    for i in range(num):
        arr[i] = int(ids[i])
    msg.u.del_slice.dl = arr
    return msg


def _fill_assoc_msg(ctrl_msg: Dict[str, Any]):
    msg = ric.slice_ctrl_msg_t()
    msg.type = ric.SLICE_CTRL_SM_V0_UE_SLICE_ASSOC

    ues = ctrl_msg.get("ues", [])
    num = int(ctrl_msg.get("num_ues", len(ues)))
    msg.u.ue_slice.len_ue_slice = num
    arr = ric.ue_slice_assoc_array(num)

    for i in range(num):
        item = ues[i]
        a = ric.ue_slice_assoc_t()
        if "rnti" in item:
            a.rnti = _to_int_rnti(item["rnti"])
        elif _LAST_RNTI is not None:
            a.rnti = _LAST_RNTI
        else:
            raise ValueError("RNTI missing and no previously observed UE RNTI is available.")
        a.dl_id = int(item["assoc_dl_slice_id"])
        arr[i] = a
    msg.u.ue_slice.ues = arr
    return msg


@mcp.tool()
def start(node_index: Optional[int] = None, interval_ms: Optional[int] = None) -> Dict[str, Any]:
    """Initialize FlexRIC and start SLICE indication subscription."""
    try:
        global _CURRENT_NODE_INDEX, _CURRENT_INTERVAL_MS
        with _OP_LOCK:
            with _LOCK:
                if _RUNNING:
                    return {
                        "status": "success",
                        "running": True,
                        "already_running": True,
                        "node_index": _CURRENT_NODE_INDEX,
                        "interval_ms": _CURRENT_INTERVAL_MS,
                    }
                if node_index is not None:
                    _CURRENT_NODE_INDEX = int(node_index)
                if interval_ms is not None:
                    _CURRENT_INTERVAL_MS = int(interval_ms)
            _ensure_started()
            with _LOCK:
                current_idx = _CURRENT_NODE_INDEX
                current_itv = _CURRENT_INTERVAL_MS
            return {"status": "success", "running": True, "node_index": current_idx, "interval_ms": current_itv}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def stop() -> Dict[str, Any]:
    """Stop SLICE indication subscription."""
    try:
        _stop()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_e2_nodes() -> Dict[str, Any]:
    """List connected E2 nodes."""
    try:
        conn = _fetch_conn()
        nodes = [{"index": i, "id_str": str(c.id)} for i, c in enumerate(conn)]
        with _LOCK:
            _CONN_CACHE = conn
        return {"status": "success", "count": len(nodes), "nodes": nodes}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def ric_init() -> Dict[str, Any]:
    """Run ric.init() once and keep initialized state."""
    try:
        _do_init()
        return {"status": "success", "initialized": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def ric_conn_e2_nodes(refresh: bool = True) -> Dict[str, Any]:
    """Run ric.conn_e2_nodes() and return E2 node list."""
    try:
        global _CONN_CACHE
        if refresh or len(_CONN_CACHE) == 0:
            conn = _fetch_conn()
            with _LOCK:
                _CONN_CACHE = conn
        with _LOCK:
            conn = list(_CONN_CACHE)
        nodes = [{"index": i, "id_str": str(c.id)} for i, c in enumerate(conn)]
        return {"status": "success", "count": len(nodes), "nodes": nodes}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def set_node_index(index: int) -> Dict[str, Any]:
    """Set the node index used by start/control tools."""
    try:
        conn = _ensure_conn()
        if index < 0 or index >= len(conn):
            return {"status": "error", "error": f"index out of range [0, {len(conn)-1}]"}
        with _LOCK:
            global _CURRENT_NODE_INDEX
            _CURRENT_NODE_INDEX = int(index)
        return {"status": "success", "node_index": index}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def create_slices(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add/modify DL slices.

    Example config:
    {
      "slice_sched_algo":"STATIC",
      "slices":[
        {"id":0,"label":"s1","ue_sched_algo":"PF","slice_algo_params":{"pos_low":0,"pos_high":2}}
      ]
    }
    """
    try:
        _require_started()
        msg = _fill_addmod_msg(config)
        with _OP_LOCK:
            try:
                with redirect_c_stdout_to_stderr():
                    ric.control_slice_sm(_NODE, msg)
            except Exception as e:
                if _should_mark_disconnected(e):
                    _mark_connection_lost(f"control failed: {e}")
                raise
        return {
            "status": "success",
            "applied": "ADDMOD",
            "num_slices": int(config.get("num_slices", len(config.get("slices", [])))),
            "slice_sched_algo": config.get("slice_sched_algo"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def create_example_slices(profile: str = "static") -> Dict[str, Any]:
    """
    Create slices using built-in examples modeled after xapp_slice_moni_ctrl.py.
    profile: static | nvs_rate | nvs_cap | edf
    """
    p = profile.strip().lower()
    examples: Dict[str, Dict[str, Any]] = {
        "static": {
            "slice_sched_algo": "STATIC",
            "slices": [
                {"id": 0, "label": "s1", "ue_sched_algo": "PF", "slice_algo_params": {"pos_low": 0, "pos_high": 2}},
                {"id": 2, "label": "s2", "ue_sched_algo": "PF", "slice_algo_params": {"pos_low": 3, "pos_high": 10}},
                {"id": 5, "label": "s3", "ue_sched_algo": "PF", "slice_algo_params": {"pos_low": 11, "pos_high": 13}},
            ],
        },
        "nvs_rate": {
            "slice_sched_algo": "NVS",
            "slices": [
                {"id": 0, "label": "s1", "ue_sched_algo": "PF", "type": "SLICE_SM_NVS_V0_RATE",
                 "slice_algo_params": {"mbps_rsvd": 60, "mbps_ref": 120}},
                {"id": 2, "label": "s2", "ue_sched_algo": "PF", "type": "SLICE_SM_NVS_V0_RATE",
                 "slice_algo_params": {"mbps_rsvd": 60, "mbps_ref": 120}},
            ],
        },
        "nvs_cap": {
            "slice_sched_algo": "NVS",
            "slices": [
                {"id": 0, "label": "s1", "ue_sched_algo": "PF", "type": "SLICE_SM_NVS_V0_CAPACITY",
                 "slice_algo_params": {"pct_rsvd": 0.5}},
                {"id": 2, "label": "s2", "ue_sched_algo": "PF", "type": "SLICE_SM_NVS_V0_CAPACITY",
                 "slice_algo_params": {"pct_rsvd": 0.3}},
                {"id": 5, "label": "s3", "ue_sched_algo": "PF", "type": "SLICE_SM_NVS_V0_CAPACITY",
                 "slice_algo_params": {"pct_rsvd": 0.2}},
            ],
        },
        "edf": {
            "slice_sched_algo": "EDF",
            "slices": [
                {"id": 0, "label": "s1", "ue_sched_algo": "PF",
                 "slice_algo_params": {"deadline": 10, "guaranteed_prbs": 20, "max_replenish": 0}},
                {"id": 2, "label": "s2", "ue_sched_algo": "RR",
                 "slice_algo_params": {"deadline": 20, "guaranteed_prbs": 20, "max_replenish": 0}},
                {"id": 5, "label": "s3", "ue_sched_algo": "MT",
                 "slice_algo_params": {"deadline": 40, "guaranteed_prbs": 10, "max_replenish": 0}},
            ],
        },
    }
    if p not in examples:
        return {"status": "error", "error": "Unknown profile. Use: static|nvs_rate|nvs_cap|edf"}
    out = create_slices(examples[p])
    if out.get("status") == "success":
        out["profile"] = p
    return out


@mcp.tool()
def delete_slices(delete_dl_slice_id: List[int]) -> Dict[str, Any]:
    """Delete DL slices by ID list."""
    try:
        _require_started()
        msg = _fill_del_msg({"delete_dl_slice_id": delete_dl_slice_id})
        with _OP_LOCK:
            try:
                with redirect_c_stdout_to_stderr():
                    ric.control_slice_sm(_NODE, msg)
            except Exception as e:
                if _should_mark_disconnected(e):
                    _mark_connection_lost(f"control failed: {e}")
                raise
        return {"status": "success", "applied": "DEL", "deleted_ids": delete_dl_slice_id}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def associate_ues(ues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Associate UEs to DL slices.

    Example item:
    {"rnti":"0x4601","assoc_dl_slice_id":2}
    """
    try:
        _require_started()
        msg = _fill_assoc_msg({"ues": ues})
        with _OP_LOCK:
            try:
                with redirect_c_stdout_to_stderr():
                    ric.control_slice_sm(_NODE, msg)
            except Exception as e:
                if _should_mark_disconnected(e):
                    _mark_connection_lost(f"control failed: {e}")
                raise
        return {"status": "success", "applied": "ASSOC_UE_SLICE", "num_ues": len(ues)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def reset_slices() -> Dict[str, Any]:
    """Reset DL slices by sending ADDMOD with zero slices."""
    try:
        _require_started()
        msg = _fill_addmod_msg({"slice_sched_algo": "STATIC", "num_slices": 0, "slices": []})
        with _OP_LOCK:
            try:
                with redirect_c_stdout_to_stderr():
                    ric.control_slice_sm(_NODE, msg)
            except Exception as e:
                if _should_mark_disconnected(e):
                    _mark_connection_lost(f"control failed: {e}")
                raise
        return {"status": "success", "applied": "RESET"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_slice_state(mode: str = "summary") -> Dict[str, Any]:
    """Get latest slice state. mode: summary|raw"""
    with _LOCK:
        out = {
            "status": _LATEST["status"],
            "ts": _LATEST["ts"],
            "node": _LATEST["node"],
            "running": _RUNNING,
            "error": _LATEST["error"],
        }
        if mode == "raw":
            out["raw"] = _LATEST["raw"]
        else:
            out["summary"] = _LATEST["summary"]
        return out


@mcp.tool()
def get_seen_ues() -> Dict[str, Any]:
    """Return latest UE list observed from slice indication."""
    with _LOCK:
        return {"status": "success", "count": len(_SEEN_UES), "ues": list(_SEEN_UES)}


@mcp.tool()
def health() -> Dict[str, Any]:
    """Quick health status for this MCP server."""
    with _LOCK:
        return {
            "status": "success",
            "running": _RUNNING,
            "initialized": _INITIALIZED,
            "node_config_index": NODE_INDEX,
            "current_node_index": _CURRENT_NODE_INDEX,
            "interval_ms": _CURRENT_INTERVAL_MS,
            "latest": {
                "status": _LATEST["status"],
                "ts": _LATEST["ts"],
                "error": _LATEST["error"],
            },
        }


if __name__ == "__main__":
    mcp.run()
