"""Small durable ledger; each update and its event commit together."""

import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.home / "boardroom.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS objectives (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                objective_id TEXT NOT NULL, time REAL NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL
            );
        """)

    def create(self, data):
        data = dict(data, id=uuid.uuid4().hex[:16], status="queued", calls=0,
                    next_task=0, round=0, plan=None, feedback="")
        self.save(data, "created")
        return data

    def save(self, data, kind):
        now = time.time()
        with self.db:
            self.db.execute("INSERT INTO objectives VALUES (?, ?, ?) "
                            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                            (data["id"], json.dumps(data), now))
            self.db.execute("INSERT INTO events(objective_id,time,kind,data) VALUES (?,?,?,?)",
                            (data["id"], now, kind, json.dumps(data)))

    def get(self, objective_id):
        row = self.db.execute("SELECT data FROM objectives WHERE id=?", (objective_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown objective: {objective_id}")
        return json.loads(row[0])

    def list(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT data FROM objectives ORDER BY updated DESC")]

    def events(self, objective_id):
        return [dict(row) for row in self.db.execute(
            "SELECT seq,time,kind FROM events WHERE objective_id=? ORDER BY seq", (objective_id,))]
