"""Handle one inbound message: resolve identity first, then answer.

An unknown sender never reaches the model or any member data (R3): the message is
quarantined and gets a fixed verification reply.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app import db
from app.identity import ResolvedIdentity, resolve
from app.llm import call_llm

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
Be concise, warm and practical. Never invent facts about other members.
Language: always answer in the language of the member's most recent message, even if names,
companies or earlier messages are in another language."""


class ConciergeReply(BaseModel):
    # Declared before `reply` so the model commits to the language before writing.
    language: str = Field(description="Language of the member's most recent message, e.g. 'English'.")
    reply: str = Field(description="The answer, written in `language`.")


@dataclass
class TurnResult:
    identity: ResolvedIdentity
    reply: str
    inbound_message_id: int
    outbound_message_id: int | None


def handle_message(conn: sqlite3.Connection, channel: str, sender_address: str, text: str) -> TurnResult:
    identity = resolve(conn, channel, sender_address)

    if not identity.is_known:
        inbound_id = db.add_message(
            conn, member_id=None, channel=channel, direction="inbound",
            body=text, sender_address=sender_address, quarantined=True,
        )
        return TurnResult(identity, UNKNOWN_SENDER_REPLY, inbound_id, None)

    inbound_id = db.add_message(
        conn, member_id=identity.member_id, channel=channel, direction="inbound",
        body=text, sender_address=sender_address,
    )

    member = db.get_member(conn, identity.member_id)
    timeline = db.get_timeline(conn, identity.member_id, limit=TIMELINE_CONTEXT_LIMIT)
    result = call_llm(_system_prompt(member), _to_llm_messages(timeline), ConciergeReply)

    outbound_id = db.add_message(
        conn, member_id=identity.member_id, channel=channel, direction="outbound", body=result.reply,
    )
    return TurnResult(identity, result.reply, inbound_id, outbound_id)


def _system_prompt(member: sqlite3.Row) -> str:
    company = f" at {member['company']}" if member["company"] else ""
    return SYSTEM_PROMPT.format(name=member["display_name"], title=member["title"] or "member", company=company)


def _to_llm_messages(timeline: list[sqlite3.Row]) -> list[dict]:
    # Only member messages are tagged, so the model doesn't learn to echo the tag in replies.
    return [
        {"role": "user", "content": f"[{row['channel']}] {row['body']}"}
        if row["direction"] == "inbound"
        else {"role": "assistant", "content": row["body"]}
        for row in timeline
    ]
