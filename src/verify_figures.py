"""Bind every plotted source value and actual SVG coordinate without plotting helpers."""

import csv
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
S = "{http://www.w3.org/2000/svg}"


def read(name):
    return json.loads((ROOT / name).read_bytes())


def vertices(node):
    path = node.find(".//" + S + "path")
    assert path is not None
    values = [
        float(s)
        for s in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"])
    ]
    assert len(values) % 2 == 0
    return list(zip(values[::2], values[1::2]))


def main():
    maps = read("figures/value_map.json")
    development = read("results/development/configuration_summary.json")
    final = read("results/final/configuration_summary.json")
    with (ROOT / "results/final/run_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        final_rows = list(csv.DictReader(handle))
    with (ROOT / "examples/geometry-hides-time.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        teaching = list(csv.DictReader(handle))
    example = read("data/example_validation.json")["cases"]["geometry-hides-time"][
        "metrics"
    ]
    assert len(maps) == 3
    values = coordinates = hashes = labels = 0
    for mapping in maps:
        tree = ElementTree.parse(ROOT / "figures" / (mapping["figure"] + ".svg"))
        ids = {node.get("id"): node for node in tree.iter() if node.get("id")}
        areas = []
        for index in range(len(mapping["axes"])):
            corners = vertices(ids["data-area-" + str(index)])
            areas.append(
                (
                    min(x for x, y in corners),
                    max(x for x, y in corners),
                    min(y for x, y in corners),
                    max(y for x, y in corners),
                )
            )
        assert len(mapping["items"]) == (
            5 if mapping["figure"] == "authored_timing" else 23
        )
        for item in mapping["items"]:
            kind = item["kind"]
            if kind in [
                "development_bytes",
                "development_position",
                "final_failures",
                "final_occupancy",
            ]:
                source = development if kind.startswith("development") else final
                record = next(
                    r for r in source if r["configuration"] == item["configuration"]
                )
                y = [r["configuration"] for r in source].index(item["configuration"])
                if kind == "development_bytes":
                    value = record["total_byte_ratio"] * 100
                elif kind == "development_position":
                    value = record["max_synchronous_error_m"]
                elif kind == "final_failures":
                    value = record["violating_runs"]
                else:
                    rows = [
                        r
                        for r in final_rows
                        if r["configuration"] == item["configuration"]
                    ]
                    assert len(rows) == 65
                    value = max(
                        int(r[f"zone{radius}_occupancy_absolute_error_s"])
                        for r in rows
                        for radius in [1000, 2000]
                    )
                display = (
                    f"{value:.1f}" if kind.startswith("development") else str(value)
                )
                assert (
                    item["y"] == y
                    and item["height"] == 0.56
                    and item["value"] == value
                    and item["display"] == display
                )
                label = " ".join("".join(ids[item["label_id"]].itertext()).split())
                assert label == display
                labels += 1
                values += 1
                expected = [
                    (0, y - 0.28),
                    (value, y - 0.28),
                    (value, y + 0.28),
                    (0, y + 0.28),
                ]
                if kind == "development_bytes":
                    expected_axis = {"xlim": [0, 112], "ylim": [10.6, -0.6]}
                elif kind == "development_position":
                    expected_axis = {"xlim": [0, 2000], "ylim": [10.6, -0.6]}
                elif kind == "final_failures":
                    expected_axis = {"xlim": [0, 73], "ylim": [10.6, -0.6]}
                else:
                    expected_axis = {"xlim": [0, 76], "ylim": [10.6, -0.6]}
                assert mapping["axes"][item["axis"]] == expected_axis
            else:
                if kind == "position_budget":
                    xs, ys = [25, 25], [-0.5, 10.5]
                elif kind == "occupancy_budget":
                    xs, ys = [2, 2], [-0.5, 10.5]
                elif kind == "authored_reference":
                    xs, ys = [int(r["elapsed_seconds"]) for r in teaching], [
                        float(r["x_m"]) for r in teaching
                    ]
                elif kind == "authored_chord":
                    xs = [
                        int(teaching[0]["elapsed_seconds"]),
                        int(teaching[-1]["elapsed_seconds"]),
                    ]
                    ys = [float(teaching[0]["x_m"]), float(teaching[-1]["x_m"])]
                elif kind == "authored_exit":
                    field = (
                        "zone1000_"
                        + ("reference" if item["view"] == "reference" else "decoded")
                        + "_first_transition_s"
                    )
                    xs, ys = [example[field]] * 2, [0, 4000]
                else:
                    assert kind == "authored_radius"
                    xs, ys = [0, 200], [1000, 1000]
                assert item["x"] == xs and item["values"] == ys
                if kind.startswith("authored"):
                    assert mapping["axes"][0] == {"xlim": [0, 200], "ylim": [0, 4300]}
                for x, y in zip(xs, ys):
                    assert isinstance(x, (int, float)) and isinstance(y, (int, float))
                    values += 2
                expected = list(zip(xs, ys))
            axis = mapping["axes"][item["axis"]]
            xmin, xmax = axis["xlim"]
            ymin, ymax = axis["ylim"]
            left, right, top, bottom = areas[item["axis"]]
            projected = [
                (
                    left + (x - xmin) / (xmax - xmin) * (right - left),
                    bottom - (y - ymin) / (ymax - ymin) * (bottom - top),
                )
                for x, y in expected
            ]
            actual = vertices(ids[item["id"]])
            assert len(actual) == len(projected), item["id"]
            for (x, y), (a, b) in zip(actual, projected):
                assert abs(x - a) <= 3e-5 and abs(y - b) <= 3e-5, (
                    item["id"],
                    (x, y),
                    (a, b),
                )
                coordinates += 2
        for name, digest in mapping["files"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            hashes += 1
        assert (ROOT / "paper/images" / (mapping["figure"] + ".pdf")).read_bytes() == (
            ROOT / "figures" / (mapping["figure"] + ".pdf")
        ).read_bytes()
    result = {
        "status": "PASS_SOURCE_VALUES_LABELS_AND_ACTUAL_SVG_GEOMETRY",
        "figures": 3,
        "mapped_source_values": values,
        "svg_coordinates": coordinates,
        "individual_numeric_labels": labels,
        "file_hashes": hashes,
        "source_coordinate_publication": "Only authored example position/time lines; actual recorded-source figures use aggregate metrics",
    }
    (ROOT / "figures/verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
