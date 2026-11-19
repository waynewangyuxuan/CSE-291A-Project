"""Plot reviewed repeat outcomes with task-level coverage and explicit unknown bounds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

PRECISIONS = ("f16", "q8", "q4")
COLORS = {"f16": "#2364AA", "q8": "#2A9D8F", "q4": "#D97732"}


def save(figure, output, name):
    for suffix in ("png", "pdf", "svg"):
        figure.savefig(
            output / f"{name}.{suffix}",
            dpi=180,
            bbox_inches="tight",
            metadata={"Creator": "Phase2 reviewed repeat analysis"} if suffix == "pdf" else None,
        )
    plt.close(figure)


def plot_stability(report, output):
    metrics = [
        ("clean", "semantic_success", "Clean task outcome"),
        ("attack", "reviewed_attack_success", "Exact attack-goal outcome"),
        ("attack", "action_sequence_sha256", "Tool-action sequence"),
    ]
    tasks = sorted({(r["suite"], r["task"]) for r in report["stability"]})
    fig, axes = plt.subplots(1, 3, figsize=(12, 8), sharey=True, layout="constrained")
    for ax, (condition, metric, title) in zip(axes, metrics, strict=True):
        index = {
            (r["suite"], r["task"], r["precision"]): r
            for r in report["stability"]
            if r["condition"] == condition and r["metric"] == metric
        }
        data = np.array(
            [
                [
                    index[s, t, p]["rate"] if index[s, t, p]["rate"] is not None else np.nan
                    for p in PRECISIONS
                ]
                for s, t in tasks
            ]
        )
        cmap = plt.colormaps["YlOrRd"].copy()
        cmap.set_bad("#E4E4E4")
        im = ax.imshow(data, vmin=0, vmax=1, cmap=cmap, aspect="auto")
        ax.set_xticks(range(3), [p.upper() for p in PRECISIONS])
        ax.set_title(title)
        ax.set_yticks(
            range(len(tasks)), [s.title() + " " + t.replace("user_task_", "") for s, t in tasks]
        )
        for i in range(len(tasks)):
            for j in range(3):
                v = data[i, j]
                ax.text(
                    j,
                    i,
                    "?" if np.isnan(v) else f"{v:.2f}",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if not np.isnan(v) and v > 0.65 else "black",
                )
    fig.colorbar(im, ax=axes, shrink=0.7, label="Disagreeing unordered run pairs / observed pairs")
    fig.suptitle("Six fresh repeats: outcome and action variation", fontsize=15)
    fig.supxlabel(
        "Gray = unknown; action variation can be legitimate. "
        "Fixed tasks, not a seed-only causal estimate.",
        fontsize=10,
    )
    save(fig, output, "repeat-stability")


def plot_risk(report, output):
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), layout="constrained", sharex=True, sharey=True)
    for i, population in enumerate(("all_enrolled", "same_seed_semantic_clean")):
        for j, metric in enumerate(("reviewed_exact_goal", "unauthorized_action")):
            ax = axes[i, j]
            for precision in PRECISIONS:
                values = sorted(
                    [
                        r
                        for r in report["macro_risks"]
                        if r["population"] == population
                        and r["metric"] == metric
                        and r["precision"] == precision
                    ],
                    key=lambda r: r["k"],
                )
                x = [r["k"] for r in values]
                lo = [np.nan if r["lower"] is None else r["lower"] for r in values]
                hi = [np.nan if r["upper"] is None else r["upper"] for r in values]
                coverage = "/".join(str(r["tasks_included"]) for r in values)
                ax.plot(
                    x,
                    lo,
                    "o-",
                    color=COLORS[precision],
                    label=f"{precision.upper()} (tasks {coverage})",
                )
                ax.fill_between(x, lo, hi, color=COLORS[precision], alpha=0.15)
                ax.plot(x, hi, ":", color=COLORS[precision], linewidth=0.8)
            ax.set_title(
                ("All enrolled tasks" if i == 0 else "Same-seed reviewed clean success")
                + "\n"
                + ("Exact malicious goal" if j == 0 else "Any witnessed unauthorized action")
            )
            ax.set_ylim(-0.02, 1.02)
            ax.set_xticks([1, 2, 3])
            ax.grid(axis="y", alpha=0.2)
            ax.legend(fontsize=8, loc="lower right")
            ax.set_xlabel("Attempts k")
            ax.set_ylabel("Equal-task macro probability")
    fig.suptitle("Attack@k under a fixed attack: six fresh repeats per task", fontsize=14)
    fig.supxlabel(
        "Shading = unresolved-outcome bounds, not confidence intervals. "
        "Legend counts correspond to k=1/2/3.",
        fontsize=9,
    )
    save(fig, output, "attack-at-k")


def plot_tools(report, output):
    selected = {
        r["precision"]: r
        for r in report["tool_statistics"]
        if r["cohort"] == "common_stable_across_all_precisions" and r["condition"] == "clean"
    }
    fig, axes = plt.subplots(2, 3, figsize=(12, 7), layout="constrained")
    n = selected["f16"]["tasks"]
    x = np.arange(3)
    colors = [COLORS[p] for p in PRECISIONS]
    for ax, name, field in [
        (axes[0, 0], "Executed tool calls", "executed_tool_calls"),
        (axes[0, 1], "Tool exceptions / failures", "tool_errors"),
        (axes[0, 2], "Witnessed redundant calls", "redundant_calls"),
    ]:
        values = [
            selected[p]["redundant_calls_observed"]
            if field == "redundant_calls"
            else selected[p]["execution"][field]["total"]
            for p in PRECISIONS
        ]
        ax.bar(x, values, color=colors)
        ax.set_title(name)
        for j, v in enumerate(values):
            ax.text(j, v, str(v), ha="center", va="bottom")
    for ax, name, field, divisor in [
        (axes[1, 0], "Episode latency (seconds)", "duration_seconds", 1),
        (axes[1, 2], "Agent process peak RSS (GiB)", "agent_peak_rss_bytes", 2**30),
    ]:
        med = [selected[p][field]["median"] for p in PRECISIONS]
        if all(v is not None for v in med):
            values = np.array(med) / divisor
            lo = [
                (selected[p][field]["median"] - selected[p][field]["p25"]) / divisor
                for p in PRECISIONS
            ]
            hi = [
                (selected[p][field]["p75"] - selected[p][field]["median"]) / divisor
                for p in PRECISIONS
            ]
            ax.bar(x, values, color=colors, yerr=[lo, hi], capsize=4)
        ax.set_title(name + "\nmedian and interquartile range")
    ax = axes[1, 1]
    prompt = [selected[p]["native_prompt_seconds"]["total"] for p in PRECISIONS]
    decode = [selected[p]["native_decode_seconds"]["total"] for p in PRECISIONS]
    ax.bar(x, prompt, color=colors, label="Prompt processing")
    ax.bar(x, decode, bottom=prompt, color=colors, hatch="///", alpha=0.5, label="Decoding")
    ax.set_title("Total native inference time (seconds)")
    ax.legend(fontsize=8)
    for ax in axes.flat:
        ax.set_xticks(x, [p.upper() for p in PRECISIONS])
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"Common stable clean cohort: {n} tasks × six runs per precision", fontsize=14)
    fig.supxlabel(
        "Posthoc cohort; descriptive costs. RSS excludes resident summarizer. "
        "Redundancy is separately reviewed.",
        fontsize=9,
    )
    save(fig, output, "tools-and-costs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis",
        type=Path,
        default=Path("results/phase2-followup-v1/reviewed-analysis/analysis.json"),
    )
    args = parser.parse_args()
    report = json.loads(args.analysis.read_text())
    if (
        report.get("status") != "full_agent_trace_review_complete"
        or sum(g["episodes"] for g in report["gates"]) != report["episodes"]
    ):
        raise ValueError("Figures require the complete reviewed export")
    output = args.analysis.parent / "figures"
    output.mkdir(exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.hashsalt": "phase2-reviewed-repeats",
            "pdf.compression": 9,
        }
    )
    plot_stability(report, output)
    plot_risk(report, output)
    plot_tools(report, output)
    evidence = {
        "source_sha256": hashlib.sha256(args.analysis.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "figures": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.iterdir())
            if p.suffix in {".png", ".pdf", ".svg"}
        },
    }
    (output / "verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {"figures": len(evidence["figures"]), "status": "rendered_requires_visual_inspection"}
        )
    )


if __name__ == "__main__":
    main()
