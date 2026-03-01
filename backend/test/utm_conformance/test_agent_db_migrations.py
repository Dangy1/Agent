import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import agent_db


class AgentDBMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "agents.sqlite3")
        self._old_db_path = agent_db.DB_PATH
        agent_db.DB_PATH = self._db_path

    def tearDown(self) -> None:
        agent_db.DB_PATH = self._old_db_path
        self._tmpdir.cleanup()

    def test_migrates_legacy_schema_and_applies_indexes(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE agent_meta (
                  agent TEXT PRIMARY KEY,
                  revision INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE agent_state (
                  agent TEXT NOT NULL,
                  state_key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (agent, state_key)
                );
                CREATE TABLE agent_actions (
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

        agent_db._ensure_schema()

        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT version FROM agent_schema WHERE singleton_id = 1").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row[0]), int(agent_db._SCHEMA_VERSION))

            state_indexes = {str(r[1]) for r in conn.execute("PRAGMA index_list('agent_state')").fetchall()}
            action_indexes = {str(r[1]) for r in conn.execute("PRAGMA index_list('agent_actions')").fetchall()}
            self.assertIn("idx_agent_state_agent_updated_at", state_indexes)
            self.assertIn("idx_agent_actions_agent_created_at", action_indexes)

    def test_delete_state_removes_saved_row(self) -> None:
        agent_db._ensure_schema()
        db = agent_db.AgentDB("migration-test")
        db.set_state("gate", {"ok": True})
        self.assertEqual(db.get_state("gate"), {"ok": True})
        db.delete_state("gate")
        self.assertIsNone(db.get_state("gate"))


if __name__ == "__main__":
    unittest.main()
