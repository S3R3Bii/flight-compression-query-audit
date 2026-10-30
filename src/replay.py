"""Acquire data separately, then verify the complete study from an unchanged archive."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
from compare_replay import Comparison

ROOT = Path(__file__).resolve().parents[1]


def replay(archive, work, receipt, through="final"):
    archive, work = archive.resolve(), work.resolve()
    if work.is_relative_to(ROOT) and not work.is_relative_to(ROOT / ".source"):
        raise ValueError(
            "Replay coordinate work must stay outside Git or inside ignored .source"
        )
    if np.__version__ != "2.2.5":
        raise RuntimeError("Replay requires the recorded NumPy2.2.5")
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected_source = json.loads((ROOT / "data/source_receipt.json").read_bytes())[
        "sha256"
    ]
    if before != expected_source:
        raise RuntimeError("Source archive does not match the recorded capture")
    work.mkdir(parents=True, exist_ok=False)

    def run(script, *arguments):
        subprocess.run(
            [sys.executable, str(ROOT / "src" / script), *map(str, arguments)],
            cwd=ROOT,
            check=True,
        )

    comparison = Comparison()
    native_public, native_private = work / "source-evidence", work / "coordinate-input"
    run(
        "ingest_source.py",
        "--archive",
        archive,
        "--private-out",
        native_private,
        "--public-out",
        native_public,
    )
    run(
        "verify_source.py",
        "--archive",
        archive,
        "--private-input",
        native_private,
        "--public-input",
        native_public,
    )
    for name in [
        "cohort.json",
        "source_members.json",
        "run_manifest.csv",
        "source_verification.json",
    ]:
        comparison.file(ROOT / "data" / name, native_public / name)
    phase_proofs = {}
    for phase in (
        ["development"] if through == "development" else ["development", "final"]
    ):
        public, private = work / phase, work / (phase + "-coordinates")
        run(
            "analyze.py",
            "--private-input",
            native_private,
            "--out",
            public,
            "--private-out",
            private,
            "--phase",
            phase,
        )
        run(
            "verify_metrics.py",
            "--archive",
            archive,
            "--private-input",
            native_private,
            "--private-work",
            private,
            "--public-output",
            public,
            "--phase",
            phase,
        )
        names = [
            "run_metrics.csv",
            "retention_witnesses.json",
            "configuration_summary.json",
            "independent_verification.json",
        ]
        names.append(
            "selection.json" if phase == "development" else "final_result.json"
        )
        for name in names:
            comparison.file(
                ROOT / "results" / phase / name,
                public / name,
                as_run=name
                in ["retention_witnesses.json", "independent_verification.json"],
            )
        proof = json.loads((public / "independent_verification.json").read_bytes())
        if proof["status"] != "PASS_INDEPENDENT_ENCODED_REPLAY_AND_SELECTION":
            raise RuntimeError("Independent replay proof did not pass")
        phase_proofs[phase] = {
            k: proof[k]
            for k in [
                "conditions",
                "cases",
                "point_diagnostics_checked",
                "selected_configuration",
            ]
        }
    run("check_examples.py", "--out", work / "example_validation.json")
    comparison.file(
        ROOT / "data/example_validation.json", work / "example_validation.json"
    )
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        check=True,
    )
    if through == "final":
        run("verify_figures.py")
        run("verify_paper.py")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != before:
        raise RuntimeError("Original source archive changed during replay")
    result = {
        "status": (
            "PASS_COMPLETE_OFFLINE_REPLAY"
            if through == "final"
            else "PASS_DEVELOPMENT_OFFLINE_REPLAY"
        ),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "exact_fields": comparison.exact,
        "tolerant_fields": comparison.tolerant,
        "runtime_specific_hashes_independently_checked": comparison.as_run_hashes,
        "largest_difference_fraction_of_declared_tolerance": str(
            comparison.maximum_normalized_difference
        ),
        "phase_proofs": phase_proofs,
        "source_archive_sha256_unchanged": before,
        "raw_source_or_coordinate_artifacts_public": False,
    }
    receipt.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--through", choices=["development", "final"], default="final")
    args = parser.parse_args()
    replay(args.archive, args.work, args.receipt, args.through)
