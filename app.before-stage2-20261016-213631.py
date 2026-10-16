import sqlite3
import hashlib
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image, UnidentifiedImageError
from change_detection import (
    DETECTION_METHOD,
    compare_images,
)


DATABASE_PATH = Path(__file__).with_name("smartpanchayat.db")
PERMIT_STATUSES = ("Verified", "Pending verification", "Not provided")
REVIEW_STATUSES = (
    "Pending Review",
    "Under Investigation",
    "Verified",
    "Resolved",
)
IMAGE_CHANGE_REASON = "Potential construction change detected from image comparison"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_EVIDENCE_DIRECTORY = Path(__file__).with_name("change_images")

st.set_page_config(
    page_title="SmartPanchayat — Illegal Construction Detection System",
    page_icon="🏗️",
    layout="wide",
)


@contextmanager
def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                ward_number INTEGER NOT NULL,
                building_reference TEXT NOT NULL,
                site_address TEXT NOT NULL,
                applicant_reference TEXT NOT NULL,
                permit_number TEXT NOT NULL,
                permit_status TEXT NOT NULL,
                permit_valid_until TEXT,
                approved_floors INTEGER NOT NULL,
                observed_floors INTEGER NOT NULL,
                inspection_date TEXT NOT NULL,
                officer_remarks TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'Pending Review',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(cases)").fetchall()
        }
        if "review_status" not in columns:
            connection.execute(
                """
                ALTER TABLE cases
                ADD COLUMN review_status TEXT NOT NULL DEFAULT 'Pending Review'
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS image_change_detections (
                case_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                monitoring_area TEXT NOT NULL,
                ward_number INTEGER,
                building_reference TEXT,
                earlier_image_date TEXT NOT NULL,
                newer_image_date TEXT NOT NULL,
                detection_date TEXT NOT NULL,
                change_percentage REAL NOT NULL,
                detection_method TEXT NOT NULL,
                reason TEXT NOT NULL,
                permit_status TEXT NOT NULL DEFAULT 'Not verified',
                review_status TEXT NOT NULL DEFAULT 'Pending Review',
                earlier_image_ref TEXT NOT NULL,
                newer_image_ref TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def load_cases():
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM cases ORDER BY inspection_date DESC, case_id"
        ).fetchall()


def load_image_detections():
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM image_change_detections
            ORDER BY detection_date DESC, case_id
            """
        ).fetchall()


def text_value(value):
    return str(value).strip() if value is not None else ""


def assess_case(case):
    reasons = []

    permit_number = text_value(case["permit_number"])
    if not permit_number:
        reasons.append("Permit number is missing")

    permit_status = text_value(case["permit_status"])
    if not permit_status or permit_status.lower() == "not provided":
        reasons.append("Permit status not provided")
    elif permit_status.lower() == "pending verification":
        reasons.append("Permit status is pending verification")
    elif permit_status.lower() != "verified":
        reasons.append(f"Permit status is unrecognized ({permit_status})")

    valid_until = text_value(case["permit_valid_until"])
    if valid_until:
        try:
            if date.fromisoformat(valid_until) < date.today():
                reasons.append(f"Permit validity date passed ({valid_until})")
        except ValueError:
            reasons.append(f"Permit validity date is invalid ({valid_until})")

    observed_floors = case["observed_floors"]
    approved_floors = case["approved_floors"]
    if approved_floors is None:
        reasons.append("Approved floor count is missing")
    if observed_floors is None:
        reasons.append("Observed floor count is missing")
    if observed_floors is not None and approved_floors is not None:
        if observed_floors > approved_floors:
            reasons.append(
                f"Observed floors ({observed_floors}) exceed approved floors "
                f"({approved_floors})"
            )
        elif observed_floors < approved_floors:
            reasons.append(
                f"Observed floor count ({observed_floors}) differs from "
                f"approved floors ({approved_floors})"
            )

    inspection_date = text_value(case["inspection_date"])
    if inspection_date:
        try:
            date.fromisoformat(inspection_date)
        except ValueError:
            reasons.append(f"Inspection date is invalid ({inspection_date})")
    else:
        reasons.append("Inspection date is missing")

    return reasons


def get_review_status(case):
    status = text_value(case["review_status"])
    return status if status in REVIEW_STATUSES else "Pending Review"


def parse_date_for_input(value):
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return date.today()


