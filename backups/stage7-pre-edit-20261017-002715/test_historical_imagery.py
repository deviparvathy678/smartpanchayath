"""Offline checks for geospatial screening and provider response handling."""

import unittest
from datetime import date
from unittest.mock import Mock

import numpy as np

from historical_comparison import compare_geographic_images
from imagery_sources import (
    Element84SentinelProvider,
    GeoImage,
    MockProvider,
    NoImageryFound,
    ProviderError,
    SceneResult,
)


def geo(rgb, *, scl=None, bbox=(-73.01, 40.0, -73.0, 40.01),
        acquired=date(2024, 1, 1), resolution=10.0, crs="EPSG:32618"):
    return GeoImage(np.asarray(rgb, dtype=np.float32), scl,
                    "synthetic-transform", crs, bbox, resolution,
                    acquired, acquired.isoformat(), "OFFLINE SYNTHETIC TEST")


class HistoricalComparisonTests(unittest.TestCase):
    def test_identical_images_have_near_zero_score(self):
        source = np.full((64, 64, 3), 0.4, np.float32)
        result = compare_geographic_images(geo(source), geo(source, acquired=date(2025, 1, 1)))
        self.assertEqual(result.changed_pixel_percentage, 0.0)
        self.assertEqual(result.mask.shape, (64, 64))

    def test_synthetic_large_change_produces_mask_and_score(self):
        old = np.full((64, 64, 3), 0.2, np.float32)
        new = old.copy()
        new[20:45, 20:45, :] = 0.9
        result = compare_geographic_images(geo(old), geo(new, acquired=date(2025, 1, 1)))
        self.assertGreater(result.changed_pixel_percentage, 0)
        self.assertEqual(result.highlighted_rgb.shape, (64, 64, 3))

    def test_cloud_and_missing_pixels_are_masked(self):
        rgb = np.full((64, 64, 3), 0.4, np.float32)
        scl = np.ones((64, 64), np.uint8)
        scl[:32, :] = 9  # Sentinel-2 SCL high-probability cloud.
        result = compare_geographic_images(geo(rgb, scl=scl), geo(rgb, scl=scl, acquired=date(2025, 1, 1)))
        self.assertGreater(result.cloud_percentage, 40)
        self.assertTrue(any("High cloud" in warning for warning in result.warnings))

    def test_footprint_and_resolution_mismatch_are_rejected(self):
        rgb = np.full((32, 32, 3), 0.4, np.float32)
        with self.assertRaisesRegex(ValueError, "footprints"):
            compare_geographic_images(geo(rgb), geo(rgb, bbox=(1, 1, 2, 2), acquired=date(2025, 1, 1)))
        with self.assertRaisesRegex(ValueError, "resolution"):
            compare_geographic_images(geo(rgb), geo(rgb, resolution=30, acquired=date(2025, 1, 1)))

    def test_large_aspect_shape_or_missing_valid_area_rejected(self):
        with self.assertRaisesRegex(ValueError, "common pixel grid"):
            compare_geographic_images(geo(np.ones((32, 32, 3))), geo(np.ones((32, 20, 3)), acquired=date(2025, 1, 1)))
        zeros = np.zeros((32, 32, 3), np.float32)
        with self.assertRaisesRegex(ValueError, "valid"):
            compare_geographic_images(geo(zeros), geo(zeros, acquired=date(2025, 1, 1)))

    def test_mock_provider_is_explicitly_offline_and_empty_is_typed(self):
        scene = SceneResult("mock-a", date(2020, 1, 1), "OFFLINE TEST MOCK", 10, 0,
                            {}, "EPSG:32618", "synthetic", "test only", {}, {})
        provider = MockProvider([scene], {})
        self.assertEqual(provider.search(0, 0, date(2019, 1, 1), date(2021, 1, 1))[0], scene)
        with self.assertRaises(NoImageryFound):
            MockProvider().search(0, 0, date(2019, 1, 1), date(2021, 1, 1))


class ProviderMetadataTests(unittest.TestCase):
    def test_stac_result_metadata_is_parsed(self):
        provider = Element84SentinelProvider()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"features": [{
            "id": "scene-1", "geometry": {"type": "Polygon", "coordinates": []},
            "properties": {"datetime": "2024-06-01T10:00:00Z", "eo:cloud_cover": 4.0,
                           "proj:epsg": 32618, "license": "proprietary"},
            "assets": {"red": {"href": "https://example.test/red.tif"},
                       "green": {"href": "https://example.test/green.tif"},
                       "blue": {"href": "https://example.test/blue.tif"}},
        }]}
        provider.session.post = Mock(return_value=response)
        scenes = provider.search(40, -73, date(2024, 6, 1), date(2024, 6, 30), 20)
        self.assertEqual(scenes[0].scene_id, "scene-1")
        self.assertEqual(scenes[0].cloud_cover_pct, 4.0)
        self.assertIn("red", scenes[0].assets)

    def test_empty_catalog_has_explicit_result(self):
        provider = Element84SentinelProvider()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"features": []}
        provider.session.post = Mock(return_value=response)
        with self.assertRaises(NoImageryFound):
            provider.search(40, -73, date(2024, 1, 1), date(2024, 1, 31), 20)

    def test_bad_catalog_payload_is_rejected(self):
        provider = Element84SentinelProvider()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"features": [{"id": "without properties"}]}
        provider.session.post = Mock(return_value=response)
        with self.assertRaises(ProviderError):
            provider.search(40, -73, date(2024, 1, 1), date(2024, 1, 31), 20)


if __name__ == "__main__":
    unittest.main()
