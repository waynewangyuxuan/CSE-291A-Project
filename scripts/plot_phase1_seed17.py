#!/usr/bin/env python3
"""Plot the fixed 60-task seed-17 clean checkpoint without evaluation dependencies.

Run in an isolated interpreter with matplotlib==3.11.2. Inputs are curated CSV;
this script neither contacts the inference service nor changes official scores.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

COUNTS = {"banking": 8, "slack": 16, "workspace": 24, "travel": 12}
COLORS = {"banking": "#286A9F", "slack": "#BD5C2D", "workspace": "#7956A4", "travel": "#248478"}


def wilson(successes: int, total: int) -> tuple[float, float]:
    z = 1.959963984540054
    p = successes / total
    scale = 1 + z * z / total
    center = (p + z * z / (2 * total)) / scale
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total**2)) / scale
    return center - radius, center + radius


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    keys = {(row["suite"], row["user_task_id"]) for row in rows}
    if len(rows) != 60 or len(keys) != 60 or {row["suite"] for row in rows} != set(COUNTS):
        raise ValueError("Expected exactly the 60 distinct tasks in four suites")
    for suite, n in COUNTS.items():
        selected = [r for r in rows if r["suite"] == suite]
        if len(selected) != n or len({r["identity"] for r in selected}) != 1:
            raise ValueError(f"Unexpected count or mixed run identity: {suite}")
        for row in selected:
            if row["run_id"] != f"phase1-f16-s17-{suite}-clean":
                raise ValueError("Unexpected run outside the fixed clean checkpoint")
            if row["repeat_id"] != "seed-17" or row["status"] not in {"completed", "error"}:
                raise ValueError("Unexpected repeat or unfinished episode")
            duration = float(row["duration_seconds"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Episode duration must be positive and finite")
            if row["status"] == "completed" and row["utility"] not in {"True", "False"}:
                raise ValueError("Completed clean utility must be boolean")
            if row["status"] == "error" and row["utility"]:
                raise ValueError("Error utility must remain unknown")
    if sum(r["utility"] == "True" for r in rows) != 26:
        raise ValueError("Official success count differs from this fixed checkpoint")
    errors = [r for r in rows if r["status"] == "error"]
    if len(errors) != 2 or any(
        r["suite"] != "workspace" or r["error_type"] != "APITimeoutError" for r in errors
    ):
        raise ValueError("Expected the two retained workspace budget timeouts")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("results/phase1/seed17-clean-episodes.csv")
    )
    parser.add_argument(
        "--output-prefix", type=Path, default=Path("results/phase1/seed17-clean-overview")
    )
    args = parser.parse_args()
    rows = load_rows(args.input)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#7b8188",
            "grid.color": "#dce0e5",
            "grid.linewidth": 0.6,
            "svg.fonttype": "none",
            "svg.hashsalt": "phase1-seed17-clean-v1",
        }
    )
    fig, (utility_ax, latency_ax) = plt.subplots(
        1, 2, figsize=(12.8, 5.8), gridspec_kw={"width_ratios": [1, 1.4]}
    )
    fig.subplots_adjust(left=0.10, right=0.98, top=0.74, bottom=0.25, wspace=0.40)
    fig.suptitle(
        "Interim clean baseline: 60 tasks, one seed",
        x=0.10,
        y=0.96,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.text(
        0.10,
        0.885,
        "Qwen3-8B F16  ·  full 32K context  ·  seed 17  ·  no attack results",
        color="#555e68",
    )
    utility_ax.set_title("A   Official task utility", loc="left", pad=16)
    latency_ax.set_title("B   All-attempt episode latency", loc="left", pad=16)

    counts = {}
    for index, (suite, n) in enumerate(COUNTS.items()):
        selected = [r for r in rows if r["suite"] == suite]
        successes = sum(r["utility"] == "True" for r in selected)
        unknown = sum(r["status"] == "error" for r in selected)
        low, high = wilson(successes, n)
        rate = successes / n
        y = 3 - index
        utility_ax.errorbar(
            rate,
            y,
            xerr=[[rate - low], [high - rate]],
            fmt="o",
            color=COLORS[suite],
            markersize=7,
            capsize=4,
            elinewidth=1.6,
        )
        label = f"{successes}/{n}" + (f"; {unknown} unknown" if unknown else "")
        utility_ax.text(rate, y + 0.23, label, ha="center", color=COLORS[suite], fontsize=10)
        ordered = sorted(selected, key=lambda r: float(r["duration_seconds"]))
        durations = [float(r["duration_seconds"]) for r in ordered]
        latency_ax.step(
            [0, *durations, 650],
            [0, *[(i + 1) / n for i in range(n)], 1],
            where="post",
            label=f"{suite.title()} (n={n})",
            color=COLORS[suite],
            linewidth=1.8,
        )
        for rank, row in enumerate(ordered, start=1):
            if row["status"] == "error":
                latency_ax.plot(
                    float(row["duration_seconds"]), rank / n, "x", color="#a32424", ms=7, mew=1.8
                )
        counts[suite] = {
            "tasks": n,
            "successes": successes,
            "unknown": unknown,
            "ci95": [low, high],
        }

    utility_ax.set_yticks([3, 2, 1, 0], [s.title() for s in COUNTS])
    utility_ax.set(
        xlim=(0, 1), ylim=(-0.5, 3.65), xlabel="Successes / all finalized clean attempts"
    )
    utility_ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
    utility_ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    utility_ax.grid(axis="x")
    utility_ax.spines["left"].set_visible(False)
    utility_ax.tick_params(axis="y", length=0, pad=9)
    latency_ax.set(
        xlim=(0, 650),
        ylim=(0, 1.05),
        xlabel="Wall-clock episode latency (seconds)",
        ylabel="Cumulative episode fraction",
    )
    latency_ax.set_xticks([0, 100, 200, 300, 400, 500, 600])
    latency_ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
    latency_ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    latency_ax.grid(axis="y")
    latency_ax.axvline(600, color="#9c7777", linestyle=":", linewidth=1)
    latency_ax.annotate(
        "2 budget timeouts\n(scores unknown)",
        xy=(600, 0.96),
        xytext=(365, 0.69),
        color="#8b2828",
        fontsize=9,
        arrowprops={"arrowstyle": "->", "color": "#8b2828"},
    )
    latency_ax.legend(loc="lower right", bbox_to_anchor=(0.88, 0), frameon=False, fontsize=9)
    fig.text(
        0.10,
        0.155,
        "Whiskers: descriptive 95% Wilson intervals; one episode per task. "
        "Denominators include errors.",
        fontsize=9,
    )
    fig.text(
        0.10,
        0.105,
        "Two timeouts retain unknown utility. Latency includes all attempts; "
        "prompt-prefix reuse is disabled.",
        fontsize=9,
        color="#59616b",
    )
    fig.text(
        0.10,
        0.055,
        "Fixed tasks, run order and host activity limit generalization and causal cost claims. "
        "Full campaign: 600 episodes.",
        fontsize=8,
        color="#59616b",
    )
    metadata = {
        "scope": "interim seed17 clean cohort only; full Phase1 incomplete; no attack results",
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "matplotlib_version": matplotlib.__version__,
        "model": "Qwen3-8B F16",
        "context_tokens": 32768,
        "seed": 17,
        "episodes": 60,
        "unique_tasks": 60,
        "successes": 26,
        "unknown": 2,
        "suites": counts,
    }
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".svg", ".png"):
        target = args.output_prefix.with_suffix(suffix)
        kwargs = {"metadata": {"Description": json.dumps(metadata, sort_keys=True)}}
        if suffix == ".svg":
            kwargs["metadata"]["Date"] = None
        else:
            kwargs["dpi"] = 200
        fig.savefig(target, **kwargs)
        if suffix == ".svg":
            # Matplotlib's multiline path attributes contain trailing spaces.
            # Newlines preserve the XML/path separators after normalization.
            target.write_text(
                "\n".join(line.rstrip() for line in target.read_text().splitlines()) + "\n"
            )
    plt.close(fig)
    out = args.output_prefix.with_name("seed17-clean-plot-metadata.json")
    out.write_text(json.dumps(metadata, indent=2) + "\n")
    print(
        json.dumps({"episodes": 60, "png": str(args.output_prefix.with_suffix(".png").resolve())})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
