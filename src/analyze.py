"""Run fixed comparisons on one partition; final requires committed dependencies."""

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import struct
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from compression import configurations, encode, simplify, summarize_run

ROOT = Path(__file__).resolve().parents[1]
MACRO_FIELDS = [
    "byte_ratio",
    "point_mae_m",
    "point_mse_m2",
    "point_rmse_m",
    "max_synchronous_error_m",
    "bias_x_m",
    "bias_y_m",
    "bias_z_m",
    "route_signed_error_m",
    "route_absolute_error_m",
    "route_relative_error",
    "route_absolute_relative_error",
    "zone1000_occupancy_signed_error_s",
    "zone2000_occupancy_signed_error_s",
    "zone1000_occupancy_absolute_error_s",
    "zone2000_occupancy_absolute_error_s",
    "zone1000_first_transition_signed_error_s",
    "zone2000_first_transition_signed_error_s",
    "zone1000_first_transition_absolute_error_s",
    "zone2000_first_transition_absolute_error_s",
]


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def aggregate(rows, config_id, rank):
    selected = [r for r in rows if r["configuration"] == config_id]
    groups = defaultdict(list)
    for row in selected:
        groups[row["group_id"]].append(row)
    means, denominators = {}, {}
    for name in MACRO_FIELDS:
        values = []
        for group in groups.values():
            available = [float(r[name]) for r in group if r[name] is not None]
            if available:
                values.append(sum(available) / len(available))
        means[name] = sum(values) / len(values) if values else None
        denominators[name] = len(values)
    return {
        "configuration": config_id,
        "rank": rank,
        "runs": len(selected),
        "aircraft_groups": len(groups),
        "total_input_bytes": sum(r["input_bytes"] for r in selected),
        "total_encoded_bytes": sum(r["encoded_bytes"] for r in selected),
        "total_byte_ratio": sum(r["encoded_bytes"] for r in selected)
        / sum(r["input_bytes"] for r in selected),
        "violating_runs": sum(not r["admissible"] for r in selected),
        "admissible_on_every_run": all(r["admissible"] for r in selected),
        "max_synchronous_error_m": max(r["max_synchronous_error_m"] for r in selected),
        "macro_aircraft_mean": means,
        "macro_available_aircraft_denominators": denominators,
        "gate_failure_counts": {
            key: sum(not r[key] for r in selected)
            for key in [
                "position_pass",
                "route_pass",
                "zone1000_occupancy_pass",
                "zone1000_transition_pass",
                "zone2000_occupancy_pass",
                "zone2000_transition_pass",
            ]
        },
    }


