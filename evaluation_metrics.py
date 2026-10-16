"""Evidence screening and held-out binary evaluation metrics.

Evidence confidence is a transparent, uncalibrated heuristic over measured
image-quality values, alignment screening confidence, and valid-pixel coverage.
It is not a detector probability, model confidence, legal status, or accuracy.

Binary evaluation uses only explicit ground-truth and prediction labels from a
single held-out test set. No Streamlit, SQLite, or scikit-learn dependency.
"""

from dataclasses import asdict, dataclass
from datetime import date
import csv
import io
import json
import math

import numpy as np

from image_quality import QUALITY_THRESHOLDS


MAX_EVALUATION_CSV_BYTES = 5 * 1024 * 1024
MAX_EVALUATION_ROWS = 100_000
CONFIDENCE_FORMULA = (
    "40% × mean per-image quality + 40% × alignment heuristic confidence "
    "+ 20% × valid-pixel coverage"
)


@dataclass(frozen=True)
class EvidenceConfidence:
    score: int | None
    reason: str
    earlier_quality_status: str = "Unknown"
    newer_quality_status: str = "Unknown"
    alignment_status: str = "Unknown"
    quality_component: float | None = None
    alignment_component: float | None = None
    valid_coverage_component: float | None = None
    earlier_quality_component: float | None = None
    newer_quality_component: float | None = None
    formula: str = CONFIDENCE_FORMULA
    eligible_comparisons: int | None = None

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class BinaryMetrics:
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    sample_count: int
    accuracy: float | None
    precision: float | None
    recall: float | None
    f1_score: float | None
    notes: tuple[str, ...] = ()

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class BinaryEvaluation(BinaryMetrics):
    dataset_version: str = "Unavailable"
    change_count: int = 0
    no_change_count: int = 0
    prediction_method: str = "Unavailable"
    evaluation_method: str = "Binary case-level change / no-change classification"
    evaluation_date: str = ""
    skipped_count: int = 0
    skipped_samples: tuple[dict, ...] = ()

    def as_dict(self):
        return asdict(self)


def calculate_binary_metrics(true_positive, true_negative, false_positive, false_negative):
    """Calculate binary classification metrics from explicit confusion counts.

    Undefined precision/recall/F1 values are ``None``. In particular, F1 is
    undefined when the test set has no positive truth or prediction cases.
    """
    values = (true_positive, true_negative, false_positive, false_negative)
    normalized = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0:
            raise ValueError("Confusion-matrix counts must be non-negative integers.")
        normalized.append(int(value))
    tp, tn, fp, fn = normalized
    total = tp + tn + fp + fn
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    f1_denominator = 2 * tp + fp + fn
    notes = []
    if total == 0:
        notes.append("Accuracy is unavailable because the test set is empty.")
    if precision_denominator == 0:
        notes.append("Precision is unavailable because there are no positive predictions.")
    if recall_denominator == 0:
        notes.append("Recall is unavailable because the test set has no positive ground-truth cases.")
    if f1_denominator == 0:
        notes.append("F1-score is unavailable because there are no positive predictions or positive ground-truth cases.")
    return BinaryMetrics(
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        sample_count=total,
        accuracy=(tp + tn) / total if total else None,
        precision=tp / precision_denominator if precision_denominator else None,
        recall=tp / recall_denominator if recall_denominator else None,
        f1_score=2 * tp / f1_denominator if f1_denominator else None,
        notes=tuple(notes),
    )


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
    return status if status in ("Pass", "Warning", "Unsuitable") else "Unknown"


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _image_quality_component(report):
    """Normalize measured quality inputs against the app's explicit warning thresholds."""
    blur = _finite_number(_value(report, "blur_score"))
    contrast = _finite_number(_value(report, "contrast_std"))
    extreme = _finite_number(_value(report, "extreme_pixel_percentage"))
    if blur is None or contrast is None or extreme is None:
        return None
    if blur < 0 or contrast < 0 or not 0 <= extreme <= 100:
        return None
    blur_limit = float(QUALITY_THRESHOLDS["blur_warning"])
    contrast_limit = float(QUALITY_THRESHOLDS["contrast_warning"])
    if blur_limit <= 0 or contrast_limit <= 0:
        return None
    # Scores are clipped to [0, 1], where 1 means meeting/exceeding the
    # project's current warning thresholds. These cutoffs are heuristic and
    # vary by sensor, scene texture, and image scale.
    blur_score = min(blur / blur_limit, 1.0)
    contrast_score = min(contrast / contrast_limit, 1.0)
    exposure_score = 1.0 - extreme / 100.0
    return 100.0 * (0.4 * blur_score + 0.4 * contrast_score + 0.2 * exposure_score)


