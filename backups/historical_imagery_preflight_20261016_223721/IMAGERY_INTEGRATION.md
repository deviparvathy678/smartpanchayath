# Future imagery and permit-data integration

SmartPanchayat currently compares images uploaded by an authorised user. It
does not connect to a satellite or aerial-imagery service, discover property
coordinates, or retrieve live imagery.

The `imagery_sources.py` module defines a small `AuthorisedImageryProvider`
interface. A future adapter should be added only after the Panchayat has
approved a provider and its terms permit this use. The adapter should:

1. Accept an area/date request and an authorised property or parcel reference
   when available.
2. Return image bytes with provider asset IDs, capture dates, and source
   attribution.
3. Preserve the provider's licensing, access-control, retention, and audit
   requirements.
4. Use credentials supplied through a secure deployment secret store; no
   credentials are required by this prototype.
5. Pass returned images through the same validation and comparison pipeline
   used for uploaded images.

An authorised building-permit integration should be a separate read-only
adapter. It should query an approved source by a verified parcel/building
reference, return permit status with source and retrieval time, and never
infer permit details from an image. Until that integration exists, image-based
alerts display **Not verified** for permit status and require officer review.
