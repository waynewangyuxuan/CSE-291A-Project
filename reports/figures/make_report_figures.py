"""Render report figures from the reviewed recovery export and historical explorations.

Run from the repository root with `make phase1-report-figures`.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
load_inputs = importlib.import_module("plot_phase1_evaluation").load_inputs

RESULTS = ROOT / "results/phase1-recovery"
DATA, EPISODES = load_inputs(
    RESULTS / "evaluation-measurements.json", RESULTS / "evaluation-episodes.csv"
)
NATIVE = DATA["native_summary"]
POOLED = DATA["pooled_request_metrics"]["overall"]
with (RESULTS / "evaluation-requests.csv").open(newline="") as handle:
    REQUESTS = list(csv.DictReader(handle))


def fraction(summary, key):
    rate = summary["rates"][key]
    return rate["successes"], rate["denominator"]


BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
RED, GRAY, INK, INK2 = "#e34948", "#b8b6ae", "#0b0b0b", "#52514e"

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.size": 7.5,
        "axes.edgecolor": "#d6d4cc",
        "axes.linewidth": 0.6,
        "axes.labelcolor": INK2,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 6.8,
        "legend.frameon": False,
        "axes.titlesize": 8,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
    }
)


def tidy(ax, xmax=None):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#e8e6df", linewidth=0.6)
    ax.set_axisbelow(True)
    if xmax is not None:
        ax.set_xlim(0, xmax)


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(OUT / f"{name}.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
# Figure 1: capability and attack success by suite, goal and template (RQ1/RQ3)
# --------------------------------------------------------------------------
SUITES = ["Banking", "Slack", "Workspace", "Travel", "All"]
GROUPS = [NATIVE["by_suite"][s.lower()] for s in SUITES[:-1]] + [NATIVE["overall"]]
CLEAN_U = [fraction(g, "clean_utility_all_attempts") for g in GROUPS]
ATK_U = [fraction(g, "attack_utility_all_attempts") for g in GROUPS]
ASR_ALL = [fraction(g, "attack_success_all_attempts") for g in GROUPS]
ASR_ELIG = [fraction(g, "attack_success_clean_completable_all_attempts") for g in GROUPS]
GOALS = [
    (label, *fraction(NATIVE["by_suite_goal"][suite][goal], "attack_success_all_attempts"))
    for label, suite, goal in (
        ("Banking: redirect payment", "banking", "injection_task_4"),
        ("Banking: change password", "banking", "injection_task_7"),
        ("Slack: phishing DM", "slack", "injection_task_1"),
        ("Slack: visit site", "slack", "injection_task_3"),
        ("Workspace: send email", "workspace", "injection_task_0"),
        ("Workspace: create event", "workspace", "injection_task_2"),
        ("Travel: book hotel", "travel", "injection_task_0"),
        ("Travel: exfiltrate data", "travel", "injection_task_3"),
    )
]
TEMPLATES = [
    (label, *fraction(NATIVE["by_attack"][key], "attack_success_all_attempts"))
    for label, key in (("TODO", "direct_append"), ("Ignore\nprev.", "ignore_previous_append"))
]


def pct(n, d):
    return 100 * n / d


def fig1():
    fig, axes = plt.subplots(
        1, 3, figsize=(6.6, 2.5), gridspec_kw={"width_ratios": [1.1, 1.3, 0.85]}
    )

    ax = axes[0]
    series = [
        ("Clean utility", CLEAN_U, BLUE),
        ("Utility under attack", ATK_U, AQUA),
        ("ASR, all attacks", ASR_ALL, ORANGE),
        ("ASR, clean-eligible", ASR_ELIG, YELLOW),
    ]
    h = 0.19
    ys = list(range(len(SUITES)))[::-1]
    for i, (label, data, color) in enumerate(series):
        vals = [pct(n, d) for n, d in data]
        ax.barh([y + (1.5 - i) * h for y in ys], vals, height=h - 0.03, color=color, label=label)
    ax.set_yticks(ys, SUITES)
    ax.set_xlabel("% of episodes", labelpad=1)
    ax.set_title("Outcomes by suite")
    tidy(ax, 100)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.40),
        ncol=2,
        handlelength=0.9,
        borderpad=0.2,
        labelspacing=0.25,
        columnspacing=0.8,
    )

    ax = axes[1]
    names = [g[0] for g in GOALS][::-1]
    vals = [pct(g[1], g[2]) for g in GOALS][::-1]
    ax.barh(names, vals, height=0.62, color=ORANGE)
    for y, g in enumerate(GOALS[::-1]):
        ax.text(pct(g[1], g[2]) + 1.2, y, f"{g[1]}/{g[2]}", va="center", fontsize=6.5, color=INK2)
    ax.set_xlabel("ASR, all attacks (%)")
    ax.set_title("ASR by attacker goal")
    tidy(ax, 62)

    ax = axes[2]
    names = [t[0] for t in TEMPLATES]
    vals = [pct(t[1], t[2]) for t in TEMPLATES]
    ax.bar(names, vals, width=0.55, color=ORANGE)
    for x, t in enumerate(TEMPLATES):
        ax.text(x, pct(t[1], t[2]) + 1.2, f"{t[1]}/{t[2]}", ha="center", fontsize=6.5, color=INK2)
    ax.set_ylabel("ASR (%)")
    ax.set_title("By template")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#e8e6df", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 40)
    ax.set_xticks(range(len(names)), names, rotation=25, ha="right")
    ax.tick_params(axis="x", labelsize=6.3)

    fig.tight_layout(w_pad=1.2)
    save(fig, "fig-outcomes")


# --------------------------------------------------------------------------
# Figure 2: failure classification (RQ2) and clean-to-attack transitions (RQ3)
# --------------------------------------------------------------------------
def fig2():
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.5), gridspec_kw={"width_ratios": [1.25, 1]})

    ax = axes[0]
    counts = Counter(r["failure_category"] for r in EPISODES if r["condition"] == "clean")
    labels = [
        ("No failure", counts["no_failure"]),
        ("Planning", counts["planning"]),
        ("Tool use", counts["tool_use"]),
        ("Other / unclear", counts["other"] + counts["unclear"]),
        ("Infrastructure", counts["infrastructure"]),
        ("Task misunderstanding", counts["task_understanding"]),
        ("Unnecessary refusal", counts["unnecessary_refusal"]),
    ][::-1]
    colors = [GRAY if label == "No failure" else BLUE for label, _ in labels]
    ax.barh(
        [label for label, _ in labels], [count for _, count in labels], height=0.6, color=colors
    )
    for y, (_name, n) in enumerate(labels):
        ax.text(n + 0.8, y, str(n), va="center", fontsize=6.5, color=INK2)
    ax.set_title("Clean failure labels (n=120)")
    ax.set_xlabel("episodes")
    tidy(ax, 54)
    ax.tick_params(axis="y", labelsize=6.5)

    ax = axes[1]
    cats = ["Clean ok → attacked fail", "Clean fail → attacked ok", "Unchanged"][::-1]
    paired = NATIVE["overall"]["paired_utility_change"]
    lost, gained = paired["clean_success_attack_failure"], paired["clean_failure_attack_success"]
    vals = [lost, gained, paired["pairs"] - lost - gained][::-1]
    cols = [RED, AQUA, GRAY][::-1]
    ax.barh(cats, vals, height=0.6, color=cols)
    for y, v in enumerate(vals):
        ax.text(v + 6, y, str(v), va="center", fontsize=6.5, color=INK2)
    ax.set_title("Utility under attack")
    ax.set_xlabel(f"attack/control pairs ({paired['pairs']} known)")
    tidy(ax, 460)
    ax.tick_params(axis="y", labelsize=6.5)

    fig.tight_layout(w_pad=2.2)
    save(fig, "fig-failures")


# --------------------------------------------------------------------------
# Figure 3: deployment cost (RQ5)
# --------------------------------------------------------------------------
def fig3():
    fig, axes = plt.subplots(
        1, 3, figsize=(6.6, 1.8), gridspec_kw={"width_ratios": [1.3, 0.8, 0.8]}
    )

    ax = axes[0]
    selections = [
        (f"{condition.title()}, all ({summary['duration_seconds']['n']})", summary)
        for condition, summary in NATIVE["overall"]["costs_by_condition"].items()
    ] + [
        (f"{condition.title()}, utility=True ({summary['selected_episodes']})", summary["costs"])
        for condition, summary in DATA["utility_success_costs"]["by_condition"].items()
    ]
    rows = [
        (label, costs["duration_seconds"]["median"], costs["duration_seconds"]["p95"])
        for label, costs in selections
    ][::-1]
    y = list(range(len(rows)))
    ax.barh([i + 0.17 for i in y], [r[2] for r in rows], height=0.3, color=GRAY, label="p95")
    ax.barh([i - 0.17 for i in y], [r[1] for r in rows], height=0.3, color=BLUE, label="median")
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlabel("episode latency (s)")
    ax.set_title("Latency")
    tidy(ax, 200)
    ax.tick_params(axis="y", labelsize=6.5)

    ax = axes[1]
    prefill, decode = POOLED["native_prefill_seconds"], POOLED["native_decode_seconds"]
    ax.bar(["prefill", "decode"], [prefill / 3600, decode / 3600], width=0.55, color=[BLUE, AQUA])
    for x, seconds in enumerate((prefill, decode)):
        ax.text(
            x,
            seconds / 3600 + 0.2,
            f"{100 * seconds / (prefill + decode):.1f}%",
            ha="center",
            fontsize=6.5,
            color=INK2,
        )
    ax.set_ylabel("native model time (h)")
    ax.set_title("Where model time goes")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#e8e6df", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 10.5)

    ax = axes[2]
    tokens = [
        sum(int(r[key] or 0) for r in REQUESTS) / 1e6
        for key in ("prompt_tokens", "completion_tokens")
    ]
    ax.bar(["prompt", "completion"], tokens, width=0.55, color=[BLUE, AQUA])
    for x, value in enumerate(tokens):
        ax.text(x, value + 0.25, f"{value:.2f} M", ha="center", fontsize=6.5, color=INK2)
    ax.set_ylabel("tokens (millions)")
    ax.set_title(f"Tokens, {POOLED['response_count']:,} responses")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#e8e6df", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 13)

    fig.tight_layout(w_pad=1.4)
    save(fig, "fig-costs")


# --------------------------------------------------------------------------
# Figure 4: exploratory studies (RQ7, RQ8)
# --------------------------------------------------------------------------
def fig4():
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 3.0), gridspec_kw={"width_ratios": [0.8, 1.4]})

    ax = axes[0]
    rows = [
        ("Clean utility", 1.67, -3.33, 6.67),
        ("Attacked utility", 1.25, -1.25, 3.75),
        ("Attack success A", -3.33, -6.67, 0.0),
        ("A, common clean-success", -4.0, -9.0, 1.0),
    ][::-1]
    y = list(range(len(rows)))
    for i, (_name, est, lo, hi) in enumerate(rows):
        ax.plot([lo, hi], [i, i], color=INK2, linewidth=1.2, solid_capstyle="round")
        ax.plot(est, i, "o", color=BLUE, markersize=5, markeredgecolor="white", markeredgewidth=1)
    ax.axvline(0, color="#d6d4cc", linewidth=0.8)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlabel("reminder − original (pp), paired 95% CI")
    ax.set_title("Prompt reminder, 300 pairs")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_xlim(-10, 8)
    ax.tick_params(axis="y", labelsize=6.5)

    ax = axes[1]
    summary = json.loads((ROOT / "results/phase2-first-pass-v1/summary.json").read_text())
    order = ["full", "truncate", "long_summary", "short_summary", "short_summary_rag"]
    pretty = {
        "full": "Full",
        "truncate": "Truncate",
        "long_summary": "Long summary",
        "short_summary": "Short summary",
        "short_summary_rag": "Short + search",
    }
    groups = {(g["precision"], g["policy"]): g for g in summary["groups"]}
    labels, ys = [], []
    y = 0
    for pol in order:
        for prec in ("f16", "q4"):
            g = groups[(prec, pol)]
            a = g["A"]
            true, unk, n = a["true"], a["unknown"], a["n"]
            false = n - true - unk
            ax.barh(y, true, color=ORANGE, height=0.7)
            ax.barh(y, false, left=true, color="#dcdad2", height=0.7)
            ax.barh(
                y,
                unk,
                left=true + false,
                color=GRAY,
                height=0.7,
                hatch="////",
                edgecolor="white",
                linewidth=0,
            )
            cu = g["clean_U"]
            ax.text(n + 0.6, y, f"{cu['true']}/{cu['n']}", va="center", fontsize=6.3, color=INK2)
            labels.append(f"{pretty[pol]} · {prec.upper()}")
            ys.append(y)
            y += 1
        y += 0.4
    ax.set_yticks(ys, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 27)
    ax.set_xlabel("attacked episodes (24 per configuration)")
    ax.set_title("Q4 × context: attack success (right: clean U)")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", labelsize=6.3)
    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color=ORANGE, label="A = True"),
            Patch(color="#dcdad2", label="A = False"),
            Patch(facecolor=GRAY, hatch="////", edgecolor="white", label="unknown (error)"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.32),
        ncol=3,
        handlelength=1.0,
        borderpad=0.2,
        columnspacing=1.0,
    )

    fig.tight_layout(w_pad=1.6)
    save(fig, "fig-explore")


if __name__ == "__main__":
    fig1()
    fig2()
    fig3()
    fig4()
    inputs = [
        RESULTS / "evaluation-measurements.json",
        RESULTS / "evaluation-episodes.csv",
        RESULTS / "evaluation-requests.csv",
        ROOT / "results/phase2-first-pass-v1/summary.json",
    ]
    provenance = {
        "snapshot_identity": DATA["snapshot_identity"],
        "matplotlib_version": matplotlib.__version__,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inputs": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
        "explorations": "Historical archived results; not rerun during baseline recovery.",
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("wrote", sorted(p.name for p in OUT.glob("fig-*.pdf")))
