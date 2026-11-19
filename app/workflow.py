"""Intro workflow: the state machine in docs/model.md, persisted in SQLite.

Guarantees, enforced here and not by the model:
- Only valid transitions, by the people allowed to make them.
- `introduced` is reachable only with recorded consent from both sides (R4).
- Every transition writes one workflow_events row in the same transaction as the state change.
- A retried transition is applied once (idempotency_key) and a stale one is rejected (version).
- Unanswered steps expire after 3 days with notice (R5), driven by tick().
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app import clock, db, permissions
from app.llm import LLMError, call_llm

logger = logging.getLogger(__name__)

# --- States and rules (docs/model.md) ----------------------------------------
TERMINAL = {"completed", "no_match", "declined", "expired", "cancelled"}
OPEN = {"requested", "awaiting_a", "awaiting_b", "introduced", "scheduled", "follow_up"}

DEADLINE_DAYS = {"requested": 3, "awaiting_a": 3, "awaiting_b": 3, "introduced": 7, "follow_up": 3}
REMINDER_BEFORE_DAYS = 1
DECLINE_BLOCK_DAYS = 90
MAX_SUGGESTIONS = 3

TRANSITIONS: dict[str, dict[str, str]] = {
    "candidate_proposed": {"requested": "awaiting_a"},
    "no_match": {"requested": "no_match"},
    "a_consented": {"awaiting_a": "awaiting_b"},
    "a_declined": {"awaiting_a": "declined"},
    "a_wants_another": {"awaiting_a": "requested"},
    "b_consented": {"awaiting_b": "introduced"},
    "b_declined": {"awaiting_b": "declined"},
    "meeting_booked": {"introduced": "scheduled"},
    "outcome_recorded": {"follow_up": "completed"},
    "deadline_passed": {
        "requested": "expired", "awaiting_a": "expired", "awaiting_b": "expired",
        "introduced": "follow_up", "scheduled": "follow_up", "follow_up": "completed",
    },
    "cancelled": {s: "cancelled" for s in ("requested", "awaiting_a", "awaiting_b", "introduced", "scheduled")},
}

# Who may cause each event. "A" = requester, "B" = target, "manager" = any manager,
# "system" = the scheduler or an internal step (actor_id None).
ALLOWED: dict[str, set[str]] = {
    "candidate_proposed": {"manager"},
    "no_match": {"manager", "system"},
    "a_consented": {"A"}, "a_declined": {"A"}, "a_wants_another": {"A"},
    "b_consented": {"B"}, "b_declined": {"B"},
    "meeting_booked": {"A", "B", "manager"},
    "outcome_recorded": {"manager"},
    "deadline_passed": {"system"},
}
CANCEL_ALLOWED = {
    "requested": {"A", "manager", "system"},
    "awaiting_a": {"A", "manager", "system"},
    "awaiting_b": {"A", "B", "manager", "system"},
    "introduced": {"A", "B", "manager", "system"},
    "scheduled": {"A", "B", "manager", "system"},
}

WONT_GO_AHEAD = "This intro won't go ahead."


class WorkflowError(Exception):
    """Base error. Messages are safe to show to the user."""


class InvalidTransition(WorkflowError):
    pass


class NotAllowed(WorkflowError):
    pass


class Conflict(WorkflowError):
    pass


class PairBlocked(WorkflowError):
    pass


# --- Reading -----------------------------------------------------------------
def get(conn: sqlite3.Connection, workflow_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM intro_workflows WHERE id = ?", (workflow_id,)).fetchone()


def events(conn: sqlite3.Connection, workflow_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY id", (workflow_id,)
    ).fetchall()


def b_was_asked(conn: sqlite3.Connection, wf: sqlite3.Row) -> bool:
    """B only learns about an intro once A has consented to this specific match."""
    if wf["target_id"] is None:
        return False
    return conn.execute(
        """SELECT 1 FROM workflow_events
           WHERE workflow_id = ? AND event = 'a_consented' AND actor_id = ?
             AND id > (SELECT MAX(id) FROM workflow_events
                       WHERE workflow_id = ? AND event = 'candidate_proposed')""",
        (wf["id"], wf["requester_id"], wf["id"]),
    ).fetchone() is not None


def intros_for_member(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    """Intros this member requested, plus those they were actually asked about."""
    rows = conn.execute(
        "SELECT * FROM intro_workflows WHERE requester_id = ? OR target_id = ? ORDER BY id DESC",
        (member_id, member_id),
    ).fetchall()
    return [w for w in rows if w["requester_id"] == member_id or b_was_asked(conn, w)]


def board(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Manager view: every intro, newest first. No conversation content is joined here."""
    return conn.execute("SELECT * FROM intro_workflows ORDER BY id DESC").fetchall()


