"""Recovery for the four edge cases marked [build] in docs/edge_cases.md (block c2)."""

import pytest

from app import clock, db, memory, orchestrator, workflow
from tests.conftest import member_id
from tests.fakes import FakeLLM, candidate


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    for module in (orchestrator, memory, workflow):
        monkeypatch.setattr(module, "call_llm", f)
    return f


def inbound_count(conn, mid):
    return conn.execute(
        "SELECT COUNT(*) FROM messages WHERE member_id = ? AND direction = 'inbound'", (mid,)
    ).fetchone()[0]


# --- E4: the model call fails after the message was saved ---------------------
def test_reply_failure_keeps_the_message_and_what_was_remembered(conn, fake):
    roberto = member_id(conn, "Roberto Silva")
    fake.fail_reply = True
    fake.candidates = [candidate()]
    with pytest.raises(orchestrator.ReplyFailed) as failure:
        orchestrator.handle_message(conn, "chat", "roberto", "I want to automate quality control")

    assert inbound_count(conn, roberto) == 1
    assert [m["content"] for m in failure.value.partial.saved_memories] == ["Wants to automate quality control"]
    assert orchestrator.unanswered_message(conn, roberto)["body"] == "I want to automate quality control"


def test_retry_answers_the_same_message_once(conn, fake):
    roberto = member_id(conn, "Roberto Silva")
    fake.fail_reply = True
    with pytest.raises(orchestrator.ReplyFailed) as failure:
        orchestrator.handle_message(conn, "chat", "roberto", "Any events next month?")
    inbound_id = failure.value.partial.inbound_message_id
    extractions = len(fake.calls_for("ExtractionResult"))

    fake.fail_reply = False
    result = orchestrator.retry_reply(conn, roberto, inbound_id)

    assert result.outbound_message_id is not None
    assert inbound_count(conn, roberto) == 1                           # not saved twice
    assert len(fake.calls_for("ExtractionResult")) == extractions      # not extracted twice
    assert orchestrator.unanswered_message(conn, roberto) is None
    with pytest.raises(orchestrator.AlreadyAnswered):                  # a double click is harmless
        orchestrator.retry_reply(conn, roberto, inbound_id)


def test_retry_cannot_answer_someone_elses_message(conn, fake):
    fake.fail_reply = True
    with pytest.raises(orchestrator.ReplyFailed) as failure:
        orchestrator.handle_message(conn, "chat", "roberto", "Hello")
    fake.fail_reply = False
    with pytest.raises(orchestrator.AlreadyAnswered):
        orchestrator.retry_reply(conn, member_id(conn, "Ana Souza"), failure.value.partial.inbound_message_id)


def test_retry_respects_the_same_permissions(conn, fake):
    """A retried reply is built exactly like a first reply: own memories only."""
    fake.fail_reply = True
    with pytest.raises(orchestrator.ReplyFailed) as failure:
        orchestrator.handle_message(conn, "chat", "ana", "What do you know about Carlos?")
    fake.fail_reply = False
    fake.calls.clear()
    orchestrator.retry_reply(conn, member_id(conn, "Ana Souza"), failure.value.partial.inbound_message_id)
    assert "Is considering leaving his job next year" not in fake.calls[-1]["system"]


# --- E1: an outdated memory (changed company) ---------------------------------
def roberto_memory(conn, content, visibility="private", category="company", sensitive=0):
    roberto = member_id(conn, "Roberto Silva")
    cur = conn.execute(
        "INSERT INTO memories (member_id, content, category, visibility, sensitive, confidence) VALUES (?, ?, ?, ?, ?, 1)",
        (roberto, content, category, visibility, sensitive))
    conn.commit()
    return roberto, cur.lastrowid


MOVED = "I left Metalúrgica Silva and now run Aço Novo"


