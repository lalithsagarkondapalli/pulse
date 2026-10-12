"""SQLite storage. Plain sqlite3 on purpose: the whole schema fits on one screen
and every query is visible, which makes the data model easy to review."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS surveys (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    brand       TEXT NOT NULL,
    question    TEXT NOT NULL,
    reward_points INTEGER NOT NULL DEFAULT 50,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS responses (
    id          INTEGER PRIMARY KEY,
    survey_id   INTEGER NOT NULL REFERENCES surveys(id),
    score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
    comment     TEXT NOT NULL DEFAULT '',
    email_hash  TEXT,               -- sha256 of normalised email, used for reward de-duplication
    contact_email TEXT,             -- stored only when the customer ticks "you can contact me"
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    -- filled in asynchronously by the tagger
    theme       TEXT,
    sentiment   TEXT,
    tagged_by   TEXT,               -- 'claude' | 'rules' | NULL (pending)
    -- close-the-loop workflow
    followed_up INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_responses_survey ON responses(survey_id, created_at);

CREATE TABLE IF NOT EXISTS rewards (
    id          INTEGER PRIMARY KEY,
    survey_id   INTEGER NOT NULL REFERENCES surveys(id),
    email_hash  TEXT NOT NULL,
    code        TEXT NOT NULL UNIQUE,
    points      INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (survey_id, email_hash)  -- one reward per customer per survey
);
"""


def db_path() -> str:
    return os.environ.get("PULSE_DB", "pulse.db")


class _Result:
    """Fully-fetched result, so no cursor outlives the lock."""

    def __init__(self, rows: list, lastrowid: int | None):
        self._rows, self.lastrowid = rows, lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class SafeConnection:
    """One SQLite connection shared by FastAPI's thread pool and background
    tasks. Every statement runs under a re-entrant lock and its rows are
    fetched before the lock is released; `tx()` holds the same lock for a
    whole transaction so threads can't interleave inside each other's writes.
    Throughput is one statement at a time - fine for SQLite in one process,
    and the reason to move to Postgres when that stops being true."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.lock = threading.RLock()

    def execute(self, sql: str, params: tuple = ()) -> _Result:
        with self.lock:
            cur = self._conn.execute(sql, params)
            return _Result(cur.fetchall(), cur.lastrowid)

    def executescript(self, sql: str) -> None:
        with self.lock:
            self._conn.executescript(sql)

    def commit(self) -> None:
        with self.lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self.lock:
            self._conn.rollback()


def connect(path: str | None = None) -> SafeConnection:
    return SafeConnection(path or db_path())


def init(conn: SafeConnection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx(conn: SafeConnection) -> Iterator[SafeConnection]:
    with conn.lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
