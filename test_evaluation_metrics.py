"""Synthetic unit tests; these are pipeline checks, not accuracy evidence."""

import unittest

from evaluation_metrics import (
    calculate_binary_metrics,
    calculate_evidence_confidence,
    confidence_from_saved_reports,
    evaluate_labeled_csv,
    evaluation_report_json,
    mean_evidence_confidence,
)


def quality(status="Pass", *, blur=25.0, contrast=14.0, extreme=0.0):
    return {
        "valid": True,
        "status": status,
        "blur_score": blur,
        "contrast_std": contrast,
        "extreme_pixel_percentage": extreme,
    }


class EvidenceConfidenceTests(unittest.TestCase):
    def test_confidence_uses_quality_alignment_and_valid_coverage(self):
        result = calculate_evidence_confidence(
            quality(), quality("Warning", blur=12.5, contrast=7.0, extreme=20),
            {"status": "Pass", "confidence": 0.8, "valid_mask": [[255, 0], [255, 255]]},
        )
        self.assertIsNotNone(result.score)
        self.assertEqual(result.quality_component, 78.0)
        self.assertEqual(result.alignment_component, 80.0)
        self.assertEqual(result.valid_coverage_component, 75.0)
        self.assertEqual(result.score, 78)  # 0.4*78 + 0.4*80 + 0.2*75 = 78.2
        self.assertIn("Not Statistically Validated", result.reason)

    def test_missing_or_unsuitable_inputs_have_no_score(self):
        cases = [
            (quality("Unsuitable"), quality(), {"status": "Pass", "confidence": 1.0, "valid_mask": [[1]]}),
            (quality(), quality(), {"status": "Failed", "confidence": 1.0, "valid_mask": [[1]]}),
            (quality(), quality(), {"status": "Pass", "confidence": 1.0}),
            ({"valid": True, "status": "Pass"}, quality(), {"status": "Pass", "confidence": 1.0, "valid_mask": [[1]]}),
            (quality(), quality(), {"status": "Pass", "confidence": float("nan"), "valid_mask": [[1]]}),
        ]
        for earlier, newer, alignment in cases:
            with self.subTest(alignment=alignment):
                self.assertIsNone(calculate_evidence_confidence(earlier, newer, alignment).score)

    def test_saved_reports_require_stored_valid_pixel_coverage(self):
        quality_json = (
            '{"earlier":{"valid":true,"status":"Pass","blur_score":25,'
            '"contrast_std":14,"extreme_pixel_percentage":0},'
            '"newer":{"valid":true,"status":"Pass","blur_score":25,'
            '"contrast_std":14,"extreme_pixel_percentage":0}}'
        )
        alignment_json = '{"status":"Pass","confidence":0.5}'
        missing = confidence_from_saved_reports(quality_json, alignment_json)
        saved = confidence_from_saved_reports(quality_json, alignment_json, 80)
        aggregate = mean_evidence_confidence([missing, saved])
        self.assertIsNone(missing.score)
        self.assertEqual(saved.score, 76)
        self.assertEqual(aggregate.score, 76)
        self.assertEqual(aggregate.eligible_comparisons, 1)
        self.assertEqual(aggregate.alignment_component, 50.0)
        self.assertEqual(aggregate.valid_coverage_component, 80.0)


class BinaryEvaluationTests(unittest.TestCase):
    HEADER = "sample_id,split,dataset_version,prediction_method,ground_truth,prediction\n"

    def test_requested_confusion_matrix_fixture(self):
        metrics = calculate_binary_metrics(40, 50, 10, 5)
        self.assertEqual(metrics.sample_count, 105)
        self.assertEqual(metrics.accuracy, 90 / 105)
        self.assertEqual(metrics.precision, 0.8)
        self.assertEqual(metrics.recall, 40 / 45)
        self.assertEqual(metrics.f1_score, 80 / 95)
        self.assertAlmostEqual(metrics.accuracy * 100, 85.71, places=2)
        self.assertAlmostEqual(metrics.precision * 100, 80.00, places=2)
        self.assertAlmostEqual(metrics.recall * 100, 88.89, places=2)
        self.assertAlmostEqual(metrics.f1_score * 100, 84.21, places=2)

    def test_csv_confusion_counts_and_distribution(self):
        rows = (
            "a,test,v1,opencv-rule-v1,change,change\n"
            "b,test,v1,opencv-rule-v1,no_change,no_change\n"
            "c,test,v1,opencv-rule-v1,no_change,change\n"
            "d,test,v1,opencv-rule-v1,change,no_change\n"
        )
        result = evaluate_labeled_csv(self.HEADER + rows, evaluation_date="2026-10-17")
        self.assertEqual((result.true_positive, result.true_negative, result.false_positive, result.false_negative), (1, 1, 1, 1))
        self.assertEqual((result.accuracy, result.precision, result.recall, result.f1_score), (0.5, 0.5, 0.5, 0.5))
        self.assertEqual((result.sample_count, result.change_count, result.no_change_count), (4, 2, 2))

    def test_zero_denominators_are_unavailable_not_zero(self):
        no_positive = calculate_binary_metrics(0, 4, 0, 0)
        no_positive_predictions = calculate_binary_metrics(0, 4, 0, 2)
        empty = calculate_binary_metrics(0, 0, 0, 0)
        self.assertEqual(no_positive.accuracy, 1.0)
        self.assertIsNone(no_positive.precision)
        self.assertIsNone(no_positive.recall)
        self.assertIsNone(no_positive.f1_score)
        self.assertIsNone(no_positive_predictions.precision)
        self.assertEqual(no_positive_predictions.recall, 0.0)
        self.assertEqual(no_positive_predictions.f1_score, 0.0)
        self.assertIsNone(empty.accuracy)
        self.assertIsNone(empty.precision)
        self.assertIsNone(empty.recall)
        self.assertIsNone(empty.f1_score)

    def test_invalid_rows_are_reported_and_excluded(self):
        csv_text = self.HEADER + (
            "good,test,v1,method,change,change\n"
            "bad,test,v1,method,unknown,change\n"
            "missing-label,test,v1,method,,no_change\n"
        )
        result = evaluate_labeled_csv(csv_text, evaluation_date="2026-10-17")
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(result.skipped_count, 2)
        self.assertEqual(result.true_positive, 1)
        self.assertEqual(len(result.skipped_samples), 2)
        report = evaluation_report_json(result)
        self.assertIn('"skipped_count": 2', report)
        self.assertNotIn("good", report)

    def test_training_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "only held-out rows"):
            evaluate_labeled_csv(self.HEADER + "a,train,v1,m,change,change\n")

    def test_duplicate_headers_versions_methods_and_samples_are_rejected(self):
        cases = [
            ("sample_id,split,dataset_version,prediction_method,ground_truth,prediction,prediction\na,test,v1,m,change,change,change\n", "duplicate column names"),
            (self.HEADER + "a,test,v1,m,change,change\nb,test,v2,m,change,change\n", "exactly one dataset_version"),
            (self.HEADER + "a,test,v1,m1,change,change\nb,test,v1,m2,change,change\n", "exactly one prediction_method"),
            (self.HEADER + "a,test,v1,m,change,change\na,test,v1,m,change,change\n", "duplicate sample_id"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    evaluate_labeled_csv(text)

    def test_confusion_counts_must_be_nonnegative_integers(self):
        for values in ((-1, 1, 1, 1), (1.5, 1, 1, 1), (True, 1, 1, 1)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    calculate_binary_metrics(*values)


if __name__ == "__main__":
    unittest.main()
