# Stage 7: permit records and investigation priority

## Data and providers

The existing `cases` and `property_locations` tables remain in use. `permit_records`
stores manually maintained permit scope linked to the exact `property_locations.location_id`.
`risk_assessments` stores one current assessment for each case record, including its
stable assessment key, evidence snapshot, reasons, score, category, and recommended
action. `permit_audit_events` preserves permit record edits and verification changes;
`case_audit_events` continues to preserve officer notes and case status history.

`ManualPermitProvider` reads local records and always labels its source type as manual.
`OfficialPermitAPIProvider` is a disconnected placeholder. No government endpoint,
credentials, or official permit data is currently configured.

To connect an authorised service later, implement the `PermitProvider.lookup()`
interface in a separate adapter. Obtain written access and API documentation from
the issuing authority, use its approved authentication mechanism through deployment
secrets or environment configuration, and return the source reference, retrieval
time, property match, permit scope, and verification evidence. Do not scrape public
pages or treat an unmatched response as proof that a permit does not exist.

## Explainable priority score

Weights are in `RISK_RULES` in `risk_assessment.py`. The score is clamped to 0–100:

| Evidence | Points |
| --- | ---: |
| Possible new building or extension category | +35 |
| Other structural change category | +25 |
| Recorded change without a more specific category | +20 |
| Image quality passes Stage 6 checks | +8 |
| Image alignment passes Stage 6 checks | +8 |
| Exact linked property has verified coordinates | +5 |
| Permit record is unavailable or unverified | +12 for verification priority |
| Verified permit is expired | +15 |
| Potential discrepancy against a verified permit record | +25 |
| Legacy floor difference without a linked verified permit | +15 |

Evidence caps prevent weak inputs from receiving high categories: unsuitable image
quality or failed alignment caps at 39, unknown quality/alignment or missing property
association caps at 49, and unverified coordinates cap at 59. Good imagery can raise
priority for review; the score is not a probability or confidence estimate.

| Score | Category |
| ---: | --- |
| 0–24 | LOW PRIORITY |
| 25–49 | REVIEW REQUIRED |
| 50–74 | HIGH PRIORITY |
| 75–100 | CRITICAL REVIEW |

The risk category is distinct from review status (`Pending Review`, `Under
Investigation`, `Additional Information Required`, `Resolved`) and permit status.
Missing permit data adds a verification task only. It does not establish that no
permit exists or that construction is unlawful.

## Comparison limits

The app does not derive floor counts, built-up area, or land use from a pixel score.
Floor comparisons use a linked verified permit record; an area comparison requires
an explicitly verified measured area with source and projected CRS, and land-use
comparison requires a verified observation source. Those observation fields are not
currently collected in the app, so current imagery cases report insufficient
evidence for those comparisons.

Satellite imagery may be too coarse for small buildings. Clouds, haze, shadows,
seasons, and alignment can create false changes. Feature matching does not prove
geographic registration. Permit records may be incomplete; local entries stay
manual unless an officer records the authoritative source checked. Authentication
is not implemented, so the prototype cannot identify or securely authorize officers.
All findings and priorities require human review; final administrative or legal
determinations belong to the authorised authority.
