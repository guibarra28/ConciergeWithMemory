"""Streamlit entry point for the Concierge with Memory prototype."""

import json
import sys
import time
from pathlib import Path

# `streamlit run app/main.py` only puts app/ on the path; add the project root
# so `from app...` imports work locally and on Railway.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app import auth, db, memory
from app.llm import LLMError
from app.orchestrator import TurnResult, handle_message

CHANNEL_LABEL = {"chat": "💬 Chat", "email": "✉️ Email"}
VISIBILITY_LABEL = {"private": "🔒 Private", "shareable": "🤝 Shareable", "internal": "🏷️ Internal (team note)"}
CATEGORY_LABEL = {
    "role": "Role", "company": "Company", "goal": "Goals", "preference": "Preferences",
    "interest": "Interests", "expertise": "Expertise", "personal": "Personal", "health": "Health",
}
OTHER_ADDRESS = "Another address…"
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 30

st.set_page_config(page_title="Concierge", page_icon="🛎️", layout="wide")


@st.cache_resource
def get_conn(db_path: str):
    # Keyed by path, so a different DB_PATH gets its own connection.
    conn = db.connect(db_path)
    db.init_db(conn)
    return conn


conn = get_conn(str(db.get_db_path()))


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
member_id = current["id"]


def remember_turn(result: TurnResult, channel: str) -> None:
    """Keep what the last turn saved or needs to confirm, to show notices with Undo."""
    st.session_state.notices = {
        "saved": [m["id"] for m in result.saved_memories],
        "pending": [c.model_dump() for c in result.needs_confirmation],
        "source_message_id": result.inbound_message_id,
        "channel": channel,
    }


# --- Sidebar: account and memory panel (R1) ----------------------------------
def memory_panel() -> None:
    st.header("What I know about you")

    paused = memory.is_paused(conn, member_id)
    new_paused = st.toggle("Pause memory", value=paused,
                           help="While paused, nothing new is saved. What is already saved is still used.")
    if new_paused != paused:
        memory.set_paused(conn, member_id, new_paused)
        st.rerun()
    if new_paused:
        st.caption("⏸️ Memory is paused. I won't save anything new.")

    memories = memory.list_memories(conn, member_id)
    if not memories:
        st.caption("Nothing saved yet. As we talk, I'll save durable facts you tell me, and you can undo any of them.")

    for category in CATEGORY_LABEL:
        group = [m for m in memories if m["category"] == category]
        if not group:
            continue
        st.markdown(f"**{CATEGORY_LABEL[category]}**")
        for m in group:
            with st.expander(m["content"]):
                st.caption(f"{VISIBILITY_LABEL[m['visibility']]} · saved {m['created_at']} UTC")
                if m["source_excerpt"]:
                    st.caption(f"From your words: “{m['source_excerpt']}”")
                new_content = st.text_area("Edit", value=m["content"], key=f"content_{m['id']}")

                if m["visibility"] == "internal":
                    new_visibility = None
                elif m["sensitive"]:
                    new_visibility = None
                    st.caption("Sensitive memories stay private and are never used in intros.")
                else:
                    new_visibility = st.radio(
                        "Who can it help?", ["private", "shareable"],
                        index=0 if m["visibility"] == "private" else 1,
                        format_func=lambda v: {"private": "Only my conversations",
                                               "shareable": "Intros I approve"}[v],
                        key=f"vis_{m['id']}", horizontal=True,
                    )

                save_col, delete_col = st.columns(2)
                if save_col.button("Save", key=f"save_{m['id']}"):
                    try:
                        memory.update_memory(conn, member_id, m["id"], content=new_content, visibility=new_visibility)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                if delete_col.button("Delete", key=f"del_{m['id']}"):
                    memory.delete_memory(conn, member_id, m["id"])
                    st.rerun()

    st.divider()
    with st.expander("Delete everything"):
        st.caption("Removes every memory and your whole conversation history. This can't be undone.")
        confirm = st.checkbox("I understand", key="confirm_delete_all")
        if st.button("Delete everything", disabled=not confirm, type="primary"):
            memory.delete_everything(conn, member_id)
            st.session_state.pop("notices", None)
            st.session_state.pop("last_email", None)
            st.rerun()


with st.sidebar:
    st.write(f"Signed in as **{current['display_name']}**")
    st.caption(f"{current['title'] or current['role']}")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    if current["role"] != "manager":
        st.divider()
        memory_panel()

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
def show_notices() -> None:
    """'Saved · Undo' for new memories and a yes/no for sensitive ones (H1)."""
    notices = st.session_state.get("notices")
    if not notices:
        return

    saved = memory.get_memories_by_ids(conn, member_id, notices["saved"])
    for m in saved:
        text_col, undo_col = st.columns([5, 1])
        text_col.success(f"Saved: {m['content']}", icon="🧠")
        if undo_col.button("Undo", key=f"undo_{m['id']}"):
            memory.delete_memory(conn, member_id, m["id"])
            notices["saved"].remove(m["id"])
            st.rerun()

    for i, data in enumerate(list(notices["pending"])):
        candidate = memory.MemoryCandidate(**data)
        with st.container(border=True):
            st.write(f"This sounds personal: **{candidate.content}**. Do you want me to remember it?")
            st.caption("It would stay private and never be used in intros.")
            yes, no = st.columns(2)
            if yes.button("Remember privately", key=f"pending_yes_{i}"):
                saved_row = memory.confirm_sensitive(
                    conn, member_id, candidate, notices["source_message_id"], notices["channel"])
                notices["pending"].pop(i)
                if saved_row:
                    notices["saved"].append(saved_row["id"])
                st.rerun()
            if no.button("Don't remember", key=f"pending_no_{i}"):
                notices["pending"].pop(i)
                st.rerun()


def used_memories_line(msg) -> str | None:
    ids = json.loads(msg["used_memory_ids"]) if msg["used_memory_ids"] else []
    rows = memory.get_memories_by_ids(conn, member_id, ids)
    if not rows:
        return None
    return "Used: " + " · ".join(m["content"] for m in rows)


timeline_col, email_col = st.columns([3, 2], gap="large")

with email_col:
    st.subheader("Simulated email inbox")
    st.caption(
        "Pretend you emailed the concierge instead of using the chat. Pick one of your addresses, "
        "or type another to see how an unknown sender is handled."
    )
    addresses = db.list_email_identities(conn, member_id)
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
                if result.identity.is_known:
                    remember_turn(result, "email")
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

    timeline = db.get_timeline(conn, member_id)
    if not timeline:
        st.write("No messages yet. Say hello in the chat, or send an email from one of your addresses.")
    for msg in timeline:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        with st.chat_message(role):
            st.caption(f"{CHANNEL_LABEL[msg['channel']]} · {msg['created_at']} UTC")
            st.markdown(msg["body"])
            if msg["context_excluded"]:
                st.caption("🙈 Forgotten: you deleted a memory from this exchange, so I no longer read it.")
            if role == "assistant" and (line := used_memories_line(msg)):
                st.caption(f"🧠 {line}")

    show_notices()

if prompt := st.chat_input("Message the concierge"):
    chat_login = db.get_chat_login(conn, member_id)
    try:
        with st.spinner("Thinking..."):
            result = handle_message(conn, "chat", chat_login, prompt)
        remember_turn(result, "chat")
    except LLMError as exc:
        st.error(f"Your message was saved, but the concierge could not reply: {exc}")
    else:
        st.rerun()
