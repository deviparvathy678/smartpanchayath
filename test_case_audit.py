import sqlite3
import unittest

from case_audit import (
    ensure_audit_table,
    record_audit_event,
    validate_resolution_summary,
)


class CaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def tearDown(self):
        self.connection.close()

    def test_migration_is_idempotent(self):
        ensure_audit_table(self.connection)
        first = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='case_audit_events'"
        ).fetchone()[0]
        ensure_audit_table(self.connection)
        second = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='case_audit_events'"
        ).fetchone()[0]
        self.assertEqual(first, second)

    def test_notes_and_status_events_are_append_only_and_actor_is_unknown(self):
        ensure_audit_table(self.connection)
        record_audit_event(
            self.connection,
            "image_detection",
            "IMG-TEST",
            "review_status_changed",
            "Pending Review",
            "Under Investigation",
        )
        record_audit_event(
            self.connection,
            "image_detection",
            "IMG-TEST",
            "investigation_note_added",
            note="Request a site inspection.",
        )
        events = self.connection.execute(
            "SELECT * FROM case_audit_events ORDER BY event_id"
        ).fetchall()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["new_status"], "Under Investigation")
        self.assertEqual(events[1]["note"], "Request a site inspection.")
        self.assertIsNone(events[0]["actor_identity"])

    def test_resolution_requires_a_summary(self):
        with self.assertRaises(ValueError):
            validate_resolution_summary("Resolved", "  ")
        validate_resolution_summary("Resolved", "Site visit completed; record closed for follow-up.")
        validate_resolution_summary("Under Investigation", "")


if __name__ == "__main__":
    unittest.main()
