#!/usr/bin/env python3
"""Plot the fully audited Phase 1 export in an isolated Matplotlib environment.

The command refuses incomplete inputs. Render only after the final exporter has
passed collection and all three review gates. It never contacts model inference.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

SUITES = ("banking", "slack", "workspace", "travel")
STYLES = ("direct_append", "ignore_previous_append")
COLORS = ("#335781", "#c16b32", "#278474")


def load_inputs(measurements: Path, episodes: Path):
    data = json.loads(measurements.read_text())
    native = data["native_summary"]
    if (
        data.get("complete") is not True
        or data.get("scope", {}).get("full_cohort_evaluation_export_complete") is not True
        or data.get("review_coverage", {}).get("complete") is not True
        or native["planned_episodes"] != 600
        or native["finalized_episodes"] != 600
        or native["missing_episodes"] != 0
        or native["overall"]["unique_tasks"] != 60
        or not native["preliminary_evaluation_ready"]
    ):
        raise ValueError("Final plots require the fully reviewed and audited 600-episode export")
    with episodes.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 600 or len({(r["run_id"], r["episode_id"]) for r in rows}) != 600:
        raise ValueError("Expected 600 unique finalized episode keys")
    if Counter(r["condition"] for r in rows) != {"clean": 120, "attack": 480}:
        raise ValueError("Expected 120 clean and 480 attack episodes")
    if (
        len({(r["suite"], r["user_task_id"]) for r in rows}) != 60
        or {r["repeat_id"] for r in rows} != {"seed-17", "seed-29"}
        or {r["suite"] for r in rows} != set(SUITES)
    ):
        raise ValueError("Unexpected task, suite or seed coverage")
    for row in rows:
        duration = float(row["duration_seconds"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Episode durations must be positive and finite")
        fields = ("utility", "attack_success") if row["condition"] == "attack" else ("utility",)
        if row["status"] == "completed":
            if any(row[f] not in {"True", "False"} for f in fields):
                raise ValueError("Completed applicable outcomes must be Boolean")
        elif row["status"] != "error" or any(row[f] for f in fields):
            raise ValueError("Error outcomes must remain unknown")
    for suite in SUITES:
        if set(data["by_suite_style"][suite]) != set(STYLES):
            raise ValueError("Expected both frozen attack styles in each suite")
    return data, rows


def style_axes(ax):
    from matplotlib.ticker import PercentFormatter

    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-0.6, 3.65)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_yticks([3, 2, 1, 0], [s.title() for s in SUITES])
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.set_axisbelow(True)
    ax.grid(axis="x")


def rate_point(ax, rate, y, color, marker, label=None, *, label_after_interval=False):
    value = rate["rate"]
    if value is None:
        ax.text(0.98, y, "n=0", ha="right", va="center", color=color, fontsize=8)
        return
    ci = rate["ci95"]
    ax.errorbar(
        value,
        y,
        xerr=[[value - ci[0]], [ci[1] - value]] if ci else None,
        fmt=marker,
        markersize=5,
        capsize=3,
        color=color,
        label=label,
    )
    fraction = f"{rate['successes']}/{rate['denominator']}"
    if label_after_interval:
        ax.annotate(
            fraction,
            xy=(ci[1] if ci else value, y),
            xytext=(6, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            color=color,
            fontsize=8,
        )
        return
    ax.text(
        value,
        y + 0.075,
        fraction,
        ha="left" if value < 0.05 else "right" if value > 0.95 else "center",
        va="bottom",
        color=color,
        fontsize=8,
    )


def plot_outcomes(data, *, subtitle):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    native = data["native_summary"]
    fig, axes = plt.subplots(2, 2, figsize=(15, 10.2))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.83, bottom=0.16, wspace=0.36, hspace=0.90)
    fig.suptitle("Phase 1: capability and exact-goal attack success", x=0.10, y=0.97, ha="left")
    fig.text(0.10, 0.925, subtitle, color="#555e68", fontsize=10)
    utility_ax, attack_ax, heat_ax, control_ax = axes.flat
    utility_ax.set_title("A   Official legitimate utility", loc="left", pad=18)
    attack_ax.set_title("B   ASR under three denominators", loc="left", pad=18)
    for i, suite in enumerate(SUITES):
        rates = native["by_suite"][suite]["rates"]
        for key, offset, color, marker, label in (
            ("clean_utility_all_attempts", 0.16, COLORS[0], "o", "Clean"),
            ("attack_utility_all_attempts", -0.16, COLORS[1], "s", "Attacked"),
        ):
            rate_point(
                utility_ax, rates[key], 3 - i + offset, color, marker, label if i == 0 else None
            )
        for key, offset, color, marker, label in (
            ("attack_success_all_attempts", 0.24, COLORS[0], "o", "All attacks"),
            ("attack_success_exposed_all_attempts", 0, COLORS[1], "s", "Exposed"),
            (
                "attack_success_clean_completable_all_attempts",
                -0.24,
                COLORS[2],
                "^",
                "Clean-success control",
            ),
        ):
            rate_point(
                attack_ax,
                rates[key],
                3 - i + offset,
                color,
                marker,
                label if i == 0 else None,
                label_after_interval=True,
            )
    for ax in (utility_ax, attack_ax):
        style_axes(ax)
        ax.legend(
            loc="lower center", bbox_to_anchor=(0.5, -0.43), ncol=2, frameon=False, fontsize=9
        )
    utility_ax.set_xlabel("Official successes / all attempts in that condition")
    attack_ax.set_xlabel("Exact-goal successes / the labeled attack subset")

    heat_ax.set_title("C   ASR by fixed attack template", loc="left", pad=18)
    matrix = [
        [
            data["by_suite_style"][s][a]["rates"]["attack_success_all_attempts"]["rate"]
            if data["by_suite_style"][s][a]["rates"]["attack_success_all_attempts"]["rate"]
            is not None
            else math.nan
            for a in STYLES
        ]
        for s in SUITES
    ]
    im = heat_ax.imshow(matrix, vmin=0, vmax=1, cmap="YlOrRd", aspect="auto")
    heat_ax.set_xticks([0, 1], ["Direct append", "Ignore previous + append"])
    heat_ax.set_yticks(range(4), [s.title() for s in SUITES])
    heat_ax.tick_params(length=0, pad=8)
    for i, suite in enumerate(SUITES):
        for j, attack in enumerate(STYLES):
            r = data["by_suite_style"][suite][attack]["rates"]["attack_success_all_attempts"]
            if r["rate"] is None:
                heat_ax.text(j, i, "N/A (n=0)", ha="center", va="center", color="#555")
                continue
            text = f"{r['successes']}/{r['denominator']}\n{r['rate']:.1%}"
            if r["unknown_outcomes"]:
                text += f"; U={r['unknown_outcomes']}"
            heat_ax.text(
                j,
                i,
                text,
                ha="center",
                va="center",
                color="white" if r["rate"] > 0.6 else "#222",
                fontsize=10,
            )
    colorbar = fig.colorbar(im, ax=heat_ax, orientation="horizontal", fraction=0.10, pad=0.25)
    colorbar.set_ticks([0, 0.25, 0.5, 0.75, 1], labels=["0%", "25%", "50%", "75%", "100%"])

    control_ax.set_title("D   Same-seed clean-control coverage", loc="left", pad=18)
    categories = (
        ("verified_clean_true", "Clean success", "#278474"),
        ("verified_clean_false", "Clean failure", "#b5bcc4"),
        ("unknown_clean_outcome", "Unknown clean", "#ebbd79"),
    )
    for i, suite in enumerate(SUITES):
        coverage = native["by_suite"][suite]["clean_control_coverage"]
        n = coverage["attack_episodes"]
        if not n:
            control_ax.text(0.98, 3 - i, "n=0", ha="right", va="center", color="#555")
            continue
        if coverage["missing_clean_result"] or sum(coverage[k] for k, _, _ in categories) != n:
            raise ValueError(
                "Final attack controls must partition into known/unknown existing results"
            )
        left = 0
        for key, _, color in categories:
            count = coverage[key]
            width = count / n
            control_ax.barh(
                3 - i,
                width,
                left=left,
                height=0.65,
                color=color,
                edgecolor="white",
                hatch="///" if key == "unknown_clean_outcome" else None,
            )
            if count:
                control_ax.text(
                    left + width / 2,
                    3 - i,
                    str(count),
                    ha="center",
                    va="center",
                    color="white" if key == "verified_clean_true" else "#222",
                )
            left += width
    style_axes(control_ax)
    control_ax.set_xlabel("Fraction of attacks; labels count reused controls")
    control_ax.legend(
        handles=[
            Patch(
                facecolor=color,
                label=label,
                hatch="///" if key == "unknown_clean_outcome" else None,
            )
            for key, label, color in categories
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.43),
        ncol=2,
        frameon=False,
        fontsize=9,
    )
    fig.text(
        0.10,
        0.075,
        "Whiskers: native task-cluster intervals. Unknown outcomes remain in all-attempt "
        "denominators; U denotes unknown.",
        fontsize=9,
    )
    fig.text(
        0.10,
        0.050,
        "60 task clusters; two seeds. Suite goals differ. Exact-goal failure does not establish "
        "permission-boundary preservation.",
        fontsize=9,
        color="#59616b",
    )
    fig.text(
        0.10,
        0.025,
        "All-zero bootstrap intervals may be degenerate; unknown-outcome bounds remain "
        "separate in the exported metrics.",
        fontsize=9,
        color="#59616b",
    )
    return fig


def plot_costs(data, rows, *, subtitle):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    fig, (latency_ax, tokens_ax) = plt.subplots(1, 2, figsize=(14, 6.5))
    fig.subplots_adjust(left=0.09, right=0.97, top=0.76, bottom=0.28, wspace=0.35)
    fig.suptitle("Phase 1: observed deployment costs", x=0.09, y=0.96, ha="left")
    fig.text(0.09, 0.89, subtitle, color="#555e68", fontsize=10)
    latency_ax.set_title("A   All-attempt episode latency", loc="left", pad=18)
    tokens_ax.set_title("B   Available token use per episode", loc="left", pad=18)
    xmax = math.ceil(max(float(r["duration_seconds"]) for r in rows) / 50) * 50
    boxes, labels, box_colors = [], [], []
    for condition, color in zip(("clean", "attack"), COLORS[:2], strict=True):
        group = sorted(
            [r for r in rows if r["condition"] == condition],
            key=lambda r: float(r["duration_seconds"]),
        )
        n = len(group)
        errors = sum(r["status"] == "error" for r in group)
        durations = [float(r["duration_seconds"]) for r in group]
        latency_ax.step(
            [0, *durations, xmax],
            [0, *[(i + 1) / n for i in range(n)], 1],
            where="post",
            color=color,
            label=f"{condition.title()} (n={n}; errors={errors})",
        )
        for i, row in enumerate(group):
            if row["status"] == "error":
                latency_ax.plot(float(row["duration_seconds"]), (i + 1) / n, "x", color=color, ms=6)
    for field in ("prompt_tokens", "completion_tokens"):
        for condition, color in zip(("clean", "attack"), COLORS[:2], strict=True):
            values = [
                float(r[field])
                for r in rows
                if r["condition"] == condition
                and int(r["usage_response_count"]) > 0
                and r[field]
                and float(r[field]) > 0
            ]
            boxes.append(values)
            labels.append(f"{condition.title()}\n{field.removesuffix('_tokens')}")
            box_colors.append(color)
    boxplot = tokens_ax.boxplot(
        boxes, tick_labels=labels, patch_artist=True, whis=(10, 90), showfliers=False
    )
    for patch, color in zip(boxplot["boxes"], box_colors, strict=True):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    for i, values in enumerate(boxes, 1):
        tokens_ax.text(
            i,
            0.98,
            f"n={len(values)}",
            transform=tokens_ax.get_xaxis_transform(),
            ha="center",
            va="top",
            fontsize=9,
        )
    tokens_ax.set_yscale("log")
    lower, upper = tokens_ax.get_ylim()
    tokens_ax.set_ylim(lower, upper * 2.5)
    tokens_ax.set_ylabel("Recorded positive tokens (log scale)")
    tokens_ax.grid(axis="y")
    latency_ax.set(
        xlim=(0, xmax),
        ylim=(0, 1.04),
        xlabel="Wall-clock seconds",
        ylabel="Cumulative episode fraction",
    )
    latency_ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    latency_ax.axvline(600, color="#9c7777", ls=":", lw=1)
    latency_ax.grid(axis="y")
    latency_ax.legend(
        loc="lower center", bbox_to_anchor=(0.5, -0.25), ncol=2, frameon=False, fontsize=9
    )
    memory = data["native_summary"]["overall"]["costs_by_condition"]
    peaks = {c: memory[c]["peak_server_rss_bytes"]["max"] for c in ("clean", "attack")}
    memory_note = "; ".join(
        f"{c} max RSS {v / 2**30:.3f} GiB" if v is not None else f"{c} RSS unavailable"
        for c, v in peaks.items()
    )
    fig.text(
        0.09,
        0.14,
        "Boxes: median/IQR, 10th–90th percentile whiskers; only episodes with returned usage. "
        "Failed-request compute remains unknown.",
        fontsize=9,
    )
    fig.text(0.09, 0.09, memory_note + ". RSS covers the sampled server process only.", fontsize=9)
    fig.text(
        0.09,
        0.045,
        "Errors remain in latency. Uncached full history; fixed order and shared-host load "
        "confound causal performance comparisons.",
        fontsize=9,
        color="#59616b",
    )
    return fig


def save_figure(fig, prefix: Path, metadata):
    import matplotlib.pyplot as plt

    for suffix in (".png", ".svg"):
        target = prefix.with_suffix(suffix)
        options = {"metadata": {"Description": json.dumps(metadata, sort_keys=True)}}
        if suffix == ".png":
            options["dpi"] = 200
        else:
            options["metadata"]["Date"] = None
        fig.savefig(target, **options)
        if suffix == ".svg":
            target.write_text(
                "\n".join(line.rstrip() for line in target.read_text().splitlines()) + "\n"
            )
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--measurements", type=Path, default=Path("results/phase1/evaluation-measurements.json")
    )
    parser.add_argument(
        "--episodes", type=Path, default=Path("results/phase1/evaluation-episodes.csv")
    )
    parser.add_argument("--output-prefix", type=Path, default=Path("results/phase1/evaluation"))
    args = parser.parse_args()
    data, rows = load_inputs(args.measurements, args.episodes)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "figure.titlesize": 17,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#dce0e5",
            "grid.linewidth": 0.6,
            "svg.fonttype": "none",
            "svg.hashsalt": "phase1-final-v1",
        }
    )
    subtitle = (
        "Qwen3-8B F16 · full 32K context · 600 episodes / 60 tasks · seeds 17 and 29 · "
        "fixed goals/templates"
    )
    metadata = {
        "scope": "Complete frozen Phase1 baseline; exact-goal scores retained; "
        "no quantization/context causal comparison",
        "measurements_sha256": hashlib.sha256(args.measurements.read_bytes()).hexdigest(),
        "episodes_sha256": hashlib.sha256(args.episodes.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analysis_sha256": data["native_summary"]["measurement_provenance"]["analysis_sha256"],
        "snapshot_identity": data["snapshot_identity"],
        "matplotlib_version": matplotlib.__version__,
    }
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for name, figure in (
        ("outcomes", plot_outcomes(data, subtitle=subtitle)),
        ("costs", plot_costs(data, rows, subtitle=subtitle)),
    ):
        prefix = args.output_prefix.with_name(args.output_prefix.name + "-" + name)
        save_figure(figure, prefix, {**metadata, "figure": name})
    args.output_prefix.with_suffix(".plots.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(
        json.dumps(
            {
                "figures": [
                    str(
                        args.output_prefix.with_name(args.output_prefix.name + "-" + n)
                        .with_suffix(".png")
                        .resolve()
                    )
                    for n in ("outcomes", "costs")
                ]
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