def _valid_pixel_percentage(alignment):
    mask = _value(alignment, "valid_mask")
    if mask is None:
        return None
    try:
        array = np.asarray(mask)
        if array.size == 0:
            return None
        return float(np.count_nonzero(array) / array.size * 100.0)
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_evidence_confidence(
    earlier_quality, newer_quality, alignment, valid_pixel_percentage=None,
):
    """Return a rule-based evidence screening score only for supported inputs.

    The image-quality component uses Laplacian variance, grayscale contrast
    standard deviation, and non-extreme-pixel share. The alignment component
    is the existing ORB/RANSAC screening heuristic. Coverage is the measured
    share of valid pixels in the aligned comparison. A failed alignment,
    unsuitable image, or missing/invalid input produces no score.
    """
    earlier_status = _quality_status(earlier_quality)
    newer_status = _quality_status(newer_quality)
    alignment_status = str(_value(alignment, "status", "Unknown") or "Unknown").strip()
    alignment_status = alignment_status.split("—", 1)[0].strip().title()

    def unavailable(reason):
        return EvidenceConfidence(
            None, reason, earlier_status, newer_status, alignment_status,
        )

    if earlier_status not in ("Pass", "Warning") or newer_status not in ("Pass", "Warning"):
        return unavailable("Both images need valid quality reports; unsuitable or unknown images are not scored.")
    if alignment_status != "Pass":
        return unavailable("Evidence confidence requires a passing alignment assessment.")
    alignment_confidence = _finite_number(_value(alignment, "confidence"))
    if alignment_confidence is None or not 0.0 <= alignment_confidence <= 1.0:
        return unavailable("The alignment screening confidence is missing or outside [0, 1].")

    if valid_pixel_percentage is None:
        valid_pixel_percentage = _valid_pixel_percentage(alignment)
    coverage = _finite_number(valid_pixel_percentage)
    if coverage is None or not 0.0 < coverage <= 100.0:
        return unavailable("Measured valid-pixel coverage is missing or outside (0, 100].")

    earlier_component = _image_quality_component(earlier_quality)
    newer_component = _image_quality_component(newer_quality)
    if earlier_component is None or newer_component is None:
        return unavailable("Blur, contrast, and exposure measurements are required for both images.")
    quality_component = (earlier_component + newer_component) / 2.0
    alignment_component = alignment_confidence * 100.0
    score = round(
        0.4 * quality_component
        + 0.4 * alignment_component
        + 0.2 * coverage
    )
    return EvidenceConfidence(
        score=score,
        reason="Rule-based Evidence Confidence — Not Statistically Validated.",
        earlier_quality_status=earlier_status,
        newer_quality_status=newer_status,
        alignment_status=alignment_status,
        quality_component=quality_component,
        alignment_component=alignment_component,
        valid_coverage_component=coverage,
        earlier_quality_component=earlier_component,
        newer_quality_component=newer_component,
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


def confidence_from_saved_reports(quality_report, alignment_report, valid_pixel_percentage=None):
    """Calculate confidence using stored reports and stored valid coverage."""
    quality = _decode_json_object(quality_report)
    alignment = _decode_json_object(alignment_report)
    earlier = quality.get("earlier")
    newer = quality.get("newer")
    if not isinstance(earlier, dict) or not isinstance(newer, dict):
        return EvidenceConfidence(None, "Saved per-image quality reports are unavailable.")
    if not alignment:
        return EvidenceConfidence(
            None, "A saved alignment report is unavailable.",
            _quality_status(earlier), _quality_status(newer), "Unknown",
        )
    return calculate_evidence_confidence(
        earlier, newer, alignment, valid_pixel_percentage=valid_pixel_percentage,
    )


def mean_evidence_confidence(confidences):
    """Mean eligible scores; unavailable comparisons are excluded, not imputed."""
    eligible = [item for item in confidences if item is not None and item.score is not None]
    if not eligible:
        return EvidenceConfidence(
            None,
            "No saved comparisons have all required image-quality, alignment, and coverage measurements.",
            eligible_comparisons=0,
        )
    def mean_component(name):
        values = [getattr(item, name) for item in eligible]
        if any(value is None for value in values):
            return None
        return sum(values) / len(values)

    return EvidenceConfidence(
        score=round(sum(item.score for item in eligible) / len(eligible)),
        reason="Mean of eligible rule-based evidence scores; unavailable comparisons are excluded.",
        earlier_quality_status="Aggregate",
        newer_quality_status="Aggregate",
        alignment_status="Aggregate",
        quality_component=mean_component("quality_component"),
        alignment_component=mean_component("alignment_component"),
        valid_coverage_component=mean_component("valid_coverage_component"),
        earlier_quality_component=mean_component("earlier_quality_component"),
        newer_quality_component=mean_component("newer_quality_component"),
        eligible_comparisons=len(eligible),
    )


def _binary_label(value, field_name, row_number):
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"1", "change", "changed", "potential_change", "positive", "yes", "true"}:
        return 1
    if normalized in {"0", "no_change", "unchanged", "negative", "no", "false"}:
        return 0
    raise ValueError(f"{field_name} must be a binary change/no-change label.")


