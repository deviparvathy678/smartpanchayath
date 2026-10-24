"""Create or update SmartPanchayat accounts from an authenticated admin session."""

from getpass import getpass
from pathlib import Path
import sqlite3

from auth_store import ROLES, authenticate, hash_password


def main():
    connection = sqlite3.connect(Path(__file__).with_name("smartpanchayat.db"))
    connection.row_factory = sqlite3.Row
    try:
        administrator = input("Admin username: ").strip()
        if not authenticate(connection, administrator, getpass("Admin password: ")):
            raise SystemExit("Administrator authentication failed.")
        username = input("Account username to add/update: ").strip()
        role = input(f"Role ({'/'.join(ROLES)}): ").strip()
        if role not in ROLES or not username:
            raise SystemExit("Enter a username and one of the listed roles.")
        password = getpass("New account password (12+ characters): ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords did not match; account was unchanged.")
        with connection:
            connection.execute("""
                INSERT INTO users(username, password_hash, role, active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash=excluded.password_hash, role=excluded.role, active=1
            """, (username, hash_password(password), role))
        print(f"Account {username} saved with role {role}.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
