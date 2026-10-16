"""Idempotent SQLite migrations and persistence for Stage 7 records."""

from datetime import date
import json
import math

from case_audit import record_audit_event


PERMIT_STATUSES = (
    "Not Available",
    "Unverified",
    "Verification Required",
    "Verified",
    "Expired",
    "Discrepancy Requires Review",
    "Not Applicable",
)


def ensure_risk_schema(connection):
    """Create the two necessary tables and indexes without changing old rows."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS permit_records (
            permit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_location_id INTEGER NOT NULL,
            property_reference TEXT NOT NULL,
            permit_number TEXT NOT NULL,
            issuing_authority TEXT,
            issue_date TEXT,
            validity_date TEXT,
            approved_floors INTEGER,
            approved_area_sqm REAL,
            approved_land_use TEXT,
            verification_status TEXT NOT NULL DEFAULT 'Unverified'
                CHECK(verification_status IN (
                    'Not Available','Unverified','Verification Required','Verified',
                    'Expired','Discrepancy Requires Review','Not Applicable'
                )),
            verification_date TEXT,
            verification_notes TEXT,
            source_reference TEXT,
            source_type TEXT NOT NULL DEFAULT 'Manual entry',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id),
            CHECK(approved_floors IS NULL OR approved_floors >= 0),
            CHECK(approved_area_sqm IS NULL OR approved_area_sqm > 0)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_permit_records_property "
        "ON permit_records(property_location_id, permit_id DESC)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_permit_property_reference "
        "ON permit_records(property_location_id, permit_number COLLATE NOCASE)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_assessments (
            assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_key TEXT NOT NULL UNIQUE,
            case_type TEXT NOT NULL CHECK(case_type IN ('construction','image_detection')),
            case_id TEXT NOT NULL,
            property_location_id INTEGER,
            property_reference TEXT,
            risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 0 AND 100),
            risk_category TEXT NOT NULL CHECK(risk_category IN (
                'LOW PRIORITY','REVIEW REQUIRED','HIGH PRIORITY','CRITICAL REVIEW'
            )),
            image_quality_status TEXT NOT NULL,
            alignment_status TEXT NOT NULL,
            permit_status TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            recommended_action TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(case_type, case_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_risk_assessments_category "
        "ON risk_assessments(risk_category, case_type, case_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS permit_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            note TEXT,
            actor_identity TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(permit_id) REFERENCES permit_records(permit_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_permit_audit_events_permit "
        "ON permit_audit_events(permit_id, event_id)"
    )


def _validate_date(value, label):
    value = str(value or "").strip() or None
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{label} must be a valid ISO date.") from error
    return value


def save_permit_record(connection, values, permit_id=None, actor_identity=None):
    """Insert/update one manually maintained permit record and append history."""
    property_location_id = values.get("property_location_id")
    property_reference = str(values.get("property_reference") or "").strip()
    permit_number = str(values.get("permit_number") or "").strip()
    status = str(values.get("verification_status") or "Unverified").strip()
    if not property_location_id or not property_reference:
        raise ValueError("Select a registered property before saving permit information.")
    if not permit_number:
        raise ValueError("Permit reference is required; use 'Not available' only when a reference is genuinely unavailable.")
    if status not in PERMIT_STATUSES:
        raise ValueError("Select a supported permit verification status.")
    issue_date = _validate_date(values.get("issue_date"), "Issue date")
    validity_date = _validate_date(values.get("validity_date"), "Validity date")
    verification_date = _validate_date(values.get("verification_date"), "Verification date")
    source_reference = str(values.get("source_reference") or "").strip() or None
    verification_notes = str(values.get("verification_notes") or "").strip() or None
    if status == "Verified" and not (verification_date and source_reference and verification_notes):
        raise ValueError(
            "To mark a manually maintained record Verified, enter the date checked, "
            "an official source/reference, and verification notes."
        )
    floors = values.get("approved_floors")
    if floors in ("", None):
        floors = None
    else:
        try:
            floors = int(floors)
        except (TypeError, ValueError) as error:
            raise ValueError("Approved floors must be a whole number.") from error
        if floors < 0 or floors > 200:
            raise ValueError("Approved floors must be between 0 and 200.")
    area = values.get("approved_area_sqm")
    if area in ("", None):
        area = None
    else:
        try:
            area = float(area)
        except (TypeError, ValueError) as error:
            raise ValueError("Approved built-up area must be numeric.") from error
        if not math.isfinite(area) or area <= 0:
            raise ValueError("Approved built-up area must be a finite positive value.")

    fields = (
        property_location_id,
        property_reference,
        permit_number,
        str(values.get("issuing_authority") or "").strip() or None,
        issue_date,
        validity_date,
        floors,
        area,
        str(values.get("approved_land_use") or "").strip() or None,
        status,
        verification_date,
        verification_notes,
        source_reference,
        "Manual entry",
    )
    with connection:
        if permit_id is None:
            cursor = connection.execute(
                """
                INSERT INTO permit_records (
                    property_location_id, property_reference, permit_number,
                    issuing_authority, issue_date, validity_date, approved_floors,
                    approved_area_sqm, approved_land_use, verification_status,
                    verification_date, verification_notes, source_reference, source_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                fields,
            )
            saved_id = cursor.lastrowid
            event_type = "permit_record_created"
            old_status = None
        else:
            old = connection.execute(
                "SELECT verification_status FROM permit_records WHERE permit_id = ?",
                (permit_id,),
            ).fetchone()
            if old is None:
                raise ValueError("The selected permit record no longer exists.")
            saved_id = int(permit_id)
            old_status = old["verification_status"]
            connection.execute(
                """
                UPDATE permit_records SET
                    property_location_id=?, property_reference=?, permit_number=?,
                    issuing_authority=?, issue_date=?, validity_date=?, approved_floors=?,
                    approved_area_sqm=?, approved_land_use=?, verification_status=?,
                    verification_date=?, verification_notes=?, source_reference=?,
                    source_type=?, updated_at=CURRENT_TIMESTAMP
                WHERE permit_id=?
                """,
                fields + (saved_id,),
            )
            event_type = "permit_verification_status_changed" if old_status != status else "permit_record_updated"
        connection.execute(
            """
            INSERT INTO permit_audit_events (
                permit_id, event_type, old_status, new_status, note, actor_identity
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                saved_id,
                event_type,
                old_status,
                status,
                f"Source type: Manual entry; source/reference: {source_reference or 'not recorded'}",
                actor_identity,
            ),
        )
    return int(saved_id)


def persist_risk_assessment(connection, assessment, actor_identity=None):
    """Upsert current case priority and append a case-audit event on change."""
    old = connection.execute(
        "SELECT * FROM risk_assessments WHERE case_type=? AND case_id=?",
        (assessment.case_type, assessment.case_id),
    ).fetchone()
    reasons_json = json.dumps(assessment.reasons, ensure_ascii=False)
    evidence_json = json.dumps(assessment.evidence, ensure_ascii=False, default=str)
    new_values = {
        "assessment_key": assessment.assessment_key,
        "case_type": assessment.case_type,
        "case_id": assessment.case_id,
        "property_location_id": assessment.property_location_id,
        "property_reference": assessment.property_reference,
        "risk_score": assessment.risk_score,
        "risk_category": assessment.risk_category,
        "image_quality_status": assessment.image_quality_status,
        "alignment_status": assessment.alignment_status,
        "permit_status": assessment.permit_status,
        "reasons_json": reasons_json,
        "recommended_action": assessment.recommended_action,
        "evidence_json": evidence_json,
    }
    if old is not None and all(old[key] == value for key, value in new_values.items()):
        return False
    risk_result_changed = old is None or any(
        old[key] != new_values[key]
        for key in ("risk_score", "risk_category", "reasons_json", "assessment_key")
    )
    status_value = f"{assessment.risk_score}/100 {assessment.risk_category}"
    with connection:
        connection.execute(
            """
            INSERT INTO risk_assessments (
                assessment_key, case_type, case_id, property_location_id,
                property_reference, risk_score, risk_category,
                image_quality_status, alignment_status, permit_status,
                reasons_json, recommended_action, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_type, case_id) DO UPDATE SET
                assessment_key=excluded.assessment_key,
                property_location_id=excluded.property_location_id,
                property_reference=excluded.property_reference,
                risk_score=excluded.risk_score,
                risk_category=excluded.risk_category,
                image_quality_status=excluded.image_quality_status,
                alignment_status=excluded.alignment_status,
                permit_status=excluded.permit_status,
                reasons_json=excluded.reasons_json,
                recommended_action=excluded.recommended_action,
                evidence_json=excluded.evidence_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                assessment.assessment_key, assessment.case_type, assessment.case_id,
                assessment.property_location_id, assessment.property_reference,
                assessment.risk_score, assessment.risk_category,
                assessment.image_quality_status, assessment.alignment_status,
                assessment.permit_status, reasons_json, assessment.recommended_action,
                evidence_json,
            ),
        )
        if risk_result_changed:
            event_type = "risk_assessment_created" if old is None else "risk_assessment_updated"
            record_audit_event(
                connection,
                assessment.case_type,
                assessment.case_id,
                event_type,
                old_status=(f"{old['risk_score']}/100 {old['risk_category']}" if old else None),
                new_status=status_value,
                note="; ".join(assessment.reasons),
                actor_identity=actor_identity,
            )
    return True
