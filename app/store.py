"""Durable single-worker job queue and conversation metadata."""

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from app.config import DATA


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    # `with connection` commits; `closing` is what actually releases the handle.
    with closing(sqlite3.connect(DATA / "app.sqlite", timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        yield db


def init():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, locator TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft', error TEXT, active_version TEXT,
          updated_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS threads (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, source_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(sources)")}
        if "progress" not in columns:
            db.execute("ALTER TABLE sources ADD COLUMN progress TEXT NOT NULL DEFAULT ''")


def progress(source_id, description):
    with connect() as db:
        db.execute("UPDATE sources SET progress=?,updated_at=? WHERE id=?", (description, now(), source_id))


def sources():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY updated_at DESC")]


def source(source_id):
    with connect() as db:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None


def add_source(title, kind, locator):
    sid = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,kind,locator,status,progress,updated_at) VALUES(?,?,?,?,?,?,?)",
            (sid, title, kind, locator, "draft", "분석 대기 중", now()),
        )
    return source(sid)


def claim():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM sources WHERE status='queued' ORDER BY updated_at LIMIT 1").fetchone()
        if row:
            db.execute(
                "UPDATE sources SET status='processing',attempts=attempts+1,error=NULL WHERE id=?",
                (row["id"],),
            )
            return dict(row)


def message(tid, role, text, evidence=None):
    with connect() as db:
        db.execute(
            "INSERT INTO messages(thread_id,role,content,evidence,created_at) VALUES(?,?,?,?,?)",
            (tid, role, text, json.dumps(evidence or [], ensure_ascii=False), now()),
        )
