"""Offline checks for evidence-confidence and labeled evaluation metrics."""

import unittest

from evaluation_metrics import (
    calculate_evidence_confidence,
    confidence_from_saved_reports,
    evaluate_labeled_csv,
    mean_evidence_confidence,
)


class EvidenceConfidenceTests(unittest.TestCase):
    def test_confidence_uses_quality_and_alignment_not_change_score(self):
        result = calculate_evidence_confidence(
            {"valid": True, "status": "Pass"},
            {"valid": True, "status": "Warning"},
            {"status": "Pass", "confidence": 0.8},
        )
        self.assertEqual(result.score, 80)
        self.assertEqual(result.quality_component, 80)
        self.assertEqual(result.alignment_component, 80)

    def test_unsuitable_or_missing_evidence_has_no_confidence_score(self):
        unsuitable = calculate_evidence_confidence(
            {"valid": False, "status": "Unsuitable"},
            {"valid": True, "status": "Pass"},
            {"status": "Pass", "confidence": 0.9},
        )
        missing_alignment = calculate_evidence_confidence(
            {"valid": True, "status": "Pass"},
            {"valid": True, "status": "Pass"},
            {"status": "Pass"},
        )
        self.assertIsNone(unsuitable.score)
        self.assertIsNone(missing_alignment.score)

        validity_not_recorded = calculate_evidence_confidence(
            {"status": "Pass"},
            {"valid": True, "status": "Pass"},
            {"status": "Pass", "confidence": 0.9},
        )
        self.assertIsNone(validity_not_recorded.score)

    def test_saved_reports_and_aggregate_skip_unavailable(self):
        saved = confidence_from_saved_reports(
            '{"earlier":{"valid":true,"status":"Pass"},"newer":{"valid":true,"status":"Pass"}}',
            '{"status":"Pass","confidence":0.5}',
        )
        aggregate = mean_evidence_confidence([saved, confidence_from_saved_reports(None, None)])
        self.assertEqual(saved.score, 80)
        self.assertEqual(aggregate.score, 80)
        self.assertEqual(aggregate.eligible_comparisons, 1)


class BinaryEvaluationTests(unittest.TestCase):
    HEADER = "sample_id,split,dataset_version,prediction_method,ground_truth,prediction\n"

    def test_confusion_matrix_and_metrics(self):
        rows = (
            "a,test,v1,opencv-rule-v1,change,change\n"
            "b,test,v1,opencv-rule-v1,no_change,no_change\n"
            "c,test,v1,opencv-rule-v1,no_change,change\n"
            "d,test,v1,opencv-rule-v1,change,no_change\n"
        )
        result = evaluate_labeled_csv(self.HEADER + rows, evaluation_date="2026-10-17")
        self.assertEqual((result.true_positive, result.true_negative, result.false_positive, result.false_negative), (1, 1, 1, 1))
        self.assertEqual(result.accuracy, 0.5)
        self.assertEqual(result.precision, 0.5)
        self.assertEqual(result.recall, 0.5)
        self.assertEqual(result.f1_score, 0.5)
        self.assertEqual(result.sample_count, 4)
        self.assertEqual(result.change_count, 2)
        self.assertEqual(result.no_change_count, 2)

    def test_zero_denominators_are_safe_and_noted(self):
        result = evaluate_labeled_csv(
            self.HEADER + "a,test,v1,opencv-rule-v1,no_change,no_change\n"
        )
        self.assertEqual(result.precision, 0.0)
        self.assertEqual(result.recall, 0.0)
        self.assertEqual(result.f1_score, 0.0)
        self.assertTrue(result.notes)

    def test_training_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "only held-out rows"):
            evaluate_labeled_csv(
                self.HEADER + "a,train,v1,opencv-rule-v1,change,change\n"
            )

    def test_duplicate_headers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate column names"):
            evaluate_labeled_csv(
                "sample_id,split,dataset_version,prediction_method,ground_truth,prediction,prediction\n"
                "a,test,v1,opencv-rule-v1,change,change,change\n"
            )

    def test_mixed_versions_methods_and_duplicate_samples_are_rejected(self):
        mixed_version = self.HEADER + (
            "a,test,v1,opencv-rule-v1,change,change\n"
            "b,test,v2,opencv-rule-v1,change,change\n"
        )
        mixed_method = self.HEADER + (
            "a,test,v1,method-a,change,change\n"
            "b,test,v1,method-b,change,change\n"
        )
        duplicate = self.HEADER + (
            "a,test,v1,opencv-rule-v1,change,change\n"
            "a,test,v1,opencv-rule-v1,change,change\n"
        )
        for csv_text, message in (
            (mixed_version, "exactly one dataset_version"),
            (mixed_method, "exactly one prediction_method"),
            (duplicate, "duplicate sample_id"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    evaluate_labeled_csv(csv_text)


if __name__ == "__main__":
    unittest.main()
