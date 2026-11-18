import pytest

from app import db


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
