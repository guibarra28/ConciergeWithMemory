"""Who may see which memory. The single place where visibility (R6) is decided.

Every path that puts memories in front of the model or a person goes through
memories_for(). Filtering happens here, in SQL, before the model is called; the model is
never asked to keep a secret it was given.

| Visibility | Owner's own conversation | Matching for intros | Manager (team) |
| ---------- | ------------------------ | ------------------- | -------------- |
| private    | yes                      | never               | never          |
| shareable  | yes                      | yes, if not sensitive | yes, if not sensitive |
| internal   | yes                      | never               | yes            |

Other members never get memories from here. They only see what the owner selects when
consenting to a specific intro (workflow.shared_items).
"""

from __future__ import annotations

import sqlite3
from typing import Literal

Purpose = Literal["own_conversation", "matching", "team"]

NOT_EXPIRED = "(valid_until IS NULL OR valid_until > datetime('now'))"


class PermissionDenied(Exception):
    pass


def memories_for(
    conn: sqlite3.Connection,
    viewer: sqlite3.Row | None,
    purpose: Purpose,
    owner_id: int | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    if purpose == "own_conversation":
        if viewer is None or owner_id not in (None, viewer["id"]):
            raise PermissionDenied("A conversation can only use the speaker's own memories.")
        return conn.execute(
            f"""SELECT * FROM memories WHERE member_id = ? AND {NOT_EXPIRED}
                ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (viewer["id"], limit),
        ).fetchall()

    if purpose == "matching":
        if owner_id is None:
            raise PermissionDenied("Matching reads one candidate at a time.")
        return conn.execute(
            f"""SELECT * FROM memories WHERE member_id = ? AND visibility = 'shareable'
                AND sensitive = 0 AND {NOT_EXPIRED} ORDER BY id LIMIT ?""",
            (owner_id, limit),
        ).fetchall()

    if purpose == "team":
        if viewer is None or viewer["role"] != "manager":
            raise PermissionDenied("Only relationship managers have the team view.")
        owner_filter = "AND m.member_id = ?" if owner_id is not None else ""
        params = (owner_id, limit) if owner_id is not None else (limit,)
        return conn.execute(
            f"""SELECT m.*, p.display_name AS owner_name FROM memories m
                JOIN members p ON p.id = m.member_id
                WHERE p.role = 'member' AND m.sensitive = 0
                  AND m.visibility IN ('shareable', 'internal') AND {NOT_EXPIRED.replace('valid_until', 'm.valid_until')}
                  {owner_filter}
                ORDER BY p.display_name, m.id LIMIT ?""",
            params,
        ).fetchall()

    raise PermissionDenied(f"Unknown purpose: {purpose}")
