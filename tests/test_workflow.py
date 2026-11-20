"""Every transition of the intro state machine (docs/model.md), plus its guards."""

import json

import pytest

from app import clock, db, memory, workflow
from tests.conftest import member_id
from tests.fakes import FakeLLM


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(workflow, "call_llm", f)
    return f


@pytest.fixture
def ids(conn):
    return {name.split()[0].lower(): member_id(conn, name)
            for name in ("Roberto Silva", "Ana Souza", "Juliana Costa", "Carlos Lima", "Marina Alves")}


def notes(conn, who):
    return [n["body"] for n in db.list_notifications(conn, who, limit=50)]


def advance(conn, days):
    clock.advance(conn, days)
    return workflow.tick(conn)


def requested(conn, fake, ids, suggest=("ana",)):
    fake.suggested_ids = [ids[s] for s in suggest]
    return workflow.request_intro(conn, ids["roberto"], "Someone who automated quality control")


def awaiting_a(conn, fake, ids):
    wf = requested(conn, fake, ids)
    return workflow.propose(conn, wf["id"], ids["juliana"], ids["ana"])


def awaiting_b(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    return workflow.consent(conn, wf["id"], ids["roberto"], [])


def introduced(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    return workflow.consent(conn, wf["id"], ids["ana"], [])


def scheduled(conn, fake, ids):
    wf = introduced(conn, fake, ids)
    return workflow.apply(conn, wf["id"], "meeting_booked", ids["roberto"],
                          payload={"meeting_at": clock.in_days(conn, 2)})


# --- Happy path --------------------------------------------------------------
def test_request_creates_requested_with_manager_as_owner(conn, fake, ids):
    wf = requested(conn, fake, ids)
    assert wf["state"] == "requested"
    assert wf["owner_id"] == ids["juliana"]
    assert json.loads(wf["suggestions"])[0]["member_id"] == ids["ana"]
    assert "New intro request from Roberto Silva." in notes(conn, ids["juliana"])


def test_requested_to_awaiting_a(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    assert (wf["state"], wf["target_id"], wf["owner_id"]) == ("awaiting_a", ids["ana"], ids["roberto"])
    assert any(n.startswith("Possible match: Ana Souza") for n in notes(conn, ids["roberto"]))
    assert notes(conn, ids["ana"]) == []  # B knows nothing yet


def test_awaiting_a_to_awaiting_b(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    assert (wf["state"], wf["owner_id"]) == ("awaiting_b", ids["ana"])
    assert "Roberto Silva would like to be introduced to you. Review it in Pending." in notes(conn, ids["ana"])


def test_awaiting_b_to_introduced(conn, fake, ids):
    wf = introduced(conn, fake, ids)
    assert wf["state"] == "introduced" and wf["owner_id"] is None
    kinds = [e["event"] for e in workflow.events(conn, wf["id"])]
    assert kinds == ["requested", "candidate_proposed", "a_consented", "b_consented"]


def test_introduced_to_scheduled(conn, fake, ids):
    wf = scheduled(conn, fake, ids)
    assert wf["state"] == "scheduled"
    assert wf["deadline_at"] == wf["meeting_at"]


def test_scheduled_to_follow_up_after_meeting(conn, fake, ids):
    wf = scheduled(conn, fake, ids)
    advance(conn, 3)
    wf = workflow.get(conn, wf["id"])
    assert (wf["state"], wf["owner_id"]) == ("follow_up", ids["juliana"])
    assert "Time to follow up: Roberto Silva and Ana Souza." in notes(conn, ids["juliana"])


def test_follow_up_to_completed_with_outcome(conn, fake, ids):
    wf = scheduled(conn, fake, ids)
    advance(conn, 3)
    wf = workflow.apply(conn, wf["id"], "outcome_recorded", ids["juliana"], payload={"outcome": "Great call"})
    assert (wf["state"], wf["outcome"]) == ("completed", "Great call")


def test_follow_up_closes_without_answer(conn, fake, ids):
    wf = scheduled(conn, fake, ids)
    advance(conn, 3)
    advance(conn, 4)
    wf = workflow.get(conn, wf["id"])
    assert (wf["state"], wf["outcome"]) == ("completed", "No answer")


# --- Other paths -------------------------------------------------------------
def test_no_candidates_closes_as_no_match(conn, fake, ids):
    wf = requested(conn, fake, ids, suggest=())
    assert wf["state"] == "no_match"
    assert any("couldn't find a good match" in n for n in notes(conn, ids["roberto"]))


def test_suggestion_failure_keeps_request_open(conn, fake, ids):
    fake.fail_suggestions = True
    wf = workflow.request_intro(conn, ids["roberto"], "Someone in logistics")
    assert wf["state"] == "requested" and wf["suggestions"] is None
    fake.fail_suggestions, fake.suggested_ids = False, [ids["carlos"]]
    workflow.refresh_suggestions(conn, wf["id"])
    assert json.loads(workflow.get(conn, wf["id"])["suggestions"])[0]["member_id"] == ids["carlos"]


def test_a_wants_another_goes_back_to_requested(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    wf = workflow.apply(conn, wf["id"], "a_wants_another", ids["roberto"])
    assert (wf["state"], wf["target_id"], wf["owner_id"]) == ("requested", None, ids["juliana"])


def test_a_declines(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    wf = workflow.apply(conn, wf["id"], "a_declined", ids["roberto"])
    assert wf["state"] == "declined"
    assert notes(conn, ids["ana"]) == []  # Ana was never asked, so she is never told


def test_b_declines_with_neutral_notice(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    wf = workflow.apply(conn, wf["id"], "b_declined", ids["ana"])
    assert wf["state"] == "declined"
    assert notes(conn, ids["roberto"])[0] == workflow.WONT_GO_AHEAD


@pytest.mark.parametrize("make, who", [
    (requested, "nobody"), (awaiting_a, "roberto"), (awaiting_b, "ana"),
])
def test_unanswered_steps_expire_after_3_days(conn, fake, ids, make, who):
    wf = make(conn, fake, ids)
    advance(conn, 2)
    assert workflow.get(conn, wf["id"])["state"] == wf["state"]  # not yet
    advance(conn, 1)
    assert workflow.get(conn, wf["id"])["state"] == "expired"
    assert notes(conn, ids["roberto"])[0] == workflow.WONT_GO_AHEAD


def test_reminder_one_day_before_deadline_sent_once(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    report = advance(conn, 2)
    assert report.reminders == 1
    assert notes(conn, ids["ana"])[0].startswith("Reminder: an intro is waiting for you")
    assert workflow.tick(conn).reminders == 0  # running again does not repeat it


def test_introduced_without_meeting_moves_to_follow_up_after_7_days(conn, fake, ids):
    wf = introduced(conn, fake, ids)
    advance(conn, 6)
    assert workflow.get(conn, wf["id"])["state"] == "introduced"
    advance(conn, 1)
    assert workflow.get(conn, wf["id"])["state"] == "follow_up"


@pytest.mark.parametrize("make, actor", [
    (requested, "roberto"), (requested, "juliana"),
    (awaiting_a, "roberto"), (awaiting_b, "ana"), (awaiting_b, "roberto"),
    (introduced, "ana"), (scheduled, "roberto"), (scheduled, "juliana"),
])
def test_cancellation_allowed(conn, fake, ids, make, actor):
    wf = make(conn, fake, ids)
    assert workflow.apply(conn, wf["id"], "cancelled", ids[actor])["state"] == "cancelled"


# --- Guards ------------------------------------------------------------------
def test_b_cannot_cancel_before_being_asked(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    with pytest.raises(workflow.NotAllowed):
        workflow.apply(conn, wf["id"], "cancelled", ids["ana"])


def test_b_cannot_consent_before_a(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    with pytest.raises(workflow.WorkflowError):
        workflow.apply(conn, wf["id"], "b_consented", ids["ana"])


def test_only_a_can_consent_for_a(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    for outsider in ("ana", "carlos", "juliana"):
        with pytest.raises(workflow.NotAllowed):
            workflow.apply(conn, wf["id"], "a_consented", ids[outsider])


def test_members_cannot_propose_candidates(conn, fake, ids):
    wf = requested(conn, fake, ids)
    with pytest.raises(workflow.NotAllowed):
        workflow.propose(conn, wf["id"], ids["roberto"], ids["ana"])


def test_manager_must_pick_a_suggested_candidate(conn, fake, ids):
    wf = requested(conn, fake, ids)
    with pytest.raises(workflow.NotAllowed):
        workflow.propose(conn, wf["id"], ids["juliana"], ids["carlos"])


def test_invalid_transition(conn, fake, ids):
    wf = requested(conn, fake, ids)
    with pytest.raises(workflow.InvalidTransition) as error:
        workflow.apply(conn, wf["id"], "meeting_booked", ids["roberto"], payload={"meeting_at": "2030-01-01"})
    # The member sees the same state name as on screen, never the internal one (d1 copy).
    assert "Looking for a match" in str(error.value) and "requested" not in str(error.value)


def test_terminal_states_accept_nothing(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    workflow.apply(conn, wf["id"], "a_declined", ids["roberto"])
    with pytest.raises(workflow.InvalidTransition):
        workflow.apply(conn, wf["id"], "cancelled", ids["roberto"])


def test_stale_version_is_rejected(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    workflow.apply(conn, wf["id"], "a_wants_another", ids["roberto"])
    with pytest.raises(workflow.Conflict):
        workflow.apply(conn, wf["id"], "cancelled", ids["roberto"], expected_version=wf["version"])


def test_retry_with_same_key_applies_once(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    workflow.apply(conn, wf["id"], "a_consented", ids["roberto"], idempotency_key="click-1")
    again = workflow.apply(conn, wf["id"], "a_consented", ids["roberto"], idempotency_key="click-1")
    assert again["state"] == "awaiting_b"
    assert [e["event"] for e in workflow.events(conn, wf["id"])].count("a_consented") == 1


def test_open_intro_blocks_a_second_one_for_the_same_pair(conn, fake, ids):
    awaiting_a(conn, fake, ids)
    fake.suggested_ids = [ids["ana"], ids["carlos"]]
    second = workflow.request_intro(conn, ids["roberto"], "Another one")
    offered = [s["member_id"] for s in json.loads(second["suggestions"])]
    assert ids["ana"] not in offered  # Ana is filtered out of the pool before the model sees it
    assert "Ana Souza" not in fake.calls_for("Suggestions")[-1]["messages"][0]["content"]


def test_decline_blocks_the_pair_for_90_days(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    workflow.apply(conn, wf["id"], "b_declined", ids["ana"])
    assert workflow.pair_blocked(conn, ids["roberto"], ids["ana"])
    clock.advance(conn, 91)
    assert not workflow.pair_blocked(conn, ids["roberto"], ids["ana"])


# --- What each side sees -----------------------------------------------------
def test_b_sees_the_intro_only_after_a_consents(conn, fake, ids):
    wf = awaiting_a(conn, fake, ids)
    assert workflow.intros_for_member(conn, ids["ana"]) == []
    workflow.consent(conn, wf["id"], ids["roberto"], [])
    assert [w["id"] for w in workflow.intros_for_member(conn, ids["ana"])] == [wf["id"]]


def test_only_chosen_shareable_memories_are_shared(conn, fake, ids):
    roberto = ids["roberto"]
    for content, vis in [("Wants to automate QC", "shareable"), ("Plans to sell the company", "private")]:
        conn.execute("INSERT INTO memories (member_id, content, category, visibility, confidence) "
                     "VALUES (?, ?, 'goal', ?, 1)", (roberto, content, vis))
    shareable_id, private_id = [r["id"] for r in conn.execute(
        "SELECT id FROM memories WHERE member_id = ? ORDER BY id", (roberto,))]

    wf = awaiting_a(conn, fake, ids)
    wf = workflow.consent(conn, wf["id"], roberto, [shareable_id, private_id])  # private one is ignored
    shared = workflow.shared_items(conn, wf, "A")
    assert shared["memories"] == ["Wants to automate QC"]
    assert shared["reason"] == "Someone who automated quality control"

    memory.delete_memory(conn, roberto, shareable_id)
    assert workflow.shared_items(conn, workflow.get(conn, wf["id"]), "A")["memories"] == []


def test_candidate_pool_has_only_shareable_memories(conn, fake, ids):
    pool = {c["member_id"]: c for c in workflow.candidate_pool(conn, ids["roberto"])}
    assert "Is considering leaving his job next year" not in pool[ids["carlos"]]["shareable"]
    assert "Attended the March event in São Paulo" not in pool[ids["ana"]]["shareable"]
    assert ids["juliana"] not in pool  # managers are not candidates
    assert ids["roberto"] not in pool


def test_delete_everything_cancels_open_intros_and_clears_payloads(conn, fake, ids):
    wf = awaiting_b(conn, fake, ids)
    memory.delete_everything(conn, ids["roberto"])
    wf = workflow.get(conn, wf["id"])
    assert wf["state"] == "cancelled"
    assert wf["reason"] == "(deleted by member)"
    assert all(e["payload"] is None for e in workflow.events(conn, wf["id"]))
    assert notes(conn, ids["ana"])[0] == workflow.WONT_GO_AHEAD
