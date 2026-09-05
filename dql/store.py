"""SQLite state: schema, run bookkeeping, cost accounting, artifact hash locks.

This is the one canonical store (BUILD_CONTRACT C1). Every stage reads and
writes through here so no two components can disagree about a value.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     TEXT PRIMARY KEY,
    subject       TEXT NOT NULL,
    body          TEXT NOT NULL,
    channel       TEXT NOT NULL,
    tier          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    vendor_label  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    stage       TEXT NOT NULL,
    model       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS labels (
    run_id     TEXT NOT NULL,
    ticket_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    label      TEXT,
    confidence REAL,
    rationale  TEXT,
    abstain    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, ticket_id, source)
);
CREATE INDEX IF NOT EXISTS idx_labels_ticket ON labels(ticket_id);
CREATE INDEX IF NOT EXISTS idx_labels_source ON labels(source);

CREATE TABLE IF NOT EXISTS qa_flags (
    flag_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    ticket_id       TEXT NOT NULL,
    flag_type       TEXT NOT NULL,
    detail          TEXT,
    rank            INTEGER,
    routed_to_human INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_flags_ticket ON qa_flags(ticket_id);
CREATE INDEX IF NOT EXISTS idx_flags_type ON qa_flags(flag_type);

CREATE TABLE IF NOT EXISTS adjudications (
    ticket_id   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    final_label TEXT,
    reviewer    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    seconds     REAL NOT NULL,
    PRIMARY KEY (ticket_id, ts)
);

CREATE TABLE IF NOT EXISTS costs (
    call_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    model      TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    usd        REAL NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    name      TEXT PRIMARY KEY,
    sha256    TEXT NOT NULL,
    locked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key  TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    response   TEXT NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the store, creating the schema if needed."""
    path = db_path or paths.DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------

def new_run_id(stage: str) -> str:
    return f"{stage}-{time.strftime('%Y%m%dT%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"


def start_run(conn: sqlite3.Connection, stage: str, model: str | None = None) -> str:
    run_id = new_run_id(stage)
    conn.execute(
        "INSERT INTO runs (run_id, stage, model, started_at, status) VALUES (?,?,?,?,'running')",
        (run_id, stage, model, utcnow()),
    )
    conn.commit()
    return run_id


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str = "ok",
    note: str | None = None,
) -> None:
    cost = conn.execute(
        "SELECT COALESCE(SUM(usd), 0.0) FROM costs WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, cost_usd = ?, note = ? WHERE run_id = ?",
        (utcnow(), status, cost, note, run_id),
    )
    conn.commit()


def latest_run(conn: sqlite3.Connection, stage: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE stage = ? AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
        (stage,),
    ).fetchone()


# --------------------------------------------------------------------------
# costs
# --------------------------------------------------------------------------

def record_cost(
    conn: sqlite3.Connection,
    call_id: str,
    run_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    usd: float,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO costs VALUES (?,?,?,?,?,?,?)",
        (call_id, run_id, model, tokens_in, tokens_out, usd, utcnow()),
    )
    conn.commit()


def total_cost(conn: sqlite3.Connection) -> float:
    return float(conn.execute("SELECT COALESCE(SUM(usd), 0.0) FROM costs").fetchone()[0])


def cost_breakdown(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.stage AS stage,
                  COUNT(c.call_id) AS calls,
                  COALESCE(SUM(c.tokens_in), 0) AS tokens_in,
                  COALESCE(SUM(c.tokens_out), 0) AS tokens_out,
                  COALESCE(SUM(c.usd), 0.0) AS usd
           FROM costs c JOIN runs r ON r.run_id = c.run_id
           GROUP BY r.stage ORDER BY usd DESC"""
    ).fetchall()


# --------------------------------------------------------------------------
# artifacts (hash locks)
# --------------------------------------------------------------------------

def lock_artifact(conn: sqlite3.Connection, name: str, path: Path) -> str:
    digest = sha256_file(path)
    conn.execute(
        "INSERT OR REPLACE INTO artifacts (name, sha256, locked_at) VALUES (?,?,?)",
        (name, digest, utcnow()),
    )
    conn.commit()
    return digest


def get_artifact(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM artifacts WHERE name = ?", (name,)).fetchone()


class TaxonomyDrift(RuntimeError):
    """Raised when taxonomy.yaml changed after it was locked (C2)."""


def assert_taxonomy_locked(conn: sqlite3.Connection) -> str:
    """Every stage after `induce` calls this first. Hard stop on drift."""
    row = get_artifact(conn, "taxonomy.yaml")
    if row is None:
        raise TaxonomyDrift(
            "taxonomy.yaml is not locked. Run `python -m dql induce` first."
        )
    if not paths.TAXONOMY.exists():
        raise TaxonomyDrift(
            f"taxonomy.yaml is locked at {row['sha256'][:12]} but the file is missing."
        )
    current = sha256_file(paths.TAXONOMY)
    if current != row["sha256"]:
        raise TaxonomyDrift(
            "taxonomy.yaml changed after it was locked.\n"
            f"  locked  : {row['sha256']}\n"
            f"  current : {current}\n"
            "Labels produced against the old taxonomy are no longer valid. "
            "Re-run `python -m dql induce --relock` deliberately, then relabel."
        )
    return current


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------

def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
        (key, json.dumps(value)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


# --------------------------------------------------------------------------
# tickets and labels
# --------------------------------------------------------------------------

def insert_tickets(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    payload = [
        (
            r["ticket_id"], r["subject"], r["body"], r["channel"],
            r["tier"], r["created_at"], r["vendor_label"],
        )
        for r in rows
    ]
    conn.executemany("INSERT OR REPLACE INTO tickets VALUES (?,?,?,?,?,?,?)", payload)
    conn.commit()
    return len(payload)


def all_tickets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tickets ORDER BY ticket_id").fetchall()


def ticket_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])


def insert_labels(conn: sqlite3.Connection, run_id: str, rows: Iterable[dict]) -> int:
    payload = [
        (
            run_id, r["ticket_id"], r["source"], r.get("label"),
            r.get("confidence"), r.get("rationale"), int(bool(r.get("abstain", False))),
        )
        for r in rows
    ]
    conn.executemany("INSERT OR REPLACE INTO labels VALUES (?,?,?,?,?,?,?)", payload)
    conn.commit()
    return len(payload)


def labels_for_run(conn: sqlite3.Connection, run_id: str, source: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM labels WHERE run_id = ? AND source = ? ORDER BY ticket_id",
        (run_id, source),
    ).fetchall()


def latest_labels(conn: sqlite3.Connection, source: str) -> dict[str, sqlite3.Row]:
    """Labels from the most recent successful labeling run for one source."""
    row = conn.execute(
        """SELECT l.run_id FROM labels l JOIN runs r ON r.run_id = l.run_id
           WHERE l.source = ? AND r.status = 'ok'
           ORDER BY r.started_at DESC LIMIT 1""",
        (source,),
    ).fetchone()
    if row is None:
        return {}
    return {
        r["ticket_id"]: r
        for r in labels_for_run(conn, row["run_id"], source)
    }
