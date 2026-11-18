"""Resolve a sender (chat login or email address) to a member, or to unknown.

Rules (docs/model.md, docs/prd.md):
- Only verified identities resolve.
- Matching is exact after lowercasing and trimming. A similar address is unknown.
- Nothing here reads memories or messages.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedIdentity:
    member_id: int | None
    display_name: str | None = None
    role: str | None = None

    @property
    def is_known(self) -> bool:
        return self.member_id is not None


UNKNOWN = ResolvedIdentity(member_id=None)


def normalize_address(address: str) -> str:
    return (address or "").strip().lower()


def resolve(conn: sqlite3.Connection, channel: str, address: str) -> ResolvedIdentity:
    normalized = normalize_address(address)
    if not normalized:
        return UNKNOWN
    row = conn.execute(
        """SELECT m.id, m.display_name, m.role
           FROM identities i
           JOIN channels c ON c.id = i.channel_id
           JOIN members m ON m.id = i.member_id
           WHERE c.name = ? AND i.address = ? AND i.verified = 1""",
        (channel, normalized),
    ).fetchone()
    if row is None:
        return UNKNOWN
    return ResolvedIdentity(member_id=row["id"], display_name=row["display_name"], role=row["role"])
