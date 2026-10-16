# Historical imagery and authorised data integration

## Current historical imagery provider

The optional Historical Imagery page queries the public Element84 Earth Search
STAC API (`https://earth-search.aws.element84.com/v1`) for the
`sentinel-2-l2a` collection. It uses only public HTTPS assets and small AOI
window reads from COG assets. It never uses requester-pays S3 assets. No
credentials or `.env` file are required. A 429, auth response, missing asset,
catalogue gap, or network failure is shown as a provider error; the existing
manual upload workflow remains the fallback.

Earth Search is a best-effort public catalogue without a service-level
availability guarantee. Historical coverage may have gaps. Scene dates and
cloud metadata are displayed from STAC results. The user chooses the two
scenes before the AOI is downloaded and compared.

Install the optional Rasterio dependency in the project's existing venv only
when using Sentinel-2 downloads:

```powershell
python -m pip install -r requirements-historical-imagery.txt
```

Rasterio reads the bands and reprojects only the approximately 500 m square
AOI to a shared 10 m grid. It is an optional compiled package using GDAL; the
app and manual workflow still run when it is absent, but AOI download then
reports the missing dependency. Do not store provider credentials in source
code. Earth Search's public endpoint does not require credentials.

## What the comparison means

This is preliminary image-difference screening, not a trained AI model. The
pixel score is the share of valid pixels marked different; it is not the share
of land newly built. Sentinel-2 is approximately 10 m/pixel at best. A typical
house may occupy about one pixel, so this is only a coarse aid for large
clearings or large structures. Cloud, shadow, snow, water, missing pixels,
seasonal differences, and alignment can affect the result. SCL cloud/shadow/
snow and water pixels are masked where the asset is available. If NIR bands
are present, NDVI screening suppresses differences where both dates remain
strongly vegetated; when NIR is absent, the UI reports that vegetation-index
screening was unavailable. Every result requires officer verification and
does not establish legality or a permit violation.

## Database migration

`initialize_database()` in `app.py` applies additive, idempotent migrations:

- Adds `verification_status` and `verification_evidence` to
  `property_locations` when missing. Existing entries default to
  `Unverified`.
- Adds imagery provenance, property link/coordinates, scene IDs, valid-pixel
  and cloud metrics, and nullable `comparison_key` to
  `image_change_detections` when missing.
- Adds a partial unique index over non-null `comparison_key` values.

No existing record IDs or image evidence are rewritten. Historical cases
reuse `image_change_detections`, start in `Pending Review`, and show
`Not verified` permit status. The deterministic key uses the property location,
both scene IDs, and comparison method; resubmission links to the existing case.

## Future authorised integrations

An imagery provider that requires credentials must be added as a separate
adapter after checking its official access terms, permissions, retention, and
attribution rules. Use deployment secrets or optional environment variables;
the public Sentinel-2 path must keep running without secrets. Any alternate
provider should return scene IDs, dates, footprints, CRS, resolution, cloud
metadata, attribution, and licensing, and should support bounded AOI reads.

An official permit database should be a separate read-only integration using
verified property references. Store its source and retrieval time, preserve
permit status separately from officer review status, and do not infer permit
details from image pixels.

## Official documentation checked

- [Element84 Earth Search](https://github.com/element84/earth-search)
- [Rasterio installation](https://rasterio.readthedocs.io/en/stable/installation.html)
- [Rasterio windowed reads](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html)
- [Sentinel-2 L2A SCL classes](https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/)
