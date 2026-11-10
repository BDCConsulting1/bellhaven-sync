"""SQLite state: proposals, decisions and run history.

Re-run safety: every proposal has a fingerprint (kind + target + exact change).
If a proposal with the same fingerprint already exists in ANY state (pending,
approved, rejected, applied, failed), the pipeline does not insert it again.
Pending proposals that the latest run no longer produces are marked 'superseded'.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  target TEXT NOT NULL,
  title TEXT NOT NULL,
  confidence TEXT,
  changes TEXT, before TEXT, create_fields TEXT, evidence TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  first_seen_run INTEGER, last_seen_run INTEGER,
  created_at TEXT, decided_at TEXT, reviewer_note TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT, summary TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def start_run(con) -> int:
    cur = con.execute("INSERT INTO runs(started_at) VALUES (?)", (now(),))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, summary: dict) -> None:
    con.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?", (now(), json.dumps(summary), run_id))
    con.commit()


def upsert_proposals(con, run_id: int, proposals: list[dict]) -> dict:
    new = existing = 0
    seen = set()
    for p in proposals:
        seen.add(p["fingerprint"])
        row = con.execute("SELECT id FROM proposals WHERE fingerprint=?", (p["fingerprint"],)).fetchone()
        if row:
            existing += 1
            con.execute("UPDATE proposals SET last_seen_run=? WHERE id=?", (run_id, row["id"]))
            continue
        new += 1
        con.execute(
            """INSERT INTO proposals(fingerprint, kind, target, title, confidence, changes, before,
               create_fields, evidence, status, first_seen_run, last_seen_run, created_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?, ?, ?)""",
            (p["fingerprint"], p["kind"], p["target"], p["title"], p["confidence"], json.dumps(p["changes"]),
             json.dumps(p["before"]), json.dumps(p["create"]), json.dumps(p["evidence"]), run_id, run_id, now()))
    superseded = 0
    for row in con.execute("SELECT id, fingerprint FROM proposals WHERE status='pending'").fetchall():
        if row["fingerprint"] not in seen:
            con.execute("UPDATE proposals SET status='superseded' WHERE id=?", (row["id"],))
            superseded += 1
    con.commit()
    return {"new": new, "already_known": existing, "superseded": superseded}


def load(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("changes", "before", "create_fields", "evidence", "result"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get(con, pid: int) -> dict:
    return load(con.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())


def set_status(con, pid: int, status: str, result: dict | None = None, note: str = "") -> None:
    con.execute("UPDATE proposals SET status=?, decided_at=?, result=?, reviewer_note=? WHERE id=?",
                (status, now(), json.dumps(result) if result is not None else None, note, pid))
    con.commit()