def check_freeze(private_runs_path):
    freeze_path = ROOT / "data/frozen_dependencies.json"
    if not freeze_path.exists():
        raise RuntimeError(
            "Final evaluation requires a committed development dependency manifest"
        )
    freeze = json.loads(freeze_path.read_bytes())
    committed = subprocess.check_output(
        ["git", "show", "HEAD:data/frozen_dependencies.json"], cwd=ROOT
    )
    if json.loads(committed) != freeze:
        raise RuntimeError("Working dependency manifest is not the committed manifest")
    for name, expected in freeze["dependencies"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("A frozen dependency changed: " + name)
    if (
        hashlib.sha256(private_runs_path.read_bytes()).hexdigest()
        != freeze["private_working_input_sha256"]
    ):
        raise RuntimeError(
            "Private source working input changed after development freeze"
        )
    selected = json.loads((ROOT / "results/development/selection.json").read_bytes())[
        "selected_configuration"
    ]
    if selected != freeze["selected_configuration"]:
        raise RuntimeError("Development selection changed after freeze")
    return freeze


def analyze(private_input, output, private_output, phase):
    plan = json.loads((ROOT / "data/source_plan.json").read_bytes())
    cohort = json.loads((ROOT / "data/cohort.json").read_bytes())
    if cohort["status"] != "PASS_SOURCE_COHORT":
        raise RuntimeError("Source cohort does not qualify")
    runs_path = private_input / "runs.json"
    freeze = check_freeze(runs_path) if phase == "final" else None
    runs = [r for r in json.loads(runs_path.read_bytes()) if r["partition"] == phase]
    if not runs:
        raise RuntimeError("Partition has no qualified runs")
    if private_output.resolve().is_relative_to(
        ROOT
    ) and not private_output.resolve().is_relative_to(ROOT / ".source"):
        raise ValueError(
            "Coordinate payloads must remain outside Git or within ignored .source"
        )
    output.mkdir(parents=True, exist_ok=False)
    private_output.mkdir(parents=True, exist_ok=False)
    (private_output / "payloads").mkdir()
    (private_output / "split_witnesses").mkdir()
    (private_output / "point_diagnostics").mkdir()
    rows, witnesses, timings = [], [], []
    choices = configurations(plan)
    assert len(choices) == 11
    for run in runs:
        for config_id, method, parameter in choices:
            start = time.perf_counter()
            indices, splits = simplify(
                run["elapsed_seconds"], run["xyz_m"], method, parameter
            )
            payload = encode(run["elapsed_seconds"], run["xyz_m"], indices)
            metrics, point_diagnostics = summarize_run(
                run["elapsed_seconds"],
                run["xyz_m"],
                indices,
                payload,
                plan["selection_guards"],
                plan["zones_radius_m"],
                with_diagnostics=True,
            )
            record = {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "partition": phase,
                "configuration": config_id,
                **metrics,
            }
            rows.append(record)
            name = run["run_id"] + "--" + config_id
            (private_output / "payloads" / (name + ".bin")).write_bytes(payload)
            split_bytes = (
                json.dumps(splits, separators=(",", ":"), allow_nan=False) + "\n"
            ).encode()
            (private_output / "split_witnesses" / (name + ".json")).write_bytes(
                split_bytes
            )
            diagnostic_bytes = (
                struct.pack("<Q", len(point_diagnostics))
                + np.asarray(point_diagnostics, dtype="<f8").tobytes()
            )
            (private_output / "point_diagnostics" / (name + ".bin")).write_bytes(
                diagnostic_bytes
            )
            witnesses.append(
                {
                    "run_id": run["run_id"],
                    "configuration": config_id,
                    "retained_indices": indices,
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    "split_witness_sha256": hashlib.sha256(split_bytes).hexdigest(),
                    "split_nodes": len(splits),
                    "point_diagnostics_sha256": hashlib.sha256(
                        diagnostic_bytes
                    ).hexdigest(),
                    "point_diagnostics_bytes": len(diagnostic_bytes),
                }
            )
            timings.append(
                {
                    "run_id": run["run_id"],
                    "configuration": config_id,
                    "seconds": time.perf_counter() - start,
                    "status": "executed",
                    "runtime_python": platform.python_version(),
                    "numpy": np.__version__,
                }
            )
            dump(private_output / "execution_progress.json", timings)
    with (output / "run_metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    dump(output / "retention_witnesses.json", witnesses)
    summaries = [
        aggregate(rows, config_id, rank)
        for rank, (config_id, _, _) in enumerate(choices)
    ]
    assert (
        summaries[0]["configuration"] == "identity"
        and summaries[0]["violating_runs"] == 0
    )
    dump(output / "configuration_summary.json", summaries)
    if phase == "development":
        eligible = [s for s in summaries if s["admissible_on_every_run"]]
        selected = min(eligible, key=lambda s: (s["total_encoded_bytes"], s["rank"]))
        selection = {
            "status": "PASS_FIXED_DEVELOPMENT_SELECTION",
            "selected_configuration": selected["configuration"],
            "objective": "minimum total actual point-record bytes subject to all individual-run fidelity guards; declared rank breaks ties",
            "aircraft_groups": cohort["aircraft_groups"][phase],
            "runs": len(runs),
            "cases": len(rows),
            "eligible_configurations": [s["configuration"] for s in eligible],
            "selected_total_byte_ratio": selected["total_byte_ratio"],
            "private_working_input_sha256": hashlib.sha256(
                runs_path.read_bytes()
            ).hexdigest(),
            "final_compression_scores_used": False,
        }
        dump(output / "selection.json", selection)
        print(json.dumps(selection))
    else:
        selected = next(
            s
            for s in summaries
            if s["configuration"] == freeze["selected_configuration"]
        )
        final = {
            "status": "EXECUTED_FROZEN_FINAL_AIRCRAFT_COMPARISON",
            "selected_configuration": selected["configuration"],
            "aircraft_groups": cohort["aircraft_groups"][phase],
            "runs": len(runs),
            "cases": len(rows),
            "selected_final_summary": selected,
            "new_selection_made": False,
        }
        dump(output / "final_result.json", final)
        print(json.dumps(final))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--private-out", type=Path, required=True)
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    args = parser.parse_args()
    analyze(args.private_input, args.out, args.private_out, args.phase)
