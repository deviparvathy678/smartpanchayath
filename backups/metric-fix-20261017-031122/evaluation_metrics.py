"""Transparent evidence-quality and held-out binary evaluation metrics.

Evidence confidence is an uncalibrated screening heuristic over image-quality
statuses and the existing alignment heuristic. It is not model confidence,
construction probability, legal status, or detection accuracy.

Evaluation metrics are calculated only from explicitly supplied labeled test
rows. This module has no Streamlit, SQLite, or scikit-learn dependency.
"""

from dataclasses import asdict, dataclass
from datetime import date
import csv
import io
import math
import json


QUALITY_STATUS_POINTS = {"Pass": 100.0, "Warning": 60.0}
QUALITY_WEIGHT = 0.60
ALIGNMENT_WEIGHT = 0.40
MAX_EVALUATION_CSV_BYTES = 5 * 1024 * 1024
MAX_EVALUATION_ROWS = 100_000


@dataclass(frozen=True)
class EvidenceConfidence:
    score: int | None
    reason: str
    earlier_quality_status: str = "Unknown"
    newer_quality_status: str = "Unknown"
    alignment_status: str = "Unknown"
    quality_component: float | None = None
    alignment_component: float | None = None
    formula: str = (
        "60% × mean image-quality points + 40% × alignment heuristic confidence"
    )
    eligible_comparisons: int | None = None

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class BinaryEvaluation:
    dataset_version: str
    sample_count: int
    change_count: int
    no_change_count: int
    prediction_method: str
    evaluation_method: str
    evaluation_date: str
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    notes: tuple[str, ...] = ()

    def as_dict(self):
        return asdict(self)


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except (AttributeError, TypeError):
        return default


def _quality_status(report):
    status = str(_value(report, "status", "Unknown") or "Unknown").strip().title()
    valid = _value(report, "valid", None)
    if valid is False:
        return "Unsuitable"
    if valid is not True:
        return "Unknown"
    if status not in ("Pass", "Warning", "Unsuitable"):
        return "Unknown"
    return status


def calculate_evidence_confidence(earlier_quality, newer_quality, alignment):
    """Return an evidence-readiness heuristic, or no score when unsupported.

    Pass quality contributes 100 points per image and Warning contributes 60.
    Unsuitable/unknown reports or non-passing/missing alignment return no score.
    The existing alignment confidence must be finite and in [0, 1].
    """
    earlier_status = _quality_status(earlier_quality)
    newer_status = _quality_status(newer_quality)
    alignment_status = str(_value(alignment, "status", "Unknown") or "Unknown").strip()
    alignment_status = alignment_status.split("—", 1)[0].strip().title()

    if earlier_status not in QUALITY_STATUS_POINTS or newer_status not in QUALITY_STATUS_POINTS:
        return EvidenceConfidence(
            None,
            "A confidence score requires two valid image-quality reports; unsuitable or unknown inputs are not scored.",
            earlier_status,
            newer_status,
            alignment_status,
        )
    if alignment_status != "Pass":
        return EvidenceConfidence(
            None,
            "A confidence score requires a passing alignment assessment.",
            earlier_status,
            newer_status,
            alignment_status,
        )

    try:
        alignment_confidence = float(_value(alignment, "confidence"))
    except (TypeError, ValueError):
        alignment_confidence = math.nan
    if not math.isfinite(alignment_confidence) or not 0.0 <= alignment_confidence <= 1.0:
        return EvidenceConfidence(
            None,
            "The alignment confidence value is missing or outside its supported range.",
            earlier_status,
            newer_status,
            alignment_status,
        )

    quality_component = (
        QUALITY_STATUS_POINTS[earlier_status] + QUALITY_STATUS_POINTS[newer_status]
    ) / 2.0
    score = round(
        QUALITY_WEIGHT * quality_component
        + ALIGNMENT_WEIGHT * alignment_confidence * 100.0
    )
    return EvidenceConfidence(
        score=score,
        reason="Uncalibrated evidence-quality heuristic; not model probability or detection accuracy.",
        earlier_quality_status=earlier_status,
        newer_quality_status=newer_status,
        alignment_status=alignment_status,
        quality_component=quality_component,
        alignment_component=alignment_confidence * 100.0,
    )


def _decode_json_object(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def confidence_from_saved_reports(quality_report, alignment_report):
    """Calculate confidence from the JSON reports already stored per detection."""
    quality = _decode_json_object(quality_report)
    alignment = _decode_json_object(alignment_report)
    earlier = quality.get("earlier")
    newer = quality.get("newer")
    if not isinstance(earlier, dict) or not isinstance(newer, dict):
        return EvidenceConfidence(None, "Saved per-image quality reports are unavailable.")
    if not alignment:
        return EvidenceConfidence(
            None,
            "A saved alignment report is unavailable.",
            _quality_status(earlier),
            _quality_status(newer),
            "Unknown",
        )
    return calculate_evidence_confidence(earlier, newer, alignment)


def mean_evidence_confidence(confidences):
    """Summarize available per-comparison scores without inventing missing ones."""
    scores = [item.score for item in confidences if item is not None and item.score is not None]
    if not scores:
        return EvidenceConfidence(
            None,
            "No saved image comparisons have sufficient quality and alignment reports.",
            eligible_comparisons=0,
        )
    return EvidenceConfidence(
        score=round(sum(scores) / len(scores)),
        reason="Mean of eligible saved image-comparison evidence scores; unavailable comparisons are excluded.",
        eligible_comparisons=len(scores),
    )


def _binary_label(value, field_name, row_number):
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"1", "change", "changed", "potential_change", "positive", "yes", "true"}:
        return 1
    if normalized in {"0", "no_change", "unchanged", "negative", "no", "false"}:
        return 0
    raise ValueError(
        f"Row {row_number}: {field_name} must be a binary change/no-change label (0/1 or text label)."
    )


