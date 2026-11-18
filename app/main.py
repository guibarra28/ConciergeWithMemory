"""Streamlit entry point for the Concierge with Memory prototype."""

import sys
from pathlib import Path

# `streamlit run app/main.py` only puts app/ on the path; add the project root
# so `from app...` imports work locally and on Railway.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app import db
from app.llm import LLMError
from app.orchestrator import handle_message

CHANNEL_LABEL = {"chat": "💬 Chat", "email": "✉️ Email"}

st.set_page_config(page_title="Concierge", page_icon="🛎️", layout="wide")


@st.cache_resource
def get_conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


conn = get_conn()
people = db.list_people(conn)

# --- Sidebar: who is using the app -------------------------------------------
with st.sidebar:
    st.header("Who is using the app")
    labels = {p["id"]: f"{p['display_name']} ({p['role']})" for p in people}
    current_id = st.selectbox("Signed in as", options=list(labels), format_func=labels.get)
    current = next(p for p in people if p["id"] == current_id)
    st.caption("Persona selector for the prototype. Real login is out of scope (see PRD).")

st.title("🛎️ Concierge")

# --- Manager view ------------------------------------------------------------
if current["role"] == "manager":
    st.subheader("Manager view")
    st.info(
        "Managers see intro status, owners and deadlines, never the content of member "
        "conversations. The intro board arrives with the workflow block (b3)."
    )
    st.metric("Messages from unknown senders in quarantine", db.count_quarantined(conn))
    st.stop()

# --- Member view -------------------------------------------------------------
timeline_col, email_col = st.columns([3, 2], gap="large")

with email_col:
    st.subheader("Simulated email inbox")
    st.caption(
        "Pretend an email reaches the concierge. Try one of this member's other addresses, "
        "or an address nobody registered."
    )
    with st.form("email_form", clear_on_submit=True):
        sender = st.text_input("From", placeholder="name@company.example")
        body = st.text_area("Message", height=120)
        sent = st.form_submit_button("Receive email")

    if sent:
        if not sender.strip() or not body.strip():
            st.warning("Fill in both the sender and the message.")
        else:
            try:
                with st.spinner("Reading the email..."):
                    result = handle_message(conn, "email", sender, body.strip())
                st.session_state.last_email = (sender.strip(), result.identity.is_known, result.reply)
            except LLMError as exc:
                st.session_state.last_email = None
                st.error(f"The email was saved, but the concierge could not reply: {exc}")

    last = st.session_state.get("last_email")
    if last:
        address, known, reply = last
        if known:
            st.success(f"Reply sent to {address}")
        else:
            st.warning(f"Unknown sender: {address}. No member data was used.")
        st.markdown(f"> {reply}")

with timeline_col:
    st.subheader(f"Timeline · {current['display_name']}")
    st.caption("Chat and email from this member, in one history.")

    timeline = db.get_timeline(conn, current["id"])
    if not timeline:
        st.write("No messages yet. Say hello in the chat, or send an email from one of this member's addresses.")
    for msg in timeline:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        with st.chat_message(role):
            st.caption(f"{CHANNEL_LABEL[msg['channel']]} · {msg['created_at']} UTC")
            st.markdown(msg["body"])

if prompt := st.chat_input(f"Message the concierge as {current['display_name']}"):
    try:
        with st.spinner("Thinking..."):
            handle_message(conn, "chat", current["chat_login"], prompt)
    except LLMError as exc:
        st.error(f"Your message was saved, but the concierge could not reply: {exc}")
    else:
        st.rerun()
