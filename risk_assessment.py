"""Explainable investigation-priority rules for SmartPanchayat.

Scores are workflow priorities, not probabilities or legal findings. This
module deliberately does not infer building area, floors, or land use from
pixel-difference scores.
"""

from dataclasses import asdict, dataclass, field
from datetime import date
import hashlib
import json
import math


RISK_RULES = {
    "potential_new_building": 35,
    "potential_extension": 35,
    "other_structural_change": 25,
    "unspecified_recorded_change": 20,
    "quality_pass": 8,
    "alignment_pass": 8,
    "verified_location": 5,
    "permit_unverified": 12,
    "permit_expired": 15,
    "verified_permit_discrepancy": 25,
    "legacy_unverified_floor_difference": 15,
    "unsuitable_evidence_score_cap": 39,
    "unknown_evidence_score_cap": 49,
    "unverified_location_score_cap": 59,
}

RISK_CATEGORIES = (
    "LOW PRIORITY",
    "REVIEW REQUIRED",
    "HIGH PRIORITY",
    "CRITICAL REVIEW",
)


@dataclass
class RiskAssessment:
    case_type: str
    case_id: str
    assessment_key: str
    property_location_id: int | None
    property_reference: str | None
    risk_score: int
    risk_category: str
    image_quality_status: str
    alignment_status: str
    permit_status: str
    reasons: list[str] = field(default_factory=list)
    recommended_action: str = "Officer review required"
    evidence: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def _get(record, name, default=None):
    if record is None:
        return default
    try:
        return record[name]
    except (KeyError, IndexError, TypeError):
        return default


