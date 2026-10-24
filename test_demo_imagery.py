import io
import sqlite3
import unittest

import numpy as np
from PIL import Image

from change_detection import compare_images
from change_markers import (
    ChangeMarker,
    create_surveillance_review_request,
    create_verified_survey_task,
    draw_change_markers,
    ensure_surveillance_schema,
    save_demo_imagery_comparison,
    update_marker_review,
)
from demo_imagery import (
    GifInfo,
    decode_comparison_frame,
    decode_gif_frame,
    filter_regions_by_area,
    extract_georeferenced_pixel_location,
    inspect_comparison_image,
    inspect_gif,
    validate_gif_pair,
)
from image_alignment import map_reference_point_to_newer_pixel


def make_gif(frames):
    output = io.BytesIO()
    pil_frames = [Image.fromarray(frame.astype(np.uint8), "RGB") for frame in frames]
    pil_frames[0].save(
        output, format="GIF", save_all=True, append_images=pil_frames[1:], duration=100, loop=0
    )
    return output.getvalue()


def png_bytes(rgb):
    output = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8), "RGB").save(output, format="PNG")
    return output.getvalue()


class DemoGifTests(unittest.TestCase):
    def test_static_png_and_jpeg_comparison_images_are_readable(self):
        rgb = np.full((24, 32, 3), (22, 88, 42), dtype=np.uint8)
        for image_format, filename in (("PNG", "forest.png"), ("JPEG", "forest.jpg")):
            output = io.BytesIO()
            Image.fromarray(rgb).save(output, format=image_format)
            payload = output.getvalue()
            info = inspect_comparison_image(payload, filename)
            self.assertEqual(info.frame_count, 1)
            self.assertEqual(info.image_format, image_format)
            decoded = decode_comparison_frame(payload, filename, 0)
            self.assertEqual(decoded.shape, rgb.shape)
        with self.assertRaisesRegex(ValueError, "extension does not match"):
            inspect_comparison_image(payload, "forest.png")

    def test_animated_gif_frame_selection_and_validation(self):
        first = np.zeros((32, 32, 3), dtype=np.uint8)
        second = np.zeros_like(first)
        second[8:24, 8:24] = (255, 0, 0)
        payload = make_gif([first, second])
        info = inspect_gif(payload, "demo.gif")
        self.assertEqual(info, GifInfo(32, 32, 2))
        frame = decode_gif_frame(payload, 1)
        self.assertEqual(tuple(frame[12, 12]), (255, 0, 0))
        with self.assertRaisesRegex(ValueError, "valid GIF|unreadable"):
            inspect_gif(b"not an image", "demo.gif")

    def test_incompatible_aspect_or_scale_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "aspect ratios"):
            validate_gif_pair(GifInfo(400, 300, 1), GifInfo(300, 400, 1))
        with self.assertRaisesRegex(ValueError, "scale factor"):
            validate_gif_pair(GifInfo(800, 800, 1), GifInfo(300, 300, 1))

    def test_synthetic_geotiff_maps_a_pixel_to_known_wgs84_coordinates(self):
        import rasterio
        from affine import Affine
        from rasterio.io import MemoryFile
        from rasterio.warp import transform

        rgb = np.zeros((30, 40, 3), dtype=np.uint8)
        rgb[:, :, 0] = np.arange(40, dtype=np.uint8)[None, :]
        rgb[:, :, 1] = np.arange(30, dtype=np.uint8)[:, None]
        with MemoryFile() as memory_file:
            with memory_file.open(
                driver="GTiff", width=40, height=30, count=3, dtype="uint8",
                crs="EPSG:32643", transform=Affine(10, 0, 500000, 0, -10, 1000000),
            ) as dataset:
                dataset.write(np.moveaxis(rgb, -1, 0))
            payload = memory_file.read()
        x, y = Affine(10, 0, 500000, 0, -10, 1000000) @ (7.5, 9.5)
        expected_lon, expected_lat = transform("EPSG:32643", "EPSG:4326", [x], [y])
        location = extract_georeferenced_pixel_location(payload, "known.tif", rgb, 7.5, 9.5)
        self.assertAlmostEqual(location.longitude, expected_lon[0], places=8)
        self.assertAlmostEqual(location.latitude, expected_lat[0], places=8)
        self.assertEqual(location.crs, "EPSG:32643")
        self.assertGreater(location.uncertainty_m, 0)

    def test_gif_cannot_be_used_as_georeferenced_location_source(self):
        rgb = np.zeros((16, 16, 3), dtype=np.uint8)
        payload = make_gif([rgb])
        with self.assertRaisesRegex(ValueError, "GeoTIFF is required"):
            extract_georeferenced_pixel_location(payload, "demo.gif", rgb, 2.5, 2.5)

    def test_alignment_pixel_transform_maps_back_to_after_frame(self):
        matrix = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -3.0]])
        after_point = map_reference_point_to_newer_pixel(matrix, 25.5, 37.5)
        self.assertAlmostEqual(after_point[0], 20.5)
        self.assertAlmostEqual(after_point[1], 40.5)

    def test_identical_and_added_rectangle_comparisons(self):
        rng = np.random.default_rng(9)
        before = rng.integers(30, 220, size=(320, 320, 3), dtype=np.uint8)
        identical = compare_images(png_bytes(before), png_bytes(before.copy()))
        self.assertEqual(int(np.count_nonzero(identical.mask)), 0)

        after = before.copy()
        after[110:180, 130:205] = (235, 235, 235)
        comparison = compare_images(png_bytes(before), png_bytes(after))
        filtered = filter_regions_by_area(comparison.mask, 200)
        self.assertGreater(int(np.count_nonzero(filtered)), 200)
        self.assertEqual(int(np.count_nonzero(filter_regions_by_area(filtered, 100_000))), 0)
        from change_markers import extract_change_markers
        markers = extract_change_markers(filtered, comparison_key="synthetic", minimum_region_pixels=200)
        circled = draw_change_markers(comparison.aligned_newer_rgb, markers)
        red_pixels = (circled[:, :, 0] > 200) & (circled[:, :, 1] < 80) & (circled[:, :, 2] < 80)
        self.assertGreater(int(np.count_nonzero(red_pixels)), 0)

    def test_confirmed_request_is_audited_deduplicated_and_dismissed_is_rejected(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE property_locations (
                location_id INTEGER PRIMARY KEY, property_reference TEXT NOT NULL,
                latitude REAL NOT NULL, longitude REAL NOT NULL,
                verification_status TEXT NOT NULL, verification_evidence TEXT
            );
            CREATE TABLE cases (case_id TEXT PRIMARY KEY);
            CREATE TABLE image_change_detections (case_id TEXT PRIMARY KEY);
            CREATE TABLE case_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, case_type TEXT NOT NULL,
                case_id TEXT NOT NULL, event_type TEXT NOT NULL, old_status TEXT,
                new_status TEXT, note TEXT, resolution_summary TEXT, actor_identity TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO property_locations VALUES (1, 'PROP-1', 12.0, 77.0, 'Verified', 'Officer checked property register');
            INSERT INTO cases VALUES ('CASE-1');
        """)
        ensure_surveillance_schema(connection)
        connection.execute("""
            INSERT INTO demo_imagery_comparisons (
                comparison_key, property_location_id, case_id, before_frame, after_frame,
                before_sha256, after_sha256, image_refs_json, parameters_json,
                changed_pixel_percentage, changed_pixel_count, region_count
            ) VALUES ('cmp', 1, 'CASE-1', 0, 0, 'before', 'after', '{}', '{}', 1.0, 100, 1)
        """)
        marker = ChangeMarker("marker-1", 1, 20, 30, 10, 100, None, None, None, None)
        save_demo_imagery_comparison(
            connection, comparison_key="cmp", property_location_id=1, case_id="CASE-1",
            before_date=None, after_date=None, before_frame=0, after_frame=0,
            before_sha256="before", after_sha256="after", image_refs={}, parameters={},
            quality_report={}, changed_pixel_percentage=1.0, changed_pixel_count=100,
            markers=[marker], actor_identity="officer",
        )
        update_marker_review(connection, "marker-1", "Dismissed", "Not a construction change", "officer")
        with self.assertRaisesRegex(ValueError, "Only an officer-confirmed"):
            create_surveillance_review_request(
                connection, "marker-1", property_location_id=1,
                case_id="CASE-1", actor_identity="officer",
            )
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM surveillance_review_requests").fetchone()[0], 0)
        update_marker_review(connection, "marker-1", "Confirmed for surveillance", "Reviewed pixels", "officer")
        request, created = create_surveillance_review_request(
            connection, "marker-1", property_location_id=1,
            case_id="CASE-1", actor_identity="officer",
        )
        duplicate, duplicate_created = create_surveillance_review_request(
            connection, "marker-1", property_location_id=1,
            case_id="CASE-1", actor_identity="officer",
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(request["request_id"], duplicate["request_id"])
        self.assertEqual(request["task_status"], "Pending Surveillance Review")
        self.assertIsNone(connection.execute("SELECT target_latitude FROM surveillance_tasks").fetchone())
        connection.execute("UPDATE property_locations SET verification_status='Unverified' WHERE location_id=1")
        with self.assertRaisesRegex(ValueError, "Verify the registered property location"):
            create_verified_survey_task(
                connection, "marker-1", property_location_id=1, case_id="CASE-1",
                latitude=12.0, longitude=77.0, after_pixel_x=20.5, after_pixel_y=30.5,
                location_source="Registered property location for general site inspection",
                location_uncertainty="Not a detected-region coordinate.", location_source_reference=None,
                task_purpose="General property site inspection", reason="Review visual finding",
                officer_notes="", assigned_to="Officer", schedule_type="One-time",
                permission_reference="", before_date="Date unknown", after_date="Date unknown",
                actor_identity="officer",
            )
        connection.execute("UPDATE property_locations SET verification_status='Verified' WHERE location_id=1")
        task, task_created = create_verified_survey_task(
            connection, "marker-1", property_location_id=1, case_id="CASE-1",
            latitude=12.0, longitude=77.0, after_pixel_x=20.5, after_pixel_y=30.5,
            location_source="Registered property location for general site inspection",
            location_uncertainty="Property location only; not detected-region accuracy.", location_source_reference=None,
            task_purpose="General property site inspection", reason="Review visual finding",
            officer_notes="Human-reviewed synthetic case.", assigned_to="Officer", schedule_type="One-time",
            scheduled_for="2026-10-25T10:30",
            permission_reference="Not entered", before_date="Date unknown", after_date="Date unknown",
            actor_identity="officer",
        )
        duplicate_task, task_duplicate_created = create_verified_survey_task(
            connection, "marker-1", property_location_id=1, case_id="CASE-1",
            latitude=12.0, longitude=77.0, after_pixel_x=20.5, after_pixel_y=30.5,
            location_source="Registered property location for general site inspection",
            location_uncertainty="Property location only; not detected-region accuracy.", location_source_reference=None,
            task_purpose="General property site inspection", reason="Review visual finding",
            officer_notes="Human-reviewed synthetic case.", assigned_to="Officer", schedule_type="One-time",
            scheduled_for="2026-10-25T10:30",
            permission_reference="Not entered", before_date="Date unknown", after_date="Date unknown",
            actor_identity="officer",
        )
        self.assertTrue(task_created)
        self.assertFalse(task_duplicate_created)
        self.assertEqual(task["task_id"], duplicate_task["task_id"])
        self.assertEqual(task["location_source"], "Registered property location for general site inspection")
        self.assertEqual(task["scheduled_for"], "2026-10-25T10:30")
        self.assertEqual((task["target_latitude"], task["target_longitude"]), (12.0, 77.0))
        self.assertEqual(task["task_status"], "Prepared - Awaiting Field Survey")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM surveillance_tasks").fetchone()[0], 1)
        events = connection.execute("SELECT * FROM case_audit_events ORDER BY event_id").fetchall()
        self.assertEqual(events[-1]["actor_identity"], "officer")
        self.assertIn("Pending Surveillance Review", [event["new_status"] for event in events])
        self.assertIn("Prepared - Awaiting Field Survey", [event["new_status"] for event in events])
        self.assertIn("confirmed the survey target", events[-1]["note"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
