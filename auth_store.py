"""Local password authentication with Argon2id password hashes."""

from __future__ import annotations

import sqlite3


ROLES = ("Viewer", "Officer", "Admin")


def ensure_auth_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Viewer', 'Officer', 'Admin')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def hash_password(password: str) -> str:
    from argon2 import PasswordHasher
    from argon2.low_level import Type
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    return PasswordHasher(type=Type.ID).hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
    try:
        return PasswordHasher().verify(password_hash, password)
    except (InvalidHash, VerificationError, VerifyMismatchError):
        return False


def authenticate(connection: sqlite3.Connection, username: str, password: str):
    ensure_auth_schema(connection)
    user = connection.execute(
        "SELECT username, password_hash, role FROM users WHERE username = ? AND active = 1",
        (str(username or "").strip(),),
    ).fetchone()
    if user is None or not verify_password(user["password_hash"], password):
        return None
    return {"username": user["username"], "role": user["role"]}


def add_first_admin(connection: sqlite3.Connection, username: str, password: str) -> None:
    ensure_auth_schema(connection)
    with connection:
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ValueError("An account already exists; first-admin setup is disabled.")
        name = str(username or "").strip()
        if not name:
            raise ValueError("Username is required.")
        connection.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, 'Admin')",
                           (name, hash_password(password)))


def can_manage_surveillance(user) -> bool:
    return bool(user and user.get("role") in ("Officer", "Admin"))
