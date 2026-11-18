import pytest

from app import auth, db


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    # Seeding hashes one password per demo person in every test; real cost is for production.
    monkeypatch.setattr(auth, "SCRYPT_N", 2**8)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def member_id(connection, name: str) -> int:
    return connection.execute(
        "SELECT id FROM members WHERE display_name = ?", (name,)
    ).fetchone()["id"]
