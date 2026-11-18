import pytest

from app import db, memory
from tests.conftest import member_id
from tests.fakes import FakeLLM, candidate

MESSAGE = "I'm the CEO and I want to automate quality control this year. My daughter is sick."


@pytest.fixture
def roberto(conn):
    return member_id(conn, "Roberto Silva")


@pytest.fixture
def msg_id(conn, roberto):
    return db.add_message(conn, member_id=roberto, channel="chat", direction="inbound", body=MESSAGE)


def apply(conn, member, candidates, msg_id, text=MESSAGE):
    return memory.apply_candidates(conn, member, candidates, text, msg_id, "chat")


# --- What gets saved ---------------------------------------------------------
def test_saves_stated_fact_as_private(conn, roberto, msg_id):
    out = apply(conn, roberto, [candidate()], msg_id)
    assert len(out.saved) == 1
    saved = out.saved[0]
    assert saved["visibility"] == "private"
    assert saved["source_message_id"] == msg_id
    assert saved["source_excerpt"] == "automate quality control"


def test_drops_candidate_whose_excerpt_is_not_in_the_message(conn, roberto, msg_id):
    inferred = candidate("Is probably expanding to Europe", "expanding to Europe")
    out = apply(conn, roberto, [inferred], msg_id)
    assert out.saved == []
    assert out.discarded[0][1] == "excerpt not found in message"


def test_excerpt_match_ignores_case_and_spacing(conn, roberto, msg_id):
    out = apply(conn, roberto, [candidate(excerpt="  AUTOMATE   quality control ")], msg_id)
    assert len(out.saved) == 1


def test_excerpt_match_ignores_punctuation_the_model_adds(conn, roberto, msg_id):
    # The message says "My daughter is sick." mid-sentence; the model may add or drop punctuation.
    sick = candidate("Daughter is sick", "my daughter is sick!", category="health", sensitive=True)
    out = apply(conn, roberto, [sick], msg_id)
    assert len(out.needs_confirmation) == 1


def test_excerpt_must_match_whole_words(conn, roberto, msg_id):
    out = apply(conn, roberto, [candidate("Is a CE", "the CE")], msg_id)
    assert out.saved == []


def test_temporary_fact_gets_an_expiry(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate(expires_in_days=90)], msg_id).saved[0]
    assert mem["valid_until"] is not None
    assert conn.execute("SELECT ? > datetime('now', '+89 days')", (mem["valid_until"],)).fetchone()[0] == 1


def test_drops_low_confidence(conn, roberto, msg_id):
    out = apply(conn, roberto, [candidate(confidence=0.4)], msg_id)
    assert out.saved == [] and out.discarded[0][1] == "low confidence"


def test_sensitive_needs_confirmation_and_is_not_saved(conn, roberto, msg_id):
    sick = candidate("Daughter is sick", "My daughter is sick", category="health", sensitive=True)
    out = apply(conn, roberto, [sick], msg_id)
    assert out.saved == []
    assert out.needs_confirmation == [sick]
    assert memory.list_memories(conn, roberto) == []


def test_sensitive_category_needs_confirmation_even_if_not_flagged(conn, roberto, msg_id):
    out = apply(conn, roberto, [candidate("Daughter is sick", "My daughter is sick", category="personal")], msg_id)
    assert out.saved == [] and len(out.needs_confirmation) == 1


def test_confirmed_sensitive_is_saved_private_and_flagged(conn, roberto, msg_id):
    sick = candidate("Daughter is sick", "My daughter is sick", category="health", sensitive=True)
    row = memory.confirm_sensitive(conn, roberto, sick, msg_id, "chat")
    assert row["sensitive"] == 1 and row["visibility"] == "private"


def test_skips_duplicates(conn, roberto, msg_id):
    apply(conn, roberto, [candidate()], msg_id)
    out = apply(conn, roberto, [candidate(content="wants to automate  quality control")], msg_id)
    assert out.saved == [] and out.discarded[0][1] == "duplicate"


def test_nothing_is_saved_while_paused(conn, roberto, msg_id, monkeypatch):
    fake = FakeLLM()
    fake.candidates = [candidate()]
    monkeypatch.setattr(memory, "call_llm", fake)
    memory.set_paused(conn, roberto, True)

    out = memory.extract_and_save(conn, roberto, MESSAGE, msg_id, "chat")
    assert out.saved == [] and out.needs_confirmation == []
    assert out.discarded[0][1] == "memory paused"
    assert memory.list_memories(conn, roberto) == []
    sick = candidate("Daughter is sick", "My daughter is sick", category="health", sensitive=True)
    assert memory.confirm_sensitive(conn, roberto, sick, msg_id, "chat") is None


def test_extractor_sees_known_memories(conn, roberto, msg_id, monkeypatch):
    apply(conn, roberto, [candidate()], msg_id)
    fake = FakeLLM()
    monkeypatch.setattr(memory, "call_llm", fake)
    memory.extract_and_save(conn, roberto, "Hello", msg_id, "chat")
    assert "Wants to automate quality control" in fake.calls[0]["messages"][0]["content"]


