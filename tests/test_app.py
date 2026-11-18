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
    assert any(s.value == "Manager view" for s in app.subheader)


def test_sign_out(app):
    sign_in(app, "ana@varejo-souza.example", auth.demo_password())
    app.sidebar.button[0].click()
    app.run()
    assert "member_id" not in app.session_state
    assert any(s.value == "Sign in" for s in app.subheader)
