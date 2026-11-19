"""UI tests for app/main.py using Streamlit's AppTest (no browser, no LLM calls)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import auth

APP = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def sign_in(at, email, password):
    at.text_input[0].input(email)
    at.text_input[1].input(password)
    at.button[0].click()
    at.run()


def test_shows_login_first(app):
    assert not app.exception
    assert any(s.value == "Sign in" for s in app.subheader)
    assert "member_id" not in app.session_state


def test_member_signs_in_and_sees_timeline(app):
    sign_in(app, "roberto@metalurgica-silva.example", auth.demo_password())
    assert not app.exception
    assert any("Roberto Silva" in m.value for m in app.sidebar.markdown)
    assert any(s.value == "Your timeline" for s in app.subheader)
    # The email form offers Roberto's own addresses, so he does not type them.
    assert "roberto.silva@personal.example" in app.selectbox[0].options


def test_wrong_password_shows_generic_error(app):
    sign_in(app, "roberto@metalurgica-silva.example", "wrong")
    assert "member_id" not in app.session_state
    assert app.error[0].value == auth.LOGIN_FAILED_MESSAGE


def test_manager_sees_manager_view(app):
    sign_in(app, "juliana@community.example", auth.demo_password())
    assert any(s.value == "Intro board" for s in app.subheader)
    assert not any(s.value == "Your timeline" for s in app.subheader)


def test_memory_panel_lists_edits_and_deletes(monkeypatch, tmp_path):
    from app import db, memory
    from tests.fakes import candidate

    path = tmp_path / "panel.db"
    monkeypatch.setenv("DB_PATH", str(path))
    setup = db.connect(path)
    db.init_db(setup)
    msg = db.add_message(setup, member_id=1, channel="chat", direction="inbound",
                         body="I want to automate quality control")
    mem_id = memory.apply_candidates(setup, 1, [candidate()], "I want to automate quality control",
                                     msg, "chat").saved[0]["id"]

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    sign_in(at, "roberto@metalurgica-silva.example", auth.demo_password())
    assert any(e.label == "Wants to automate quality control" for e in at.sidebar.expander)

    at.sidebar.text_area(key=f"content_{mem_id}").input("Automate QC in 2027")
    at.sidebar.button(key=f"save_{mem_id}").click()
    at.run()
    assert memory.get_memory(setup, 1, mem_id)["content"] == "Automate QC in 2027"

    at.sidebar.button(key=f"del_{mem_id}").click()
    at.run()
    assert memory.list_memories(setup, 1) == []
    assert not any(e.label == "Automate QC in 2027" for e in at.sidebar.expander)


def test_pause_toggle(app):
    from app import db, memory

    sign_in(app, "ana@varejo-souza.example", auth.demo_password())
    app.sidebar.toggle[0].set_value(True)
    app.run()
    conn = db.connect()
    assert memory.is_paused(conn, 2)


def test_intro_flow_through_the_interface(monkeypatch, tmp_path):
    """Juliana proposes Ana, Roberto asks, Ana accepts, then the meeting is booked."""
    from app import db, workflow
    from tests.fakes import FakeLLM

    path = tmp_path / "flow.db"
    monkeypatch.setenv("DB_PATH", str(path))
    fake = FakeLLM()
    monkeypatch.setattr(workflow, "call_llm", fake)
    setup = db.connect(path)
    db.init_db(setup)
    fake.suggested_ids = [2]  # Ana
    wf_id = workflow.request_intro(setup, 1, "Someone who automated quality control")["id"]

    def as_person(email):
        at = AppTest.from_file(APP, default_timeout=30)
        at.run()
        sign_in(at, email, auth.demo_password())
        assert not at.exception
        return at

    juliana = as_person("juliana@community.example")
    juliana.button(key=f"propose_{wf_id}").click()
    juliana.run()
    assert workflow.get(setup, wf_id)["state"] == "awaiting_a"

    roberto = as_person("roberto@metalurgica-silva.example")
    roberto.button(key=f"ask_{wf_id}").click()
    roberto.run()
    assert workflow.get(setup, wf_id)["state"] == "awaiting_b"

    ana = as_person("ana@varejo-souza.example")
    assert any("Roberto Silva" in m.value and "would like to meet you" in m.value for m in ana.markdown)
    ana.button(key=f"accept_{wf_id}").click()
    ana.run()
    assert workflow.get(setup, wf_id)["state"] == "introduced"

    ana.button(key=f"book_{wf_id}").click()
    ana.run()
    assert workflow.get(setup, wf_id)["state"] == "scheduled"

    for _ in range(4):
        ana.button(key="advance_day").click()
        ana.run()
    assert workflow.get(setup, wf_id)["state"] == "follow_up"


def test_member_cannot_send_simulated_email_as_another_member(app):
    from app import db

    sign_in(app, "ana@varejo-souza.example", auth.demo_password())
    app.selectbox[0].set_value("Another address…")
    other = next(t for t in app.text_input if t.label == "Other address")
    other.input("roberto@metalurgica-silva.example")
    next(t for t in app.text_area if t.label == "Message").input("What do you know about me?")
    next(b for b in app.button if b.label == "Send email").click()
    app.run()

    assert any("belongs to another member" in e.value for e in app.error)
    conn = db.connect()
    assert db.get_timeline(conn, 1) == []  # nothing reached Roberto's timeline


def test_manager_profiles_show_no_private_memories(app):
    sign_in(app, "juliana@community.example", auth.demo_password())
    shown = " ".join(m.value for m in app.markdown)
    assert "Implemented computer vision for package inspection" in shown  # Carlos, shareable
    assert "Is considering leaving his job next year" not in shown        # Carlos, private


def test_no_emojis_in_the_interface():
    import re

    source = Path(APP).read_text(encoding="utf-8")
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", source)


def test_sign_out(app):
    sign_in(app, "ana@varejo-souza.example", auth.demo_password())
    app.sidebar.button[0].click()
    app.run()
    assert "member_id" not in app.session_state
    assert any(s.value == "Sign in" for s in app.subheader)
