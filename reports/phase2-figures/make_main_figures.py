"""Compact report figures/tables from the immutable, reviewed 972-episode main export."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SOURCE = ROOT / "results/phase2-main-v1/checkpoints/main-054-tasks"
PINNED = {
    "episodes.json": "da7ec56415e1ea9f2c1c4c50388c2a58bfc524753ec26fe94f7a5905106eec0f",
    "measurements.json": "2d107031a7d4ba48ca801541cfa24f04bff54175eb7613d429508ecf0697ace4",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(metric):
    return f"{metric['success']}/{metric['n']} [{metric['unknown']}]"


def main():
    for name, expected in PINNED.items():
        if sha(SOURCE / name) != expected:
            raise ValueError(f"Frozen main snapshot changed: {name}")
    report = json.loads((SOURCE / "measurements.json").read_text())
    rows = json.loads((SOURCE / "episodes.json").read_text())
    assert report["included_episodes"] == report["reviewed_episodes"] == len(rows) == 972
    assert set(report["cohorts"]) == {"main"}
    data = report["cohorts"]["main"]
    sys.path.insert(0, str(ROOT / "scripts"))
    plotter = importlib.import_module("plot_phase2_matrix")
    scope = plotter.plot_scope(report, rows)["main"]
    configs = data["configurations"]
    labels = [f"{c['precision'].upper()} {c['policy'][0].upper()}" for c in configs]
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.titlesize": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "legend.frameon": False,
            "svg.hashsalt": "phase2-main54-report",
        }
    )
    produced = []

    def save(fig, name):
        for extension in ("pdf", "png"):
            path = OUT / f"{name}.{extension}"
            metadata = {"CreationDate": None, "ModDate": None} if extension == "pdf" else {}
            fig.savefig(path, dpi=220, bbox_inches="tight", metadata=metadata)
            produced.append(path)
        plt.close(fig)

    def table(name, lines):
        path = OUT / name
        path.write_text("\n".join(lines) + "\n")
        produced.append(path)

    table(
        "main-outcomes.tex",
        [
            " & ".join(
                [
                    label,
                    *(
                        count(c[k])
                        for k in (
                            "clean_utility",
                            "attacked_utility",
                            "attack_success_all",
                            "attack_success_own_clean",
                            "attack_success_common_clean",
                        )
                    ),
                ]
            )
            + r" \\"
            for label, c in zip(labels, configs, strict=True)
        ],
    )
    cost_lines = []
    for label, c in zip(labels, configs, strict=True):
        a = c["all_outcome_costs"]
        numbers = [
            f"{a['seconds']['median']:.1f}/{a['seconds']['p90']:.1f}",
            *(
                f"{a[k]['total'] / 1000:.1f}"
                for k in (
                    "agent_prompt_tokens",
                    "agent_completion_tokens",
                    "summary_prompt_tokens",
                    "summary_completion_tokens",
                )
            ),
            f"{a['summary_seconds']['total']:.1f}",
            f"{a['run_aggregate_peak_rss_bytes']['median'] / 2**30:.2f}",
        ]
        cost_lines.append(" & ".join([label, *numbers]) + r" \\")
    table("main-costs.tex", cost_lines)
    table(
        "main-sensitivity.tex",
        [
            " & ".join(
                [
                    label,
                    count(c["reviewed_clean_eligibility_sensitivity"]["own_clean_ASR"]),
                    count(c["reviewed_clean_eligibility_sensitivity"]["common_clean_ASR"]),
                ]
            )
            + r" \\"
            for label, c in zip(labels, configs, strict=True)
        ],
    )

    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.35), layout="constrained", sharey=True)
    contrasts = [(p, q) for p in ("full", "truncate", "summary") for q in ("q8", "q4")]
    for ax, metric, title in zip(
        axes,
        ("clean_utility", "attack_success_common_clean"),
        ("Clean utility: quantized minus F16", "Target ASR: pairwise common-clean"),
        strict=True,
    ):
        for i, (p, q) in enumerate(contrasts):
            effect = next(
                e for e in data["paired_comparisons"] if e["a"] == ["f16", p] and e["b"] == [q, p]
            )[metric]
            if effect["unknown_pairs"]:
                ax.hlines(i, *effect["delta_unknown_bounds"], color="#929292", ls="--", lw=1.5)
            ci = effect["uncertainty"]["interval_95"]
            if ci is not None:
                ax.hlines(i, *ci, color="#21618c", lw=2.5)
            ax.plot(effect["delta_known"], i, "o", color="#21618c", ms=3)
            ax.text(
                0.19,
                i,
                f"{effect['known_pairs']}/{effect['n']}" + ("*" if ci is None else ""),
                fontsize=7,
                va="center",
            )
        ax.axvline(0, color="#b0b0b0", lw=0.7)
        ax.set_xlim(-0.19, 0.27)
        ax.set_xticks([-0.15, 0, 0.15], ["-15", "0", "+15"])
        ax.set_xlabel("Percentage-point difference")
        ax.set_title(title)
        ax.grid(axis="x", alpha=0.15)
    axes[0].set_yticks(range(6), [f"{q.upper()} {p[0].upper()}" for p, q in contrasts])
    axes[0].invert_yaxis()
    fig.legend(
        handles=[
            Line2D([0], [0], color="#21618c", marker="o", label="Known delta / 95% CI"),
            Line2D([0], [0], color="#929292", ls="--", label="All-selected unknown bounds"),
        ],
        loc="outside lower center",
        ncol=2,
    )
    save(fig, "paired-main")

    fig, axes = plt.subplots(1, 4, figsize=(6.8, 1.95), layout="constrained")
    metrics = [
        ("clean_compacted", "Clean compacted"),
        ("attack_compacted", "Attack compacted"),
        ("attacked_literal_exposed", "Attack exposed"),
        ("attacked_removal_after_exposure", "Later literal absence"),
    ]
    for ax, (key, title) in zip(axes, metrics, strict=True):
        grid = np.array([c["context"][key] for c in configs]).reshape(3, 3).T
        ax.imshow(grid / 54, vmin=0, vmax=1, cmap="Blues", aspect="auto")
        for i in range(3):
            for j in range(3):
                ax.text(
                    j,
                    i,
                    f"{grid[i, j]}/54",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if grid[i, j] > 28 else "#172335",
                )
        ax.set_xticks(range(3), ["F16", "Q8", "Q4"])
        ax.set_yticks(range(3), ["F", "T", "S"])
        ax.set_title(title)
    save(fig, "context-main")

    fig, ax = plt.subplots(figsize=(3.25, 2.3), layout="constrained")
    pairs = [(q, p) for q in ("q8", "q4") for p in ("truncate", "summary")]
    ax.imshow(np.zeros((4, 2)), cmap="RdBu_r", vmin=-2, vmax=2, aspect="auto")
    for row in data["interactions"]:
        i = pairs.index((row["precision"], row["policy"]))
        j = int(row["outcome"] != "clean_utility")
        cov = scope["interactions"][row["precision"], row["policy"], row["outcome"]]
        assert row["difference_in_differences"] == 0 and cov["compacted_episodes"] == 0
        ci = "CI [0,0]" if row["uncertainty"]["interval_95"] is not None else "CI withheld"
        ax.text(
            j,
            i,
            f"0.000; {row['known_tasks']}/{row['n']}\n{ci}",
            fontsize=7,
            ha="center",
            va="center",
        )
    ax.set_xticks([0, 1], ["Clean U", "Common ASR"])
    ax.set_yticks(range(4), [f"{q.upper()} x {p[0].upper()}" for q, p in pairs])
    save(fig, "interaction-main")

    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.55), layout="constrained", sharey=True)
    markers = {"full": "o", "truncate": "s", "summary": "^"}
    plotted = None
    for ax, metric, scale, title in zip(
        axes,
        ("seconds", "run_aggregate_peak_rss_bytes"),
        (1, 2**30),
        ("Matched clean latency (s)", "Paired-run RSS (GiB)"),
        strict=True,
    ):
        for i, c in enumerate(configs):
            attack = c["attack_success_common_clean"]
            if attack["unknown"]:
                continue
            assert c["common_clean_cost_coverage"] == {"expected": 18, "complete": 18}
            x = c["common_clean_costs"][metric]["median"] / scale
            y = c["clean_utility"]["lower_bound"] * 100
            ax.vlines(x, y, c["clean_utility"]["upper_bound"] * 100, color="#555", lw=0.7)
            plotted = ax.scatter(
                x,
                y,
                c=[attack["lower_bound"]],
                vmin=0.35,
                vmax=0.5,
                cmap="YlOrRd",
                marker=markers[c["policy"]],
                s=25,
                edgecolors="#444",
                lw=0.5,
            )
            ax.annotate(
                str(i + 1),
                (x, y),
                xytext=((2, 5), (2, 16), (3, 28))[i % 3],
                textcoords="offset points",
                fontsize=7,
            )
        ax.set_xlabel(title)
        ax.set_ylim(39, 64)
        ax.grid(alpha=0.15)
    axes[0].set_ylabel("All-task clean U (%)")
    fig.colorbar(plotted, ax=axes, shrink=0.85, label="ASR on 18 common-clean tasks")
    fig.supxlabel(
        "1/2/3: F16 F/T/S; 4/5/6: Q8 F/T/S; 7/8/9: Q4 F/T/S. Q8 Full (4): unknown ASR, omitted.",
        fontsize=7,
    )
    save(fig, "cost-main")
    evidence = [SOURCE / n for n in PINNED]
    evidence.extend(
        ROOT / "results/phase2-main-v1/reviews" / f"main-{i:03d}.{ext}"
        for i in range(1, 19)
        for ext in ("md", "csv")
    )
    evidence.extend(
        [
            Path(__file__),
            ROOT / "scripts/plot_phase2_matrix.py",
            ROOT / "scripts/analyze_phase2_matrix.py",
        ]
    )
    for name, expected in PINNED.items():
        assert sha(SOURCE / name) == expected
    (OUT / "provenance.json").write_text(
        json.dumps(
            {
                "status": "main972_reviewed_confirmation_and_diagnostics_pending",
                "main_tasks": 54,
                "main_episodes": 972,
                "scope": "Main only; no new scores, CIs or paired comparisons",
                "inputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in evidence},
                "outputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in produced},
                "matched_compaction": {f"{q}/{p}": c for (q, p), c in scope["matched"].items()},
                "interaction_compaction": {
                    "/".join(k): c for k, c in scope["interactions"].items()
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"status": "main972_only", "outputs": len(produced), "directory": str(OUT)}))


if __name__ == "__main__":
    main()
