from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_LOCK = threading.Lock()


def _default_db_path() -> str:
    here = Path(__file__).resolve().parent
    data_dir = here / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "agents.sqlite3")


DB_PATH = os.getenv("AGENT_STATE_DB_PATH", _default_db_path())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=2.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_schema() -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_meta (
                  agent TEXT PRIMARY KEY,
                  revision INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_state (
                  agent TEXT NOT NULL,
                  state_key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (agent, state_key)
                );
                CREATE TABLE IF NOT EXISTS agent_actions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  agent TEXT NOT NULL,
                  action TEXT NOT NULL,
                  entity_id TEXT,
                  payload_json TEXT,
                  result_json TEXT,
                  created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


_ensure_schema()


class AgentDB:
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self._ensure_agent_row()

    def _ensure_agent_row(self) -> None:
        now = _utc_now()
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO agent_meta(agent, revision, updated_at) VALUES (?, 0, ?)",
                    (self.agent, now),
                )
                conn.commit()
            finally:
                conn.close()

    def get_sync(self) -> Dict[str, Any]:
        with _LOCK:
            conn = _connect()
            try:
                row = conn.execute("SELECT revision, updated_at FROM agent_meta WHERE agent = ?", (self.agent,)).fetchone()
                if row is None:
                    return {"agent": self.agent, "revision": 0, "updated_at": _utc_now()}
                return {"agent": self.agent, "revision": int(row["revision"]), "updated_at": str(row["updated_at"])}
            finally:
                conn.close()

    def set_state(self, key: str, value: Any) -> None:
        now = _utc_now()
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    """
                    INSERT INTO agent_state(agent, state_key, value_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(agent, state_key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                    """,
                    (self.agent, key, raw, now),
                )
                conn.commit()
            finally:
                conn.close()

    def get_state(self, key: str) -> Any | None:
        with _LOCK:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT value_json FROM agent_state WHERE agent = ? AND state_key = ?",
                    (self.agent, key),
                ).fetchone()
                if row is None:
                    return None
                return json.loads(str(row["value_json"]))
            finally:
                conn.close()

    def recent_actions(self, limit: int = 20) -> List[Dict[str, Any]]:
        lim = max(1, min(100, int(limit)))
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, action, entity_id, payload_json, result_json, created_at
                    FROM agent_actions
                    WHERE agent = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (self.agent, lim),
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for r in rows:
                    out.append(
                        {
                            "id": int(r["id"]),
                            "action": str(r["action"]),
                            "entity_id": r["entity_id"],
                            "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                            "result": json.loads(r["result_json"]) if r["result_json"] else None,
                            "created_at": str(r["created_at"]),
                        }
                    )
                return out
            finally:
                conn.close()

    def record_action(self, action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
        now = _utc_now()
        payload_raw = None if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        result_raw = None if result is None else json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    """
                    INSERT INTO agent_actions(agent, action, entity_id, payload_json, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (self.agent, action, entity_id, payload_raw, result_raw, now),
                )
                conn.execute(
                    """
                    INSERT INTO agent_meta(agent, revision, updated_at) VALUES (?, 1, ?)
                    ON CONFLICT(agent) DO UPDATE SET revision = agent_meta.revision + 1, updated_at = excluded.updated_at
                    """,
                    (self.agent, now),
                )
                row = conn.execute("SELECT revision, updated_at FROM agent_meta WHERE agent = ?", (self.agent,)).fetchone()
                conn.commit()
                return {
                    "agent": self.agent,
                    "revision": int(row["revision"]) if row else 0,
                    "updated_at": str(row["updated_at"]) if row else now,
                }
            finally:
                conn.close()

