"""SQLite 存储层：设备、幂等批次、事件。用标准库 sqlite3，不引 ORM。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  first_seen INTEGER,
  last_seen  INTEGER
);
CREATE TABLE IF NOT EXISTS ingested_batches (
  batch_id    TEXT PRIMARY KEY,
  device_id   TEXT,
  received_at INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id   TEXT NOT NULL,
  ts          INTEGER NOT NULL,
  type        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, ts);
"""


class Storage:
    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def register_batch(self, batch_id: str, device_id: str, received_at: int) -> bool:
        """登记一个批次；返回 True=首次，False=已存在(幂等命中)。"""
        cur = self._conn.execute(
            "SELECT 1 FROM ingested_batches WHERE batch_id = ?", (batch_id,)
        )
        if cur.fetchone():
            return False
        self._conn.execute(
            "INSERT INTO ingested_batches(batch_id, device_id, received_at) VALUES (?,?,?)",
            (batch_id, device_id, received_at),
        )
        self._conn.commit()
        return True

    def upsert_device(self, device_id: str, ts: int) -> None:
        self._conn.execute(
            """
            INSERT INTO devices(device_id, first_seen, last_seen) VALUES (?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen
            """,
            (device_id, ts, ts),
        )
        self._conn.commit()

    def insert_event(self, device_id: str, ts: int, type: str, payload: dict, received_at: int) -> None:
        self._conn.execute(
            "INSERT INTO events(device_id, ts, type, payload, received_at) VALUES (?,?,?,?,?)",
            (device_id, ts, type, json.dumps(payload, ensure_ascii=False), received_at),
        )
        self._conn.commit()

    def event_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
