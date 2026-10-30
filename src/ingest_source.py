"""Create non-coordinate cohort evidence and a separate private working input."""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
from trajectory_quality import qualify_track

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    if path.exists():
        raise FileExistsError("Refusing to overwrite existing source evidence")
    path.write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def integer(token, positive=False):
    value = Decimal(token)
    if (
        not value.is_finite()
        or value != value.to_integral_value()
        or value < int(positive)
    ):
        raise ValueError("Invalid native integer")
    return int(value)


def parse_native_line(line):
    fields = line.split()
    if len(fields) != 7:
        raise ValueError("Native processed field count is not seven")
    aircraft = integer(fields[1], positive=True)
    frame, xyz, error = None, None, None
    try:
        frame = integer(fields[0])
        xyz = [Decimal(t) for t in fields[2:5]]
        if not all(value.is_finite() for value in xyz):
            raise ValueError("Nonfinite position")
    except (ValueError, InvalidOperation):
        error = "invalid_frame_or_coordinate"
    return aircraft, {"frame": frame, "xyz_km": xyz, "error": error}


def ingest(archive, private_out, public_out):
    private_out, public_out = private_out.resolve(), public_out.resolve()
    if private_out.is_relative_to(ROOT) and not private_out.is_relative_to(
        ROOT / ".source"
    ):
        raise ValueError(
            "Coordinate working input must be outside Git or within ignored .source"
        )
    plan = json.loads((ROOT / "data/source_plan.json").read_text(encoding="utf-8"))
    content = archive.read_bytes()
    assert len(content) == plan["file"]["bytes"]
    assert hashlib.md5(content).hexdigest() == plan["file"]["publisher_md5"]
    digest = hashlib.sha256(content).hexdigest()
    assert digest == "d058adbbc19393bdb08ace91e0505933c858b626e5098ed87dd7645ab045485c"
    members, tracks, counts = [], defaultdict(list), Counter()
    with ZipFile(archive) as source:
        eligible = []
        for info in source.infolist():
            path = PurePosixPath(info.filename)
            assert not path.is_absolute() and ".." not in path.parts
            if "__MACOSX" in path.parts:
                continue
            if path.suffix == ".txt" and any(
                "/processed_data/" + kind + "/" in "/" + info.filename
                for kind in ["train", "test"]
            ):
                assert path.stem.isdigit()
                eligible.append(info)
        assert len({int(PurePosixPath(i.filename).stem) for i in eligible}) == len(
            eligible
        )
        selected = sorted(
            eligible, key=lambda i: (int(PurePosixPath(i.filename).stem), i.filename)
        )[: plan["scene_count"]]
        assert len(selected) == plan["scene_count"]
        assert (
            sum(i.file_size for i in selected)
            <= plan["maximum_selected_uncompressed_bytes"]
        )
        for scene_order, info in enumerate(selected, 1):
            payload = source.read(info)
            assert len(payload) == info.file_size
            lines = payload.decode("utf-8-sig").splitlines()
            members.append(
                {
                    "member": info.filename,
                    "scene_order": scene_order,
                    "bytes": len(payload),
                    "rows": len(lines),
                    "crc32": f"{info.CRC:08x}",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            for number, line in enumerate(lines, 1):
                counts["native_rows"] += 1
                assert counts["native_rows"] <= plan["maximum_native_rows"]
                aircraft, row = parse_native_line(line)
                row.update(row=number, member=info.filename)
                tracks[(scene_order, aircraft)].append(row)
    private_runs, exclusions = [], []
    for (scene, aircraft), rows in sorted(tracks.items()):
        track_ordinal = f'T{len(exclusions) + counts["valid_tracks"] + 1:04d}'
        result = qualify_track(
            rows, plan["minimum_run_points"], plan["maximum_run_points"]
        )
        counts["out_of_order_transitions"] += result["stats"][
            "out_of_order_transitions"
        ]
        counts["duplicate_frame_rows"] += result["stats"]["duplicate_frame_rows"]
        if result["reason"]:
            exclusions.append(
                {
                    "track": track_ordinal,
                    "scene_order": scene,
                    "reason": result["reason"],
                    "rows": len(rows),
                }
            )
            continue
        counts["valid_tracks"] += 1
        counts["gaps_split"] += result["stats"]["gaps_split"]
        for n in result["excluded_lengths"]:
            exclusions.append(
                {
                    "track": track_ordinal,
                    "scene_order": scene,
                    "reason": "run_point_count_outside_fixed_bound",
                    "rows": n,
                }
            )
        for chunk in result["runs"]:
            partition = (
                "development"
                if hashlib.sha256(
                    ("trajair-aircraft-v1:" + str(aircraft)).encode()
                ).digest()[0]
                % 2
                == 0
                else "final"
            )
            private_runs.append(
                {
                    "scene_order": scene,
                    "member": chunk[0]["member"],
                    "aircraft_source_id": aircraft,
                    "partition": partition,
                    "native_start_frame": chunk[0]["frame"],
                    "elapsed_seconds": [r["frame"] - chunk[0]["frame"] for r in chunk],
                    "xyz_m": [[float(v * 1000) for v in r["xyz_km"]] for r in chunk],
                    "native_rows": [r["row"] for r in chunk],
                }
            )
    group_ids = {}
    for partition in ["development", "final"]:
        prefix = "D" if partition == "development" else "F"
        identifiers = sorted(
            {
                r["aircraft_source_id"]
                for r in private_runs
                if r["partition"] == partition
            }
        )
        for number, identifier in enumerate(identifiers, 1):
            group_ids[identifier] = f"{prefix}G{number:03d}"
    run_counts = Counter()
    manifest = []
    for run in private_runs:
        part = run["partition"]
        prefix = "D" if part == "development" else "F"
        run_counts[part] += 1
        run["run_id"] = f"{prefix}{run_counts[part]:04d}"
        run["group_id"] = group_ids[run["aircraft_source_id"]]
        manifest.append(
            {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "partition": part,
                "scene_order": run["scene_order"],
                "points": len(run["elapsed_seconds"]),
                "duration_seconds": run["elapsed_seconds"][-1],
            }
        )
    group_counts = {
        part: len({r["group_id"] for r in private_runs if r["partition"] == part})
        for part in ["development", "final"]
    }
    failed = [
        f"{part}:fewer_than_two_qualifying_aircraft"
        for part, n in group_counts.items()
        if n < plan["minimum_qualifying_aircraft_per_partition"]
    ]
    private_out.mkdir(parents=True, exist_ok=False)
    public_out.mkdir(parents=True, exist_ok=True)
    write_json(private_out / "runs.json", private_runs)
    write_json(
        private_out / "exclusion_source_map.json",
        {
            "source_track_keys_private": [
                {"scene_order": s, "aircraft_source_id": a} for s, a in sorted(tracks)
            ],
            "source_archive_sha256": digest,
        },
    )
    write_json(public_out / "source_members.json", members)
    with (public_out / "run_manifest.csv").open(
        "x", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            [
                "run_id",
                "group_id",
                "partition",
                "scene_order",
                "points",
                "duration_seconds",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(manifest)
    cohort = {
        "status": "PASS_SOURCE_COHORT" if not failed else "FAIL_SOURCE_COHORT",
        "archive_sha256": digest,
        "archive_bytes": len(content),
        "eligible_scenes": len(eligible),
        "selected_scenes": len(members),
        "selected_member_bytes": sum(m["bytes"] for m in members),
        "native_counts": dict(counts),
        "native_tracks": len(tracks),
        "qualified_runs": len(private_runs),
        "qualified_points": sum(len(r["elapsed_seconds"]) for r in private_runs),
        "aircraft_groups": group_counts,
        "run_counts": dict(run_counts),
        "excluded_counts": dict(Counter(e["reason"] for e in exclusions)),
        "exclusions": exclusions,
        "failed_gates": failed,
        "scenarios_or_compression_scores_run": False,
        "coordinate_records_and_source_identifiers_public": False,
        "units": {
            "native_positions": "km",
            "working_positions": "m",
            "elapsed_frame_seconds": 1,
        },
        "source_reference": "Processed cubic-interpolated 1 Hz source tracks; no raw-sensor accuracy or complete-flight claim",
    }
    write_json(public_out / "cohort.json", cohort)
    print(
        json.dumps(
            {
                k: cohort[k]
                for k in [
                    "status",
                    "native_counts",
                    "native_tracks",
                    "qualified_runs",
                    "qualified_points",
                    "aircraft_groups",
                    "run_counts",
                    "excluded_counts",
                    "failed_gates",
                ]
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--private-out", type=Path, required=True)
    parser.add_argument("--public-out", type=Path, required=True)
    args = parser.parse_args()
    ingest(args.archive, args.private_out, args.public_out)