def add_fictional_demo_cases():
    samples = [
        (
            "DEMO-001",
            1,
            "DEMO-BLDG-001",
            "Fictional demonstration address A",
            "Fictional owner reference A",
            "",
            "Not provided",
            None,
            1,
            2,
            "2026-01-15",
            "Fictional sample only; entered to demonstrate a missing permit record.",
        ),
        (
            "DEMO-002",
            2,
            "DEMO-BLDG-002",
            "Fictional demonstration address B",
            "Fictional owner reference B",
            "DEMO-PERMIT-002",
            "Verified",
            "2020-12-31",
            2,
            2,
            "2026-02-10",
            "Fictional sample only; entered to demonstrate an expired validity date.",
        ),
        (
            "DEMO-003",
            3,
            "DEMO-BLDG-003",
            "Fictional demonstration address C",
            "Fictional owner reference C",
            "DEMO-PERMIT-003",
            "Verified",
            "2099-12-31",
            2,
            2,
            "2026-03-05",
            "Fictional sample only; no discrepancy in the entered fields.",
        ),
    ]

    with get_connection() as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO cases (
                case_id, ward_number, building_reference, site_address,
                applicant_reference, permit_number, permit_status,
                permit_valid_until, approved_floors, observed_floors,
                inspection_date, officer_remarks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            samples,
        )
        return connection.total_changes - before


def render_dashboard():
    cases = load_cases()
    image_detections = load_image_detections()
    pending_count = sum(
        get_review_status(case) == "Pending Review" for case in cases
    ) + sum(row["review_status"] == "Pending Review" for row in image_detections)
    investigation_count = sum(
        get_review_status(case) == "Under Investigation" for case in cases
    ) + sum(row["review_status"] == "Under Investigation" for row in image_detections)
    resolved_count = sum(get_review_status(case) == "Resolved" for case in cases) + sum(
        row["review_status"] == "Resolved" for row in image_detections
    )
    discrepancy_count = sum(bool(assess_case(case)) for case in cases)

    st.header("Officer dashboard")
    st.caption(
        "Image changes and permit discrepancies are review leads only; "
        "neither is proof of an illegal building."
    )
    demo_message = st.session_state.pop("demo_cases_message", None)
    if demo_message:
        st.success(demo_message)

    total_col, pending_col, image_col = st.columns(3)
    total_col.metric("Total recorded cases", len(cases) + len(image_detections))
    pending_col.metric("Pending reviews", pending_count)
    image_col.metric("Potential image changes", len(image_detections))
    investigation_col, resolved_col, discrepancy_col = st.columns(3)
    investigation_col.metric("Under investigation", investigation_count)
    resolved_col.metric("Resolved", resolved_count)
    discrepancy_col.metric("Permit/floor discrepancies", discrepancy_count)

    st.subheader("Cases to review")
    flagged_cases = [case for case in cases if assess_case(case)]
    if not flagged_cases:
        st.info("No cases are currently flagged from the information entered.")
    else:
        rows = []
        for case in flagged_cases:
            rows.append(
                {
                    "Case ID": case["case_id"],
                    "Ward": case["ward_number"],
                    "Building reference": case["building_reference"] or "Not recorded",
                    "Reason for review": "; ".join(assess_case(case)),
                    "Inspection date": case["inspection_date"],
                    "Review status": get_review_status(case),
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)

    with st.expander("Add fictional demonstration cases"):
        st.warning(
            "These are invented examples for testing only. They are not "
            "Nagaroor Grama Panchayat records or real permit information."
        )
        if st.button("Load fictional demo cases", key="load_demo_cases"):
            added = add_fictional_demo_cases()
            st.session_state["demo_cases_message"] = (
                f"Added {added} fictional case(s). "
                "Existing demo IDs were left unchanged."
            )
            st.rerun()


def render_case_registration():
    st.header("Register a construction case")
    st.write(
        "Enter the available case and permit information. A missing record "
        "or mismatch is a prompt for verification, not a finding of illegality."
    )

    with st.form("case_registration", clear_on_submit=True):
        left, right = st.columns(2)

        with left:
            case_id = st.text_input("Case ID *", placeholder="e.g. CASE-2026-001")
            ward_number = st.number_input(
                "Ward number *", min_value=1, max_value=1000, value=1, step=1
            )
            building_reference = st.text_input("Building ID or reference number *")
            site_address = st.text_input("Site address *")
            applicant_reference = st.text_input("Applicant or owner reference *")
            permit_number = st.text_input(
                "Building permit number",
                help="Leave blank if the permit number is not available.",
            )
            permit_status = st.selectbox("Permit status *", PERMIT_STATUSES)
            has_validity_date = st.checkbox("Permit validity end date is available")
            permit_valid_until = (
                st.date_input("Permit valid through")
                if has_validity_date
                else None
            )

        with right:
            approved_floors = st.number_input(
                "Approved number of floors *",
                min_value=0,
                max_value=100,
                value=1,
                step=1,
            )
            observed_floors = st.number_input(
                "Observed number of floors *",
                min_value=0,
                max_value=100,
                value=1,
                step=1,
            )
            inspection_date = st.date_input("Inspection date *", value=date.today())
            officer_remarks = st.text_area("Officer remarks")

        submitted = st.form_submit_button("Save case", type="primary")

    if submitted:
        required_values = {
            "Case ID": case_id,
            "Building ID or reference number": building_reference,
            "Site address": site_address,
            "Applicant or owner reference": applicant_reference,
        }
        missing_fields = [
            label for label, value in required_values.items() if not value.strip()
        ]

        if missing_fields:
            st.error("Please complete the required fields: " + ", ".join(missing_fields))
        else:
            try:
                with get_connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO cases (
                            case_id, ward_number, building_reference, site_address,
                            applicant_reference, permit_number, permit_status,
                            permit_valid_until, approved_floors, observed_floors,
                            inspection_date, officer_remarks
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case_id.strip(),
                            int(ward_number),
                            building_reference.strip(),
                            site_address.strip(),
                            applicant_reference.strip(),
                            permit_number.strip(),
                            permit_status,
                            permit_valid_until.isoformat()
                            if permit_valid_until
                            else None,
                            int(approved_floors),
                            int(observed_floors),
                            inspection_date.isoformat(),
                            officer_remarks.strip(),
                        ),
                    )
                st.success(f"Case {case_id.strip()} was saved.")
            except sqlite3.IntegrityError:
                st.error(
                    f"Case ID {case_id.strip()} already exists. "
                    "Use a unique case ID."
                )
            except sqlite3.Error as error:
                st.error(f"The case could not be saved to the local database: {error}")

    st.divider()
    st.subheader("Permit verification guidance")
    st.markdown(
        """
        A case is marked **Needs Review** when a permit number is missing,
        the permit status is not verified, the recorded validity date has
        passed, or observed floors differ from approved floors. These checks
        use only the information entered here; officers must verify it against
        authorized records and inspect the site before reaching any conclusion.
        """
    )


