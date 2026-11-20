"""Streamlit entry point for the Concierge with Memory prototype."""

import html
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

# `streamlit run app/main.py` only puts app/ on the path; add the project root
# so `from app...` imports work locally and on Railway.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app import actions, auth, clock, db, memory, permissions, workflow
from app.identity import resolve
from app.llm import LLMError
from app.orchestrator import AlreadyAnswered, ReplyFailed, TurnResult, handle_message, retry_reply, unanswered_message

CHANNEL_LABEL = {"chat": ":material/chat: Chat", "email": ":material/mail: Email"}
VISIBILITY_LABEL = {
    "private": ":material/lock: Private",
    "shareable": ":material/group: Shareable",
    "internal": ":material/badge: Team note",
}
CATEGORY_LABEL = {
    "role": ("Role", "badge"), "company": ("Company", "apartment"), "goal": ("Goals", "flag"),
    "preference": ("Preferences", "tune"), "interest": ("Interests", "interests"),
    "expertise": ("Expertise", "school"), "personal": ("Personal", "person"),
    "health": ("Health", "favorite"),
}
STATE_LABEL = workflow.STATE_LABEL
OTHER_ADDRESS = "Another address…"
REPLY_FAILED = ("Your message is saved, but the concierge couldn't reply. Select Try again under your "
                "message; you don't need to type it again.")
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 30

st.set_page_config(page_title="Concierge", page_icon=":material/room_service:", layout="wide")

# Small layout touches the theme can't express. Colors come from currentColor, so they
# follow the light and dark themes in .streamlit/config.toml.
st.markdown("""<style>
.block-container {padding-top: 2.2rem; max-width: 1320px;}
.eyebrow {font-size: .72rem; font-weight: 600; letter-spacing: .12em; text-transform: uppercase;
          opacity: .6; margin-bottom: -.4rem;}
.profile {display: flex; gap: .75rem; align-items: center; margin: .25rem 0 .5rem;}
.profile .avatar {flex: none; width: 2.5rem; height: 2.5rem; border-radius: 50%; display: grid;
                  place-items: center; font-weight: 600; font-size: .9rem;
                  background: color-mix(in srgb, currentColor 9%, transparent);
                  border: 1px solid color-mix(in srgb, currentColor 22%, transparent);}
.profile .who b {display: block; line-height: 1.2;}
.profile .who span {font-size: .82rem; opacity: .7;}
</style>""", unsafe_allow_html=True)


def eyebrow(text: str, where=st) -> None:
    where.markdown(f"<div class='eyebrow'>{html.escape(text)}</div>", unsafe_allow_html=True)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


@st.cache_resource
def get_conn(db_path: str):
    # Keyed by path, so a different DB_PATH gets its own connection.
    conn = db.connect(db_path)
    db.init_db(conn)
    return conn


conn = get_conn(str(db.get_db_path()))


def fmt_date(value: str | None) -> str:
    return clock.parse(value).strftime("%a %d %b") if value else ""


# --- Login -------------------------------------------------------------------
def show_login() -> None:
    _, page, _ = st.columns([1, 6, 1])
    with page:
        eyebrow("Members' concierge")
        st.title("Concierge")
        st.write("Remembers what you tell it across chat and email, shows you everything it keeps, "
                 "and introduces you to other members only when both of you agree.")
        st.write("")
        left, right = st.columns([2, 3], gap="large")
    login_form(left)
    demo_accounts(right)


def login_form(where) -> None:
    with where.container(border=True):
        st.subheader("Sign in")
        locked_until = st.session_state.get("locked_until", 0)
        with st.form("login", border=False):
            email = st.text_input("Email", placeholder="you@company.example")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

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


def demo_accounts(where) -> None:
    with where:
        st.subheader("Demo accounts")
        st.caption("Fictional people from the discovery. All of them use this password:")
        st.code(auth.demo_password(), language=None)
        st.dataframe(
            [{"Person": r["display_name"], "Role": r["role"].capitalize(), "Email": r["address"]}
             for r in db.list_demo_logins(conn)],
            hide_index=True, use_container_width=True,
        )
        st.info("This is a public prototype. Don't type real personal or company information: "
                "anyone with a demo account can read what's saved in it.", icon=":material/info:")