def evaluate_labeled_csv(csv_content, *, evaluation_date=None):
    """Evaluate a CSV containing only held-out binary test-set predictions.

    Required columns: sample_id, split, dataset_version, prediction_method,
    ground_truth, prediction. Every row must be test data; mixed train/test
    files are rejected. Zero-denominator precision/recall/F1 values are 0.0
    and explicitly noted.
    """
    if isinstance(csv_content, str):
        raw = csv_content.encode("utf-8")
    else:
        raw = bytes(csv_content)
    if not raw or len(raw) > MAX_EVALUATION_CSV_BYTES:
        raise ValueError("Evaluation CSV must be non-empty and no larger than 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Evaluation CSV must use UTF-8 encoding.") from error

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Evaluation CSV must include a header row.")
    normalized_headers = [str(column or "").strip() for column in reader.fieldnames]
    if len(normalized_headers) != len(set(normalized_headers)):
        raise ValueError("Evaluation CSV contains duplicate column names.")
    reader.fieldnames = normalized_headers
    required = {
        "sample_id", "split", "dataset_version", "prediction_method",
        "ground_truth", "prediction",
    }
    columns = set(normalized_headers)
    missing = sorted(required - columns)
    if missing:
        raise ValueError("Missing required CSV columns: " + ", ".join(missing))

    rows = []
    sample_ids = set()
    versions = set()
    methods = set()
    for row_number, row in enumerate(reader, start=2):
        if row_number > MAX_EVALUATION_ROWS + 1:
            raise ValueError(f"Evaluation CSV exceeds {MAX_EVALUATION_ROWS:,} test rows.")
        if None in row:
            raise ValueError(f"Row {row_number}: unexpected extra CSV fields.")
        sample_id = str(row.get("sample_id") or "").strip()
        split = str(row.get("split") or "").strip().casefold()
        version = str(row.get("dataset_version") or "").strip()
        method = str(row.get("prediction_method") or "").strip()
        if not sample_id or not version or not method:
            raise ValueError(f"Row {row_number}: sample_id, dataset_version, and prediction_method are required.")
        if split != "test":
            raise ValueError(
                f"Row {row_number}: only held-out rows with split=test are allowed; training rows must stay separate."
            )
        if sample_id in sample_ids:
            raise ValueError(f"Row {row_number}: duplicate sample_id {sample_id!r}.")
        sample_ids.add(sample_id)
        versions.add(version)
        methods.add(method)
        rows.append((
            _binary_label(row.get("ground_truth"), "ground_truth", row_number),
            _binary_label(row.get("prediction"), "prediction", row_number),
        ))
    if not rows:
        raise ValueError("Evaluation CSV contains no labeled test rows.")
    if len(versions) != 1:
        raise ValueError("Use exactly one dataset_version per evaluation run.")
    if len(methods) != 1:
        raise ValueError("Use exactly one prediction_method per evaluation run.")

    true_positive = sum(actual == 1 and predicted == 1 for actual, predicted in rows)
    true_negative = sum(actual == 0 and predicted == 0 for actual, predicted in rows)
    false_positive = sum(actual == 0 and predicted == 1 for actual, predicted in rows)
    false_negative = sum(actual == 1 and predicted == 0 for actual, predicted in rows)
    sample_count = len(rows)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / recall_denominator if recall_denominator else 0.0
    f1_denominator = precision + recall
    f1_score = 2 * precision * recall / f1_denominator if f1_denominator else 0.0
    notes = []
    if not precision_denominator:
        notes.append("Precision is set to 0 because there are no positive predictions.")
    if not recall_denominator:
        notes.append("Recall is set to 0 because the test set contains no positive ground-truth samples.")
    return BinaryEvaluation(
        dataset_version=next(iter(versions)),
        sample_count=sample_count,
        change_count=sum(actual == 1 for actual, _ in rows),
        no_change_count=sum(actual == 0 for actual, _ in rows),
        prediction_method=next(iter(methods)),
        evaluation_method="Binary case-level change / no-change classification",
        evaluation_date=str(evaluation_date or date.today().isoformat()),
        true_positive=true_positive,
        true_negative=true_negative,
        false_positive=false_positive,
        false_negative=false_negative,
        accuracy=(true_positive + true_negative) / sample_count,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        notes=tuple(notes),
    )
