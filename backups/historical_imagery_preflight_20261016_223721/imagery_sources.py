"""Interfaces for future authorised aerial or satellite imagery providers.

The application currently accepts only user-uploaded images. This module does
not connect to an imagery service or retrieve live imagery.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class ImageryRequest:
    area_name: str
    ward_number: int | None
    earlier_date: date
    newer_date: date
    authorised_reference: str | None = None


@dataclass(frozen=True)
class ImageryAsset:
    image_bytes: bytes
    captured_on: date
    provider_name: str
    provider_asset_id: str


class AuthorisedImageryProvider(Protocol):
    """Contract an approved provider adapter must implement before use."""

    def fetch_pair(self, request: ImageryRequest) -> tuple[ImageryAsset, ImageryAsset]:
        """Return authorised imagery for the requested area and date range."""
        ...
