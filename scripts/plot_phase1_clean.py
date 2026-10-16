#!/usr/bin/env python3
"""Plot the 120-episode clean baseline using curated native analysis exports.

Use an isolated interpreter with matplotlib==3.11.2. No evaluation dependencies,
inference requests, outcome changes or new uncertainty estimates are needed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

COUNTS = {"banking": 8, "slack": 16, "workspace": 24, "travel": 12}
REPEATS = ("seed-17", "seed-29")
PAIR_STYLES = (
    ("both_success", "Both successful", "#278474"),
    ("both_failure", "Both unsuccessful", "#b5bcc4"),
    ("false_to_true", "Failure → success", "#397fb0"),
    ("true_to_false", "Success → failure", "#cc773c"),
    ("unknown", "Any unknown", "#eee7db"),
)


def load_inputs(measurements_path: Path, episodes_path: Path):
    measurements = json.loads(measurements_path.read_text())
    summary = measurements["native_summary"]
    if (
        summary["planned_episodes"] != 120
        or summary["finalized_episodes"] != 120
        or summary["missing_episodes"] != 0
        or summary["overall"]["unique_tasks"] != 60
    ):
        raise ValueError("Expected the complete 120-clean / 60-task checkpoint")
    with episodes_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    keys = [(r["suite"], r["user_task_id"], r["repeat_id"]) for r in rows]
    if len(rows) != 120 or len(set(keys)) != 120:
        raise ValueError("Unexpected or duplicate episode keys")
    pairs = {}
    for suite, n in COUNTS.items():
        paired = {}
        for repeat in REPEATS:
            selected = [r for r in rows if r["suite"] == suite and r["repeat_id"] == repeat]
            rate = measurements["by_suite_repeat"][suite][repeat]["rates"][
                "clean_utility_all_attempts"
            ]
            if len(selected) != n or rate["denominator"] != n:
                raise ValueError(f"Unexpected suite/repeat denominator: {suite}/{repeat}")
            if rate["successes"] != sum(r["utility"] == "True" for r in selected):
                raise ValueError("Curated CSV and native success count differ")
            if rate["ci_method"] != "wilson_unique_tasks":
                raise ValueError("Each seed/suite must have one observation per task")
            for row in selected:
                if row["condition"] != "clean" or row["run_id"] != (
                    f"phase1-f16-s{repeat.removeprefix('seed-')}-{suite}-clean"
                ):
                    raise ValueError("Episode is outside the frozen clean cohort")
                if row["status"] == "completed":
                    if row["utility"] not in {"True", "False"}:
                        raise ValueError("Completed utility must be Boolean")
                elif row["status"] != "error" or row["utility"]:
                    raise ValueError("Error utility must remain unknown")
                paired.setdefault(row["user_task_id"], {})[repeat] = row["utility"]
        if len(paired) != n or any(set(p) != set(REPEATS) for p in paired.values()):
            raise ValueError("Incomplete same-task seed pairing")
        counts = Counter()
        labels = {
            ("True", "True"): "both_success",
            ("False", "False"): "both_failure",
            ("False", "True"): "false_to_true",
            ("True", "False"): "true_to_false",
        }
        for pair in paired.values():
            counts[labels.get(tuple(pair[r] for r in REPEATS), "unknown")] += 1
        pairs[suite] = {name: counts[name] for name, _, _ in PAIR_STYLES}
    if set(r["suite"] for r in rows) != set(COUNTS):
        raise ValueError("Unexpected suite membership")
    comparison = summary["repeat_consistency"]["utility_known_comparison"]
    if (
        comparison["repeated_cells"] != 60
        or comparison["known_disagreement_cells"]
        != sum(p["false_to_true"] + p["true_to_false"] for p in pairs.values())
        or comparison["noncomparable_cells"] != sum(p["unknown"] for p in pairs.values())
    ):
        raise ValueError("Pair counts differ from native known-repeat comparisons")
    return measurements, pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--measurements", type=Path, default=Path("results/phase1/clean-baseline-measurements.json")
    )
    parser.add_argument(
        "--episodes", type=Path, default=Path("results/phase1/clean-baseline-episodes.csv")
    )
    parser.add_argument(
        "--output-prefix", type=Path, default=Path("results/phase1/clean-baseline-repeat-overview")
    )
    args = parser.parse_args()
    measurements, pairs = load_inputs(args.measurements, args.episodes)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
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
            "svg.hashsalt": "phase1-clean120-v1",
        }
    )
    fig, (rate_ax, pair_ax) = plt.subplots(1, 2, figsize=(13.8, 6.2))
    fig.subplots_adjust(left=0.13, right=0.98, top=0.74, bottom=0.36, wspace=0.42)
    fig.suptitle(
        "Clean baseline across two seeds",
        x=0.13,
        y=0.96,
        ha="left",
        fontsize=18,
        fontweight="bold",
    )
    fig.text(
        0.13,
        0.89,
        "Qwen3-8B F16 · full 32K context · 60 tasks × 2 seeds = 120 clean episodes",
        color="#555e68",
    )
    rate_ax.set_title("A   Official utility by seed", loc="left", pad=18)
    pair_ax.set_title("B   Paired task outcomes (17 → 29)", loc="left", pad=18)
    for index, suite in enumerate(COUNTS):
        y = 3 - index
        for repeat, offset, marker, color in (
            ("seed-17", 0.13, "o", "#354f7b"),
            ("seed-29", -0.13, "s", "#bd632b"),
        ):
            rate = measurements["by_suite_repeat"][suite][repeat]["rates"][
                "clean_utility_all_attempts"
            ]
            value = rate["rate"]
            low, high = rate["ci95"]
            rate_ax.errorbar(
                value,
                y + offset,
                xerr=[[value - low], [high - value]],
                fmt=marker,
                color=color,
                markersize=6,
                capsize=3,
                elinewidth=1.5,
                label=repeat.replace("seed-", "Seed ") if index == 0 else None,
            )
        left = 0.0
        for name, _, color in PAIR_STYLES:
            count = pairs[suite][name]
            width = count / COUNTS[suite]
            pair_ax.barh(
                y,
                width,
                left=left,
                height=0.62,
                color=color,
                edgecolor="white",
                linewidth=1,
                hatch="///" if name == "unknown" else None,
            )
            if count:
                pair_ax.text(
                    left + width / 2,
                    y,
                    str(count),
                    ha="center",
                    va="center",
                    color="white" if name in {"both_success", "false_to_true"} else "#242b32",
                    fontsize=10,
                )
            left += width
    for ax in (rate_ax, pair_ax):
        ax.set_xlim(0, 1)
        ax.set_ylim(-0.55, 3.55)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.set_yticks([3, 2, 1, 0], [f"{s.title()} (n={n})" for s, n in COUNTS.items()])
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=8)
        ax.set_axisbelow(True)
        ax.grid(axis="x")
    rate_ax.set_xlabel("Successes / all clean attempts at that seed")
    rate_ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.30), ncol=2, frameon=False)
    pair_ax.set_xlabel("Fraction of distinct tasks; labels are task counts")
    pair_ax.legend(
        handles=[
            Patch(facecolor=color, label=label, hatch="///" if name == "unknown" else None)
            for name, label, color in PAIR_STYLES
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.45),
        ncol=2,
        frameon=False,
        fontsize=9,
    )
    fig.text(
        0.13,
        0.105,
        "Whiskers: native descriptive 95% Wilson intervals, one episode per task per seed. "
        "Errors remain in denominators.",
        fontsize=9,
    )
    fig.text(
        0.13,
        0.06,
        "Unknown pairs establish neither agreement nor a known flip. "
        "Fixed tasks and two seeds limit generalization; parent campaign includes 480 attacks.",
        fontsize=9,
        color="#59616b",
    )
    metadata = {
        "scope": "120 clean episodes only; no attack estimate; parent campaign has 600 episodes",
        "measurements_sha256": hashlib.sha256(args.measurements.read_bytes()).hexdigest(),
        "episodes_sha256": hashlib.sha256(args.episodes.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analysis_sha256": measurements["native_summary"]["measurement_provenance"][
            "analysis_sha256"
        ],
        "matplotlib_version": matplotlib.__version__,
        "paired_outcomes": pairs,
        "known_repeat_comparison": measurements["native_summary"]["repeat_consistency"][
            "utility_known_comparison"
        ],
    }
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".svg", ".png"):
        target = args.output_prefix.with_suffix(suffix)
        options = {"metadata": {"Description": json.dumps(metadata, sort_keys=True)}}
        if suffix == ".svg":
            options["metadata"]["Date"] = None
        else:
            options["dpi"] = 200
        fig.savefig(target, **options)
        if suffix == ".svg":
            target.write_text(
                "\n".join(line.rstrip() for line in target.read_text().splitlines()) + "\n"
            )
    plt.close(fig)
    args.output_prefix.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(
        json.dumps({"png": str(args.output_prefix.with_suffix(".png").resolve()), "pairs": pairs})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
