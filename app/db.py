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
    {
        "display_name": "Carlos Lima",
        "role": "member",
        "company": "Rota Logística",
        "title": "CFO",
        "identities": [
            ("chat", "carlos", True),
            ("email", "carlos@rota-logistica.example", True),
        ],
    },
    {
        "display_name": "Marina Alves",
        "role": "member",
        "company": "VisionQC",
        "title": "Founder and CEO",
        "identities": [
            ("chat", "marina", True),
            ("email", "marina@visionqc.example", True),
        ],
    },
]

# Memories other members already have, so intro matching has something to work with.
# (display_name, content, category, visibility). The private one must never leak (c1).
# (person, content, category, visibility, days since last confirmed)
SEED_MEMORIES = [
    ("Ana Souza", "Led an AI rollout in customer service operations", "expertise", "shareable", 0),
    ("Ana Souza", "Wants to meet manufacturing leaders working on automation", "interest", "shareable", 0),
    ("Ana Souza", "Attended the March event in São Paulo", "interest", "internal", 0),
    # Old enough to be flagged "Still true?" in Ana's memory panel (R8).
    ("Ana Souza", "Leads a customer service team of 400 people", "role", "private", 240),
    ("Carlos Lima", "Implemented computer vision for package inspection in warehouses", "expertise", "shareable", 0),
    ("Carlos Lima", "Is considering leaving his job next year", "goal", "private", 0),
    ("Marina Alves", "Sells AI quality-control software to manufacturers", "company", "shareable", 0),
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
    """Create tables if missing, upgrade old databases and add any missing demo people."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    if seed:
        _seed(conn)
        _seed_demo_passwords(conn)
    conn.commit()


# (table, column, definition) added after the first version of the schema.
MIGRATIONS = [
    ("messages", "context_excluded",
     "INTEGER NOT NULL DEFAULT 0 CHECK (context_excluded IN (0, 1))"),
    ("intro_workflows", "suggestions", "TEXT"),
    ("action_items", "reminded_at", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created (CREATE TABLE IF NOT EXISTS won't)."""
    for table, column, definition in MIGRATIONS:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _seed_demo_passwords(conn: sqlite3.Connection) -> None:
    from app.auth import demo_password, set_password

    for row in conn.execute(
        "SELECT id FROM members WHERE id NOT IN (SELECT member_id FROM credentials)"
    ).fetchall():
        set_password(conn, row["id"], demo_password())


def _seed(conn: sqlite3.Connection) -> None:
    """Insert demo people that don't exist yet (by name), with their identities and memories."""
    existing = {r["display_name"] for r in conn.execute("SELECT display_name FROM members")}
    new_ids: dict[str, int] = {}
    for m in SEED_MEMBERS:
        if m["display_name"] in existing:
            continue
        cur = conn.execute(
            "INSERT INTO members (display_name, role, company, title) VALUES (?, ?, ?, ?)",
            (m["display_name"], m["role"], m["company"], m["title"]),
        )
        new_ids[m["display_name"]] = cur.lastrowid
        for i, (channel, address, verified) in enumerate(m["identities"]):
            conn.execute(
                """INSERT INTO identities (member_id, channel_id, address, verified, is_primary)
                   VALUES (?, ?, ?, ?, ?)""",
                (cur.lastrowid, channel_id(conn, channel), address.lower(), int(verified), int(i == 0)),
            )
    for name, content, category, visibility, age_days in SEED_MEMORIES:
        if name in new_ids:
            age = f"-{age_days} days"
            conn.execute(
                """INSERT INTO memories (member_id, content, category, visibility, confidence,
                                         last_confirmed_at, created_at)
                   VALUES (?, ?, ?, ?, 1.0, datetime('now', ?), datetime('now', ?))""",
                (new_ids[name], content, category, visibility, age, age),
            )


def channel_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM channels WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown channel: {name}")
    return row["id"]


def get_chat_login(conn: sqlite3.Connection, member_id: int) -> str | None:
    row = conn.execute(
        """SELECT i.address FROM identities i JOIN channels c ON c.id = i.channel_id
           WHERE i.member_id = ? AND c.name = 'chat' AND i.verified = 1
           ORDER BY i.is_primary DESC, i.id LIMIT 1""",
        (member_id,),
    ).fetchone()
    return row["address"] if row else None


def list_email_identities(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT i.address, i.verified FROM identities i JOIN channels c ON c.id = i.channel_id
           WHERE i.member_id = ? AND c.name = 'email'
           ORDER BY i.verified DESC, i.is_primary DESC, i.id""",
        (member_id,),
    ).fetchall()


def list_demo_logins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Primary verified email of every person who has a password, for the demo login hint."""
    return conn.execute(
        """SELECT m.display_name, m.role, i.address
           FROM members m
           JOIN credentials cr ON cr.member_id = m.id
           JOIN identities i ON i.member_id = m.id AND i.verified = 1
           JOIN channels c ON c.id = i.channel_id AND c.name = 'email'
           WHERE i.id = (SELECT MIN(i2.id) FROM identities i2
                         WHERE i2.member_id = m.id AND i2.channel_id = c.id AND i2.verified = 1)
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
    from app import clock

    cur = conn.execute(
        """INSERT INTO messages
           (member_id, channel_id, direction, sender_address, body, quarantined, used_memory_ids, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            member_id,
            channel_id(conn, channel),
            direction,
            sender_address,
            body,
            int(quarantined),
            json.dumps(used_memory_ids) if used_memory_ids else None,
            clock.now_str(conn),  # simulated clock, so "Advance 1 day" shows in the timeline
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_timeline(
    conn: sqlite3.Connection, member_id: int, limit: int | None = None, for_model: bool = False
) -> list[sqlite3.Row]:
    """All messages of one member, across channels, oldest first.

    With for_model=True, messages excluded after a memory was deleted are left out.
    """
    rows = conn.execute(
        f"""SELECT msg.id, msg.direction, msg.body, msg.sender_address, msg.created_at,
                   msg.used_memory_ids, msg.context_excluded, c.name AS channel
            FROM messages msg JOIN channels c ON c.id = msg.channel_id
            WHERE msg.member_id = ? AND msg.quarantined = 0
                  {"AND msg.context_excluded = 0" if for_model else ""}
            ORDER BY msg.id DESC
            LIMIT ?""",
        (member_id, limit if limit is not None else -1),
    ).fetchall()
    return list(reversed(rows))


def exclude_exchange_from_context(conn: sqlite3.Connection, member_id: int, inbound_message_id: int) -> None:
    """Hide a member message and the concierge reply right after it from the model."""
    conn.execute(
        """UPDATE messages SET context_excluded = 1
           WHERE member_id = ? AND (
               id = ?
               OR id = (SELECT MIN(id) FROM messages
                        WHERE member_id = ? AND id > ? AND direction = 'outbound')
           )""",
        (member_id, inbound_message_id, member_id, inbound_message_id),
    )


def count_quarantined(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE quarantined = 1").fetchone()[0]


def first_manager_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM members WHERE role = 'manager' ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def add_notification(conn: sqlite3.Connection, member_id: int, body: str, workflow_id: int | None = None) -> None:
    """Does not commit: callers include it in their own transaction."""
    from app import clock

    conn.execute(
        "INSERT INTO notifications (member_id, workflow_id, body, created_at) VALUES (?, ?, ?, ?)",
        (member_id, workflow_id, body, clock.now_str(conn)),
    )


def list_notifications(conn: sqlite3.Connection, member_id: int, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notifications WHERE member_id = ? ORDER BY id DESC LIMIT ?", (member_id, limit)
    ).fetchall()
