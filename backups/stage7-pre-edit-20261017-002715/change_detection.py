"""Image-only comparison helpers with quality and alignment gates.

This module does not use a trained model and does not determine whether a
construction change is permitted or unlawful.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from image_alignment import AlignmentReport, align_image_pair
from image_quality import (
    ImageQualityReport,
    PairCompatibilityReport,
    assess_image_quality,
    assess_pair_compatibility,
)


MAX_IMAGE_PIXELS = 20_000_000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MIN_COMPONENT_PIXELS = 64
DETECTION_METHOD = "ORB + RANSAC affine alignment + CLAHE/Lab pixel difference (OpenCV heuristic)"


@dataclass
class ComparisonAssessment:
    earlier_quality: ImageQualityReport
    newer_quality: ImageQualityReport
    compatibility: PairCompatibilityReport
    alignment: AlignmentReport | None
    ready: bool
    status: str
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    earlier_rgb: np.ndarray
    newer_rgb: np.ndarray
    aligned_newer_rgb: np.ndarray
    mask: np.ndarray
    highlighted_rgb: np.ndarray
    changed_pixel_percentage: float
    alignment_note: str
    assessment: ComparisonAssessment
    valid_pixel_percentage: float


class ComparisonBlockedError(ValueError):
    """Raised when image quality or alignment is not suitable for differencing."""

    def __init__(self, message, assessment):
        super().__init__(message)
        self.assessment = assessment


def assess_comparison_inputs(earlier_bytes, newer_bytes):
    """Assess both uploaded files and alignment without running change detection."""
    earlier = assess_image_quality(earlier_bytes)
    newer = assess_image_quality(newer_bytes)
    compatibility = assess_pair_compatibility(earlier, newer)
    warnings = list(earlier.warnings) + list(newer.warnings) + list(compatibility.warnings)
    recommendations = list(earlier.recommendations) + list(newer.recommendations) + list(compatibility.recommendations)
    if not earlier.valid or not newer.valid:
        reason = "One or both image files are invalid or unreadable."
        return ComparisonAssessment(earlier, newer, compatibility, None, False, "Unsuitable", reason, warnings, recommendations)
    if earlier.status == "Unsuitable" or newer.status == "Unsuitable" or compatibility.status == "Unsuitable":
        reason = compatibility.reason or "One or both images failed the configured minimum quality checks."
        return ComparisonAssessment(earlier, newer, compatibility, None, False, "Unsuitable", reason, warnings, recommendations)
    try:
        alignment = align_image_pair(earlier.rgb, newer.rgb)
    except cv2.error as error:
        reason = f"Image alignment failed: {error}"
        return ComparisonAssessment(earlier, newer, compatibility, None, False, "Unsuitable", reason, warnings + [reason], recommendations)
    warnings.extend(alignment.warnings)
    recommendations.extend(alignment.recommendations)
    ready = alignment.status == "Pass"
    if not ready:
        reason = alignment.reason or "The images could not be aligned reliably."
    else:
        reason = None
    has_warning = bool(warnings) or earlier.status == "Warning" or newer.status == "Warning" or compatibility.status == "Warning"
    status = "Unsuitable" if not ready else ("Warning" if has_warning else "Pass")
    return ComparisonAssessment(earlier, newer, compatibility, alignment, ready, status, reason, warnings, recommendations)


def compare_images(earlier_bytes, newer_bytes, *, assessment=None):
    """Compare two images only after quality, compatibility, and alignment pass."""
    if len(earlier_bytes) > MAX_UPLOAD_BYTES or len(newer_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Each image must be 10 MB or smaller.")
    assessment = assessment or assess_comparison_inputs(earlier_bytes, newer_bytes)
    if not assessment.ready or assessment.alignment is None:
        raise ComparisonBlockedError(assessment.reason or "Image comparison is blocked by quality or alignment checks.", assessment)

    earlier_rgb = assessment.earlier_quality.rgb
    newer_rgb = assessment.newer_quality.rgb
    aligned_newer_rgb = assessment.alignment.aligned_newer_rgb
    valid = assessment.alignment.valid_mask > 0
    if earlier_rgb is None or newer_rgb is None or aligned_newer_rgb is None or not np.any(valid):
        raise ComparisonBlockedError("No valid aligned image pixels are available for comparison.", assessment)

    earlier_lab = cv2.cvtColor(earlier_rgb, cv2.COLOR_RGB2LAB)
    newer_lab = cv2.cvtColor(aligned_newer_rgb, cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    earlier_lab[:, :, 0] = clahe.apply(earlier_lab[:, :, 0])
    newer_lab[:, :, 0] = clahe.apply(newer_lab[:, :, 0])
    channel_difference = cv2.absdiff(earlier_lab, newer_lab)
    lightness_difference = channel_difference[:, :, 0]
    chroma_difference = cv2.max(channel_difference[:, :, 1], channel_difference[:, :, 2])
    score_gray = cv2.addWeighted(lightness_difference, 0.55, chroma_difference, 0.45, 0)
    score_gray = cv2.GaussianBlur(score_gray, (5, 5), 0)
    _, mask = cv2.threshold(score_gray, 22, 255, cv2.THRESH_BINARY)
    mask[~valid] = 0

    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    minimum_area = max(MIN_COMPONENT_PIXELS, int(mask.size * 0.00002))
    filtered_mask = np.zeros_like(mask)
    for component in range(1, component_count):
        if stats[component, cv2.CC_STAT_AREA] >= minimum_area:
            filtered_mask[labels == component] = 255
    filtered_mask[~valid] = 0

    valid_count = int(np.count_nonzero(valid))
    changed_pixels = int(np.count_nonzero(filtered_mask))
    percentage = changed_pixels / max(1, valid_count) * 100
    highlighted = aligned_newer_rgb.copy()
    changed = filtered_mask > 0
    highlighted[changed] = (0.45 * highlighted[changed] + 0.55 * np.array([255, 0, 0])).astype(np.uint8)
    valid_percentage = valid_count / filtered_mask.size * 100
    note = assessment.alignment.transform_description
    return ComparisonResult(
        earlier_rgb=earlier_rgb,
        newer_rgb=newer_rgb,
        aligned_newer_rgb=aligned_newer_rgb,
        mask=filtered_mask,
        highlighted_rgb=highlighted,
        changed_pixel_percentage=percentage,
        alignment_note=note,
        assessment=assessment,
        valid_pixel_percentage=valid_percentage,
    )
