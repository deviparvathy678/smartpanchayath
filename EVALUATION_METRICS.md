# SmartPanchayat evaluation metrics

## Current evaluation status

No manually labeled, held-out evaluation dataset is included in this project.
Accuracy, precision, recall, and F1 therefore remain **Not evaluated** until a
test CSV with manually reviewed ground truth and predictions is supplied. Test
fixtures in `test_evaluation_metrics.py` only verify the software formulas;
they are not model performance evidence.

The current change detector is a pixel-difference workflow, not a trained
classifier. The evaluator supports **binary case-level change/no-change
classification** from explicit labels. It does not treat a pixel-difference
percentage as accuracy, and it does not currently evaluate pixel masks.

## Prepare a held-out test CSV

Use UTF-8 CSV with one row per independently reviewed case:

```csv
sample_id,split,dataset_version,prediction_method,ground_truth,prediction
```

- `sample_id`: unique, non-sensitive sample key.
- `split`: every row must be `test`; train/tuning rows are rejected.
- `dataset_version`: one immutable version for the file.
- `prediction_method`: one fixed detector/rule version for the file.
- `ground_truth`: adjudicated `change` or `no_change` (or `1` / `0`).
- `prediction`: that fixed method's result with the same binary meaning.

Define the case-level meaning of “change” before evaluating. Resolve label
disagreements before opening the test results. Do not tune thresholds or choose
examples based on the held-out test set. Invalid/missing label rows are skipped
and listed in the on-screen report; duplicate IDs, mixed methods/versions, and
non-test splits stop the evaluation. The report includes valid sample count,
skipped row count, class distribution, evaluation date, method, version, and
confusion counts. The app can download a JSON summary. Evaluation is session
only and does not write to SQLite.

## Metric definitions

- Accuracy = `(TP + TN) / (TP + TN + FP + FN)`
- Precision = `TP / (TP + FP)`
- Recall = `TP / (TP + FN)`
- F1 = `2 × TP / (2 × TP + FP + FN)`

If a denominator is zero, the corresponding value is unavailable and shown as
“Not evaluated,” with an explanatory note. The evaluator never substitutes
zero for a metric that has no meaningful denominator.

To reproduce the test calculation from PowerShell:

```powershell
cd "C:\Users\HP\OneDrive\Desktop\smartpanchayath"
.\.venv\Scripts\python.exe evaluation_metrics.py heldout_test.csv `
  --evaluation-date 2026-10-17 `
  --output evaluation-summary.json
```

For TP=40, TN=50, FP=10, FN=5, the expected case-level values are Accuracy
85.71%, Precision 80.00%, Recall 88.89%, and F1 84.21% (105 cases).

## Evidence confidence

The UI label is **Rule-based Evidence Confidence — Not Statistically
Validated**. It is separate from accuracy, risk priority, model probability,
property/legal status, and permit verification. It uses:

1. Per-image quality = `40% × min(blur_score / blur_warning_threshold, 1)` +
   `40% × min(contrast_std / contrast_warning_threshold, 1)` +
   `20% × (1 − extreme_pixel_percentage / 100)`.
2. Alignment component = existing ORB/RANSAC alignment heuristic confidence.
3. Valid-coverage component = measured valid pixels / comparison pixels.
4. Pair score = `40% × mean per-image quality` + `40% × alignment component` +
   `20% × valid-coverage component`.

Quality warning thresholds are read from `image_quality.QUALITY_THRESHOLDS`.
The score is available only for two valid Pass/Warning reports with numeric
blur, contrast, and exposure measures, passing alignment with finite confidence,
and measured valid-pixel coverage. Missing or unsuitable evidence, failed
alignment, or missing coverage returns **Not available**; missing inputs are
never filled with assumed values. Thresholds and weights are transparent
screening choices, are not calibrated using labels, and can vary in meaning by
sensor, scene, scale, and capture conditions. A high score does not establish
that a detected change is real or that a detector is accurate.

## Known limitations

- The uploaded CSV evaluates supplied case-level predictions; it does not run
  the detector over a folder of image pairs or ground-truth masks.
- No labeled evaluation dataset is currently present, so no real accuracy,
  precision, recall, or F1 claim is available.
- Synthetic formula tests validate arithmetic and input handling only.
- No metric estimates the probability of illegal construction. Human and
  official verification remains necessary.
