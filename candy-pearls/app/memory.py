"""
Persistent conversation memory backed by SQLite at /data/memory.db.

Three tables:
  - messages:  per-group conversation history (role/content/timestamp)
  - proposals: one open proposal per group (cleared after booking or timeout)
  - refills:   last refill date per account (restart-safety guard)

Write functions are async and serialised through a module-level asyncio.Lock
so concurrent coroutines (inbound request handlers + refill background task)
cannot interleave their execute/commit pairs on the shared connection.
"""
import asyncio
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

_conn: sqlite3.Connection | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


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

        CREATE TABLE IF NOT EXISTS refills (
            group_id    TEXT PRIMARY KEY,
            last_date   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prices (
            name    TEXT PRIMARY KEY,
            pearls  INTEGER NOT NULL
        );
    """)
    conn.commit()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _init_db(_conn)
    return _conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Read functions (sync — no state mutation, safe under asyncio single-thread)
# ---------------------------------------------------------------------------

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


def get_open_proposal_sync(group_id: str) -> dict[str, Any] | None:
    """Sync read of the open proposal — does NOT expire it (use get_open_proposal for that)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT data, ts FROM proposals WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    ts = datetime.fromisoformat(row["ts"])
    if datetime.now(timezone.utc) - ts > timedelta(seconds=PROPOSAL_TIMEOUT_SECONDS):
        return None  # expired — caller should clear separately
    return json.loads(row["data"])


def get_last_refill_date(group_id: str) -> str | None:
    """Last local date (YYYY-MM-DD) this account's daily refill ran, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT last_date FROM refills WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    return row["last_date"] if row else None


# ---------------------------------------------------------------------------
# Write functions (async — serialised through _lock)
# ---------------------------------------------------------------------------

async def append(group_id: str, role: str, content: str) -> None:
    async with _get_lock():
        conn = get_conn()
        conn.execute(
            "INSERT INTO messages (group_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (group_id, role, content, _now_iso()),
        )
        conn.commit()


async def set_open_proposal(group_id: str, proposal: dict[str, Any]) -> None:
    async with _get_lock():
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO proposals (group_id, data, ts) VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET data=excluded.data, ts=excluded.ts
            """,
            (group_id, json.dumps(proposal, ensure_ascii=False), _now_iso()),
        )
        conn.commit()


async def get_open_proposal(group_id: str) -> dict[str, Any] | None:
    """Read + expire the open proposal atomically under the write lock."""
    async with _get_lock():
        conn = get_conn()
        row = conn.execute(
            "SELECT data, ts FROM proposals WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        ts = datetime.fromisoformat(row["ts"])
        if datetime.now(timezone.utc) - ts > timedelta(seconds=PROPOSAL_TIMEOUT_SECONDS):
            conn.execute("DELETE FROM proposals WHERE group_id = ?", (group_id,))
            conn.commit()
            return None
        return json.loads(row["data"])


async def clear_open_proposal(group_id: str) -> None:
    async with _get_lock():
        conn = get_conn()
        conn.execute("DELETE FROM proposals WHERE group_id = ?", (group_id,))
        conn.commit()


async def set_last_refill_date(group_id: str, date_str: str) -> None:
    async with _get_lock():
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO refills (group_id, last_date) VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET last_date=excluded.last_date
            """,
            (group_id, date_str),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Prices (sync read, async writes)
# ---------------------------------------------------------------------------

def get_prices() -> dict[str, int]:
    """Return the full price list sorted alphabetically by product name."""
    conn = get_conn()
    rows = conn.execute("SELECT name, pearls FROM prices ORDER BY name ASC").fetchall()
    return {row["name"]: row["pearls"] for row in rows}


async def set_price(name: str, pearls: int) -> None:
    async with _get_lock():
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO prices (name, pearls) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET pearls=excluded.pearls
            """,
            (name, pearls),
        )
        conn.commit()


async def delete_price(name: str) -> bool:
    """Delete a price entry. Returns True if it existed, False if not found."""
    async with _get_lock():
        conn = get_conn()
        cur = conn.execute("DELETE FROM prices WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0