if "member_id" not in st.session_state:
    show_login()
    st.stop()

current = db.get_member(conn, st.session_state.member_id)
if current is None:  # account removed while signed in
    st.session_state.clear()
    st.rerun()
member_id = current["id"]
is_manager = current["role"] == "manager"


def run_action(fn, *args, **kwargs) -> None:
    """Run a workflow or action change; show a clear message instead of a crash."""
    try:
        fn(*args, **kwargs)
    except workflow.WorkflowError as exc:
        st.session_state.flash = str(exc)
    except LLMError:
        st.session_state.flash = "That step didn't finish, so nothing changed. Try again in a minute."
    st.rerun()


# --- Sidebar: account, demo clock and memory panel (R1) ----------------------
def memory_panel() -> None:
    st.subheader("What I know about you")
    memories = memory.list_memories(conn, member_id)
    if memories:
        st.caption(f"{plural(len(memories), 'memory', 'memories')}. Private ones are used only in "
                   "your own conversations.")

    paused = memory.is_paused(conn, member_id)
    new_paused = st.toggle("Pause memory", value=paused,
                           help="While paused, nothing new is saved. What is already saved is still used.")
    if new_paused != paused:
        memory.set_paused(conn, member_id, new_paused)
        st.rerun()
    if new_paused:
        st.caption(":material/pause_circle: Memory is paused. I won't save anything new until you "
                   "turn it back on.")

    if not memories:
        st.caption("Nothing saved yet. When you tell me something worth keeping, like a goal or a "
                   "preference, I'll save it and show you, so you can undo it.")
    stale = {m["id"] for m in memories if memory.is_stale(conn, m)}
    if stale:
        st.info(f"{plural(len(stale), 'memory', 'memories')} not confirmed in 6 months. "
                f"Check that {'it is' if len(stale) == 1 else 'they are'} still true.",
                icon=":material/history:")

    for category, (label, icon) in CATEGORY_LABEL.items():
        group = [m for m in memories if m["category"] == category]
        if not group:
            continue
        st.markdown(f":material/{icon}: **{label}**")
        for m in group:
            with st.expander(m["content"], expanded=m["id"] in stale):
                st.caption(f"{VISIBILITY_LABEL[m['visibility']]} · saved {fmt_date(m['created_at'])}")
                if m["id"] in stale:
                    st.caption(f":material/history: Last confirmed {fmt_date(m['last_confirmed_at'])}. "
                               "Still true? If not, edit or delete it.")
                    if st.button("Still true", key=f"confirm_{m['id']}", icon=":material/check:"):
                        memory.confirm_still_true(conn, member_id, m["id"])
                        st.rerun()
                if m["source_excerpt"]:
                    st.caption(f"From your words: “{m['source_excerpt']}”")
                new_content = st.text_area("Edit", value=m["content"], key=f"content_{m['id']}")

                if m["visibility"] == "internal" or m["sensitive"]:
                    new_visibility = None
                    if m["sensitive"]:
                        st.caption("Sensitive memories stay private and are never used in intros.")
                else:
                    new_visibility = st.radio(
                        "Where can I use it?", ["private", "shareable"],
                        index=0 if m["visibility"] == "private" else 1,
                        format_func=lambda v: {"private": "Private: only in my conversations",
                                               "shareable": "Shareable: also in intros I approve"}[v],
                        key=f"vis_{m['id']}", horizontal=True,
                    )

                save_col, delete_col = st.columns(2)
                if save_col.button("Save changes", key=f"save_{m['id']}", type="primary",
                                   use_container_width=True):
                    try:
                        memory.update_memory(conn, member_id, m["id"], content=new_content, visibility=new_visibility)
                        st.session_state.flash_ok = "Changes saved."
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                if delete_col.button("Delete", key=f"del_{m['id']}", icon=":material/delete:",
                                     use_container_width=True):
                    if workflow.seen_by_others(conn, member_id, m["id"]):
                        st.session_state.confirm_delete = m["id"]
                    else:
                        memory.delete_memory(conn, member_id, m["id"])
                        st.session_state.flash_ok = f"Deleted: {m['content']}. I won't use it again."
                    st.rerun()
                if st.session_state.get("confirm_delete") == m["id"]:
                    confirm_delete_shared(m)

    st.divider()
    with st.expander("Delete everything"):
        st.caption("Removes every memory, your pending items and your whole conversation history. "
                   "Open intros are cancelled, and only the record that you agreed to them is kept.")
        confirm = st.checkbox("I understand this can't be undone", key="confirm_delete_all")
        if st.button("Delete everything", disabled=not confirm, type="primary"):
            memory.delete_everything(conn, member_id)
            st.session_state.pop("notices", None)
            st.session_state.pop("last_email", None)
            st.session_state.flash_ok = "Everything is deleted. I start from a blank page with you."
            st.rerun()


