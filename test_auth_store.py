import sqlite3
import unittest
import importlib.util

from auth_store import ROLES, add_first_admin, authenticate, can_manage_surveillance, ensure_auth_schema


class AuthStoreTests(unittest.TestCase):
    def test_roles_and_idempotent_user_schema(self):
        connection = sqlite3.connect(":memory:")
        ensure_auth_schema(connection)
        ensure_auth_schema(connection)
        roles = {row[0] for row in connection.execute("SELECT role FROM (SELECT 'Viewer' AS role UNION SELECT 'Officer' UNION SELECT 'Admin')")}
        self.assertEqual(roles, set(ROLES))
        self.assertFalse(can_manage_surveillance({"role": "Viewer"}))
        self.assertTrue(can_manage_surveillance({"role": "Officer"}))
        self.assertTrue(can_manage_surveillance({"role": "Admin"}))
        connection.close()

    @unittest.skipUnless(importlib.util.find_spec("argon2"), "argon2-cffi dependency is not installed")
    def test_first_admin_uses_interactive_password_hash_and_blocks_second_bootstrap(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ensure_auth_schema(connection)
        add_first_admin(connection, "admin", "a-long-unique-test-passphrase")
        self.assertEqual(authenticate(connection, "admin", "a-long-unique-test-passphrase")["role"], "Admin")
        self.assertIsNone(authenticate(connection, "admin", "incorrect-password"))
        with self.assertRaisesRegex(ValueError, "already exists"):
            add_first_admin(connection, "another", "another-long-test-passphrase")
        connection.close()


if __name__ == "__main__":
    unittest.main()
