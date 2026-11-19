"""Streamlit entry point for the Concierge with Memory prototype."""

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
    "internal": ":material/badge: Internal (team note)",
}
CATEGORY_LABEL = {
    "role": "Role", "company": "Company", "goal": "Goals", "preference": "Preferences",
    "interest": "Interests", "expertise": "Expertise", "personal": "Personal", "health": "Health",
}
STATE_LABEL = {
    "requested": "Looking for a match", "awaiting_a": "Waiting for the requester",
    "awaiting_b": "Waiting for the other member", "introduced": "Introduced",
    "scheduled": "Meeting booked", "follow_up": "Follow-up", "completed": "Completed",
    "no_match": "No match", "declined": "Won't go ahead", "expired": "Won't go ahead",
    "cancelled": "Won't go ahead",
}
OTHER_ADDRESS = "Another address…"
REPLY_FAILED = ("The concierge couldn't reply just now. Your message is saved, so you don't need to "
                "type it again. Use Try again under it.")
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 30

st.set_page_config(page_title="Concierge", page_icon=":material/room_service:", layout="wide")


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
    st.title("Concierge")
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
is_manager = current["role"] == "manager"


def run_action(fn, *args, **kwargs) -> None:
    """Run a workflow or action change; show a clear message instead of a crash."""
    try:
        fn(*args, **kwargs)
    except workflow.WorkflowError as exc:
        st.session_state.flash = str(exc)
    except LLMError:
        st.session_state.flash = ("The assistant couldn't finish that step, so nothing changed. "
                                  "Try again in a minute.")
    st.rerun()


# --- Sidebar: account, demo clock and memory panel (R1) ----------------------
def memory_panel() -> None:
    st.header("What I know about you")

    paused = memory.is_paused(conn, member_id)
    new_paused = st.toggle("Pause memory", value=paused,
                           help="While paused, nothing new is saved. What is already saved is still used.")
    if new_paused != paused:
        memory.set_paused(conn, member_id, new_paused)
        st.rerun()
    if new_paused:
        st.caption(":material/pause_circle: Memory is paused. I won't save anything new.")

    memories = memory.list_memories(conn, member_id)
    if not memories:
        st.caption("Nothing saved yet. As we talk, I'll save durable facts you tell me, and you can undo any of them.")
    stale = {m["id"] for m in memories if memory.is_stale(conn, m)}
    if stale:
        st.info(f"{len(stale)} {'memory has' if len(stale) == 1 else 'memories have'} not been confirmed "
                "in 6 months. Check that they're still true.", icon=":material/history:")

    for category in CATEGORY_LABEL:
        group = [m for m in memories if m["category"] == category]
        if not group:
            continue
        st.markdown(f"**{CATEGORY_LABEL[category]}**")
        for m in group:
            with st.expander(m["content"], expanded=m["id"] in stale):
                st.caption(f"{VISIBILITY_LABEL[m['visibility']]} · saved {m['created_at'][:10]}")
                if m["id"] in stale:
                    st.caption(f":material/history: Last confirmed {fmt_date(m['last_confirmed_at'])}. "
                               "Is it still true? If not, edit or delete it.")
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
                    if workflow.seen_by_others(conn, member_id, m["id"]):
                        st.session_state.confirm_delete = m["id"]
                    else:
                        memory.delete_memory(conn, member_id, m["id"])
                    st.rerun()
                if st.session_state.get("confirm_delete") == m["id"]:
                    confirm_delete_shared(m)

    st.divider()
    with st.expander("Delete everything"):
        st.caption("Removes every memory, your pending items and your whole conversation history. "
                   "Open intros are cancelled. This can't be undone.")
        confirm = st.checkbox("I understand", key="confirm_delete_all")
        if st.button("Delete everything", disabled=not confirm, type="primary"):
            memory.delete_everything(conn, member_id)
            st.session_state.pop("notices", None)
            st.session_state.pop("last_email", None)
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


with st.sidebar:
    st.write(f"Signed in as **{current['display_name']}**")
    st.caption(f"{current['title'] or current['role']}")
    if st.button("Sign out", icon=":material/logout:"):
        st.session_state.clear()
        st.rerun()

    st.divider()
    st.subheader("Demo clock")
    st.caption(f"Today is **{clock.now(conn):%a %d %b %Y}** in the demo. Move time forward to see "
               "reminders, expirations and follow-ups.")
    if st.button("Advance 1 day", icon=":material/fast_forward:", key="advance_day"):
        clock.advance(conn, 1)
        report = workflow.tick(conn)
        due = actions.remind_due(conn)
        st.session_state.flash_ok = (f"Moved to {clock.now(conn):%a %d %b}. "
                                     f"{report.transitions} intro updates, {report.reminders + due} reminders.")
        st.rerun()

    if not is_manager:
        st.divider()
        memory_panel()

