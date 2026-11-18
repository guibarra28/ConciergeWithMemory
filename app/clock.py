"""Simulated clock for long-running workflows.

The prototype has an "Advance 1 day" button, so deadlines, reminders and expirations
can be demonstrated in minutes. The clock is real UTC time plus an offset in days,
stored in the database so every session and restart sees the same time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%d %H:%M:%S"  # same format SQLite's datetime() produces, so strings compare correctly


def offset_days(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'clock_offset_days'").fetchone()
    return int(row["value"]) if row else 0


def now(conn: sqlite3.Connection) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) + timedelta(days=offset_days(conn))


def now_str(conn: sqlite3.Connection) -> str:
    return now(conn).strftime(FMT)


def in_days(conn: sqlite3.Connection, days: float) -> str:
    return (now(conn) + timedelta(days=days)).strftime(FMT)


def advance(conn: sqlite3.Connection, days: int = 1) -> None:
    conn.execute(
        """INSERT INTO settings (key, value) VALUES ('clock_offset_days', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (str(offset_days(conn) + days),),
    )
    conn.commit()


def parse(value: str) -> datetime:
    return datetime.strptime(value[:19], FMT)
