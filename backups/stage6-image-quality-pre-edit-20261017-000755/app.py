import sqlite3
import hashlib
import html
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import cv2
import folium
import numpy as np
import requests
import streamlit as st
from streamlit_folium import st_folium
from PIL import Image, UnidentifiedImageError
from change_detection import (
    DETECTION_METHOD,
    compare_images,
)
from imagery_sources import (
    AuthenticationMissing,
    Element84SentinelProvider,
    ImageryProviderError,
    NoImageryFound,
    ProviderError,
    RateLimited,
)
from historical_comparison import compare_geographic_images
from case_audit import ensure_audit_table, record_audit_event, validate_resolution_summary


DATABASE_PATH = Path(__file__).with_name("smartpanchayat.db")
PERMIT_STATUSES = ("Verified", "Pending verification", "Not provided")
REVIEW_STATUSES = (
    "Pending Review",
    "Under Investigation",
    "Additional Information Required",
    "Verified",
    "Resolved",
)
CHANGE_CATEGORIES = (
    "Possible new building on a previously empty plot",
    "Possible additional floor or building extension",
    "Other visible structural change",
    "No significant change detected",
    "Unable to determine reliably",
)
ANALYSIS_STATUSES = (
    "Potential change detected",
    "Pending officer verification",
    "Verified discrepancy",
    "No discrepancy found",
    "Closed after review",
)
FLOOR_VERIFICATION_STATUSES = ("Not verified", "Pending verification", "Verified")
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
                change_category TEXT NOT NULL DEFAULT 'Unable to determine reliably',
                analysis_status TEXT NOT NULL DEFAULT 'Pending officer verification',
                earlier_observed_floors INTEGER,
                recent_observed_floors INTEGER,
                approved_floor_count INTEGER,
                floor_evidence_source TEXT,
                floor_verification_status TEXT NOT NULL DEFAULT 'Not verified',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS property_locations (
                location_id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_reference TEXT NOT NULL COLLATE NOCASE UNIQUE,
                latitude REAL NOT NULL CHECK(latitude >= -90 AND latitude <= 90),
                longitude REAL NOT NULL CHECK(longitude >= -180 AND longitude <= 180),
                ward_number INTEGER CHECK(ward_number IS NULL OR (ward_number >= 1 AND ward_number <= 1000)),
                monitoring_area TEXT NOT NULL,
                associated_case_id TEXT,
                associated_case_type TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (associated_case_id IS NULL AND associated_case_type IS NULL)
                    OR (associated_case_id IS NOT NULL AND associated_case_type IN ('construction', 'image_detection'))
                )
            )
            """
        )
        location_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(property_locations)").fetchall()
        }
        for column_name, definition in {
            "verification_status": "TEXT NOT NULL DEFAULT 'Unverified'",
            "verification_evidence": "TEXT",
        }.items():
            if column_name not in location_columns:
                connection.execute(f"ALTER TABLE property_locations ADD COLUMN {column_name} {definition}")
        # Provenance columns are additive; existing records and IDs are untouched.
        detection_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(image_change_detections)").fetchall()
        }
        historical_columns = {
            "comparison_key": "TEXT",
            "imagery_source": "TEXT NOT NULL DEFAULT 'Manual upload'",
            "property_location_id": "INTEGER",
            "latitude": "REAL",
            "longitude": "REAL",
            "earlier_scene_id": "TEXT",
            "newer_scene_id": "TEXT",
            "valid_pixel_percentage": "REAL",
            "cloud_percentage": "REAL",
        }
        for column_name, definition in historical_columns.items():
            if column_name not in detection_columns:
                connection.execute(f"ALTER TABLE image_change_detections ADD COLUMN {column_name} {definition}")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_image_detection_comparison_key "
            "ON image_change_detections(comparison_key) WHERE comparison_key IS NOT NULL"
        )
        detection_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(image_change_detections)"
            ).fetchall()
        }
        new_detection_columns = {
            "change_category": "TEXT NOT NULL DEFAULT 'Unable to determine reliably'",
            "analysis_status": "TEXT NOT NULL DEFAULT 'Pending officer verification'",
            "earlier_observed_floors": "INTEGER",
            "recent_observed_floors": "INTEGER",
            "approved_floor_count": "INTEGER",
            "floor_evidence_source": "TEXT",
            "floor_verification_status": "TEXT NOT NULL DEFAULT 'Not verified'",
        }
        for column_name, column_definition in new_detection_columns.items():
            if column_name not in detection_columns:
                connection.execute(
                    f"ALTER TABLE image_change_detections "
                    f"ADD COLUMN {column_name} {column_definition}"
                )
        # Append-only investigation history; no historical case rows are rewritten.
        ensure_audit_table(connection)
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


def load_property_locations():
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM property_locations ORDER BY property_reference COLLATE NOCASE"
        ).fetchall()


def save_property_location(
    property_reference,
    latitude,
    longitude,
    ward_number,
    monitoring_area,
    associated_case_id=None,
    associated_case_type=None,
    verification_status="Unverified",
    verification_evidence=None,
):
    """Validate and persist an officer-selected property point."""
    property_reference = text_value(property_reference)
    monitoring_area = text_value(monitoring_area)
    if not property_reference:
        raise ValueError("Property reference is required.")
    if not monitoring_area:
        raise ValueError("Monitoring-area name is required.")
    if verification_status not in ("Unverified", "Verified"):
        raise ValueError("Location verification must be Unverified or Verified.")
    verification_evidence = text_value(verification_evidence) or None
    if verification_status == "Verified" and not verification_evidence:
        raise ValueError("Enter the source used to verify the property location.")
    latitude, longitude = validate_coordinates(latitude, longitude)
    if ward_number is not None:
        try:
            ward_number = int(ward_number)
        except (TypeError, ValueError) as error:
            raise ValueError("Ward number must be a whole number from 1 to 1000.") from error
        if not 1 <= ward_number <= 1000:
            raise ValueError("Ward number must be a whole number from 1 to 1000.")
    if associated_case_id is None:
        associated_case_type = None
    elif associated_case_type == "construction":
        with get_connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (associated_case_id,)
            ).fetchone()
        if not exists:
            raise ValueError("The selected construction case no longer exists.")
    elif associated_case_type == "image_detection":
        with get_connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM image_change_detections WHERE case_id = ?",
                (associated_case_id,),
            ).fetchone()
        if not exists:
            raise ValueError("The selected image-review case no longer exists.")
    else:
        raise ValueError("The selected case type is invalid.")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO property_locations (
                property_reference, latitude, longitude, ward_number,
                monitoring_area, associated_case_id, associated_case_type,
                verification_status, verification_evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                property_reference,
                latitude,
                longitude,
                ward_number,
                monitoring_area,
                associated_case_id,
                associated_case_type,
                verification_status,
                verification_evidence,
            ),
        )
        if associated_case_id:
            record_audit_event(
                connection,
                associated_case_type,
                associated_case_id,
                "case_linked_to_property",
                note=f"Linked to property reference {property_reference}.",
            )


def validate_coordinates(latitude, longitude):
    """Return normalized coordinates or raise a readable validation error."""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter valid numeric latitude and longitude values.") from error
    if not np.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not np.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    return latitude, longitude


def coordinates_from_map_result(map_result):
    """Read and validate Folium's click result without assuming a location."""
    if not isinstance(map_result, dict):
        return None
    clicked = map_result.get("last_clicked")
    if not isinstance(clicked, dict) or clicked.get("lat") is None or clicked.get("lng") is None:
        return None
    return validate_coordinates(clicked["lat"], clicked["lng"])


def set_selected_map_coordinates(coordinates):
    """Keep the visible coordinate fields synchronized with a map selection."""
    latitude, longitude = validate_coordinates(*coordinates)
    st.session_state["gps_selected_coordinates"] = (latitude, longitude)
    st.session_state["gps_latitude_input"] = latitude
    st.session_state["gps_longitude_input"] = longitude


def load_mappable_case_records():
    """Return existing construction cases and image-detection cases for linking."""
    records = []
    for case in load_cases():
        records.append(
            {
                "case_id": case["case_id"],
                "case_type": "construction",
                "label": (
                    f"{case['case_id']} — {case['building_reference'] or 'No building reference'}"
                ),
                "record": dict(case),
            }
        )
    for detection in load_image_detections():
        records.append(
            {
                "case_id": detection["case_id"],
                "case_type": "image_detection",
                "label": (
                    f"{detection['case_id']} — "
                    f"{detection['building_reference'] or detection['monitoring_area']} (image review)"
                ),
                "record": dict(detection),
            }
        )
    return records