def confirm_delete_shared(m) -> None:
    """Deleting something another member already saw: say what deleting can and can't undo."""
    seen = workflow.seen_by_others(conn, member_id, m["id"])
    who = ", ".join(f"{s['other_name']} ({fmt_date(s['shared_at'])})" for s in seen)
    st.warning(f"You shared this in an intro, so {who} already saw it. Deleting removes it from the "
               "intro and from my memory, but can't take back what they read. They won't be told.",
               icon=":material/visibility:")
    yes, no = st.columns(2)
    if yes.button("Delete anyway", key=f"del_yes_{m['id']}", type="primary"):
        memory.delete_memory(conn, member_id, m["id"])
        st.session_state.pop("confirm_delete", None)
        names = ", ".join(s["other_name"] for s in seen)
        st.session_state.flash_ok = f"Deleted. It's no longer shown in your intro with {names}, and I won't use it again."
        st.rerun()
    if no.button("Keep it", key=f"del_no_{m['id']}"):
        st.session_state.pop("confirm_delete", None)
        st.rerun()


def initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper()


with st.sidebar:
    detail = " at ".join(p for p in (current["title"], current["company"]) if p) or current["role"].capitalize()
    st.markdown(
        f"<div class='profile'><span class='avatar'>{html.escape(initials(current['display_name']))}</span>"
        f"<span class='who'><b>{html.escape(current['display_name'])}</b>"
        f"<span>{html.escape(detail)}</span></span></div>",
        unsafe_allow_html=True,
    )
    if st.button("Sign out", icon=":material/logout:", type="tertiary"):
        st.session_state.clear()
        st.rerun()

    with st.container(border=True):
        eyebrow("Demo clock")
        st.markdown(f":material/calendar_today: **{clock.now(conn):%a %d %b %Y}**")
        st.caption("Move time forward to see reminders, expirations and follow-ups.")
        if st.button("Advance 1 day", icon=":material/fast_forward:", key="advance_day",
                     use_container_width=True):
            clock.advance(conn, 1)
            report = workflow.tick(conn)
            due = actions.remind_due(conn)
            st.session_state.flash_ok = (f"It's now {clock.now(conn):%a %d %b}. "
                                         f"{plural(report.transitions, 'intro update')}, "
                                         f"{plural(report.reminders + due, 'reminder')} sent.")
            st.rerun()

    if not is_manager:
        st.divider()
        memory_panel()

eyebrow("Team desk" if is_manager else "Members' concierge")
st.title(f"Hello, {current['display_name'].split()[0]}")
if msg := st.session_state.pop("flash", None):
    st.error(msg, icon=":material/error:")
if msg := st.session_state.pop("flash_ok", None):
    st.success(msg, icon=":material/check_circle:")


def retry_box(who: int) -> None:
    """If the last message never got a reply, say so and offer to answer it again."""
    pending = unanswered_message(conn, who)
    if pending is None:
        return
    with st.container(border=True):
        st.write(":material/sync_problem: Your last message has no reply yet. It's saved, "
                 "so you don't need to type it again.")
        if st.button("Try again", key="retry_reply", icon=":material/refresh:"):
            try:
                with st.spinner("Thinking..."):
                    result = retry_reply(conn, who, pending["id"])
                if (notices := st.session_state.get("notices")) is not None:
                    notices["intro"] = result.intro is not None
            except AlreadyAnswered:
                pass
            except LLMError:
                st.session_state.flash = ("Still no reply. Your message is saved; try again in a minute.")
            st.rerun()


def show_updates(who: int) -> None:
    rows = db.list_notifications(conn, who, limit=15)
    if not rows:
        st.caption("No updates yet.")
    for n in rows:
        st.markdown(f"{n['body']}  \n:gray[{fmt_date(n['created_at'])}]")