def test_a_changed_fact_replaces_the_old_memory(conn, fake):
    roberto, old_id = roberto_memory(conn, "Runs Metalúrgica Silva")
    fake.candidates = [candidate("Runs Aço Novo", "now run Aço Novo", category="company", replaces=[old_id])]
    result = orchestrator.handle_message(conn, "chat", "roberto", MOVED)

    assert [m["content"] for m in memory.list_memories(conn, roberto)] == ["Runs Aço Novo"]
    (new, previous), = result.updated_memories
    assert new["id"] == old_id and [p["content"] for p in previous] == ["Runs Metalúrgica Silva"]
    assert result.saved_memories == []


def test_a_replaced_shareable_memory_becomes_private(conn, fake):
    roberto, old_id = roberto_memory(conn, "Runs Metalúrgica Silva", visibility="shareable")
    fake.candidates = [candidate("Runs Aço Novo", "now run Aço Novo", category="company", replaces=[old_id])]
    orchestrator.handle_message(conn, "chat", "roberto", MOVED)
    assert memory.get_memory(conn, roberto, old_id)["visibility"] == "private"
    assert workflow.shareable_memories(conn, roberto) == []


def test_undo_puts_the_old_memory_back(conn, fake):
    roberto, old_id = roberto_memory(conn, "Runs Metalúrgica Silva", visibility="shareable")
    fake.candidates = [candidate("Runs Aço Novo", "now run Aço Novo", category="company", replaces=[old_id])]
    (_, previous), = orchestrator.handle_message(conn, "chat", "roberto", MOVED).updated_memories
    memory.undo_replace(conn, roberto, previous)
    restored = memory.get_memory(conn, roberto, old_id)
    assert (restored["content"], restored["visibility"]) == ("Runs Metalúrgica Silva", "shareable")


@pytest.mark.parametrize("target", ["another member", "team note", "sensitive"])
def test_only_the_members_own_ordinary_memories_can_be_replaced(conn, fake, target):
    roberto = member_id(conn, "Roberto Silva")
    if target == "another member":
        old_id = conn.execute("SELECT id FROM memories WHERE content LIKE 'Is considering leaving%'").fetchone()[0]
    elif target == "team note":
        _, old_id = roberto_memory(conn, "Spoke at the June event", visibility="internal")
    else:
        _, old_id = roberto_memory(conn, "Has a heart condition", category="health", sensitive=1)
    before = conn.execute("SELECT content FROM memories WHERE id = ?", (old_id,)).fetchone()[0]

    fake.candidates = [candidate("Runs Aço Novo", "now run Aço Novo", category="company", replaces=[old_id])]
    result = orchestrator.handle_message(conn, "chat", "roberto", MOVED)

    assert conn.execute("SELECT content FROM memories WHERE id = ?", (old_id,)).fetchone()[0] == before
    assert result.updated_memories == []
    assert [m["content"] for m in result.saved_memories] == ["Runs Aço Novo"]  # saved as a new memory
    assert all(m["member_id"] == roberto for m in result.saved_memories)


def test_extraction_sees_memory_ids_so_it_can_point_to_the_old_one(conn, fake):
    _, old_id = roberto_memory(conn, "Runs Metalúrgica Silva")
    orchestrator.handle_message(conn, "chat", "roberto", MOVED)
    sent = fake.calls_for("ExtractionResult")[-1]["messages"][0]["content"]
    assert f"[{old_id}] Runs Metalúrgica Silva" in sent


# --- E2: forgetting something already used in an intro -------------------------
def introduced_with_shared_memory(conn, fake):
    roberto, ana, juliana = (member_id(conn, n) for n in ("Roberto Silva", "Ana Souza", "Juliana Costa"))
    _, shared_id = roberto_memory(conn, "Wants to automate weld inspection", visibility="shareable", category="goal")
    fake.suggested_ids = [ana]
    wf = workflow.request_intro(conn, roberto, "Someone who automated QC")
    workflow.propose(conn, wf["id"], juliana, ana)
    return roberto, ana, shared_id, wf["id"]


def test_shared_memory_is_flagged_as_seen_only_after_consent(conn, fake):
    roberto, ana, shared_id, wf_id = introduced_with_shared_memory(conn, fake)
    assert workflow.seen_by_others(conn, roberto, shared_id) == []  # Ana hasn't been asked yet
    workflow.consent(conn, wf_id, roberto, [shared_id])
    seen, = workflow.seen_by_others(conn, roberto, shared_id)
    assert seen["other_name"] == "Ana Souza" and seen["shared_at"]


