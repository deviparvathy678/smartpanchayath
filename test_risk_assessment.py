"""Synthetic pipeline and SQLite checks for Stage 7 investigation priority."""

from datetime import date
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from case_audit import ensure_audit_table, record_audit_event, validate_resolution_summary
from permit_provider import ManualPermitProvider, OfficialPermitAPIProvider
from risk_assessment import assess_investigation_priority
from risk_store import ensure_risk_schema, persist_risk_assessment, save_permit_record


def image_case(**overrides):
    result = {
        "case_id": "IMG-SYNTH-001",
        "building_reference": "PROP-SYNTH-001",
        "change_category": "Possible new building on a previously empty plot",
        "change_percentage": 18.0,
        "earlier_image_date": "2021-01-01",
        "newer_image_date": "2025-01-01",
        "detection_method": "Synthetic test only",
        "imagery_source": "Manual upload",
        "review_status": "Pending Review",
        "quality_report_json": json.dumps({"overall_status": "Pass"}),
        "alignment_report_json": json.dumps({"status": "Pass", "confidence": 0.8}),
    }
    result.update(overrides)
    return result


def verified_property(**overrides):
    value = {
        "location_id": 11,
        "property_reference": "PROP-SYNTH-001",
        "ward_number": 4,
        "monitoring_area": "Synthetic test area",
        "latitude": 12.3,
        "longitude": 76.5,
        "verification_status": "Verified",
    }
    value.update(overrides)
    return value


def verified_permit(**overrides):
    value = {
        "permit_id": 8,
        "property_location_id": 11,
        "property_reference": "PROP-SYNTH-001",
        "permit_number": "SYNTH-PERMIT-ONLY",
        "verification_status": "Verified",
        "verification_date": "2025-01-02",
        "verification_notes": "Synthetic test source checked; not a real permit.",
        "source_reference": "synthetic fixture",
        "validity_date": "2030-01-01",
        "approved_floors": 2,
        "approved_area_sqm": 180.0,
        "approved_land_use": "Synthetic fixture",
        "source_type": "Manual entry",
    }
    value.update(overrides)
    return value


