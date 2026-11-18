"""Streamlit entry point for the Concierge with Memory prototype."""

import sys
import time
from pathlib import Path

# `streamlit run app/main.py` only puts app/ on the path; add the project root
# so `from app...` imports work locally and on Railway.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app import auth, db
from app.llm import LLMError
from app.orchestrator import handle_message

CHANNEL_LABEL = {"chat": "💬 Chat", "email": "✉️ Email"}
OTHER_ADDRESS = "Another address…"
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 30

st.set_page_config(page_title="Concierge", page_icon="🛎️", layout="wide")


@st.cache_resource
def get_conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


conn = get_conn()


# --- Login -------------------------------------------------------------------
def show_login() -> None:
    st.title("🛎️ Concierge")
    left, right = st.columns([2, 3], gap="large")

    with left:
        st.subheader("Sign in")
        locked_until = st.session_state.get("locked_until", 0)
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            if time.time() < locked_until:
                st.error(f"Too many attempts. Try again in {int(locked_until - time.time()) + 1} seconds.")
                return
            result = auth.authenticate(conn, email, password)
            if result.ok:
                st.session_state.member_id = result.identity.member_id
                st.session_state.failed_logins = 0
                st.rerun()
            failures = st.session_state.get("failed_logins", 0) + 1
            st.session_state.failed_logins = failures
            if failures >= MAX_FAILED_LOGINS:
                st.session_state.locked_until = time.time() + LOCKOUT_SECONDS
                st.session_state.failed_logins = 0
            st.error(result.error)

    with right:
        st.subheader("Demo accounts")
        st.caption("Fictional people from the discovery. All demo accounts share one password.")
        st.table(
            [{"Person": r["display_name"], "Role": r["role"], "Email": r["address"]}
             for r in db.list_demo_logins(conn)]
        )
        st.code(auth.demo_password(), language=None)


if "member_id" not in st.session_state:
    show_login()
    st.stop()

current = db.get_member(conn, st.session_state.member_id)
if current is None:  # account removed while signed in
    st.session_state.clear()
    st.rerun()

# --- Sidebar -----------------------------------------------------------------
with st.sidebar:
    st.write(f"Signed in as **{current['display_name']}**")
    st.caption(f"{current['title'] or current['role']}")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

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
        "Pretend you emailed the concierge instead of using the chat. Pick one of your addresses, "
        "or type another to see how an unknown sender is handled."
    )
    addresses = db.list_email_identities(conn, current["id"])
    options = [a["address"] for a in addresses] + [OTHER_ADDRESS]
    unverified = {a["address"] for a in addresses if not a["verified"]}

    with st.form("email_form", clear_on_submit=True):
        choice = st.selectbox(
            "From", options,
            format_func=lambda a: f"{a} (not verified)" if a in unverified else a,
        )
        other = st.text_input("Other address", placeholder="name@company.example",
                              help=f"Used only when '{OTHER_ADDRESS}' is selected.")
        body = st.text_area("Message", height=120)
        sent = st.form_submit_button("Send email")

    if sent:
        sender = other.strip() if choice == OTHER_ADDRESS else choice
        if not sender or not body.strip():
            st.warning("Fill in the sender and the message.")
        else:
            try:
                with st.spinner("Reading the email..."):
                    result = handle_message(conn, "email", sender, body.strip())
                st.session_state.last_email = (sender, result.identity.is_known, result.reply)
            except LLMError as exc:
                st.session_state.last_email = None
                st.error(f"The email was saved, but the concierge could not reply: {exc}")

    last = st.session_state.get("last_email")
    if last:
        address, known, reply = last
        if known:
            st.success(f"Reply sent to {address}. It is also in your timeline.")
        else:
            st.warning(f"{address} is not a verified address, so no account data was used.")
        st.markdown(f"> {reply}")

with timeline_col:
    st.subheader("Your timeline")
    st.caption("Chat and email in one history.")

    timeline = db.get_timeline(conn, current["id"])
    if not timeline:
        st.write("No messages yet. Say hello in the chat, or send an email from one of your addresses.")
    for msg in timeline:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        with st.chat_message(role):
            st.caption(f"{CHANNEL_LABEL[msg['channel']]} · {msg['created_at']} UTC")
            st.markdown(msg["body"])

if prompt := st.chat_input("Message the concierge"):
    chat_login = db.get_chat_login(conn, current["id"])
    try:
        with st.spinner("Thinking..."):
            handle_message(conn, "chat", chat_login, prompt)
    except LLMError as exc:
        st.error(f"Your message was saved, but the concierge could not reply: {exc}")
    else:
        st.rerun()
