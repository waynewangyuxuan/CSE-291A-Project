"""Report tables and figures from hash-pinned formal exports, keeping cohorts separate."""

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
FORMAL = ROOT / "results/phase2-main-v1/analysis"
FORMAL_PINNED = {
    "episodes.json": "b15daed4b5b64516d120873584f4623e401c16291dd10e345181ba9feccdd45b",
    "episodes.csv": "0360d985e20cf6e04ebb6ea9afc9ca94b84e4bf83b6e264e371a43d57bf7d52c",
    "measurements.json": "5f068ac9fa7e6ffdfcd11e5fdb7aec2412d754ee93f1508eec200a59762ca0e0",
}
DIAGNOSTICS = ROOT / "results/phase2-seed-diagnostics-v1"
DIAGNOSTIC_PINNED = {
    "README.md": "a8dead67b55b87298b57ef8b03d703a7303f9dd437d37f5e15fb414688bf23bb",
    "comparison.json": "bf109a86a3a0c87824d497f3cd56bacdc6a13bae3c3de777621ac16bfc30b75a",
    "comparison.csv": "fd32b7058143f0740f06cb4a0fa0bdc979ffb1d6348ef6095c945880128d449f",
    "provenance.json": "4ad1972baf79131f29563bad877fffa55fcd03544593f201bda5206fe85d7bc9",
    "recovery-verification.json": (
        "75c3bd52a98a297a74271b6165d2d2c9c1aef3d1405973bea9e02a026cfc6faf"
    ),
    "review-completion.json": "f534a5bb46df39c56aa30b233f6067d3894e2fd5dfb30604d5cb80c0af30c7cc",
    "review-findings.md": "2b6d59a6c7ef2666388186dd2bd75c284385181b590ba45620f5c5812fe6494f",
    "selection.json": "539bf723235abf7d021f6dc47fdff9a6ac95f8667aea43ec4460c27143ab3cf7",
    "trace-review.csv": "7fd457f1505fedf7c8ff41124054cece246168fa65d3aa1d19a92b74629aae4c",
    "verification.json": "8ae814c2ecee4b1d31d9497b1e5b64335b6d5b3b892c354a59dae28b674851c3",
}
DIAGNOSTIC_DESIGN_PINNED = {
    "results/phase2-main-v1/seed29-hypotheses.json": (
        "eaf26490f0aef45bf5ea0601583488c878fccc8eb56c253d2bd1453a8a086ac4"
    ),
    "results/phase2-main-v1/seed29-hypotheses.md": (
        "381cb89d51405f8f75bbef238e39edcf59d854555a410de363d3d4036249da9a"
    ),
    "artifacts/phase2-seed-diagnostics-v1/evidence.tar.gz": (
        "342be526552443b69bc9d8cd0076ff62fd81ba14c5eeac1c3603dec9465ecae7"
    ),
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
    for name, expected in FORMAL_PINNED.items():
        if sha(FORMAL / name) != expected:
            raise ValueError(f"Frozen formal export changed: {name}")
    formal = json.loads((FORMAL / "measurements.json").read_text())
    formal_rows = json.loads((FORMAL / "episodes.json").read_text())
    assert formal["status"] == "final_reviewed"
    assert formal["included_episodes"] == formal["reviewed_episodes"] == len(formal_rows) == 1098
    assert formal["cohorts"]["main"] == data
    assert [r for r in formal_rows if r["cohort"] == "main"] == rows
    diagnostic_inputs = {DIAGNOSTICS / n: h for n, h in DIAGNOSTIC_PINNED.items()}
    diagnostic_inputs.update({ROOT / n: h for n, h in DIAGNOSTIC_DESIGN_PINNED.items()})
    for path, expected in diagnostic_inputs.items():
        if sha(path) != expected:
            raise ValueError(f"Frozen diagnostic evidence changed: {path}")
    diagnostic_rows = json.loads((DIAGNOSTICS / "comparison.json").read_text())
    diagnostic_gate = json.loads((DIAGNOSTICS / "verification.json").read_text())
    diagnostic_recovery = json.loads((DIAGNOSTICS / "recovery-verification.json").read_text())
    assert diagnostic_gate["checks"] == "passed" and diagnostic_gate["partial"] is False
    assert diagnostic_gate["reviewed_episodes"] == len(diagnostic_rows) == 24
    assert diagnostic_recovery["status"] == "reproduced_offline_without_inference"
    assert diagnostic_recovery["episode_audits_reproduced"] == 24
    for name, expected in diagnostic_recovery["reproduced"].items():
        assert sha(DIAGNOSTICS / name) == expected
    sys.path.insert(0, str(ROOT / "scripts"))
    plotter = importlib.import_module("plot_phase2_matrix")
    scope = plotter.plot_scope(report, rows)["main"]
    confirmation = formal["cohorts"]["confirmation"]
    confirmation_scope = plotter.plot_scope(formal, formal_rows)["confirmation"]
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

    confirmation_rows = []
    confirmation_costs = []
    for c in confirmation["configurations"]:
        label = f"{c['precision'].upper()} {c['policy'][0].upper()}"
        assert c["clean_utility"]["success"] == c["attacked_utility"]["success"] == 1
        for metric in ("attack_success_own_clean", "attack_success_common_clean"):
            assert c[metric]["n"] == 1 and c[metric]["success"] == c[metric]["unknown"] == 0
        sensitivity = c["reviewed_clean_eligibility_sensitivity"]
        assert sensitivity["own_clean_ASR"]["n"] == sensitivity["common_clean_ASR"]["n"] == 0
        assert sensitivity["own_clean_ASR"]["lower_bound"] is None
        confirmation_rows.append(
            " & ".join(
                [
                    label,
                    *(
                        count(c[k])
                        for k in ("clean_utility", "attacked_utility", "attack_success_all")
                    ),
                    f"{c['context']['clean_compacted']}/{c['context']['attack_compacted']}",
                ]
            )
            + r" \\"
        )
        a = c["all_outcome_costs"]
        confirmation_costs.append(
            " & ".join(
                [
                    label,
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
            )
            + r" \\"
        )
    table("confirmation-outcomes.tex", confirmation_rows)
    table("confirmation-costs.tex", confirmation_costs)

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
    evidence.extend(FORMAL / n for n in FORMAL_PINNED)
    evidence.extend(diagnostic_inputs)
    evidence.extend(
        ROOT / "results/phase2-main-v1/reviews" / f"main-{i:03d}.{ext}"
        for i in range(1, 19)
        for ext in ("md", "csv")
    )
    evidence.extend(
        ROOT / "results/phase2-main-v1/reviews" / f"confirmation-{i:03d}.{ext}"
        for i in range(1, 4)
        for ext in ("md", "csv", "verification.json")
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
    for name, expected in FORMAL_PINNED.items():
        assert sha(FORMAL / name) == expected
    for path, expected in diagnostic_inputs.items():
        assert sha(path) == expected
    (OUT / "provenance.json").write_text(
        json.dumps(
            {
                "status": "final_formal1098_and_separate_diagnostic24_reviewed",
                "main_tasks": 54,
                "main_episodes": 972,
                "confirmation_tasks": 7,
                "confirmation_episodes": 126,
                "diagnostic_cases": 6,
                "diagnostic_new_episodes": 24,
                "diagnostic_role": (
                    "Outcome-selected action review; no pooled estimates or precision ranking"
                ),
                "diagnostic_full_gate": "24 reviewed, nonpartial, passed",
                "diagnostic_offline_recovery": diagnostic_recovery,
                "main_identical_to_final_export": True,
                "scope": (
                    "Main, confirmation and diagnostics separate; "
                    "existing estimates and source-linked action review only"
                ),
                "inputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in evidence},
                "outputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in produced},
                "matched_compaction": {f"{q}/{p}": c for (q, p), c in scope["matched"].items()},
                "interaction_compaction": {
                    "/".join(k): c for k, c in scope["interactions"].items()
                },
                "confirmation_interaction_compaction": {
                    "/".join(k): c for k, c in confirmation_scope["interactions"].items()
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "status": "final_formal1098_separate_diagnostic24",
                "outputs": len(produced),
                "directory": str(OUT),
            }
        )
    )


if __name__ == "__main__":
    main()
