"""Create the first SmartPanchayat administrator without a default password."""

from getpass import getpass
from pathlib import Path
import sqlite3

from auth_store import add_first_admin, ensure_auth_schema


def main():
    database_path = Path(__file__).with_name("smartpanchayat.db")
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        ensure_auth_schema(connection)
        username = input("First admin username: ").strip()
        password = getpass("New password (12+ characters): ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords did not match; no account was created.")
        add_first_admin(connection, username, password)
        print("First administrator created. Sign in with the new account.")
    except (ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from error
    finally:
        connection.close()


if __name__ == "__main__":
    main()
