from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    disk_id TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    hash TEXT,
    PRIMARY KEY (disk_id, path)
);

CREATE TABLE IF NOT EXISTS scans (
    disk_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    file_count INTEGER,
    total_bytes INTEGER,
    errors INTEGER
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scans run one thread per disk, each with its own connection; WAL lets
    # them write without blocking readers such as the API.
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