# --- Manager view ------------------------------------------------------------
def manager_view() -> None:
    intros = workflow.board(conn)
    open_intros = [w for w in intros if w["state"] in workflow.OPEN]
    waiting_on_me = [w for w in open_intros if w["owner_id"] == member_id]
    c1, c2, c3 = st.columns(3)
    c1.metric("Open intros", len(open_intros))
    c2.metric("Waiting on you", len(waiting_on_me))
    c3.metric("Emails from unknown senders", db.count_quarantined(conn),
              help="Held without a reply that uses any member data, until someone verifies the address.")
    st.caption("You see intro status, owners and deadlines. You never see what members tell the concierge.")

    board_col, side_col = st.columns([3, 2], gap="large")
    with board_col:
        st.subheader("Intro board")
        if not intros:
            st.caption("No intro requests yet. They appear here when a member asks the concierge to "
                       "meet someone.")
        for wf in intros:
            manager_intro_card(wf)
    with side_col:
        tab_ask, tab_profiles, tab_items, tab_updates = st.tabs(
            [":material/forum: Ask", ":material/group: Profiles", ":material/task_alt: Your items",
             ":material/notifications: Updates"])
        with tab_ask:
            st.caption("The concierge sees only what members shared for intros and team notes, "
                       "never private memories or conversations.")
            for msg in db.get_timeline(conn, member_id):
                with st.chat_message("user" if msg["direction"] == "inbound" else "assistant"):
                    st.markdown(msg["body"])
            retry_box(member_id)
        with tab_profiles:
            st.caption("Only what members marked Shareable, plus team notes. Private memories never appear here.")
            visible = permissions.memories_for(conn, current, "team")
            by_member: dict[str, list] = {}
            for m in visible:
                by_member.setdefault(m["owner_name"], []).append(m)
            if not by_member:
                st.caption("No member has shared anything yet.")
            for name, rows in by_member.items():
                with st.expander(name):
                    for m in rows:
                        st.markdown(f"- {m['content']}  \n  :gray[{VISIBILITY_LABEL[m['visibility']]}]")
        with tab_items:
            items = actions.list_owned_by(conn, member_id)
            if not items:
                st.caption("Nothing assigned to you.")
            for item in items:
                with st.container(border=True):
                    st.write(item["description"])
                    st.caption(f"For {item['member_name']} · due {fmt_date(item['due_date'])}")
                    if st.button("Done", key=f"mgr_done_{item['id']}", icon=":material/check:"):
                        actions.set_status(conn, item["id"], member_id, "done")
                        st.rerun()
        with tab_updates:
            show_updates(member_id)


def manager_intro_card(wf) -> None:
    a = db.get_member(conn, wf["requester_id"])
    b = db.get_member(conn, wf["target_id"]) if wf["target_id"] else None
    owner = db.get_member(conn, wf["owner_id"]) if wf["owner_id"] else None
    title = f"#{wf['id']} · {a['display_name']} → {b['display_name'] if b else '?'} · {STATE_LABEL[wf['state']]}"
    with st.expander(title, expanded=wf["owner_id"] == member_id):
        meta = []
        if owner:
            meta.append(f"Waiting on {owner['display_name']}")
        elif wf["state"] in ("introduced", "scheduled"):
            meta.append("Waiting on both members")
        if wf["deadline_at"]:
            meta.append(f"until {fmt_date(wf['deadline_at'])}")
        if meta:
            st.caption(" ".join(meta))
        st.write(f"**Request** (shared with the team to find a match): {wf['reason']}")

        if wf["state"] == "requested":
            suggestions = json.loads(wf["suggestions"]) if wf["suggestions"] else None
            if suggestions is None:
                st.warning("Candidates haven't been suggested yet.")
                if st.button("Find candidates", key=f"find_{wf['id']}", icon=":material/search:"):
                    run_action(workflow.refresh_suggestions, conn, wf["id"])
            else:
                by_id = {s["member_id"]: s["why"] for s in suggestions}
                choice = st.radio(
                    "Suggested by the concierge", list(by_id), key=f"pick_{wf['id']}",
                    format_func=lambda i: f"{workflow.name_title(db.get_member(conn, i))}: {by_id[i]}",
                )
                if st.button(f"Propose to {a['display_name'].split()[0]}", key=f"propose_{wf['id']}",
                             type="primary"):
                    run_action(workflow.propose, conn, wf["id"], member_id, choice)
            if st.button("Close as no match", key=f"nomatch_{wf['id']}"):
                run_action(workflow.apply, conn, wf["id"], "no_match", member_id, expected_version=wf["version"])

        if wf["state"] == "follow_up":
            outcome = st.text_input("How did it go?", key=f"outcome_{wf['id']}",
                                    placeholder="e.g. Good call, they plan a site visit")
            if st.button("Record outcome", key=f"record_{wf['id']}", type="primary"):
                run_action(workflow.apply, conn, wf["id"], "outcome_recorded", member_id,
                           payload={"outcome": outcome}, expected_version=wf["version"])

        if wf["state"] == "completed" and wf["outcome"]:
            st.caption(f"Outcome: {wf['outcome']}")

        if wf["state"] in workflow.CANCEL_ALLOWED:
            if st.button("Cancel intro", key=f"mgr_cancel_{wf['id']}", icon=":material/close:"):
                run_action(workflow.apply, conn, wf["id"], "cancelled", member_id, expected_version=wf["version"])


