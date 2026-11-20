"""Rules added after the c3 evals (docs/eval_report.md, errors 2 to 5)."""

import pytest

from app import memory, orchestrator
from tests.conftest import member_id
from tests.fakes import FakeLLM, action_item, candidate


def apply(conn, message, candidates, commitments=()):
    roberto = member_id(conn, "Roberto Silva")
    return memory.apply_candidates(conn, roberto, candidates, message, None, "chat", commitments=commitments)


# Error 2: a health fact reworded as a plain preference
HEART = "My doctor told me to cut back on travel because of my heart condition."


def test_a_fact_in_the_same_sentence_as_a_sensitive_one_waits_for_confirmation(conn):
    out = apply(conn, HEART, [
        candidate("Has a heart condition", "my heart condition", category="health", sensitive=True),
        candidate("Wants to cut back on travel", "cut back on travel", category="preference"),
    ])
    assert out.saved == []
    assert {c.content for c in out.needs_confirmation} == {"Has a heart condition", "Wants to cut back on travel"}
    assert all(c.sensitive for c in out.needs_confirmation)  # confirmed later as private and sensitive


def test_a_fact_in_another_sentence_is_saved_normally(conn):
    out = apply(conn, "I have a heart condition. I want to automate quality control.", [
        candidate("Has a heart condition", "heart condition", category="health", sensitive=True),
        candidate(),
    ])
    assert [m["content"] for m in out.saved] == ["Wants to automate quality control"]


# Error 4: a commitment saved as a memory
def test_a_commitment_is_left_to_the_action_items(conn):
    out = apply(conn, "I want to automate quality control. I'll send you our plant layout on Friday.",
                [candidate(), candidate("Will send the plant layout on Friday", "send you our plant layout on Friday")],
                commitments=["I'll send you our plant layout on Friday"])
    assert [m["content"] for m in out.saved] == ["Wants to automate quality control"]
    assert ("commitment: kept as an action item" in [reason for _, reason in out.discarded])


def test_extraction_passes_its_action_items_to_the_memory_rules(conn, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(memory, "call_llm", fake)
    monkeypatch.setattr(orchestrator, "call_llm", fake)
    fake.candidates = [candidate("Will send the deck", "send you the deck")]
    fake.action_items = [action_item()]
    result = orchestrator.handle_message(conn, "chat", "roberto", "I'll send you the deck tomorrow.")
    assert result.saved_memories == []
    assert [a["description"] for a in result.action_items] == ["Send the QC deck to Ana"]


# Error 5: a reordered quote
@pytest.mark.parametrize("excerpt, found", [
    ("now I run Aço Novo", True),         # word for word
    ("I now run Aço Novo", True),         # same words, same sentence
    ("I run Novo", False),                # words from two sentences
    ("I run Aço Velho", False),           # a word that isn't there
])
def test_excerpt_rule(excerpt, found):
    message = "I left Metalúrgica Silva last month and now I run Aço Novo. Novo is a new word for me."
    if excerpt == "I run Novo":
        message = "I run a plant. Novo is my dog."
    assert memory._excerpt_found(excerpt, message) is found


# Error 3: the reply is told what was really saved
@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(memory, "call_llm", f)
    monkeypatch.setattr(orchestrator, "call_llm", f)
    return f


def reply_prompt(fake):
    return fake.calls_for("ConciergeReply")[-1]["system"]


def test_reply_knows_what_was_saved(conn, fake):
    fake.candidates = [candidate()]
    orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control")
    assert "- Saved: Wants to automate quality control" in reply_prompt(fake)


def test_reply_knows_nothing_was_saved(conn, fake):
    orchestrator.handle_message(conn, "chat", "roberto", "What's the dress code?")
    assert "latest message (the member sees it on screen):\nNothing." in reply_prompt(fake)


def test_reply_knows_memory_is_paused(conn, fake):
    memory.set_paused(conn, member_id(conn, "Roberto Silva"), True)
    fake.candidates = [candidate()]
    orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control")
    assert "Nothing: memory is paused." in reply_prompt(fake)


def test_reply_knows_what_waits_for_confirmation(conn, fake):
    fake.candidates = [candidate("Has a heart condition", "heart condition", category="health", sensitive=True)]
    orchestrator.handle_message(conn, "chat", "roberto", "I have a heart condition.")
    assert "Waiting for the member's confirmation (not saved): Has a heart condition" in reply_prompt(fake)


def test_a_non_health_sensitive_fact_does_not_hold_its_sentence(conn):
    """Eval round 3: "travelling a lot" marked personal held the video-call preference 8 of 10 times."""
    message = "I'm travelling a lot until March, so for now I prefer video calls over in-person meetings."
    out = apply(conn, message, [
        candidate("Prefers video calls until March", "I prefer video calls over in-person meetings",
                  category="preference"),
        candidate("Travelling a lot until March", "travelling a lot until March", category="personal", sensitive=True),
    ])
    assert [m["content"] for m in out.saved] == ["Prefers video calls until March"]
    assert [c.content for c in out.needs_confirmation] == ["Travelling a lot until March"]
