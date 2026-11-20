"""Memory Service: extract, store, retrieve and let the member control memories.

Policy: docs/memory_policy.md. The rules that protect the member are enforced here in
code, not left to the model:
- a candidate is saved only if its supporting excerpt is in the message, word for word or
  with all its words in one sentence (the model cannot save something the member did not say);
- a candidate from the same sentence as a health fact is held for the same confirmation;
- a candidate that restates a commitment is left to the action items, not saved as a memory;
- low-confidence candidates are dropped;
- sensitive candidates are never saved without the member's explicit confirmation;
- nothing is saved while memory is paused;
- an outdated memory is replaced only if it is the member's own, not sensitive and not a
  team note, and the replacement starts private again;
- every read and write is scoped to one member_id.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app import actions, clock, db, permissions
from app.actions import ActionItemCandidate
from app.llm import call_llm

Category = Literal["role", "company", "goal", "preference", "interest", "expertise", "personal", "health"]
SENSITIVE_CATEGORIES = {"personal", "health"}
MEMBER_VISIBILITIES = ("private", "shareable")  # 'internal' is set by the team only
MIN_CONFIDENCE = 0.7
CONTEXT_LIMIT = 50
STALE_AFTER_DAYS = 180  # R8: ask the member to confirm memories older than this

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
- If the message changes or contradicts a known memory (a new company or role, a changed
  plan or preference), propose the new fact and list in `replaces_memory_ids` the ids of
  every known memory it makes outdated (often more than one, e.g. both the old role and
  the old company). Otherwise return an empty list.
- `confidence` is how explicitly the member stated it: 1.0 for a direct statement.
- Write `content` as a short third-person sentence in English, e.g. "Wants to automate quality control in 2027".
- It is normal to return no candidates.

Also extract action items: explicit commitments in the message, such as "I'll send you the
deck on Friday" (owner: member) or "can you book a call with Ana next week?" (owner: team).
Only explicit commitments; an intro request is not an action item. `source_excerpt` must be
copied word for word. It is normal to return no action items."""


class MemoryCandidate(BaseModel):
    content: str = Field(description="Short third-person sentence in English.")
    category: Category
    confidence: float = Field(description="0 to 1. How explicitly the member stated it.")
    sensitive: bool
    source_excerpt: str = Field(description="Exact words from the member's message that state this fact.")
    expires_in_days: int | None = Field(description="Days until a temporary fact stops being true; null if durable.")
    replaces_memory_ids: list[int] = Field(
        default_factory=list, description="Ids of known memories this fact makes outdated; empty if none.")


class ExtractionResult(BaseModel):
    candidates: list[MemoryCandidate]
    action_items: list[ActionItemCandidate]


@dataclass
class ExtractionOutcome:
    saved: list[sqlite3.Row]
    needs_confirmation: list[MemoryCandidate]
    discarded: list[tuple[MemoryCandidate, str]]  # (candidate, reason) for debugging and evals
    action_items: list[sqlite3.Row] = field(default_factory=list)
    # (memory after the update, the memories it replaced as they were) so the interface can offer Undo.
    updated: list[tuple[sqlite3.Row, list[dict]]] = field(default_factory=list)


# --- Extraction --------------------------------------------------------------
def extract_and_save(
    conn: sqlite3.Connection, member_id: int, message_text: str, source_message_id: int, channel: str
) -> ExtractionOutcome:
    """One model call extracts memory candidates and action items.

    While memory is paused the call still runs, because action items are not memories,
    but every memory candidate is ignored.
    """
    known = list_memories(conn, member_id)
    known_text = "\n".join(f"- [{m['id']}] {m['content']}" for m in known) or "(none)"
    result = call_llm(
        EXTRACTION_PROMPT,
        [{"role": "user", "content": f"Known memories:\n{known_text}\n\nMember's message:\n{message_text}"}],
        ExtractionResult,
    )
    if is_paused(conn, member_id):
        outcome = ExtractionOutcome([], [], [(c, "memory paused") for c in result.candidates])
    else:
        outcome = apply_candidates(conn, member_id, result.candidates, message_text, source_message_id, channel,
                                   commitments=[a.source_excerpt for a in result.action_items])
    outcome.action_items = actions.save_candidates(
        conn, member_id, result.action_items, message_text, source_message_id)
    return outcome


