"""Recompute actual encoded replays using independent Decimal and SQLite paths."""

import argparse
import bisect
import csv
import hashlib
import json
import math
import sqlite3
import struct
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(archive, private_input, private_work, public_output, phase):
    count, checked_points, split_points = 0, 0, 0

    def check(ok, label):
        nonlocal count
        count += 1
        if not ok:
            raise AssertionError(label)

    def close(actual, expected, tolerance, label):
        if expected is None:
            check(actual in ["", None], label + ":undefined")
        else:
            check(
                abs(Decimal(str(actual)) - Decimal(expected)) <= Decimal(tolerance),
                label,
            )

    def d(value):
        return Decimal.from_float(float(value))

    def decimal_point(at, rt, rp):
        left = min(bisect.bisect_right(rt, at) - 1, len(rt) - 2)
        numerator, denominator = Decimal(at - rt[left]), Decimal(
            rt[left + 1] - rt[left]
        )
        return [
            d(a) + numerator * (d(b) - d(a)) / denominator
            for a, b in zip(rp[left], rp[left + 1])
        ]

    def length(points):
        return sum(
            (
                ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2).sqrt()
                for a, b in zip(points, points[1:])
            ),
            Decimal(0),
        )

    def fp_squared(point, a, b, at, ta, tb, method):
        direction = [b[k] - a[k] for k in range(3)]
        if method == "synchronous":
            fraction = (at - ta) / float(tb - ta)
        else:
            divisor = (
                direction[0] * direction[0] + direction[1] * direction[1]
            ) + direction[2] * direction[2]
            if divisor == 0:
                fraction = 0.0
            else:
                offsets = [point[k] - a[k] for k in range(3)]
                product = (
                    offsets[0] * direction[0] + offsets[1] * direction[1]
                ) + offsets[2] * direction[2]
                fraction = min(1.0, max(0.0, product / divisor))
        error = [point[k] - (a[k] + fraction * direction[k]) for k in range(3)]
        return (error[0] * error[0] + error[1] * error[1]) + error[2] * error[2]

    plan = json.loads((ROOT / "data/source_plan.json").read_bytes())
    source_verification = json.loads(
        (ROOT / "data/source_verification.json").read_bytes()
    )
    run_bytes = (private_input / "runs.json").read_bytes()
    check(
        hashlib.sha256(run_bytes).hexdigest()
        == source_verification["private_working_input_sha256"],
        "working input bound to independent native source check",
    )
    check(
        hashlib.sha256(archive.read_bytes()).hexdigest()
        == source_verification["source_sha256_unchanged"],
        "original archive unchanged",
    )
    source_runs = {
        r["run_id"]: r for r in json.loads(run_bytes) if r["partition"] == phase
    }
    choices = [("identity", "identity", None)]
    for kind, key in [
        ("stride", "uniform_stride"),
        ("geometric", "geometric_tolerance_m"),
        ("synchronous", "synchronous_tolerance_m"),
    ]:
        choices += [
            (f"{kind}-{parameter}", kind, parameter)
            for parameter in plan["configurations"][key]
        ]
    check(len(choices) == 11, "eleven fixed configurations")
    by_config = {
        name: (kind, value, rank) for rank, (name, kind, value) in enumerate(choices)
    }
    with (public_output / "run_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        public_rows = list(csv.DictReader(handle))
    witnesses = {
        (w["run_id"], w["configuration"]): w
        for w in json.loads((public_output / "retention_witnesses.json").read_bytes())
    }
    expected_keys = {(run, config) for run in source_runs for config in by_config}
    check(
        len(public_rows) == len(witnesses) == len(expected_keys),
        "complete case denominator",
    )
    check(
        {(r["run_id"], r["configuration"]) for r in public_rows} == expected_keys,
        "complete fixed case keys",
    )
    expected_records = []
    with localcontext() as context:
        context.prec = 80
        for row in public_rows:
            run, config_id = source_runs[row["run_id"]], row["configuration"]
            kind, parameter, rank = by_config[config_id]
            times, positions = run["elapsed_seconds"], run["xyz_m"]
            n = len(times)
            witness = witnesses[(run["run_id"], config_id)]
            indices = witness["retained_indices"]
            check(
                indices == sorted(set(indices))
                and indices[0] == 0
                and indices[-1] == n - 1,
                "retained endpoint and order",
            )
            check(all(0 <= i < n for i in indices), "retained index range")
            name = run["run_id"] + "--" + config_id
            payload = (private_work / "payloads" / (name + ".bin")).read_bytes()
            check(
                hashlib.sha256(payload).hexdigest() == witness["payload_sha256"],
                "actual retained payload hash",
            )
            check(len(payload) == 8 + 32 * len(indices), "actual encoded byte length")
            check(
                struct.unpack_from("<Q", payload)[0] == len(indices),
                "encoded point count",
            )
            records = list(struct.iter_unpack("<qddd", payload[8:]))
            rt, rp = [p[0] for p in records], [p[1:] for p in records]
            for index, record in zip(indices, records):
                check(record[0] == times[index], "encoded native elapsed time")
                check(
                    list(record[1:]) == positions[index],
                    "encoded native coordinates exactly preserved",
                )
            trace_bytes = (
                private_work / "split_witnesses" / (name + ".json")
            ).read_bytes()
            check(
                hashlib.sha256(trace_bytes).hexdigest()
                == witness["split_witness_sha256"],
                "split witness as-run hash",
            )
            trace = json.loads(trace_bytes)
            check(len(trace) == witness["split_nodes"], "split witness count")
            if kind == "identity":
                check(
                    indices == list(range(n)) and not trace, "identity retains source"
                )
            elif kind == "stride":
                check(
                    indices == sorted(set(range(0, n, parameter)) | {n - 1})
                    and not trace,
                    "fixed stride and forced endpoint",
                )
            else:
                pending, expected_retained = {(0, n - 1)}, {0, n - 1}
                for split in trace:
                    a, b, k = split["start"], split["end"], split["max_index"]
                    check(
                        (a, b) in pending and b - a > 1 and a < k < b,
                        "complete top-down interval witness",
                    )
                    pending.remove((a, b))
                    values = [
                        fp_squared(
                            positions[j],
                            positions[a],
                            positions[b],
                            times[j],
                            times[a],
                            times[b],
                            kind,
                        )
                        for j in range(a + 1, b)
                    ]
                    split_points += len(values)
                    maximum = max(values)
                    best = values.index(maximum) + a + 1
                    check(
                        k == best, "independent scalar maximal split and earliest tie"
                    )
                    close(
                        split["max_distance_squared_m2"],
                        Decimal.from_float(maximum),
                        "1e-6",
                        "independent squared split distance",
                    )
                    is_split = maximum > parameter**2
                    check(split["split"] == is_split, "strict tolerance branch")
                    if is_split:
                        expected_retained.add(k)
                        if k - a > 1:
                            pending.add((a, k))
                        if b - k > 1:
                            pending.add((k, b))
                check(
                    not pending and sorted(expected_retained) == indices,
                    "entire witnessed simplification tree",
                )
            diagnostics = (
                private_work / "point_diagnostics" / (name + ".bin")
            ).read_bytes()
            check(
                len(diagnostics) == 8 + 56 * n == witness["point_diagnostics_bytes"],
                "private point diagnostic shape",
            )
            check(
                struct.unpack_from("<Q", diagnostics)[0] == n, "diagnostic point count"
            )
            check(
                hashlib.sha256(diagnostics).hexdigest()
                == witness["point_diagnostics_sha256"],
                "actual point diagnostic hash",
            )
            binary_points = list(struct.iter_unpack("<7d", diagnostics[8:]))
            reference = [[d(value) for value in point] for point in positions]
            decoded = [decimal_point(at, rt, rp) for at in times]
            errors = [
                [a - b for a, b in zip(actual, target)]
                for actual, target in zip(decoded, reference)
            ]
            norms2 = [
                sum((value * value for value in error), Decimal(0)) for error in errors
            ]
            for diagnostic, actual, error, squared in zip(
                binary_points, decoded, errors, norms2
            ):
                for axis in range(3):
                    close(
                        diagnostic[axis],
                        actual[axis],
                        "1e-8",
                        "actual point reconstruction versus Decimal",
                    )
                    close(
                        diagnostic[axis + 3],
                        error[axis],
                        "1e-8",
                        "actual signed point error versus Decimal",
                    )
                close(
                    diagnostic[6],
                    squared,
                    "1e-6",
                    "actual squared point error versus Decimal",
                )
                checked_points += 1
            mse = sum(norms2, Decimal(0)) / n
            maximum = max(norms2).sqrt()
            raw_length, compressed_length = length(reference), length(decoded)
            signed_length = compressed_length - raw_length
            relative = signed_length / raw_length if raw_length > 0 else None
            expected = {
                "input_points": n,
                "retained_points": len(indices),
                "input_bytes": 8 + 32 * n,
                "encoded_bytes": len(payload),
                "byte_ratio": Decimal(len(payload)) / Decimal(8 + 32 * n),
                "point_mae_m": sum((x.sqrt() for x in norms2), Decimal(0)) / n,
                "point_mse_m2": mse,
                "point_rmse_m": mse.sqrt(),
                "max_synchronous_error_m": maximum,
                **{
                    f"bias_{axis}_m": sum((e[number] for e in errors), Decimal(0)) / n
                    for number, axis in enumerate("xyz")
                },
                "route_reference_m": raw_length,
                "route_decoded_m": compressed_length,
                "route_signed_error_m": signed_length,
                "route_absolute_error_m": abs(signed_length),
                "route_relative_error": relative,
                "route_absolute_relative_error": (
                    abs(relative) if relative is not None else None
                ),
                "position_pass": max(norms2)
                <= Decimal(plan["selection_guards"]["maximum_synchronous_error_m"])
                ** 2,
                "route_pass": (
                    abs(relative)
                    <= Decimal(
                        str(plan["selection_guards"]["maximum_route_relative_error"])
                    )
                    if relative is not None
                    else compressed_length <= Decimal("1e-6")
                ),
                "route_boundary_decimal_check": relative is not None
                and abs(abs(Decimal(row["route_relative_error"])) - Decimal(".01"))
                <= Decimal("1e-10"),
            }
            passed = [expected["position_pass"], expected["route_pass"]]
            for radius in plan["zones_radius_m"]:
                radius2 = Decimal(radius) ** 2
                ref_mask = [p[0] * p[0] + p[1] * p[1] <= radius2 for p in reference]
                decoded_mask = [p[0] * p[0] + p[1] * p[1] <= radius2 for p in decoded]

                def timing(mask):
                    occupied = sum(
                        times[i + 1] - times[i] for i in range(n - 1) if mask[i]
                    )
                    transitions = [
                        times[i] - times[0] for i in range(1, n) if mask[i] != mask[0]
                    ]
                    return occupied, transitions[0] if transitions else None

                reference_occupied, reference_transition = timing(ref_mask)
                decoded_occupied, decoded_transition = timing(decoded_mask)
                same = (reference_transition is None) == (decoded_transition is None)
                difference = (
                    decoded_transition - reference_transition
                    if reference_transition is not None
                    and decoded_transition is not None
                    else None
                )
                prefix = f"zone{radius}_"
                occupancy_pass = (
                    abs(decoded_occupied - reference_occupied)
                    <= plan["selection_guards"]["maximum_zone_occupancy_error_seconds"]
                )
                transition_pass = same and (
                    difference is None
                    or abs(difference)
                    <= plan["selection_guards"][
                        "maximum_first_transition_error_seconds"
                    ]
                )
                near_limit = 2 * radius * 1e-8 + 1e-16
                near = sum(
                    abs((p[0] * p[0] + p[1] * p[1]) - radius**2) <= near_limit
                    for p in positions
                )
                near += sum(
                    abs((p[0] * p[0] + p[1] * p[1]) - radius**2) <= near_limit
                    for p in binary_points
                )
                expected.update(
                    {
                        prefix + "reference_occupancy_s": reference_occupied,
                        prefix + "decoded_occupancy_s": decoded_occupied,
                        prefix
                        + "occupancy_signed_error_s": decoded_occupied
                        - reference_occupied,
                        prefix
                        + "occupancy_absolute_error_s": abs(
                            decoded_occupied - reference_occupied
                        ),
                        prefix + "reference_first_transition_s": reference_transition,
                        prefix + "decoded_first_transition_s": decoded_transition,
                        prefix + "first_transition_signed_error_s": difference,
                        prefix
                        + "first_transition_absolute_error_s": (
                            abs(difference) if difference is not None else None
                        ),
                        prefix + "transition_state_match": same,
                        prefix + "occupancy_pass": occupancy_pass,
                        prefix + "transition_pass": transition_pass,
                        prefix + "decimal_boundary_points": near,
                    }
                )
                passed.extend([occupancy_pass, transition_pass])
            expected["admissible"] = all(passed)
            check(
                row["group_id"] == run["group_id"] and row["partition"] == phase,
                "case entity and partition identity",
            )
            for key, value in expected.items():
                actual = row[key]
                if isinstance(value, bool):
                    check(actual == str(value), "exact guard or state:" + key)
                elif value is None:
                    check(actual == "", "undefined metric:" + key)
                elif isinstance(value, int):
                    check(actual == str(value), "exact count/time:" + key)
                else:
                    tolerance = (
                        "1e-6"
                        if key == "point_mse_m2"
                        or key.startswith("route_")
                        and key.endswith("_m")
                        else (
                            "1e-10" if "ratio" in key or "relative" in key else "1e-8"
                        )
                    )
                    close(actual, value, tolerance, "independent metric:" + key)
            expected_records.append(
                {
                    "run_id": run["run_id"],
                    "group_id": run["group_id"],
                    "configuration": config_id,
                    **expected,
                }
            )

    # SQL supplies grouping and denominators independently of the analysis aggregation.
    summaries = json.loads((public_output / "configuration_summary.json").read_bytes())
    numeric_fields = [
        k
        for k, v in expected_records[0].items()
        if k not in ["run_id", "group_id", "configuration"] and not isinstance(v, bool)
    ]
    db = sqlite3.connect(":memory:")
    db.execute(
        "create table metrics(run_id text, group_id text, configuration text, admissible integer,"
        + ",".join('"' + k + '" real' for k in numeric_fields)
        + ")"
    )
    for record in expected_records:
        values = [
            record["run_id"],
            record["group_id"],
            record["configuration"],
            int(record["admissible"]),
        ]
        values += [
            None if record[k] is None else float(record[k]) for k in numeric_fields
        ]
        db.execute(
            "insert into metrics values (" + ",".join("?" for _ in values) + ")", values
        )
    check(len(summaries) == 11, "complete configuration summaries")
    expected_summary = []
    for name, kind, parameter in choices:
        rows = [r for r in expected_records if r["configuration"] == name]
        summary = next(s for s in summaries if s["configuration"] == name)
        n, groups, input_bytes, encoded_bytes, violations = db.execute(
            "select count(*),count(distinct group_id),sum(input_bytes),sum(encoded_bytes),sum(1-admissible) from metrics where configuration=?",
            (name,),
        ).fetchone()
        for key, expected in [
            ("runs", n),
            ("aircraft_groups", groups),
            ("total_input_bytes", int(input_bytes)),
            ("total_encoded_bytes", int(encoded_bytes)),
            ("violating_runs", violations),
        ]:
            check(summary[key] == expected, "SQL configuration total:" + key)
        check(summary["rank"] == by_config[name][2], "declared tie rank")
        check(
            summary["admissible_on_every_run"] == (violations == 0),
            "all-run admissibility",
        )
        close(
            summary["total_byte_ratio"],
            Decimal(encoded_bytes) / Decimal(input_bytes),
            "1e-10",
            "SQL total encoded byte ratio",
        )
        close(
            summary["max_synchronous_error_m"],
            max(r["max_synchronous_error_m"] for r in rows),
            "1e-8",
            "independent maximum across runs",
        )
        for field, value in summary["macro_aircraft_mean"].items():
            sql = (
                'select avg(aircraft_mean),count(aircraft_mean) from(select group_id,avg("'
                + field
                + '") aircraft_mean from metrics where configuration=? group by group_id)'
            )
            expected, denominator = db.execute(sql, (name,)).fetchone()
            tolerance = (
                "1e-6"
                if field == "point_mse_m2"
                or field.startswith("route_")
                and field.endswith("_m")
                else (
                    "1e-10"
                    if "ratio" in field or "relative" in field or field.endswith("_s")
                    else "1e-8"
                )
            )
            close(
                value,
                Decimal(str(expected)) if expected is not None else None,
                tolerance,
                "SQL equal-aircraft macro:" + field,
            )
            check(
                summary["macro_available_aircraft_denominators"][field] == denominator,
                "SQL available aircraft denominator",
            )
        for key, value in summary["gate_failure_counts"].items():
            check(
                value == sum(not r[key] for r in rows),
                "independent guard failure count",
            )
        expected_summary.append(
            (name, int(encoded_bytes), violations, by_config[name][2])
        )
    if phase == "development":
        selection = json.loads((public_output / "selection.json").read_bytes())
        eligible = [s for s in expected_summary if s[2] == 0]
        chosen = min(eligible, key=lambda s: (s[1], s[3]))[0]
        check(
            selection["selected_configuration"] == chosen,
            "independent minimum-byte constrained selection",
        )
        check(
            selection["eligible_configurations"] == [s[0] for s in eligible],
            "all eligible candidates retained",
        )
        check(
            selection["final_compression_scores_used"] is False,
            "development-only selection",
        )
        check(
            selection["private_working_input_sha256"]
            == hashlib.sha256(run_bytes).hexdigest(),
            "selected source input identity",
        )
    else:
        final = json.loads((public_output / "final_result.json").read_bytes())
        frozen = json.loads((ROOT / "data/frozen_dependencies.json").read_bytes())
        check(
            final["selected_configuration"] == frozen["selected_configuration"]
            and final["new_selection_made"] is False,
            "frozen final application without selection",
        )
        chosen = final["selected_configuration"]
        check(
            final["selected_final_summary"]
            == next(s for s in summaries if s["configuration"] == chosen),
            "final selected summary binding",
        )
    check(
        hashlib.sha256(archive.read_bytes()).hexdigest()
        == source_verification["source_sha256_unchanged"],
        "source unchanged after numerical checks",
    )
    result = {
        "status": "PASS_INDEPENDENT_ENCODED_REPLAY_AND_SELECTION",
        "phase": phase,
        "conditions": count,
        "cases": len(expected_records),
        "point_diagnostics_checked": checked_points,
        "split_interior_points_independently_checked": split_points,
        "selected_configuration": chosen,
        "method": "Independent struct payload/point-record decoding, scalar split trees, 80-digit Decimal reconstruction/errors/queries and SQLite equal-aircraft summaries; no analysis helper imports",
        "working_input_sha256": hashlib.sha256(run_bytes).hexdigest(),
        "source_archive_sha256_unchanged": source_verification[
            "source_sha256_unchanged"
        ],
        "public_artifact_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(public_output.iterdir())
            if p.name != "independent_verification.json"
        },
    }
    (public_output / "independent_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument("--private-work", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    args = parser.parse_args()
    verify(
        args.archive,
        args.private_input,
        args.private_work,
        args.public_output,
        args.phase,
    )
