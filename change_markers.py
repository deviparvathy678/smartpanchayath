"""Geospatial change-region markers and additive SQLite persistence.

Markers describe connected regions in an image-difference mask. They are not
building outlines or measurements of construction.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
import sqlite3

import cv2
import numpy as np


@dataclass(frozen=True)
class ChangeMarker:
    marker_key: str
    number: int
    pixel_x: float
    pixel_y: float
    pixel_radius: float
    region_pixels: int
    latitude: float | None
    longitude: float | None
    radius_m: float | None
    area_sqm: float | None
    warnings: tuple[str, ...] = ()

    @property
    def georeferenced(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _utm_crs(lon: float, lat: float) -> str:
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _to_wgs84(crs, xs, ys):
    from rasterio.warp import transform
    lons, lats = transform(crs, "EPSG:4326", list(xs), list(ys))
    return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)


def _metric_points(crs, xs, ys, lon, lat):
    from rasterio.warp import transform
    local = _utm_crs(lon, lat)
    mx, my = transform(crs, local, list(xs), list(ys))
    return np.asarray(mx, dtype=float), np.asarray(my, dtype=float)


def _polygon_area(points):
    x, y = points[:, 0], points[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2


def _audit_target(connection, case_id, fallback_key):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if case_id and "cases" in tables and connection.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone():
        return "construction", case_id
    if case_id and "image_change_detections" in tables and connection.execute("SELECT 1 FROM image_change_detections WHERE case_id = ?", (case_id,)).fetchone():
        return "image_detection", case_id
    return "image_detection", case_id or fallback_key


def _pixel_area_sqm(transform, crs, col, row, lon, lat):
    corners = [transform @ (col, row), transform @ (col + 1, row),
               transform @ (col + 1, row + 1), transform @ (col, row + 1)]
    mx, my = _metric_points(crs, [p[0] for p in corners], [p[1] for p in corners], lon, lat)
    return _polygon_area(np.column_stack((mx, my)))


def _circle_radius_m(transform, crs, x, y, radius_px, lon, lat):
    center = transform @ (x, y)
    cardinal = [transform @ (x + radius_px, y), transform @ (x - radius_px, y),
                transform @ (x, y + radius_px), transform @ (x, y - radius_px)]
    mx, my = _metric_points(crs, [center[0], *[p[0] for p in cardinal]],
                            [center[1], *[p[1] for p in cardinal]], lon, lat)
    return float(max(math.hypot(mx[i] - mx[0], my[i] - my[0]) for i in range(1, 5)))


def extract_change_markers(
    mask,
    *,
    comparison_key: str,
    minimum_region_pixels: int = 64,
    transform=None,
    crs=None,
    image_bounds_wgs84: tuple[float, float, float, float] | None = None,
    property_point: tuple[float, float] | None = None,
    far_distance_m: float = 100.0,
) -> list[ChangeMarker]:
    """Cluster a filtered mask and map region circles when CRS and transform exist."""
    if not str(comparison_key).strip():
        raise ValueError("A deterministic comparison key is required.")
    if minimum_region_pixels < 1:
        raise ValueError("Minimum region area must be at least one pixel.")
    binary = np.asarray(mask)
    if binary.ndim != 2 or binary.size == 0:
        raise ValueError("Change mask must be a non-empty two-dimensional array.")
    binary = np.where(binary > 0, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    components = [i for i in range(1, count) if int(stats[i, cv2.CC_STAT_AREA]) >= minimum_region_pixels]
    components.sort(key=lambda i: (int(stats[i, cv2.CC_STAT_TOP]), int(stats[i, cv2.CC_STAT_LEFT])))
    georeferenced = transform is not None and crs is not None
    markers = []
    for number, label in enumerate(components, 1):
        ys, xs = np.where(labels == label)
        points = np.column_stack((xs.astype(np.float32) + 0.5, ys.astype(np.float32) + 0.5))
        (x, y), radius_px = cv2.minEnclosingCircle(points)
        radius_px = max(float(radius_px), 0.5)
        component_pixels = np.column_stack((xs, ys)).astype(np.int32, copy=False)
        marker_digest = hashlib.sha256(component_pixels.tobytes()).hexdigest()[:20]
        key = hashlib.sha256(f"{comparison_key}:{marker_digest}".encode("utf-8")).hexdigest()
        lat = lon = radius_m = area_sqm = None
        warnings = []
        if georeferenced:
            from rasterio.crs import CRS
            source_crs = CRS.from_user_input(crs)
            px, py = transform @ (float(x), float(y))
            lons, lats = _to_wgs84(source_crs, [px], [py])
            lon, lat = float(lons[0]), float(lats[0])
            radius_m = _circle_radius_m(transform, source_crs, float(x), float(y), radius_px, lon, lat)
            area_sqm = int(stats[label, cv2.CC_STAT_AREA]) * _pixel_area_sqm(
                transform, source_crs, float(x), float(y), lon, lat)
            if image_bounds_wgs84:
                west, south, east, north = image_bounds_wgs84
                if not (west <= lon <= east and south <= lat <= north):
                    warnings.append("Marker centre falls outside the image bounds; verify its georeferencing.")
            if property_point:
                prop_lat, prop_lon = property_point
                from rasterio.warp import transform as project
                xs_m, ys_m = project("EPSG:4326", _utm_crs(lon, lat), [prop_lon, lon], [prop_lat, lat])
                if math.hypot(xs_m[1] - xs_m[0], ys_m[1] - ys_m[0]) > far_distance_m:
                    warnings.append("Marker is far from the registered property point; verify the property association.")
        markers.append(ChangeMarker(key, number, float(x), float(y), radius_px,
                                    int(stats[label, cv2.CC_STAT_AREA]), lat, lon,
                                    radius_m, area_sqm, tuple(warnings)))
    return markers


def draw_change_markers(after_rgb, markers: list[ChangeMarker]) -> np.ndarray:
    """Draw numbered red circle outlines on a copy of an RGB image."""
    image = np.asarray(after_rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Image must be RGB.")
    output = image.copy()
    for marker in markers:
        center = (round(marker.pixel_x), round(marker.pixel_y))
        cv2.circle(output, center, max(2, round(marker.pixel_radius)), (255, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(output, str(marker.number), (center[0] + 3, center[1] - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1, cv2.LINE_AA)
    return output


def validate_marker_location(marker: ChangeMarker, bounds_wgs84=None) -> tuple[str, ...]:
    warnings = list(marker.warnings)
    if marker.georeferenced and bounds_wgs84:
        west, south, east, north = bounds_wgs84
        if not (west <= marker.longitude <= east and south <= marker.latitude <= north):
            warnings.append("Marker centre falls outside the supplied image bounds; verify its georeferencing.")
    return tuple(dict.fromkeys(warnings))


def ensure_surveillance_schema(connection: sqlite3.Connection) -> None:
    """Create additive marker/task tables. Safe to run repeatedly in a transaction."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS change_markers (
            marker_key TEXT PRIMARY KEY,
            comparison_key TEXT NOT NULL,
            property_location_id INTEGER,
            case_id TEXT,
            marker_number INTEGER NOT NULL,
            pixel_x REAL NOT NULL,
            pixel_y REAL NOT NULL,
            pixel_radius REAL NOT NULL,
            region_pixels INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            radius_m REAL,
            area_sqm REAL,
            method TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            quality_report_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            review_status TEXT NOT NULL DEFAULT 'Uncertain',
            review_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS ix_change_markers_property ON change_markers(property_location_id, created_at)")
    marker_columns = {row[1] for row in connection.execute("PRAGMA table_info(change_markers)")}
    if "warnings_json" not in marker_columns:
        connection.execute("ALTER TABLE change_markers ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS property_imagery_pairs (
            comparison_key TEXT PRIMARY KEY,
            property_location_id INTEGER NOT NULL,
            case_id TEXT,
            before_date TEXT NOT NULL,
            after_date TEXT NOT NULL,
            method TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            quality_report_json TEXT,
            parameters_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS ix_property_imagery_property ON property_imagery_pairs(property_location_id, created_at)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS surveillance_tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            marker_key TEXT NOT NULL UNIQUE,
            property_location_id INTEGER NOT NULL,
            case_id TEXT,
            target_latitude REAL NOT NULL,
            target_longitude REAL NOT NULL,
            buffer_radius_m REAL NOT NULL,
            reason TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            risk_priority TEXT,
            assigned_to TEXT,
            schedule_type TEXT NOT NULL DEFAULT 'Weekly',
            suggested_altitude_m REAL,
            suggested_overlap_pct REAL,
            permission_reference TEXT,
            task_status TEXT NOT NULL DEFAULT 'Ready for pilot hand-off',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(marker_key) REFERENCES change_markers(marker_key),
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id)
        )
    """)
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_surveillance_task_property_point
        ON surveillance_tasks(property_location_id, ROUND(target_latitude, 6), ROUND(target_longitude, 6))
    """)
    task_columns = {row[1] for row in connection.execute("PRAGMA table_info(surveillance_tasks)")}
    for name, declaration in (
        ("suggested_altitude_m", "REAL"),
        ("suggested_overlap_pct", "REAL"),
        ("task_dedupe_key", "TEXT"),
        ("task_purpose", "TEXT NOT NULL DEFAULT 'Targeted potential change review'"),
        ("location_source", "TEXT NOT NULL DEFAULT 'Georeferenced marker'"),
        ("location_uncertainty", "TEXT"),
        ("marker_number", "INTEGER"),
        ("pixel_x", "REAL"),
        ("pixel_y", "REAL"),
        ("before_date", "TEXT"),
        ("after_date", "TEXT"),
        ("officer_notes", "TEXT"),
        ("scheduled_for", "TEXT"),
    ):
        if name not in task_columns:
            connection.execute(f"ALTER TABLE surveillance_tasks ADD COLUMN {name} {declaration}")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_surveillance_task_dedupe_key "
        "ON surveillance_tasks(task_dedupe_key) WHERE task_dedupe_key IS NOT NULL"
    )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS demo_imagery_comparisons (
            comparison_key TEXT PRIMARY KEY,
            property_location_id INTEGER,
            case_id TEXT,
            before_date TEXT,
            after_date TEXT,
            before_frame INTEGER NOT NULL,
            after_frame INTEGER NOT NULL,
            before_sha256 TEXT NOT NULL,
            after_sha256 TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            quality_report_json TEXT,
            changed_pixel_percentage REAL NOT NULL,
            changed_pixel_count INTEGER NOT NULL,
            region_count INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS ix_demo_comparison_property ON demo_imagery_comparisons(property_location_id, created_at)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS surveillance_review_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            marker_key TEXT NOT NULL UNIQUE,
            comparison_key TEXT NOT NULL,
            property_location_id INTEGER NOT NULL,
            case_id TEXT NOT NULL,
            task_status TEXT NOT NULL DEFAULT 'Pending Surveillance Review',
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(marker_key) REFERENCES change_markers(marker_key),
            FOREIGN KEY(comparison_key) REFERENCES demo_imagery_comparisons(comparison_key),
            FOREIGN KEY(property_location_id) REFERENCES property_locations(location_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS ix_surveillance_review_case ON surveillance_review_requests(case_id, requested_at)")


def save_demo_imagery_comparison(
    connection, *, comparison_key, property_location_id, case_id, before_date, after_date,
    before_frame, after_frame, before_sha256, after_sha256, image_refs, parameters,
    quality_report, changed_pixel_percentage, changed_pixel_count, markers, actor_identity,
):
    """Store GIF comparison metadata and pixel-only markers idempotently."""
    if not str(actor_identity or "").strip():
        raise ValueError("Authenticated actor identity is required for the audit log.")
    ensure_surveillance_schema(connection)
    with connection:
        comparison_insert = connection.execute("""
            INSERT INTO demo_imagery_comparisons (
                comparison_key, property_location_id, case_id, before_date, after_date,
                before_frame, after_frame, before_sha256, after_sha256, image_refs_json,
                parameters_json, quality_report_json, changed_pixel_percentage,
                changed_pixel_count, region_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comparison_key) DO NOTHING
        """, (
            comparison_key, property_location_id, case_id, before_date, after_date,
            int(before_frame), int(after_frame), before_sha256, after_sha256,
            json.dumps(image_refs, sort_keys=True), json.dumps(parameters, sort_keys=True),
            json.dumps(quality_report, sort_keys=True) if quality_report is not None else None,
            float(changed_pixel_percentage), int(changed_pixel_count), len(markers),
        ))
        if comparison_insert.rowcount:
            from case_audit import record_audit_event
            audit_type, audit_id = _audit_target(connection, case_id, comparison_key)
            record_audit_event(
                connection, audit_type, audit_id, "demo_imagery_comparison_saved",
                note=(
                    f"DEMO MODE GIF comparison saved for frames {int(before_frame) + 1} and "
                    f"{int(after_frame) + 1}; {len(markers)} pixel-difference regions retained."
                ),
                actor_identity=actor_identity,
            )
        for marker in markers:
            inserted = connection.execute("""
                INSERT INTO change_markers (
                    marker_key, comparison_key, property_location_id, case_id, marker_number,
                    pixel_x, pixel_y, pixel_radius, region_pixels, latitude, longitude,
                    radius_m, area_sqm, method, parameters_json, image_refs_json,
                    quality_report_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(marker_key) DO NOTHING
            """, (
                marker.marker_key, comparison_key, property_location_id, case_id,
                marker.number, marker.pixel_x, marker.pixel_y, marker.pixel_radius,
                marker.region_pixels, "OpenCV pixel differencing (demo GIF)",
                json.dumps(parameters, sort_keys=True), json.dumps(image_refs, sort_keys=True),
                json.dumps(quality_report, sort_keys=True) if quality_report is not None else None,
                json.dumps(tuple(marker.warnings) + ("Demo imagery is not georeferenced; pixel location only.",)),
            ))
            if inserted.rowcount:
                from case_audit import record_audit_event
                audit_type, audit_id = _audit_target(connection, case_id, marker.marker_key)
                record_audit_event(
                    connection, audit_type, audit_id, "change_marker_created",
                    new_status="Uncertain",
                    note=f"Demo GIF pixel-difference marker {marker.number} created; verification required.",
                    actor_identity=actor_identity,
                )


def create_surveillance_review_request(
    connection, marker_key, *, property_location_id, case_id, actor_identity,
):
    """Create a deduplicated, non-geospatial request for officer review."""
    if not str(actor_identity or "").strip():
        raise ValueError("Authenticated officer identity is required.")
    if property_location_id is None or not str(case_id or "").strip():
        raise ValueError("Select a registered property and related case before requesting surveillance review.")
    ensure_surveillance_schema(connection)
    with connection:
        marker = connection.execute(
            "SELECT comparison_key, property_location_id, case_id, review_status FROM change_markers WHERE marker_key = ?",
            (marker_key,),
        ).fetchone()
        if marker is None or marker["review_status"] != "Confirmed for surveillance":
            raise ValueError("Only an officer-confirmed potential change can be sent for surveillance review.")
        if marker["property_location_id"] != int(property_location_id):
            raise ValueError("The selected property does not match the property linked to this comparison.")
        if marker["case_id"] != str(case_id):
            raise ValueError("The selected case does not match the case linked to this comparison.")
        if not connection.execute("SELECT 1 FROM property_locations WHERE location_id = ?", (property_location_id,)).fetchone():
            raise ValueError("The registered property was not found.")
        case_found = connection.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if not case_found:
            case_found = connection.execute("SELECT 1 FROM image_change_detections WHERE case_id = ?", (case_id,)).fetchone()
        if not case_found:
            raise ValueError("The related case was not found.")
        existing = connection.execute(
            "SELECT * FROM surveillance_review_requests WHERE marker_key = ?", (marker_key,)
        ).fetchone()
        if existing:
            return existing, False
        connection.execute("""
            INSERT INTO surveillance_review_requests (
                marker_key, comparison_key, property_location_id, case_id,
                task_status, requested_by
            ) VALUES (?, ?, ?, ?, 'Pending Surveillance Review', ?)
        """, (marker_key, marker["comparison_key"], int(property_location_id), str(case_id), str(actor_identity).strip()))
        from case_audit import record_audit_event
        case_type = "construction" if connection.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone() else "image_detection"
        record_audit_event(
            connection, case_type, case_id, "surveillance_review_requested",
            new_status="Pending Surveillance Review",
            note=f"Pixel-only marker {marker_key} sent for further surveillance review; no flight was authorized or commanded.",
            actor_identity=actor_identity,
        )
        row = connection.execute(
            "SELECT * FROM surveillance_review_requests WHERE marker_key = ?", (marker_key,)
        ).fetchone()
        return row, True


def upsert_change_markers(connection, comparison_key, property_location_id, case_id,
                           markers, *, method, parameters, image_refs, quality_report,
                           actor_identity):
    if not str(actor_identity or "").strip():
        raise ValueError("Authenticated actor identity is required for the audit log.")
    ensure_surveillance_schema(connection)
    connection.execute("""
        INSERT INTO property_imagery_pairs (
            comparison_key, property_location_id, case_id, before_date, after_date,
            method, image_refs_json, quality_report_json, parameters_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comparison_key) DO NOTHING
    """, (comparison_key, property_location_id, case_id,
          image_refs.get("before_date", ""), image_refs.get("after_date", ""),
          method, json.dumps(image_refs, sort_keys=True),
          json.dumps(quality_report, sort_keys=True) if quality_report is not None else None,
          json.dumps(parameters, sort_keys=True)))
    for marker in markers:
        result = connection.execute("""
            INSERT INTO change_markers (
                marker_key, comparison_key, property_location_id, case_id, marker_number,
                pixel_x, pixel_y, pixel_radius, region_pixels, latitude, longitude,
                radius_m, area_sqm, method, parameters_json, image_refs_json, quality_report_json,
                warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marker_key) DO NOTHING
        """, (marker.marker_key, comparison_key, property_location_id, case_id, marker.number,
              marker.pixel_x, marker.pixel_y, marker.pixel_radius, marker.region_pixels,
              marker.latitude, marker.longitude, marker.radius_m, marker.area_sqm,
              method, json.dumps(parameters, sort_keys=True), json.dumps(image_refs, sort_keys=True),
              json.dumps(quality_report, sort_keys=True) if quality_report is not None else None,
              json.dumps(marker.warnings)))
        if result.rowcount:
            from case_audit import record_audit_event
            audit_type, audit_id = _audit_target(connection, case_id, marker.marker_key)
            record_audit_event(connection, audit_type, audit_id,
                               "change_marker_created", new_status="Uncertain",
                               note=f"Marker {marker.number} created from image differencing; verification required.",
                               actor_identity=actor_identity)


def update_marker_review(connection, marker_key, status, reason, actor_identity):
    if status not in ("Confirmed for surveillance", "Dismissed", "Uncertain"):
        raise ValueError("Unsupported marker review status.")
    if not str(reason or "").strip() or not str(actor_identity or "").strip():
        raise ValueError("A reason and authenticated officer identity are required.")
    with connection:
        row = connection.execute("SELECT case_id, review_status FROM change_markers WHERE marker_key = ?", (marker_key,)).fetchone()
        if row is None:
            raise ValueError("Marker was not found.")
        connection.execute("UPDATE change_markers SET review_status = ?, review_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE marker_key = ?",
                           (status, reason.strip(), marker_key))
        from case_audit import record_audit_event
        audit_type, audit_id = _audit_target(connection, row["case_id"], marker_key)
        record_audit_event(connection, audit_type, audit_id,
                           "change_marker_reviewed", old_status=row["review_status"],
                           new_status=status, note=reason.strip(), actor_identity=actor_identity)


def create_or_update_surveillance_task(connection, marker_key, *, buffer_margin_m,
                                       risk_priority, assigned_to, actor_identity,
                                       permission_reference=None, suggested_altitude_m=None,
                                       suggested_overlap_pct=None):
    with connection:
        marker = connection.execute("SELECT * FROM change_markers WHERE marker_key = ?", (marker_key,)).fetchone()
        if marker is None or marker["review_status"] != "Confirmed for surveillance":
            raise ValueError("Only a confirmed marker can be sent for surveillance.")
        if marker["latitude"] is None or marker["longitude"] is None or marker["radius_m"] is None:
            raise ValueError("Drone hand-off requires a georeferenced marker with a metric radius.")
        if not str(assigned_to or "").strip():
            raise ValueError("Assign a pilot or officer before creating the hand-off task.")
        if not str(actor_identity or "").strip():
            raise ValueError("Authenticated officer identity is required for audit.")
        refs = json.loads(marker["image_refs_json"])
        existing_at_location = connection.execute("""
            SELECT task_id, marker_key FROM surveillance_tasks
            WHERE property_location_id = ? AND ROUND(target_latitude, 6) = ROUND(?, 6)
              AND ROUND(target_longitude, 6) = ROUND(?, 6)
            ORDER BY task_id LIMIT 1
        """, (marker["property_location_id"], marker["latitude"], marker["longitude"])).fetchone()
        if existing_at_location and existing_at_location["marker_key"] != marker_key:
            connection.execute("""
                UPDATE surveillance_tasks SET marker_key=?, case_id=?, target_latitude=?, target_longitude=?,
                    buffer_radius_m=?, reason=?, image_refs_json=?, risk_priority=?, assigned_to=?,
                    suggested_altitude_m=?, suggested_overlap_pct=?, permission_reference=?,
                    updated_at=CURRENT_TIMESTAMP WHERE task_id=?
            """, (marker_key, marker["case_id"], marker["latitude"], marker["longitude"],
                  marker["radius_m"] + max(0.0, float(buffer_margin_m)),
                  "Potential Construction Change - Verification Required", json.dumps(refs, sort_keys=True),
                  risk_priority, assigned_to.strip(), suggested_altitude_m, suggested_overlap_pct,
                  permission_reference, existing_at_location["task_id"]))
        else:
            connection.execute("""
            INSERT INTO surveillance_tasks (
                marker_key, property_location_id, case_id, target_latitude, target_longitude,
                buffer_radius_m, reason, image_refs_json, risk_priority, assigned_to,
                suggested_altitude_m, suggested_overlap_pct, permission_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marker_key) DO UPDATE SET
                target_latitude=excluded.target_latitude, target_longitude=excluded.target_longitude,
                buffer_radius_m=excluded.buffer_radius_m, reason=excluded.reason,
                image_refs_json=excluded.image_refs_json, risk_priority=excluded.risk_priority,
                assigned_to=excluded.assigned_to, suggested_altitude_m=excluded.suggested_altitude_m,
                suggested_overlap_pct=excluded.suggested_overlap_pct,
                permission_reference=excluded.permission_reference,
                updated_at=CURRENT_TIMESTAMP
            """, (marker_key, marker["property_location_id"], marker["case_id"],
                  marker["latitude"], marker["longitude"], marker["radius_m"] + max(0.0, float(buffer_margin_m)),
                  "Potential Construction Change - Verification Required", json.dumps(refs, sort_keys=True),
                  risk_priority, assigned_to.strip(), suggested_altitude_m,
                  suggested_overlap_pct, permission_reference))
        from case_audit import record_audit_event
        audit_type, audit_id = _audit_target(connection, marker["case_id"], marker_key)
        record_audit_event(connection, audit_type, audit_id,
                           "surveillance_task_created_or_updated",
                           note=f"Pilot hand-off task assigned to {assigned_to.strip()}.",
                           actor_identity=actor_identity)
        return connection.execute("SELECT * FROM surveillance_tasks WHERE marker_key = ?", (marker_key,)).fetchone()


def create_verified_survey_task(
    connection,
    marker_key,
    *,
    property_location_id,
    case_id,
    latitude,
    longitude,
    after_pixel_x,
    after_pixel_y,
    location_source,
    location_uncertainty,
    location_source_reference,
    task_purpose,
    reason,
    officer_notes,
    assigned_to,
    schedule_type,
    permission_reference,
    before_date,
    after_date,
    scheduled_for=None,
    actor_identity,
):
    """Save a human-prepared task after officer confirmation of a verified location."""
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("A verified latitude and longitude are required.") from error
    if not (math.isfinite(latitude) and -90 <= latitude <= 90):
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not (math.isfinite(longitude) and -180 <= longitude <= 180):
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    try:
        after_pixel_x, after_pixel_y = float(after_pixel_x), float(after_pixel_y)
    except (TypeError, ValueError) as error:
        raise ValueError("AFTER-image pixel coordinates are required.") from error
    if not (math.isfinite(after_pixel_x) and math.isfinite(after_pixel_y) and after_pixel_x >= 0 and after_pixel_y >= 0):
        raise ValueError("AFTER-image pixel coordinates must be finite and non-negative.")
    if not all(str(value or "").strip() for value in (
        actor_identity, location_source, location_uncertainty, task_purpose,
        reason, assigned_to, schedule_type,
    )):
        raise ValueError("Location source, uncertainty, survey purpose, reason, assignment, and officer identity are required.")
    if schedule_type not in ("One-time", "Weekly", "Monthly"):
        raise ValueError("Choose a supported proposed schedule.")

    ensure_surveillance_schema(connection)
    dedupe_key = hashlib.sha256(
        f"{case_id}:{marker_key}:{task_purpose.strip()}".encode("utf-8")
    ).hexdigest()
    with connection:
        marker = connection.execute(
            "SELECT * FROM change_markers WHERE marker_key = ?", (marker_key,)
        ).fetchone()
        if marker is None or marker["review_status"] != "Confirmed for surveillance":
            raise ValueError("Only an officer-confirmed region can be prepared for survey.")
        if marker["property_location_id"] != int(property_location_id):
            raise ValueError("The selected property does not match the confirmed region.")
        if marker["case_id"] != str(case_id):
            raise ValueError("The selected case does not match the confirmed region.")
        property_row = connection.execute(
            "SELECT * FROM property_locations WHERE location_id = ?", (property_location_id,)
        ).fetchone()
        if property_row is None or property_row["verification_status"] != "Verified":
            raise ValueError("Verify the registered property location before preparing location-based field surveillance.")
        case_type = "construction" if connection.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone() else "image_detection"
        case_exists = connection.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone() or connection.execute(
            "SELECT 1 FROM image_change_detections WHERE case_id = ?", (case_id,)
        ).fetchone()
        if not case_exists:
            raise ValueError("The linked case no longer exists.")
        existing = connection.execute(
            "SELECT * FROM surveillance_tasks WHERE task_dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        if existing:
            return existing, False

        image_refs = json.loads(marker["image_refs_json"] or "{}")
        image_refs.update({
            "location_source": location_source.strip(),
            "location_uncertainty": location_uncertainty.strip(),
            "location_source_reference": location_source_reference,
            "latitude": latitude,
            "longitude": longitude,
        })
        try:
            connection.execute("""
                INSERT INTO surveillance_tasks (
                    marker_key, property_location_id, case_id, target_latitude,
                    target_longitude, buffer_radius_m, reason, image_refs_json,
                    risk_priority, assigned_to, schedule_type, permission_reference,
                    task_status, task_dedupe_key, task_purpose, location_source,
                    location_uncertainty, marker_number, pixel_x, pixel_y,
                    before_date, after_date, officer_notes, scheduled_for
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'Officer review', ?, ?, ?,
                    'Prepared - Awaiting Field Survey', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                marker_key, int(property_location_id), str(case_id), latitude, longitude,
                reason.strip(), json.dumps(image_refs, sort_keys=True), assigned_to.strip(),
                schedule_type, str(permission_reference or "").strip() or None,
                dedupe_key, task_purpose.strip(), location_source.strip(),
                location_uncertainty.strip(), marker["marker_number"], after_pixel_x,
                after_pixel_y, before_date, after_date,
                str(officer_notes or "").strip() or None, scheduled_for,
            ))
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "A survey task already exists for this registered property location. "
                "Review the existing task before preparing another one."
            ) from error

        from case_audit import record_audit_event
        record_audit_event(
            connection, case_type, case_id, "surveillance_task_prepared",
            new_status="Prepared - Awaiting Field Survey",
            note=(
                f"Officer {str(actor_identity).strip()} confirmed the survey target for region "
                f"{marker['marker_number']}. Location source: {location_source.strip()}; "
                f"source reference: {str(location_source_reference or 'not recorded').strip()}; "
                f"uncertainty: {location_uncertainty.strip()}; proposed schedule: {scheduled_for or 'not set'}; "
                f"permission reference: {str(permission_reference or '').strip() or 'not entered'}. "
                "No flight was authorized or commanded."
            ),
            actor_identity=actor_identity,
        )
        task = connection.execute(
            "SELECT * FROM surveillance_tasks WHERE task_dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        return task, True


def _buffer_coordinates(task, segments=64):
    from rasterio.warp import transform
    lon, lat = float(task["target_longitude"]), float(task["target_latitude"])
    local = _utm_crs(lon, lat)
    x, y = transform("EPSG:4326", local, [lon], [lat])
    radius = max(0.0, float(task["buffer_radius_m"]))
    ring = [(x[0] + radius * math.cos(2 * math.pi * i / segments),
             y[0] + radius * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
    ring.append(ring[0])
    lons, lats = transform(local, "EPSG:4326", [p[0] for p in ring], [p[1] for p in ring])
    return [[float(a), float(b)] for a, b in zip(lons, lats)]


def export_task_geojson(task, property_reference, before_date, after_date, notes="") -> str:
    properties = {"task_id": task["task_id"], "property_reference": property_reference,
                  "buffer_radius_m": task["buffer_radius_m"], "before_date": before_date,
                  "after_date": after_date, "reason": task["reason"], "notes": notes}
    point = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [task["target_longitude"], task["target_latitude"]]},
             "properties": dict(properties, feature="target")}
    buffer = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_buffer_coordinates(task)]},
              "properties": dict(properties, feature="buffer")}
    return json.dumps({"type": "FeatureCollection", "features": [point, buffer]}, sort_keys=True)


def export_task_kml(task, property_reference, notes="") -> str:
    ring = " ".join(f"{lon},{lat},0" for lon, lat in _buffer_coordinates(task))
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<kml xmlns=\"http://www.opengis.net/kml/2.2\"><Document>"
            "<Placemark><name>Survey target</name>"
            f"<description>{html_escape(property_reference)}: {html_escape(notes)}</description>"
            f"<Point><coordinates>{task['target_longitude']},{task['target_latitude']},0</coordinates></Point></Placemark>"
            "<Placemark><name>Survey buffer</name><Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
            "</Document></kml>")


def html_escape(value):
    import html
    return html.escape(str(value or ""), quote=True)


def export_task_csv(task, property_reference, before_date, after_date, notes="") -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=("task_id", "property_reference", "latitude", "longitude",
        "buffer_radius_m", "before_date", "after_date", "reason", "notes"))
    writer.writeheader()
    writer.writerow({"task_id": task["task_id"], "property_reference": property_reference,
                     "latitude": task["target_latitude"], "longitude": task["target_longitude"],
                     "buffer_radius_m": task["buffer_radius_m"], "before_date": before_date,
                     "after_date": after_date, "reason": task["reason"], "notes": notes})
    return output.getvalue()
