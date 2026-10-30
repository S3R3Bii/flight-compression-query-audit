"""Render complete fixed-grid comparisons and a separate authored timing example."""

import csv
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "svg.fonttype": "none",
        "svg.hashsalt": "study40-query-fidelity",
        "path.simplify": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": "#203d4c",
        "axes.labelcolor": "#203d4c",
        "savefig.facecolor": "white",
    }
)


def read(name):
    return json.loads((ROOT / name).read_bytes())


def label(name):
    if name == "identity":
        return "Keep all points"
    method, value = name.split("-")
    return (
        ("Every " + value + " points")
        if method == "stride"
        else ("Geometric " if method == "geometric" else "Time-aware ") + value + " m"
    )


def color(name):
    return (
        "#69747e"
        if name == "identity"
        else (
            "#b16b2e"
            if name.startswith("stride")
            else "#b55847" if name.startswith("geometric") else "#245d74"
        )
    )


def finish(figure, axes, name, items):
    for number, axis in enumerate(axes):
        axis.patch.set_gid("data-area-" + str(number))
    for item in items:
        item.pop("artist").set_gid(item["id"])
    hashes = {}
    for extension in ["pdf", "svg", "png"]:
        path = ROOT / "figures" / (name + "." + extension)
        metadata = (
            {"Date": None}
            if extension == "svg"
            else {"CreationDate": None, "ModDate": None} if extension == "pdf" else None
        )
        figure.savefig(path, dpi=150, metadata=metadata)
        if extension == "svg":
            path.write_bytes(
                (
                    "\n".join(
                        x.rstrip()
                        for x in path.read_text(encoding="utf-8").splitlines()
                    )
                    + "\n"
                ).encode()
            )
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    shutil.copyfile(
        ROOT / "figures" / (name + ".pdf"), ROOT / "paper/images" / (name + ".pdf")
    )
    result = {
        "figure": name,
        "axes": [
            {"xlim": list(a.get_xlim()), "ylim": list(a.get_ylim())} for a in axes
        ],
        "items": items,
        "files": hashes,
    }
    plt.close(figure)
    return result


