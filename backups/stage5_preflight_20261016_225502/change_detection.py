"""Image-only comparison helpers for user-supplied site photographs.

This module does not use a trained model and does not determine whether a
construction change is permitted or unlawful.
"""

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_PIXELS = 20_000_000
MIN_COMPONENT_PIXELS = 64
DETECTION_METHOD = "ORB alignment + CLAHE/Lab pixel difference (OpenCV)"


@dataclass
class ComparisonResult:
    earlier_rgb: np.ndarray
    newer_rgb: np.ndarray
    aligned_newer_rgb: np.ndarray
    mask: np.ndarray
    highlighted_rgb: np.ndarray
    changed_pixel_percentage: float
    alignment_note: str


def decode_rgb_image(image_bytes):
    """Decode an uploaded image and reject invalid or excessively large files."""
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            if width < 2 or height < 2:
                raise ValueError("Image dimensions must be at least 2 by 2 pixels.")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image is too large to process safely (maximum 20 megapixels).")
            return np.asarray(image.convert("RGB"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a readable image.") from error


def _align_newer_to_earlier(earlier_rgb, newer_rgb):
    """Try a feature-based homography; keep resized images if alignment is weak."""
    height, width = earlier_rgb.shape[:2]
    if newer_rgb.shape[:2] != (height, width):
        newer_rgb = cv2.resize(newer_rgb, (width, height), interpolation=cv2.INTER_AREA)

    earlier_gray = cv2.cvtColor(earlier_rgb, cv2.COLOR_RGB2GRAY)
    newer_gray = cv2.cvtColor(newer_rgb, cv2.COLOR_RGB2GRAY)
    detector = cv2.ORB_create(nfeatures=2500)
    earlier_points, earlier_descriptors = detector.detectAndCompute(earlier_gray, None)
    newer_points, newer_descriptors = detector.detectAndCompute(newer_gray, None)

    if earlier_descriptors is None or newer_descriptors is None:
        return newer_rgb, "Feature alignment unavailable; images were resized to a common size."

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(newer_descriptors, earlier_descriptors, k=2)
    good_matches = [
        first for pair in pairs if len(pair) == 2
        for first, second in [pair]
        if first.distance < 0.75 * second.distance
    ]
    if len(good_matches) < 10:
        return newer_rgb, "Feature alignment was not reliable; images were resized to a common size."

    source_points = np.float32(
        [newer_points[match.queryIdx].pt for match in good_matches]
    ).reshape(-1, 1, 2)
    target_points = np.float32(
        [earlier_points[match.trainIdx].pt for match in good_matches]
    ).reshape(-1, 1, 2)
    transform, inlier_mask = cv2.findHomography(
        source_points, target_points, cv2.RANSAC, 4.0
    )
    if transform is None or inlier_mask is None or int(inlier_mask.sum()) < 8:
        return newer_rgb, "Feature alignment was not reliable; images were resized to a common size."
    if not np.isfinite(transform).all():
        return newer_rgb, "Feature alignment was not reliable; images were resized to a common size."

    aligned = cv2.warpPerspective(
        newer_rgb,
        transform,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return aligned, "Newer image aligned to the earlier image using ORB features."


def compare_images(earlier_bytes, newer_bytes):
    """Compare two images and return a filtered pixel-difference mask."""
    earlier_rgb = decode_rgb_image(earlier_bytes)
    newer_rgb = decode_rgb_image(newer_bytes)

    earlier_height, earlier_width = earlier_rgb.shape[:2]
    newer_height, newer_width = newer_rgb.shape[:2]
    earlier_ratio = earlier_width / earlier_height
    newer_ratio = newer_width / newer_height
    if abs(earlier_ratio - newer_ratio) / earlier_ratio > 0.08:
        raise ValueError(
            "The images have different aspect ratios. Upload images framed at a similar angle and shape."
        )

    aligned_newer_rgb, alignment_note = _align_newer_to_earlier(
        earlier_rgb, newer_rgb
    )

    # CLAHE reduces broad brightness variation. Lab color distance retains
    # chromatic changes; blur, thresholding, and morphology suppress speckle.
    earlier_lab = cv2.cvtColor(earlier_rgb, cv2.COLOR_RGB2LAB)
    newer_lab = cv2.cvtColor(aligned_newer_rgb, cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    earlier_lab[:, :, 0] = clahe.apply(earlier_lab[:, :, 0])
    newer_lab[:, :, 0] = clahe.apply(newer_lab[:, :, 0])
    channel_difference = cv2.absdiff(earlier_lab, newer_lab)
    lightness_difference = channel_difference[:, :, 0]
    chroma_difference = cv2.max(
        channel_difference[:, :, 1], channel_difference[:, :, 2]
    )
    score_gray = cv2.addWeighted(
        lightness_difference, 0.55, chroma_difference, 0.45, 0
    )
    score_gray = cv2.GaussianBlur(score_gray, (5, 5), 0)
    _, mask = cv2.threshold(score_gray, 22, 255, cv2.THRESH_BINARY)

    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    minimum_area = max(MIN_COMPONENT_PIXELS, int(mask.size * 0.00002))
    filtered_mask = np.zeros_like(mask)
    for component in range(1, component_count):
        if stats[component, cv2.CC_STAT_AREA] >= minimum_area:
            filtered_mask[labels == component] = 255

    changed_pixels = int(np.count_nonzero(filtered_mask))
    percentage = changed_pixels / filtered_mask.size * 100
    highlighted = aligned_newer_rgb.copy()
    changed = filtered_mask > 0
    red = np.array([255, 0, 0], dtype=np.uint8)
    highlighted[changed] = (
        0.45 * highlighted[changed] + 0.55 * red
    ).astype(np.uint8)

    return ComparisonResult(
        earlier_rgb=earlier_rgb,
        newer_rgb=newer_rgb,
        aligned_newer_rgb=aligned_newer_rgb,
        mask=filtered_mask,
        highlighted_rgb=highlighted,
        changed_pixel_percentage=percentage,
        alignment_note=alignment_note,
    )
