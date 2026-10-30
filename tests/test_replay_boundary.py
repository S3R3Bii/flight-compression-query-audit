import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import analyze
from compare_replay import Comparison


class ReplayBoundaryTests(unittest.TestCase):
    def test_final_cannot_run_without_a_committed_freeze(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyze, "ROOT", Path(folder)):
                with self.assertRaisesRegex(RuntimeError, "committed development"):
                    analyze.check_freeze(Path(folder) / "unavailable-private-runs.json")

    def test_boolean_and_count_failures_cannot_hide_in_float_tolerances(self):
        for expected, actual in [(True, 1), (41, 42), (False, True), (None, 0)]:
            with (
                self.subTest(expected=expected, actual=actual),
                self.assertRaises(AssertionError),
            ):
                Comparison().value(expected, actual, ("required_fact",))

    def test_as_run_hash_policy_never_ignores_source_or_retained_indices(self):
        with self.assertRaises(AssertionError):
            Comparison().value(
                {"source_sha256_unchanged": "a" * 64},
                {"source_sha256_unchanged": "b" * 64},
                as_run=True,
            )
        with self.assertRaises(AssertionError):
            Comparison().value(
                {"retained_indices": [0, 2]}, {"retained_indices": [0, 1]}, as_run=True
            )
        comparison = Comparison()
        comparison.value(
            {"point_diagnostics_sha256": "a" * 64},
            {"point_diagnostics_sha256": "b" * 64},
            as_run=True,
        )
        self.assertEqual(comparison.as_run_hashes, 1)

    def test_nonfinite_or_excessive_metric_difference_fails(self):
        for actual in ["NaN", "Infinity", ".000001"]:
            with self.subTest(actual=actual), self.assertRaises(AssertionError):
                Comparison().numeric("0", actual, "point_mae_m")
        comparison = Comparison()
        comparison.numeric("1", "1.000000001", "point_mae_m")
        self.assertEqual(comparison.tolerant, 1)


if __name__ == "__main__":
    unittest.main()