def test_deleting_a_seen_memory_removes_it_from_the_intro_without_telling_the_other_side(conn, fake):
    roberto, ana, shared_id, wf_id = introduced_with_shared_memory(conn, fake)
    workflow.consent(conn, wf_id, roberto, [shared_id])
    notes_before = len(db.list_notifications(conn, ana, limit=50))

    memory.delete_memory(conn, roberto, shared_id)

    assert workflow.shared_items(conn, workflow.get(conn, wf_id), "A")["memories"] == []
    assert workflow.seen_by_others(conn, roberto, shared_id) == []
    assert len(db.list_notifications(conn, ana, limit=50)) == notes_before


def test_a_reused_memory_id_does_not_inherit_old_consent(conn, fake):
    """Found while building E2: SQLite reuses the id of the last deleted row."""
    roberto, ana, shared_id, wf_id = introduced_with_shared_memory(conn, fake)
    workflow.consent(conn, wf_id, roberto, [shared_id])
    memory.delete_memory(conn, roberto, shared_id)

    _, new_id = roberto_memory(conn, "Plans to open a plant in Mexico", visibility="shareable", category="goal")
    assert new_id == shared_id  # same id, a different memory he never agreed to share here
    assert workflow.shared_items(conn, workflow.get(conn, wf_id), "A")["memories"] == []


# --- E3: a memory nobody confirmed in six months (R8) --------------------------
def test_seeded_old_memory_is_stale_and_confirming_clears_it(conn):
    ana = member_id(conn, "Ana Souza")
    old = next(m for m in memory.list_memories(conn, ana) if m["content"] == "Leads a customer service team of 400 people")
    assert memory.is_stale(conn, old)
    memory.confirm_still_true(conn, ana, old["id"])
    assert not memory.is_stale(conn, memory.get_memory(conn, ana, old["id"]))


def test_memories_become_stale_as_time_passes(conn):
    roberto, mem_id = roberto_memory(conn, "Runs Metalúrgica Silva")
    assert not memory.is_stale(conn, memory.get_memory(conn, roberto, mem_id))
    clock.advance(conn, memory.STALE_AFTER_DAYS)
    assert memory.is_stale(conn, memory.get_memory(conn, roberto, mem_id))


def test_the_model_is_told_which_memories_may_be_out_of_date(conn, fake):
    orchestrator.handle_message(conn, "chat", "ana", "Hi")
    system = fake.calls_for("ConciergeReply")[-1]["system"]
    fresh, stale = system.split("Possibly out of date (not confirmed in over 6 months):")
    assert "(role) Leads a customer service team of 400 people" in stale
    assert "(expertise) Led an AI rollout in customer service operations" in fresh


def test_one_new_fact_can_replace_several_outdated_ones(conn, fake):
    """Seen live: the model had saved both 'Is the CEO of X' and 'Works at X'."""
    roberto, ceo_id = roberto_memory(conn, "Is the CEO of Metalúrgica Silva", category="role")
    _, works_id = roberto_memory(conn, "Works at Metalúrgica Silva", visibility="shareable")
    fake.candidates = [candidate("Runs Aço Novo", "now run Aço Novo", category="company",
                                 replaces=[ceo_id, works_id])]
    (new, previous), = orchestrator.handle_message(conn, "chat", "roberto", MOVED).updated_memories

    assert [m["content"] for m in memory.list_memories(conn, roberto)] == ["Runs Aço Novo"]
    assert {p["content"] for p in previous} == {"Is the CEO of Metalúrgica Silva", "Works at Metalúrgica Silva"}

    memory.undo_replace(conn, roberto, previous)
    restored = {m["content"]: m["visibility"] for m in memory.list_memories(conn, roberto)}
    assert restored == {"Is the CEO of Metalúrgica Silva": "private", "Works at Metalúrgica Silva": "shareable"}
