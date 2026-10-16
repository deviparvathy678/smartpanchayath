"""Coarse, georeferenced Sentinel-2 visual-change screening helpers."""

from dataclasses import dataclass

import cv2
import numpy as np
from image_quality import assess_rgb_quality


@dataclass
class HistoricalComparison:
    earlier_rgb: np.ndarray
    newer_rgb: np.ndarray
    highlighted_rgb: np.ndarray
    mask: np.ndarray
    changed_pixel_percentage: float
    valid_pixel_percentage: float
    cloud_percentage: float | None
    warnings: list[str]
    method: str = "Sentinel-2 L2A RGB differencing (OpenCV; preliminary screening)"
    earlier_quality: object | None = None
    newer_quality: object | None = None
    alignment_status: str = "Unknown"
    newer_before_alignment_rgb: np.ndarray | None = None
    alignment_applied: bool = False


def _overlap_ratio(a, b):
    left, bottom = max(a[0], b[0]), max(a[1], b[1])
    right, top = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or top <= bottom:
        return 0.0
    area = (right - left) * (top - bottom)
    return area / max(1e-15, min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])))


def compare_geographic_images(earlier, newer):
    """Compare AOIs already resampled by the provider onto a common grid.

    Clouds/shadows/snow and water are excluded when SCL data exists. The score
    is the fraction of usable pixels marked different, not built-up area.
    """
    warnings = []
    if _overlap_ratio(earlier.bbox_wgs84, newer.bbox_wgs84) < 0.90:
        raise ValueError("The two imagery footprints overlap by less than 90%; comparison rejected.")
    if earlier.crs != newer.crs:
        raise ValueError("Images are in different coordinate systems; reproject them to a common grid first.")
    if earlier.rgb.shape != newer.rgb.shape:
        raise ValueError("Images do not share a common pixel grid.")
    if abs(earlier.resolution_m - newer.resolution_m) / max(earlier.resolution_m, newer.resolution_m) > 0.5:
        raise ValueError("Image resolution differs too much for a reliable comparison.")
    day_gap = abs((newer.acquisition_date - earlier.acquisition_date).days)
    if day_gap > 120:
        warnings.append("Large seasonal gap between acquisitions may create vegetation and lighting differences.")
    if earlier.rgb.shape[0] < 10 or earlier.rgb.shape[1] < 10:
        raise ValueError("AOI images are too small to compare.")

    old = np.asarray(earlier.rgb, dtype=np.float32)
    new = np.asarray(newer.rgb, dtype=np.float32)
    old_valid = np.isfinite(old).all(axis=2) & (old.max(axis=2) > 0)
    new_valid = np.isfinite(new).all(axis=2) & (new.max(axis=2) > 0)
    valid = old_valid & new_valid
    cloud = np.zeros(valid.shape, dtype=bool)
    if earlier.scl is not None:
        scl = np.asarray(earlier.scl)
        cloud |= np.isin(scl, (3, 8, 9, 10, 11))
        valid &= ~cloud & (scl != 0)
    if newer.scl is not None:
        scl = np.asarray(newer.scl)
        cloud |= np.isin(scl, (3, 8, 9, 10, 11))
        valid &= ~cloud & (scl != 0)
    if earlier.scl is not None:
        valid &= np.asarray(earlier.scl) != 6
    if newer.scl is not None:
        valid &= np.asarray(newer.scl) != 6
    valid_pct = float(np.count_nonzero(valid) / valid.size * 100)
    has_scl = earlier.scl is not None or newer.scl is not None
    cloud_pct = float(np.count_nonzero(cloud) / cloud.size * 100) if has_scl else None
    if valid_pct < 40:
        raise ValueError(f"Only {valid_pct:.1f}% of pixels are valid after missing-data/cloud masking; comparison rejected.")
    if cloud_pct is None:
        warnings.append("SCL data was unavailable; cloud/shadow masking could not be measured.")
    elif cloud_pct > 40:
        warnings.append(f"High cloud/shadow/snow contamination ({cloud_pct:.1f}%); result is unsuitable for confident screening.")

    old_quality_valid = old_valid
    new_quality_valid = new_valid
    if earlier.scl is not None:
        old_quality_valid &= np.asarray(earlier.scl) != 0
    if newer.scl is not None:
        new_quality_valid &= np.asarray(newer.scl) != 0
    old_scene_cloud = (
        float(np.isin(np.asarray(earlier.scl), (3, 8, 9, 10, 11)).mean() * 100)
        if earlier.scl is not None else None
    )
    new_scene_cloud = (
        float(np.isin(np.asarray(newer.scl), (3, 8, 9, 10, 11)).mean() * 100)
        if newer.scl is not None else None
    )
    old_cloud_label = (
        f"SCL cloud/shadow/snow classes: {old_scene_cloud:.1f}%"
        if old_scene_cloud is not None else "Unknown — SCL cloud data unavailable."
    )
    new_cloud_label = (
        f"SCL cloud/shadow/snow classes: {new_scene_cloud:.1f}%"
        if new_scene_cloud is not None else "Unknown — SCL cloud data unavailable."
    )
    earlier_quality = assess_rgb_quality(
        earlier.rgb, image_format="Georeferenced satellite AOI",
        valid_mask=old_quality_valid, cloud_assessment=old_cloud_label,
    )
    newer_quality = assess_rgb_quality(
        newer.rgb, image_format="Georeferenced satellite AOI",
        valid_mask=new_quality_valid, cloud_assessment=new_cloud_label,
    )
    for label, quality in (("Earlier", earlier_quality), ("Newer", newer_quality)):
        warnings.extend(f"{label} image quality: {warning}" for warning in quality.warnings)
        if quality.status == "Unsuitable":
            raise ValueError(f"{label} geospatial image is unsuitable: " + "; ".join(quality.warnings))

    # Per-band robust normalization reduces broad exposure differences.
    norm_images = []
    for image in (old, new):
        channels = []
        for channel in range(3):
            values = image[:, :, channel][valid]
            lo, hi = np.percentile(values, (2, 98)) if values.size else (0, 1)
            channels.append(np.clip((image[:, :, channel] - lo) / max(float(hi-lo), 1e-5), 0, 1))
        norm_images.append(np.stack(channels, axis=2))
    old_n, new_n = norm_images
    new_before_alignment = new_n.copy()

    stable_vegetation = np.zeros(valid.shape, dtype=bool)
    if earlier.nir is not None and newer.nir is not None:
        old_red = old[:, :, 0]
        new_red = new[:, :, 0]
        old_ndvi = (np.asarray(earlier.nir) - old_red) / (np.asarray(earlier.nir) + old_red + 1e-6)
        new_ndvi = (np.asarray(newer.nir) - new_red) / (np.asarray(newer.nir) + new_red + 1e-6)
        # Suppress differences where both dates remain strongly vegetated;
        # clearing that changes one date's vegetation index remains visible.
        stable_vegetation = (old_ndvi > 0.45) & (new_ndvi > 0.45)
        warnings.append("NDVI screening suppressed differences where both dates remain strongly vegetated.")
    else:
        warnings.append("NIR bands were unavailable; vegetation-index screening was not applied.")

    # Accept ECC translation only if it measurably improves correlation; the
    # masked baseline remains the safe fallback. Cap movement to 3 pixels.
    alignment_note = "Common AOI grid; no image registration needed."
    alignment_applied = False
    try:
        old_gray = cv2.cvtColor(old_n.astype(np.float32), cv2.COLOR_RGB2GRAY)
        new_gray = cv2.cvtColor(new_n.astype(np.float32), cv2.COLOR_RGB2GRAY)
        warp = np.eye(2, 3, dtype=np.float32)
        score, candidate = cv2.findTransformECC(old_gray, new_gray, warp,
                                                cv2.MOTION_TRANSLATION,
                                                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5))
        dx, dy = float(candidate[0, 2]), float(candidate[1, 2])
        before = np.corrcoef(old_gray[valid], new_gray[valid])[0, 1]
        aligned_gray = cv2.warpAffine(new_gray, candidate, (new_gray.shape[1], new_gray.shape[0]),
                                      flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                      borderMode=cv2.BORDER_CONSTANT)
        aligned_valid = cv2.warpAffine(
            valid.astype(np.uint8), candidate, (new_gray.shape[1], new_gray.shape[0]),
            flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
        ).astype(bool)
        joint_valid = valid & aligned_valid
        after = (
            np.corrcoef(old_gray[joint_valid], aligned_gray[joint_valid])[0, 1]
            if int(joint_valid.sum()) >= 16 else float("nan")
        )
        if np.isfinite(score) and abs(dx) <= 3 and abs(dy) <= 3 and np.isfinite(after) and np.isfinite(before) and after > before + 0.01:
            new_n = cv2.warpAffine(new_n, candidate, (new_n.shape[1], new_n.shape[0]),
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_CONSTANT)
            valid = joint_valid
            alignment_note = f"Small translation accepted after correlation improved (ECC {score:.3f})."
            alignment_applied = True
    except (cv2.error, ValueError, IndexError):
        alignment_note = "ECC alignment was unavailable or did not converge; common geospatial grid retained."
    warnings.append(alignment_note)

    old_lab = cv2.cvtColor((old_n * 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    new_lab = cv2.cvtColor((new_n * 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    # Blur and color-normalized Lab distance suppress isolated noise.
    distance = np.linalg.norm(old_lab - new_lab, axis=2)
    score = cv2.GaussianBlur(distance.astype(np.float32), (3, 3), 0)
    candidate_mask = (score > 24) & valid & ~stable_vegetation
    mask8 = (candidate_mask.astype(np.uint8) * 255)
    mask8 = cv2.morphologyEx(mask8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask8 = cv2.morphologyEx(mask8, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask8, 8)
    filtered = np.zeros_like(mask8)
    minimum_area = max(3, int(mask8.size * 0.0005))
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= minimum_area:
            filtered[labels == index] = 255
    filtered[~valid] = 0
    changed_pct = float(np.count_nonzero(filtered) / max(1, np.count_nonzero(valid)) * 100)
    preview = np.clip(new_n * 255, 0, 255).astype(np.uint8)
    changed = filtered > 0
    preview[changed] = (0.45 * preview[changed] + 0.55 * np.array([255, 0, 0])).astype(np.uint8)
    if earlier.resolution_m >= 10 or newer.resolution_m >= 10:
        warnings.append("Sentinel-2 is about 10 m/pixel at best; a typical house may occupy roughly one pixel. This is coarse screening for large changes only.")
    return HistoricalComparison(
        old_n, new_n, preview, filtered, changed_pct,
        valid_pct, cloud_pct, warnings,
        earlier_quality=earlier_quality,
        newer_quality=newer_quality,
        alignment_status=(
            "Pass — matching CRS, overlapping footprints, and common provider-resampled grid. "
            + alignment_note
        ),
        newer_before_alignment_rgb=new_before_alignment,
        alignment_applied=alignment_applied,
    )
