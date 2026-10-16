"""Heuristic image-quality checks for SmartPanchayat image comparisons.

Image pixels alone cannot prove that haze, mist, fog, clouds, or shadows are
weather phenomena. The thresholds below are screening heuristics and should be
adjusted for the sensor and image type being used.
"""

from dataclasses import dataclass, field, fields
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError


# Central configuration. Blur and contrast cutoffs are not universal sensor
# thresholds; they are conservative indicators for officer review.
QUALITY_THRESHOLDS = {
    "max_image_bytes": 10 * 1024 * 1024,
    "max_image_pixels": 20_000_000,
    "minimum_dimension_unsuitable": 16,
    "minimum_dimension_warning": 96,
    "blur_unsuitable": 2.0,
    "blur_warning": 25.0,
    "contrast_unsuitable": 2.0,
    "contrast_warning": 14.0,
    "blank_extreme_share": 0.985,
    "large_extreme_region_warning": 0.35,
    "dark_pixel_threshold": 35,
    "dark_pixel_share_warning": 0.65,
    "very_dark_mean": 25,
    "overexposed_mean": 238,
    "aspect_ratio_difference_unsuitable": 0.05,
    "resolution_ratio_warning": 2.0,
}


@dataclass
class ImageQualityReport:
    valid: bool
    status: str
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    image_format: str | None = None
    blur_score: float | None = None
    contrast_std: float | None = None
    contrast_percentile_spread: float | None = None
    brightness_mean: float | None = None
    dark_pixel_percentage: float | None = None
    extreme_pixel_percentage: float | None = None
    cloud_assessment: str = "Unknown — no reliable cloud detector for uploaded images."
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    error: str | None = None
    # The decoded array is retained only during this app session; it is not
    # serialized to SQLite or included in as_dict().
    rgb: np.ndarray | None = field(default=None, repr=False, compare=False)

    def as_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self) if item.name != "rgb"}


@dataclass
class PairCompatibilityReport:
    status: str
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    aspect_ratio_difference: float | None = None
    dimension_scale_ratio: float | None = None
    reason: str | None = None

    def as_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


def _status(warnings, unsuitable=False):
    if unsuitable:
        return "Unsuitable"
    return "Warning" if warnings else "Pass"


