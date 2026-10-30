"""Independent native ZIP, SQLite run boundaries and Decimal coordinate checks."""

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def verify(archive, private_input, public_input):
    conditions = 0

    def check(condition, label):
        nonlocal conditions
        conditions += 1
        if not condition:
            raise AssertionError(label)

    plan = json.loads((ROOT / "data/source_plan.json").read_text(encoding="utf-8"))
    cohort = json.loads((public_input / "cohort.json").read_text(encoding="utf-8"))
    member_receipt = json.loads(
        (public_input / "source_members.json").read_text(encoding="utf-8")
    )
    private_runs = json.loads((private_input / "runs.json").read_text(encoding="utf-8"))
    with (public_input / "run_manifest.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        public_runs = list(csv.DictReader(handle))
    content = archive.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    check(digest == cohort["archive_sha256"], "archive SHA256 identity")
    check(
        len(content) == plan["file"]["bytes"] == cohort["archive_bytes"],
        "archive byte count",
    )
    check(
        hashlib.md5(content).hexdigest() == plan["file"]["publisher_md5"],
        "publisher checksum",
    )
    db = sqlite3.connect(":memory:")
    db.execute(
        "create table points(scene integer, aircraft integer, frame integer, x text, y text, z text, row_number integer)"
    )
    with ZipFile(archive) as source:
        candidates = []
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            check(
                not path.is_absolute() and ".." not in path.parts,
                "safe source archive member",
            )
            if "__MACOSX" in path.parts:
                continue
            if (
                path.suffix == ".txt"
                and len(path.parts) >= 3
                and path.parts[-3:-1]
                in [("processed_data", "train"), ("processed_data", "test")]
            ):
                check(path.stem.isdigit(), "numeric scene identifier")
                candidates.append(entry)
        check(
            len({int(PurePosixPath(i.filename).stem) for i in candidates})
            == len(candidates),
            "scene identity unique across source split labels",
        )
        selected = sorted(
            candidates, key=lambda i: (int(PurePosixPath(i.filename).stem), i.filename)
        )[: plan["scene_count"]]
        check(len(selected) == 24 == len(member_receipt), "fixed scene count")
        check(
            sum(i.file_size for i in selected)
            <= plan["maximum_selected_uncompressed_bytes"],
            "selected uncompressed bound",
        )
        for scene, (entry, receipt) in enumerate(zip(selected, member_receipt), 1):
            payload = source.read(entry)
            check(
                entry.filename == receipt["member"] and scene == receipt["scene_order"],
                "metadata-only scene selection",
            )
            check(
                len(payload) == entry.file_size == receipt["bytes"],
                "native member byte count",
            )
            check(f"{entry.CRC:08x}" == receipt["crc32"], "native member CRC")
            check(
                hashlib.sha256(payload).hexdigest() == receipt["sha256"],
                "native member SHA256",
            )
            lines = payload.decode("utf-8-sig").splitlines()
            check(len(lines) == receipt["rows"], "native member row count")
            for number, line in enumerate(lines, 1):
                fields = line.split()
                check(len(fields) == 7, "seven native fields")
                frame, aircraft = Decimal(fields[0]), Decimal(fields[1])
                check(
                    frame.is_finite()
                    and frame >= 0
                    and frame == frame.to_integral_value(),
                    "native frame integer",
                )
                check(
                    aircraft.is_finite()
                    and aircraft > 0
                    and aircraft == aircraft.to_integral_value(),
                    "native aircraft integer",
                )
                positions = [Decimal(t) for t in fields[2:5]]
                for position in positions:
                    check(position.is_finite(), "native coordinate finite")
                db.execute(
                    "insert into points values (?,?,?,?,?,?,?)",
                    (
                        scene,
                        int(aircraft),
                        int(frame),
                        *[str(p * 1000) for p in positions],
                        number,
                    ),
                )
    count = db.execute("select count(*) from points").fetchone()[0]
    check(
        count == cohort["native_counts"]["native_rows"] <= plan["maximum_native_rows"],
        "all native rows and bound",
    )
    duplicates = db.execute(
        "select scene,aircraft,frame,count(*) from points group by scene,aircraft,frame having count(*)>1"
    ).fetchall()
    check(
        not duplicates and cohort["native_counts"]["duplicate_frame_rows"] == 0,
        "current source contains no duplicate frame keys",
    )
    reverse_count = db.execute(
        """with ordered as(select frame,lag(frame)over(partition by scene,aircraft order by row_number)previous from points)
        select count(*) from ordered where frame<previous"""
    ).fetchone()[0]
    check(
        reverse_count == cohort["native_counts"]["out_of_order_transitions"],
        "native source ordering",
    )
    db.execute(
        """create table segmented as with previous as(
        select *,lag(frame)over(partition by scene,aircraft order by frame)prior from points),
        starts as(select *,case when prior is null or frame-prior<>1 then 1 else 0 end begins from previous)
        select *,sum(begins)over(partition by scene,aircraft order by frame)block from starts"""
    )
    pieces = db.execute(
        """select scene,aircraft,block,min(frame),max(frame),count(*)
        from segmented group by scene,aircraft,block order by scene,aircraft,min(frame)"""
    ).fetchall()
    qualified = [
        p
        for p in pieces
        if plan["minimum_run_points"] <= p[5] <= plan["maximum_run_points"]
    ]
    excluded = [
        p
        for p in pieces
        if not plan["minimum_run_points"] <= p[5] <= plan["maximum_run_points"]
    ]
    track_count = db.execute(
        "select count(*) from(select distinct scene,aircraft from points)"
    ).fetchone()[0]
    check(
        track_count
        == cohort["native_tracks"]
        == cohort["native_counts"]["valid_tracks"],
        "native track denominator",
    )
    check(
        len(pieces) - track_count == cohort["native_counts"]["gaps_split"],
        "SQL gap segmentation",
    )
    check(
        len(qualified)
        == cohort["qualified_runs"]
        == len(private_runs)
        == len(public_runs),
        "qualified run denominator",
    )
    check(
        sum(p[5] for p in qualified) == cohort["qualified_points"],
        "qualified source point denominator",
    )
    check(
        len(excluded) == len(cohort["exclusions"]),
        "all short/long run exclusions retained",
    )
    check(
        sorted(p[5] for p in excluded)
        == sorted(e["rows"] for e in cohort["exclusions"]),
        "native excluded run lengths",
    )
    check(
        all(
            e["reason"] == "run_point_count_outside_fixed_bound"
            for e in cohort["exclusions"]
        ),
        "native exclusion reasons",
    )
    partitions = {}
    for (aircraft,) in db.execute(
        "select distinct aircraft from points order by aircraft"
    ):
        parts = ["development", "final"]
        partitions[aircraft] = parts[
            hashlib.sha256(("trajair-aircraft-v1:" + str(aircraft)).encode()).digest()[
                0
            ]
            & 1
        ]
    identifiers = {
        part: sorted({p[1] for p in qualified if partitions[p[1]] == part})
        for part in ["development", "final"]
    }
    check(
        not set(identifiers["development"]) & set(identifiers["final"]),
        "native aircraft holdout disjointness",
    )
    for part in identifiers:
        check(
            len(identifiers[part])
            == cohort["aircraft_groups"][part]
            >= plan["minimum_qualifying_aircraft_per_partition"],
            "minimum distinct aircraft per partition",
        )
    run_number, points_checked = Counter(), 0
    largest_conversion_error = Decimal(0)
    with localcontext() as context:
        context.prec = 70
        for piece, private, public in zip(qualified, private_runs, public_runs):
            scene, aircraft, block, first, last, n = piece
            part = partitions[aircraft]
            prefix = "D" if part == "development" else "F"
            run_number[part] += 1
            run_id = f"{prefix}{run_number[part]:04d}"
            group = f"{prefix}G{identifiers[part].index(aircraft) + 1:03d}"
            expected_public = {
                "run_id": run_id,
                "group_id": group,
                "partition": part,
                "scene_order": str(scene),
                "points": str(n),
                "duration_seconds": str(last - first),
            }
            for key, value in expected_public.items():
                check(public[key] == value, "independent ordinal manifest:" + key)
            check(
                private["run_id"] == run_id
                and private["group_id"] == group
                and private["partition"] == part,
                "private run and partition identity",
            )
            check(
                private["aircraft_source_id"] == aircraft
                and private["scene_order"] == scene
                and private["native_start_frame"] == first,
                "private native identity binding",
            )
            rows = db.execute(
                "select frame,x,y,z,row_number from segmented where scene=? and aircraft=? and block=? order by frame",
                (scene, aircraft, block),
            ).fetchall()
            check(
                len(private["xyz_m"])
                == len(private["elapsed_seconds"])
                == len(private["native_rows"])
                == n,
                "private array lengths",
            )
            for index, row in enumerate(rows):
                check(
                    private["elapsed_seconds"][index] == row[0] - first,
                    "native elapsed frame",
                )
                check(
                    private["native_rows"][index] == row[4], "native source row binding"
                )
                for axis in range(3):
                    error = abs(
                        Decimal.from_float(private["xyz_m"][index][axis])
                        - Decimal(row[axis + 1])
                    )
                    check(
                        error <= Decimal("1e-8"),
                        "independent native Decimal kilometre conversion",
                    )
                    largest_conversion_error = max(largest_conversion_error, error)
                points_checked += 1
    check(dict(run_number) == cohort["run_counts"], "per-partition run counts")
    check(
        cohort["status"] == "PASS_SOURCE_COHORT" and not cohort["failed_gates"],
        "source cohort status",
    )
    check(
        cohort["scenarios_or_compression_scores_run"] is False,
        "source quality before compression scores",
    )
    check(
        hashlib.sha256(archive.read_bytes()).hexdigest() == digest,
        "source archive unchanged after checks",
    )
    result = {
        "status": "PASS_INDEPENDENT_NATIVE_COHORT",
        "conditions": conditions,
        "native_rows": count,
        "qualified_points_checked": points_checked,
        "qualified_runs": len(qualified),
        "aircraft_groups": {p: len(v) for p, v in identifiers.items()},
        "maximum_coordinate_conversion_error_m": str(largest_conversion_error),
        "source_sha256_unchanged": digest,
        "method": "Separate native ZIP parsing, SQLite lag/cumulative segment boundaries and 70-digit Decimal coordinate checks; no ingestion helper imports",
        "public_input_hashes": {
            name: hashlib.sha256((public_input / name).read_bytes()).hexdigest()
            for name in ["cohort.json", "source_members.json", "run_manifest.csv"]
        },
    }
    (public_input / "source_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument("--public-input", type=Path, required=True)
    args = parser.parse_args()
    verify(args.archive, args.private_input, args.public_input)
