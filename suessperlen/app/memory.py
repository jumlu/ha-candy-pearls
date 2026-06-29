"""
Persistent conversation memory backed by SQLite at /data/memory.db.

Two tables:
  - messages: per-group conversation history (role/content/timestamp)
  - proposals: one open proposal per group (cleared after booking or timeout)

Proposal timeout: if an open proposal is older than PROPOSAL_TIMEOUT_SECONDS it
is treated as expired and cleared on read.
TODO: add explicit expiry enforcement (e.g. 5-minute timeout on open proposals).
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path("/data/memory.db")

# TODO: make proposal timeout configurable
PROPOSAL_TIMEOUT_SECONDS = 300  # 5 minutes


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id  TEXT    NOT NULL,
            role      TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            ts        TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts);

        CREATE TABLE IF NOT EXISTS proposals (
            group_id  TEXT PRIMARY KEY,
            data      TEXT NOT NULL,
            ts        TEXT NOT NULL
        );
    """)
    conn.commit()


_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _init_db(_conn)
    return _conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(group_id: str, role: str, content: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (group_id, role, content, ts) VALUES (?, ?, ?, ?)",
        (group_id, role, content, _now_iso()),
    )
    conn.commit()


def history(group_id: str, max_turns: int, max_minutes: int) -> list[dict[str, str]]:
    """Return the most recent conversation turns for a group.

    Applies whichever limit is more restrictive: the last max_turns rows
    OR rows from the last max_minutes minutes — whichever yields fewer rows.
    """
    conn = get_conn()
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(minutes=max_minutes)).isoformat()

    rows_by_turns = conn.execute(
        """
        SELECT role, content FROM (
            SELECT role, content, ts FROM messages
            WHERE group_id = ?
            ORDER BY ts DESC
            LIMIT ?
        ) ORDER BY ts ASC
        """,
        (group_id, max_turns),
    ).fetchall()

    rows_by_time = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE group_id = ? AND ts >= ?
        ORDER BY ts ASC
        """,
        (group_id, cutoff_ts),
    ).fetchall()

    # Use whichever window is smaller (more restrictive)
    chosen = rows_by_turns if len(rows_by_turns) <= len(rows_by_time) else rows_by_time
    return [{"role": r["role"], "content": r["content"]} for r in chosen]


def set_open_proposal(group_id: str, proposal: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO proposals (group_id, data, ts) VALUES (?, ?, ?)
        ON CONFLICT(group_id) DO UPDATE SET data=excluded.data, ts=excluded.ts
        """,
        (group_id, json.dumps(proposal, ensure_ascii=False), _now_iso()),
    )
    conn.commit()


def get_open_proposal(group_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT data, ts FROM proposals WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    # Expire stale proposals
    # TODO: surface timeout as a config option
    ts = datetime.fromisoformat(row["ts"])
    if datetime.now(timezone.utc) - ts > timedelta(seconds=PROPOSAL_TIMEOUT_SECONDS):
        clear_open_proposal(group_id)
        return None
    return json.loads(row["data"])


def clear_open_proposal(group_id: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM proposals WHERE group_id = ?", (group_id,))
    conn.commit()
