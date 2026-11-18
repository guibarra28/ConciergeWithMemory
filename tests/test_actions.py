import pytest

from app import actions, clock, db
from tests.conftest import member_id
from tests.fakes import action_item

MESSAGE = "I'll send you the deck on Friday. Can you book a call with Ana next week?"


@pytest.fixture
def roberto(conn):
    return member_id(conn, "Roberto Silva")


def save(conn, member, items, text=MESSAGE):
    return actions.save_candidates(conn, member, items, text, None)


def test_member_commitment_is_owned_by_member_with_due_date(conn, roberto):
    item = save(conn, roberto, [action_item(due_in_days=3)])[0]
    assert item["owner_id"] == roberto
    assert item["due_date"][:10] == clock.in_days(conn, 3)[:10]


def test_team_request_is_owned_by_the_manager(conn, roberto):
    item = save(conn, roberto, [action_item("Book a call with Ana", "team", 7, "book a call with Ana")])[0]
    assert item["owner_id"] == member_id(conn, "Juliana Costa")


def test_missing_due_date_gets_the_default(conn, roberto):
    item = save(conn, roberto, [action_item(due_in_days=None)])[0]
    assert item["due_date"][:10] == clock.in_days(conn, actions.DEFAULT_DUE_DAYS)[:10]


def test_excerpt_must_be_in_the_message(conn, roberto):
    assert save(conn, roberto, [action_item(excerpt="sign the contract")]) == []


def test_duplicates_are_skipped(conn, roberto):
    save(conn, roberto, [action_item()])
    assert save(conn, roberto, [action_item()]) == []


def test_done_by_member_but_not_by_someone_else(conn, roberto):
    item = save(conn, roberto, [action_item()])[0]
    assert not actions.set_status(conn, item["id"], member_id(conn, "Ana Souza"), "done")
    assert actions.set_status(conn, item["id"], roberto, "done")
    assert actions.list_open(conn, roberto) == []


def test_due_reminder_is_sent_once(conn, roberto):
    save(conn, roberto, [action_item(due_in_days=2)])
    assert actions.remind_due(conn) == 0
    clock.advance(conn, 1)
    assert actions.remind_due(conn) == 1
    assert actions.remind_due(conn) == 0
    assert db.list_notifications(conn, roberto)[0]["body"].startswith("Action item due")


def test_manager_sees_only_the_description(conn, roberto):
    save(conn, roberto, [action_item("Book a call with Ana", "team", 7, "book a call with Ana")])
    rows = actions.list_owned_by(conn, member_id(conn, "Juliana Costa"))
    assert set(rows[0].keys()) == {"id", "description", "due_date", "member_name"}