def apply_candidates(
    conn: sqlite3.Connection,
    member_id: int,
    candidates: list[MemoryCandidate],
    message_text: str,
    source_message_id: int | None,
    channel: str,
    commitments: list[str] = (),
) -> ExtractionOutcome:
    outcome = ExtractionOutcome([], [], [])
    existing = {_norm(m["content"]) for m in list_memories(conn, member_id)}
    commitments = [x for x in commitments if _excerpt_found(x, message_text)]
    # Sentences that carry a health fact: everything else said in them is held too, so it can't
    # slip through reworded as a plain preference ("wants to travel less"). Health only: applied
    # to every sensitive category, it also held ordinary facts said next to "I travel a lot".
    health_sentences = {
        _sentence_index(message_text, c.source_excerpt) for c in candidates
        if c.category == "health" and _excerpt_found(c.source_excerpt, message_text)
    }

    for c in candidates:
        if c.confidence < MIN_CONFIDENCE:
            outcome.discarded.append((c, "low confidence"))
        elif not _excerpt_found(c.source_excerpt, message_text):
            outcome.discarded.append((c, "excerpt not found in message"))
        elif _norm(c.content) in existing:
            outcome.discarded.append((c, "duplicate"))
        elif any(_restates(c.source_excerpt, x) for x in commitments):
            outcome.discarded.append((c, "commitment: kept as an action item"))
        elif c.sensitive or c.category in SENSITIVE_CATEGORIES:
            outcome.needs_confirmation.append(c)
        elif _sentence_index(message_text, c.source_excerpt) in health_sentences:
            outcome.needs_confirmation.append(c.model_copy(update={"sensitive": True}))
        elif olds := _replaceable(conn, member_id, c.replaces_memory_ids):
            outcome.updated.append((_replace(conn, member_id, olds, c, source_message_id, channel),
                                    [dict(o) for o in olds]))
            existing.add(_norm(c.content))
        else:
            outcome.saved.append(_insert(conn, member_id, c, source_message_id, channel, sensitive=False))
            existing.add(_norm(c.content))
    return outcome


def _replaceable(conn, member_id: int, memory_ids: list[int]) -> list[sqlite3.Row]:
    """Memories a candidate may replace: the member's own, not sensitive, not team notes."""
    olds = []
    for memory_id in dict.fromkeys(memory_ids):
        old = get_memory(conn, member_id, memory_id)  # scoped to this member: no cross-member ids
        if old is not None and not old["sensitive"] and old["visibility"] != "internal":
            olds.append(old)
    return olds


def _replace(conn, member_id: int, olds: list[sqlite3.Row], c: MemoryCandidate, source_message_id,
             channel) -> sqlite3.Row:
    """Update the first outdated memory in place and remove the others.

    The result is private: agreeing to share the old fact was not agreeing to share the new one.
    """
    from app import workflow  # imported here: workflow does not depend on memory

    old, extra = olds[0], olds[1:]
    for e in extra:
        conn.execute("DELETE FROM memories WHERE id = ? AND member_id = ?", (e["id"], member_id))
        workflow.forget_shared_memory(conn, member_id, e["id"])
    expires = f"+{c.expires_in_days} days" if c.expires_in_days and c.expires_in_days > 0 else None
    now = clock.now_str(conn)
    conn.execute(
        """UPDATE memories
           SET content = ?, category = ?, confidence = ?, visibility = 'private',
               source_message_id = ?, source_excerpt = ?, channel_id = ?,
               valid_until = CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END,
               last_confirmed_at = ?, updated_at = ?
           WHERE id = ? AND member_id = ?""",
        (c.content.strip(), c.category, c.confidence, source_message_id, c.source_excerpt.strip(),
         db.channel_id(conn, channel), expires, expires, now, now, old["id"], member_id),
    )
    conn.commit()
    return get_memory(conn, member_id, old["id"])


