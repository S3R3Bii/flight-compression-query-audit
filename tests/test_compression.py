import json
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from compression import (
    configurations,
    encode,
    first_transition,
    reconstruct,
    simplify,
    summarize_run,
    unpack,
    validate,
    zone_membership,
)

GUARDS = {
    "maximum_synchronous_error_m": 25,
    "maximum_route_relative_error": 0.01,
    "maximum_zone_occupancy_error_seconds": 2,
    "maximum_first_transition_error_seconds": 2,
}


def positions(xs, ys=None, zs=None):
    return np.column_stack(
        [
            xs,
            ys if ys is not None else np.zeros(len(xs)),
            zs if zs is not None else np.zeros(len(xs)),
        ]
    )


class CompressionTests(unittest.TestCase):
    def test_private_point_evidence_binds_the_actual_reconstruction(self):
        t, p = [0, 1, 2], positions([0, 0, 100])
        metrics, evidence = summarize_run(
            t,
            p,
            [0, 2],
            encode(t, p, [0, 2]),
            GUARDS,
            [1000, 2000],
            with_diagnostics=True,
        )
        self.assertEqual(evidence.shape, (3, 7))
        self.assertEqual(evidence[1].tolist(), [50, 0, 0, 50, 0, 0, 2500])
        self.assertAlmostEqual(metrics["point_mse_m2"], 2500 / 3)

    def test_fixed_search_has_eleven_configurations_and_identity_first(self):
        plan = json.loads((ROOT / "data/source_plan.json").read_bytes())
        self.assertEqual(len(configurations(plan)), 11)
        self.assertEqual(configurations(plan)[0], ("identity", "identity", None))

    def test_uniform_stride_forces_the_final_endpoint(self):
        indices, _ = simplify([0, 1, 2, 3], positions([0, 1, 2, 3]), "stride", 2)
        self.assertEqual(indices, [0, 2, 3])

    def test_collinear_unequal_speed_has_different_synchronous_error(self):
        t, p = list(range(4)), positions([0, 0, 0, 100])
        geometric, _ = simplify(t, p, "geometric", 1)
        synchronous, _ = simplify(t, p, "synchronous", 1)
        self.assertEqual(geometric, [0, 3])
        self.assertEqual(synchronous, [0, 2, 3])

    def test_finite_segment_clamps_the_projection(self):
        indices, _ = simplify([0, 1, 2], positions([0, -100, 100]), "geometric", 25)
        self.assertEqual(indices, [0, 1, 2])

    def test_repeated_endpoints_have_a_defined_spatial_distance(self):
        indices, _ = simplify([0, 1, 2], positions([0, 100, 0]), "geometric", 25)
        self.assertEqual(indices, [0, 1, 2])

    def test_exact_tolerance_is_inclusive(self):
        for method in ["geometric", "synchronous"]:
            t, p = [0, 1, 2], positions([0, 1, 2], [0, 10, 0])
            self.assertEqual(simplify(t, p, method, 10)[0], [0, 2])
            self.assertEqual(simplify(t, p, method, 9)[0], [0, 1, 2])

    def test_earliest_maximal_point_breaks_a_tie(self):
        _, witness = simplify(
            [0, 1, 2, 3], positions([0, 1, 2, 3], [0, 10, 10, 0]), "geometric", 5
        )
        self.assertEqual(witness[0]["max_index"], 1)

    def test_irregular_integer_times_use_time_ratio(self):
        t, p = [0, 4, 10], positions([0, 40, 100])
        self.assertEqual(simplify(t, p, "synchronous", 0)[0], [0, 2])
        decoded = reconstruct(t, [0, 10], positions([0, 100]))
        self.assertEqual(decoded[:, 0].tolist(), [0, 40, 100])

    def test_invalid_or_missing_timing_is_rejected(self):
        for t in [[0, 0], [1, 0], [0, 0.5], [0, float("nan")]]:
            with self.subTest(t=t), self.assertRaises(ValueError):
                validate(t, positions([0, 1]))

    def test_nonfinite_coordinate_is_rejected(self):
        with self.assertRaises(ValueError):
            validate([0, 1], positions([0, float("inf")]))

    def test_reconstruction_never_truncates_a_fractional_time(self):
        with self.assertRaises(ValueError):
            reconstruct([0, 0.5, 1], [0, 1], positions([0, 1]))

    def test_encoding_is_the_declared_32_byte_record_format(self):
        payload = encode([0, 1, 2], positions([0, 10, 20]), [0, 2])
        self.assertEqual(len(payload), 8 + 32 * 2)
        self.assertEqual(struct.unpack_from("<Q", payload)[0], 2)
        t, p = unpack(payload)
        self.assertEqual(t.tolist(), [0, 2])
        self.assertEqual(p[:, 0].tolist(), [0, 20])

    def test_malformed_payload_length_is_rejected(self):
        for payload in [b"", struct.pack("<Q", 2), struct.pack("<Q", 0)]:
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                unpack(payload)

    def test_missing_or_duplicate_retained_endpoints_are_rejected(self):
        for indices in [[0, 1], [1, 2], [0, 1, 1, 2], [2, 0]]:
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                encode([0, 1, 2], positions([0, 1, 2]), indices)

    def test_boundary_equality_uses_a_closed_disk(self):
        t, p = validate([0, 1], [[600, 800, 0], [600.000000001, 800, 0]])
        mask, near = zone_membership(t, t, p, p, 1000)
        self.assertEqual(mask.tolist(), [True, False])
        self.assertEqual(near, 2)
        self.assertEqual(first_transition(t, mask), 1)

    def test_occupancy_excludes_the_final_interval_endpoint(self):
        t, p = [0, 1, 2], positions([0, 2000, 4000])
        result = summarize_run(
            t, p, [0, 1, 2], encode(t, p, [0, 1, 2]), GUARDS, [1000, 2000]
        )
        self.assertEqual(result["zone1000_reference_occupancy_s"], 1)
        self.assertEqual(result["zone1000_reference_first_transition_s"], 1)
        self.assertEqual(result["point_mse_m2"], 0)
        self.assertTrue(result["admissible"])

    def test_route_length_alone_can_hide_a_timing_failure(self):
        t, p = list(range(11)), positions([0] * 10 + [4000])
        indices, _ = simplify(t, p, "geometric", 25)
        result = summarize_run(
            t, p, indices, encode(t, p, indices), GUARDS, [1000, 2000]
        )
        self.assertAlmostEqual(result["route_signed_error_m"], 0)
        self.assertEqual(result["zone1000_first_transition_signed_error_s"], -7)
        self.assertFalse(result["zone1000_transition_pass"])
        self.assertFalse(result["admissible"])

    def test_missed_transition_is_undefined_error_and_failed_guard(self):
        t, p = [0, 1, 2], positions([0, 2000, 0])
        payload = encode(t, p, [0, 2])
        result = summarize_run(t, p, [0, 2], payload, GUARDS, [1000, 2000])
        self.assertFalse(result["zone1000_transition_state_match"])
        self.assertIsNone(result["zone1000_first_transition_signed_error_s"])
        self.assertFalse(result["zone1000_transition_pass"])

    def test_stationary_route_has_no_invented_relative_score(self):
        t, p = [0, 1, 2], positions([0, 0, 0], zs=[0, 10, 0])
        result = summarize_run(
            t, p, [0, 1, 2], encode(t, p, [0, 1, 2]), GUARDS, [1000, 2000]
        )
        self.assertIsNone(result["route_relative_error"])
        self.assertTrue(result["route_pass"])
        self.assertTrue(result["admissible"])


if __name__ == "__main__":
    unittest.main()
