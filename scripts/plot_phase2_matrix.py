"""Render cohort-separated formal figures with visible scope and unknown outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

matplotlib.rcParams["svg.hashsalt"] = "phase2-main-v1"

ROOT = Path("results/phase2-main-v1/analysis")
POLICIES = ("full", "truncate", "summary")
PRECISIONS = ("f16", "q8", "q4")


def save(fig, path):
    for extension in ("png", "svg", "pdf"):
        metadata = {"Date": None} if extension == "svg" else {}
        if extension == "pdf":
            metadata = {"CreationDate": None, "ModDate": None}
        destination = path.with_suffix("." + extension)
        fig.savefig(destination, dpi=180, metadata=metadata)
        if extension == "svg":
            destination.write_text(
                "\n".join(line.rstrip() for line in destination.read_text().splitlines()) + "\n"
            )
    plt.close(fig)


def figures(report, cohort, data):
    output = ROOT / "figures" / cohort
    output.mkdir(parents=True, exist_ok=True)
    configs = data["configurations"]
    labels = [f"{c['precision'].upper()} / {c['policy'].title()}" for c in configs]
    title = (
        f"{cohort.title()} cohort · {data['tasks']} tasks · {report['status'].replace('_', ' ')}"
    )
    fig, axes = plt.subplots(1, 4, figsize=(15, 6), sharey=True, layout="constrained")
    for ax, key, name in zip(
        axes,
        ("clean_utility", "attack_success_all", "attack_success_own_clean", "unnecessary_refusal"),
        (
            "Legitimate clean success",
            "Attack success: all",
            "Attack success: own-clean",
            "Unnecessary refusal",
        ),
        strict=True,
    ):
        for i, config in enumerate(configs):
            value = config[key]
            if value["n"]:
                lower, upper = value["lower_bound"], value["upper_bound"]
                ax.barh(i, lower, color="#377c9c", height=0.66)
                ax.barh(i, upper - lower, left=lower, color="#d8dde3", hatch="///", height=0.66)
                ax.text(
                    1.03,
                    i,
                    f"{value['success']}/{value['n']}  ?{value['unknown']}",
                    va="center",
                    fontsize=8,
                )
            else:
                ax.text(0.02, i, "No eligible cases", va="center", fontsize=8)
        ax.set_xlim(0, 1.55)
        ax.set_xticks([0, 0.5, 1], ["0", "50%", "100%"])
        ax.set_title(name, fontsize=10)
        ax.grid(axis="x", alpha=0.2)
    axes[0].set_yticks(range(len(configs)), labels)
    axes[0].invert_yaxis()
    fig.suptitle("RQ1: capability and security — " + title, fontsize=13)
    fig.legend(
        handles=[
            Patch(color="#377c9c", label="Observed true outcome"),
            Patch(facecolor="#d8dde3", hatch="///", label="Unknown-outcome range"),
        ],
        loc="outside lower center",
        ncol=2,
    )
    save(fig, output / "01-outcomes")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True, layout="constrained")
    contrasts = [(policy, precision) for policy in POLICIES for precision in ("q8", "q4")]
    for ax, key, name in zip(
        axes,
        ("clean_utility", "attack_success_common_clean"),
        ("Clean success: quantized − F16", "ASR: quantized − F16 on pairwise common-clean"),
        strict=True,
    ):
        for i, (policy, precision) in enumerate(contrasts):
            comparison = next(
                c
                for c in data["paired_comparisons"]
                if c["a"] == ["f16", policy] and c["b"] == [precision, policy]
            )[key]
            bounds = comparison["delta_unknown_bounds"]
            if bounds and comparison["unknown_pairs"]:
                ax.hlines(i, *bounds, color="#7b8796", linestyle="--", linewidth=2)
            value = comparison["delta_known"]
            if value is not None:
                interval = comparison["uncertainty"]["interval_95"]
                if interval:
                    ax.hlines(i, *interval, color="#226e93", linewidth=3)
                ax.plot(value, i, "o", color="#226e93")
            ax.text(
                1.05,
                i,
                f"{comparison['false_to_true']}↑ {comparison['true_to_false']}↓  "
                f"n={comparison['known_pairs']}/{comparison['n']}",
                va="center",
                fontsize=8,
            )
        ax.axvline(0, color="#a6adb5", linewidth=1)
        ax.set_xlim(-1.05, 1.75)
        ax.set_xticks([-1, -0.5, 0, 0.5, 1])
        ax.set_xlabel("Paired rate difference")
        ax.set_title(name, fontsize=10)
        ax.grid(axis="x", alpha=0.15)
    axes[0].set_yticks(range(len(contrasts)), [f"{p.upper()} / {c.title()}" for c, p in contrasts])
    axes[0].invert_yaxis()
    fig.suptitle("RQ1: paired precision effects — " + title, fontsize=12)
    fig.legend(
        handles=[
            Line2D(
                [0], [0], color="#226e93", marker="o", label="Known-pair delta / 95% task bootstrap"
            ),
            Line2D(
                [0], [0], color="#7b8796", linestyle="--", label="All-pair unknown-outcome bounds"
            ),
        ],
        loc="outside lower center",
        ncol=2,
        fontsize=9,
        title="↑/↓: outcome flips. Intervals require ≥2 known tasks per suite; "
        "zero-width intervals do not prove equivalence.",
        title_fontsize=8,
    )
    save(fig, output / "05-paired-precision")

    lookup = {(c["precision"], c["policy"]): c for c in configs}
    metrics = (
        ("clean_compacted", "Clean episodes compacted", "clean_utility"),
        ("attack_compacted", "Attacked episodes compacted", "attack_success_all"),
        ("attacked_literal_exposed", "Attacks present in sent requests", "attack_success_all"),
        (
            "attacked_removal_after_exposure",
            "Literal removal after prior exposure",
            "attack_success_all",
        ),
    )
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), layout="constrained")
    for ax, (key, name, denominator) in zip(axes.flat, metrics, strict=True):
        values = np.zeros((3, 3))
        for i, policy in enumerate(POLICIES):
            for j, precision in enumerate(PRECISIONS):
                c = lookup[precision, policy]
                count, n = c["context"][key], c[denominator]["n"]
                values[i, j] = count / n if n else np.nan
                ax.text(
                    j,
                    i,
                    f"{count}/{n}",
                    ha="center",
                    va="center",
                    color="white" if n and count / n > 0.55 else "#172335",
                )
        im = ax.imshow(values, vmin=0, vmax=1, cmap="Blues")
        ax.set_xticks(range(3), [p.upper() for p in PRECISIONS])
        ax.set_yticks(range(3), [p.title() for p in POLICIES])
        ax.set_title(name, fontsize=10)
    fig.colorbar(im, ax=axes, shrink=0.75, label="Fraction of condition-specific episodes")
    fig.suptitle("RQ2: observed context mechanisms — " + title, fontsize=12)
    fig.supxlabel("Literal absence does not establish semantic removal or resistance.", fontsize=9)
    save(fig, output / "02-context")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    markers = {"full": "o", "truncate": "s", "summary": "^"}
    plotted = None
    for ax, metric, xlabel, scale in zip(
        axes,
        ("seconds", "run_aggregate_peak_rss_bytes"),
        (
            "Median clean latency on common-clean tasks (s)",
            "Median paired-run peak aggregate RSS (GiB)",
        ),
        (1, 2**30),
        strict=True,
    ):
        axis_has_points = False
        for i, c in enumerate(configs):
            x = c["common_clean_costs"][metric]["median"]
            security = c["attack_success_common_clean"]
            coverage = c["common_clean_cost_coverage"]
            if (
                x is None
                or not security["n"]
                or security["unknown"]
                or coverage["complete"] != security["n"]
            ):
                continue
            y = c["clean_utility"]["lower_bound"]
            axis_has_points = True
            ax.vlines(x / scale, y, c["clean_utility"]["upper_bound"], color="#25334a", linewidth=1)
            plotted = ax.scatter(
                x / scale,
                y,
                c=[security["lower_bound"]],
                vmin=0,
                vmax=1,
                cmap="YlOrRd",
                marker=markers[c["policy"]],
                s=80,
                edgecolors="#25334a",
            )
            ax.annotate(
                str(i + 1),
                (x / scale, y),
                xytext=(4, 8 + 11 * (i % 3)),
                textcoords="offset points",
                fontsize=8,
            )
        ax.text(
            0.03,
            0.97,
            "\n".join(
                "   ·   ".join(f"{i + 1} {labels[i]}" for i in range(start, start + 3))
                for start in (0, 3, 6)
            ),
            transform=ax.transAxes,
            va="top",
            fontsize=7,
        )
        ax.margins(x=0.1)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_ylim(-0.05, 1.24)
        ax.set_yticks([0, 0.5, 1], ["0", "50%", "100%"])
        ax.set_ylabel("Official clean success over all tasks")
        ax.grid(alpha=0.18)
        if not axis_has_points:
            ax.text(
                0.5,
                0.5,
                "No complete matched costs and known attack outcomes",
                transform=ax.transAxes,
                ha="center",
                wrap=True,
            )
    if plotted is not None:
        fig.colorbar(
            plotted,
            ax=axes,
            shrink=0.8,
            label="ASR on the same all-configuration common-clean tasks",
        )
    fig.suptitle("RQ3: observed cost/capability/security — " + title, fontsize=12)
    fig.supxlabel(
        "Vertical lines: unknown utility range. Checker sensitivity is separate. "
        "RSS is not unique physical RAM.",
        fontsize=8,
    )
    save(fig, output / "03-tradeoffs")

    fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
    pairs = [(p, c) for p in ("q8", "q4") for c in ("truncate", "summary")]
    outcomes = ("clean_utility", "attack_success_common_clean")
    values = np.full((4, 2), np.nan)
    for row in data["interactions"]:
        i, j = pairs.index((row["precision"], row["policy"])), outcomes.index(row["outcome"])
        value = row["difference_in_differences"]
        if value is not None:
            values[i, j] = value
        ax.text(
            j,
            i,
            f"{value:+.2f}\nn={row['known_tasks']}/{row['n']}"
            if value is not None
            else f"Unknown\nn=0/{row['n']}",
            ha="center",
            va="center",
            fontsize=9,
        )
    im = ax.imshow(values, vmin=-2, vmax=2, cmap="RdBu_r", aspect="auto")
    ax.set_xticks(range(2), ["Clean utility", "Attack success\non common-clean"])
    ax.set_yticks(range(4), [f"{p.upper()} × {c.title()}" for p, c in pairs])
    fig.colorbar(im, ax=ax, label="Difference in differences (rate units)", shrink=0.8)
    ax.set_title("Factor interaction relative to F16 / Full\n" + title, fontsize=11)
    fig.supxlabel(
        "(Quantized policy − quantized Full) − (F16 policy − F16 Full); known paired tasks only.",
        fontsize=8,
    )
    save(fig, output / "04-interactions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provisional", action="store_true")
    args = parser.parse_args()
    report = json.loads((ROOT / "measurements.json").read_text())
    if report["status"] != "final_reviewed" and not args.provisional:
        raise ValueError("Figures require final review or explicit --provisional labeling")
    for cohort, data in report["cohorts"].items():
        figures(report, cohort, data)
    paths = [
        ROOT / "measurements.json",
        Path(__file__).relative_to(Path.cwd()),
        *(p for p in sorted((ROOT / "figures").glob("*/*")) if p.is_file()),
    ]
    (ROOT / "figures" / "provenance.json").write_text(
        json.dumps(
            {
                "status": report["status"],
                "sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "cohorts": list(report["cohorts"]),
                "output": str(ROOT / "figures"),
            }
        )
    )
