from app import auth, db
from tests.conftest import member_id

PASSWORD = auth.demo_password()


def test_hash_is_salted_and_verifies():
    a, b = auth.hash_password("s3cret"), auth.hash_password("s3cret")
    assert a != b
    assert "s3cret" not in a
    assert auth.verify_password("s3cret", a)
    assert not auth.verify_password("wrong", a)


def test_verify_rejects_malformed_hash():
    assert not auth.verify_password("x", "not-a-hash")
    assert not auth.verify_password("x", "md5$abc$def")


def test_login_with_demo_account(conn):
    result = auth.authenticate(conn, "roberto@metalurgica-silva.example", PASSWORD)
    assert result.ok
    assert result.identity.member_id == member_id(conn, "Roberto Silva")


def test_login_with_secondary_verified_email(conn):
    assert auth.authenticate(conn, " Roberto.Silva@personal.example ", PASSWORD).ok


def test_wrong_password_and_unknown_email_get_the_same_error(conn):
    wrong_password = auth.authenticate(conn, "ana@varejo-souza.example", "nope")
    unknown_email = auth.authenticate(conn, "nobody@nowhere.example", PASSWORD)
    assert not wrong_password.ok and not unknown_email.ok
    assert wrong_password.error == unknown_email.error == auth.LOGIN_FAILED_MESSAGE


def test_unverified_email_cannot_log_in(conn):
    assert not auth.authenticate(conn, "assistant@metalurgica-silva.example", PASSWORD).ok


def test_chat_login_is_not_an_email_login(conn):
    assert not auth.authenticate(conn, "roberto", PASSWORD).ok


def test_empty_password(conn):
    assert not auth.authenticate(conn, "ana@varejo-souza.example", "").ok


def test_set_password_replaces_old_one(conn):
    ana = member_id(conn, "Ana Souza")
    auth.set_password(conn, ana, "new-password")
    assert auth.authenticate(conn, "ana@varejo-souza.example", "new-password").ok
    assert not auth.authenticate(conn, "ana@varejo-souza.example", PASSWORD).ok


def test_demo_logins_list_one_email_per_person(conn):
    logins = db.list_demo_logins(conn)
    assert [r["address"] for r in logins] == [
        "roberto@metalurgica-silva.example",
        "ana@varejo-souza.example",
        "juliana@community.example",
    ]


def test_existing_database_gets_demo_passwords(tmp_path):
    # A database created before login existed has members but no credentials.
    path = tmp_path / "old.db"
    old = db.connect(path)
    db.init_db(old)
    old.execute("DELETE FROM credentials")
    old.commit()
    old.close()

    upgraded = db.connect(path)
    db.init_db(upgraded)
    assert auth.authenticate(upgraded, "ana@varejo-souza.example", PASSWORD).ok
