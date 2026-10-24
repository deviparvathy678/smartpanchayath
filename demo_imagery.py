"""Safe GIF frame decoding and pixel-only comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError


MAX_GIF_BYTES = 10 * 1024 * 1024
MAX_GIF_PIXELS_PER_FRAME = 20_000_000
MAX_GIF_FRAMES = 200
MAX_GIF_DECODED_PIXELS = 100_000_000
MAX_PAIR_SCALE_RATIO = 2.0
MAX_PAIR_ASPECT_DELTA = 0.05


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    frame_count: int
    image_format: str = "GIF"


GifInfo = ImageInfo


@dataclass(frozen=True)
class ExtractedPixelLocation:
    latitude: float
    longitude: float
    crs: str
    uncertainty_m: float | None
    source_rgb: np.ndarray


def extract_georeferenced_pixel_location(
    payload: bytes,
    filename: str,
    expected_after_rgb: np.ndarray,
    pixel_x: float,
    pixel_y: float,
) -> ExtractedPixelLocation:
    """Map a marker only from a GeoTIFF exactly matching the selected AFTER frame."""
    if Path(str(filename or "")).suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("A georeferenced GeoTIFF is required for region location extraction.")
    if not payload or len(payload) > MAX_GIF_BYTES:
        raise ValueError("The GeoTIFF must be non-empty and 10 MB or smaller.")
    try:
        import rasterio
        from rasterio.io import MemoryFile
        from rasterio.warp import transform as transform_coordinates
    except ImportError as error:
        raise ValueError("GeoTIFF location extraction requires the project's Rasterio dependency.") from error

    try:
        with MemoryFile(payload) as memory_file, memory_file.open() as dataset:
            transform = dataset.transform
            coefficients = tuple(transform)[:6]
            determinant = transform.a * transform.e - transform.b * transform.d
            if dataset.crs is None or transform.is_identity:
                raise ValueError("GeoTIFF is missing a usable CRS or spatial transform.")
            if not all(math.isfinite(value) for value in coefficients) or not math.isfinite(determinant) or abs(determinant) < 1e-15:
                raise ValueError("GeoTIFF spatial transform is invalid or singular.")
            if dataset.count < 3 or any(dtype != "uint8" for dtype in dataset.dtypes[:3]):
                raise ValueError("The matching GeoTIFF must contain three 8-bit RGB bands.")
            if dataset.width * dataset.height > MAX_GIF_PIXELS_PER_FRAME:
                raise ValueError("The GeoTIFF exceeds the 20-million-pixel safety limit.")

            expected = np.asarray(expected_after_rgb)
            if expected.ndim != 3 or expected.shape[2] < 3:
                raise ValueError("The selected AFTER frame is not a valid RGB image.")
            if expected.shape[:2] != (dataset.height, dataset.width):
                raise ValueError("GeoTIFF dimensions must exactly match the selected AFTER frame.")
            source_rgb = np.moveaxis(dataset.read((1, 2, 3)), 0, -1)
            if not np.array_equal(source_rgb, expected[:, :, :3]):
                raise ValueError(
                    "GeoTIFF pixels do not exactly match the selected AFTER frame; "
                    "location extraction is blocked to avoid mapping a different image."
                )

            column = int(math.floor(float(pixel_x)))
            row = int(math.floor(float(pixel_y)))
            if not (0 <= column < dataset.width and 0 <= row < dataset.height):
                raise ValueError("The selected region pixel falls outside the georeferenced image.")
            if not dataset.dataset_mask()[row, column]:
                raise ValueError("The selected region pixel is outside the GeoTIFF's valid data area.")

            map_x, map_y = transform @ (float(pixel_x), float(pixel_y))
            longitudes, latitudes = transform_coordinates(
                dataset.crs, "EPSG:4326", [map_x], [map_y]
            )
            longitude, latitude = float(longitudes[0]), float(latitudes[0])
            if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("The GeoTIFF transform produced invalid latitude/longitude coordinates.")

            uncertainty_m = None
            if -80 <= latitude <= 84:
                zone = max(1, min(60, int((longitude + 180) // 6) + 1))
                local_crs = f"EPSG:{32600 + zone if latitude >= 0 else 32700 + zone}"
                corners = [
                    transform @ (float(pixel_x) + dx, float(pixel_y) + dy)
                    for dx, dy in ((-0.5, -0.5), (-0.5, 0.5), (0.5, -0.5), (0.5, 0.5))
                ]
                local_x, local_y = transform_coordinates(
                    dataset.crs, local_crs,
                    [map_x, *[point[0] for point in corners]],
                    [map_y, *[point[1] for point in corners]],
                )
                uncertainty_m = max(
                    math.hypot(local_x[index] - local_x[0], local_y[index] - local_y[0])
                    for index in range(1, 5)
                )
                if not math.isfinite(uncertainty_m):
                    uncertainty_m = None
            return ExtractedPixelLocation(
                latitude=latitude,
                longitude=longitude,
                crs=dataset.crs.to_string(),
                uncertainty_m=uncertainty_m,
                source_rgb=source_rgb.copy(),
            )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"GeoTIFF location extraction failed: {error}") from error


def inspect_comparison_image(payload: bytes, filename: str) -> ImageInfo:
    """Validate a bounded GIF, PNG, or JPEG and decode every GIF frame."""
    suffix = Path(str(filename or "")).suffix.lower()
    expected_formats = {".gif": "GIF", ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
    if suffix not in expected_formats:
        raise ValueError("Choose a GIF, PNG, JPG, or JPEG image.")
    if not payload:
        raise ValueError("The selected image file is empty.")
    if len(payload) > MAX_GIF_BYTES:
        raise ValueError("Image files must be 10 MB or smaller.")
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != expected_formats[suffix]:
                raise ValueError("The file extension does not match a valid GIF, PNG, or JPEG image.")
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1)) if image.format == "GIF" else 1
            if width <= 0 or height <= 0 or width * height > MAX_GIF_PIXELS_PER_FRAME:
                raise ValueError("Image dimensions must be valid and no larger than 20 million pixels per frame.")
            if frame_count < 1 or frame_count > MAX_GIF_FRAMES:
                raise ValueError("GIFs must contain between 1 and 200 frames.")
            if width * height * frame_count > MAX_GIF_DECODED_PIXELS:
                raise ValueError("This GIF has too much total frame data to preview safely.")
            for index in range(frame_count):
                if image.format == "GIF":
                    image.seek(index)
                image.convert("RGB").load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"The image is unreadable: {error}") from error
    return ImageInfo(width, height, frame_count, expected_formats[suffix])


def inspect_gif(payload: bytes, filename: str) -> GifInfo:
    """Backwards-compatible GIF-only validation helper."""
    if not str(filename or "").lower().endswith(".gif"):
        raise ValueError("Choose a file with the .gif extension.")
    return inspect_comparison_image(payload, filename)


def decode_comparison_frame(payload: bytes, filename: str, frame_index: int) -> np.ndarray:
    """Return a selected GIF frame or the sole PNG/JPEG frame as RGB pixels."""
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format not in ("GIF", "PNG", "JPEG"):
                raise ValueError("The uploaded file is not a supported image.")
            if not 0 <= int(frame_index) < int(getattr(image, "n_frames", 1)):
                raise ValueError("Selected image frame is outside the available range.")
            if image.format == "GIF":
                image.seek(int(frame_index))
            image.load()
            return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"The selected image frame is unreadable: {error}") from error


def decode_gif_frame(payload: bytes, frame_index: int) -> np.ndarray:
    """Backwards-compatible GIF decoder."""
    return decode_comparison_frame(payload, "upload.gif", frame_index)


def validate_gif_pair(before: GifInfo, after: GifInfo) -> None:
    """Reject different framing or extreme size differences before alignment."""
    old_ratio = before.width / before.height
    new_ratio = after.width / after.height
    aspect_delta = abs(old_ratio - new_ratio) / max(old_ratio, 1e-9)
    if aspect_delta > MAX_PAIR_ASPECT_DELTA:
        raise ValueError(
            f"Image aspect ratios differ by {aspect_delta * 100:.1f}%. "
            "Choose images with matching framing; this comparison will not stretch them."
        )
    scale_ratio = max(
        before.width / after.width, after.width / before.width,
        before.height / after.height, after.height / before.height,
    )
    if scale_ratio > MAX_PAIR_SCALE_RATIO:
        raise ValueError(
            f"Image dimensions differ by a scale factor of {scale_ratio:.1f}. "
            "Choose images with more compatible pixel dimensions."
        )


def filter_regions_by_area(mask: np.ndarray, minimum_area: int) -> np.ndarray:
    """Return a binary mask containing connected regions above pixel area."""
    array = np.asarray(mask)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("Change mask must be a non-empty two-dimensional array.")
    if int(minimum_area) < 1:
        raise ValueError("Minimum region area must be at least one pixel.")
    binary = np.where(array > 0, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    output = np.zeros_like(binary)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= int(minimum_area):
            output[labels == label] = 255
    return output