if is_manager:
    manager_view()
    if prompt := st.chat_input("Ask the concierge about shared profiles"):
        try:
            with st.spinner("Thinking..."):
                handle_message(conn, "chat", db.get_chat_login(conn, member_id), prompt)
        except LLMError:
            st.session_state.flash = REPLY_FAILED
        st.rerun()
    st.stop()


# --- Member view -------------------------------------------------------------
def remember_turn(result: TurnResult, channel: str) -> None:
    """Keep what the last turn saved or needs to confirm, to show notices with Undo."""
    st.session_state.notices = {
        "saved": [m["id"] for m in result.saved_memories],
        "updated": [{"id": new["id"], "previous": previous} for new, previous in result.updated_memories],
        "pending": [c.model_dump() for c in result.needs_confirmation],
        "actions": [a["description"] for a in result.action_items],
        "intro": result.intro is not None,
        "source_message_id": result.inbound_message_id,
        "channel": channel,
    }


def show_notices() -> None:
    """'Saved · Undo' for new memories, a yes/no for sensitive ones (H1), and new pending items."""
    notices = st.session_state.get("notices")
    if not notices:
        return

    for m in memory.get_memories_by_ids(conn, member_id, notices["saved"]):
        text_col, undo_col = st.columns([5, 1])
        text_col.success(f"Saved to memory: {m['content']}", icon=":material/bookmark_added:")
        if undo_col.button("Undo", key=f"undo_{m['id']}", use_container_width=True):
            memory.delete_memory(conn, member_id, m["id"])
            notices["saved"].remove(m["id"])
            st.session_state.flash_ok = f"Undone. I won't remember: {m['content']}."
            st.rerun()

    for change in list(notices.get("updated", [])):
        m = memory.get_memory(conn, member_id, change["id"])
        if m is None:
            notices["updated"].remove(change)
            continue
        previous = change["previous"]
        was = "; ".join(p["content"] for p in previous)
        text_col, undo_col = st.columns([5, 1])
        text_col.success(f"Memory updated: {m['content']}. Before: {was}.", icon=":material/edit_note:")
        if any(p["visibility"] == "shareable" for p in previous):
            text_col.caption("The old version was Shareable. The new one stays Private until you make "
                             "it Shareable in What I know about you.")
        if undo_col.button("Undo", key=f"undo_upd_{m['id']}", use_container_width=True):
            memory.undo_replace(conn, member_id, previous)
            notices["updated"].remove(change)
            st.rerun()

    for i, data in enumerate(list(notices["pending"])):
        candidate = memory.MemoryCandidate(**data)
        with st.container(border=True):
            st.write(f":material/shield_person: Should I remember this? **{candidate.content}**")
            st.caption("It looks like health or personal information, so I save it only if you say yes. "
                       "It would stay private and never be used in intros.")
            yes, no = st.columns(2)
            if yes.button("Remember privately", key=f"pending_yes_{i}", type="primary",
                          use_container_width=True):
                saved_row = memory.confirm_sensitive(
                    conn, member_id, candidate, notices["source_message_id"], notices["channel"])
                notices["pending"].pop(i)
                if saved_row:
                    notices["saved"].append(saved_row["id"])
                st.rerun()
            if no.button("Don't save", key=f"pending_no_{i}", use_container_width=True):
                notices["pending"].pop(i)
                st.rerun()

    for description in notices.get("actions", []):
        st.info(f"Added to Pending: {description}", icon=":material/task_alt:")
    if notices.get("intro"):
        st.info("Intro requested. Your relationship manager will look for the right person; "
                "follow it in Pending.", icon=":material/handshake:")


