"""Synthetic tests for marker geometry, persistence, and pilot hand-off exports."""

import json
import sqlite3
import unittest

import numpy as np

try:
    from rasterio.transform import from_origin
    from rasterio.warp import transform
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

from change_markers import (
    create_or_update_surveillance_task,
    draw_change_markers,
    ensure_surveillance_schema,
    export_task_csv,
    export_task_geojson,
    export_task_kml,
    extract_change_markers,
    update_marker_review,
    upsert_change_markers,
    validate_marker_location,
)


def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE property_locations(location_id INTEGER PRIMARY KEY, property_reference TEXT)")
    db.execute("INSERT INTO property_locations VALUES(1, 'P-1')")
    db.execute("CREATE TABLE case_audit_events(event_id INTEGER PRIMARY KEY, case_type TEXT NOT NULL, case_id TEXT NOT NULL, event_type TEXT NOT NULL, old_status TEXT, new_status TEXT, note TEXT, resolution_summary TEXT, actor_identity TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    ensure_surveillance_schema(db)
    db.commit()
    return db


@unittest.skipUnless(HAS_RASTERIO, "Rasterio is required for geospatial marker tests")
class GeospatialMarkerTests(unittest.TestCase):
    def test_projected_marker_coordinates_radius_and_minimum_component_filter(self):
        mask = np.zeros((128, 128), dtype=np.uint8)
        mask[39:61, 59:81] = 255
        mask[5:8, 5:8] = 255
        affine = from_origin(500000, 2200000, 0.5, 0.5)
        markers = extract_change_markers(mask, comparison_key="known-pair", minimum_region_pixels=20,
                                        transform=affine, crs="EPSG:32643",
                                        property_point=(19.9, 75.0))
        self.assertEqual(len(markers), 1)
        marker = markers[0]
        x, y = affine @ (70, 50)
        lon, lat = transform("EPSG:32643", "EPSG:4326", [x], [y])
        self.assertAlmostEqual(marker.longitude, lon[0], delta=1e-6)
        self.assertAlmostEqual(marker.latitude, lat[0], delta=1e-6)
        self.assertAlmostEqual(marker.radius_m, marker.pixel_radius * 0.5, delta=0.03)
        self.assertAlmostEqual(marker.area_sqm, 22 * 22 * 0.25, delta=2)
        repeated = extract_change_markers(mask, comparison_key="known-pair", minimum_region_pixels=20,
                                          transform=affine, crs="EPSG:32643",
                                          property_point=(19.9, 75.0))
        self.assertEqual(markers, repeated)

    def test_unreferenced_markers_have_no_coordinates_or_metric_area(self):
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[10:20, 10:20] = 255
        marker, = extract_change_markers(mask, comparison_key="pixel-pair", minimum_region_pixels=4)
        self.assertIsNone(marker.latitude)
        self.assertIsNone(marker.longitude)
        self.assertIsNone(marker.radius_m)
        self.assertIsNone(marker.area_sqm)
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        self.assertTrue(np.any(draw_change_markers(image, [marker])[:, :, 0] > 0))

    def test_location_warnings_cover_far_property_and_outside_image_bounds(self):
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[10:20, 10:20] = 255
        marker, = extract_change_markers(mask, comparison_key="far", minimum_region_pixels=1,
            transform=from_origin(500000, 2200000, 1, 1), crs="EPSG:32643", property_point=(0, 0))
        self.assertTrue(any("far from" in warning for warning in marker.warnings))
        warnings = validate_marker_location(marker, (0, 0, 1, 1))
        self.assertTrue(any("outside" in warning for warning in warnings))

    def test_idempotent_schema_audit_and_one_task_per_marker_or_location(self):
        db = database()
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[10:20, 10:20] = 255
        marker, = extract_change_markers(mask, comparison_key="persist", minimum_region_pixels=1,
            transform=from_origin(500000, 2200000, 1, 1), crs="EPSG:32643")
        args = ("persist", 1, "C-1", [marker])
        refs = {"before": "a", "after": "b", "before_date": "2024-01-01", "after_date": "2025-01-01"}
        for _ in range(2):
            ensure_surveillance_schema(db)
            upsert_change_markers(db, *args, method="synthetic", parameters={"threshold": 1},
                                  image_refs=refs, quality_report={"valid": 99}, actor_identity="officer@example.org")
        db.commit()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM change_markers").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM property_imagery_pairs").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "(?i)only a confirmed marker"):
            create_or_update_surveillance_task(db, marker.marker_key, buffer_margin_m=10,
                risk_priority="Routine", assigned_to="Pilot 1", actor_identity="officer@example.org")
        update_marker_review(db, marker.marker_key, "Confirmed for surveillance", "Officer reviewed", "officer@example.org")
        task = create_or_update_surveillance_task(db, marker.marker_key, buffer_margin_m=10,
            risk_priority="Routine", assigned_to="Pilot 1", actor_identity="officer@example.org", permission_reference="REF-1")
        again = create_or_update_surveillance_task(db, marker.marker_key, buffer_margin_m=10,
            risk_priority="Routine", assigned_to="Pilot 1", actor_identity="officer@example.org", permission_reference="REF-1")
        self.assertEqual(task["task_id"], again["task_id"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM surveillance_tasks").fetchone()[0], 1)
        second_marker, = extract_change_markers(mask, comparison_key="persist-again", minimum_region_pixels=1,
            transform=from_origin(500000, 2200000, 1, 1), crs="EPSG:32643")
        upsert_change_markers(db, "persist-again", 1, "C-2", [second_marker], method="synthetic",
            parameters={"threshold": 1}, image_refs=refs, quality_report={"valid": 99}, actor_identity="officer@example.org")
        update_marker_review(db, second_marker.marker_key, "Confirmed for surveillance", "Same target point", "officer@example.org")
        shared_point_task = create_or_update_surveillance_task(db, second_marker.marker_key, buffer_margin_m=15,
            risk_priority="Routine", assigned_to="Pilot 1", actor_identity="officer@example.org")
        self.assertEqual(task["task_id"], shared_point_task["task_id"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM surveillance_tasks").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT actor_identity FROM case_audit_events ORDER BY event_id DESC LIMIT 1").fetchone()[0], "officer@example.org")

    def test_task_exports_include_point_and_buffer_and_omit_personal_details(self):
        task = {"task_id": 7, "target_latitude": 18.5, "target_longitude": 73.8,
                "buffer_radius_m": 40, "reason": "Potential Construction Change - Verification Required"}
        geojson = json.loads(export_task_geojson(task, "PROP-1", "2024-01-01", "2025-01-01", "Capture west side"))
        self.assertEqual(len(geojson["features"]), 2)
        self.assertEqual(geojson["features"][0]["geometry"]["coordinates"], [73.8, 18.5])
        self.assertEqual(geojson["features"][1]["geometry"]["type"], "Polygon")
        kml = export_task_kml(task, "PROP-1", "Capture west side")
        self.assertIn("<Point>", kml)
        self.assertIn("<Polygon>", kml)
        self.assertIn("PROP-1", kml)
        csv_text = export_task_csv(task, "PROP-1", "2024-01-01", "2025-01-01", "Capture west side")
        self.assertIn("latitude", csv_text)
        self.assertIn("longitude", csv_text)
        self.assertNotIn("owner", csv_text.casefold())
