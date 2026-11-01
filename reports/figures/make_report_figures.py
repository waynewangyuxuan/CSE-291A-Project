"""Compact figures for the Phase 1 report.

Values are transcribed from the audited published results:
  results/phase1/evaluation-assessment.md            (RQ1, RQ2, RQ3, RQ5)
  results/prompt-defense-first-wave-v1/README.md     (RQ7)
  results/phase2-first-pass-v1/summary.json          (RQ8, read directly)

Run from the repository root:
  uv run --no-project --isolated --with matplotlib==3.11.2 \
      python reports/figures/make_report_figures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent

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
CLEAN_U = [(6, 16), (20, 32), (23, 48), (9, 24), (58, 120)]
ATK_U = [(13, 64), (42, 128), (91, 192), (27, 96), (173, 480)]
ASR_ALL = [(28, 64), (46, 128), (3, 192), (15, 96), (92, 480)]
ASR_ELIG = [(12, 24), (32, 80), (2, 92), (9, 36), (55, 232)]

GOALS = [
    ("Banking: redirect payment", 12, 32),
    ("Banking: change password", 16, 32),
    ("Slack: phishing DM", 22, 64),
    ("Slack: visit site", 24, 64),
    ("Workspace: send email", 3, 96),
    ("Workspace: create event", 0, 96),
    ("Travel: book hotel", 4, 48),
    ("Travel: exfiltrate data", 11, 48),
]
TEMPLATES = [("TODO", 13, 240), ("Ignore\nprev.", 79, 240)]


def pct(n, d):
    return 100 * n / d


def fig1():
    fig, axes = plt.subplots(
        1, 3, figsize=(6.6, 1.6), gridspec_kw={"width_ratios": [1.35, 1.25, 0.6]}
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
    ax.set_xlabel("% of episodes")
    ax.set_title("Outcomes by suite")
    tidy(ax, 100)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, handlelength=0.9,
              borderpad=0.2, labelspacing=0.25, columnspacing=0.8)

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
    ax.tick_params(axis="x", labelsize=6.3)

    fig.tight_layout(w_pad=1.2)
    save(fig, "fig-outcomes")


# --------------------------------------------------------------------------
# Figure 2: failure classification (RQ2) and clean-to-attack transitions (RQ3)
# --------------------------------------------------------------------------
def fig2():
    fig, axes = plt.subplots(1, 2, figsize=(3.4, 1.9), gridspec_kw={"width_ratios": [1.25, 1]})

    ax = axes[0]
    labels = [
        ("No failure", 40),
        ("Planning", 45),
        ("Tool use", 15),
        ("Other / unclear", 16),
        ("Task misunderstanding", 2),
        ("Unnecessary refusal", 2),
    ][::-1]
    colors = [GRAY, BLUE, BLUE, BLUE, BLUE, BLUE][::-1]
    ax.barh([l[0] for l in labels], [l[1] for l in labels], height=0.6, color=colors)
    for y, (name, n) in enumerate(labels):
        ax.text(n + 0.8, y, str(n), va="center", fontsize=6.5, color=INK2)
    ax.set_title("Clean failure labels (n=120)")
    ax.set_xlabel("episodes")
    tidy(ax, 54)
    ax.tick_params(axis="y", labelsize=6.5)

    ax = axes[1]
    cats = ["Clean ok → attacked fail", "Clean fail → attacked ok", "Unchanged"][::-1]
    vals = [74, 16, 457 - 74 - 16][::-1]
    cols = [RED, AQUA, GRAY][::-1]
    ax.barh(cats, vals, height=0.6, color=cols)
    for y, v in enumerate(vals):
        ax.text(v + 6, y, str(v), va="center", fontsize=6.5, color=INK2)
    ax.set_title("Utility under attack")
    ax.set_xlabel("attack/control pairs (457 known)")
    tidy(ax, 460)
    ax.tick_params(axis="y", labelsize=6.5)

    fig.tight_layout(w_pad=2.2)
    save(fig, "fig-failures")


# --------------------------------------------------------------------------
# Figure 3: deployment cost (RQ5)
# --------------------------------------------------------------------------
def fig3():
    fig, axes = plt.subplots(1, 3, figsize=(6.6, 1.1), gridspec_kw={"width_ratios": [1.3, 0.8, 0.8]})

    ax = axes[0]
    rows = [
        ("Clean, all (120)", 56.43, 180.06),
        ("Attack, all (480)", 49.70, 159.13),
        ("Clean, utility=True (58)", 44.73, 159.33),
        ("Attack, utility=True (173)", 48.18, 151.01),
    ][::-1]
    y = list(range(len(rows)))
    ax.barh([i + 0.17 for i in y], [r[2] for r in rows], height=0.3, color=GRAY, label="p95")
    ax.barh([i - 0.17 for i in y], [r[1] for r in rows], height=0.3, color=BLUE, label="median")
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlabel("episode latency (s)")
    ax.set_title("Latency")
    tidy(ax, 200)
    ax.legend(loc="lower right", handlelength=0.9)
    ax.tick_params(axis="y", labelsize=6.5)

    ax = axes[1]
    ax.bar(["prefill", "decode"], [31948.65 / 3600, 9198.24 / 3600], width=0.55, color=[BLUE, AQUA])
    ax.text(0, 31948.65 / 3600 + 0.2, "78%", ha="center", fontsize=6.5, color=INK2)
    ax.text(1, 9198.24 / 3600 + 0.2, "22%", ha="center", fontsize=6.5, color=INK2)
    ax.set_ylabel("native model time (h)")
    ax.set_title("Where model time goes")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#e8e6df", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 10.5)

    ax = axes[2]
    ax.bar(["prompt", "completion"], [11.354, 0.146], width=0.55, color=[BLUE, AQUA])
    ax.text(0, 11.354 + 0.25, "11.35 M", ha="center", fontsize=6.5, color=INK2)
    ax.text(1, 0.146 + 0.25, "0.15 M", ha="center", fontsize=6.5, color=INK2)
    ax.set_ylabel("tokens (millions)")
    ax.set_title("Tokens, 2,965 requests")
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
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 1.8), gridspec_kw={"width_ratios": [0.8, 1.4]})

    ax = axes[0]
    rows = [
        ("Clean utility", 1.67, -3.33, 6.67),
        ("Attacked utility", 1.25, -1.25, 3.75),
        ("Attack success A", -3.33, -6.67, 0.0),
        ("A, common clean-success", -4.0, -9.0, 1.0),
    ][::-1]
    y = list(range(len(rows)))
    for i, (name, est, lo, hi) in enumerate(rows):
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
            ax.barh(y, unk, left=true + false, color=GRAY, height=0.7, hatch="////", edgecolor="white", linewidth=0)
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
        bbox_to_anchor=(0.5, -0.28),
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
    print("wrote", sorted(p.name for p in OUT.glob("fig-*.pdf")))