def used_memories_line(msg) -> str | None:
    ids = json.loads(msg["used_memory_ids"]) if msg["used_memory_ids"] else []
    rows = memory.get_memories_by_ids(conn, member_id, ids)
    if not rows:
        return None
    return "This reply used: " + " · ".join(m["content"] for m in rows)


def consent_checklist(wf, owner_id: int, prefix: str) -> list[int]:
    """Checkboxes for exactly what the other member will see. Returns chosen memory ids."""
    st.checkbox("Your name and title", value=True, disabled=True, key=f"{prefix}_name_{wf['id']}")
    if owner_id == wf["requester_id"]:
        st.checkbox(f"Why you want to meet: “{wf['reason']}”", value=True, disabled=True,
                    key=f"{prefix}_reason_{wf['id']}")
    chosen = []
    shareable = workflow.shareable_memories(conn, owner_id)
    for m in shareable:
        if st.checkbox(m["content"], value=True, key=f"{prefix}_mem_{wf['id']}_{m['id']}"):
            chosen.append(m["id"])
    if not shareable:
        st.caption("Only Shareable memories can be offered. You can make a memory Shareable in "
                   "What I know about you.")
    return chosen


def show_shared(wf, side: str) -> None:
    shared = workflow.shared_items(conn, wf, side)
    lines = [shared["name_title"]] if shared["name_title"] else []
    if shared["reason"]:
        lines.append(f"Looking for: {shared['reason']}")
    lines += shared["memories"]
    st.markdown("\n".join(f"- {line}" for line in lines) or "- Nothing shared.")


def cancel_button(wf, role: str) -> None:
    if role in workflow.CANCEL_ALLOWED.get(wf["state"], set()):
        if st.button("Cancel intro", key=f"cancel_{wf['id']}", icon=":material/close:"):
            run_action(workflow.apply, conn, wf["id"], "cancelled", member_id, expected_version=wf["version"])


