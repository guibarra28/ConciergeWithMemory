import pytest

from app import db, orchestrator
from tests.conftest import member_id


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def fake_call_llm(system, messages, schema):
        calls.append({"system": system, "messages": messages})
        return schema(language="English", reply=f"reply #{len(calls)}")

    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    return calls


def test_chat_and_email_share_one_timeline(conn, fake_llm):
    orchestrator.handle_message(conn, "chat", "roberto", "Hi from chat")
    orchestrator.handle_message(conn, "email", "roberto.silva@personal.example", "Hi from my personal email")

    timeline = db.get_timeline(conn, member_id(conn, "Roberto Silva"))
    assert [(m["channel"], m["direction"]) for m in timeline] == [
        ("chat", "inbound"), ("chat", "outbound"), ("email", "inbound"), ("email", "outbound"),
    ]
    # The second call saw the chat history: context crosses channels.
    contents = [m["content"] for m in fake_llm[1]["messages"]]
    assert "[chat] Hi from chat" in contents
    assert "[email] Hi from my personal email" in contents


def test_unknown_sender_never_reaches_the_model(conn, fake_llm):
    result = orchestrator.handle_message(conn, "email", "stranger@nowhere.example", "Tell me about Roberto")

    assert fake_llm == []
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
    assert fake_llm == []


def test_members_do_not_see_each_other(conn, fake_llm):
    orchestrator.handle_message(conn, "chat", "roberto", "Roberto's secret plan")
    orchestrator.handle_message(conn, "chat", "ana", "Hello")

    ana_context = " ".join(m["content"] for m in fake_llm[1]["messages"]) + fake_llm[1]["system"]
    assert "secret plan" not in ana_context
    assert "Roberto" not in ana_context