def render_alert_management():
    st.header("Officer alert management")
    st.write(
        "Alerts describe possible changes or recorded discrepancies that need "
        "human verification. Resolving a case does not confirm permit compliance."
    )
    flagged_cases = [case for case in load_cases() if assess_case(case)]
    image_detections = load_image_detections()

    if not flagged_cases and not image_detections:
        st.info("No cases currently need review based on the recorded information.")
        return

    alert_rows = []
    alert_choices = {}
    for case in flagged_cases:
        choice = f"Case record: {case['case_id']}"
        alert_choices[choice] = ("case", case)
        permit_status = text_value(case["permit_status"])
        alert_rows.append(
            {
                "Case ID": case["case_id"],
                "Ward": case["ward_number"] if case["ward_number"] is not None else "Not recorded",
                "Building reference": case["building_reference"] or "Not recorded",
                "Reason for review": "; ".join(assess_case(case)),
                "Detection date": (case["created_at"] or case["inspection_date"] or "Not recorded")[:10],
                "Permit verification status": (
                    "Verified (recorded)"
                    if permit_status.lower() == "verified"
                    else "Not verified"
                ),
                "Review status": get_review_status(case),
            }
        )
    for detection in image_detections:
        choice = f"Image change: {detection['case_id']}"
        alert_choices[choice] = ("image", detection)
        alert_rows.append(
            {
                "Case ID": detection["case_id"],
                "Ward": detection["ward_number"] or "Not recorded",
                "Building reference": detection["building_reference"] or detection["monitoring_area"],
                "Reason for review": detection["reason"],
                "Detection date": detection["detection_date"] or "Not recorded",
                "Permit verification status": "Not verified",
                "Review status": detection["review_status"],
                "Change score": f"{detection['change_percentage']:.2f}% of pixels",
            }
        )
    st.dataframe(alert_rows, width="stretch", hide_index=True)

    selected_label = st.selectbox(
        "Select an alert to update",
        options=list(alert_choices),
        key="alert_case_selection",
    )
    source_table, selected_case = alert_choices[selected_label]
    selected_id = selected_case["case_id"]

    if source_table == "case":
        current_reasons = "; ".join(assess_case(selected_case))
    else:
        current_reasons = selected_case["reason"]
        st.caption(f"Monitoring area: {selected_case['monitoring_area']}")
        st.caption(
            f"Earlier image: {selected_case['earlier_image_date']} | "
            f"Newer image: {selected_case['newer_image_date']} | "
            f"Pixel difference: {selected_case['change_percentage']:.2f}%"
        )
    st.markdown(f"**Current review reasons:** {current_reasons}")
    st.caption(
        "Review status records the officer workflow. Verified or Resolved does "
        "not establish that the property has a valid permit."
    )

    with st.form("alert_status_update"):
        current_status = get_review_status(selected_case)
        selected_status = st.selectbox(
            "Review status",
            REVIEW_STATUSES,
            index=REVIEW_STATUSES.index(current_status),
        )
        update_submitted = st.form_submit_button("Save review status")

    if update_submitted:
        try:
            with get_connection() as connection:
                if source_table == "image":
                    result = connection.execute(
                        "UPDATE image_change_detections SET review_status = ? WHERE case_id = ?",
                        (selected_status, selected_id),
                    )
                else:
                    result = connection.execute(
                        "UPDATE cases SET review_status = ? WHERE case_id = ?",
                        (selected_status, selected_id),
                    )
                if result.rowcount != 1:
                    st.error(f"Could not find case {selected_id} to update.")
                else:
                    st.success(f"Review status for {selected_id} saved.")
        except sqlite3.Error as error:
            st.error(f"The review status could not be saved: {error}")