def shared_items(conn: sqlite3.Connection, wf: sqlite3.Row, side: str) -> dict:
    """What one side authorized the other to see, resolved live.

    Memories deleted or made private since consent are no longer shown.
    """
    raw = wf["shared_by_a"] if side == "A" else wf["shared_by_b"]
    if not raw:
        return {"name_title": None, "reason": None, "memories": []}
    data = json.loads(raw)
    owner_id = wf["requester_id"] if side == "A" else wf["target_id"]
    owner = db.get_member(conn, owner_id)
    ids = [i for i in data.get("memory_ids", []) if isinstance(i, int)]
    memories = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        memories = conn.execute(
            f"""SELECT content FROM memories WHERE member_id = ? AND visibility = 'shareable'
                AND sensitive = 0 AND id IN ({placeholders})""",
            (owner_id, *ids),
        ).fetchall()
    return {
        "name_title": name_title(owner) if owner else None,
        "reason": wf["reason"] if side == "A" else None,
        "memories": [m["content"] for m in memories],
    }


def shareable_memories(conn: sqlite3.Connection, member_id: int) -> list[sqlite3.Row]:
    return permissions.memories_for(conn, None, "matching", owner_id=member_id)


# --- Creating a request and suggesting candidates ----------------------------
class Suggestion(BaseModel):
    member_id: int
    why: str = Field(description="One sentence, based only on the candidate's listed information.")


class Suggestions(BaseModel):
    candidates: list[Suggestion]


MATCH_PROMPT = """You help a relationship manager of an executive community find the best
people to introduce to a member.

You get the member's request, the member's own preferences, and a list of candidates with
the information each candidate allowed to be used for intros.

Rules:
- Suggest at most 3 candidates, best first. Only use ids from the list.
- Respect the member's preferences (for example, if they don't want vendors, never suggest
  someone who sells to them).
- `why` must only use the candidate's listed information. Never mention the member's
  preferences, private details or anything not in the candidate's list.
- If nobody is a good fit, return an empty list."""


def request_intro(conn: sqlite3.Connection, requester_id: int, reason: str) -> sqlite3.Row:
    """Create a request (state `requested`) and ask the agent for candidates."""
    reason = reason.strip() or "Wants an introduction"
    manager_id = db.first_manager_id(conn)
    with conn:
        cur = conn.execute(
            """INSERT INTO intro_workflows (requester_id, reason, state, owner_id, deadline_at, created_at, updated_at)
               VALUES (?, ?, 'requested', ?, ?, ?, ?)""",
            (requester_id, reason, manager_id, clock.in_days(conn, DEADLINE_DAYS["requested"]),
             clock.now_str(conn), clock.now_str(conn)),
        )
        wf_id = cur.lastrowid
        _insert_event(conn, wf_id, "requested", None, "requested", requester_id, None, f"create:{wf_id}")
        requester = db.get_member(conn, requester_id)
        db.add_notification(conn, requester_id,
                            "I'm looking for the right person. Your relationship manager will confirm a match, "
                            "and nothing about you is shared without your approval.", wf_id)
        if manager_id:
            db.add_notification(conn, manager_id, f"New intro request from {requester['display_name']}.", wf_id)

    try:
        refresh_suggestions(conn, wf_id)
    except LLMError:
        # The request is saved; the manager can retry finding candidates from the board.
        logger.warning("Could not suggest candidates for intro %s", wf_id, exc_info=True)
    return get(conn, wf_id)


def refresh_suggestions(conn: sqlite3.Connection, wf_id: int) -> list[Suggestion]:
    """Ask the agent for candidates. With none, the request closes as no_match."""
    wf = get(conn, wf_id)
    if wf["state"] != "requested":
        raise InvalidTransition("Candidates can only be suggested while the request is open.")
    suggestions = suggest_candidates(conn, wf)
    with conn:
        conn.execute("UPDATE intro_workflows SET suggestions = ? WHERE id = ?",
                     (json.dumps([s.model_dump() for s in suggestions]), wf_id))
    if not suggestions:
        apply(conn, wf_id, "no_match", None)
    return suggestions


