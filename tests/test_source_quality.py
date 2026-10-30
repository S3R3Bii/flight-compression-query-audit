"""Authored source failures and segmentation expectations, without recorded coordinates."""

import sys
import unittest
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ingest_source import integer, parse_native_line
from trajectory_quality import qualify_track


def row(frame, x="0", error=None):
    return {
        "frame": frame,
        "xyz_km": [Decimal(x), Decimal(0), Decimal(0)],
        "error": error,
    }


class SourceQualityTests(unittest.TestCase):
    def test_fractional_frame_is_unavailable(self):
        _, parsed = parse_native_line("1.5 10 0 0 0 0 0")
        self.assertEqual(parsed["error"], "invalid_frame_or_coordinate")

    def test_nonfinite_coordinate_invalidates_whole_track(self):
        _, parsed = parse_native_line("1 10 NaN 0 0 0 0")
        result = qualify_track([row(0), parsed, row(2)], 2, 10)
        self.assertEqual(result["reason"], "invalid_frame_or_coordinate")
        self.assertEqual(result["runs"], [])

    def test_wrong_field_count_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_native_line("1 10 0 0 0 0")

    def test_invalid_identifier_cannot_be_assigned_a_track(self):
        for token in ["0", "-1", "1.5", "NaN", "not-an-id"]:
            with (
                self.subTest(token=token),
                self.assertRaises((ValueError, InvalidOperation)),
            ):
                integer(token, positive=True)

    def test_decimal_integer_frame_is_allowed(self):
        aircraft, parsed = parse_native_line("3.000 10.0 .001 -.002 .003 0 0")
        self.assertEqual((aircraft, parsed["frame"]), (10, 3))
        self.assertEqual(
            parsed["xyz_km"], [Decimal(".001"), Decimal("-.002"), Decimal(".003")]
        )

    def test_exact_duplicate_collapses_without_new_motion(self):
        result = qualify_track(
            [row(0), row(1, ".1"), row(1, ".10"), row(2, ".2")], 2, 10
        )
        self.assertIsNone(result["reason"])
        self.assertEqual([r["frame"] for r in result["runs"][0]], [0, 1, 2])
        self.assertEqual(result["stats"]["duplicate_frame_rows"], 1)

    def test_conflicting_duplicate_rejects_the_entire_track(self):
        result = qualify_track([row(0), row(1, ".1"), row(1, ".2"), row(2)], 2, 10)
        self.assertEqual(result["reason"], "conflicting_duplicate_frame")
        self.assertEqual(result["runs"], [])

    def test_gaps_are_split_without_an_invented_point(self):
        result = qualify_track([row(i) for i in [0, 1, 2, 5, 6]], 2, 10)
        self.assertEqual(
            [[r["frame"] for r in part] for part in result["runs"]], [[0, 1, 2], [5, 6]]
        )
        self.assertEqual(result["stats"]["gaps_split"], 1)

    def test_source_order_is_reported_before_sorting(self):
        result = qualify_track([row(2), row(0), row(1)], 2, 10)
        self.assertEqual(result["stats"]["out_of_order_transitions"], 1)
        self.assertEqual([r["frame"] for r in result["runs"][0]], [0, 1, 2])

    def test_run_bounds_include_endpoints_and_keep_exclusions(self):
        result = qualify_track(
            [row(i) for i in [0, 2, 3, 5, 6, 7, 9, 10, 11, 12]], 2, 3
        )
        self.assertEqual([len(part) for part in result["runs"]], [2, 3])
        self.assertEqual(result["excluded_lengths"], [1, 4])


if __name__ == "__main__":
    unittest.main()