def main():
    (ROOT / "figures").mkdir(exist_ok=True)
    (ROOT / "paper/images").mkdir(parents=True, exist_ok=True)
    development, final = read("results/development/configuration_summary.json"), read(
        "results/final/configuration_summary.json"
    )
    with (ROOT / "results/final/run_metrics.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        final_rows = list(csv.DictReader(handle))
    maps = []
    for phase, records, name in [
        ("development", development, "development_fidelity"),
        ("final", final, "final_query_failures"),
    ]:
        figure, axes = plt.subplots(1, 2, figsize=(8.8, 5.5), sharey=True)
        figure.subplots_adjust(
            left=0.22, right=0.97, bottom=0.17, top=0.78, wspace=0.32
        )
        items = []
        for index, record in enumerate(records):
            config = record["configuration"]
            if phase == "development":
                values = [
                    100 * record["total_byte_ratio"],
                    record["max_synchronous_error_m"],
                ]
                kinds = ["development_bytes", "development_position"]
            else:
                cases = [r for r in final_rows if r["configuration"] == config]
                values = [
                    record["violating_runs"],
                    max(
                        int(r[f"zone{radius}_occupancy_absolute_error_s"])
                        for r in cases
                        for radius in [1000, 2000]
                    ),
                ]
                kinds = ["final_failures", "final_occupancy"]
            for number, (value, kind) in enumerate(zip(values, kinds)):
                artist = axes[number].barh(
                    index, value, height=0.56, color=color(config)
                )[0]
                display = f"{value:.1f}" if phase == "development" else str(value)
                offset = (2.0 if number == 0 else 34) if phase == "development" else 1.3
                value_label = axes[number].text(
                    value + offset, index, display, va="center", fontsize=9
                )
                value_label.set_gid(f"{kind}-{index}-label")
                items.append(
                    {
                        "artist": artist,
                        "id": f"{kind}-{index}",
                        "kind": kind,
                        "axis": number,
                        "configuration": config,
                        "y": index,
                        "height": 0.56,
                        "value": value,
                        "display": display,
                        "label_id": f"{kind}-{index}-label",
                    }
                )
        for axis in axes:
            axis.set_ylim(10.6, -0.6)
            axis.set_yticks(range(11), [label(r["configuration"]) for r in records])
            axis.xaxis.grid(alpha=0.15)
            axis.set_axisbelow(True)
        axes[1].tick_params(labelleft=False)
        if phase == "development":
            axes[0].set_xlim(0, 112)
            axes[0].set_xticks([0, 25, 50, 75, 100])
            axes[0].set_xlabel("Retained point-record bytes (%)")
            axes[1].set_xlim(0, 2000)
            axes[1].set_xlabel("Maximum time-aligned error (m)")
            budget, budget_kind = 25, "position_budget"
            title = "Development selected the original point records"
            subtitle = "41 runs from 13 aircraft; every point-reducing setting fails at least one fixed budget."
            footer = "Byte ratios use an 8-byte header plus 32 bytes per retained point. All axes start at zero."
        else:
            axes[0].set_xlim(0, 73)
            axes[0].set_xticks([0, 20, 40, 60])
            axes[0].set_xlabel("Runs failing a budget (of 65)")
            axes[1].set_xlim(0, 76)
            axes[1].set_xticks([0, 20, 40, 60])
            axes[1].set_xlabel("Maximum zone occupancy error (s)")
            budget, budget_kind = 2, "occupancy_budget"
            title = "Final aircraft preserve the conservative conclusion"
            subtitle = "65 runs from 17 different aircraft; all fixed settings shown, with no new selection."
            footer = "Occupancy errors are maxima over both authored zones. Runs share aircraft and source processing."
        artist = axes[1].plot(
            [budget, budget], [-0.5, 10.5], "--", color="#536f7c", linewidth=1.1
        )[0]
        axes[1].text(
            0.02,
            1.04,
            f"Dashed line: {budget} "
            + ("m position budget" if phase == "development" else "s occupancy budget"),
            transform=axes[1].transAxes,
            fontsize=9,
        )
        items.append(
            {
                "artist": artist,
                "id": budget_kind,
                "kind": budget_kind,
                "axis": 1,
                "x": [budget, budget],
                "values": [-0.5, 10.5],
            }
        )
        figure.suptitle(title, x=0.22, y=0.97, ha="left", fontsize=14, weight="bold")
        figure.text(0.22, 0.88, subtitle, fontsize=9)
        figure.text(0.22, 0.045, footer, fontsize=8.4)
        maps.append(finish(figure, axes, name, items))

    with (ROOT / "examples/geometry-hides-time.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        teaching = list(csv.DictReader(handle))
    times = [int(r["elapsed_seconds"]) for r in teaching]
    positions = [float(r["x_m"]) for r in teaching]
    example = read("data/example_validation.json")["cases"]["geometry-hides-time"][
        "metrics"
    ]
    figure, axis = plt.subplots(figsize=(8.8, 4.7))
    figure.subplots_adjust(left=0.13, right=0.96, bottom=0.21, top=0.75)
    items = []
    for kind, xs, ys, text, paint in [
        (
            "authored_reference",
            times,
            positions,
            "Authored reference motion",
            "#245d74",
        ),
        (
            "authored_chord",
            [times[0], times[-1]],
            [positions[0], positions[-1]],
            "Geometric endpoint replay",
            "#b55847",
        ),
    ]:
        artist = axis.plot(xs, ys, label=text, color=paint, linewidth=2.3)[0]
        items.append(
            {
                "artist": artist,
                "id": kind,
                "kind": kind,
                "axis": 0,
                "x": xs,
                "values": ys,
            }
        )
    for view, key, paint, height in [
        ("reference", "zone1000_reference_first_transition_s", "#245d74", 3100),
        ("decoded", "zone1000_decoded_first_transition_s", "#b55847", 2600),
    ]:
        at = example[key]
        artist = axis.plot([at, at], [0, 4000], "--", linewidth=1.1, color=paint)[0]
        axis.text(
            at + 3,
            height,
            ("Reference" if view == "reference" else "Replay") + f" exit: {at} s",
            color=paint,
            fontsize=10,
        )
        items.append(
            {
                "artist": artist,
                "id": "authored_exit_" + view,
                "kind": "authored_exit",
                "view": view,
                "axis": 0,
                "x": [at, at],
                "values": [0, 4000],
            }
        )
    artist = axis.plot([0, 200], [1000, 1000], ":", color="#69747e", linewidth=1)[0]
    items.append(
        {
            "artist": artist,
            "id": "authored_radius",
            "kind": "authored_radius",
            "axis": 0,
            "x": [0, 200],
            "values": [1000, 1000],
        }
    )
    axis.set(
        xlim=(0, 200),
        ylim=(0, 4300),
        xlabel="Elapsed time (s)",
        ylabel="Position along authored route (m)",
    )
    axis.grid(alpha=0.13)
    axis.legend(loc="upper left", fontsize=9, frameon=False)
    figure.suptitle(
        "A correct route line can replay a zone exit 75 seconds early",
        x=0.13,
        y=0.97,
        ha="left",
        fontsize=14,
        weight="bold",
    )
    figure.text(
        0.13,
        0.86,
        "The straight 4,000 m route is preserved; the timing is not. Markers show first sampled exit.",
        fontsize=9,
    )
    figure.text(
        0.13,
        0.055,
        "Authored teaching case, not an observed flight. Closed 1 km zone; original one-second samples.",
        fontsize=9,
    )
    maps.append(finish(figure, [axis], "authored_timing", items))
    (ROOT / "figures/value_map.json").write_bytes(
        (json.dumps(maps, indent=2) + "\n").encode()
    )
    (ROOT / "figures/render_runtime.json").write_bytes(
        (
            json.dumps(
                {
                    "matplotlib": matplotlib.__version__,
                    "source_values_only": "Checked study metrics and separate authored examples; no recorded source coordinates plotted",
                },
                indent=2,
            )
            + "\n"
        ).encode()
    )
    print(
        json.dumps(
            {
                "status": "THREE_FIGURES_RENDERED_PENDING_INDEPENDENT_AND_VISUAL_CHECKS",
                "figures": 3,
                "pdf_svg_png_files": 9,
            }
        )
    )


if __name__ == "__main__":
    main()
