from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR

DB_PATH = DATA_DIR / "osint.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    source_type  TEXT NOT NULL,   -- news | reddit_post | reddit_comment | youtube_video | youtube_comment
    title        TEXT,
    text         TEXT,
    url          TEXT,
    author       TEXT,
    ts           TEXT NOT NULL,   -- published time, UTC ISO (falls back to collection time)
    collected_at TEXT NOT NULL,
    sentiment    TEXT,            -- negative | neutral | positive; NULL = not analyzed yet
    score        REAL,            -- -1 .. 1
    category     TEXT,            -- negative-news category, 'none' for non-negative
    severity     INTEGER,         -- 1..5 for negative, 0 otherwise
    summary      TEXT,
    analyzer     TEXT,
    -- Credibility check (negative items only); NULL = not checked yet
    corroboration      INTEGER,   -- how many distinct sources carry the same story
    credibility        INTEGER,   -- 0..100
    cred_reason        TEXT,      -- why that score, in plain words
    factcheck_rating   TEXT,      -- verdict from a published fact-check, e.g. "False"
    factcheck_publisher TEXT,
    factcheck_url      TEXT,
    checked_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_ts ON items(ts);
CREATE INDEX IF NOT EXISTS idx_items_sentiment ON items(sentiment);

CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    window_hours REAL NOT NULL,
    total        INTEGER NOT NULL,
    negative     INTEGER NOT NULL,
    ratio        REAL NOT NULL,
    level        TEXT NOT NULL,   -- MEDIUM | HIGH | CRITICAL
    categories   TEXT NOT NULL,   -- JSON {category: count}
    item_ids     TEXT NOT NULL,   -- JSON list of the top negative item ids
    status       TEXT NOT NULL,   -- sent | failed | no_smtp | dry_run
    error        TEXT,
    report_path  TEXT
);

CREATE TABLE IF NOT EXISTS state (   -- small key/value store, e.g. last YouTube run time
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


# Columns added after the first release; databases created earlier are upgraded in place
LATER_COLUMNS = {
    "corroboration": "INTEGER", "credibility": "INTEGER", "cred_reason": "TEXT",
    "factcheck_rating": "TEXT", "factcheck_publisher": "TEXT", "factcheck_url": "TEXT",
    "checked_at": "TEXT",
}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for column, kind in LATER_COLUMNS.items():
        if column not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {column} {kind}")
    conn.commit()
    return conn


def insert_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Insert new items, silently skipping ones already stored. Returns the number inserted."""
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO items (id, source, source_type, title, text, url, author, ts, collected_at)
           VALUES (:id, :source, :source_type, :title, :text, :url, :author, :ts, :collected_at)""",
        items,
    )
    conn.commit()
    return conn.total_changes - before


def unanalyzed(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM items WHERE sentiment IS NULL ORDER BY ts DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]


def save_analysis(conn: sqlite3.Connection, results: list[dict]) -> None:
    conn.executemany(
        """UPDATE items SET sentiment=:sentiment, score=:score, category=:category,
                            severity=:severity, summary=:summary, analyzer=:analyzer
           WHERE id=:id""",
        results,
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def prune(conn: sqlite3.Connection, days: float) -> int:
    """Delete items collected more than `days` ago and compact the file (keeps the pushed database small).
    Uses collection time, not publish time, so old articles still sitting in a feed aren't re-added every run."""
    n = conn.execute("DELETE FROM items WHERE collected_at < ?", (hours_ago(days * 24),)).rowcount
    conn.commit()
    if n:
        conn.execute("VACUUM")
    return n


def reset_analysis(conn: sqlite3.Connection, analyzer: str = "lexicon") -> int:
    """Mark items analyzed by `analyzer` as pending so the next analyze pass redoes them."""
    n = conn.execute(
        "UPDATE items SET sentiment=NULL, score=NULL, category=NULL, severity=NULL, summary=NULL, analyzer=NULL "
        "WHERE analyzer = ?", (analyzer,)
    ).rowcount
    conn.commit()
    return n


def unchecked_negatives(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM items WHERE sentiment = 'negative' AND checked_at IS NULL ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def items_since(conn: sqlite3.Connection, since: str) -> list[dict]:
    """Everything collected in a window — used to see how many sources carry the same story."""
    rows = conn.execute(
        "SELECT id, source, source_type, title, text, ts FROM items WHERE ts >= ?", (since,)
    )
    return [dict(r) for r in rows]


def reset_credibility(conn: sqlite3.Connection) -> int:
    """Clear the credibility check so the next pass redoes it (e.g. after enabling the Fact Check API)."""
    n = conn.execute("UPDATE items SET checked_at = NULL WHERE sentiment = 'negative'").rowcount
    conn.commit()
    return n


def save_credibility(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """UPDATE items SET corroboration=:corroboration, credibility=:credibility, cred_reason=:cred_reason,
                            factcheck_rating=:factcheck_rating, factcheck_publisher=:factcheck_publisher,
                            factcheck_url=:factcheck_url, checked_at=:checked_at
           WHERE id=:id""",
        rows,
    )
    conn.commit()


def window_stats(conn: sqlite3.Connection, since: str) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(sentiment = 'negative'), 0) AS negative,
                  COALESCE(SUM(sentiment = 'positive'), 0) AS positive,
                  AVG(CASE WHEN sentiment = 'negative' THEN severity END) AS avg_severity
           FROM items WHERE sentiment IS NOT NULL AND ts >= ?""",
        (since,),
    ).fetchone()
    categories = {
        r["category"]: r["n"]
        for r in conn.execute(
            """SELECT category, COUNT(*) AS n FROM items
               WHERE sentiment = 'negative' AND ts >= ?
               GROUP BY category ORDER BY n DESC""",
            (since,),
        )
    }
    total, negative = row["total"], row["negative"]
    return {
        "since": since,
        "total": total,
        "negative": negative,
        "positive": row["positive"],
        "neutral": total - negative - row["positive"],
        "ratio": negative / total if total else 0.0,
        "avg_severity": row["avg_severity"] or 0.0,
        "categories": categories,
    }


def top_negative(conn: sqlite3.Connection, since: str, limit: int) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM items WHERE sentiment = 'negative' AND ts >= ?
           ORDER BY severity DESC, score ASC, ts DESC LIMIT ?""",
        (since, limit),
    )
    return [dict(r) for r in rows]


def last_ticket(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> dict | None:
    placeholders = ",".join("?" * len(statuses))
    row = conn.execute(
        f"SELECT * FROM tickets WHERE status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
        statuses,
    ).fetchone()
    return dict(row) if row else None


def save_ticket(conn: sqlite3.Connection, t: dict) -> None:
    conn.execute(
        """INSERT INTO tickets (id, created_at, window_hours, total, negative, ratio, level,
                                categories, item_ids, status, error, report_path)
           VALUES (:id, :created_at, :window_hours, :total, :negative, :ratio, :level,
                   :categories, :item_ids, :status, :error, :report_path)""",
        {**t, "categories": json.dumps(t["categories"], ensure_ascii=False),
         "item_ids": json.dumps(t["item_ids"])},
    )
    conn.commit()


def recent_tickets(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)
    )]