def candidate_pool(conn: sqlite3.Connection, requester_id: int) -> list[dict]:
    """Members who could be suggested, with only their shareable, non-sensitive memories."""
    pool = []
    for m in conn.execute(
        "SELECT * FROM members WHERE role = 'member' AND id <> ? ORDER BY id", (requester_id,)
    ).fetchall():
        if pair_blocked(conn, requester_id, m["id"]):
            continue
        pool.append({
            "member_id": m["id"],
            "name_title": name_title(m),
            "shareable": [r["content"] for r in shareable_memories(conn, m["id"])],
        })
    return pool


def suggest_candidates(conn: sqlite3.Connection, wf: sqlite3.Row) -> list[Suggestion]:
    pool = candidate_pool(conn, wf["requester_id"])
    if not pool:
        return []
    # The requester's own preferences help *their* request ("no vendors"). They go through
    # the own-conversation rule, minus anything sensitive, and the prompt forbids repeating
    # them in `why`, which the manager sees.
    requester = db.get_member(conn, wf["requester_id"])
    # Team notes ('internal') are not the member's preferences, so they stay out.
    preferences = [
        m for m in permissions.memories_for(conn, requester, "own_conversation")
        if not m["sensitive"] and m["visibility"] != "internal"
        and m["category"] in ("preference", "goal", "interest")
    ]
    listing = "\n".join(
        f"- id {c['member_id']}: {c['name_title']}. " + ("; ".join(c["shareable"]) or "No details shared.")
        for c in pool
    )
    prompt = (
        f"Member's request: {wf['reason']}\n\n"
        f"Member's preferences:\n" + ("\n".join(f"- {p['content']}" for p in preferences) or "(none)") +
        f"\n\nCandidates:\n{listing}"
    )
    result = call_llm(MATCH_PROMPT, [{"role": "user", "content": prompt}], Suggestions)
    allowed = {c["member_id"] for c in pool}
    seen, clean = set(), []
    for s in result.candidates:
        if s.member_id in allowed and s.member_id not in seen:
            seen.add(s.member_id)
            clean.append(s)
    return clean[:MAX_SUGGESTIONS]


def pair_blocked(conn: sqlite3.Connection, requester_id: int, target_id: int, exclude_id: int | None = None) -> bool:
    """Blocked if an intro between the two is open, or B declined A in the last 90 days."""
    placeholders = ",".join("?" for _ in OPEN)
    open_row = conn.execute(
        f"""SELECT 1 FROM intro_workflows
            WHERE state IN ({placeholders}) AND id IS NOT ?
              AND ((requester_id = ? AND target_id = ?) OR (requester_id = ? AND target_id = ?))""",
        (*OPEN, exclude_id, requester_id, target_id, target_id, requester_id),
    ).fetchone()
    if open_row:
        return True
    declined = conn.execute(
        """SELECT 1 FROM workflow_events e JOIN intro_workflows w ON w.id = e.workflow_id
           WHERE e.event = 'b_declined' AND w.requester_id = ? AND e.actor_id = ? AND e.created_at > ?""",
        (requester_id, target_id, clock.in_days(conn, -DECLINE_BLOCK_DAYS)),
    ).fetchone()
    return declined is not None


# --- Convenience actions used by the interface -------------------------------
def propose(conn, wf_id: int, manager_id: int, target_id: int) -> sqlite3.Row:
    wf = get(conn, wf_id)
    offered = {s["member_id"] for s in json.loads(wf["suggestions"] or "[]")}
    if target_id not in offered:
        raise NotAllowed("Choose one of the suggested candidates.")
    if pair_blocked(conn, wf["requester_id"], target_id, exclude_id=wf_id):
        raise PairBlocked("These two members can't be introduced right now.")
    return apply(conn, wf_id, "candidate_proposed", manager_id, payload={"target_id": target_id})


def consent(conn, wf_id: int, actor_id: int, memory_ids: list[int]) -> sqlite3.Row:
    """A or B agrees, choosing which of their shareable memories the other side may see."""
    wf = get(conn, wf_id)
    event = "a_consented" if actor_id == wf["requester_id"] else "b_consented"
    allowed = {m["id"] for m in shareable_memories(conn, actor_id)}
    chosen = sorted(i for i in memory_ids if i in allowed)
    return apply(conn, wf_id, event, actor_id, payload={"target_id": wf["target_id"], "memory_ids": chosen})


