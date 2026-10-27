"""Render the audited first-pass tables without importing the inference runtime."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

ROOT = Path("results/phase2-first-pass-v1/analysis")
FIGURES = ROOT / "figures"
POLICIES = ["full", "truncate", "long_summary", "short_summary", "short_summary_rag"]
NAMES = dict(zip(POLICIES, ["Full", "Truncate", "Long", "Short", "Short+RAG"], strict=True))
COLORS = {"success": "#21876c", "failure": "#d27b69", "unknown": "#c4c8d0"}


def read_json(name):
    return json.loads((ROOT / name).read_text())


def read_csv(name):
    with (ROOT / name).open() as handle:
        return list(csv.DictReader(handle))


def label(config):
    return f"{config['precision'].upper()} / {NAMES[config['policy']]}"


def save(fig, name):
    for extension in ["png", "svg", "pdf"]:
        metadata = {"Date": None} if extension == "svg" else {}
        if extension == "pdf":
            metadata = {"CreationDate": None, "ModDate": None}
        fig.savefig(FIGURES / f"{name}.{extension}", dpi=180, metadata=metadata)
        if extension == "svg":
            path = FIGURES / f"{name}.svg"
            lines = [line.rstrip() for line in path.read_text().splitlines()]
            path.write_text("\n".join(lines) + "\n")
    plt.close(fig)


def outcomes(configs):
    fig, axes = plt.subplots(1, 4, figsize=(15, 6), layout="constrained", sharey=True)
    for ax, key, title in zip(
        axes,
        ["clean_U", "attack_U", "A", "errors"],
        [
            "Clean task completion U",
            "Attacked task completion U",
            "Attack goal A",
            "Tool-budget exhaustion",
        ],
        strict=True,
    ):
        for i, config in enumerate(configs):
            if key == "errors":
                count, n = config[key], 36
                ax.barh(i, count, color="#717ba5")
                ax.text(count + 0.3, i, f"{count}/{n}", va="center", fontsize=9)
            else:
                s = config[key]
                yes, unknown, n = s["success"], s["unknown"], s["n"]
                ax.barh(i, yes, color=COLORS["success"])
                ax.barh(i, n - yes - unknown, left=yes, color=COLORS["failure"])
                ax.barh(i, unknown, left=n - unknown, color=COLORS["unknown"], hatch="///")
                ax.text(n + 0.3, i, f"{yes}/{n}; ?{unknown}", va="center", fontsize=9)
            ax.set_xlim(0, n * 1.48)
        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Episodes")
        ax.grid(axis="x", alpha=0.15)
    axes[0].set_yticks(range(10), [label(c) for c in configs])
    axes[0].invert_yaxis()
    fig.suptitle("RQ1 / RQ3: official outcomes and operational termination", fontsize=15)
    fig.legend(
        handles=[Patch(color=COLORS[k], label=k.title()) for k in COLORS],
        loc="outside lower center",
        ncol=3,
    )
    save(fig, "01-outcomes")


def task_map(configs, rows):
    tasks = sorted(
        read_json("task-features.json"),
        key=lambda t: (
            t["suite"],
            int(t["ground_truth_tool_count"]),
            int(t["user_task_id"].split("_")[-1]),
        ),
    )
    lookup = {(r["precision"], r["policy"], r["suite"], r["task"], r["goal"]): r for r in rows}
    fig, axes = plt.subplots(1, 3, figsize=(16, 7), layout="constrained", sharey=True)
    for panel, ax in enumerate(axes):
        values, marks = [], []
        for task in tasks:
            goals = [4, 7] if task["suite"] == "banking" else [1, 3]
            goal = "" if panel == 0 else f"injection_task_{goals[panel - 1]}"
            selected = [
                lookup[c["precision"], c["policy"], task["suite"], task["user_task_id"], goal]
                for c in configs
            ]
            values.append([{"True": 2, "False": 0, "": 1}[r["U"]] for r in selected])
            marks.append(
                ["●" if r["A"] == "True" else "?" if not r["A"] and panel else "" for r in selected]
            )
        ax.imshow(
            values,
            cmap=ListedColormap([COLORS["failure"], COLORS["unknown"], COLORS["success"]]),
            vmin=0,
            vmax=2,
            aspect="auto",
        )
        for i, row in enumerate(marks):
            for j, mark in enumerate(row):
                ax.text(j, i, mark, ha="center", va="center", fontsize=9)
        ax.set_xticks(range(10), [label(c) for c in configs], rotation=65, ha="right", fontsize=8)
        ax.set_yticks(
            range(12),
            [
                f"{t['suite']} {t['user_task_id'].split('_')[-1]}"
                f"  [ref {t['ground_truth_tool_count']}]"
                for t in tasks
            ],
        )
        ax.set_xticks(np.arange(-0.5, 10, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 12, 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=1)
        ax.tick_params(which="minor", length=0)
        ax.set_title(["Clean", "Attack: banking 4 / slack 1", "Attack: banking 7 / slack 3"][panel])
    fig.suptitle(
        "RQ1: 12 tasks × 10 settings × 3 conditions\n"
        "Color = U (green true, red false, gray unknown); dot = A true; ? = A unknown",
        fontsize=14,
    )
    save(fig, "02-task-map")


def paired_effects():
    rows = [r for r in read_csv("comparisons.csv") if r["subset"] == "all"]
    contrasts = list(dict.fromkeys((r["control"], r["variant"]) for r in rows))
    fig, axes = plt.subplots(1, 3, figsize=(15, 9), sharey=True, layout="constrained")
    for ax, condition, metric in zip(
        axes, ["clean", "attack", "attack"], ["U", "U", "A"], strict=True
    ):
        for i, (control, variant) in enumerate(contrasts):
            r = next(
                r
                for r in rows
                if (r["control"], r["variant"], r["condition"], r["metric"])
                == (control, variant, condition, metric)
            )
            low, high, point = [
                float(r[k])
                for k in ["unknown_bound_low_pp", "unknown_bound_high_pp", "delta_success_pp"]
            ]
            ax.plot([low, high], [i, i], color="#9096a3", linewidth=4, solid_capstyle="round")
            ax.plot(point, i, "o", color="#286f9e")
            ax.text(103, i, f"n{r['n']} / ?pairs {r['unknown_pairs']}", va="center", fontsize=8)
        ax.axvline(0, color="black", linewidth=0.7)
        ax.set_xlim(-100, 163)
        ax.set_xticks([-100, -50, 0, 50, 100])
        ax.set_title(f"{condition.title()} {metric}")
        ax.set_xlabel("Variant − control (percentage points)")
        ax.grid(axis="x", alpha=0.15)
    axes[0].set_yticks(range(len(contrasts)), [f"{a} → {b}" for a, b in contrasts], fontsize=8)
    axes[0].invert_yaxis()
    fig.suptitle(
        "Paired settings: observed-positive difference and worst-case unknown bounds\n"
        "These intervals are NOT confidence intervals; each setting has only 12 task identities.",
        fontsize=13,
    )
    save(fig, "03-paired-effects")


def joint_outcomes(configs):
    states = ["U1A0", "U1A1", "U0A0", "U0A1", "unknown"]
    colors = ["#21876c", "#b9bd58", "#e3b396", "#ab595a", "#c4c8d0"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained", sharey=True)
    for i, c in enumerate(configs):
        start = 0
        for state, color in zip(states, colors, strict=True):
            n = c["joint"].get(state, 0)
            axes[0].barh(i, n, left=start, color=color)
            if n:
                axes[0].text(start + n / 2, i, str(n), ha="center", va="center", fontsize=9)
            start += n
        s = c["own_clean_A"]
        axes[1].barh(i, s["success"], color="#ab595a")
        axes[1].barh(i, s["n"] - s["success"] - s["unknown"], left=s["success"], color="#eee3d9")
        axes[1].barh(i, s["unknown"], left=s["n"] - s["unknown"], color="#c4c8d0", hatch="///")
        axes[1].text(
            s["n"] + 0.2,
            i,
            f"A {s['success']}/{s['n']}; ?{s['unknown']} ({s['n'] // 2} tasks)",
            va="center",
            fontsize=9,
        )
    axes[0].set_yticks(range(10), [label(c) for c in configs])
    axes[0].invert_yaxis()
    axes[0].set_title("All attacked episodes: U × A (n=24 each)")
    axes[1].set_title("A on each setting's clean-completed tasks")
    axes[1].set_xlim(0, 20)
    for ax in axes:
        ax.set_xlabel("Episodes")
    fig.legend(
        handles=[Patch(color=c, label=s) for s, c in zip(states, colors, strict=True)],
        loc="outside lower center",
        ncol=5,
    )
    fig.suptitle("RQ3: attack reduction and user value are separate outcomes", fontsize=15)
    save(fig, "04-joint-and-capability")


def mechanisms(configs, rows):
    fig, axes = plt.subplots(1, 3, figsize=(15, 6), layout="constrained")
    for i, c in enumerate(configs):
        sub = [r for r in rows if r["precision"] == c["precision"] and r["policy"] == c["policy"]]
        total = sum(int(r["tool_calls"]) for r in sub)
        axes[0].barh(i, c["repeated_calls"] / total * 100, color="#697fab")
        axes[0].barh(i, c["repeated_call_results"] / total * 100, color="#354b76")
        axes[1].barh(i, c["errors"], color="#b97768")
    axes[0].set_yticks(range(10), [label(c) for c in configs])
    axes[1].set_yticks(range(10), [])
    for ax in axes[:2]:
        ax.invert_yaxis()
    axes[0].set_xlabel("% of executed tool calls")
    axes[0].set_title("Repeat exact tool + args\nDark subset: same result + error too")
    axes[1].set_xlabel("Episodes out of 36")
    axes[1].set_title("Tool-budget exhaustion")
    search = [r for r in rows if int(r["search_calls"])]
    stages = [
        72,
        len(search),
        sum(int(r["search_calls"]) for r in search),
        sum(int(r["accepted_searches"]) for r in search),
        sum(int(r["searches_with_matches"]) for r in search),
    ]
    axes[2].barh(range(5), stages, color=["#dadde3", "#9aabc0", "#657e9d", "#657e9d", "#657e9d"])
    axes[2].set_yticks(
        range(5),
        [
            "RAG episodes",
            "Used search (episodes)",
            "Search calls",
            "Accepted calls",
            "Calls with matches",
        ],
    )
    for i, value in enumerate(stages):
        axes[2].text(value + 1, i, str(value), va="center")
    axes[2].invert_yaxis()
    axes[2].set_xlim(0, 85)
    axes[2].set_title("Retrieval coverage\nMatches do not establish useful recovery")
    fig.suptitle("RQ2: observable repetition, termination and sparse retrieval", fontsize=15)
    save(fig, "05-behavior")


def costs(configs, rows):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), layout="constrained")
    contrasts = [
        ("f16", "full", "q4", "full"),
        ("f16", "short_summary", "f16", "short_summary_rag"),
        ("q4", "short_summary", "q4", "short_summary_rag"),
    ]
    for ax, (q0, p0, q1, p1) in zip(axes[0], contrasts, strict=True):

        def selected(q, p):
            return {
                (r["suite"], r["task"], r["goal"]): r
                for r in rows
                if r["precision"] == q and r["policy"] == p
            }

        left, right = selected(q0, p0), selected(q1, p1)
        maximum = max(float(r["seconds"]) for r in [*left.values(), *right.values()]) * 1.08
        for key, a in left.items():
            b = right[key]
            error = a["status"] == "error" or b["status"] == "error"
            ax.scatter(
                float(a["seconds"]),
                float(b["seconds"]),
                marker="x" if error else "o",
                color="#b85e58" if error else "#347b96",
                s=30,
                alpha=0.8,
            )
        ax.plot([0, maximum], [0, maximum], "--", color="#888", linewidth=0.8)
        ax.set(
            xlim=(0, maximum),
            ylim=(0, maximum),
            xlabel=f"{q0}/{NAMES[p0]} seconds",
            ylabel=f"{q1}/{NAMES[p1]} seconds",
        )
        ax.set_title("× = either episode exhausted tool budget", fontsize=10)
    x = np.arange(10)
    for ax, key1, key2, title in [
        (axes[1, 0], "agent_http_seconds", "summary_seconds", "Mean API time / episode (seconds)"),
        (axes[1, 1], "agent_tokens", "summary_tokens", "Mean total model tokens / episode"),
    ]:
        agent = [c["cost"][key1]["mean"] for c in configs]
        summary = [c["cost"][key2]["mean"] for c in configs]
        ax.bar(x, agent, color="#347b96", label="Agent")
        ax.bar(x, summary, bottom=agent, color="#d9a45b", label="F16 summarizer")
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=8)
    for i, c in enumerate(configs):
        sub = [r for r in rows if r["precision"] == c["precision"] and r["policy"] == c["policy"]]
        peaks = {r["suite"]: float(r["run_deployment_peak_rss_bytes"]) / 1e9 for r in sub}
        axes[1, 2].scatter([i - 0.12, i + 0.12], list(peaks.values()), color="#697fab", s=30)
    axes[1, 2].set_title(
        "Sum-of-process RSS peak (GB)\n2 suite runs per setting; F16 summary resident", fontsize=10
    )
    axes[1, 2].set_ylabel("Not physical unified memory")
    for ax in axes[1]:
        ax.set_xticks(x, [label(c) for c in configs], rotation=65, ha="right", fontsize=8)
    fig.suptitle("RQ4: paired wall time, model cost and deployment RSS", fontsize=15)
    save(fig, "06-costs")


def growth():
    series = read_json("trajectory-series.json")
    fig, axes = plt.subplots(2, 2, figsize=(13, 7), layout="constrained")
    cases = [
        ("f16", "banking", "user_task_3", "injection_task_4", ["full", "long_summary"]),
        ("q4", "slack", "user_task_16", "injection_task_1", ["short_summary", "short_summary_rag"]),
    ]
    for col, (q, suite, task, goal, policies) in enumerate(cases):
        for policy, color in zip(policies, ["#347b96", "#b97768"], strict=True):
            events = series[f"{q}/{policy}/{suite}/{task}/attack/{goal}"]
            start = events[0]["start"]
            cumulative = np.cumsum([e["total_tokens"] for e in events])
            axes[1, col].step(
                [e["end"] - start for e in events],
                cumulative,
                where="post",
                color=color,
                label=NAMES[policy],
            )
            for source, marker in [("agent", "o"), ("summarizer", "x")]:
                points = [e for e in events if e["source"] == source]
                if not points:
                    continue
                axes[0, col].plot(
                    [e["start"] - start for e in points],
                    [e["prompt_tokens"] for e in points],
                    marker=marker,
                    linestyle="-" if source == "agent" else ":",
                    color=color,
                    label=f"{NAMES[policy]} / {source}",
                    markersize=4,
                )
        axes[0, col].set_title(f"{q.upper()} {suite} {task} / {goal}", fontsize=11)
        axes[0, col].set_ylabel("Prompt tokens per request")
        axes[1, col].set_ylabel("Cumulative agent + summary tokens")
        for ax in axes[:, col]:
            ax.set_xlabel("Seconds from first model request")
            ax.legend(fontsize=8)
            ax.grid(alpha=0.15)
    fig.suptitle("RQ4: context size and cumulative cost on selected paired traces", fontsize=15)
    save(fig, "07-trajectory-growth")


def attack_stages(configs):
    rows = read_csv("attack-stages.csv")
    categories = [
        "No matched call emitted",
        "Emitted, none executed",
        "Executed, none accepted",
        "At least one accepted",
    ]
    colors = ["#cad0d8", "#c9b778", "#df9d6d", "#a55f68"]
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    for i, config in enumerate(configs):
        selected = [
            r
            for r in rows
            if r["precision"] == config["precision"] and r["policy"] == config["policy"]
        ]
        counts = [0] * 4
        for row in selected:
            stage = 0
            for key in [
                "target_matched_call_emitted",
                "target_matched_call_executed",
                "target_matched_call_accepted",
            ]:
                stage += row[key] == "True"
            counts[stage] += 1
        left = 0
        for count, color in zip(counts, colors, strict=True):
            ax.barh(i, count, left=left, color=color)
            if count:
                ax.text(left + count / 2, i, str(count), ha="center", va="center")
            left += count
    ax.set_yticks(range(10), [label(c) for c in configs])
    ax.invert_yaxis()
    ax.set_xlabel(
        "Attacked episodes (24 per setting); trace actions retained even when final A is unknown"
    )
    ax.set_title(
        "RQ3: observable target-related tool stages\n"
        "Accepted call ≠ target achieved; no matched call ≠ explicit refusal",
        fontsize=14,
    )
    fig.legend(
        handles=[
            Patch(color=color, label=name) for name, color in zip(categories, colors, strict=True)
        ],
        loc="outside lower center",
        ncol=2,
    )
    save(fig, "08-attack-stages")


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.hashsalt": "phase2-first-pass-v1",
        }
    )
    configs, rows = read_json("config-summary.json"), read_csv("episode-audit.csv")
    outcomes(configs)
    task_map(configs, rows)
    paired_effects()
    joint_outcomes(configs)
    mechanisms(configs, rows)
    costs(configs, rows)
    growth()
    attack_stages(configs)
    print("Rendered 8 figures as PNG, SVG and PDF.")


if __name__ == "__main__":
    main()
