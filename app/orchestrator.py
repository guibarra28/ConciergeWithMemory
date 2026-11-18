"""Handle one inbound message: resolve identity first, then remember, then answer.

An unknown sender never reaches the model or any member data (R3): the message is
quarantined and gets a fixed verification reply.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app import db, memory
from app.identity import ResolvedIdentity, resolve
from app.llm import LLMError, call_llm

logger = logging.getLogger(__name__)

TIMELINE_CONTEXT_LIMIT = 20

UNKNOWN_SENDER_REPLY = (
    "Thanks for your message. We couldn't match this address to a member account, "
    "so we haven't used any account information to reply. If you're a member, please "
    "write from the email registered with the community, or ask your relationship "
    "manager to add this address."
)

SYSTEM_PROMPT = """You are the concierge of a private community of CEOs, investors and executives.
You are talking to {name} ({title}{company}).
The conversation below includes messages from chat and email; each one is tagged with its channel.
Be concise, warm and practical. Never invent facts about the member or about other members.
You have no internet access and no real-time data (weather, news, prices, schedules). If asked,
say so plainly instead of guessing.
Never promise to remember something; the app shows the member what was saved.

What you remember about this member (id in brackets):
{memories}

These memories are the member's latest word: if the conversation history disagrees with
them, trust the memories. Do not bring up facts from the history that are not in this list.

Use a memory only when it is relevant to the answer, and list the ids of the memories you
actually used in `used_memory_ids`. If you used none, return an empty list.
Never mention memory ids in the reply text.

Language: always answer in the language of the member's most recent message, even if names,
companies, memories or earlier messages are in another language."""


class ConciergeReply(BaseModel):
    # Declared before `reply` so the model commits to the language before writing.
    language: str = Field(description="Language of the member's most recent message, e.g. 'English'.")
    reply: str = Field(description="The answer, written in `language`.")
    used_memory_ids: list[int] = Field(description="Ids of the memories used in the reply.")


@dataclass
class TurnResult:
    identity: ResolvedIdentity
    reply: str
    inbound_message_id: int
    outbound_message_id: int | None
    saved_memories: list[sqlite3.Row] = field(default_factory=list)
    needs_confirmation: list[memory.MemoryCandidate] = field(default_factory=list)
    used_memories: list[sqlite3.Row] = field(default_factory=list)


def handle_message(conn: sqlite3.Connection, channel: str, sender_address: str, text: str) -> TurnResult:
    identity = resolve(conn, channel, sender_address)

    if not identity.is_known:
        inbound_id = db.add_message(
            conn, member_id=None, channel=channel, direction="inbound",
            body=text, sender_address=sender_address, quarantined=True,
        )
        return TurnResult(identity, UNKNOWN_SENDER_REPLY, inbound_id, None)

    member_id = identity.member_id
    inbound_id = db.add_message(
        conn, member_id=member_id, channel=channel, direction="inbound",
        body=text, sender_address=sender_address,
    )

    try:
        extraction = memory.extract_and_save(conn, member_id, text, inbound_id, channel)
    except LLMError:
        # Remembering is secondary: if it fails, still answer the member.
        logger.warning("Memory extraction failed for message %s", inbound_id, exc_info=True)
        extraction = memory.ExtractionOutcome([], [], [])

    member = db.get_member(conn, member_id)
    memories = memory.get_context(conn, member_id)
    timeline = db.get_timeline(conn, member_id, limit=TIMELINE_CONTEXT_LIMIT, for_model=True)
    result = call_llm(_system_prompt(member, memories), _to_llm_messages(timeline), ConciergeReply)

    # Keep only ids that were actually offered to the model for this member.
    offered = {m["id"] for m in memories}
    used_ids = sorted({i for i in result.used_memory_ids if i in offered})

    outbound_id = db.add_message(
        conn, member_id=member_id, channel=channel, direction="outbound",
        body=result.reply, used_memory_ids=used_ids,
    )
    return TurnResult(
        identity, result.reply, inbound_id, outbound_id,
        saved_memories=extraction.saved,
        needs_confirmation=extraction.needs_confirmation,
        used_memories=memory.get_memories_by_ids(conn, member_id, used_ids),
    )


def _system_prompt(member: sqlite3.Row, memories: list[sqlite3.Row]) -> str:
    company = f" at {member['company']}" if member["company"] else ""
    return SYSTEM_PROMPT.format(
        name=member["display_name"],
        title=member["title"] or "member",
        company=company,
        memories=memory.format_for_prompt(memories),
    )


def _to_llm_messages(timeline: list[sqlite3.Row]) -> list[dict]:
    # Only member messages are tagged, so the model doesn't learn to echo the tag in replies.
    return [
        {"role": "user", "content": f"[{row['channel']}] {row['body']}"}
        if row["direction"] == "inbound"
        else {"role": "assistant", "content": row["body"]}
        for row in timeline
    ]