def _decode_report(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _positive_finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _quality_status(case):
    report = _decode_report(_get(case, "quality_report_json"))
    status = str(report.get("overall_status") or "").strip().title()
    if status in ("Pass", "Warning", "Unsuitable"):
        return status
    statuses = [
        str(report.get(part, {}).get("status", "")).title()
        for part in ("earlier", "newer")
        if isinstance(report.get(part), dict)
    ]
    if "Unsuitable" in statuses:
        return "Unsuitable"
    if "Warning" in statuses:
        return "Warning"
    if statuses and all(item == "Pass" for item in statuses):
        return "Pass"
    return "Unknown"


def _alignment_status(case):
    report = _decode_report(_get(case, "alignment_report_json"))
    raw = str(report.get("status") or "").strip().casefold()
    if raw in ("pass", "warning", "failed", "unsuitable"):
        return "Failed" if raw == "unsuitable" else raw.title()
    if raw.startswith("pass"):
        return "Pass"
    if raw.startswith("warning"):
        return "Warning"
    if raw.startswith(("fail", "unsuitable")):
        return "Failed"
    return "Unknown"


def _category_for_score(score):
    if score >= 75:
        return "CRITICAL REVIEW"
    if score >= 50:
        return "HIGH PRIORITY"
    if score >= 25:
        return "REVIEW REQUIRED"
    return "LOW PRIORITY"


def _assessment_key(case_type, case, property_record):
    property_id = _get(property_record, "location_id") or _get(case, "property_location_id")
    property_ref = (
        _get(property_record, "property_reference")
        or _get(case, "building_reference")
        or ""
    )
    if property_id is None and not str(property_ref).strip():
        property_identity = f"unlinked:{case_type}:{_get(case, 'case_id', '')}"
    elif property_id is not None:
        property_identity = f"property-id:{property_id}"
    else:
        property_identity = f"property-reference:{str(property_ref).strip().casefold()}"
    identity = "|".join((
        property_identity,
        str(_get(case, "earlier_image_date", "")),
        str(_get(case, "newer_image_date", "")),
        str(_get(case, "earlier_scene_id", "")),
        str(_get(case, "newer_scene_id", "")),
        str(_get(case, "detection_method", "case-record")),
        case_type,
        str(_get(case, "case_id", "")),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def assess_investigation_priority(
    case_type,
    case,
    *,
    property_record=None,
    permit_record=None,
    today=None,
):
    """Return a transparent 0–100 investigation-priority assessment.

    `permit_record` must be a registry record. Legacy free-text permit fields
    are never treated as authoritative by themselves.
    """
    if case_type not in ("construction", "image_detection"):
        raise ValueError("case_type must be construction or image_detection")
    case_id = str(_get(case, "case_id", "") or "").strip()
    if not case_id:
        raise ValueError("case_id is required for risk assessment")
    today = today or date.today()
    reasons = []
    points = 0
    cap = 100
    quality = _quality_status(case) if case_type == "image_detection" else "Not applicable"
    alignment = _alignment_status(case) if case_type == "image_detection" else "Not applicable"
    permit_state = str(_get(permit_record, "verification_status", "") or "").strip()
    if not permit_record:
        permit_state = "Not Available"
        reasons.append(
            "Permit status not verified. No local permit record is available; this does not mean no permit exists."
        )
        points += RISK_RULES["permit_unverified"]
    elif permit_state.casefold() in ("verified", "verified against official record"):
        permit_state = "Verified"
        reasons.append("Permit record is marked verified in the local registry; check its cited source and scope.")
    elif permit_state.casefold() == "discrepancy requires review":
        permit_state = "Discrepancy Requires Review"
        reasons.append("Potential permit discrepancy is recorded and requires an officer to check the cited evidence.")
        points += RISK_RULES["verified_permit_discrepancy"]
    elif permit_state.casefold() == "expired":
        permit_state = "Expired"
        reasons.append("The recorded permit validity has expired; confirm the authoritative record and any renewal.")
        points += RISK_RULES["permit_expired"]
    elif permit_state.casefold() == "not applicable":
        permit_state = "Not Applicable"
        reasons.append("Permit verification is marked not applicable; confirm that this classification is appropriate.")
    else:
        permit_state = "Unverified"
        reasons.append("Permit status not verified. Manually entered permit information is not authoritative by itself.")
        points += RISK_RULES["permit_unverified"]

    category = str(_get(case, "change_category", "") or "").casefold()
    change_score = _get(case, "change_percentage")
    if category == "possible new building on a previously empty plot":
        points += RISK_RULES["potential_new_building"]
        reasons.append("Potential new-building change was recorded by image screening; officer verification is required.")
    elif category == "possible additional floor or building extension":
        points += RISK_RULES["potential_extension"]
        reasons.append("Potential building extension was recorded; it is not a confirmed construction finding.")
    elif category == "other visible structural change":
        points += RISK_RULES["other_structural_change"]
        reasons.append("Other potential structural image change was recorded for review.")
    elif case_type == "image_detection" and category != "no significant change detected":
        if _positive_finite_number(change_score):
            points += RISK_RULES["unspecified_recorded_change"]
            reasons.append("A potential image change was recorded; the pixel score is not a construction-area estimate.")
        else:
            reasons.append("Image-change result is inconclusive; additional evidence is required.")

    if case_type == "image_detection":
        if quality == "Pass":
            points += RISK_RULES["quality_pass"]
            reasons.append("Both image quality reports passed the configured heuristic checks.")
        elif quality == "Warning":
            reasons.append("Image quality has warnings; interpret the change result cautiously.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
        elif quality == "Unsuitable":
            reasons.append("Image quality is unsuitable; do not treat its change result as reliable evidence.")
            cap = min(cap, RISK_RULES["unsuitable_evidence_score_cap"])
        else:
            reasons.append("Image quality status is unavailable; verification is required before relying on this result.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])

        if alignment == "Pass":
            points += RISK_RULES["alignment_pass"]
            reasons.append("Image alignment passed the configured screening; this does not prove geographic registration.")
        elif alignment == "Warning":
            reasons.append("Alignment has warnings; confirm the images cover the same location and view.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
        elif alignment == "Failed":
            reasons.append("Image alignment failed; the change result must not be treated as reliable evidence.")
            cap = min(cap, RISK_RULES["unsuitable_evidence_score_cap"])
        else:
            reasons.append("Alignment status is unavailable; geographic correspondence requires verification.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])

    property_id = _get(property_record, "location_id") or _get(case, "property_location_id")
    property_reference = (
        _get(property_record, "property_reference")
        or _get(case, "building_reference")
    )
    if property_record is None:
        reasons.append("Property association requires verification; no registered property is linked by an exact identifier.")
        cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
    else:
        location_status = str(_get(property_record, "verification_status", "Unverified") or "Unverified")
        if location_status.casefold() == "verified":
            points += RISK_RULES["verified_location"]
            reasons.append("The linked property coordinates are marked verified in the registry.")
        else:
            reasons.append("Property coordinates are unverified; confirm the location before field action.")
            cap = min(cap, RISK_RULES["unverified_location_score_cap"])

    # Only compare floors when the local permit registry has a verified record.
    approved_floors = _get(permit_record, "approved_floors") if permit_state == "Verified" else None
    observed_floors = _get(case, "observed_floors") if case_type == "construction" else _get(case, "recent_observed_floors")
    floor_evidence_verified = (
        case_type == "construction"
        or (
            str(_get(case, "floor_verification_status", "")).casefold() == "verified"
            and bool(str(_get(case, "floor_evidence_source", "") or "").strip())
        )
    )
    if approved_floors is not None and observed_floors is not None and floor_evidence_verified:
        try:
            floor_mismatch = int(approved_floors) != int(observed_floors)
        except (TypeError, ValueError):
            floor_mismatch = None
        if floor_mismatch is None:
            reasons.append("Insufficient evidence — officer verification required for the recorded floor counts.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
        elif floor_mismatch:
            points += RISK_RULES["verified_permit_discrepancy"]
            reasons.append(
                f"Potential floor-count discrepancy: {observed_floors} recorded observed versus "
                f"{approved_floors} approved in the verified permit record. Confirm on site."
            )
    elif case_type == "construction" and _get(case, "approved_floors") is not None and observed_floors is not None and not permit_record:
        try:
            legacy_floor_mismatch = int(_get(case, "approved_floors")) != int(observed_floors)
        except (TypeError, ValueError):
            legacy_floor_mismatch = False
        if legacy_floor_mismatch:
            points += RISK_RULES["legacy_unverified_floor_difference"]
            reasons.append(
                "A floor-count difference is recorded in the case, but the approved count is not linked to a verified permit record."
            )
    if permit_record and _get(permit_record, "validity_date"):
        try:
            expiry = date.fromisoformat(str(_get(permit_record, "validity_date")))
            if expiry < today and permit_state == "Verified":
                if not any("expired" in item.casefold() for item in reasons):
                    points += RISK_RULES["permit_expired"]
                    reasons.append("Verified permit validity date has passed; confirm renewal or extension records.")
                permit_state = "Expired"
        except ValueError:
            reasons.append("Permit validity date is invalid; officer verification is required.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])

    approved_area = _get(permit_record, "approved_area_sqm") if permit_state == "Verified" else None
    observed_area = _get(case, "observed_area_sqm")
    area_source = str(_get(case, "observed_area_source", "") or "").strip()
    area_crs = str(_get(case, "observed_area_crs", "") or "").strip()
    area_verified = bool(_get(case, "observed_area_verified")) and bool(area_source and area_crs)
    if approved_area is not None and observed_area is not None and area_verified:
        try:
            approved_area = float(approved_area)
            observed_area = float(observed_area)
            if math.isfinite(approved_area) and math.isfinite(observed_area) and approved_area > 0:
                difference = abs(observed_area - approved_area) / approved_area
                if difference > 0.10:
                    points += RISK_RULES["verified_permit_discrepancy"]
                    reasons.append(
                        f"Potential built-up-area discrepancy: verified measurement {observed_area:.1f} m² "
                        f"versus permit scope {approved_area:.1f} m² (source: {area_source}; CRS: {area_crs})."
                    )
            else:
                reasons.append("Insufficient evidence — officer verification required for the built-up-area measurement.")
                cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
        except (TypeError, ValueError):
            reasons.append("Insufficient evidence — officer verification required for the built-up-area measurement.")
            cap = min(cap, RISK_RULES["unknown_evidence_score_cap"])
    else:
        reasons.append("Insufficient evidence — officer verification required for a defensible built-up-area comparison; no area discrepancy is inferred.")

    approved_use = str(_get(permit_record, "approved_land_use", "") or "").strip()
    observed_use = str(_get(case, "observed_land_use", "") or "").strip()
    observed_use_source = str(_get(case, "observed_land_use_source", "") or "").strip()
    if (
        permit_state == "Verified"
        and approved_use
        and observed_use
        and bool(_get(case, "observed_land_use_verified"))
        and observed_use_source
    ):
        if approved_use.casefold() != observed_use.casefold():
            points += RISK_RULES["verified_permit_discrepancy"]
            reasons.append(
                f"Potential land-use discrepancy between verified permit scope and recorded observation "
                f"(observation source: {observed_use_source}); authority review is required."
            )
    elif approved_use:
        reasons.append("No verified observed land-use evidence is available for comparison.")

    score = max(0, min(100, min(points, cap)))
    risk_category = _category_for_score(score)
    if risk_category == "LOW PRIORITY":
        recommended = "Routine record check; retain the case for normal officer review."
    elif risk_category == "REVIEW REQUIRED":
        recommended = "Verify the property association, imagery quality/alignment, and permit source before relying on the finding."
    elif risk_category == "HIGH PRIORITY":
        recommended = "Prioritise officer investigation and compare the cited permit record with a site inspection."
    else:
        recommended = "Prompt senior officer review of the cited evidence; make no legal conclusion from this score."
    if quality == "Unsuitable" or alignment == "Failed":
        recommended = "Reacquire or verify suitable, aligned evidence before using the image finding to prioritise field action."
    elif property_record is None:
        recommended = "Confirm the exact registered property association before field action."
    elif permit_state in ("Not Available", "Unverified", "Expired", "Discrepancy Requires Review"):
        recommended += " Verify permit information against an authorised source; missing records do not establish non-compliance."

    evidence = {
        "earlier_image_date": _get(case, "earlier_image_date"),
        "newer_image_date": _get(case, "newer_image_date"),
        "detection_method": _get(case, "detection_method"),
        "imagery_source": _get(case, "imagery_source"),
        "earlier_scene_id": _get(case, "earlier_scene_id"),
        "newer_scene_id": _get(case, "newer_scene_id"),
        "change_score_percent": change_score,
        "review_status": _get(case, "review_status", "Pending Review"),
        "property_ward": _get(property_record, "ward_number") if property_record else _get(case, "ward_number"),
        "latitude": _get(property_record, "latitude") if property_record else _get(case, "latitude"),
        "longitude": _get(property_record, "longitude") if property_record else _get(case, "longitude"),
        "coordinate_verification_status": _get(property_record, "verification_status") if property_record else "Unknown",
        "earlier_image_ref": _get(case, "earlier_image_ref"),
        "newer_image_ref": _get(case, "newer_image_ref"),
        "legacy_permit_number": _get(case, "permit_number"),
        "legacy_permit_status": _get(case, "permit_status"),
        "permit_record_id": _get(permit_record, "permit_id"),
        "permit_reference": _get(permit_record, "permit_number"),
        "permit_authority": _get(permit_record, "issuing_authority"),
        "permit_source_reference": _get(permit_record, "source_reference"),
        "approved_floors": _get(permit_record, "approved_floors"),
        "approved_area_sqm": _get(permit_record, "approved_area_sqm"),
        "approved_land_use": _get(permit_record, "approved_land_use"),
    }
    return RiskAssessment(
        case_type=case_type,
        case_id=case_id,
        assessment_key=_assessment_key(case_type, case, property_record),
        property_location_id=property_id,
        property_reference=str(property_reference) if property_reference else None,
        risk_score=int(score),
        risk_category=risk_category,
        image_quality_status=quality,
        alignment_status=alignment,
        permit_status=permit_state,
        reasons=list(dict.fromkeys(reasons)),
        recommended_action=recommended,
        evidence=evidence,
    )
