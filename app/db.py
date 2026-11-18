"""SQLite access: connection, schema setup, demo seed data and timeline queries."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "concierge.db"

# Fictional people from docs/discovery.md. Addresses use .example domains on purpose.
SEED_MEMBERS = [
    {
        "display_name": "Roberto Silva",
        "role": "member",
        "company": "Metalúrgica Silva",
        "title": "CEO",
        "identities": [
            ("chat", "roberto", True),
            ("email", "roberto@metalurgica-silva.example", True),
            ("email", "roberto.silva@personal.example", True),
            # Assistant address the member has NOT registered (R9 is out of scope).
            ("email", "assistant@metalurgica-silva.example", False),
        ],
    },
    {
        "display_name": "Ana Souza",
        "role": "member",
        "company": "Varejo Souza",
        "title": "COO",
        "identities": [
            ("chat", "ana", True),
            ("email", "ana@varejo-souza.example", True),
        ],
    },
    {
        "display_name": "Juliana Costa",
        "role": "manager",
        "company": None,
        "title": "Relationship manager",
        "identities": [
            ("chat", "juliana", True),
            ("email", "juliana@community.example", True),
        ],
    },
]


def get_db_path() -> Path:
    return Path(os.getenv("DB_PATH", DEFAULT_DB_PATH))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys on and rows accessible by column name."""
    path = Path(db_path) if db_path else get_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, seed: bool = True) -> None:
    """Create tables if missing and insert the demo members once."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    if seed and conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0:
        _seed(conn)
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    for m in SEED_MEMBERS:
        cur = conn.execute(
            "INSERT INTO members (display_name, role, company, title) VALUES (?, ?, ?, ?)",
            (m["display_name"], m["role"], m["company"], m["title"]),
        )
        member_id = cur.lastrowid
        for i, (channel, address, verified) in enumerate(m["identities"]):
            conn.execute(
                """INSERT INTO identities (member_id, channel_id, address, verified, is_primary)
                   VALUES (?, ?, ?, ?, ?)""",
                (member_id, channel_id(conn, channel), address.lower(), int(verified), int(i == 0)),
            )


def channel_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM channels WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown channel: {name}")
    return row["id"]


def list_people(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT m.id, m.display_name, m.role, i.address AS chat_login
           FROM members m
           JOIN identities i ON i.member_id = m.id
           JOIN channels c ON c.id = i.channel_id AND c.name = 'chat'
           WHERE i.is_primary = 1
           ORDER BY m.id"""
    ).fetchall()


def get_member(conn: sqlite3.Connection, member_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()


def add_message(
    conn: sqlite3.Connection,
    *,
    member_id: int | None,
    channel: str,
    direction: str,
    body: str,
    sender_address: str | None = None,
    quarantined: bool = False,
    used_memory_ids: list[int] | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO messages
           (member_id, channel_id, direction, sender_address, body, quarantined, used_memory_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            member_id,
            channel_id(conn, channel),
            direction,
            sender_address,
            body,
            int(quarantined),
            json.dumps(used_memory_ids) if used_memory_ids else None,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_timeline(conn: sqlite3.Connection, member_id: int, limit: int | None = None) -> list[sqlite3.Row]:
    """All messages of one member, across channels, oldest first."""
    rows = conn.execute(
        """SELECT msg.id, msg.direction, msg.body, msg.sender_address, msg.created_at, c.name AS channel
           FROM messages msg JOIN channels c ON c.id = msg.channel_id
           WHERE msg.member_id = ? AND msg.quarantined = 0
           ORDER BY msg.id DESC
           LIMIT ?""",
        (member_id, limit if limit is not None else -1),
    ).fetchall()
    return list(reversed(rows))


def count_quarantined(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE quarantined = 1").fetchone()[0]