st.title("Concierge")
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
        st.write(":material/sync_problem: I haven't replied to your last message yet. It's saved, "
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
                st.session_state.flash = ("Still couldn't reply. Your message is still saved; "
                                          "try again in a minute.")
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
    c3.metric("Unknown senders in quarantine", db.count_quarantined(conn))
    st.caption("You see intro status, owners and deadlines. You never see the content of member conversations.")

    board_col, side_col = st.columns([3, 2], gap="large")
    with board_col:
        st.subheader("Intro board")
        if not intros:
            st.caption("No intro requests yet. Sign in as a member and ask the concierge for an intro.")
        for wf in intros:
            manager_intro_card(wf)
    with side_col:
        tab_ask, tab_profiles, tab_items, tab_updates = st.tabs(
            ["Ask the concierge", "Member profiles", "Your action items", "Updates"])
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
        text_col.success(f"Saved: {m['content']}", icon=":material/bookmark_added:")
        if undo_col.button("Undo", key=f"undo_{m['id']}"):
            memory.delete_memory(conn, member_id, m["id"])
            notices["saved"].remove(m["id"])
            st.rerun()

    for change in list(notices.get("updated", [])):
        m = memory.get_memory(conn, member_id, change["id"])
        if m is None:
            notices["updated"].remove(change)
            continue
        previous = change["previous"]
        was = "; ".join(p["content"] for p in previous)
        text_col, undo_col = st.columns([5, 1])
        text_col.success(f"Updated: {m['content']} (was: {was})", icon=":material/edit_note:")
        if any(p["visibility"] == "shareable" for p in previous):
            text_col.caption("The old version was shared for intros. The new one is private until you "
                             "share it again in the memory panel.")
        if undo_col.button("Undo", key=f"undo_upd_{m['id']}"):
            memory.undo_replace(conn, member_id, previous)
            notices["updated"].remove(change)
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

    for description in notices.get("actions", []):
        st.info(f"Added to your pending items: {description}", icon=":material/task_alt:")
    if notices.get("intro"):
        st.info("Intro request created. Follow it in the Pending tab.", icon=":material/handshake:")


def used_memories_line(msg) -> str | None:
    ids = json.loads(msg["used_memory_ids"]) if msg["used_memory_ids"] else []
    rows = memory.get_memories_by_ids(conn, member_id, ids)
    if not rows:
        return None
    return "Used: " + " · ".join(m["content"] for m in rows)


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
        st.caption("Only memories marked Shareable can be offered. You can change that in your memory panel.")
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
            st.caption("I couldn't find a good match for this request. You can ask again anytime.")
            return
        if state == "completed":
            st.caption("Completed.")
            return

        deadline = f" · until {fmt_date(wf['deadline_at'])}" if wf["deadline_at"] else ""

        if state == "requested":
            st.caption(f"Looking for the right person. Your relationship manager confirms the match{deadline}.")
        elif state == "awaiting_a" and i_am_a:
            why = {s["member_id"]: s["why"] for s in json.loads(wf["suggestions"] or "[]")}.get(other_id, "")
            st.write(f"Proposed: **{workflow.name_title(other)}**")
            if why:
                st.caption(f"Why: {why}")
            st.write(f"Before I ask {other['display_name'].split()[0]}, choose what they can see about you:")
            chosen = consent_checklist(wf, member_id, "a")
            st.caption(f"Waiting for you{deadline}")
            c1, c2, c3 = st.columns(3)
            if c1.button(f"Ask {other['display_name'].split()[0]}", key=f"ask_{wf['id']}", type="primary"):
                run_action(workflow.consent, conn, wf["id"], member_id, chosen)
            if c2.button("Someone else", key=f"another_{wf['id']}"):
                run_action(workflow.apply, conn, wf["id"], "a_wants_another", member_id, expected_version=v)
            if c3.button("Not now", key=f"adecline_{wf['id']}"):
                run_action(workflow.apply, conn, wf["id"], "a_declined", member_id, expected_version=v)
            return
        elif state == "awaiting_b" and i_am_a:
            st.caption(f"Waiting for {other['display_name'].split()[0]}'s answer{deadline}.")
        elif state == "awaiting_b" and not i_am_a:
            st.write(f"**{workflow.name_title(other)}** would like to meet you. They shared:")
            show_shared(wf, "A")
            st.write("If you accept, choose what they can see about you:")
            chosen = consent_checklist(wf, member_id, "b")
            st.caption(f"Waiting for you{deadline}")
            c1, c2 = st.columns(2)
            if c1.button("Accept", key=f"accept_{wf['id']}", type="primary"):
                run_action(workflow.consent, conn, wf["id"], member_id, chosen)
            if c2.button("Decline", key=f"bdecline_{wf['id']}"):
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
    st.caption("Pretend you emailed the concierge instead of using the chat. Pick one of your addresses, "
               "or type another to see how an unknown sender is handled.")
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
            st.error("That address belongs to another member. You can simulate emails from your own "
                     "addresses or from an address nobody registered.", icon=":material/block:")
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
                st.error("The email is saved in your timeline, but the concierge couldn't reply just now. "
                         "Use Try again under it.", icon=":material/sync_problem:")

    last = st.session_state.get("last_email")
    if last:
        address, known, reply = last
        if known:
            st.success(f"Reply sent to {address}. It is also in your timeline.", icon=":material/send:")
        else:
            st.warning(f"{address} is not a verified address, so no account data was used.",
                       icon=":material/gpp_maybe:")
        st.markdown(f"> {reply}")


timeline_col, side_col = st.columns([3, 2], gap="large")

with side_col:
    open_count = sum(1 for w in workflow.intros_for_member(conn, member_id) if w["state"] in workflow.OPEN)
    tab_pending, tab_updates, tab_email = st.tabs(
        [f"Pending ({open_count + len(actions.list_open(conn, member_id))})", "Updates", "Email inbox"])
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
        st.write("No messages yet. Say hello in the chat, or send an email from one of your addresses.")
    for msg in timeline:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        with st.chat_message(role):
            st.caption(f"{CHANNEL_LABEL[msg['channel']]} · {msg['created_at'][:16]}")
            st.markdown(msg["body"])
            if msg["context_excluded"]:
                st.caption(":material/visibility_off: Forgotten: you deleted a memory from this exchange, "
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
