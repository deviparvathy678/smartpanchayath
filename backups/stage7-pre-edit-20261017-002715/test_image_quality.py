"""Synthetic checks for quality gates, alignment, and the existing CV path."""

from io import BytesIO
import unittest

import cv2
import numpy as np
from PIL import Image

from change_detection import (
    ComparisonBlockedError,
    assess_comparison_inputs,
    compare_images,
)
from image_alignment import align_image_pair
from image_quality import assess_image_quality


def image_bytes(rgb, image_format="PNG"):
    output = BytesIO()
    Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(output, format=image_format)
    return output.getvalue()


def textured_scene(size=256):
    """Deterministic naturalistic texture with corners for ORB matching."""
    rng = np.random.default_rng(8721)
    image = np.zeros((size, size, 3), dtype=np.uint8)
    image[:] = (80, 115, 70)
    for _ in range(100):
        x, y = rng.integers(5, size - 20, size=2)
        w, h = rng.integers(5, 20, size=2)
        color = tuple(int(v) for v in rng.integers(20, 235, size=3))
        cv2.rectangle(image, (int(x), int(y)), (int(x + w), int(y + h)), color, -1)
    return image


class ImageQualityTests(unittest.TestCase):
    def setUp(self):
        self.scene = textured_scene()

    def test_two_valid_aligned_images_pass_and_run_change_pipeline(self):
        older = image_bytes(self.scene)
        newer_scene = self.scene.copy()
        cv2.rectangle(newer_scene, (80, 80), (140, 140), (225, 225, 225), -1)
        newer = image_bytes(newer_scene)
        assessment = assess_comparison_inputs(older, newer)
        self.assertTrue(assessment.ready, assessment.reason)
        self.assertEqual(assessment.alignment.status, "Pass")
        result = compare_images(older, newer, assessment=assessment)
        self.assertGreater(result.changed_pixel_percentage, 0)
        self.assertEqual(result.highlighted_rgb.shape, self.scene.shape)
        self.assertGreater(result.valid_pixel_percentage, 90)

    def test_corrupted_file_is_unsuitable(self):
        report = assess_image_quality(b"not an image")
        self.assertFalse(report.valid)
        self.assertEqual(report.status, "Unsuitable")
        self.assertTrue(report.error)

    def test_blank_image_is_unsuitable_and_cloud_status_unknown(self):
        report = assess_image_quality(image_bytes(np.zeros((128, 128, 3), dtype=np.uint8)))
        self.assertEqual(report.status, "Unsuitable")
        self.assertIn("Unknown", report.cloud_assessment)

    def test_blurry_image_is_flagged(self):
        detailed = textured_scene(256)
        blurry = cv2.GaussianBlur(detailed, (51, 51), 20)
        report = assess_image_quality(image_bytes(blurry))
        self.assertIn(report.status, ("Warning", "Unsuitable"))
        self.assertTrue(any("variance" in warning.lower() for warning in report.warnings))

    def test_low_contrast_is_flagged_without_claiming_weather(self):
        rng = np.random.default_rng(33)
        low = np.clip(120 + rng.integers(-5, 6, (256, 256, 1)), 0, 255)
        low = np.repeat(low, 3, axis=2).astype(np.uint8)
        report = assess_image_quality(image_bytes(low))
        self.assertTrue(any("Low contrast" in warning for warning in report.warnings))
        self.assertIn("cannot confirm", " ".join(report.warnings))

    def test_different_dimensions_align_without_axis_stretch(self):
        smaller = cv2.resize(self.scene, (192, 192), interpolation=cv2.INTER_AREA)
        assessment = assess_comparison_inputs(image_bytes(self.scene), image_bytes(smaller))
        self.assertTrue(assessment.ready, assessment.reason)
        self.assertEqual(assessment.compatibility.status, "Warning")
        self.assertTrue(any("proportional" in warning for warning in assessment.compatibility.warnings))

    def test_different_aspect_ratios_are_blocked(self):
        landscape = cv2.resize(self.scene, (256, 128))
        assessment = assess_comparison_inputs(image_bytes(self.scene), image_bytes(landscape))
        self.assertFalse(assessment.ready)
        self.assertEqual(assessment.compatibility.status, "Unsuitable")
        with self.assertRaises(ComparisonBlockedError):
            compare_images(image_bytes(self.scene), image_bytes(landscape), assessment=assessment)

    def test_synthetic_shift_and_rotation_are_aligned(self):
        transform = cv2.getRotationMatrix2D((128, 128), 4.0, 1.0)
        transform[0, 2] += 7
        transform[1, 2] -= 5
        moved = cv2.warpAffine(self.scene, transform, (256, 256))
        alignment = align_image_pair(self.scene, moved)
        self.assertEqual(alignment.status, "Pass", alignment.reason)
        self.assertGreaterEqual(alignment.inliers, 8)
        self.assertIsNotNone(alignment.aligned_newer_rgb)

    def test_feature_matching_failure_blocks_comparison(self):
        old = np.full((256, 256, 3), 120, dtype=np.uint8)
        new = old.copy()
        new[100:110, 100:110] = 123
        alignment = align_image_pair(old, new)
        self.assertEqual(alignment.status, "Unsuitable")
        self.assertIn("Feature alignment failed", alignment.reason)
        assessment = assess_comparison_inputs(image_bytes(old), image_bytes(new))
        self.assertFalse(assessment.ready)

    def test_unsuitable_quality_blocks_but_warning_quality_remains_flagged(self):
        blank = image_bytes(np.zeros((256, 256, 3), dtype=np.uint8))
        good = image_bytes(self.scene)
        blocked = assess_comparison_inputs(blank, good)
        self.assertFalse(blocked.ready)
        self.assertEqual(blocked.status, "Unsuitable")

        small = image_bytes(cv2.resize(self.scene, (64, 64)))
        marginal = assess_comparison_inputs(small, small)
        self.assertTrue(marginal.ready)
        self.assertEqual(marginal.status, "Warning")
        self.assertTrue(any("Small image" in warning for warning in marginal.warnings))

    def test_pair_warnings_are_not_reported_as_pass(self):
        brighter = np.clip(self.scene.astype(np.int16) + 45, 0, 255).astype(np.uint8)
        assessment = assess_comparison_inputs(image_bytes(self.scene), image_bytes(brighter))
        self.assertTrue(assessment.ready)
        self.assertEqual(assessment.status, "Warning")
        self.assertTrue(any("brightness" in warning.lower() for warning in assessment.warnings))


if __name__ == "__main__":
    unittest.main()