def assess_rgb_quality(
    rgb,
    *,
    image_format="Array",
    channels=None,
    valid_mask=None,
    cloud_assessment=None,
):
    """Assess an already-decoded RGB image (also used for geospatial AOIs)."""
    warnings = []
    recommendations = []
    try:
        array = np.asarray(rgb)
        if array.ndim != 3 or array.shape[2] < 3 or array.shape[0] < 2 or array.shape[1] < 2:
            raise ValueError("Image must have at least 2 by 2 pixels and three color channels.")
        height, width = array.shape[:2]
        if valid_mask is None:
            valid = np.ones((height, width), dtype=bool)
        else:
            valid = np.asarray(valid_mask, dtype=bool)
            if valid.shape != (height, width):
                raise ValueError("Valid-pixel mask does not match image dimensions.")
        if not np.any(valid):
            raise ValueError("Image contains no valid pixels.")
        if not np.isfinite(array[valid]).all():
            raise ValueError("Image contains non-finite values in pixels marked valid.")

        color = np.where(np.isfinite(array[:, :, :3]), array[:, :, :3], 0)
        if np.issubdtype(color.dtype, np.floating):
            low, high = np.percentile(color[valid], (1, 99))
            if 0 <= low and high <= 1.5:
                color8 = np.clip(color * 255, 0, 255).astype(np.uint8)
            else:
                color8 = np.clip(color, 0, 255).astype(np.uint8)
        else:
            color8 = np.clip(color, 0, 255).astype(np.uint8)
        gray = cv2.cvtColor(color8, cv2.COLOR_RGB2GRAY)
        valid_gray = gray[valid]
        p5, p95 = np.percentile(valid_gray, (5, 95))
        contrast_std = float(np.std(valid_gray))
        contrast_spread = float(p95 - p5)
        brightness = float(np.mean(valid_gray))

        # Laplacian variance is scale and sensor dependent. Resize for a
        # predictable calculation cost, while keeping its heuristic caveat.
        scale = min(1.0, 1024.0 / max(width, height))
        if scale < 1:
            sample = cv2.resize(
                gray, (max(16, round(width * scale)), max(16, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            sample = gray
        blur_score = float(cv2.Laplacian(sample, cv2.CV_64F).var())

        dark_share = float(np.count_nonzero(valid_gray < QUALITY_THRESHOLDS["dark_pixel_threshold"]) / valid_gray.size)
        extreme = np.all((color8 <= 1) | (color8 >= 254), axis=2) & valid
        extreme_share = float(np.count_nonzero(extreme) / valid.sum())
        low, high = QUALITY_THRESHOLDS["minimum_dimension_unsuitable"], QUALITY_THRESHOLDS["minimum_dimension_warning"]
        unsuitable = False

        if min(width, height) < low:
            warnings.append(f"Very small image ({width}×{height}); too few pixels for reliable screening.")
            unsuitable = True
            recommendations.append("Use a higher-resolution image with at least 16 pixels in each dimension.")
        elif min(width, height) < high:
            warnings.append(f"Small image ({width}×{height}); fine building details may not be resolved.")
            recommendations.append("Use a larger image where available.")

        if extreme_share >= QUALITY_THRESHOLDS["blank_extreme_share"] and contrast_std < 2.0:
            warnings.append("Image is almost entirely blank or uniformly black/white; it may be missing, overexposed, or empty.")
            unsuitable = True
            recommendations.append("Check that the source file contains the intended scene and valid pixel coverage.")
        elif extreme_share >= QUALITY_THRESHOLDS["large_extreme_region_warning"]:
            warnings.append(f"A large share of pixels ({extreme_share * 100:.1f}%) is near black or white; blank/no-data borders or exposure problems may be present.")
            recommendations.append("Inspect the image for no-data areas, blank borders, and clipped exposure.")

        if blur_score < QUALITY_THRESHOLDS["blur_unsuitable"] and min(width, height) >= high:
            warnings.append(f"Very low Laplacian variance ({blur_score:.1f}) indicates severe blur or little image detail.")
            unsuitable = True
            recommendations.append("Use a sharper image; blur score is a heuristic, not a universal sensor threshold.")
        elif blur_score < QUALITY_THRESHOLDS["blur_warning"]:
            warnings.append(f"Low Laplacian variance ({blur_score:.1f}) may indicate blur or a low-texture scene.")
            recommendations.append("Inspect edges and building detail; this blur metric is sensor and scene dependent.")

        if contrast_std < QUALITY_THRESHOLDS["contrast_warning"] or contrast_spread < 20:
            warnings.append("Low contrast may be caused by haze, mist, fog, poor exposure, or sensor differences; pixels cannot confirm the cause.")
            recommendations.append("Prefer a clear image with more contrast and similar capture conditions.")

        if brightness < QUALITY_THRESHOLDS["very_dark_mean"]:
            warnings.append(f"Image is very dark (mean brightness {brightness:.1f}/255); shadow or underexposure may obscure details.")
            recommendations.append("Use an image with better illumination if available; dark areas are not classified as clouds or buildings.")
        elif brightness > QUALITY_THRESHOLDS["overexposed_mean"]:
            warnings.append(f"Image is very bright (mean brightness {brightness:.1f}/255); overexposure may hide detail.")
            recommendations.append("Use an image without clipped highlights if available.")
        if dark_share >= QUALITY_THRESHOLDS["dark_pixel_share_warning"]:
            warnings.append(f"Many pixels ({dark_share * 100:.1f}%) are dark; shadows or uneven illumination may affect comparison.")
            recommendations.append("Review dark regions manually; this is not a cloud-shadow detector.")

        cloud_status = cloud_assessment or "Unknown — no reliable cloud detector for this image."
        return ImageQualityReport(
            valid=True,
            status=_status(warnings, unsuitable),
            width=width,
            height=height,
            channels=channels or int(array.shape[2]),
            image_format=image_format,
            blur_score=blur_score,
            contrast_std=contrast_std,
            contrast_percentile_spread=contrast_spread,
            brightness_mean=brightness,
            dark_pixel_percentage=dark_share * 100,
            extreme_pixel_percentage=extreme_share * 100,
            cloud_assessment=cloud_status,
            warnings=warnings,
            recommendations=recommendations,
            rgb=color8,
        )
    except (ValueError, TypeError, cv2.error) as error:
        return ImageQualityReport(
            valid=False,
            status="Unsuitable",
            cloud_assessment=cloud_assessment or "Unknown — no reliable cloud detector for this image.",
            warnings=[str(error)],
            recommendations=["Choose a valid, readable image with non-empty pixel data."],
            error=str(error),
        )


def assess_image_quality(image_bytes):
    """Validate and assess uploaded image bytes without throwing UI errors."""
    try:
        if not image_bytes:
            raise ValueError("Image file is empty.")
        if len(image_bytes) > QUALITY_THRESHOLDS["max_image_bytes"]:
            raise ValueError("Image file exceeds the 10 MB upload limit.")
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = image.format or "Unknown"
            channels = len(image.getbands())
            if width * height > QUALITY_THRESHOLDS["max_image_pixels"]:
                raise ValueError("Image exceeds the 20 megapixel processing limit.")
            rgb = np.asarray(image.convert("RGB"))
        report = assess_rgb_quality(rgb, image_format=image_format, channels=channels)
        return report
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as error:
        return ImageQualityReport(
            valid=False,
            status="Unsuitable",
            cloud_assessment="Unknown — no reliable cloud detector for uploaded images.",
            warnings=[str(error)],
            recommendations=["Upload a readable JPG or PNG image that contains the intended scene."],
            error=str(error),
        )


def assess_pair_compatibility(earlier, newer):
    """Check dimensions/aspect compatibility without pretending they georegister images."""
    warnings = []
    recommendations = []
    if not earlier.valid or not newer.valid:
        return PairCompatibilityReport(
            "Unsuitable", reason="Both images must be valid before comparison.",
            warnings=["At least one image is invalid or unreadable."],
        )
    old_ratio = earlier.width / earlier.height
    new_ratio = newer.width / newer.height
    aspect_delta = abs(old_ratio - new_ratio) / max(old_ratio, 1e-9)
    old_scale = max(earlier.width / newer.width, newer.width / earlier.width,
                    earlier.height / newer.height, newer.height / earlier.height)
    if aspect_delta > QUALITY_THRESHOLDS["aspect_ratio_difference_unsuitable"]:
        return PairCompatibilityReport(
            "Unsuitable", aspect_ratio_difference=aspect_delta,
            dimension_scale_ratio=old_scale,
            warnings=[f"Aspect ratios differ by {aspect_delta * 100:.1f}%; the images may show different crops or viewing geometry."],
            recommendations=["Use images framed to the same area and similar viewing angle."],
            reason="Aspect-ratio mismatch is too large for a reliable comparison.",
        )
    if earlier.width != newer.width or earlier.height != newer.height:
        warnings.append("Image dimensions differ; alignment will use proportional resampling and crop/pad, not a stretched resize.")
        recommendations.append("Use matching image dimensions and scale where possible.")
    if old_scale > QUALITY_THRESHOLDS["resolution_ratio_warning"]:
        warnings.append(f"Pixel dimensions differ by a scale factor of {old_scale:.1f}; fine-detail comparisons may be unreliable.")
        recommendations.append("Use images from similar resolutions or the same sensor when possible.")
    if abs(earlier.brightness_mean - newer.brightness_mean) > 35:
        warnings.append("Average brightness differs substantially; lighting or exposure changes may inflate pixel differences.")
        recommendations.append("Review illumination and shadow differences before interpreting the mask.")
    status = "Warning" if warnings else "Pass"
    return PairCompatibilityReport(
        status=status,
        warnings=warnings,
        recommendations=recommendations,
        aspect_ratio_difference=aspect_delta,
        dimension_scale_ratio=old_scale,
    )
