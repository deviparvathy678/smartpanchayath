"""Provider boundary for local permit records and future official services.

No network access or fabricated data is provided by this module. The current
provider reads officer-entered local records supplied by the app; an official
adapter remains deliberately disconnected until an authorised API exists.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class PermitLookupResult:
    status: str
    records: list[dict] = field(default_factory=list)
    message: str = ""
    source_type: str = "manual"


class PermitProvider(Protocol):
    def lookup(self, property_id=None, property_reference=None) -> PermitLookupResult:
        """Look up permit evidence for an exact property identifier."""


class ManualPermitProvider:
    """Expose saved local permit records without implying official verification."""

    def __init__(self, record_loader):
        if not callable(record_loader):
            raise TypeError("record_loader must be callable")
        self._record_loader = record_loader

    def lookup(self, property_id=None, property_reference=None):
        records = self._record_loader(property_id, property_reference) or []
        normalized = [dict(record) for record in records]
        if not normalized:
            return PermitLookupResult(
                status="not_available",
                message="Permit status not verified; no local permit record is available.",
            )
        # Local records can carry a manually recorded verification state, but
        # the adapter never represents them as an API response.
        return PermitLookupResult(
            status="manual_records_available",
            records=normalized,
            message="Local permit records are manually maintained; inspect their source and verification notes.",
            source_type="manual",
        )


class OfficialPermitAPIProvider:
    """Not-connected placeholder for a future authorised read-only API."""

    def lookup(self, property_id=None, property_reference=None):
        return PermitLookupResult(
            status="not_connected",
            message="Official permit API is not connected. Permit status not verified.",
            source_type="official_api_not_connected",
        )
