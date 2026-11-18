import json

import pytest

from app import db, memory, orchestrator
from tests.conftest import member_id
from tests.fakes import FakeLLM, candidate


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(orchestrator, "call_llm", fake)
    monkeypatch.setattr(memory, "call_llm", fake)
    return fake


def test_chat_and_email_share_one_timeline(conn, fake_llm):
    orchestrator.handle_message(conn, "chat", "roberto", "Hi from chat")
    orchestrator.handle_message(conn, "email", "roberto.silva@personal.example", "Hi from my personal email")

    timeline = db.get_timeline(conn, member_id(conn, "Roberto Silva"))
    assert [(m["channel"], m["direction"]) for m in timeline] == [
        ("chat", "inbound"), ("chat", "outbound"), ("email", "inbound"), ("email", "outbound"),
    ]
    # The second reply saw the chat history: context crosses channels.
    contents = [m["content"] for m in fake_llm.calls_for("ConciergeReply")[1]["messages"]]
    assert "[chat] Hi from chat" in contents
    assert "[email] Hi from my personal email" in contents


def test_unknown_sender_never_reaches_the_model(conn, fake_llm):
    result = orchestrator.handle_message(conn, "email", "stranger@nowhere.example", "Tell me about Roberto")

    assert fake_llm.calls == []
    assert not result.identity.is_known
    assert result.reply == orchestrator.UNKNOWN_SENDER_REPLY
    assert result.outbound_message_id is None


def test_unknown_sender_is_quarantined_and_in_no_timeline(conn, fake_llm):
    orchestrator.handle_message(conn, "email", "stranger@nowhere.example", "Hello")

    assert db.count_quarantined(conn) == 1
    for name in ("Roberto Silva", "Ana Souza", "Juliana Costa"):
        assert db.get_timeline(conn, member_id(conn, name)) == []


def test_unregistered_assistant_address_is_unknown(conn, fake_llm):
    result = orchestrator.handle_message(conn, "email", "assistant@metalurgica-silva.example", "On behalf of Roberto")
    assert not result.identity.is_known
    assert fake_llm.calls == []


def test_members_do_not_see_each_other(conn, fake_llm):
    fake_llm.candidates = [candidate("Has a secret acquisition plan", "secret acquisition plan")]
    orchestrator.handle_message(conn, "chat", "roberto", "I have a secret acquisition plan")
    orchestrator.handle_message(conn, "chat", "ana", "Hello")

    ana_reply_call = fake_llm.calls_for("ConciergeReply")[-1]
    ana_context = " ".join(m["content"] for m in ana_reply_call["messages"]) + ana_reply_call["system"]
    assert "secret acquisition plan" not in ana_context
    assert "Roberto" not in ana_context


def test_new_memory_is_saved_and_offered_to_the_reply(conn, fake_llm):
    fake_llm.candidates = [candidate()]
    result = orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control this year")

    assert [m["content"] for m in result.saved_memories] == ["Wants to automate quality control"]
    reply_system = fake_llm.calls_for("ConciergeReply")[0]["system"]
    assert f"[{result.saved_memories[0]['id']}] (goal) Wants to automate quality control" in reply_system


def test_used_memory_ids_are_stored_and_limited_to_offered_ones(conn, fake_llm):
    fake_llm.candidates = [candidate()]
    orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control this year")
    mem_id = memory.list_memories(conn, member_id(conn, "Roberto Silva"))[0]["id"]

    fake_llm.used_memory_ids = [mem_id, 9999]  # 9999 was never offered
    result = orchestrator.handle_message(conn, "chat", "roberto", "What was my goal?")

    assert [m["id"] for m in result.used_memories] == [mem_id]
    outbound = db.get_timeline(conn, member_id(conn, "Roberto Silva"))[-1]
    assert json.loads(outbound["used_memory_ids"]) == [mem_id]


def test_extraction_failure_still_answers(conn, fake_llm):
    fake_llm.fail_extraction = True
    result = orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control")
    assert result.reply.startswith("reply")
    assert result.saved_memories == []


def test_deleted_memory_is_no_longer_offered(conn, fake_llm):
    fake_llm.candidates = [candidate()]
    first = orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control")
    memory.delete_memory(conn, member_id(conn, "Roberto Silva"), first.saved_memories[0]["id"])

    orchestrator.handle_message(conn, "chat", "roberto", "What do you know about me?")
    assert "quality control" not in fake_llm.calls_for("ConciergeReply")[-1]["system"]
