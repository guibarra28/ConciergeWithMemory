"""Action items extracted from conversations, each with an owner and a due date (R7)."""

from __future__ import annotations

import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from app import clock, db

DEFAULT_DUE_DAYS = 7
MAX_DUE_DAYS = 90


class ActionItemCandidate(BaseModel):
    description: str = Field(description="Short imperative sentence in English, e.g. 'Send the QC deck to Ana'.")
    owner: Literal["member", "team"] = Field(
        description="'member' if the member committed to do it, 'team' if they asked the community team to do it."
    )
    due_in_days: int | None = Field(description="Days until due if the member gave a time; null otherwise.")
    source_excerpt: str = Field(description="Exact words from the member's message with the commitment.")


def save_candidates(
    conn: sqlite3.Connection,
    member_id: int,
    candidates: list[ActionItemCandidate],
    message_text: str,
    source_message_id: int | None,
) -> list[sqlite3.Row]:
    """Save explicit commitments. Same guard as memories: the excerpt must be in the message."""
    saved = []
    open_items = {_words(r["description"]) for r in list_open(conn, member_id)}
    message_words = _words(message_text)
    for c in candidates:
        excerpt = _words(c.source_excerpt)
        if not excerpt or excerpt not in message_words or _words(c.description) in open_items:
            continue
        owner_id = member_id if c.owner == "member" else (db.first_manager_id(conn) or member_id)
        days = c.due_in_days if c.due_in_days and 0 < c.due_in_days <= MAX_DUE_DAYS else DEFAULT_DUE_DAYS
        cur = conn.execute(
            """INSERT INTO action_items (member_id, owner_id, description, due_date, source_message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (member_id, owner_id, c.description.strip(), clock.in_days(conn, days),
             source_message_id, clock.now_str(conn)),
        )
        saved.append(conn.execute("SELECT * FROM action_items WHERE id = ?", (cur.lastrowid,)).fetchone())
        open_items.add(_words(c.description))
    conn.commit()
    return saved


def list_open(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    """Open items from this member's conversations."""
    return conn.execute(
        """SELECT a.*, o.display_name AS owner_name FROM action_items a JOIN members o ON o.id = a.owner_id
           WHERE a.member_id = ? AND a.status = 'open' ORDER BY a.due_date""",
        (member_id,),
    ).fetchall()


def list_owned_by(conn: sqlite3.Connection, owner_id: int) -> list[sqlite3.Row]:
    """Open items a person must do (used for the manager). Only the description is shown."""
    return conn.execute(
        """SELECT a.id, a.description, a.due_date, m.display_name AS member_name
           FROM action_items a JOIN members m ON m.id = a.member_id
           WHERE a.owner_id = ? AND a.status = 'open' ORDER BY a.due_date""",
        (owner_id,),
    ).fetchall()


def set_status(conn: sqlite3.Connection, item_id: int, actor_id: int, status: str) -> bool:
    """The member whose conversation it came from, or the owner, can close an item."""
    if status not in ("done", "cancelled"):
        raise ValueError("Status must be done or cancelled.")
    cur = conn.execute(
        """UPDATE action_items SET status = ?, completed_at = ?
           WHERE id = ? AND status = 'open' AND (member_id = ? OR owner_id = ?)""",
        (status, clock.now_str(conn), item_id, actor_id, actor_id),
    )
    conn.commit()
    return cur.rowcount == 1


def update_due_date(conn: sqlite3.Connection, item_id: int, member_id: int, due_date: str) -> bool:
    cur = conn.execute(
        "UPDATE action_items SET due_date = ? WHERE id = ? AND member_id = ? AND status = 'open'",
        (due_date, item_id, member_id),
    )
    conn.commit()
    return cur.rowcount == 1


def remind_due(conn: sqlite3.Connection) -> int:
    """Notify owners of open items due within a day, once per item."""
    soon = clock.in_days(conn, 1)
    count = 0
    for item in conn.execute(
        "SELECT * FROM action_items WHERE status = 'open' AND due_date <= ? AND reminded_at IS NULL",
        (soon,),
    ).fetchall():
        db.add_notification(conn, item["owner_id"],
                            f"Action item due {clock.parse(item['due_date']):%a %d %b}: {item['description']}")
        conn.execute("UPDATE action_items SET reminded_at = ? WHERE id = ?", (clock.now_str(conn), item["id"]))
        count += 1
    conn.commit()
    return count


def _words(text: str) -> str:
    words = re.findall(r"\w+", (text or "").lower())
    return f" {' '.join(words)} " if words else ""
