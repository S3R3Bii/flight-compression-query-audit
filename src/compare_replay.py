"""Compare replay facts exactly and declared floating metrics by absolute tolerances."""

import csv
import json
from decimal import Decimal
from pathlib import Path

FLOAT_CSV_FIELDS = {
    "byte_ratio",
    "point_mae_m",
    "point_mse_m2",
    "point_rmse_m",
    "max_synchronous_error_m",
    "bias_x_m",
    "bias_y_m",
    "bias_z_m",
    "route_reference_m",
    "route_decoded_m",
    "route_signed_error_m",
    "route_absolute_error_m",
    "route_relative_error",
    "route_absolute_relative_error",
}


class Comparison:
    def __init__(self):
        self.exact = 0
        self.tolerant = 0
        self.as_run_hashes = 0
        self.maximum_normalized_difference = Decimal(0)

    def numeric(self, expected, actual, name):
        tolerance = Decimal(
            "1e-6"
            if name == "point_mse_m2"
            or name.startswith("route_")
            and name.endswith("_m")
            else (
                "1e-10"
                if "ratio" in name or "relative" in name or name.endswith("_s")
                else "1e-8"
            )
        )
        first, second = Decimal(str(expected)), Decimal(str(actual))
        if (
            not first.is_finite()
            or not second.is_finite()
            or abs(first - second) > tolerance
        ):
            raise AssertionError("Numerical replay mismatch: " + name)
        self.tolerant += 1
        self.maximum_normalized_difference = max(
            self.maximum_normalized_difference, abs(first - second) / tolerance
        )

    def value(self, expected, actual, path=(), as_run=False):
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or set(expected) != set(actual):
                raise AssertionError("Replay keys differ: " + ".".join(path))
            for key in expected:
                if as_run and (
                    key in ["point_diagnostics_sha256", "split_witness_sha256"]
                    or "public_artifact_hashes" in path
                ):
                    if not isinstance(actual[key], str) or len(actual[key]) != 64:
                        raise AssertionError("Invalid regenerated as-run hash")
                    self.as_run_hashes += 1
                else:
                    self.value(expected[key], actual[key], path + (key,), as_run)
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(expected) != len(actual):
                raise AssertionError("Replay list length differs: " + ".".join(path))
            for index, (a, b) in enumerate(zip(expected, actual)):
                self.value(a, b, path + (str(index),), as_run)
        elif isinstance(expected, bool):
            if type(actual) is not bool or expected != actual:
                raise AssertionError("Replay boolean differs: " + ".".join(path))
            self.exact += 1
        elif isinstance(expected, float):
            if type(actual) not in [int, float]:
                raise AssertionError("Replay numeric type differs")
            self.numeric(expected, actual, path[-1])
        else:
            if type(actual) is not type(expected) or expected != actual:
                raise AssertionError("Replay fact differs: " + ".".join(path))
            self.exact += 1

    def file(self, expected, actual, as_run=False):
        if expected.suffix == ".csv":
            with expected.open(encoding="utf-8", newline="") as handle:
                first = list(csv.DictReader(handle))
            with actual.open(encoding="utf-8", newline="") as handle:
                second = list(csv.DictReader(handle))
            if len(first) != len(second):
                raise AssertionError("Replay CSV row count differs")
            for a, b in zip(first, second):
                if set(a) != set(b):
                    raise AssertionError("Replay CSV columns differ")
                for key in a:
                    if key in FLOAT_CSV_FIELDS and a[key] != "" and b[key] != "":
                        self.numeric(a[key], b[key], key)
                    else:
                        self.value(a[key], b[key], (expected.name, key))
        elif expected.suffix == ".json":
            self.value(
                json.loads(expected.read_bytes()),
                json.loads(actual.read_bytes()),
                (expected.name,),
                as_run,
            )
        else:
            if expected.read_bytes() != actual.read_bytes():
                raise AssertionError("Replay bytes differ: " + expected.name)
            self.exact += 1
