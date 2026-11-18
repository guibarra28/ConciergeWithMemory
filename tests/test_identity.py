from app.identity import normalize_address, resolve
from tests.conftest import member_id


def test_normalize_trims_and_lowercases():
    assert normalize_address("  Roberto@Metalurgica-Silva.EXAMPLE ") == "roberto@metalurgica-silva.example"


def test_two_emails_resolve_to_same_member(conn):
    work = resolve(conn, "email", "roberto@metalurgica-silva.example")
    personal = resolve(conn, "email", "roberto.silva@personal.example")
    assert work.member_id == personal.member_id == member_id(conn, "Roberto Silva")


def test_chat_login_resolves(conn):
    assert resolve(conn, "chat", "ana").member_id == member_id(conn, "Ana Souza")


def test_case_and_spaces_do_not_matter(conn):
    assert resolve(conn, "email", " ANA@Varejo-Souza.example ").is_known


def test_unknown_address(conn):
    assert not resolve(conn, "email", "stranger@nowhere.example").is_known


def test_similar_address_is_unknown(conn):
    # One letter off from a registered address: no fuzzy matching.
    assert not resolve(conn, "email", "roberto@metalurgica-silvaa.example").is_known


def test_unverified_identity_is_unknown(conn):
    assert not resolve(conn, "email", "assistant@metalurgica-silva.example").is_known


def test_address_only_matches_its_channel(conn):
    # A chat login used as an email address does not resolve.
    assert not resolve(conn, "email", "roberto").is_known


def test_empty_address(conn):
    assert not resolve(conn, "email", "   ").is_known