# --- The transition engine ---------------------------------------------------
def apply(
    conn: sqlite3.Connection,
    wf_id: int,
    event: str,
    actor_id: int | None,
    *,
    payload: dict | None = None,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> sqlite3.Row:
    if idempotency_key and conn.execute(
        "SELECT 1 FROM workflow_events WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone():
        return get(conn, wf_id)  # already applied: a retry is a no-op

    wf = get(conn, wf_id)
    if wf is None:
        raise WorkflowError("This intro no longer exists.")
    if expected_version is not None and wf["version"] != expected_version:
        raise Conflict("This intro changed while you were looking at it. Please review it again.")

    to_state = TRANSITIONS.get(event, {}).get(wf["state"])
    if to_state is None:
        raise InvalidTransition(f"This intro is {wf['state'].replace('_', ' ')}, so that action isn't available.")
    _check_actor(conn, wf, event, actor_id)
    payload = payload or {}

    if event == "b_consented" and not b_was_asked(conn, wf):
        raise InvalidTransition("Both sides must agree, and the requester hasn't yet.")  # R4 guard

    fields = _fields_for(conn, wf, event, to_state, payload)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        cur = conn.execute(
            f"""UPDATE intro_workflows SET state = ?, {sets}, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?""",
            (to_state, *fields.values(), clock.now_str(conn), wf_id, wf["version"]),
        )
        if cur.rowcount != 1:
            raise Conflict("This intro changed while you were looking at it. Please review it again.")
        _insert_event(conn, wf_id, event, wf["state"], to_state, actor_id, payload,
                      idempotency_key or f"{event}:{wf_id}:{wf['version']}:{uuid.uuid4().hex[:8]}")
        _notify(conn, get(conn, wf_id), event, wf["state"], actor_id)
    return get(conn, wf_id)


def _check_actor(conn, wf, event: str, actor_id: int | None) -> None:
    allowed = CANCEL_ALLOWED.get(wf["state"], set()) if event == "cancelled" else ALLOWED.get(event, set())
    roles = set()
    if actor_id is None:
        roles.add("system")
    else:
        if actor_id == wf["requester_id"]:
            roles.add("A")
        if actor_id == wf["target_id"] and b_was_asked(conn, wf):
            roles.add("B")
        member = db.get_member(conn, actor_id)
        if member and member["role"] == "manager":
            roles.add("manager")
    if not roles & allowed:
        raise NotAllowed("You can't do that on this intro.")


def _fields_for(conn, wf, event: str, to_state: str, payload: dict) -> dict:
    manager_id = db.first_manager_id(conn)
    fields: dict = {}
    if event == "candidate_proposed":
        fields["target_id"] = payload["target_id"]
    if event == "a_wants_another":
        fields["target_id"] = None
        fields["shared_by_a"] = None
    if event == "a_consented":
        fields["shared_by_a"] = json.dumps({"memory_ids": payload.get("memory_ids", [])})
    if event == "b_consented":
        fields["shared_by_b"] = json.dumps({"memory_ids": payload.get("memory_ids", [])})
    if event == "meeting_booked":
        fields["meeting_at"] = payload["meeting_at"]
    if event == "outcome_recorded":
        fields["outcome"] = payload.get("outcome", "").strip() or "Recorded"
    if event == "deadline_passed" and wf["state"] == "follow_up":
        fields["outcome"] = "No answer"

    target_id = fields.get("target_id", wf["target_id"])
    owner = {
        "requested": manager_id, "awaiting_a": wf["requester_id"], "awaiting_b": target_id,
        "introduced": None, "scheduled": None, "follow_up": manager_id,
    }
    fields["owner_id"] = owner.get(to_state)
    if to_state == "scheduled":
        fields["deadline_at"] = payload["meeting_at"]
    elif to_state in DEADLINE_DAYS:
        fields["deadline_at"] = clock.in_days(conn, DEADLINE_DAYS[to_state])
    else:
        fields["deadline_at"] = None
    return fields


def _insert_event(conn, wf_id, event, from_state, to_state, actor_id, payload, key) -> None:
    conn.execute(
        """INSERT INTO workflow_events
           (workflow_id, event, from_state, to_state, actor_id, payload, idempotency_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (wf_id, event, from_state, to_state, actor_id,
         json.dumps(payload) if payload else None, key, clock.now_str(conn)),
    )


def _notify(conn, wf, event: str, from_state: str, actor_id: int | None) -> None:
    a, b = wf["requester_id"], wf["target_id"]
    a_row, b_row = db.get_member(conn, a), db.get_member(conn, b) if b else None
    manager_id = db.first_manager_id(conn)

    def send(member_id, body):
        if member_id and member_id != actor_id:
            db.add_notification(conn, member_id, body, wf["id"])

    state = wf["state"]
    if event == "candidate_proposed":
        send(a, f"Possible match: {name_title(b_row)}. Review it in Pending.")
    elif event == "a_wants_another":
        send(manager_id, f"{a_row['display_name']} asked for another candidate.")
    elif event == "a_consented":
        send(b, f"{a_row['display_name']} would like to be introduced to you. Review it in Pending.")
    elif event == "b_consented":
        send(a, f"You're introduced to {b_row['display_name']}. See what they shared in Pending.")
        db.add_notification(conn, b, f"You're introduced to {a_row['display_name']}.", wf["id"])
    elif event == "meeting_booked":
        for person in (a, b):
            send(person, f"Meeting booked for {wf['meeting_at'][:10]}.")
    elif event == "no_match":
        send(a, "I couldn't find a good match for this request. You can ask again anytime.")
    elif state == "follow_up":
        send(manager_id, f"Time to follow up: {a_row['display_name']} and {b_row['display_name']}.")
        send(a, f"How did it go with {b_row['display_name']}? Tell your relationship manager.")
        send(b, f"How did it go with {a_row['display_name']}? Tell your relationship manager.")
    elif state in {"declined", "expired", "cancelled"}:
        # One neutral notice, whatever the reason, to protect the other person (model.md).
        send(a, WONT_GO_AHEAD)
        if b and from_state in {"awaiting_b", "introduced", "scheduled"}:
            send(b, WONT_GO_AHEAD)


def name_title(member: sqlite3.Row | None) -> str:
    if member is None:
        return "Unknown"
    parts = [member["title"], member["company"]]
    detail = " at ".join(p for p in parts if p)
    return f"{member['display_name']}, {detail}" if detail else member["display_name"]


# --- Time-driven steps (Scheduler) -------------------------------------------
@dataclass
class TickReport:
    transitions: int = 0
    reminders: int = 0


def tick(conn: sqlite3.Connection) -> TickReport:
    """Apply deadlines and send reminders, as of the simulated clock. Safe to run repeatedly."""
    report = TickReport()
    now = clock.now_str(conn)
    soon = clock.in_days(conn, REMINDER_BEFORE_DAYS)
    placeholders = ",".join("?" for _ in OPEN)

    for wf in conn.execute(
        f"SELECT * FROM intro_workflows WHERE state IN ({placeholders}) AND deadline_at IS NOT NULL",
        tuple(OPEN),
    ).fetchall():
        if wf["deadline_at"] <= now:
            apply(conn, wf["id"], "deadline_passed", None,
                  idempotency_key=f"deadline:{wf['id']}:{wf['version']}")
            report.transitions += 1
        elif wf["deadline_at"] <= soon and wf["state"] != "scheduled":
            key = f"reminder:{wf['id']}:{wf['version']}"
            if conn.execute("SELECT 1 FROM workflow_events WHERE idempotency_key = ?", (key,)).fetchone():
                continue
            with conn:
                _insert_event(conn, wf["id"], "reminder_sent", wf["state"], wf["state"], None, None, key)
                recipients = [wf["owner_id"]] if wf["owner_id"] else [wf["requester_id"], wf["target_id"]]
                for person in recipients:
                    if person:
                        db.add_notification(conn, person,
                                            f"Reminder: an intro is waiting for you until {wf['deadline_at'][:10]}.",
                                            wf["id"])
            report.reminders += 1

    # A scheduled meeting can pass and then also need its follow-up reminder: run once more.
    if report.transitions:
        again = tick(conn)
        report.transitions += again.transitions
        report.reminders += again.reminders
    return report


def cancel_all_for_member(conn: sqlite3.Connection, member_id: int) -> None:
    """Used by "delete everything": close open intros and keep only ids, states and dates."""
    placeholders = ",".join("?" for _ in OPEN)
    for wf in conn.execute(
        f"""SELECT * FROM intro_workflows WHERE state IN ({placeholders})
            AND (requester_id = ? OR target_id = ?)""",
        (*OPEN, member_id, member_id),
    ).fetchall():
        if wf["state"] in CANCEL_ALLOWED:
            apply(conn, wf["id"], "cancelled", None)
        else:  # follow_up: nothing left to cancel, just close it
            apply(conn, wf["id"], "deadline_passed", None)
    with conn:
        conn.execute(
            """UPDATE workflow_events SET payload = NULL WHERE workflow_id IN
               (SELECT id FROM intro_workflows WHERE requester_id = ? OR target_id = ?)""",
            (member_id, member_id),
        )
        conn.execute(
            """UPDATE intro_workflows SET reason = '(deleted by member)', shared_by_a = NULL,
                      shared_by_b = NULL, suggestions = NULL
               WHERE requester_id = ? OR target_id = ?""",
            (member_id, member_id),
        )