def undo_replace(conn: sqlite3.Connection, member_id: int, previous: list[dict]) -> bool:
    """Put the replaced memories back as they were before an automatic update."""
    first, removed = previous[0], previous[1:]
    for r in removed:
        taken = conn.execute("SELECT 1 FROM memories WHERE id = ?", (r["id"],)).fetchone()
        conn.execute(
            """INSERT INTO memories (id, member_id, content, category, sensitive, visibility, confidence,
                                     source_message_id, source_excerpt, channel_id, valid_until,
                                     last_confirmed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (None if taken else r["id"], member_id, r["content"], r["category"], r["sensitive"],
             r["visibility"], r["confidence"], r["source_message_id"], r["source_excerpt"],
             r["channel_id"], r["valid_until"], r["last_confirmed_at"], r["created_at"], clock.now_str(conn)),
        )
    previous = first
    cur = conn.execute(
        """UPDATE memories
           SET content = ?, category = ?, confidence = ?, visibility = ?, source_message_id = ?,
               source_excerpt = ?, channel_id = ?, valid_until = ?, last_confirmed_at = ?, updated_at = ?
           WHERE id = ? AND member_id = ?""",
        (previous["content"], previous["category"], previous["confidence"], previous["visibility"],
         previous["source_message_id"], previous["source_excerpt"], previous["channel_id"],
         previous["valid_until"], previous["last_confirmed_at"], clock.now_str(conn),
         previous["id"], member_id),
    )
    conn.commit()
    return cur.rowcount == 1


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
    """Memories the agent may use in this member's own conversation (app/permissions.py)."""
    return permissions.memories_for(conn, db.get_member(conn, member_id), "own_conversation",
                                    limit=CONTEXT_LIMIT)


def format_for_prompt(memories: list[sqlite3.Row], conn: sqlite3.Connection | None = None) -> str:
    if not memories:
        return "(no memories yet)"
    stale_ids = {m["id"] for m in memories if conn is not None and is_stale(conn, m)}
    lines = [f"[{m['id']}] ({m['category']}) {m['content']}" for m in memories if m["id"] not in stale_ids]
    if stale_ids:
        lines.append("\nPossibly out of date (not confirmed in over 6 months):")
        lines += [f"[{m['id']}] ({m['category']}) {m['content']}" for m in memories if m["id"] in stale_ids]
    return "\n".join(lines)


# --- Staleness (R8) ----------------------------------------------------------
def is_stale(conn: sqlite3.Connection, memory: sqlite3.Row) -> bool:
    confirmed = clock.parse(memory["last_confirmed_at"])
    return clock.now(conn) - confirmed >= timedelta(days=STALE_AFTER_DAYS)


def confirm_still_true(conn: sqlite3.Connection, member_id: int, memory_id: int) -> bool:
    now = clock.now_str(conn)
    cur = conn.execute(
        "UPDATE memories SET last_confirmed_at = ?, updated_at = ? WHERE id = ? AND member_id = ?",
        (now, now, memory_id, member_id),
    )
    conn.commit()
    return cur.rowcount == 1


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
               last_confirmed_at = ?, updated_at = ?
           WHERE id = ? AND member_id = ?""",
        (content.strip() if content else None, visibility, clock.now_str(conn), clock.now_str(conn),
         memory_id, member_id),
    )
    conn.commit()
    return True


def delete_memory(conn: sqlite3.Connection, member_id: int, memory_id: int) -> bool:
    """Delete the memory and stop the model from reading the exchange it came from.

    Without the second step the model would still "remember" the fact from the
    conversation history, and deleting would not really make it forget.
    """
    from app import workflow  # imported here: workflow does not depend on memory

    memory = get_memory(conn, member_id, memory_id)
    if memory is None:
        return False
    conn.execute("DELETE FROM memories WHERE id = ? AND member_id = ?", (memory_id, member_id))
    workflow.forget_shared_memory(conn, member_id, memory_id)
    if memory["source_message_id"]:
        db.exclude_exchange_from_context(conn, member_id, memory["source_message_id"])
    conn.commit()
    return True


def delete_everything(conn: sqlite3.Connection, member_id: int) -> None:
    """Remove all memories and the whole timeline of this member (privacy limit 4).

    Open intros are cancelled first, with a neutral notice to the other side; intro records
    keep only ids, states and dates as proof that consent existed (model.md).
    """
    from app import workflow  # imported here: workflow does not depend on memory

    workflow.cancel_all_for_member(conn, member_id)
    conn.execute("DELETE FROM notifications WHERE member_id = ?", (member_id,))
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


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _sentence_index(message: str, excerpt: str) -> int | None:
    """Which sentence of the message the excerpt comes from (None if none holds all its words)."""
    words = _words(excerpt)
    for i, sentence in enumerate(_sentences(message)):
        sentence_words = _words(sentence)
        if words and (words in sentence_words or set(words.split()) <= set(sentence_words.split())):
            return i
    return None


def _excerpt_found(excerpt: str, message: str) -> bool:
    """Word for word, or every word of the excerpt in one sentence of the message.

    The second form accepts a reordered quote ("I now run" for "now I run") without letting
    the model assemble a fact from words scattered across the message.
    """
    words = _words(excerpt)
    if not words:
        return False
    return words in _words(message) or _sentence_index(message, excerpt) is not None


def _restates(excerpt: str, commitment: str) -> bool:
    """Most of the memory's excerpt is the commitment's excerpt: it's the same statement."""
    mine, theirs = set(_words(excerpt).split()), set(_words(commitment).split())
    return bool(mine) and len(mine & theirs) >= 0.6 * len(mine)


def _words(text: str) -> str:
    """Lowercase words only, so punctuation the model adds or drops doesn't matter.

    Padded with spaces so a match is always on whole words ("CEO" does not match "CEOs").
    """
    words = re.findall(r"\w+", (text or "").lower())
    return f" {' '.join(words)} " if words else ""