class RiskAssessmentTests(unittest.TestCase):
    def test_missing_permit_is_not_absence_of_a_permit(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(), today=date(2026, 1, 1)
        )
        self.assertEqual(result.permit_status, "Not Available")
        self.assertTrue(any("does not mean no permit exists" in x for x in result.reasons))

    def test_unverified_permit_is_explicit(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(),
            permit_record=verified_permit(verification_status="Unverified"), today=date(2026, 1, 1)
        )
        self.assertEqual(result.permit_status, "Unverified")
        self.assertTrue(any("not authoritative" in x for x in result.reasons))

    def test_verified_consistent_permit_does_not_add_discrepancy_points(self):
        verified = verified_permit()
        result = assess_investigation_priority(
            "construction",
            {"case_id": "CASE-1", "building_reference": "PROP-SYNTH-001", "approved_floors": 2, "observed_floors": 2},
            property_record=verified_property(), permit_record=verified, today=date(2026, 1, 1),
        )
        self.assertEqual(result.permit_status, "Verified")
        self.assertFalse(any("Potential floor-count discrepancy" in x for x in result.reasons))

    def test_expired_verified_permit_is_flagged(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(),
            permit_record=verified_permit(validity_date="2024-01-01"), today=date(2026, 1, 1)
        )
        self.assertEqual(result.permit_status, "Expired")
        self.assertTrue(any("validity date has passed" in x for x in result.reasons))

    def test_good_quality_aligned_change_can_be_high_priority(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(),
            permit_record=None, today=date(2026, 1, 1)
        )
        self.assertGreaterEqual(result.risk_score, 50)
        self.assertEqual(result.risk_category, "HIGH PRIORITY")
        self.assertNotIn("probability", result.recommended_action.casefold())

    def test_poor_quality_caps_priority(self):
        result = assess_investigation_priority(
            "image_detection", image_case(quality_report_json=json.dumps({"overall_status": "Unsuitable"})),
            property_record=verified_property(), permit_record=verified_permit(), today=date(2026, 1, 1)
        )
        self.assertLessEqual(result.risk_score, 39)
        self.assertEqual(result.image_quality_status, "Unsuitable")

    def test_failed_alignment_caps_priority(self):
        result = assess_investigation_priority(
            "image_detection", image_case(alignment_report_json=json.dumps({"status": "Unsuitable"})),
            property_record=verified_property(), permit_record=verified_permit(), today=date(2026, 1, 1)
        )
        self.assertLessEqual(result.risk_score, 39)
        self.assertEqual(result.alignment_status, "Failed")

    def test_missing_imagery_metadata_is_not_high_priority(self):
        case = image_case(quality_report_json=None, alignment_report_json=None)
        result = assess_investigation_priority(
            "image_detection", case, property_record=verified_property(),
            permit_record=verified_permit(), today=date(2026, 1, 1)
        )
        self.assertLessEqual(result.risk_score, 49)
        self.assertEqual(result.image_quality_status, "Unknown")
        self.assertEqual(result.alignment_status, "Unknown")

    def test_missing_property_coordinates_link_caps_and_requires_verification(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), permit_record=verified_permit(), today=date(2026, 1, 1)
        )
        self.assertLessEqual(result.risk_score, 49)
        self.assertTrue(any("Property association requires verification" in x for x in result.reasons))

    def test_unverified_coordinates_cap_priority(self):
        result = assess_investigation_priority(
            "image_detection", image_case(),
            property_record=verified_property(verification_status="Unverified"),
            permit_record=verified_permit(), today=date(2026, 1, 1),
        )
        self.assertLessEqual(result.risk_score, 59)
        self.assertTrue(any("coordinates are unverified" in x for x in result.reasons))

    def test_area_discrepancy_is_not_inferred_without_measured_area(self):
        result = assess_investigation_priority(
            "construction",
            {"case_id": "CASE-2", "building_reference": "PROP-SYNTH-001", "observed_floors": 2},
            property_record=verified_property(), permit_record=verified_permit(), today=date(2026, 1, 1),
        )
        self.assertTrue(any("Insufficient evidence — officer verification required" in x for x in result.reasons))

    def test_floor_discrepancy_requires_verified_permit(self):
        case = {"case_id": "CASE-3", "building_reference": "PROP-SYNTH-001", "observed_floors": 3}
        with_permit = assess_investigation_priority(
            "construction", case, property_record=verified_property(),
            permit_record=verified_permit(approved_floors=2), today=date(2026, 1, 1),
        )
        without_permit = assess_investigation_priority(
            "construction", {**case, "approved_floors": 2},
            property_record=verified_property(), today=date(2026, 1, 1),
        )
        self.assertTrue(any("Potential floor-count discrepancy" in x for x in with_permit.reasons))
        self.assertTrue(any("not linked to a verified permit" in x for x in without_permit.reasons))
        self.assertEqual(without_permit.risk_category, "REVIEW REQUIRED")

    def test_discrepancy_status_increases_priority_and_explains(self):
        result = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(),
            permit_record=verified_permit(verification_status="Discrepancy Requires Review"), today=date(2026, 1, 1)
        )
        self.assertIn("Discrepancy", result.permit_status)
        self.assertTrue(any("Potential permit discrepancy" in x for x in result.reasons))

    def test_same_case_key_is_deterministic_and_distinct_properties_do_not_merge(self):
        first = assess_investigation_priority("image_detection", image_case(), property_record=verified_property())
        repeated = assess_investigation_priority("image_detection", image_case(), property_record=verified_property())
        other = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(location_id=12)
        )
        self.assertEqual(first.assessment_key, repeated.assessment_key)
        self.assertNotEqual(first.assessment_key, other.assessment_key)

    def test_area_and_land_use_are_not_inferred_from_pixel_score(self):
        result = assess_investigation_priority(
            "image_detection", image_case(change_percentage=99.9),
            property_record=verified_property(), permit_record=verified_permit(), today=date(2026, 1, 1)
        )
        self.assertTrue(any("Insufficient evidence — officer verification required" in x for x in result.reasons))
        self.assertTrue(any("No verified observed land-use evidence" in x for x in result.reasons))

    def test_verified_spatial_area_evidence_can_flag_a_potential_area_discrepancy(self):
        result = assess_investigation_priority(
            "construction",
            {
                "case_id": "CASE-AREA",
                "observed_area_sqm": 250.0,
                "observed_area_verified": True,
                "observed_area_source": "Synthetic surveyed plan",
                "observed_area_crs": "EPSG:32643",
            },
            property_record=verified_property(),
            permit_record=verified_permit(approved_area_sqm=180.0),
            today=date(2026, 1, 1),
        )
        self.assertTrue(any("Potential built-up-area discrepancy" in x for x in result.reasons))

    def test_verified_land_use_evidence_can_flag_a_potential_scope_discrepancy(self):
        result = assess_investigation_priority(
            "construction",
            {
                "case_id": "CASE-USE",
                "observed_land_use": "Commercial",
                "observed_land_use_verified": True,
                "observed_land_use_source": "Synthetic field report",
            },
            property_record=verified_property(),
            permit_record=verified_permit(approved_land_use="Residential"),
            today=date(2026, 1, 1),
        )
        self.assertTrue(any("Potential land-use discrepancy" in x for x in result.reasons))

    def test_not_applicable_and_verified_states_remain_separate(self):
        result = assess_investigation_priority(
            "construction", {"case_id": "CASE-4"}, property_record=verified_property(),
            permit_record=verified_permit(verification_status="Not Applicable"), today=date(2026, 1, 1)
        )
        self.assertEqual(result.permit_status, "Not Applicable")
        self.assertNotIn(result.risk_category, ("Verified", "Resolved"))


class RiskStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "risk-test.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE property_locations(location_id INTEGER PRIMARY KEY, property_reference TEXT NOT NULL)")
        self.db.execute("INSERT INTO property_locations VALUES(11, 'PROP-SYNTH-001')")
        self.db.execute("CREATE TABLE cases(case_id TEXT PRIMARY KEY, review_status TEXT NOT NULL)")
        self.db.execute("INSERT INTO cases VALUES('CASE-WORKFLOW', 'Pending Review')")
        ensure_audit_table(self.db)
        ensure_risk_schema(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_migration_runs_twice_and_preserves_existing_case(self):
        ensure_risk_schema(self.db)
        ensure_risk_schema(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM cases").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM permit_records").fetchone()[0], 0)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unverified_permit_persists_as_manual_and_audited(self):
        permit_id = save_permit_record(self.db, {
            "property_location_id": 11,
            "property_reference": "PROP-SYNTH-001",
            "permit_number": "SYNTH-ONLY",
            "verification_status": "Unverified",
            "approved_floors": "2",
        })
        row = self.db.execute("SELECT * FROM permit_records WHERE permit_id=?", (permit_id,)).fetchone()
        self.assertEqual(row["verification_status"], "Unverified")
        self.assertEqual(row["source_type"], "Manual entry")
        self.assertEqual(self.db.execute("SELECT count(*) FROM permit_audit_events").fetchone()[0], 1)

    def test_verified_permit_requires_source_date_and_notes(self):
        with self.assertRaisesRegex(ValueError, "source/reference"):
            save_permit_record(self.db, {
                "property_location_id": 11, "property_reference": "PROP-SYNTH-001",
                "permit_number": "SYNTH-ONLY", "verification_status": "Verified",
            })

    def test_verified_permit_status_update_persists_and_audits(self):
        permit_id = save_permit_record(self.db, {
            "property_location_id": 11, "property_reference": "PROP-SYNTH-001",
            "permit_number": "SYNTH-ONLY", "verification_status": "Unverified",
        })
        save_permit_record(self.db, {
            "property_location_id": 11, "property_reference": "PROP-SYNTH-001",
            "permit_number": "SYNTH-ONLY", "verification_status": "Verified",
            "verification_date": "2026-01-02", "source_reference": "synthetic official record fixture",
            "verification_notes": "Synthetic source check for unit testing only.",
        }, permit_id=permit_id)
        row = self.db.execute("SELECT verification_status FROM permit_records WHERE permit_id=?", (permit_id,)).fetchone()
        self.assertEqual(row["verification_status"], "Verified")
        self.assertEqual(self.db.execute("SELECT count(*) FROM permit_audit_events").fetchone()[0], 2)

    def test_duplicate_permit_reference_for_property_is_prevented(self):
        values = {
            "property_location_id": 11, "property_reference": "PROP-SYNTH-001",
            "permit_number": "SYNTH-ONLY", "verification_status": "Unverified",
        }
        save_permit_record(self.db, values)
        with self.assertRaises(sqlite3.IntegrityError):
            save_permit_record(self.db, values)

    def test_risk_score_category_and_reasons_persist_and_upsert_once(self):
        assessment = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(), today=date(2026, 1, 1)
        )
        self.assertTrue(persist_risk_assessment(self.db, assessment))
        self.assertFalse(persist_risk_assessment(self.db, assessment))
        row = self.db.execute("SELECT * FROM risk_assessments WHERE case_id=?", (assessment.case_id,)).fetchone()
        self.assertEqual(row["risk_score"], assessment.risk_score)
        self.assertEqual(row["risk_category"], assessment.risk_category)
        self.assertEqual(json.loads(row["reasons_json"]), assessment.reasons)
        self.assertEqual(self.db.execute("SELECT count(*) FROM risk_assessments").fetchone()[0], 1)

    def test_changed_risk_score_is_audited_without_new_case(self):
        first = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(), today=date(2026, 1, 1)
        )
        persist_risk_assessment(self.db, first)
        second = assess_investigation_priority(
            "image_detection", image_case(change_category="Other visible structural change"),
            property_record=verified_property(), today=date(2026, 1, 1)
        )
        persist_risk_assessment(self.db, second)
        self.assertEqual(self.db.execute("SELECT count(*) FROM risk_assessments").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM case_audit_events WHERE event_type LIKE 'risk_assessment_%'").fetchone()[0], 2)

    def test_review_status_snapshot_updates_without_a_false_risk_change_event(self):
        assessment = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(), today=date(2026, 1, 1)
        )
        persist_risk_assessment(self.db, assessment)
        assessment.evidence["review_status"] = "Under Investigation"
        self.assertTrue(persist_risk_assessment(self.db, assessment))
        row = self.db.execute("SELECT evidence_json FROM risk_assessments WHERE case_id=?", (assessment.case_id,)).fetchone()
        self.assertEqual(json.loads(row["evidence_json"])["review_status"], "Under Investigation")
        self.assertEqual(self.db.execute("SELECT count(*) FROM case_audit_events WHERE event_type LIKE 'risk_assessment_%'").fetchone()[0], 1)

    def test_officer_status_note_and_reopen_history_persists(self):
        validate_resolution_summary("Resolved", "Reviewed permit record and completed field visit.")
        with self.db:
            self.db.execute("UPDATE cases SET review_status='Under Investigation' WHERE case_id='CASE-WORKFLOW'")
            record_audit_event(self.db, "construction", "CASE-WORKFLOW", "review_status_changed", "Pending Review", "Under Investigation")
            record_audit_event(self.db, "construction", "CASE-WORKFLOW", "investigation_note_added", note="Synthetic note; no real case details.")
            self.db.execute("UPDATE cases SET review_status='Resolved' WHERE case_id='CASE-WORKFLOW'")
            record_audit_event(self.db, "construction", "CASE-WORKFLOW", "case_resolved", "Under Investigation", "Resolved", resolution_summary="Synthetic test resolution; no legal determination.")
            self.db.execute("UPDATE cases SET review_status='Under Investigation' WHERE case_id='CASE-WORKFLOW'")
            record_audit_event(self.db, "construction", "CASE-WORKFLOW", "case_reopened", "Resolved", "Under Investigation", note="Synthetic reopening test.")
        events = self.db.execute("SELECT event_type FROM case_audit_events WHERE case_id='CASE-WORKFLOW' ORDER BY event_id").fetchall()
        self.assertEqual([r[0] for r in events], ["review_status_changed", "investigation_note_added", "case_resolved", "case_reopened"])
        with self.assertRaisesRegex(ValueError, "resolution summary"):
            validate_resolution_summary("Resolved", "")

    def test_risk_refresh_survives_sqlite_reopen(self):
        assessment = assess_investigation_priority(
            "image_detection", image_case(), property_record=verified_property(), today=date(2026, 1, 1)
        )
        persist_risk_assessment(self.db, assessment)
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        row = self.db.execute("SELECT risk_score,risk_category FROM risk_assessments WHERE case_id=?", (assessment.case_id,)).fetchone()
        self.assertEqual((row["risk_score"], row["risk_category"]), (assessment.risk_score, assessment.risk_category))

    def test_manual_provider_and_official_stub_are_honest(self):
        provider = ManualPermitProvider(lambda property_id, reference: [verified_permit()])
        self.assertEqual(provider.lookup(property_id=11).source_type, "manual")
        result = OfficialPermitAPIProvider().lookup(property_reference="PROP-SYNTH-001")
        self.assertEqual(result.status, "not_connected")
        self.assertIn("not connected", result.message.casefold())


if __name__ == "__main__":
    unittest.main()
