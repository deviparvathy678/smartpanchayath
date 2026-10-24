"""Upload parsing and conservative quality gates for aerial imagery.

This module has no Streamlit or provider credentials. GeoTIFF support uses the
optional Rasterio dependency; ordinary JPEG/PNG uploads remain explicitly
unreferenced and cannot support building-level conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
import math
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

MAX_AERIAL_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_AERIAL_IMAGE_PIXELS = 20_000_000


class ImageryUploadError(ValueError):
    """An upload cannot be safely interpreted for visual comparison."""


@dataclass(frozen=True)
class UploadedAerialImage:
    rgb: np.ndarray
    source_filename: str
    acquisition_date: date
    georeferenced: bool
    crs: str | None = None
    bounds_wgs84: tuple[float, float, float, float] | None = None
    resolution_m: float | None = None
    valid_data_percentage: float | None = None
    cloud_percentage: float | None = None
    source: str = "Officer upload"
    transform: object | None = None
    width: int | None = None
    height: int | None = None
    valid_mask: np.ndarray | None = None

    @property
    def resolution_capability(self) -> str:
        if self.resolution_m is None:
            return "Unknown: ground resolution is unavailable"
        if self.resolution_m > 5:
            return "Coarse imagery: large changes only (large clearings)"
        if self.resolution_m >= 0.5:
            return "Large structures may be visible; verify manually"
        return "Building-scale detail may be visible; verify manually"

    @property
    def supports_building_level_review(self) -> bool:
        return self.georeferenced and self.resolution_m is not None and self.resolution_m < 0.5


@dataclass(frozen=True)
class PairValidation:
    warnings: tuple[str, ...]
    overlap_percentage: float | None
    comparison_allowed: bool
    building_level_allowed: bool
    resolution_capability: str


def _parse_acquisition_date(value: str | date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    value = str(value).strip()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _geotiff_date(tags: dict[str, str]) -> date | None:
    for name in ("acquisition_date", "datetime", "DATE_ACQUIRED", "TIFFTAG_DATETIME"):
        parsed = _parse_acquisition_date(tags.get(name))
        if parsed:
            return parsed
    return None


def _rgb_uint8(dataset, valid_mask: np.ndarray) -> np.ndarray:
    if dataset.count < 3:
        raise ImageryUploadError("GeoTIFF must contain at least three RGB bands.")
    bands = dataset.read((1, 2, 3), masked=False).astype(np.float32)
    rgb = np.moveaxis(bands, 0, -1)
    if dataset.dtypes[0] == "uint8":
        return np.clip(rgb, 0, 255).astype(np.uint8)
    pixels = rgb[valid_mask]
    if pixels.size == 0:
        raise ImageryUploadError("GeoTIFF contains no valid image pixels.")
    output = np.zeros(rgb.shape, dtype=np.uint8)
    for band in range(3):
        values = rgb[:, :, band][valid_mask]
        low, high = np.percentile(values, (2, 98))
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            continue
        scaled = (rgb[:, :, band] - low) * (255.0 / (high - low))
        output[:, :, band] = np.clip(scaled, 0, 255).astype(np.uint8)
    return output


def _ground_resolution_m(dataset, bounds_wgs84) -> float:
    """Estimate local ground sample distance after projecting to local UTM."""
    from rasterio.warp import transform as transform_coords

    west, south, east, north = bounds_wgs84
    lon, lat = (west + east) / 2, (south + north) / 2
    if not (-180 <= lon <= 180 and -80 <= lat <= 84):
        raise ImageryUploadError("GeoTIFF bounds are outside supported ground-resolution coverage.")
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    local_crs = f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
    col, row = dataset.width / 2, dataset.height / 2
    points = [dataset.transform @ (col, row), dataset.transform @ (col + 1, row), dataset.transform @ (col, row + 1)]
    xs, ys = transform_coords(dataset.crs, local_crs, [p[0] for p in points], [p[1] for p in points])
    dx = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
    dy = math.hypot(xs[2] - xs[0], ys[2] - ys[0])
    resolution = (dx + dy) / 2
    if not math.isfinite(resolution) or resolution <= 0:
        raise ImageryUploadError("Ground resolution could not be calculated from the GeoTIFF transform.")
    return resolution


def read_aerial_upload(
    file_bytes: bytes,
    filename: str,
    acquisition_date: str | date | datetime | None = None,
) -> UploadedAerialImage:
    """Read a georeferenced GeoTIFF or an explicitly unreferenced JPG/PNG."""
    suffix = Path(filename).suffix.lower()
    if not file_bytes:
        raise ImageryUploadError("The uploaded image is empty.")
    if len(file_bytes) > MAX_AERIAL_UPLOAD_BYTES:
        raise ImageryUploadError("Each image upload must be 10 MB or smaller.")

    if suffix in (".tif", ".tiff"):
        try:
            import rasterio
            from rasterio.io import MemoryFile
            from rasterio.warp import transform_bounds
        except ImportError as error:
            raise ImageryUploadError("GeoTIFF upload requires Rasterio. Install the optional geospatial requirements.") from error
        try:
            with MemoryFile(file_bytes) as memory_file, memory_file.open() as dataset:
                if dataset.width * dataset.height > MAX_AERIAL_IMAGE_PIXELS:
                    raise ImageryUploadError("GeoTIFF exceeds the 20-million-pixel safety limit.")
                if dataset.crs is None or dataset.transform.is_identity:
                    raise ImageryUploadError("GeoTIFF is missing a usable CRS or affine georeferencing.")
                bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
                if not all(math.isfinite(value) for value in bounds):
                    raise ImageryUploadError("GeoTIFF has invalid geographic bounds.")
                valid_mask = dataset.dataset_mask() > 0
                valid_percentage = float(np.count_nonzero(valid_mask) / valid_mask.size * 100)
                rgb = _rgb_uint8(dataset, valid_mask)
                tags = dataset.tags()
                effective_date = _parse_acquisition_date(acquisition_date) or _geotiff_date(tags)
                if effective_date is None:
                    raise ImageryUploadError("Acquisition date is missing. Enter the date recorded for this image.")
                resolution = _ground_resolution_m(dataset, bounds)
                return UploadedAerialImage(
                    rgb=rgb,
                    source_filename=Path(filename).name,
                    acquisition_date=effective_date,
                    georeferenced=True,
                    crs=dataset.crs.to_string(),
                    bounds_wgs84=tuple(float(v) for v in bounds),
                    resolution_m=resolution,
                    valid_data_percentage=valid_percentage,
                    cloud_percentage=None,
                    source="Uploaded GeoTIFF orthomosaic",
                    transform=dataset.transform,
                    width=dataset.width,
                    height=dataset.height,
                    valid_mask=valid_mask,
                )
        except ImageryUploadError:
            raise
        except Exception as error:
            raise ImageryUploadError(f"GeoTIFF could not be read: {error}") from error

    if suffix not in (".jpg", ".jpeg", ".png"):
        raise ImageryUploadError("Upload a GeoTIFF, JPG, JPEG, or PNG image.")
    try:
        with Image.open(BytesIO(file_bytes)) as image:
            if image.width * image.height > MAX_AERIAL_IMAGE_PIXELS:
                raise ImageryUploadError("Image exceeds the 20-million-pixel safety limit.")
            rgb = np.asarray(image.convert("RGB"))
            embedded_date = None
            try:
                exif = image.getexif()
                embedded_date = _parse_acquisition_date(exif.get(36867) or exif.get(306))
            except (AttributeError, TypeError, ValueError):
                embedded_date = None
    except (UnidentifiedImageError, OSError) as error:
        raise ImageryUploadError("The uploaded file is not a readable image.") from error
    effective_date = _parse_acquisition_date(acquisition_date) or embedded_date
    if effective_date is None:
        raise ImageryUploadError("Image has no usable acquisition date. Enter the date recorded for this image.")
    return UploadedAerialImage(
        rgb=rgb,
        source_filename=Path(filename).name,
        acquisition_date=effective_date,
        georeferenced=False,
        valid_data_percentage=100.0,
        source="Unreferenced officer upload",
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
        valid_mask=np.ones(rgb.shape[:2], dtype=bool),
    )


def _overlap_percentage(first, second) -> float:
    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[2], second[2])
    top = min(first[3], second[3])
    if right <= left or top <= bottom:
        return 0.0
    overlap = (right - left) * (top - bottom)
    smaller = min((first[2] - first[0]) * (first[3] - first[1]), (second[2] - second[0]) * (second[3] - second[1]))
    return overlap / smaller * 100 if smaller > 0 else 0.0


def validate_aerial_pair(
    earlier: UploadedAerialImage,
    newer: UploadedAerialImage,
    minimum_valid_percentage: float = 50.0,
    minimum_overlap_percentage: float = 90.0,
) -> PairValidation:
    """Gate comparisons by dates, coordinates, coverage, and image resolution."""
    if earlier.acquisition_date >= newer.acquisition_date:
        raise ImageryUploadError("Earlier image date must be before the recent image date.")
    warnings = []
    overlap = None
    if earlier.georeferenced != newer.georeferenced:
        raise ImageryUploadError("Both images must either have georeferencing or both be unreferenced.")
    if not earlier.georeferenced:
        warnings.append("Neither image is georeferenced. Same-area validation is unavailable; comparison is unverified.")
        warnings.append("Building-level conclusions are blocked for unreferenced imagery.")
        return PairValidation(tuple(warnings), None, True, False, "Unknown: ground resolution is unavailable")
    if earlier.crs != newer.crs:
        raise ImageryUploadError("The images use different coordinate reference systems. Reproject them to one CRS first.")
    if not earlier.bounds_wgs84 or not newer.bounds_wgs84:
        raise ImageryUploadError("Geographic bounds are missing; same-area validation cannot be performed.")
    overlap = _overlap_percentage(earlier.bounds_wgs84, newer.bounds_wgs84)
    if overlap < minimum_overlap_percentage:
        raise ImageryUploadError(
            f"Image footprints overlap by {overlap:.1f}%; at least {minimum_overlap_percentage:.0f}% overlap is required."
        )
    valid_values = (earlier.valid_data_percentage, newer.valid_data_percentage)
    if any(value is None or value < minimum_valid_percentage for value in valid_values):
        raise ImageryUploadError(
            f"Each image needs at least {minimum_valid_percentage:.0f}% valid pixels for comparison."
        )
    if earlier.cloud_percentage is None or newer.cloud_percentage is None:
        warnings.append("Cloud coverage is unknown for one or both uploads; inspect the images before interpreting differences.")
    resolutions = [value for value in (earlier.resolution_m, newer.resolution_m) if value is not None]
    if not resolutions:
        capability = "Unknown: ground resolution is unavailable"
        building_allowed = False
    else:
        limiting_resolution = max(resolutions)
        building_allowed = all(image.supports_building_level_review for image in (earlier, newer))
        if limiting_resolution > 5:
            capability = "Coarse imagery: large changes only (large clearings)"
            building_allowed = False
            warnings.append("Resolution is coarser than 5 m/pixel; building-level conclusions are blocked.")
        elif limiting_resolution >= 0.5:
            capability = "Large structures may be visible; verify manually"
            building_allowed = False
            warnings.append("Resolution is 0.5–5 m/pixel; building-level conclusions are blocked.")
        else:
            capability = "Building-scale detail may be visible; officer verification is still required"
    if any(value is None for value in (earlier.cloud_percentage, newer.cloud_percentage)):
        warnings.append("Cloud coverage unknown: manual review required before comparison results are saved.")
    return PairValidation(tuple(dict.fromkeys(warnings)), overlap, True, building_allowed, capability)
