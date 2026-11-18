"""Email and password login.

- Passwords are stored as salted scrypt hashes (standard library, no extra dependency).
- The login email goes through the same identity rules as inbound email: it must be a
  verified address, matched exactly (app/identity.py).
- Every failure returns the same message, so the login form does not reveal which
  emails are registered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass

from app.identity import ResolvedIdentity, resolve

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
LOGIN_FAILED_MESSAGE = "Email or password is incorrect."

# Fictional demo accounts so visitors can try the prototype. Override in production.
DEFAULT_DEMO_PASSWORD = "concierge-demo"


@dataclass(frozen=True)
class LoginResult:
    identity: ResolvedIdentity | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.identity is not None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return hmac.compare_digest(actual, expected)


def set_password(conn: sqlite3.Connection, member_id: int, password: str) -> None:
    conn.execute(
        """INSERT INTO credentials (member_id, password_hash) VALUES (?, ?)
           ON CONFLICT(member_id) DO UPDATE SET password_hash = excluded.password_hash""",
        (member_id, hash_password(password)),
    )
    conn.commit()


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> LoginResult:
    identity = resolve(conn, "email", email)
    if not identity.is_known or not password:
        return LoginResult(None, LOGIN_FAILED_MESSAGE)
    row = conn.execute(
        "SELECT password_hash FROM credentials WHERE member_id = ?", (identity.member_id,)
    ).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return LoginResult(None, LOGIN_FAILED_MESSAGE)
    return LoginResult(identity)


def demo_password() -> str:
    return os.getenv("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
