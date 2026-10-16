# SmartPanchayat evaluation metrics

The repository currently has no labeled evaluation dataset. The application
therefore reports Accuracy, Precision, Recall, and F1-Score as **Not
evaluated** until an officer/researcher supplies a held-out, manually reviewed
CSV. Unit-test fixtures are software checks only and are not performance
evidence.

## Prepare a held-out test CSV

Create a UTF-8 CSV with exactly one row per independently reviewed case and
these columns:

```csv
sample_id,split,dataset_version,prediction_method,ground_truth,prediction
```

- `sample_id`: unique, non-sensitive sample key.
- `split`: must be `test` for every row. Keep training and tuning data in a
  different file; mixed splits are rejected.
- `dataset_version`: one immutable version identifier for the complete file.
- `prediction_method`: one frozen detector/rule version for the complete file.
- `ground_truth`: manually adjudicated `change` or `no_change` (or `1` / `0`).
- `prediction`: output from that same fixed method, using the same labels.

Define the case-level meaning of “change” before looking at test results.
Record how disagreements were adjudicated. Do not tune thresholds or select
examples based on this test set. Use an independent held-out set for each
method/version comparison. Avoid including personal or unnecessary property
information.

Upload the file in the **Evaluation Details** expander on Dashboard, Image
Comparison, Change Detection, or Officer Alerts & Risk. The app displays the
dataset version, row count, method, evaluation date, class distribution, and
TP/TN/FP/FN counts. Results stay in the current Streamlit session and do not
alter SQLite.

## Reproduce a calculation from PowerShell

With the project environment active, run the same calculation from the local
file and pin the evaluation date when reproducing a report:

```powershell
cd "C:\Users\HP\OneDrive\Desktop\smartpanchayath"
@'
from pathlib import Path
from evaluation_metrics import evaluate_labeled_csv

result = evaluate_labeled_csv(
    Path("heldout_test.csv").read_bytes(),
    evaluation_date="2026-10-17",
)
print(result.as_dict())
'@ | .\.venv\Scripts\python.exe -
```

Accuracy is `(TP + TN) / N`; Precision is `TP / (TP + FP)`; Recall is
`TP / (TP + FN)`; F1 is the harmonic mean of Precision and Recall. A
zero-denominator Precision, Recall, or F1 is returned as 0 and noted in the
result. Metrics describe only the submitted held-out test set and prediction
method; they do not describe legality or guarantee performance on future
imagery.

## Evidence Confidence heuristic

The separate Evidence Confidence display is an **uncalibrated evidence-quality
heuristic**, not an evaluated model score. When two valid image-quality reports
and a passing alignment report with a finite confidence value are available,
the score is:

`0.60 × mean image-quality points + 0.40 × alignment heuristic confidence`

Each Pass image-quality status contributes 100 points; Warning contributes 60.
Unsuitable/unknown quality, failed alignment, or missing alignment confidence
produces no score. The alignment component is the existing ORB/RANSAC screening
heuristic. This score does not measure detection accuracy, probability of
construction, or legal status. The weights/status mapping are transparent
screening choices and are not calibrated against labeled data.
