"""Synthetic GeoTIFF and unreferenced image upload checks."""

from datetime import date
from io import BytesIO
import unittest

import numpy as np
from PIL import Image

from aerial_imagery import ImageryUploadError, read_aerial_upload, validate_aerial_pair

try:
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False


def geotiff(resolution=0.25, *, crs="EPSG:32643", x=500000, valid=True, acquired="2025-01-01"):
    profile = {"driver": "GTiff", "height": 128, "width": 128, "count": 3,
               "dtype": "uint8", "crs": crs,
               "transform": from_origin(x, 2200000, resolution, resolution)}
    with MemoryFile() as memory:
        with memory.open(**profile) as dataset:
            dataset.write(np.full((3, 128, 128), 80, dtype=np.uint8))
            dataset.update_tags(acquisition_date=acquired)
            if not valid:
                mask = np.zeros((128, 128), dtype=np.uint8)
                mask[:60, :] = 255
                dataset.write_mask(mask)
        return memory.read()


@unittest.skipUnless(HAS_RASTERIO, "Rasterio is required for GeoTIFF tests")
class AerialUploadTests(unittest.TestCase):
    def test_geotiff_reports_metadata_and_valid_mask(self):
        image = read_aerial_upload(geotiff(), "survey.tif")
        self.assertEqual(image.acquisition_date, date(2025, 1, 1))
        self.assertTrue(image.georeferenced)
        self.assertEqual(image.crs, "EPSG:32643")
        self.assertAlmostEqual(image.resolution_m, 0.25, delta=0.01)
        self.assertEqual(image.valid_data_percentage, 100)
        self.assertTrue(image.supports_building_level_review)
        self.assertTrue(image.valid_mask.all())

    def test_resolution_capability_blocks_coarse_building_review(self):
        old = read_aerial_upload(geotiff(10, acquired="2024-01-01"), "old.tif")
        new = read_aerial_upload(geotiff(10), "new.tif")
        result = validate_aerial_pair(old, new)
        self.assertFalse(result.building_level_allowed)
        self.assertIn("large changes only", result.resolution_capability)

    def test_rejects_missing_date_crs_and_low_valid_coverage(self):
        with self.assertRaisesRegex(ImageryUploadError, "Acquisition date"):
            read_aerial_upload(geotiff(acquired=""), "survey.tif")
        no_crs_profile = {"driver": "GTiff", "height": 4, "width": 4, "count": 3, "dtype": "uint8"}
        with MemoryFile() as memory:
            with memory.open(**no_crs_profile) as dataset:
                dataset.write(np.ones((3, 4, 4), dtype=np.uint8))
            with self.assertRaisesRegex(ImageryUploadError, "CRS"):
                read_aerial_upload(memory.read(), "no-crs.tif", "2025-01-01")
        old = read_aerial_upload(geotiff(acquired="2024-01-01"), "old.tif")
        invalid = read_aerial_upload(geotiff(valid=False), "invalid.tif")
        with self.assertRaisesRegex(ImageryUploadError, "valid pixels"):
            validate_aerial_pair(old, invalid)

    def test_unreferenced_image_requires_date_and_disables_geographic_review(self):
        output = BytesIO()
        Image.fromarray(np.full((128, 128, 3), 100, dtype=np.uint8)).save(output, format="PNG")
        with self.assertRaisesRegex(ImageryUploadError, "acquisition date"):
            read_aerial_upload(output.getvalue(), "manual.png")
        old = read_aerial_upload(output.getvalue(), "manual.png", "2024-01-01")
        new = read_aerial_upload(output.getvalue(), "manual.png", "2025-01-01")
        result = validate_aerial_pair(old, new)
        self.assertTrue(result.comparison_allowed)
        self.assertFalse(result.building_level_allowed)
        self.assertIsNone(result.overlap_percentage)