def member_intro_card(wf) -> None:
    i_am_a = wf["requester_id"] == member_id
    role = "A" if i_am_a else "B"
    other_id = wf["target_id"] if i_am_a else wf["requester_id"]
    other = db.get_member(conn, other_id) if other_id else None
    state = wf["state"]
    v = wf["version"]

    with st.container(border=True):
        if i_am_a:
            st.markdown(f"**Intro to {other['display_name']}**" if other and state != "requested"
                        else "**Your intro request**")
        else:
            st.markdown(f"**Intro from {other['display_name']}**")

        if state in ("declined", "expired", "cancelled"):
            st.caption(workflow.WONT_GO_AHEAD)
            return
        if state == "no_match":
            st.caption(workflow.NO_MATCH)
            return
        if state == "completed":
            st.caption("Completed.")
            return

        deadline = f" · until {fmt_date(wf['deadline_at'])}" if wf["deadline_at"] else ""
        answer_by = (f" Answer by {fmt_date(wf['deadline_at'])}, or the request closes."
                     if wf["deadline_at"] else "")

        if state == "requested":
            st.caption("Looking for the right person. Your relationship manager confirms the match "
                       f"before anyone is asked{deadline}.")
        elif state == "awaiting_a" and i_am_a:
            why = {s["member_id"]: s["why"] for s in json.loads(wf["suggestions"] or "[]")}.get(other_id, "")
            st.write(f"Proposed: **{workflow.name_title(other)}**")
            if why:
                st.caption(f"Why: {why}")
            first = other["display_name"].split()[0]
            st.write(f"Choose what {first} will see about you if you go ahead. Nothing else is shared.")
            chosen = consent_checklist(wf, member_id, "a")
            st.caption(f"{first} isn't told anything until you select Ask {first}.{answer_by}")
            c1, c2, c3 = st.columns(3)
            if c1.button(f"Ask {first}", key=f"ask_{wf['id']}", type="primary", use_container_width=True):
                run_action(workflow.consent, conn, wf["id"], member_id, chosen)
            if c2.button("Suggest someone else", key=f"another_{wf['id']}", use_container_width=True):
                run_action(workflow.apply, conn, wf["id"], "a_wants_another", member_id, expected_version=v)
            if c3.button("Close request", key=f"adecline_{wf['id']}", use_container_width=True):
                run_action(workflow.apply, conn, wf["id"], "a_declined", member_id, expected_version=v)
            return
        elif state == "awaiting_b" and i_am_a:
            st.caption(f"Waiting for {other['display_name'].split()[0]}'s answer{deadline}.")
        elif state == "awaiting_b" and not i_am_a:
            first = other["display_name"].split()[0]
            st.write(f"**{workflow.name_title(other)}** would like to meet you. They chose to share:")
            show_shared(wf, "A")
            st.write(f"If you accept, {first} will see what you check below. Nothing else about you is shared.")
            chosen = consent_checklist(wf, member_id, "b")
            st.caption(f"If you decline, {first} only sees that the intro won't go ahead, not why.{answer_by}")
            c1, c2 = st.columns(2)
            if c1.button("Accept and share", key=f"accept_{wf['id']}", type="primary", use_container_width=True):
                run_action(workflow.consent, conn, wf["id"], member_id, chosen)
            if c2.button("Decline", key=f"bdecline_{wf['id']}", use_container_width=True):
                run_action(workflow.apply, conn, wf["id"], "b_declined", member_id, expected_version=v)
            return
        elif state in ("introduced", "scheduled", "follow_up"):
            st.write("What they shared:")
            show_shared(wf, "B" if i_am_a else "A")
            if state == "introduced":
                today = clock.now(conn).date()
                day = st.date_input("Meeting date", value=today + timedelta(days=3), min_value=today,
                                    key=f"meet_{wf['id']}")
                if st.button("Mark meeting booked", key=f"book_{wf['id']}", icon=":material/event:"):
                    run_action(workflow.apply, conn, wf["id"], "meeting_booked", member_id,
                               payload={"meeting_at": f"{day:%Y-%m-%d} 12:00:00"}, expected_version=v)
            elif state == "scheduled":
                st.caption(f"Meeting on {fmt_date(wf['meeting_at'])}.")
            else:
                st.caption("How did it go? Tell your relationship manager.")

        cancel_button(wf, role)


def pending_tab() -> None:
    st.markdown("**Intros**")
    intros = workflow.intros_for_member(conn, member_id)
    if not intros:
        st.caption("No intros yet. Ask the concierge to introduce you to someone.")
    for wf in intros:
        member_intro_card(wf)

    st.markdown("**Action items**")
    items = actions.list_open(conn, member_id)
    if not items:
        st.caption("Nothing pending. Commitments from your conversations show up here.")
    for item in items:
        with st.container(border=True):
            st.write(item["description"])
            owner = "you" if item["owner_id"] == member_id else item["owner_name"]
            st.caption(f"Owner: {owner} · due {fmt_date(item['due_date'])}")
            c1, c2 = st.columns([3, 1])
            new_due = c1.date_input("Due", value=clock.parse(item["due_date"]).date(),
                                    key=f"due_{item['id']}", label_visibility="collapsed")
            if f"{new_due:%Y-%m-%d}" != item["due_date"][:10]:
                actions.update_due_date(conn, item["id"], member_id, f"{new_due:%Y-%m-%d} 12:00:00")
                st.rerun()
            if c2.button("Done", key=f"done_{item['id']}", icon=":material/check:"):
                actions.set_status(conn, item["id"], member_id, "done")
                st.rerun()


