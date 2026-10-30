"""Check a proposed compression on a supplied local elapsed-time trajectory CSV."""

import argparse
import csv
import json
from pathlib import Path

from compression import encode, simplify, summarize_run

ROOT = Path(__file__).resolve().parents[1]


def assess(path, method, value):
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"elapsed_seconds", "x_m", "y_m", "z_m"} <= set(
                reader.fieldnames or []
            ):
                raise ValueError(
                    "Required time and metre coordinate columns are missing"
                )
            rows = list(reader)
        times = [float(row["elapsed_seconds"]) for row in rows]
        positions = [[float(row[key]) for key in ["x_m", "y_m", "z_m"]] for row in rows]
        plan = json.loads((ROOT / "data/source_plan.json").read_bytes())
        indices, _ = simplify(times, positions, method, value)
        metrics = summarize_run(
            times,
            positions,
            indices,
            encode(times, positions, indices),
            plan["selection_guards"],
            plan["zones_radius_m"],
        )
        return {
            "status": (
                "PASS_SUPPLIED_RECORD_BUDGETS"
                if metrics["admissible"]
                else "FAIL_SUPPLIED_RECORD_BUDGETS"
            ),
            "decision": (
                "The proposed representation meets the supplied-record budgets."
                if metrics["admissible"]
                else "Keep the source records; this representation fails a fidelity budget."
            ),
            "method": method,
            "parameter": value,
            "budgets": plan["selection_guards"],
            "scope": "Offline check of supplied records under authored budgets; no operational safety or airline-delay finding.",
            "metrics": metrics,
        }
    except (ValueError, KeyError, TypeError, OverflowError):
        return {
            "status": "UNAVAILABLE_INPUT",
            "decision": "Cannot assess: complete finite coordinates and strictly increasing integer elapsed times are required.",
            "metrics": None,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check query fidelity before discarding original trajectory points."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Local CSV: elapsed_seconds,x_m,y_m,z_m; coordinates in metres",
    )
    parser.add_argument(
        "--method",
        choices=["identity", "stride", "geometric", "synchronous"],
        default="identity",
    )
    parser.add_argument(
        "--value",
        type=float,
        help="Stride count or geometric/synchronous tolerance in metres",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = assess(args.input, args.method, args.value)
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.out:
        args.out.write_bytes(text.encode())
    print(text, end="")
