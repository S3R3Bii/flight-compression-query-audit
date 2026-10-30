"""Closed-form checks for authored teaching cases, separate from recorded source data."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(output):
    results = {}
    conditions = 0

    def check(value):
        nonlocal conditions
        conditions += 1
        if not value:
            raise AssertionError("Authored example expectation failed")

    for name in ["safe-simple-motion", "geometry-hides-time", "incomplete-time"]:
        data = subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "src/exercise.py"),
                str(ROOT / "examples" / (name + ".csv")),
                "--method",
                "geometric",
                "--value",
                "25",
            ],
            text=True,
            cwd=ROOT,
        )
        results[name] = json.loads(data)
    safe = results["safe-simple-motion"]
    check(safe["status"] == "PASS_SUPPLIED_RECORD_BUDGETS")
    check(safe["metrics"]["retained_points"] == 2)
    check(
        safe["metrics"]["input_bytes"] == 6440
        and safe["metrics"]["encoded_bytes"] == 72
    )
    check(safe["metrics"]["max_synchronous_error_m"] == 0)
    for radius in [1000, 2000]:
        check(safe["metrics"][f"zone{radius}_occupancy_signed_error_s"] == 0)
        check(safe["metrics"][f"zone{radius}_first_transition_signed_error_s"] == 0)
    hidden = results["geometry-hides-time"]
    check(hidden["status"] == "FAIL_SUPPLIED_RECORD_BUDGETS")
    check(hidden["metrics"]["retained_points"] == 2)
    check(hidden["metrics"]["route_signed_error_m"] == 0)
    check(hidden["metrics"]["max_synchronous_error_m"] == 2000)
    check(hidden["metrics"]["zone1000_reference_first_transition_s"] == 126)
    check(hidden["metrics"]["zone1000_decoded_first_transition_s"] == 51)
    check(hidden["metrics"]["zone1000_first_transition_signed_error_s"] == -75)
    check(hidden["metrics"]["zone2000_first_transition_signed_error_s"] == -50)
    check(results["incomplete-time"]["status"] == "UNAVAILABLE_INPUT")
    check(results["incomplete-time"]["metrics"] is None)
    result = {
        "status": "PASS_THREE_AUTHORED_EXERCISES",
        "conditions": conditions,
        "cases": results,
        "provenance": "Authored teaching records, not TrajAir observations or measured operational outcomes",
    }
    output.write_bytes((json.dumps(result, indent=2, allow_nan=False) + "\n").encode())
    print(
        json.dumps({"status": result["status"], "conditions": conditions, "cases": 3})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.out)