def evaluate_labeled_csv(csv_content, *, evaluation_date=None):
    """Evaluate a labeled CSV, report invalid rows, and exclude them explicitly.

    Required columns: sample_id, split, dataset_version, prediction_method,
    ground_truth, prediction. Only split=test is permitted. Structural issues
    that can mix methods/datasets or duplicate identity are rejected; rows with
    missing metadata or invalid labels are reported in skipped_samples.
    """
    raw = csv_content.encode("utf-8") if isinstance(csv_content, str) else bytes(csv_content)
    if not raw or len(raw) > MAX_EVALUATION_CSV_BYTES:
        raise ValueError("Evaluation CSV must be non-empty and no larger than 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Evaluation CSV must use UTF-8 encoding.") from error
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Evaluation CSV must include a header row.")
    headers = [str(column or "").strip() for column in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise ValueError("Evaluation CSV contains duplicate column names.")
    reader.fieldnames = headers
    required = {"sample_id", "split", "dataset_version", "prediction_method", "ground_truth", "prediction"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError("Missing required CSV columns: " + ", ".join(missing))

    valid_rows = []
    skipped = []
    sample_ids = set()
    versions = set()
    methods = set()
    for row_number, row in enumerate(reader, start=2):
        if row_number > MAX_EVALUATION_ROWS + 1:
            raise ValueError(f"Evaluation CSV exceeds {MAX_EVALUATION_ROWS:,} rows.")
        if None in row:
            raise ValueError(f"Row {row_number}: unexpected extra CSV fields.")
        sample_id = str(row.get("sample_id") or "").strip()
        split = str(row.get("split") or "").strip().casefold()
        version = str(row.get("dataset_version") or "").strip()
        method = str(row.get("prediction_method") or "").strip()
        if split and split != "test":
            raise ValueError(f"Row {row_number}: only held-out rows with split=test are allowed; training rows must stay separate.")
        if version:
            versions.add(version)
        if method:
            methods.add(method)
        if sample_id:
            if sample_id in sample_ids:
                raise ValueError(f"Row {row_number}: duplicate sample_id {sample_id!r}.")
            sample_ids.add(sample_id)
        if not sample_id or not split or not version or not method:
            skipped.append({"row": row_number, "sample_id": sample_id or None, "reason": "Required sample/test/dataset/method metadata is missing."})
            continue
        try:
            truth = _binary_label(row.get("ground_truth"), "ground_truth", row_number)
            prediction = _binary_label(row.get("prediction"), "prediction", row_number)
        except ValueError as error:
            skipped.append({"row": row_number, "sample_id": sample_id, "reason": str(error)})
            continue
        valid_rows.append((truth, prediction))
    if len(versions) > 1:
        raise ValueError("Use exactly one dataset_version per evaluation run.")
    if len(methods) > 1:
        raise ValueError("Use exactly one prediction_method per evaluation run.")

    tp = sum(actual == 1 and predicted == 1 for actual, predicted in valid_rows)
    tn = sum(actual == 0 and predicted == 0 for actual, predicted in valid_rows)
    fp = sum(actual == 0 and predicted == 1 for actual, predicted in valid_rows)
    fn = sum(actual == 1 and predicted == 0 for actual, predicted in valid_rows)
    metrics = calculate_binary_metrics(tp, tn, fp, fn)
    notes = list(metrics.notes)
    if skipped:
        notes.append(f"{len(skipped)} invalid row(s) were skipped and are listed in the report.")
    return BinaryEvaluation(
        **{**metrics.as_dict(), "notes": tuple(notes)},
        dataset_version=next(iter(versions), "Unavailable"),
        change_count=sum(actual == 1 for actual, _ in valid_rows),
        no_change_count=sum(actual == 0 for actual, _ in valid_rows),
        prediction_method=next(iter(methods), "Unavailable"),
        evaluation_date=str(evaluation_date or date.today().isoformat()),
        skipped_count=len(skipped),
        skipped_samples=tuple(skipped),
    )


def evaluation_report_json(evaluation):
    """Create a reproducible JSON summary without exposing sample identifiers."""
    result = evaluation.as_dict() if hasattr(evaluation, "as_dict") else dict(evaluation)
    result.pop("skipped_samples", None)
    result["report_schema"] = "smartpanchayat-binary-evaluation-v1"
    result["interpretation"] = "Held-out labeled test-set performance; not legal status."
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv=None):
    """CLI: python evaluation_metrics.py test.csv --output report.json"""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Evaluate a held-out SmartPanchayat label CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evaluation-date", default=None)
    args = parser.parse_args(argv)
    result = evaluate_labeled_csv(args.csv_path.read_bytes(), evaluation_date=args.evaluation_date)
    report = evaluation_report_json(result)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
