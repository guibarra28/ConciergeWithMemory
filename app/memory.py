"""Memory Service: extract, store, retrieve and let the member control memories.

Policy: docs/memory_policy.md. The rules that protect the member are enforced here in
code, not left to the model:
- a candidate is saved only if its supporting excerpt is literally in the message
  (the model cannot save something the member did not say);
- low-confidence candidates are dropped;
- sensitive candidates are never saved without the member's explicit confirmation;
- nothing is saved while memory is paused;
- every read and write is scoped to one member_id.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app import db
from app.llm import call_llm

Category = Literal["role", "company", "goal", "preference", "interest", "expertise", "personal", "health"]
SENSITIVE_CATEGORIES = {"personal", "health"}
MEMBER_VISIBILITIES = ("private", "shareable")  # 'internal' is set by the team only
MIN_CONFIDENCE = 0.7
CONTEXT_LIMIT = 50

EXTRACTION_PROMPT = """You extract durable facts that a member of an executive community
explicitly stated about themselves, so a concierge can remember them later.

Rules:
- Only facts the member clearly stated about themselves. Never infer, guess or generalize.
- Only durable facts (role, company, goals, preferences, interests, expertise). Skip
  small talk, questions and one-off requests.
- Preferences about who the member wants or does not want to meet (e.g. "no vendors")
  are important; always capture them.
- `source_excerpt` must be copied word for word from the member's message.
- Mark `sensitive` true for personal life (family, relationships, finances) and health,
  including facts about the member's family. Propose them anyway; the member will be
  asked before anything sensitive is saved.
- If the fact is temporary ("these months", "until March", "this quarter"), set
  `expires_in_days` to a reasonable estimate. Otherwise set it to null.
