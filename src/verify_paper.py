"""Bind manuscript values to checked results and verify the reviewed PDF bytes."""

import ast
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_bytes())


def main():
    tex = (ROOT / "paper/main.tex").read_text()
    pdf = PdfReader(ROOT / "paper/manuscript.pdf")
    assert len(pdf.pages) == 6
    pages = [p.extract_text() or "" for p in pdf.pages]
    assert all(p.strip() for p in pages)
    flat = "\n".join(pages)
    checks = 0
    bindings = read("paper_bindings.json")
    for name, item in bindings.items():
        path = (ROOT / item["file"]).resolve()
        assert path.is_relative_to(ROOT) and path.suffix == ".json"
        value = json.loads(path.read_bytes())
        for key in item["pointer"]:
            value = value[key]
        assert item["value"] == value
        checks += 1
        fmt = item["format"]
        assert fmt in ["integer", "percent", "decimal"]
        display = (
            f"{value:,}"
            if fmt == "integer"
            else f"{value*100:.1f}" if fmt == "percent" else f"{value:.2f}"
        )
        assert item["display"] == display
        checks += 1
        assert "\\newcommand{\\" + name + "}{" + display + "}" in tex
        assert "\\" + name + "{}" in tex
        checks += 1
        assert display in flat
        checks += 1
    assert len(bindings) == 66
    checks += 1
    dev, final = [
        read(f"results/{phase}/configuration_summary.json")
        for phase in ["development", "final"]
    ]
    assert [x["configuration"] for x in dev if x["admissible_on_every_run"]] == [
        "identity"
    ]
    assert final[0]["violating_runs"] == 0 and final[0]["total_byte_ratio"] == 1
    assert final[6]["violating_runs"] == 65 and final[8]["violating_runs"] == 12
    assert final[8]["gate_failure_counts"]["route_pass"] == 12
    assert all(
        v == 0 for k, v in final[8]["gate_failure_counts"].items() if k != "route_pass"
    )
    checks += 5
    assert read("data/frozen_dependencies.json")["dependency_count"] == 34
    fixture_count = sum(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        for p in (ROOT / "tests").glob("test_*.py")
        for n in ast.walk(ast.parse(p.read_text()))
    )
    assert fixture_count == 34
    checks += 2
    replay = read("data/replay_validation.json")
    assert replay["analysis_revision"] == "d3d630f4c9d20f89894ad1f5d99c6b8bfc569d76"
    assert [x["python"] for x in replay["runtimes"]] == ["3.10.11", "3.12.14"]
    for row in replay["runtimes"]:
        assert row["status"] == "PASS_COMPLETE_OFFLINE_REPLAY"
        assert row["exact_fields"] == 168193 and row["tolerant_fields"] == 16863
        assert row["runtime_specific_hashes_independently_checked"] == 2340
        checks += 3
    for phrase in [
        "Not peer reviewed",
        "processed, cubic-interpolated 1 Hz",
        "not sensor ground truth",
        "All source coordinates were present",
        "34 analytical dependencies",
        "no final-set reselection",
        "macro RMSE is not the square root of macro MSE",
        "not a recorded aircraft event",
        "separate download of the pinned public archive",
        "not an operational safety standard",
        "no new compression algorithm",
        "does not prove that every reducer",
        "could preserve source fluctuations",
    ]:
        assert phrase in tex, phrase
        checks += 1
    build = read("paper/build_receipt.json")
    assert (
        build["status"] == "PASS_COMPILE_AND_SIX_PAGE_VISUAL_REVIEW"
        and build["pages"] == 6
        and not build["layout_warnings"]
    )
    checks += 1
    for name, digest in build["artifact_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
        checks += 1
    for p in (ROOT / "paper/images").glob("*.pdf"):
        assert p.read_bytes() == (ROOT / "figures" / p.name).read_bytes()
        checks += 1
    result = dict(
        status="PASS_SOURCE_BOUND_MANUSCRIPT",
        pages=6,
        bound_measurements=len(bindings),
        conditions=checks,
        manuscript_sha256=hashlib.sha256(
            (ROOT / "paper/manuscript.pdf").read_bytes()
        ).hexdigest(),
        page_characters=[len(p) for p in pages],
        rendering="All six local Poppler renders inspected; CI checks source bindings and exact reviewed bytes without compiling TeX.",
    )
    (ROOT / "paper_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