@st.cache_data(ttl=3600, show_spinner=False)
def search_public_location(query):
    """Search OpenStreetMap Nominatim only after an officer submits a query."""
    limiter = _nominatim_rate_limiter()
    with limiter["lock"]:
        delay = 1.1 - (time.monotonic() - limiter["last_request"])
        if delay > 0:
            time.sleep(delay)
        limiter["last_request"] = time.monotonic()
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 5},
            headers={
                "User-Agent": "SmartPanchayatPropertyMap/1.0 (local officer prototype)"
            },
            timeout=10,
        )
    response.raise_for_status()
    results = []
    for item in response.json():
        try:
            latitude, longitude = validate_coordinates(item["lat"], item["lon"])
        except (KeyError, ValueError):
            continue
        results.append(
            {
                "label": item.get("display_name", "Location result"),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return results


@st.cache_resource
def _nominatim_rate_limiter():
    """Share a one-request-per-second Nominatim limit across app sessions."""
    return {"lock": threading.Lock(), "last_request": 0.0}


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
        existing_ids = {
            row["case_id"]
            for row in connection.execute(
                "SELECT case_id FROM cases WHERE case_id IN (?, ?, ?)",
                ("DEMO-001", "DEMO-002", "DEMO-003"),
            ).fetchall()
        }
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
        for sample in samples:
            if sample[0] not in existing_ids:
                record_audit_event(
                    connection,
                    "construction",
                    sample[0],
                    "case_created",
                    new_status="Pending Review",
                    note="Fictional demonstration record.",
                )
        return sum(sample[0] not in existing_ids for sample in samples)


def render_dashboard():
    cases = load_cases()
    image_detections = load_image_detections()
    pending_count = sum(
        get_review_status(case) == "Pending Review" for case in cases
    ) + sum(
        row["review_status"] == "Pending Review"
        and row["analysis_status"] not in ("No discrepancy found", "Closed after review")
        for row in image_detections
    )
    investigation_count = sum(
        get_review_status(case) == "Under Investigation" for case in cases
    ) + sum(row["review_status"] == "Under Investigation" for row in image_detections)
    resolved_count = sum(get_review_status(case) == "Resolved" for case in cases) + sum(
        row["review_status"] == "Resolved" for row in image_detections
    )
    additional_info_count = sum(
        get_review_status(case) == "Additional Information Required" for case in cases
    ) + sum(
        row["review_status"] == "Additional Information Required" for row in image_detections
    )
    permit_awaiting_count = len(cases)
    discrepancy_count = sum(bool(assess_case(case)) for case in cases)
    potential_image_changes = sum(
        row["change_category"] != "No significant change detected"
        and row["analysis_status"] not in ("No discrepancy found", "Closed after review")
        for row in image_detections
    )
    manual_comparisons = sum(
        text_value(row["imagery_source"] if "imagery_source" in row.keys() else "Manual upload") == "Manual upload"
        for row in image_detections
    )
    retrieved_comparisons = sum(
        text_value(row["imagery_source"] if "imagery_source" in row.keys() else "Manual upload") != "Manual upload"
        for row in image_detections
    )
    potential_awaiting = sum(
        row["review_status"] == "Pending Review"
        and row["change_category"] != "No significant change detected"
        and row["analysis_status"] not in ("No discrepancy found", "Closed after review")
        for row in image_detections
    )

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
    image_col.metric("Potential image changes", potential_image_changes)
    investigation_col, resolved_col, discrepancy_col = st.columns(3)
    investigation_col.metric("Under investigation", investigation_count)
    resolved_col.metric("Resolved", resolved_count)
    discrepancy_col.metric("Permit/floor discrepancies", discrepancy_count)
    info_col, permit_col, blank_col = st.columns(3)
    info_col.metric("Awaiting additional information", additional_info_count)
    permit_col.metric("Permit records awaiting verification", permit_awaiting_count)
    blank_col.empty()
    st.caption(
        "Permit verification count reflects construction-case entries only. "
        "No authoritative permit database is connected, so an entered 'Verified' "
        "value does not by itself confirm an official record was checked."
    )
    manual_col, retrieved_col, awaiting_col = st.columns(3)
    manual_col.metric("Manual-upload comparisons", manual_comparisons)
    retrieved_col.metric("Retrieved-imagery comparisons", retrieved_comparisons)
    awaiting_col.metric("Potential changes awaiting review", potential_awaiting)

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
                    record_audit_event(
                        connection,
                        "construction",
                        case_id.strip(),
                        "case_created",
                        new_status="Pending Review",
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


def load_case_audit_events(case_type, case_id):
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM case_audit_events WHERE case_type = ? AND case_id = ? "
            "ORDER BY event_id DESC",
            (case_type, case_id),
        ).fetchall()


def _render_case_evidence(case_type, record, locations):
    with st.expander("Case evidence and linked records", expanded=True):
        location = None
        if case_type == "image" and record["property_location_id"] is not None:
            location = next((p for p in locations if p["location_id"] == record["property_location_id"]), None)
        if location is None:
            reference = text_value(record["building_reference"])
            if reference:
                location = next((p for p in locations if text_value(p["property_reference"]).casefold() == reference.casefold()), None)
        if location is not None:
            st.write(f"**Property reference:** {location['property_reference']}")
            st.write(f"**Coordinates:** {location['latitude']:.6f}, {location['longitude']:.6f}")
            if text_value(location["verification_status"]) == "Verified":
                st.write(f"**Coordinate verification:** Verified — {location['verification_evidence'] or 'source not recorded'}")
            else:
                st.warning("Unverified location. Confirm coordinates using an authorised source.")
        elif case_type == "image" and record["latitude"] is not None and record["longitude"] is not None:
            st.write(f"**Recorded coordinates:** {record['latitude']:.6f}, {record['longitude']:.6f}")
            st.warning("Coordinate verification status is unavailable for this record.")
        else:
            st.write("**Property reference:** " + (text_value(record["building_reference"]) or "Not recorded"))
            st.info("No linked property coordinates are available.")

        if case_type == "image":
            st.write(f"**Detection method:** {text_value(record['detection_method']) or 'Not recorded'}")
            st.write(f"**Imagery source:** {text_value(record['imagery_source']) or 'Not recorded'}")
            st.write(f"**Earlier / newer dates:** {record['earlier_image_date']} / {record['newer_image_date']}")
            st.write(f"**Potential-change description:** {text_value(record['change_category']) or 'Not recorded'}")
            st.write(f"**Pixel-difference score:** {record['change_percentage']:.2f}% of image pixels (not construction area)")
            if record["valid_pixel_percentage"] is not None:
                st.write(f"**Valid pixels:** {record['valid_pixel_percentage']:.1f}%")
            if record["cloud_percentage"] is not None:
                st.write(f"**Cloud percentage:** {record['cloud_percentage']:.1f}%")
            st.write(f"**Permit verification:** {text_value(record['permit_status']) if text_value(record['permit_status']).casefold() == 'verified' else 'Permit status not verified'}")
            evidence_paths = [
                _evidence_path_from_reference(record["earlier_image_ref"]),
                _evidence_path_from_reference(record["newer_image_ref"]),
            ]
            images = []
            for path in evidence_paths:
                try:
                    if path is None or not path.is_file():
                        images.append(None)
                    else:
                        with Image.open(path) as image:
                            images.append(image.convert("RGB").copy())
                except (OSError, UnidentifiedImageError, ValueError):
                    images.append(None)
            left, right = st.columns(2)
            for column, image, caption in zip(
                (left, right), images,
                (f"Earlier image — {record['earlier_image_date']}", f"Newer image — {record['newer_image_date']}"),
            ):
                if image is None:
                    column.warning("Image evidence is missing or unreadable.")
                else:
                    column.image(image, caption=caption, width="stretch")
            if all(image is not None for image in images):
                try:
                    comparison = compare_images(evidence_paths[0].read_bytes(), evidence_paths[1].read_bytes())
                    st.image(comparison.highlighted_rgb, caption="Recalculated preview: highlighted pixel differences. This is not the original stored mask.", width="stretch")
                except (OSError, ValueError, cv2.error):
                    st.info("A comparison overlay could not be recalculated from the saved images.")
            else:
                st.caption("No original change mask is stored for this case.")
            location_id = record["property_location_id"]
            related = [
                item for item in load_image_detections()
                if item["case_id"] != record["case_id"] and (
                    (location_id is not None and item["property_location_id"] == location_id)
                    or (text_value(record["building_reference"]) and text_value(item["building_reference"]).casefold() == text_value(record["building_reference"]).casefold())
                )
            ]
            if related:
                st.markdown("**Related recorded image observations**")
                st.dataframe([{
                    "Case ID": item["case_id"], "Dates": f"{item['earlier_image_date']} / {item['newer_image_date']}",
                    "Source": item["imagery_source"], "Method": item["detection_method"], "Review status": item["review_status"],
                } for item in related], hide_index=True, width="stretch")
        else:
            st.write(f"**Inspection date:** {text_value(record['inspection_date']) or 'Not recorded'}")
            st.write(f"**Officer remarks:** {text_value(record['officer_remarks']) or 'None recorded'}")
            permit = text_value(record["permit_status"])
            if permit.casefold() == "verified":
                st.write("**Permit status:** Entered as Verified; authoritative source is not recorded in this app.")
            else:
                st.write("**Permit status:** Permit status not verified")
            if text_value(record["permit_number"]):
                st.write(f"**Permit reference:** {record['permit_number']}")
            else:
                st.write("**Permit reference:** Not recorded")
            st.write(f"**Approved / observed floors:** {record['approved_floors']} / {record['observed_floors']}")

        events = load_case_audit_events(case_type, record["case_id"])
        st.markdown("**Investigation history**")
        if not events:
            st.caption("No audit events have been recorded for this existing case yet.")
        else:
            st.dataframe([{
                "Time": event["created_at"], "Event": event["event_type"],
                "Previous status": event["old_status"] or "—", "New status": event["new_status"] or "—",
                "Note": event["note"] or event["resolution_summary"] or "—",
                "Officer identity": event["actor_identity"] or "Not authenticated",
            } for event in events], width="stretch", hide_index=True)


def render_alert_management():
    st.header("Officer alert management")
    st.write(
        "Review potential image changes and recorded discrepancies in one queue. "
        "Findings require human verification; resolving a case does not confirm "
        "permit compliance or declare a violation."
    )
    flagged_cases = [
        case for case in load_cases()
        if assess_case(case) or get_review_status(case) != "Resolved"
    ]
    image_detections = load_image_detections()

    if not flagged_cases and not image_detections:
        st.info("No cases currently need review based on the recorded information.")
        return

    locations = load_property_locations()
    alert_records = []
    for case in flagged_cases:
        permit_status = text_value(case["permit_status"])
        location = next(
            (item for item in locations if item["property_reference"].casefold() == text_value(case["building_reference"]).casefold()),
            None,
        )
        alert_records.append({
            "case_type": "case",
            "case": case,
            "property_reference": text_value(case["building_reference"]) or "Not recorded",
            "monitoring_area": location["monitoring_area"] if location else "Not recorded",
            "ward": case["ward_number"],
            "method": "Permit/floor record review",
            "permit": (
                "Entered as Verified; authority not recorded"
                if permit_status.casefold() == "verified"
                else "Permit status not verified"
            ),
            "reason": "; ".join(assess_case(case)) or "No recorded permit/floor discrepancy; case remains in the review workflow",
            "detection_date": text_value(case["inspection_date"]) or text_value(case["created_at"]),
        })
    for detection in image_detections:
        location = next(
            (item for item in locations if (
                ("property_location_id" in detection.keys() and item["location_id"] == detection["property_location_id"])
                or text_value(item["property_reference"]).casefold() == text_value(detection["building_reference"]).casefold()
                or (item["associated_case_id"] == detection["case_id"] and item["associated_case_type"] == "image_detection")
            )),
            None,
        )
        alert_records.append({
            "case_type": "image",
            "case": detection,
            "property_reference": text_value(detection["building_reference"]) or (location["property_reference"] if location else "Not recorded"),
            "monitoring_area": text_value(detection["monitoring_area"]) or (location["monitoring_area"] if location else "Not recorded"),
            "ward": detection["ward_number"],
            "method": text_value(detection["detection_method"]) or "Not recorded",
            "permit": "Permit status not verified",
            "reason": text_value(detection["reason"]) or "Potential change requires officer review",
            "detection_date": text_value(detection["detection_date"]) or text_value(detection["created_at"]),
        })

    ward_options = sorted({str(r["ward"]) for r in alert_records if r["ward"] is not None})
    method_options = sorted({r["method"] for r in alert_records})
    status_options = sorted({get_review_status(r["case"]) for r in alert_records})
    permit_options = sorted({r["permit"] for r in alert_records})
    filter_cols = st.columns(4)
    selected_ward = filter_cols[0].selectbox("Ward", ["All"] + ward_options, key="alert_filter_ward")
    selected_method = filter_cols[1].selectbox("Detection method", ["All"] + method_options, key="alert_filter_method")
    selected_review = filter_cols[2].selectbox("Review status", ["All"] + status_options, key="alert_filter_review")
    selected_permit = filter_cols[3].selectbox("Permit verification", ["All"] + permit_options, key="alert_filter_permit")
    search_text = st.text_input("Search alerts", placeholder="Case ID, property, area, or reason", key="alert_filter_search").strip().casefold()

    filtered_records = []
    for item in alert_records:
        record = item["case"]
        if selected_ward != "All" and str(item["ward"]) != selected_ward:
            continue
        if selected_method != "All" and item["method"] != selected_method:
            continue
        if selected_review != "All" and get_review_status(record) != selected_review:
            continue
        if selected_permit != "All" and item["permit"] != selected_permit:
            continue
        searchable = " ".join((record["case_id"], item["property_reference"], item["monitoring_area"], item["reason"])).casefold()
        if search_text and search_text not in searchable:
            continue
        filtered_records.append(item)

    if not filtered_records:
        st.info("No alerts match the selected filters.")
        return

    alert_rows = []
    alert_choices = {}
    for item in filtered_records:
        record = item["case"]
        label = f"{record['case_id']} — {item['property_reference']}"
        alert_choices[label] = item
        alert_rows.append({
            "Case ID": record["case_id"],
            "Property reference": item["property_reference"],
            "Ward": item["ward"] if item["ward"] is not None else "Not recorded",
            "Monitoring area": item["monitoring_area"],
            "Detection date": item["detection_date"] or "Not recorded",
            "Earlier image date": record["earlier_image_date"] if item["case_type"] == "image" else "Not available",
            "Newer image date": record["newer_image_date"] if item["case_type"] == "image" else "Not available",
            "Detection method": item["method"],
            "Potential change / review reason": record["change_category"] if item["case_type"] == "image" else item["reason"],
            "Permit verification": item["permit"],
            "Review status": get_review_status(record),
            "Assigned officer": "Not supported (no authentication)",
        })
    st.dataframe(alert_rows, width="stretch", hide_index=True)

    selected_label = st.selectbox("Open an alert", options=list(alert_choices), key="alert_case_selection")
    selected_item = alert_choices[selected_label]
    source_table = selected_item["case_type"]
    selected_case = selected_item["case"]
    selected_id = selected_case["case_id"]
    current_reasons = selected_item["reason"]
    if source_table == "image":
        st.caption(
            f"Earlier image: {selected_case['earlier_image_date']} | "
            f"Newer image: {selected_case['newer_image_date']} | "
            f"Pixel-difference score: {selected_case['change_percentage']:.2f}%"
        )
    st.markdown(f"**Current review reasons:** {current_reasons}")
    _render_case_evidence(source_table, selected_case, locations)
    st.caption(
        "Image-change findings, permit verification, investigation status, and "
        "administrative decisions are separate. No login is configured; event "
        "history has no authenticated officer identity."
    )
    st.caption(
        "The legacy review status and image analysis status are separate. "
        "Verified discrepancy means the entered evidence supports a floor-count "
        "mismatch; it does not establish illegality or permit non-compliance."
    )

    if source_table == "image":
        with st.expander("Correct saved change analysis and floor counts"):
            with st.form("saved_analysis_correction"):
                corrected_category = st.selectbox(
                    "Preliminary change category",
                    CHANGE_CATEGORIES,
                    index=(
                        CHANGE_CATEGORIES.index(selected_case["change_category"])
                        if selected_case["change_category"] in CHANGE_CATEGORIES
                        else CHANGE_CATEGORIES.index("Unable to determine reliably")
                    ),
                )
                earlier_default = selected_case["earlier_observed_floors"]
                recent_default = selected_case["recent_observed_floors"]
                approved_default = selected_case["approved_floor_count"]
                corrected_earlier = st.number_input(
                    "Earlier observed floors (leave blank if unknown)",
                    min_value=0,
                    max_value=100,
                    value=int(earlier_default or 0),
                    step=1,
                )
                earlier_known = st.checkbox(
                    "Earlier floor count is known",
                    value=earlier_default is not None,
                )
                corrected_recent = st.number_input(
                    "Recent observed floors (leave blank if unknown)",
                    min_value=0,
                    max_value=100,
                    value=int(recent_default or 0),
                    step=1,
                )
                recent_known = st.checkbox(
                    "Recent floor count is known",
                    value=recent_default is not None,
                )
                corrected_approved = st.number_input(
                    "Approved floors (leave blank if unknown)",
                    min_value=0,
                    max_value=100,
                    value=int(approved_default or 0),
                    step=1,
                )
                approved_known = st.checkbox(
                    "Approved floor count is known",
                    value=approved_default is not None,
                )
                corrected_source = st.text_input(
                    "Evidence source",
                    value=selected_case["floor_evidence_source"] or "",
                    max_chars=200,
                )
                corrected_verification = st.selectbox(
                    "Floor-count verification status",
                    FLOOR_VERIFICATION_STATUSES,
                    index=(
                        FLOOR_VERIFICATION_STATUSES.index(
                            selected_case["floor_verification_status"]
                        )
                        if selected_case["floor_verification_status"]
                        in FLOOR_VERIFICATION_STATUSES
                        else 0
                    ),
                )
                correction_submitted = st.form_submit_button(
                    "Save corrected analysis details"
                )
            if correction_submitted:
                corrected_recent_value = corrected_recent if recent_known else None
                corrected_approved_value = corrected_approved if approved_known else None
                valid_verified_mismatch = (
                    corrected_verification == "Verified"
                    and bool(corrected_source.strip())
                    and corrected_recent_value is not None
                    and corrected_approved_value is not None
                    and corrected_recent_value != corrected_approved_value
                )
                if (
                    selected_case["analysis_status"] == "Verified discrepancy"
                    and not valid_verified_mismatch
                ):
                    st.error(
                        "Keep the analysis status at Pending officer verification "
                        "until a verified source and differing recent/approved "
                        "floor counts are recorded."
                    )
                elif corrected_verification == "Verified" and (
                    corrected_approved_value is None or not corrected_source.strip()
                ):
                    st.error(
                        "Verified floor counts require an approved count and "
                        "an evidence source."
                    )
                else:
                    try:
                        verified_floor_mismatch = (
                            corrected_verification == "Verified"
                            and corrected_recent_value is not None
                            and corrected_approved_value is not None
                            and corrected_recent_value != corrected_approved_value
                        )
                        corrected_reason = (
                            "No significant change passed pixel screening"
                            if corrected_category == "No significant change detected"
                            else IMAGE_CHANGE_REASON
                        )
                        if verified_floor_mismatch:
                            corrected_reason += (
                                f"; manually recorded floor discrepancy: "
                                f"{corrected_recent_value} observed versus "
                                f"{corrected_approved_value} approved (source: "
                                f"{corrected_source.strip()})"
                            )
                        with get_connection() as connection:
                            connection.execute(
                                "UPDATE image_change_detections SET "
                                "change_category = ?, earlier_observed_floors = ?, "
                                "recent_observed_floors = ?, approved_floor_count = ?, "
                                "floor_evidence_source = ?, floor_verification_status = ?, "
                                "reason = ? "
                                "WHERE case_id = ?",
                                (
                                    corrected_category,
                                    corrected_earlier if earlier_known else None,
                                    corrected_recent_value,
                                    corrected_approved_value,
                                    corrected_source.strip() or None,
                                    corrected_verification,
                                    corrected_reason,
                                    selected_id,
                                ),
                            )
                        st.success("Saved analysis details updated.")
                        st.rerun()
                    except sqlite3.Error as error:
                        st.error(f"Analysis details could not be updated: {error}")

    with st.form("alert_status_update"):
        current_status = get_review_status(selected_case)
        selected_status = st.selectbox(
            "Investigation status",
            REVIEW_STATUSES,
            index=REVIEW_STATUSES.index(current_status),
            help="This is separate from permit verification and image-analysis status.",
        )
        investigation_note = st.text_area("Investigation note (optional)", max_chars=4000)
        resolution_summary = st.text_area(
            "Resolution summary (required when resolving)",
            max_chars=4000,
            help="Summarize the administrative outcome or next action. You do not need to declare a violation.",
        )
        selected_analysis_status = None
        if source_table == "image":
            selected_analysis_status = st.selectbox(
                "Image analysis status",
                ANALYSIS_STATUSES,
                index=(
                    ANALYSIS_STATUSES.index(selected_case["analysis_status"])
                    if selected_case["analysis_status"] in ANALYSIS_STATUSES
                    else ANALYSIS_STATUSES.index("Pending officer verification")
                ),
            )
        update_submitted = st.form_submit_button("Save investigation update")

    if update_submitted:
        if selected_status == "Resolved" and current_status != "Resolved":
            try:
                validate_resolution_summary(selected_status, resolution_summary)
            except ValueError as error:
                st.error(str(error))
                return
        verified_floor_evidence = (
            selected_case["floor_verification_status"] == "Verified"
            and bool(text_value(selected_case["floor_evidence_source"]))
            and selected_case["approved_floor_count"] is not None
            and selected_case["recent_observed_floors"] is not None
            and selected_case["approved_floor_count"]
            != selected_case["recent_observed_floors"]
        )
        if (
            source_table == "image"
            and selected_analysis_status == "Verified discrepancy"
            and not verified_floor_evidence
        ):
            st.error(
                "Verified discrepancy requires a manually entered recent floor "
                "count, a verified approved count, and its evidence source."
            )
            return
        try:
            with get_connection() as connection:
                table = "image_change_detections" if source_table == "image" else "cases"
                current_row = connection.execute(
                    f"SELECT review_status FROM {table} WHERE case_id = ?",
                    (selected_id,),
                ).fetchone()
                if current_row is None:
                    st.error(f"Could not find case {selected_id} to update.")
                    return
                old_status = text_value(current_row["review_status"]) or "Pending Review"
                audit_case_type = "image_detection" if source_table == "image" else "construction"
                if source_table == "image":
                    result = connection.execute(
                        "UPDATE image_change_detections "
                        "SET review_status = ?, analysis_status = ? WHERE case_id = ?",
                        (selected_status, selected_analysis_status, selected_id),
                    )
                else:
                    result = connection.execute(
                        "UPDATE cases SET review_status = ? WHERE case_id = ?",
                        (selected_status, selected_id),
                    )
                if result.rowcount != 1:
                    st.error(f"Could not find case {selected_id} to update.")
                else:
                    if old_status != selected_status:
                        event_type = "case_reopened" if old_status == "Resolved" and selected_status != "Resolved" else (
                            "case_resolved" if selected_status == "Resolved" else "review_status_changed"
                        )
                        record_audit_event(
                            connection, audit_case_type, selected_id, event_type,
                            old_status=old_status, new_status=selected_status,
                            resolution_summary=resolution_summary if selected_status == "Resolved" else None,
                        )
                    if investigation_note.strip():
                        record_audit_event(
                            connection, audit_case_type, selected_id, "investigation_note_added",
                            old_status=old_status, new_status=selected_status,
                            note=investigation_note,
                        )
                    if selected_status == "Resolved" and resolution_summary.strip() and old_status == "Resolved":
                        record_audit_event(
                            connection, audit_case_type, selected_id, "resolution_summary_updated",
                            old_status=old_status, new_status=selected_status,
                            resolution_summary=resolution_summary,
                        )
                    st.success(f"Investigation update for {selected_id} saved.")
        except sqlite3.Error as error:
            st.error(f"The investigation update could not be saved: {error}")

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
                "Analysis result": detection["change_category"],
                "Analysis status": detection["analysis_status"],
                "Change score": f"{detection['change_percentage']:.2f}% of pixels",
                "Floor counts": (
                    f"earlier {detection['earlier_observed_floors'] if detection['earlier_observed_floors'] is not None else 'unknown'}; "
                    f"recent {detection['recent_observed_floors'] if detection['recent_observed_floors'] is not None else 'unknown'}; "
                    f"approved {detection['approved_floor_count'] if detection['approved_floor_count'] is not None else 'unknown'}"
                ),
                "Floor evidence": detection["floor_evidence_source"] or "Not recorded",
                "Floor verification": detection["floor_verification_status"],
                "Analysis method": detection["detection_method"],
                "Imagery source": detection["imagery_source"] if "imagery_source" in detection.keys() else "Manual upload",
                "Earlier scene": detection["earlier_scene_id"] if "earlier_scene_id" in detection.keys() else None,
                "Newer scene": detection["newer_scene_id"] if "newer_scene_id" in detection.keys() else None,
                "Valid pixels": (
                    f"{detection['valid_pixel_percentage']:.1f}%"
                    if "valid_pixel_percentage" in detection.keys() and detection["valid_pixel_percentage"] is not None
                    else "Not recorded"
                ),
                "Cloud mask": (
                    f"{detection['cloud_percentage']:.1f}%"
                    if "cloud_percentage" in detection.keys() and detection["cloud_percentage"] is not None
                    else "Not recorded"
                ),
                "Classifier confidence": "Not available",
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
            edit_resolution_summary = st.text_area(
                "Resolution summary (required when resolving)",
                max_chars=4000,
                key=f"edit_resolution_{case_key}",
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
        if edit_review_status == "Resolved" and current_review_status != "Resolved":
            try:
                validate_resolution_summary(edit_review_status, edit_resolution_summary)
            except ValueError as error:
                st.error(str(error))
                return
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
                    previous = connection.execute(
                        "SELECT review_status, permit_status FROM cases WHERE case_id = ?",
                        (selected_id,),
                    ).fetchone()
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
                        old_status = text_value(previous["review_status"]) or "Pending Review"
                        if old_status != edit_review_status:
                            record_audit_event(
                                connection,
                                "construction",
                                selected_id,
                                "case_reopened" if old_status == "Resolved" and edit_review_status != "Resolved" else (
                                    "case_resolved" if edit_review_status == "Resolved" else "review_status_changed"
                                ),
                                old_status=old_status,
                                new_status=edit_review_status,
                                resolution_summary=edit_resolution_summary if edit_review_status == "Resolved" else None,
                            )
                        if text_value(previous["permit_status"]) != edit_permit_status:
                            record_audit_event(
                                connection,
                                "construction",
                                selected_id,
                                "recorded_permit_status_changed",
                                note=f"Recorded status changed from {previous['permit_status']} to {edit_permit_status}.",
                            )
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


def _detection_fingerprint(metadata, earlier_jpeg, newer_jpeg):
    """Identify the property and encoded image pair, independent of form dates."""
    digest = hashlib.sha256()
    property_reference = (
        metadata["building_reference"].strip().casefold()
        or metadata["monitoring_area"].strip().casefold()
    )
    for value in (
        property_reference,
        str(metadata["ward_number"] or ""),
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(hashlib.sha256(earlier_jpeg).digest())
    digest.update(hashlib.sha256(newer_jpeg).digest())
    digest.update(text_value(metadata.get("detection_method") or DETECTION_METHOD).encode("utf-8"))
    return digest.hexdigest()


def _encode_evidence_image(rgb_image):
    success, encoded = cv2.imencode(
        ".jpg",
        cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, 94],
    )
    if not success:
        raise OSError("The image evidence could not be encoded.")
    return encoded.tobytes()


def _evidence_path_from_reference(reference):
    relative_path = Path(text_value(reference))
    if len(relative_path.parts) != 2 or relative_path.parts[0] != "change_images":
        return None
    evidence_path = IMAGE_EVIDENCE_DIRECTORY / relative_path.name
    if evidence_path.resolve().parent != IMAGE_EVIDENCE_DIRECTORY.resolve():
        return None
    return evidence_path


def _same_property(metadata, detection):
    property_key = (
        metadata["building_reference"].strip().casefold()
        or metadata["monitoring_area"].strip().casefold()
    )
    saved_property_key = (
        text_value(detection["building_reference"]).casefold()
        or text_value(detection["monitoring_area"]).casefold()
    )
    return (
        property_key == saved_property_key
        and metadata["ward_number"] == detection["ward_number"]
    )


def _find_duplicate_image_pair(metadata, earlier_jpeg, newer_jpeg):
    earlier_hash = hashlib.sha256(earlier_jpeg).digest()
    newer_hash = hashlib.sha256(newer_jpeg).digest()
    for detection in load_image_detections():
        if not _same_property(metadata, detection):
            continue
        earlier_path = _evidence_path_from_reference(detection["earlier_image_ref"])
        newer_path = _evidence_path_from_reference(detection["newer_image_ref"])
        if not earlier_path or not newer_path or not earlier_path.is_file() or not newer_path.is_file():
            continue
        if (
            hashlib.sha256(earlier_path.read_bytes()).digest() == earlier_hash
            and hashlib.sha256(newer_path.read_bytes()).digest() == newer_hash
        ):
            return detection
    return None


def _save_evidence_images(earlier_jpeg, newer_jpeg):
    """Store reviewed source images under random, app-relative filenames."""
    IMAGE_EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    references = []
    absolute_paths = []
    try:
        for label, encoded_image in (
            ("earlier", earlier_jpeg),
            ("newer", newer_jpeg),
        ):
            filename = f"{token}_{label}.jpg"
            absolute_path = IMAGE_EVIDENCE_DIRECTORY / filename
            with absolute_path.open("xb") as image_file:
                image_file.write(encoded_image)
            references.append(f"change_images/{filename}")
            absolute_paths.append(absolute_path)
        return references, absolute_paths
    except Exception:
        for path in absolute_paths:
            path.unlink(missing_ok=True)
        raise


def save_image_detection(metadata, comparison):
    """Persist an officer-confirmed change and its evidence exactly once."""
    earlier_jpeg = _encode_evidence_image(comparison.earlier_rgb)
    newer_jpeg = _encode_evidence_image(comparison.newer_rgb)
    comparison_key = text_value(metadata.get("comparison_key")) or None
    if comparison_key:
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT case_id FROM image_change_detections WHERE comparison_key = ?",
                (comparison_key,),
            ).fetchone()
        if existing:
            return existing["case_id"], False
    fingerprint = _detection_fingerprint(metadata, earlier_jpeg, newer_jpeg)
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT case_id FROM image_change_detections WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if existing:
            return existing["case_id"], False

    matching_pair = None if comparison_key else _find_duplicate_image_pair(
        metadata, earlier_jpeg, newer_jpeg
    )
    if matching_pair:
        return matching_pair["case_id"], False

    case_id = f"IMG-{fingerprint[:12].upper()}"
    with get_connection() as connection:
        collision = connection.execute(
            "SELECT 1 FROM cases WHERE case_id = ? UNION ALL "
            "SELECT 1 FROM image_change_detections WHERE case_id = ? LIMIT 1",
            (case_id, case_id),
        ).fetchone()
    if collision:
        case_id = f"IMG-{fingerprint[:12].upper()}-{uuid.uuid4().hex[:8].upper()}"
    references, absolute_paths = _save_evidence_images(earlier_jpeg, newer_jpeg)
    category = metadata["change_category"]
    floor_status = metadata["floor_verification_status"]
    recent_floors = metadata["recent_observed_floors"]
    approved_floors = metadata["approved_floor_count"]
    verified_floor_mismatch = (
        floor_status == "Verified"
        and recent_floors is not None
        and approved_floors is not None
        and recent_floors != approved_floors
    )
    analysis_status = (
        "No discrepancy found"
        if category == "No significant change detected" and not verified_floor_mismatch
        else "Pending officer verification"
    )
    reason = (
        "No significant change passed pixel screening"
        if category == "No significant change detected"
        else IMAGE_CHANGE_REASON
    )
    if verified_floor_mismatch:
        reason += (
            f"; manually recorded floor discrepancy: {recent_floors} observed "
            f"versus {approved_floors} approved (source: "
            f"{metadata['floor_evidence_source'].strip()})"
        )
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO image_change_detections (
                    case_id, fingerprint, monitoring_area, ward_number,
                    building_reference, earlier_image_date, newer_image_date,
                    detection_date, change_percentage, detection_method, reason,
                    permit_status, review_status, earlier_image_ref, newer_image_ref,
                    change_category, analysis_status, earlier_observed_floors,
                    recent_observed_floors, approved_floor_count,
                    floor_evidence_source, floor_verification_status,
                    comparison_key, imagery_source, property_location_id,
                    latitude, longitude, earlier_scene_id, newer_scene_id,
                    valid_pixel_percentage, cloud_percentage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    metadata.get("detection_method") or DETECTION_METHOD,
                    reason,
                    "Not verified",
                    "Pending Review",
                    references[0],
                    references[1],
                    category,
                    analysis_status,
                    metadata["earlier_observed_floors"],
                    recent_floors,
                    approved_floors,
                    metadata["floor_evidence_source"].strip() or None,
                    floor_status,
                    comparison_key,
                    metadata.get("imagery_source") or "Manual upload",
                    metadata.get("property_location_id"),
                    metadata.get("latitude"),
                    metadata.get("longitude"),
                    metadata.get("earlier_scene_id"),
                    metadata.get("newer_scene_id"),
                    metadata.get("valid_pixel_percentage"),
                    metadata.get("cloud_percentage"),
                ),
            )
            record_audit_event(
                connection,
                "image_detection",
                case_id,
                "case_created",
                new_status="Pending Review",
                note="Potential construction change saved for officer review.",
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
            if not existing and comparison_key:
                existing = connection.execute(
                    "SELECT case_id FROM image_change_detections WHERE comparison_key = ?",
                    (comparison_key,),
                ).fetchone()
        if existing:
            return existing["case_id"], False
        raise
    except Exception:
        for path in absolute_paths:
            path.unlink(missing_ok=True)
        raise


def render_historical_imagery():
    st.header("Historical Imagery")
    st.caption("Optional Sentinel-2 L2A coarse screening. Every result requires officer verification.")
    st.warning(
        "Sentinel-2 is about 10 m per pixel at best; a typical house is roughly one pixel. "
        "Use this only as coarse screening for large clearings or large structures. The score "
        "is not the percentage of new construction, and this is not a trained AI model."
    )
    st.info("The base map is separate from this page. Retrieved imagery is not guaranteed current; historical availability and coverage vary.")
    locations = load_property_locations()
    if not locations:
        st.warning("No registered property coordinates are available. Record an officer-selected location on GPS Property Map first.")
        st.caption("If no scene is available or provider access fails, use the existing Automatic Change Detection manual upload workflow.")
        return
    by_id = {int(row["location_id"]): row for row in locations}
    location_id = st.selectbox(
        "Registered property", list(by_id),
        format_func=lambda key: f"{by_id[key]['property_reference']} — {by_id[key]['monitoring_area']} (Ward {by_id[key]['ward_number'] or 'not recorded'})",
    )
    location = by_id[location_id]
    st.write(f"**Coordinates:** {location['latitude']:.6f}, {location['longitude']:.6f}")
    if text_value(location["verification_status"]) == "Verified":
        st.success(f"Location marked Verified. Source: {location['verification_evidence'] or 'not recorded'}")
    else:
        st.warning("This location is unverified. Check it against an authorised source before interpreting imagery.")
    st.caption("Imagery lookup covers approximately 500 m × 500 m around these recorded coordinates. Coordinates are not changed here.")

    with st.form("historical_imagery_search"):
        earlier_target = st.date_input("Earlier target date", value=date(2020, 1, 1), key="history_earlier_target")
        newer_target = st.date_input("Newer target date", value=date(2025, 1, 1), key="history_newer_target")
        tolerance = st.slider("Search window around each target date (days)", 7, 90, 45)
        max_cloud = st.slider("Maximum listed cloud cover (%)", 0, 100, 30)
        submitted = st.form_submit_button("Search Sentinel-2 scenes", type="primary")
    if submitted:
        st.session_state.pop("historical_scene_search", None)
        if earlier_target >= newer_target:
            st.error("The earlier target date must be before the newer target date.")
        else:
            found = {}
            provider = Element84SentinelProvider()
            for label, target in (("earlier", earlier_target), ("newer", newer_target)):
                start = max(date(2015, 6, 23), date.fromordinal(target.toordinal() - tolerance))
                end = date.fromordinal(target.toordinal() + tolerance)
                try:
                    found[label] = provider.search(location["latitude"], location["longitude"], start, end, max_cloud)
                except NoImageryFound as error:
                    for radius in (45, 90, 180, 365):
                        try:
                            broad_start = max(date(2015, 6, 23), date.fromordinal(target.toordinal() - radius))
                            broad_end = date.fromordinal(target.toordinal() + radius)
                            nearest = provider.search(location["latitude"], location["longitude"], broad_start, broad_end, 100)
                            error.nearest_dates = [scene.acquisition_date.isoformat() for scene in
                                                   sorted(nearest, key=lambda scene: abs((scene.acquisition_date - target).days))[:8]]
                            break
                        except NoImageryFound:
                            continue
                        except ImageryProviderError:
                            break
                    found[label] = error
                except (AuthenticationMissing, RateLimited, ProviderError) as error:
                    found[label] = error
            st.session_state["historical_scene_search"] = {"location_id": location_id, "results": found}

    search = st.session_state.get("historical_scene_search")
    if search and search["location_id"] == location_id:
        found = search["results"]
        for label in ("earlier", "newer"):
            result = found.get(label)
            if isinstance(result, Exception):
                st.error(f"{label.capitalize()} image search: {result}")
                if isinstance(result, NoImageryFound) and result.nearest_dates:
                    st.caption("Nearest available dates: " + ", ".join(result.nearest_dates))
        if all(isinstance(found.get(label), list) and found[label] for label in ("earlier", "newer")):
            chosen = {}
            for label in ("earlier", "newer"):
                scenes = found[label]
                labels = {f"{scene.acquisition_date} | {scene.scene_id} | cloud {scene.cloud_cover_pct if scene.cloud_cover_pct is not None else 'unknown'}%": scene for scene in scenes}
                picked = st.selectbox(f"{label.capitalize()} scene", list(labels), key=f"history_{label}_scene")
                chosen[label] = labels[picked]
                scene = chosen[label]
                st.caption(f"{scene.source} · {scene.resolution_m or 'unknown'} m · cloud {scene.cloud_cover_pct if scene.cloud_cover_pct is not None else 'unknown'}% · {scene.attribution} · {scene.license}")
            if st.button("Download small AOIs and compare", type="primary", key="history_run_comparison"):
                try:
                    with st.spinner("Reading small public COG windows and comparing the common area…"):
                        provider = Element84SentinelProvider()
                        old_geo = provider.download_aoi(chosen["earlier"], location["latitude"], location["longitude"], 500)
                        new_geo = provider.download_aoi(chosen["newer"], location["latitude"], location["longitude"], 500)
                        result = compare_geographic_images(old_geo, new_geo)
                    old_rgb = np.clip(result.earlier_rgb * 255, 0, 255).astype(np.uint8)
                    new_rgb = np.clip(result.newer_rgb * 255, 0, 255).astype(np.uint8)
                    comparison = SimpleNamespace(
                        earlier_rgb=old_rgb, newer_rgb=new_rgb, aligned_newer_rgb=new_rgb,
                        mask=result.mask, highlighted_rgb=result.highlighted_rgb,
                        changed_pixel_percentage=result.changed_pixel_percentage,
                        alignment_note="; ".join(result.warnings) or "Common geographic grid.",
                    )
                    method = result.method
                    key_raw = (
                        f"{location_id}|{old_geo.acquisition_date.isoformat()}|{chosen['earlier'].scene_id}|"
                        f"{new_geo.acquisition_date.isoformat()}|{chosen['newer'].scene_id}|{method}"
                    )
                    metadata = {
                        "monitoring_area": location["monitoring_area"], "ward_number": location["ward_number"],
                        "building_reference": location["property_reference"],
                        "earlier_image_date": old_geo.acquisition_date, "newer_image_date": new_geo.acquisition_date,
                        "change_category": "Unable to determine reliably",
                        "earlier_observed_floors": None, "recent_observed_floors": None,
                        "approved_floor_count": None, "floor_evidence_source": "",
                        "floor_verification_status": "Not verified", "detection_method": method,
                        "imagery_source": "Sentinel-2 L2A via Element84 Earth Search",
                        "property_location_id": location_id, "latitude": location["latitude"], "longitude": location["longitude"],
                        "earlier_scene_id": old_geo.scene_id, "newer_scene_id": new_geo.scene_id,
                        "valid_pixel_percentage": result.valid_pixel_percentage, "cloud_percentage": result.cloud_percentage,
                        "comparison_key": hashlib.sha256(key_raw.encode()).hexdigest(),
                        "historical_warnings": result.warnings,
                    }
                    token = hashlib.sha256((key_raw + str(result.changed_pixel_percentage)).encode()).hexdigest()[:12]
                    st.session_state["historical_comparison_preview"] = {"metadata": metadata, "comparison": comparison, "result": result, "scenes": chosen, "token": token}
                except (ImageryProviderError, ValueError, OSError, cv2.error) as error:
                    st.error(f"Historical comparison could not be completed: {error}")

    preview = st.session_state.get("historical_comparison_preview")
    if preview and preview["metadata"].get("property_location_id") == location_id:
        meta, comparison, result = preview["metadata"], preview["comparison"], preview["result"]
        st.subheader("Potential Construction Change - Verification Required")
        st.caption(f"Property: {location['property_reference']} · {location['latitude']:.6f}, {location['longitude']:.6f} · Permit status not verified")
        cols = st.columns(3)
        scenes = (preview["scenes"]["earlier"], preview["scenes"]["newer"], preview["scenes"]["newer"])
        for col, label, image, scene in zip(cols, ("Earlier image", "Newer image", "Highlighted change mask"),
                                              (comparison.earlier_rgb, comparison.newer_rgb, comparison.highlighted_rgb), scenes):
            with col:
                st.markdown(f"**{label}**")
                st.image(image, width="stretch")
                if label != "Highlighted change mask":
                    st.caption(f"{scene.acquisition_date} · {scene.source} · {scene.resolution_m or 'unknown'} m · cloud {scene.cloud_cover_pct if scene.cloud_cover_pct is not None else 'unknown'}%")
        m1, m2, m3 = st.columns(3)
        m1.metric("Usable pixels marked different", f"{result.changed_pixel_percentage:.2f}%")
        m2.metric("Valid pixel coverage", f"{result.valid_pixel_percentage:.1f}%")
        m3.metric("Cloud/shadow/snow mask", f"{result.cloud_percentage:.1f}%" if result.cloud_percentage is not None else "Unavailable")
        for warning in result.warnings:
            st.warning(warning)
        st.warning("The score is not the percentage of new construction and does not establish a permit discrepancy.")
        if st.button("Send to Automatic Change Detection for officer review", type="primary", key="history_send_to_review"):
            st.session_state["automatic_change_preview"] = preview
            st.session_state["navigation"] = "Automatic Change Detection"
            st.rerun()
    st.divider()
    st.caption("Fallback: existing manual-upload comparison remains available when no suitable public scenes can be retrieved.")
    if st.button("Open manual upload comparison", key="history_manual_fallback"):
        st.session_state["navigation"] = "Automatic Change Detection"
        st.rerun()


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
                preview_token = hashlib.sha256(
                    comparison.earlier_rgb.tobytes()
                    + comparison.newer_rgb.tobytes()
                ).hexdigest()[:12]
                st.session_state["automatic_change_preview"] = {
                    "metadata": {
                        "monitoring_area": clean_area,
                        "ward_number": ward_number,
                        "building_reference": clean_reference,
                        "earlier_image_date": earlier_date,
                        "newer_image_date": newer_date,
                    },
                    "comparison": comparison,
                    "token": preview_token,
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
    metadata = preview["metadata"]
    token = preview["token"]
    st.subheader("Review detected differences")
    st.caption(comparison.alignment_note)
    st.caption(
        f"Monitoring area: {metadata['monitoring_area']} | Earlier image: "
        f"{metadata['earlier_image_date']} | Newer image: {metadata['newer_image_date']} | "
        f"Property reference: {metadata['building_reference'] or 'Not provided'}"
    )
    if "not reliable" in comparison.alignment_note.lower() or "unavailable" in comparison.alignment_note.lower():
        st.warning(
            "The images could not be aligned reliably. Camera movement or framing "
            "may affect the highlighted differences."
        )
    for note in metadata.get("historical_warnings", []):
        st.warning(note)
    if metadata.get("imagery_source"):
        st.info(
            "Potential Construction Change - Verification Required. Permit status not verified. "
            "The pixel-difference score is not the percentage of new construction; this is "
            "image differencing, not a trained AI model."
        )
    if comparison.earlier_rgb.shape[:2] != comparison.newer_rgb.shape[:2]:
        st.warning(
            "The source images had different dimensions; the newer image was "
            "resized for comparison. Fine details and the score may be affected."
        )
    if min(comparison.earlier_rgb.shape[:2]) < 256:
        st.warning(
            "At least one image is low resolution. Small building details may "
            "not be visible enough for reliable review."
        )
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
    st.subheader("Building Change Analysis")
    st.caption(
        "Preliminary visual-change screening only. Select the category you "
        "observe; this app has no semantic building classifier."
    )
    default_category = (
        "No significant change detected"
        if comparison.changed_pixel_percentage <= 0
        else "Unable to determine reliably"
    )
    change_category = st.selectbox(
        "Preliminary change category (officer assessment)",
        CHANGE_CATEGORIES,
        index=CHANGE_CATEGORIES.index(default_category),
        key=f"change_category_{token}",
    )
    st.caption("Classifier confidence: Not available; no classifier is installed or used.")

    st.markdown("**Manual floor-count observations**")
    st.caption(
        "Floor counts are entered by an officer. The image-difference score "
        "does not estimate the number of floors."
    )
    earlier_count_known = st.checkbox(
        "Earlier observed floor count is available",
        key=f"earlier_count_known_{token}",
    )
    earlier_observed_floors = (
        st.number_input(
            "Earlier observed floors",
            min_value=0,
            max_value=100,
            step=1,
            key=f"earlier_floors_{token}",
        )
        if earlier_count_known
        else None
    )
    recent_count_known = st.checkbox(
        "Recent observed floor count is available",
        key=f"recent_count_known_{token}",
    )
    recent_observed_floors = (
        st.number_input(
            "Recent observed floors",
            min_value=0,
            max_value=100,
            step=1,
            key=f"recent_floors_{token}",
        )
        if recent_count_known
        else None
    )
    approved_count_known = st.checkbox(
        "Approved floor count is available from a permit record",
        key=f"approved_count_known_{token}",
    )
    approved_floor_count = (
        st.number_input(
            "Approved floors",
            min_value=0,
            max_value=100,
            step=1,
            key=f"approved_floors_{token}",
        )
        if approved_count_known
        else None
    )
    floor_evidence_source = st.text_input(
        "Floor-count evidence source (permit/reference or inspection note)",
        max_chars=200,
        key=f"floor_evidence_source_{token}",
    )
    floor_verification_status = st.selectbox(
        "Floor-count verification status",
        FLOOR_VERIFICATION_STATUSES,
        key=f"floor_verification_{token}",
        help="Approved floors are compared only when this is Verified and an evidence source is supplied.",
    )

    if (
        floor_verification_status == "Verified"
        and approved_floor_count is not None
        and recent_observed_floors is not None
    ):
        if recent_observed_floors != approved_floor_count:
            st.warning(
                f"Manual recent count ({recent_observed_floors}) differs from "
                f"the entered approved count ({approved_floor_count}). This is "
                "a review discrepancy, not a finding of illegality."
            )
        else:
            st.info("The manually entered recent count matches the verified approved count.")

    st.warning(
        "Review and confirm these manually entered details. A visual change or "
        "floor-count discrepancy does not establish that construction is illegal."
    )
    save_label = (
        "Save analysis result"
        if change_category == "No significant change detected"
        else "Confirm and save as a review case"
    )
    if st.button(save_label, type="primary", key=f"save_analysis_{token}"):
        if (
            floor_verification_status == "Verified"
            and (approved_floor_count is None or not floor_evidence_source.strip())
        ):
            st.error(
                "To mark approved floors Verified, enter the approved count and "
                "its evidence source."
            )
            return
        try:
            metadata.update(
                {
                    "change_category": change_category,
                    "earlier_observed_floors": (
                        int(earlier_observed_floors)
                        if earlier_observed_floors is not None
                        else None
                    ),
                    "recent_observed_floors": (
                        int(recent_observed_floors)
                        if recent_observed_floors is not None
                        else None
                    ),
                    "approved_floor_count": (
                        int(approved_floor_count)
                        if approved_floor_count is not None
                        else None
                    ),
                    "floor_evidence_source": floor_evidence_source,
                    "floor_verification_status": floor_verification_status,
                }
            )
            case_id, created = save_image_detection(
                metadata, comparison
            )
            if created:
                st.session_state["automatic_change_saved_message"] = (
                    f"Analysis saved as case {case_id}. Check Officer Alert Management for its review status."
                )
            else:
                st.session_state["automatic_change_saved_message"] = (
                    f"This property and image pair are already recorded as {case_id}; no duplicate case was created."
                )
            st.session_state.pop("automatic_change_preview", None)
            st.rerun()
        except (sqlite3.Error, OSError, cv2.error) as error:
            st.error(f"The review case could not be saved: {error}")


def _render_associated_case(record_item):
    record = record_item["record"]
    st.markdown(f"**{record_item['case_id']}**")
    if record_item["case_type"] == "construction":
        details = {
            "Building reference": record["building_reference"] or "Not recorded",
            "Ward": record["ward_number"] if record["ward_number"] is not None else "Not recorded",
            "Site address": record["site_address"] or "Not recorded",
            "Inspection date": record["inspection_date"] or "Not recorded",
            "Review status": get_review_status(record),
        }
    else:
        details = {
            "Monitoring area": record["monitoring_area"] or "Not recorded",
            "Building reference": record["building_reference"] or "Not recorded",
            "Ward": record["ward_number"] if record["ward_number"] is not None else "Not recorded",
            "Image dates": (
                f"{record['earlier_image_date']} to {record['newer_image_date']}"
            ),
            "Analysis status": record["analysis_status"] or "Not recorded",
            "Review status": get_review_status(record),
        }
    st.json(details)


def render_gps_property_map():
    st.header("GPS Property Map")
    st.write(
        "Search for a public place or landmark, or click the map to select the "
        "property point yourself. Coordinates are saved only after officer review."
    )
    st.info(
        "The map uses OpenStreetMap street-map tiles. This is not a satellite "
        "or historical imagery feed, and map imagery may not be current."
    )
    st.caption(
        "Map tiles © OpenStreetMap contributors. Location search is sent to "
        "OpenStreetMap Nominatim; use a public place or landmark, not private "
        "personal information. Search and map tiles require an internet connection."
    )

    with st.form("gps_location_search"):
        search_query = st.text_input(
            "Search location (public place or landmark)", max_chars=200
        )
        search_submitted = st.form_submit_button("Search map")
    if search_submitted:
        query = search_query.strip()
        if not query:
            st.error("Enter a public place or landmark to search for.")
        else:
            try:
                matches = search_public_location(query)
                st.session_state["gps_location_search_results"] = matches
                if not matches:
                    st.info("No matching public place was found. You can click the map manually.")
            except requests.RequestException as error:
                st.session_state["gps_location_search_results"] = []
                st.error(f"Location search is unavailable: {error}")
            except (ValueError, TypeError) as error:
                st.session_state["gps_location_search_results"] = []
                st.error(f"Location results could not be read: {error}")

    search_results = st.session_state.get("gps_location_search_results", [])
    if search_results:
        result_labels = [item["label"] for item in search_results]
        chosen_result = st.selectbox("Search results", result_labels)
        if st.button("Center map on selected result"):
            item = search_results[result_labels.index(chosen_result)]
            set_selected_map_coordinates((item["latitude"], item["longitude"]))
            st.session_state["gps_map_revision"] = (
                st.session_state.get("gps_map_revision", 0) + 1
            )
            st.rerun()

    selected_coordinates = st.session_state.get("gps_selected_coordinates")
    locations = load_property_locations()
    if selected_coordinates:
        center = selected_coordinates
        zoom = 17
    elif locations:
        center = (
            sum(row["latitude"] for row in locations) / len(locations),
            sum(row["longitude"] for row in locations) / len(locations),
        )
        zoom = 11
    else:
        # A neutral world view, not an assumed Panchayat/property coordinate.
        center, zoom = (0.0, 0.0), 2

    property_map = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    for location in locations:
        property_ref = html.escape(location["property_reference"] or "Property")
        case_text = (
            f"<br>Linked case: {html.escape(location['associated_case_id'])}"
            if location["associated_case_id"]
            else "<br>No case linked"
        )
        popup_html = (
            f"<b>{property_ref}</b><br>Ward: "
            f"{html.escape(str(location['ward_number'] or 'Not recorded'))}"
            f"{case_text}"
        )
        folium.Marker(
            location=(location["latitude"], location["longitude"]),
            tooltip=property_ref,
            popup=folium.Popup(popup_html, max_width=320),
            icon=folium.Icon(color="blue", icon="home", prefix="fa"),
        ).add_to(property_map)
    if selected_coordinates:
        folium.Marker(
            location=selected_coordinates,
            tooltip="Selected property point (not saved yet)",
            popup="Officer-selected point — save only after verifying the location.",
            icon=folium.Icon(color="red", icon="map-marker", prefix="fa"),
        ).add_to(property_map)

    st.markdown("**Click the map to choose a property point.**")
    map_revision = st.session_state.get("gps_map_revision", 0)
    map_result = st_folium(
        property_map,
        height=500,
        use_container_width=True,
        key=f"gps_property_map_{map_revision}",
        returned_objects=["last_clicked"],
    )
    try:
        clicked_coordinates = coordinates_from_map_result(map_result)
        if (
            clicked_coordinates is not None
            and clicked_coordinates
            != st.session_state.get("gps_selected_coordinates")
        ):
            set_selected_map_coordinates(clicked_coordinates)
            st.rerun()
    except ValueError:
        st.warning("The map returned invalid coordinates; select another point.")

    selected_coordinates = st.session_state.get("gps_selected_coordinates")
    if selected_coordinates:
        st.success(
            f"Selected point: latitude {selected_coordinates[0]:.6f}, "
            f"longitude {selected_coordinates[1]:.6f}"
        )
    else:
        st.caption("No point selected. Search or click the map; no coordinates are assumed.")

    case_records = load_mappable_case_records()
    case_by_key = {
        f"{item['case_type']}:{item['case_id']}": item for item in case_records
    }
    choices = {"none": "No associated case"}
    for key, item in case_by_key.items():
        choices[key] = item["label"]

    if "gps_latitude_input" not in st.session_state:
        st.session_state["gps_latitude_input"] = (
            selected_coordinates[0] if selected_coordinates else None
        )
    if "gps_longitude_input" not in st.session_state:
        st.session_state["gps_longitude_input"] = (
            selected_coordinates[1] if selected_coordinates else None
        )

    with st.form("save_gps_property_location"):
        st.subheader("Record this property location")
        property_reference = st.text_input(
            "Property reference *", max_chars=120
        )
        monitoring_area = st.text_input("Monitoring-area name *", max_chars=120)
        ward_text = st.text_input("Ward number (optional)", max_chars=8)
        coordinate_column, longitude_column = st.columns(2)
        with coordinate_column:
            latitude_value = st.number_input(
                "Latitude *",
                min_value=-90.0,
                max_value=90.0,
                step=0.000001,
                format="%.6f",
                key="gps_latitude_input",
            )
        with longitude_column:
            longitude_value = st.number_input(
                "Longitude *",
                min_value=-180.0,
                max_value=180.0,
                step=0.000001,
                format="%.6f",
                key="gps_longitude_input",
            )
        associated_key = st.selectbox(
            "Associate an existing case (optional)",
            list(choices),
            format_func=lambda value: choices[value],
        )
        verification_status = st.selectbox(
            "Location verification", ("Unverified", "Verified"),
            help="Choose Verified only when coordinates were checked against an authorised source.",
        )
        verification_evidence = st.text_input(
            "Verification source (required if Verified)", max_chars=240
        )
        save_submitted = st.form_submit_button("Save property location", type="primary")

    if save_submitted:
        clean_reference = property_reference.strip()
        clean_area = monitoring_area.strip()
        errors = []
        if not clean_reference:
            errors.append("Property reference is required.")
        if not clean_area:
            errors.append("Monitoring-area name is required.")
        ward_number = None
        if ward_text.strip():
            try:
                ward_number = int(ward_text.strip())
                if not 1 <= ward_number <= 1000:
                    raise ValueError
            except ValueError:
                errors.append("Ward number must be a whole number from 1 to 1000.")
        try:
            latitude_value, longitude_value = validate_coordinates(
                latitude_value, longitude_value
            )
        except ValueError as error:
            errors.append(str(error))
        selected_case = case_by_key.get(associated_key)
        if associated_key != "none" and selected_case is None:
            errors.append("Select a valid existing case or leave the case unassociated.")

        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                save_property_location(
                    clean_reference,
                    latitude_value,
                    longitude_value,
                    ward_number,
                    clean_area,
                    selected_case["case_id"] if selected_case else None,
                    selected_case["case_type"] if selected_case else None,
                    verification_status,
                    verification_evidence,
                )
                st.session_state["gps_selected_coordinates"] = (
                    latitude_value,
                    longitude_value,
                )
                st.success(f"Property location for {clean_reference} was saved.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error(
                    "That property reference already has a saved map location. "
                    "No existing location was overwritten."
                )
            except ValueError as error:
                st.error(str(error))
            except sqlite3.Error as error:
                st.error(f"The property location could not be saved: {error}")

    st.divider()
    st.subheader("Saved property locations")
    locations = load_property_locations()
    if locations:
        st.dataframe(
            [
                {
                    "Property reference": row["property_reference"],
                    "Ward": row["ward_number"] or "Not recorded",
                    "Monitoring area": row["monitoring_area"],
                    "Latitude": f"{row['latitude']:.6f}",
                    "Longitude": f"{row['longitude']:.6f}",
                    "Location verification": row["verification_status"] or "Unverified",
                    "Associated case": row["associated_case_id"] or "Not linked",
                }
                for row in locations
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No property locations have been recorded yet.")

    linked_keys = {
        (row["associated_case_type"], row["associated_case_id"])
        for row in locations
        if row["associated_case_id"]
    }
    linked_records = [
        item
        for item in case_records
        if (item["case_type"], item["case_id"]) in linked_keys
    ]
    if linked_records:
        with st.expander("Open an associated case"):
            linked_labels = {item["label"]: item for item in linked_records}
            selected_label = st.selectbox(
                "Linked case", list(linked_labels), key="gps_open_linked_case"
            )
            _render_associated_case(linked_labels[selected_label])

    mapped_keys = {
        (row["associated_case_type"], row["associated_case_id"])
        for row in locations
        if row["associated_case_id"]
    }
    unmapped_records = [
        item
        for item in case_records
        if (item["case_type"], item["case_id"]) not in mapped_keys
    ]
    st.subheader("Existing cases without mapped coordinates")
    if unmapped_records:
        st.caption("These records remain unmapped; no coordinates have been inferred for them.")
        st.dataframe(
            [
                {
                    "Case ID": item["case_id"],
                    "Record type": (
                        "Construction case"
                        if item["case_type"] == "construction"
                        else "Image-change review"
                    ),
                    "Property reference": item["record"]["building_reference"]
                    or item["record"].get("monitoring_area", "Not recorded"),
                    "Ward": item["record"]["ward_number"] or "Not recorded",
                }
                for item in unmapped_records
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.success("All existing case records have an associated mapped location.")


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
        "GPS Property Map",
        "Historical Imagery",
    ),
    key="navigation",
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
elif page == "GPS Property Map":
    render_gps_property_map()
elif page == "Historical Imagery":
    render_historical_imagery()
else:
    render_image_comparison()

st.divider()
st.caption(
    "Prototype only. It does not determine legality or issue legal notices. "
    "Use authorized records and human inspection for decisions."
)