- Do not propose facts already in the list of known memories.
- `confidence` is how explicitly the member stated it: 1.0 for a direct statement.
- Write `content` as a short third-person sentence in English, e.g. "Wants to automate quality control in 2027".
- It is normal to return no candidates."""


class MemoryCandidate(BaseModel):
    content: str = Field(description="Short third-person sentence in English.")
    category: Category
    confidence: float = Field(description="0 to 1. How explicitly the member stated it.")
    sensitive: bool
    source_excerpt: str = Field(description="Exact words from the member's message that state this fact.")
    expires_in_days: int | None = Field(description="Days until a temporary fact stops being true; null if durable.")


class ExtractionResult(BaseModel):
    candidates: list[MemoryCandidate]


@dataclass
class ExtractionOutcome:
    saved: list[sqlite3.Row]
    needs_confirmation: list[MemoryCandidate]
    discarded: list[tuple[MemoryCandidate, str]]  # (candidate, reason) for debugging and evals


# --- Extraction --------------------------------------------------------------
def extract_and_save(
    conn: sqlite3.Connection, member_id: int, message_text: str, source_message_id: int, channel: str
) -> ExtractionOutcome:
    if is_paused(conn, member_id):
        return ExtractionOutcome([], [], [])

    known = list_memories(conn, member_id)
    known_text = "\n".join(f"- {m['content']}" for m in known) or "(none)"
    result = call_llm(
        EXTRACTION_PROMPT,
        [{"role": "user", "content": f"Known memories:\n{known_text}\n\nMember's message:\n{message_text}"}],
        ExtractionResult,
    )
    return apply_candidates(conn, member_id, result.candidates, message_text, source_message_id, channel)


def apply_candidates(
    conn: sqlite3.Connection,
    member_id: int,
    candidates: list[MemoryCandidate],
    message_text: str,
    source_message_id: int | None,
    channel: str,
) -> ExtractionOutcome:
    outcome = ExtractionOutcome([], [], [])
    existing = {_norm(m["content"]) for m in list_memories(conn, member_id)}
    message_words = _words(message_text)

    for c in candidates:
        if c.confidence < MIN_CONFIDENCE:
            outcome.discarded.append((c, "low confidence"))
        elif not _words(c.source_excerpt) or _words(c.source_excerpt) not in message_words:
            outcome.discarded.append((c, "excerpt not found in message"))
        elif _norm(c.content) in existing:
            outcome.discarded.append((c, "duplicate"))
        elif c.sensitive or c.category in SENSITIVE_CATEGORIES:
            outcome.needs_confirmation.append(c)
        else:
            outcome.saved.append(_insert(conn, member_id, c, source_message_id, channel, sensitive=False))
            existing.add(_norm(c.content))
    return outcome


def confirm_sensitive(
    conn: sqlite3.Connection, member_id: int, candidate: MemoryCandidate, source_message_id: int | None, channel: str
) -> sqlite3.Row | None:
    """Save a sensitive candidate after the member explicitly said yes. Always private."""
    if is_paused(conn, member_id):
        return None
    return _insert(conn, member_id, candidate, source_message_id, channel, sensitive=True)


def _insert(conn, member_id, c: MemoryCandidate, source_message_id, channel, sensitive: bool) -> sqlite3.Row:
    expires = f"+{c.expires_in_days} days" if c.expires_in_days and c.expires_in_days > 0 else None
    cur = conn.execute(
        """INSERT INTO memories (member_id, content, category, sensitive, visibility, confidence,
                                 source_message_id, source_excerpt, channel_id, valid_until)
           VALUES (?, ?, ?, ?, 'private', ?, ?, ?, ?,
                   CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END)""",
        (member_id, c.content.strip(), c.category, int(sensitive), c.confidence,
         source_message_id, c.source_excerpt.strip(), db.channel_id(conn, channel), expires, expires),
    )
    conn.commit()
    return get_memory(conn, member_id, cur.lastrowid)


# --- Retrieval ---------------------------------------------------------------
def get_context(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    """Memories the agent may use in this member's own conversation.

    Only this member's memories; expired ones are left out. Every visibility is allowed
    here because the member is talking about themselves. Other purposes (intros, manager
    view) get their own filtered functions in later blocks.
    """
    return conn.execute(
        """SELECT * FROM memories
           WHERE member_id = ? AND (valid_until IS NULL OR valid_until > datetime('now'))
           ORDER BY updated_at DESC, id DESC
           LIMIT ?""",
        (member_id, CONTEXT_LIMIT),
    ).fetchall()


def format_for_prompt(memories: list[sqlite3.Row]) -> str:
    if not memories:
        return "(no memories yet)"
    return "\n".join(f"[{m['id']}] ({m['category']}) {m['content']}" for m in memories)


# --- Member control (R1) -----------------------------------------------------
def list_memories(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM memories WHERE member_id = ? ORDER BY category, id", (member_id,)
    ).fetchall()


def get_memory(conn: sqlite3.Connection, member_id: int, memory_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM memories WHERE id = ? AND member_id = ?", (memory_id, member_id)
    ).fetchone()


def get_memories_by_ids(conn: sqlite3.Connection, member_id: int, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(
        f"SELECT * FROM memories WHERE member_id = ? AND id IN ({placeholders}) ORDER BY id",
        (member_id, *ids),
    ).fetchall()


def update_memory(
    conn: sqlite3.Connection, member_id: int, memory_id: int, *, content: str | None = None, visibility: str | None = None
) -> bool:
    memory = get_memory(conn, member_id, memory_id)
    if memory is None:
        return False
    if visibility is not None:
        if visibility not in MEMBER_VISIBILITIES:
            raise ValueError("Members can set visibility to private or shareable only.")
        if visibility == "shareable" and memory["sensitive"]:
            raise ValueError("Sensitive memories can't be shared.")
    if content is not None and not content.strip():
        raise ValueError("A memory can't be empty. Delete it instead.")
    conn.execute(
        """UPDATE memories
           SET content = COALESCE(?, content), visibility = COALESCE(?, visibility),
               last_confirmed_at = datetime('now'), updated_at = datetime('now')
           WHERE id = ? AND member_id = ?""",
        (content.strip() if content else None, visibility, memory_id, member_id),
    )
    conn.commit()
    return True


def delete_memory(conn: sqlite3.Connection, member_id: int, memory_id: int) -> bool:
    """Delete the memory and stop the model from reading the exchange it came from.

    Without the second step the model would still "remember" the fact from the
    conversation history, and deleting would not really make it forget.
    """
    memory = get_memory(conn, member_id, memory_id)
    if memory is None:
        return False
    conn.execute("DELETE FROM memories WHERE id = ? AND member_id = ?", (memory_id, member_id))
    if memory["source_message_id"]:
        db.exclude_exchange_from_context(conn, member_id, memory["source_message_id"])
    conn.commit()
    return True


def delete_everything(conn: sqlite3.Connection, member_id: int) -> None:
    """Remove all memories and the whole timeline of this member (privacy limit 4)."""
    conn.execute("DELETE FROM memories WHERE member_id = ?", (member_id,))
    conn.execute("DELETE FROM action_items WHERE member_id = ?", (member_id,))
    conn.execute("DELETE FROM messages WHERE member_id = ?", (member_id,))
    conn.commit()


def is_paused(conn: sqlite3.Connection, member_id: int) -> bool:
    row = conn.execute("SELECT memory_paused FROM members WHERE id = ?", (member_id,)).fetchone()
    return bool(row and row["memory_paused"])


def set_paused(conn: sqlite3.Connection, member_id: int, paused: bool) -> None:
    conn.execute("UPDATE members SET memory_paused = ? WHERE id = ?", (int(paused), member_id))
    conn.commit()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _words(text: str) -> str:
    """Lowercase words only, so punctuation the model adds or drops doesn't matter.

    Padded with spaces so a match is always on whole words ("CEO" does not match "CEOs").
    """
    words = re.findall(r"\w+", (text or "").lower())
    return f" {' '.join(words)} " if words else ""