def _render_saved_image_detections(detections):
    if not detections:
        return
    st.divider()
    st.subheader("Potential image-change cases")
    st.caption(
        "These are officer-confirmed image comparison alerts, not confirmed "
        "construction or permit violations. Evidence files remain in the app's "
        "local evidence folder and are not shown as filesystem paths."
    )
    rows = []
    for detection in detections:
        rows.append(
            {
                "Case ID": detection["case_id"],
                "Monitoring area": detection["monitoring_area"],
                "Ward": detection["ward_number"] or "Not recorded",
                "Building reference": detection["building_reference"] or "Not recorded",
                "Earlier image date": detection["earlier_image_date"],
                "Newer image date": detection["newer_image_date"],
                "Change score": f"{detection['change_percentage']:.2f}% of pixels",
                "Permit verification": "Not verified",
                "Review status": detection["review_status"],
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)


def render_case_register():
    st.header("Saved construction cases")
    cases = load_cases()
    image_detections = load_image_detections()

    if not cases and not image_detections:
        st.info(
            "No cases have been registered yet. Use the Register case page "
            "or load the clearly labelled fictional examples from the dashboard."
        )
        return

    if not cases:
        _render_saved_image_detections(image_detections)
        return

    st.caption(f"{len(cases)} case(s) saved in the local SQLite database.")
    rows = []
    for case in cases:
        reasons = assess_case(case)
        rows.append(
            {
                "Case ID": case["case_id"],
                "Ward": case["ward_number"] if case["ward_number"] is not None else "Not recorded",
                "Building reference": case["building_reference"] or "Not recorded",
                "Address": case["site_address"] or "Not recorded",
                "Permit number": case["permit_number"] or "Not recorded",
                "Permit status": case["permit_status"] or "Not provided",
                "Approved floors": case["approved_floors"] if case["approved_floors"] is not None else "Not recorded",
                "Observed floors": case["observed_floors"] if case["observed_floors"] is not None else "Not recorded",
                "Inspection date": case["inspection_date"] or "Not recorded",
                "Review status": get_review_status(case),
                "Alert": "Needs Review" if reasons else "No recorded discrepancy",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)

    for case in cases:
        reasons = assess_case(case)
        with st.expander(f"{case['case_id']} — Ward {case['ward_number']}"):
            st.write(f"**Site:** {case['site_address'] or 'Not recorded'}")
            st.write(
                f"**Applicant/owner reference:** "
                f"{case['applicant_reference'] or 'Not recorded'}"
            )
            st.write(f"**Permit number:** {case['permit_number'] or 'Not recorded'}")
            st.write(f"**Permit status:** {case['permit_status'] or 'Not provided'}")
            st.write(
                "**Permit valid through:** "
                f"{case['permit_valid_until'] or 'Not recorded'}"
            )
            st.write(f"**Officer remarks:** {case['officer_remarks'] or 'None'}")
            st.write(f"**Review status:** {get_review_status(case)}")
            if reasons:
                st.warning("Needs Review: " + "; ".join(reasons))
            else:
                st.info(
                    "No discrepancy was found in the entered fields. "
                    "This is not confirmation of legal compliance."
                )

    st.divider()
    st.subheader("Edit a saved case")
    selected_id = st.selectbox(
        "Choose a case to edit",
        options=[case["case_id"] for case in cases],
        key="edit_case_selection",
    )
    case = next(case for case in cases if case["case_id"] == selected_id)
    case_key = "".join(
        character if character.isalnum() else "_" for character in selected_id
    )
    st.caption(f"Case ID is fixed as {selected_id}; updates will preserve this record.")

    if case["permit_valid_until"] and not _is_valid_date(case["permit_valid_until"]):
        st.warning(
            "The stored permit validity date is not a valid date. Choose a valid "
            "date below or clear the validity-date checkbox."
        )
    if case["inspection_date"] and not _is_valid_date(case["inspection_date"]):
        st.warning(
            "The stored inspection date is not valid. Select a valid inspection date below."
        )

    with st.form(f"edit_case_form_{case_key}"):
        left, right = st.columns(2)
        with left:
            edit_ward = st.number_input(
                "Ward number *",
                min_value=1,
                max_value=1000,
                value=max(1, int(case["ward_number"] or 1)),
                step=1,
                key=f"edit_ward_{case_key}",
            )
            edit_building = st.text_input(
                "Building ID or reference number *",
                value=text_value(case["building_reference"]),
                key=f"edit_building_{case_key}",
            )
            edit_address = st.text_input(
                "Site address *",
                value=text_value(case["site_address"]),
                key=f"edit_address_{case_key}",
            )
            edit_applicant = st.text_input(
                "Applicant or owner reference *",
                value=text_value(case["applicant_reference"]),
                key=f"edit_applicant_{case_key}",
            )
            edit_permit_number = st.text_input(
                "Building permit number",
                value=text_value(case["permit_number"]),
                key=f"edit_permit_number_{case_key}",
            )
            existing_permit_status = text_value(case["permit_status"]) or "Not provided"
            permit_options = list(PERMIT_STATUSES)
            if existing_permit_status not in permit_options:
                permit_options.append(existing_permit_status)
            edit_permit_status = st.selectbox(
                "Permit status *",
                permit_options,
                index=permit_options.index(existing_permit_status),
                key=f"edit_permit_status_{case_key}",
            )
            current_review_status = get_review_status(case)
            edit_review_status = st.selectbox(
                "Review status",
                REVIEW_STATUSES,
                index=REVIEW_STATUSES.index(current_review_status),
                key=f"edit_review_status_{case_key}",
                help="This is separate from permit status and records the officer's case workflow.",
            )
            has_validity_date = st.checkbox(
                "Permit validity end date is available",
                value=bool(case["permit_valid_until"]),
                key=f"edit_has_validity_{case_key}",
            )
            edit_valid_until = (
                st.date_input(
                    "Permit valid through",
                    value=parse_date_for_input(case["permit_valid_until"]),
                    key=f"edit_valid_until_{case_key}",
                )
                if has_validity_date
                else None
            )

        with right:
            edit_approved_floors = st.number_input(
                "Approved number of floors *",
                min_value=0,
                max_value=100,
                value=max(0, int(case["approved_floors"] or 0)),
                step=1,
                key=f"edit_approved_floors_{case_key}",
            )
            edit_observed_floors = st.number_input(
                "Observed number of floors *",
                min_value=0,
                max_value=100,
                value=max(0, int(case["observed_floors"] or 0)),
                step=1,
                key=f"edit_observed_floors_{case_key}",
            )
            edit_inspection_date = st.date_input(
                "Inspection date *",
                value=parse_date_for_input(case["inspection_date"]),
                key=f"edit_inspection_date_{case_key}",
            )
            edit_remarks = st.text_area(
                "Officer remarks",
                value=text_value(case["officer_remarks"]),
                key=f"edit_remarks_{case_key}",
            )

        edit_submitted = st.form_submit_button("Save case changes", type="primary")

    if edit_submitted:
        required_values = {
            "Building ID or reference number": edit_building,
            "Site address": edit_address,
            "Applicant or owner reference": edit_applicant,
        }
        missing_fields = [
            label for label, value in required_values.items() if not value.strip()
        ]
        if missing_fields:
            st.error("Please complete the required fields: " + ", ".join(missing_fields))
        else:
            try:
                with get_connection() as connection:
                    result = connection.execute(
                        """
                        UPDATE cases
                        SET ward_number = ?, building_reference = ?, site_address = ?,
                            applicant_reference = ?, permit_number = ?,
                            permit_status = ?, review_status = ?, permit_valid_until = ?,
                            approved_floors = ?, observed_floors = ?,
                            inspection_date = ?, officer_remarks = ?
                        WHERE case_id = ?
                        """,
                        (
                            int(edit_ward),
                            edit_building.strip(),
                            edit_address.strip(),
                            edit_applicant.strip(),
                            edit_permit_number.strip(),
                            edit_permit_status,
                            edit_review_status,
                            edit_valid_until.isoformat() if edit_valid_until else None,
                            int(edit_approved_floors),
                            int(edit_observed_floors),
                            edit_inspection_date.isoformat(),
                            edit_remarks.strip(),
                            selected_id,
                        ),
                    )
                    if result.rowcount != 1:
                        st.error(f"Could not find case {selected_id} to update.")
                    else:
                        st.success(f"Case {selected_id} was updated.")
            except sqlite3.Error as error:
                st.error(f"The case could not be updated in the local database: {error}")

    _render_saved_image_detections(image_detections)


def _is_valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def _detection_fingerprint(metadata, earlier_rgb, newer_rgb):
    """Return a stable key so resubmitting identical evidence is idempotent."""
    digest = hashlib.sha256()
    for value in (
        metadata["monitoring_area"].strip().casefold(),
        str(metadata["ward_number"] or ""),
        metadata["building_reference"].strip().casefold(),
        metadata["earlier_image_date"].isoformat(),
        metadata["newer_image_date"].isoformat(),
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(hashlib.sha256(earlier_rgb.tobytes()).digest())
    digest.update(hashlib.sha256(newer_rgb.tobytes()).digest())
    digest.update(DETECTION_METHOD.encode("utf-8"))
    return digest.hexdigest()


def _save_evidence_images(comparison):
    """Store reviewed source images under random, app-relative filenames."""
    IMAGE_EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    references = []
    absolute_paths = []
    try:
        for label, rgb_image in (
            ("earlier", comparison.earlier_rgb),
            ("newer", comparison.newer_rgb),
        ):
            filename = f"{token}_{label}.jpg"
            absolute_path = IMAGE_EVIDENCE_DIRECTORY / filename
            success, encoded = cv2.imencode(
                ".jpg",
                cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 94],
            )
            if not success:
                raise OSError("The image evidence could not be encoded.")
            with absolute_path.open("xb") as image_file:
                image_file.write(encoded.tobytes())
            references.append(f"change_images/{filename}")
            absolute_paths.append(absolute_path)
        return references, absolute_paths
    except Exception:
        for path in absolute_paths:
            path.unlink(missing_ok=True)
        raise


def save_image_detection(metadata, comparison):
    """Persist an officer-confirmed change and its evidence exactly once."""
    fingerprint = _detection_fingerprint(
        metadata, comparison.earlier_rgb, comparison.newer_rgb
    )
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT case_id FROM image_change_detections WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if existing:
            return existing["case_id"], False

    case_id = f"IMG-{fingerprint[:12].upper()}"
    with get_connection() as connection:
        collision = connection.execute(
            "SELECT 1 FROM cases WHERE case_id = ? UNION ALL "
            "SELECT 1 FROM image_change_detections WHERE case_id = ? LIMIT 1",
            (case_id, case_id),
        ).fetchone()
    if collision:
        case_id = f"IMG-{fingerprint[:12].upper()}-{uuid.uuid4().hex[:8].upper()}"
    references, absolute_paths = _save_evidence_images(comparison)
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO image_change_detections (
                    case_id, fingerprint, monitoring_area, ward_number,
                    building_reference, earlier_image_date, newer_image_date,
                    detection_date, change_percentage, detection_method, reason,
                    permit_status, review_status, earlier_image_ref, newer_image_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    fingerprint,
                    metadata["monitoring_area"],
                    metadata["ward_number"],
                    metadata["building_reference"] or None,
                    metadata["earlier_image_date"].isoformat(),
                    metadata["newer_image_date"].isoformat(),
                    date.today().isoformat(),
                    comparison.changed_pixel_percentage,
                    DETECTION_METHOD,
                    IMAGE_CHANGE_REASON,
                    "Not verified",
                    "Pending Review",
                    references[0],
                    references[1],
                ),
            )
        return case_id, True
    except sqlite3.IntegrityError:
        for path in absolute_paths:
            path.unlink(missing_ok=True)
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT case_id FROM image_change_detections WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if existing:
            return existing["case_id"], False
        raise
    except Exception:
        for path in absolute_paths:
            path.unlink(missing_ok=True)
        raise


def render_automatic_change_detection():
    st.header("Automatic Change Detection")
    st.write(
        "Compare two user-uploaded images of the same monitoring area. "
        "This is a computer-vision pixel comparison, not a trained AI model."
    )
    st.info(
        "A detected image change is a lead for officer review, not proof of "
        "new construction or a permit violation. Pixel percentage is not the "
        "percentage of newly constructed area."
    )
    st.caption(
        "This prototype has no user login. Keep access limited to authorised "
        "officers on a trusted machine or network."
    )

    with st.form("automatic_change_detection_form"):
        area_name = st.text_input("Monitoring-area name *", max_chars=120)
        ward_text = st.text_input("Ward number (optional)", max_chars=8)
        building_reference = st.text_input(
            "Property/building reference (optional)", max_chars=120
        )
        earlier_column, newer_column = st.columns(2)
        with earlier_column:
            earlier_date = st.date_input("Earlier image date *", value=date.today())
            earlier_upload = st.file_uploader(
                "Earlier image (JPG or PNG, up to 10 MB)",
                type=["jpg", "jpeg", "png"],
                key="auto_earlier_image",
            )
        with newer_column:
            newer_date = st.date_input("Newer image date *", value=date.today())
            newer_upload = st.file_uploader(
                "Newer image (JPG or PNG, up to 10 MB)",
                type=["jpg", "jpeg", "png"],
                key="auto_newer_image",
            )
        compare_submitted = st.form_submit_button("Run change detection", type="primary")

    if compare_submitted:
        st.session_state.pop("automatic_change_preview", None)
        clean_area = area_name.strip()
        clean_reference = building_reference.strip()
        if not clean_area:
            st.error("Enter a monitoring-area name.")
        elif earlier_date >= newer_date:
            st.error("The earlier image date must be before the newer image date.")
        elif not earlier_upload or not newer_upload:
            st.error("Upload both the earlier and newer images.")
        else:
            try:
                ward_number = None
                if ward_text.strip():
                    ward_number = int(ward_text.strip())
                    if not 1 <= ward_number <= 1000:
                        raise ValueError("Ward number must be between 1 and 1000.")
                uploads = (earlier_upload, newer_upload)
                for upload in uploads:
                    if len(upload.getvalue()) > MAX_UPLOAD_BYTES:
                        raise ValueError("Each image must be 10 MB or smaller.")
                    if Path(upload.name).suffix.lower() not in (".jpg", ".jpeg", ".png"):
                        raise ValueError("Upload JPG or PNG image files only.")

                comparison = compare_images(
                    earlier_upload.getvalue(), newer_upload.getvalue()
                )
                st.session_state["automatic_change_preview"] = {
                    "metadata": {
                        "monitoring_area": clean_area,
                        "ward_number": ward_number,
                        "building_reference": clean_reference,
                        "earlier_image_date": earlier_date,
                        "newer_image_date": newer_date,
                    },
                    "comparison": comparison,
                }
            except (ValueError, OSError, cv2.error) as error:
                st.error(f"Images could not be compared: {error}")

    saved_message = st.session_state.pop("automatic_change_saved_message", None)
    if saved_message:
        st.success(saved_message)

    preview = st.session_state.get("automatic_change_preview")
    if not preview:
        return

    comparison = preview["comparison"]
    st.subheader("Review detected differences")
    st.caption(comparison.alignment_note)
    earlier_column, newer_column, highlighted_column = st.columns(3)
    with earlier_column:
        st.markdown("**Earlier image**")
        st.image(comparison.earlier_rgb, width="stretch")
    with newer_column:
        st.markdown("**Newer image (aligned for comparison)**")
        st.image(comparison.aligned_newer_rgb, width="stretch")
    with highlighted_column:
        st.markdown("**Potential change mask**")
        st.image(comparison.highlighted_rgb, width="stretch")

    st.metric("Pixels identified as different", f"{comparison.changed_pixel_percentage:.2f}%")
    st.caption(
        "The score is the share of pixels in the comparison mask. It is not "
        "a measurement of constructed land area. Shadows, lighting, seasonal "
        "changes, occlusion, and camera movement can still cause false positives."
    )
    if comparison.changed_pixel_percentage <= 0:
        st.info("No change passed the current filtering threshold; there is no alert to save.")
        return

    st.warning(
        "Confirm only that this image comparison should be recorded for human "
        "review. The system does not decide whether construction is permitted."
    )
    if st.button("Confirm and save as a review case", type="primary"):
        try:
            case_id, created = save_image_detection(
                preview["metadata"], comparison
            )
            if created:
                st.session_state["automatic_change_saved_message"] = (
                    f"Potential change saved as case {case_id}. It appears in Officer Alert Management."
                )
            else:
                st.session_state["automatic_change_saved_message"] = (
                    f"This detection was already saved as case {case_id}; no duplicate was created."
                )
            st.session_state.pop("automatic_change_preview", None)
            st.rerun()
        except (sqlite3.Error, OSError, cv2.error) as error:
            st.error(f"The review case could not be saved: {error}")


def render_image_comparison():
    st.header("Image comparison")
    st.write(
        "Upload earlier and recent images of the same location to highlight "
        "visible pixel differences."
    )
    st.info(
        "Image differences are leads for human review, not proof of "
        "unauthorized construction."
    )

    earlier_column, recent_column = st.columns(2)
    with earlier_column:
        st.markdown("### Earlier image")
        old_file = st.file_uploader(
            "Upload the earlier site image",
            type=["jpg", "jpeg", "png"],
            key="earlier_image",
        )
    with recent_column:
        st.markdown("### Recent image")
        new_file = st.file_uploader(
            "Upload the recent site image",
            type=["jpg", "jpeg", "png"],
            key="recent_image",
        )

    if old_file is None or new_file is None:
        st.info("Upload both images to begin the comparison.")
        return

    try:
        old_image = Image.open(old_file).convert("RGB")
        new_image = Image.open(new_file).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        st.error(f"Could not read one of the uploaded images: {error}")
        return

    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size)

    old_array = np.array(old_image)
    new_array = np.array(new_image)
    difference = cv2.absdiff(old_array, new_array)
    gray = cv2.cvtColor(difference, cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)

    highlighted = new_array.copy()
    changed_pixels_mask = mask > 0
    highlighted[changed_pixels_mask] = (
        0.5 * highlighted[changed_pixels_mask]
        + 0.5 * np.array([255, 0, 0])
    ).astype(np.uint8)

    changed_pixels = np.count_nonzero(mask)
    change_percentage = changed_pixels / mask.size * 100

    st.subheader("Comparison results")
    old_result, new_result, difference_result = st.columns(3)
    with old_result:
        st.markdown("**Earlier image**")
        st.image(old_array, width="stretch")
    with new_result:
        st.markdown("**Recent image**")
        st.image(new_array, width="stretch")
    with difference_result:
        st.markdown("**Highlighted differences**")
        st.image(highlighted, width="stretch")

    st.metric("Pixels marked as different", f"{change_percentage:.2f}%")
    st.warning(
        "This percentage is not the percentage of new construction. "
        "Lighting, shadows, camera angle, image resizing, and alignment "
        "differences can cause false detections. Confirm changes through "
        "an on-site inspection and authorized records."
    )


initialize_database()

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 2rem;}
    [data-testid="stMetric"] {
        background-color: #202938 !important;
        color: #f5f7fa !important;
        border: 1px solid #465164;
        padding: 1rem;
        border-radius: 0.75rem;
    }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] {
        color: #f5f7fa !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🏗️ SmartPanchayat — Illegal Construction Detection System")
st.caption("Prototype for Panchayat officer assistance | Findings require human review")

page = st.sidebar.radio(
    "Navigation",
    (
        "Dashboard",
        "Register case",
        "Saved cases",
        "Alert management",
        "Image comparison",
        "Automatic Change Detection",
    ),
)

if page == "Dashboard":
    render_dashboard()
elif page == "Register case":
    render_case_registration()
elif page == "Saved cases":
    render_case_register()
elif page == "Alert management":
    render_alert_management()
elif page == "Automatic Change Detection":
    render_automatic_change_detection()
else:
    render_image_comparison()

st.divider()
st.caption(
    "Prototype only. It does not determine legality or issue legal notices. "
    "Use authorized records and human inspection for decisions."
)
