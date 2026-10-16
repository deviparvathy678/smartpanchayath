"""Historical imagery providers. No Streamlit imports belong in this module.

Earth Search is a best-effort public STAC catalogue. Only public HTTPS COG
assets are opened; requester-pays or authenticated assets are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Protocol

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
EARTH_SEARCH_COLLECTION = "sentinel-2-l2a"
EARTH_SEARCH_ATTRIBUTION = "Sentinel-2, processed by Element84 Earth Search"


class ImageryProviderError(RuntimeError):
    """Base class for explicit provider failures shown to officers."""


class NoImageryFound(ImageryProviderError):
    def __init__(self, message: str, nearest_dates: list[str] | None = None):
        super().__init__(message)
        self.nearest_dates = nearest_dates or []


class AuthenticationMissing(ImageryProviderError):
    pass


class RateLimited(ImageryProviderError):
    pass


class ProviderError(ImageryProviderError):
    pass


@dataclass(frozen=True)
class SceneResult:
    scene_id: str
    acquisition_date: date
    source: str
    resolution_m: float | None
    cloud_cover_pct: float | None
    footprint: dict
    crs: str | None
    attribution: str
    license: str
    assets: dict[str, str]
    properties: dict


@dataclass(frozen=True)
class GeoImage:
    """A small AOI cube: RGB reflectance, SCL values, and geographic metadata."""

    rgb: object
    scl: object | None
    transform: object
    crs: str
    bbox_wgs84: tuple[float, float, float, float]
    resolution_m: float
    acquisition_date: date
    scene_id: str
    source: str
    nir: object | None = None


class ImageryProvider(Protocol):
    def search(self, lat: float, lon: float, start_date: date, end_date: date,
               max_cloud_cover: float) -> list[SceneResult]: ...

    def download_aoi(self, scene: SceneResult, lat: float, lon: float,
                     size_m: int = 500) -> GeoImage: ...


class Element84SentinelProvider:
    """Sentinel-2 L2A adapter for the public Element84 Earth Search STAC API."""

    name = "Sentinel-2 L2A via Element84 Earth Search"

    def __init__(self, timeout: tuple[int, int] = (10, 30)):
        self.timeout = timeout
        self.session = requests.Session()
        retries = Retry(total=3, connect=3, read=3, status=3,
                        backoff_factor=0.5,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=frozenset(("GET", "POST")),
                        respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({"User-Agent": "SmartPanchayat/1.0 (historical imagery screening)"})

    def search(self, lat: float, lon: float, start_date: date, end_date: date,
               max_cloud_cover: float = 30) -> list[SceneResult]:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Property coordinates are outside valid ranges.")
        if start_date > end_date:
            raise ValueError("Start date must be on or before end date.")
        if not 0 <= float(max_cloud_cover) <= 100:
            raise ValueError("Maximum cloud cover must be from 0 to 100 percent.")
        half = float(500) / 2
        lat_delta = half / 111_320
        lon_delta = half / max(1.0, 111_320 * abs(math.cos(math.radians(lat))))
        bbox = [lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta]
        payload = {
            "collections": [EARTH_SEARCH_COLLECTION],
            "bbox": bbox,
            "datetime": f"{start_date.isoformat()}T00:00:00Z/{end_date.isoformat()}T23:59:59Z",
            "limit": 100,
        }
        # Fetch the date-nearest candidates too; cloud suitability is displayed
        # and selected by the caller rather than hiding nearby scenes.
        try:
            response = self.session.post(f"{EARTH_SEARCH_URL}/search", json=payload,
                                         timeout=self.timeout)
        except requests.RequestException as error:
            raise ProviderError(f"Earth Search request failed: {error}") from error
        if response.status_code in (401, 403):
            raise AuthenticationMissing("Earth Search returned an authorization error; no public imagery was returned.")
        if response.status_code == 429:
            raise RateLimited("Earth Search rate limited the request. Please try again later.")
        if response.status_code >= 400:
            raise ProviderError(f"Earth Search returned HTTP {response.status_code}.")
        try:
            payload = response.json()
            features = payload.get("features", [])
        except (ValueError, AttributeError) as error:
            raise ProviderError("Earth Search returned unreadable STAC metadata.") from error
        if not features:
            raise NoImageryFound("No Sentinel-2 scenes intersect this area and date range.")
        scenes = []
        for feature in features:
            try:
                props = feature["properties"]
                acquired = date.fromisoformat(props["datetime"][:10])
                assets = feature.get("assets", {})
                # Earth Search publishes assets as public HTTPS COGs. Never use
                # requester-pays S3 URLs or signed/authenticated links.
                hrefs = {key: val.get("href") for key, val in assets.items()
                         if isinstance(val, dict) and str(val.get("href", "")).startswith("https://")}
                cloud = props.get("eo:cloud_cover")
                scenes.append(SceneResult(
                    scene_id=feature["id"], acquisition_date=acquired,
                    source=self.name,
                    resolution_m=float(props.get("gsd") or 10.0),
                    cloud_cover_pct=float(cloud) if cloud is not None else None,
                    footprint=feature.get("geometry") or {},
                    crs=props.get("proj:epsg") and f"EPSG:{props['proj:epsg']}",
                    attribution=EARTH_SEARCH_ATTRIBUTION,
                    license=str(props.get("license") or "Provider terms apply; see Earth Search catalogue."),
                    assets=hrefs, properties=props,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if not scenes:
            raise ProviderError("The catalogue returned scenes with incomplete date metadata.")
        # Surface nearest dates even when all scenes exceed the officer's cloud limit.
        scenes.sort(key=lambda item: (item.acquisition_date, item.scene_id))
        suitable = [scene for scene in scenes
                    if scene.cloud_cover_pct is None or scene.cloud_cover_pct <= float(max_cloud_cover)]
        if not suitable:
            raise NoImageryFound(
                f"Scenes were found, but none meet the {max_cloud_cover:.0f}% cloud-cover limit.",
                [scene.acquisition_date.isoformat() for scene in scenes[:8]],
            )
        return suitable

    def download_aoi(self, scene: SceneResult, lat: float, lon: float,
                     size_m: int = 500) -> GeoImage:
        try:
            import rasterio
            from rasterio.transform import from_origin
            from rasterio.warp import reproject, Resampling
        except ImportError as error:
            raise ProviderError("Historical imagery download needs Rasterio. Install requirements-historical-imagery.txt.") from error
        if not 50 <= int(size_m) <= 2000:
            raise ValueError("AOI size must be between 50 and 2000 metres.")
        if scene.properties.get("storage:requester_pays") or any(href.startswith("s3://") for href in scene.assets.values()):
            raise AuthenticationMissing("This scene requires requester-pays access and will not be downloaded.")
        aliases = {"red": ("red", "B04"), "green": ("green", "B03"),
                   "blue": ("blue", "B02"), "nir": ("nir", "B08"),
                   "scl": ("scl", "SCL")}
        selected = {}
        for output_name, keys in aliases.items():
            href = next((scene.assets[key] for key in keys if key in scene.assets), None)
            if href:
                selected[output_name] = href
        if not all(name in selected for name in ("red", "green", "blue")):
            raise ProviderError("This scene is missing one or more public RGB band COGs.")
        from math import cos, radians
        from rasterio.crs import CRS
        from rasterio.env import Env
        utm_zone = max(1, min(60, int((lon + 180) // 6) + 1))
        epsg = (32600 if lat >= 0 else 32700) + utm_zone
        out_crs = CRS.from_epsg(epsg)
        resolution = 10.0
        width = height = int(size_m / resolution)
        # Compute the projected centre and use a fixed common grid at both dates.
        from rasterio.warp import transform as transform_coords
        xs, ys = transform_coords("EPSG:4326", out_crs, [lon], [lat])
        west = xs[0] - size_m / 2
        north = ys[0] + size_m / 2
        dst_transform = from_origin(west, north, resolution, resolution)
        rgb = []
        with Env(GDAL_HTTP_TIMEOUT="30", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                 CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff"):
            for band in ("red", "green", "blue"):
                output = np.zeros((height, width), dtype="float32")
                try:
                    with rasterio.open(selected[band]) as source:
                        reproject(source=rasterio.band(source, 1), destination=output,
                                  src_transform=source.transform, src_crs=source.crs,
                                  src_nodata=source.nodata, dst_transform=dst_transform,
                                  dst_crs=out_crs, dst_nodata=0, resampling=Resampling.bilinear)
                except Exception as error:
                    raise ProviderError(f"Could not read the scene {band} band: {error}") from error
                rgb.append(output)
            scl = None
            if "scl" in selected:
                try:
                    scl = np.zeros((height, width), dtype="uint8")
                    with rasterio.open(selected["scl"]) as source:
                        reproject(source=rasterio.band(source, 1), destination=scl,
                                  src_transform=source.transform, src_crs=source.crs,
                                  src_nodata=source.nodata, dst_transform=dst_transform,
                                  dst_crs=out_crs, dst_nodata=0, resampling=Resampling.nearest)
                except Exception:
                    scl = None
            nir = None
            if "nir" in selected:
                try:
                    nir = np.zeros((height, width), dtype="float32")
                    with rasterio.open(selected["nir"]) as source:
                        reproject(source=rasterio.band(source, 1), destination=nir,
                                  src_transform=source.transform, src_crs=source.crs,
                                  src_nodata=source.nodata, dst_transform=dst_transform,
                                  dst_crs=out_crs, dst_nodata=0, resampling=Resampling.bilinear)
                except Exception:
                    nir = None
        cube = np.stack(rgb, axis=-1)
        # Sentinel-2 L2A surface-reflectance COGs commonly store 0..10000.
        cube = np.clip(cube / 10000.0, 0, 1)
        lat_delta = (size_m / 2) / 111_320
        lon_delta = (size_m / 2) / max(1.0, 111_320 * abs(cos(radians(lat))))
        coords = (scene.footprint or {}).get("coordinates", [])
        flat_points = []
        def collect_points(value):
            if isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
                flat_points.append(value)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect_points(child)
        collect_points(coords)
        footprint_bbox = (
            (min(p[0] for p in flat_points), min(p[1] for p in flat_points),
             max(p[0] for p in flat_points), max(p[1] for p in flat_points))
            if flat_points else (lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta)
        )
        return GeoImage(cube, scl, dst_transform, out_crs.to_string(),
                        footprint_bbox, float(scene.resolution_m or resolution),
                        scene.acquisition_date, scene.scene_id, self.name,
                        np.clip(nir / 10000.0, 0, 1) if nir is not None else None)


class ManualUploadProvider:
    """Marker adapter for the existing manual-upload workflow."""
    name = "Manual upload (existing image comparison workflow)"
    def search(self, *args, **kwargs):
        raise NoImageryFound("Manual uploads are selected directly in the existing comparison workflow.")
    def download_aoi(self, *args, **kwargs):
        raise ProviderError("Manual uploads do not have satellite AOI georeferencing.")


class MockProvider:
    """Deterministic offline provider for tests only; never expose in normal UI."""
    name = "OFFLINE TEST MOCK (not real imagery)"
    def __init__(self, scenes=None, images=None):
        self.scenes = scenes or []
        self.images = images or {}
    def search(self, lat, lon, start_date, end_date, max_cloud_cover=100):
        found = [scene for scene in self.scenes
                 if start_date <= scene.acquisition_date <= end_date]
        if not found:
            raise NoImageryFound("MockProvider has no configured test scene.")
        return found
    def download_aoi(self, scene, lat, lon, size_m=500):
        if scene.scene_id not in self.images:
            raise NoImageryFound("MockProvider has no configured test image.")
        return self.images[scene.scene_id]
