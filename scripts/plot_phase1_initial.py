#!/usr/bin/env python3
"""Plot the frozen first 24 clean episodes from curated files, without runtime access.

Requires Matplotlib in the plotting interpreter; no evaluation dependencies change.
Example: python scripts/plot_phase1_initial.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median

EXPECTED = {
    "phase1-f16-s17-banking-clean": ("banking", 8),
    "phase1-f16-s17-slack-clean": ("slack", 16),
}
COLORS = {"banking": "#286A9F", "slack": "#BD5C2D"}


def load_curated(measurements_path: Path, latencies_path: Path):
    measurements = json.loads(measurements_path.read_text())
    with latencies_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    runs = measurements["runs"]
    if {run["run_id"] for run in runs} != EXPECTED.keys() or len(runs) != 2:
        raise ValueError("This figure is frozen to the first two seed-17 clean runs")
    if len(rows) != 24 or len({(row["run_id"], row["episode_id"]) for row in rows}) != 24:
        raise ValueError("Expected exactly 24 distinct curated episode latencies")
    if set(row["run_id"] for row in rows) != EXPECTED.keys():
        raise ValueError("Latency rows include an unexpected run")
    series = []
    for run in runs:
        suite, expected = EXPECTED[run["run_id"]]
        selected = [row for row in rows if row["run_id"] == run["run_id"]]
        if len(selected) != expected or any(
            row["identity"] != run["identity"] or row["suite"] != suite for row in selected
        ):
            raise ValueError(f"Curated latency identity/count mismatch for {suite}")
        durations = sorted(float(row["duration_seconds"]) for row in selected)
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("Latencies must be positive finite measurements")
        summary = run["summary"]
        if not all(
            math.isclose(actual, summary["latency_seconds"][key], rel_tol=1e-12)
            for key, actual in (
                ("mean", mean(durations)),
                ("median", median(durations)),
                ("total", sum(durations)),
            )
        ):
            raise ValueError(f"Latency CSV differs from curated summary for {suite}")
        utility = run["analysis_crosscheck"]["clean_utility"]
        if utility["denominator"] != expected or utility["unique_tasks"] != expected:
            raise ValueError("Descriptive Wilson intervals require one episode per task here")
        if utility["ci_method"] != "wilson_unique_tasks" or len(utility["ci95"]) != 2:
            raise ValueError("Expected the actual analyzer's Wilson interval")
        if utility["rate"] != summary["clean_utility_all_attempts"]["rate"]:
            raise ValueError("Utility differs between curated summary and analysis")
        if not utility["ci95"][0] <= utility["rate"] <= utility["ci95"][1]:
            raise ValueError("Utility estimate lies outside its interval")
        series.append((suite, durations, utility))
    return sorted(series, key=lambda item: item[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--measurements", type=Path, default=Path("results/phase1/initial-clean-measurements.json")
    )
    parser.add_argument(
        "--latencies", type=Path, default=Path("results/phase1/initial-latencies.csv")
    )
    parser.add_argument(
        "--output-prefix", type=Path, default=Path("results/phase1/initial-clean-overview")
    )
    args = parser.parse_args()
    series = load_curated(args.measurements, args.latencies)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelsize": 10,
            "axes.titlesize": 12,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#7b8188",
            "axes.linewidth": 0.8,
            "xtick.color": "#353b43",
            "ytick.color": "#353b43",
            "grid.color": "#dce0e5",
            "grid.linewidth": 0.6,
            "svg.fonttype": "none",
            "svg.hashsalt": "phase1-initial-clean-v1",
        }
    )
    fig, (utility_ax, latency_ax) = plt.subplots(
        1, 2, figsize=(10.2, 5.0), gridspec_kw={"width_ratios": [1, 1.25]}
    )
    fig.subplots_adjust(left=0.13, right=0.98, top=0.73, bottom=0.29, wspace=0.40)
    fig.suptitle(
        "Initial clean baseline: 24 completed episodes",
        x=0.13,
        y=0.96,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    fig.text(
        0.13,
        0.885,
        "Qwen3-8B F16 weights  ·  full 32K context  ·  seed 17 only",
        fontsize=10,
        color="#555e68",
    )

    utility_ax.set_title("A   Legitimate task completion", loc="left", pad=13)
    for position, (suite, _durations, estimate) in enumerate(series):
        y = 1 - position
        rate, (low, high) = estimate["rate"], estimate["ci95"]
        utility_ax.errorbar(
            rate,
            y,
            xerr=[[rate - low], [high - rate]],
            fmt="o",
            color=COLORS[suite],
            markersize=7,
            capsize=4,
            elinewidth=1.6,
            capthick=1.2,
            zorder=3,
        )
        utility_ax.text(
            rate,
            y + 0.20,
            f"{estimate['successes']} / {estimate['denominator']}",
            ha="center",
            color=COLORS[suite],
            fontsize=10,
        )
    utility_ax.set_yticks([1, 0], ["Banking", "Slack"])
    utility_ax.set_ylim(-0.55, 1.55)
    utility_ax.set_xlim(0, 1)
    utility_ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
    utility_ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    utility_ax.set_xlabel("Official utility score")
    utility_ax.grid(axis="x", zorder=0)
    utility_ax.spines["left"].set_visible(False)
    utility_ax.tick_params(axis="y", length=0, pad=9)

    latency_ax.set_title("B   Episode latency distribution", loc="left", pad=13)
    maximum = math.ceil(max(max(durations) for _, durations, _ in series) / 15) * 15
    for suite, durations, _estimate in series:
        n = len(durations)
        latency_ax.step(
            [0, *durations, maximum],
            [0, *[(i + 1) / n for i in range(n)], 1],
            where="post",
            label=f"{suite.title()} (n = {n})",
            color=COLORS[suite],
            linewidth=1.8,
        )
    latency_ax.set_xlim(0, maximum)
    latency_ax.set_ylim(0, 1.04)
    latency_ax.set_xticks(range(0, maximum + 1, 15))
    latency_ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
    latency_ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    latency_ax.set_xlabel("Wall-clock episode latency (seconds)")
    latency_ax.set_ylabel("Cumulative episode fraction")
    latency_ax.grid(axis="y", zorder=0)
    latency_ax.legend(loc="lower right", frameon=False, fontsize=9)

    fig.text(
        0.13,
        0.165,
        "Partial cohort: banking (8) and Slack (16); later runs and all attacks excluded.",
        fontsize=9,
        color="#353b43",
    )
    fig.text(
        0.13,
        0.115,
        "95% Wilson intervals are descriptive for this fixed task set. "
        "Latency includes model calls, tools and client overhead.",
        fontsize=8,
        color="#59616b",
    )
    fig.text(
        0.13,
        0.070,
        "Suites differ in tasks; these observations do not establish causal performance effects.",
        fontsize=8,
        color="#59616b",
    )
    provenance = json.dumps(
        {
            "scope": "Frozen first 24 clean episodes, banking/slack seed17; no attack inference",
            "measurements_sha256": hashlib.sha256(args.measurements.read_bytes()).hexdigest(),
            "latencies_sha256": hashlib.sha256(args.latencies.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "matplotlib_version": matplotlib.__version__,
        },
        sort_keys=True,
    )
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    svg = args.output_prefix.with_suffix(".svg")
    png = args.output_prefix.with_suffix(".png")
    fig.savefig(
        svg,
        metadata={
            "Title": "Initial clean baseline: 24 episodes",
            "Description": provenance,
            "Date": None,
        },
    )
    fig.savefig(
        png,
        dpi=200,
        metadata={"Description": provenance, "Software": f"Matplotlib {matplotlib.__version__}"},
    )
    plt.close(fig)
    print(
        json.dumps(
            {
                "episodes": sum(len(durations) for _, durations, _ in series),
                "counts": {suite: len(durations) for suite, durations, _ in series},
                "matplotlib_version": matplotlib.__version__,
                "svg": str(svg.resolve()),
                "png": str(png.resolve()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