def email_tab() -> None:
    st.caption("Write as if you emailed the concierge. Pick one of your addresses, or type another "
               "to see how an unknown sender is handled.")
    addresses = db.list_email_identities(conn, member_id)
    options = [a["address"] for a in addresses] + [OTHER_ADDRESS]
    unverified = {a["address"] for a in addresses if not a["verified"]}

    with st.form("email_form", clear_on_submit=True):
        choice = st.selectbox("From", options,
                              format_func=lambda a: f"{a} (not verified)" if a in unverified else a)
        other = st.text_input("Other address", placeholder="name@company.example",
                              help=f"Used only when '{OTHER_ADDRESS}' is selected.")
        body = st.text_area("Message", height=120)
        sent = st.form_submit_button("Send email")

    if sent:
        sender = other.strip() if choice == OTHER_ADDRESS else choice
        owner = resolve(conn, "email", sender) if sender else None
        if not sender or not body.strip():
            st.warning("Fill in the sender and the message.")
        elif owner and owner.is_known and owner.member_id != member_id:
            # The simulator shows the reply on screen. A real reply would go to that
            # member's inbox, so sending "as" someone else here would leak their data.
            st.error("That address belongs to another member, so it can't be used here. Send from one "
                     "of your addresses, or from an address nobody registered.", icon=":material/block:")
        else:
            try:
                with st.spinner("Reading the email..."):
                    result = handle_message(conn, "email", sender, body.strip())
                st.session_state.last_email = (sender, result.identity.is_known, result.reply)
                if result.identity.is_known:
                    remember_turn(result, "email")
            except ReplyFailed as exc:
                st.session_state.last_email = None
                remember_turn(exc.partial, "email")
                st.error("Your email is saved in your timeline, but the concierge couldn't reply. "
                         "Select Try again under it.", icon=":material/sync_problem:")

    last = st.session_state.get("last_email")
    if last:
        address, known, reply = last
        if known:
            st.success(f"This is the reply {address} would receive. It's also in your timeline.",
                       icon=":material/send:")
        else:
            st.warning(f"{address} isn't registered to any member, so the reply below uses no account "
                       "information. If it's yours, ask your relationship manager to add it.",
                       icon=":material/gpp_maybe:")
        st.markdown(f"> {reply}")


timeline_col, side_col = st.columns([3, 2], gap="large")

with side_col:
    open_count = sum(1 for w in workflow.intros_for_member(conn, member_id) if w["state"] in workflow.OPEN)
    tab_pending, tab_updates, tab_email = st.tabs(
        [f":material/pending_actions: Pending ({open_count + len(actions.list_open(conn, member_id))})",
         ":material/notifications: Updates", ":material/mail: Email"])
    with tab_pending:
        pending_tab()
    with tab_updates:
        show_updates(member_id)
    with tab_email:
        email_tab()

with timeline_col:
    st.subheader("Your timeline")
    st.caption("Chat and email in one history.")

    timeline = db.get_timeline(conn, member_id)
    if not timeline:
        with st.container(border=True):
            st.write(":material/forum: No messages yet. Write in the chat below, or send an email from "
                     "the Email tab. Both end up here.")
            st.caption("Try: \"I want to automate quality control at our plant in 2027.\"")
    # Long histories scroll inside their own box, so the notices below stay in view.
    history = st.container(height=560, border=False) if len(timeline) > 6 else st.container()
    with history:
        for msg in timeline:
            role = "user" if msg["direction"] == "inbound" else "assistant"
            avatar = ":material/person:" if role == "user" else ":material/room_service:"
            with st.chat_message(role, avatar=avatar):
                st.caption(f"{CHANNEL_LABEL[msg['channel']]} · {fmt_date(msg['created_at'])}, "
                           f"{msg['created_at'][11:16]}")
                st.markdown(msg["body"])
                if msg["context_excluded"]:
                    st.caption(":material/visibility_off: You deleted a memory from this exchange, "
                               "so I no longer read it.")
                if role == "assistant" and (line := used_memories_line(msg)):
                    st.caption(f":material/psychology: {line}")

    retry_box(member_id)
    show_notices()

if prompt := st.chat_input("Message the concierge"):
    chat_login = db.get_chat_login(conn, member_id)
    try:
        with st.spinner("Thinking..."):
            result = handle_message(conn, "chat", chat_login, prompt)
        remember_turn(result, "chat")
    except ReplyFailed as exc:
        remember_turn(exc.partial, "chat")
        st.session_state.flash = REPLY_FAILED
    except LLMError:
        st.session_state.flash = REPLY_FAILED
    st.rerun()