# --- Member control (R1) and isolation ---------------------------------------
def test_edit_content_and_visibility(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    assert memory.update_memory(conn, roberto, mem["id"], content="Automating QC in 2027", visibility="shareable")
    updated = memory.get_memory(conn, roberto, mem["id"])
    assert updated["content"] == "Automating QC in 2027"
    assert updated["visibility"] == "shareable"


def test_member_cannot_set_internal(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    with pytest.raises(ValueError):
        memory.update_memory(conn, roberto, mem["id"], visibility="internal")


def test_sensitive_cannot_be_shared(conn, roberto, msg_id):
    sick = candidate("Daughter is sick", "My daughter is sick", category="health", sensitive=True)
    row = memory.confirm_sensitive(conn, roberto, sick, msg_id, "chat")
    with pytest.raises(ValueError):
        memory.update_memory(conn, roberto, row["id"], visibility="shareable")


def test_empty_edit_is_rejected(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    with pytest.raises(ValueError):
        memory.update_memory(conn, roberto, mem["id"], content="   ")


def test_delete_removes_the_row(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    assert memory.delete_memory(conn, roberto, mem["id"])
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = ?", (mem["id"],)).fetchone()[0] == 0


def test_delete_hides_the_source_exchange_from_the_model(conn, roberto, msg_id):
    reply_id = db.add_message(conn, member_id=roberto, channel="chat", direction="outbound", body="Noted!")
    later_id = db.add_message(conn, member_id=roberto, channel="chat", direction="inbound", body="Later message")
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]

    memory.delete_memory(conn, roberto, mem["id"])

    for_model = [m["id"] for m in db.get_timeline(conn, roberto, for_model=True)]
    assert for_model == [later_id]
    # The member still sees the whole history.
    assert [m["id"] for m in db.get_timeline(conn, roberto)] == [msg_id, reply_id, later_id]


def test_old_database_gets_the_new_column(tmp_path):
    path = tmp_path / "old.db"
    old = db.connect(path)
    old.executescript("""CREATE TABLE messages (id INTEGER PRIMARY KEY, member_id INTEGER,
        channel_id INTEGER, direction TEXT, sender_address TEXT, body TEXT,
        quarantined INTEGER DEFAULT 0, used_memory_ids TEXT, created_at TEXT)""")
    old.close()
    upgraded = db.connect(path)
    db.init_db(upgraded)
    columns = {r["name"] for r in upgraded.execute("PRAGMA table_info(messages)")}
    assert "context_excluded" in columns


def test_member_cannot_touch_another_members_memory(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    ana = member_id(conn, "Ana Souza")
    assert memory.get_memory(conn, ana, mem["id"]) is None
    assert not memory.update_memory(conn, ana, mem["id"], content="hacked")
    assert not memory.delete_memory(conn, ana, mem["id"])
    assert memory.get_memory(conn, roberto, mem["id"])["content"] == "Wants to automate quality control"


def test_context_only_has_own_memories(conn, roberto, msg_id):
    apply(conn, roberto, [candidate()], msg_id)
    ana = member_id(conn, "Ana Souza")
    assert all(m["member_id"] == ana for m in memory.get_context(conn, ana))
    assert "Wants to automate quality control" not in [m["content"] for m in memory.get_context(conn, ana)]
    assert [m["content"] for m in memory.get_context(conn, roberto)] == ["Wants to automate quality control"]


def test_action_items_are_saved_even_while_paused(conn, roberto, msg_id, monkeypatch):
    from tests.fakes import action_item

    fake = FakeLLM()
    fake.action_items = [action_item(excerpt="automate quality control")]
    monkeypatch.setattr(memory, "call_llm", fake)
    memory.set_paused(conn, roberto, True)
    out = memory.extract_and_save(conn, roberto, MESSAGE, msg_id, "chat")
    assert [a["description"] for a in out.action_items] == ["Send the QC deck to Ana"]


def test_expired_memory_is_left_out_of_context(conn, roberto, msg_id):
    mem = apply(conn, roberto, [candidate()], msg_id).saved[0]
    conn.execute("UPDATE memories SET valid_until = datetime('now', '-1 day') WHERE id = ?", (mem["id"],))
    assert memory.get_context(conn, roberto) == []


def test_delete_everything(conn, roberto, msg_id):
    apply(conn, roberto, [candidate()], msg_id)
    ana = member_id(conn, "Ana Souza")
    db.add_message(conn, member_id=ana, channel="chat", direction="inbound", body="Ana's message")

    memory.delete_everything(conn, roberto)
    assert memory.list_memories(conn, roberto) == []
    assert db.get_timeline(conn, roberto) == []
    assert len(db.get_timeline(conn, ana)) == 1  # nobody else is affected
