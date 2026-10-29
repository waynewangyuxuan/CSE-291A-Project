"""Reproducible offline figures for the six research investigation products."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/research-investigation-v1"
FIRST = ROOT / "results/prompt-defense-first-wave-v1"
RAW = ROOT / "artifacts/prompt-defense-first-wave-v1"
FOLLOW_RAW = ROOT / "artifacts/prompt-defense-seed29-diagnostics-v1"
ARMS = ("default", "source_boundary_v1")
ARM_NAMES = ("Default", "Reminder")
SUITES = ("banking", "slack", "workspace", "travel")
BLUE, ORANGE, GRAY, INK = "#2563a6", "#d28a32", "#bdc4cb", "#233345"
STATES = ("U+ / A−", "U+ / A+", "U− / A−", "U− / A+", "Unknown")
FIGURES = []


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_raw(path):
    candidate = path if path.exists() else path.with_suffix(path.suffix + ".gz")
    data = (
        gzip.decompress(candidate.read_bytes())
        if candidate.suffix == ".gz"
        else candidate.read_bytes()
    )
    return data


def save(fig, name, title, data, reading):
    fig.savefig(OUT / f"{name}.png", dpi=170, facecolor="white")
    fig.savefig(OUT / f"{name}.svg", facecolor="white", metadata={"Date": None})
    FIGURES.append(
        {
            "name": name,
            "title": title,
            "reading": reading,
            "data": data,
            "png_sha256": sha(OUT / f"{name}.png"),
            "svg_sha256": sha(OUT / f"{name}.svg"),
        }
    )
    plt.close(fig)


def footer(fig, text):
    fig.text(0.02, 0.018, text, fontsize=8, color="#536273", va="bottom")


def pairs(rows):
    result = defaultdict(dict)
    for row in rows:
        assert row["arm"] not in result[row["pair_id"]]
        result[row["pair_id"]][row["arm"]] = row
    assert all(set(p) == set(ARMS) for p in result.values())
    return result


def protect_inputs():
    path = OUT / "protected-inputs.json"
    if not path.exists():
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        records = []
        for relative in tracked:
            file = ROOT / relative
            if relative and file.is_file():
                records.append(
                    {"path": relative, "bytes": file.stat().st_size, "sha256": sha(file)}
                )
        dump(
            path,
            {
                "scope": (
                    "Previously tracked files at investigation start; analysis adds new files only."
                ),
                "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
                .decode()
                .strip(),
                "files": records,
            },
        )
    inventory = load(path)
    for row in inventory["files"]:
        file = ROOT / row["path"]
        assert file.stat().st_size == row["bytes"] and sha(file) == row["sha256"], row["path"]
    return {"passed": True, "files": len(inventory["files"]), "inventory_sha256": sha(path)}


def overall(m):
    keys = [
        ("Clean U ↑", "clean_utility"),
        ("Attacked U ↑", "attack_utility"),
        ("Attack A ↓", "attack_success"),
        ("A on common-clean tasks ↓", "common_clean_attack_success"),
    ]
    fig, axs = plt.subplots(1, 3, figsize=(14, 5.3), gridspec_kw={"width_ratios": [1.6, 1, 1]})
    fig.suptitle(
        "1  Prompt reminder effects: estimates and uncertainty",
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    data = []
    for i, (label, key) in enumerate(keys):
        y = 3 - i
        armkey = (
            "attack_success_common_clean_eligible" if key == "common_clean_attack_success" else key
        )
        default, reminder = (m["arms"][a][armkey] for a in ARMS)
        effect = m["paired"][key]
        text = (
            f"{label}\nDefault {default['successes']}/{default['denominator']}  "
            f"→  Reminder {reminder['successes']}/{reminder['denominator']}"
            f"    unknown {default['unknown_outcomes']} / {reminder['unknown_outcomes']}"
        )
        axs[0].text(0, y, text, va="center", fontsize=10)
        lo, hi = np.array(effect["ci95"]) * 100
        delta = effect["delta"] * 100
        axs[1].plot([lo, hi], [y, y], color=BLUE, lw=3)
        axs[1].plot(delta, y, "o", color=BLUE, ms=7)
        axs[1].text(delta, y + 0.20, f"{delta:+.2f}", ha="center", fontsize=9)
        lower, upper = np.array(effect["unknown_bounds"]) * 100
        axs[2].plot([lower, upper], [y, y], color="#7c8795", lw=7, solid_capstyle="butt")
        if lower == upper:
            axs[2].plot(lower, y, "D", color="#7c8795", ms=6)
        axs[2].text(
            (lower + upper) / 2, y + 0.20, f"[{lower:+.2f}, {upper:+.2f}]", ha="center", fontsize=9
        )
        data.append({"metric": key, "default": default, "reminder": reminder, "paired": effect})
    axs[0].set(xlim=(0, 1), ylim=(-0.5, 3.65))
    axs[0].axis("off")
    for ax in axs[1:]:
        ax.axvline(0, color="#b24747", lw=1, ls="--")
        ax.set(
            xlim=(-11, 8),
            ylim=(-0.5, 3.65),
            yticks=[],
            xlabel="Reminder − default (percentage points)",
        )
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.grid(axis="x", alpha=0.18)
    axs[1].set_title("Paired 95% cluster interval", fontsize=11)
    axs[2].set_title(
        "Possible range from unknown outcomes\n(not a confidence interval)", fontsize=10
    )
    footer(
        fig,
        "Up to 60 task clusters (common-clean: 25); seed 17; official U/A. ↑ "
        "higher is favorable; ↓ lower is favorable. 2,000 suite-stratified "
        "task-cluster bootstrap draws.",
    )
    fig.subplots_adjust(left=0.025, right=0.98, top=0.80, bottom=0.17, wspace=0.12)
    save(
        fig,
        "01-overall-effects",
        "总体效果与不确定性",
        data,
        "U上升和A下降是有利方向；置信区间与未知结果范围分开阅读。区间触及零或未知范围跨零时，不能宣称稳定改善。",
    )


def task_map(rows, features, suite=None):
    wanted = [s for s in SUITES if suite is None or s == suite]
    task_keys = sorted(
        {(r["suite"], r["user_task_id"]) for r in rows if r["suite"] in wanted},
        key=lambda k: (
            SUITES.index(k[0]),
            int(features[k]["ground_truth_tool_count"]),
            int(k[1].split("_")[-1]),
        ),
    )
    selection = load(ROOT / "configs/phase1/task-manifest.json")["selection"]
    cells, dots, metadata = [], [], []
    rowlookup = {
        (
            r["suite"],
            r["user_task_id"],
            r["condition"],
            r["attack_style"],
            r["injection_task_id"],
            r["arm"],
        ): r
        for r in rows
    }
    for y, key in enumerate(task_keys):
        s, task = key
        goals = selection[s]["injection_task_ids"]
        conditions = [("clean", None, None)] + [
            ("attack", style, goal)
            for style in ("direct_append", "ignore_previous_append")
            for goal in goals
        ]
        values = []
        for condition, style, goal in conditions:
            for arm in ARMS:
                r = rowlookup[(s, task, condition, style, goal, arm)]
                values.append(-1 if r["utility"] is None else int(r["utility"]))
                if r["attack_success"] is True:
                    dots.append((len(values) - 1, y))
        cells.append(values)
        f = features[key]
        tools = json.loads(f["reference_tool_functions_in_source_order"])
        short = ", ".join(dict.fromkeys(tools))
        if len(short) > 48:
            short = short[:45] + "…"
        metadata.append(
            {
                "suite": s,
                "task": task,
                "GT": int(f["ground_truth_tool_count"]),
                "intent": f["task_intent"],
                "tools": tools,
                "values": values,
            }
        )
    contracts = {
        (r["suite"], r["user_task_id"]): r for r in load(OUT / "task-contracts.json")["tasks"]
    }
    for item in metadata:
        c = contracts[(item["suite"], item["task"])]
        item.update(theme=c["theme"], operation=c["operation"])
    n = len(task_keys)
    fig, ax = plt.subplots(figsize=(14, max(5.6, n * 0.29 + 2.2)))
    fig.suptitle(
        "2  Task map" + (f" — {suite.title()}" if suite else " — all 60 tasks"),
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    ax.imshow(cells, cmap=ListedColormap([GRAY, ORANGE, BLUE]), vmin=-1, vmax=1, aspect="auto")
    if dots:
        ax.scatter(*zip(*dots, strict=True), c="#111827", s=22, marker="D", zorder=3)
    labels = []
    for item in metadata:
        short = ", ".join(dict.fromkeys(item["tools"]))
        if len(short) > 48:
            short = short[:45] + "…"
        labels.append(
            f"{item['suite'][:1].upper()}{int(item['task'].split('_')[-1]):02}  "
            f"GT{item['GT']}  {'action' if item['intent'] == 'explicit_action' else 'info'}  "
            f"{short}"
        )
    ax.set_yticks(range(n), labels, fontsize=8)
    ax.set_xticks(range(10), ["D", "R"] * 5, fontsize=10)
    ax.xaxis.tick_top()
    for x, label in [
        (0.5, "Clean"),
        (2.5, "Direct / goal 1"),
        (4.5, "Direct / goal 2"),
        (6.5, "Ignore / goal 1"),
        (8.5, "Ignore / goal 2"),
    ]:
        ax.annotate(
            label,
            (x, 1),
            xycoords=ax.get_xaxis_transform(),
            xytext=(0, 32),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_xticks(np.arange(-0.5, 10, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n, 1), minor=True)
    ax.grid(which="minor", color="white", lw=0.7)
    ax.tick_params(which="minor", length=0)
    for x in (1.5, 3.5, 5.5, 7.5):
        ax.axvline(x, color=INK, lw=1)
    for y in range(1, n):
        if task_keys[y][0] != task_keys[y - 1][0]:
            ax.axhline(y - 0.5, color=INK, lw=2.2)
    legend = [
        Patch(color=BLUE, label="U=True"),
        Patch(color=ORANGE, label="U=False"),
        Patch(color=GRAY, label="U unknown"),
        Line2D(
            [],
            [],
            marker="D",
            color="none",
            markerfacecolor="#111827",
            markersize=5,
            label="A=True (exact goal)",
        ),
    ]
    fig.legend(
        handles=legend,
        loc="lower center",
        bbox_to_anchor=(0.58, 0.028),
        ncol=4,
        fontsize=9,
        frameon=False,
    )
    footer(
        fig,
        "D=default, R=reminder. Rows grouped by suite then reference tool count. "
        "Reference steps are not minimal steps. U/A are official scores; U is "
        "not L.",
    )
    fig.subplots_adjust(
        left=0.42, right=0.98, top=1 - 1.4 / fig.get_figheight(), bottom=1.05 / fig.get_figheight()
    )
    name = "02-task-map" + (f"-{suite}" if suite else "")
    save(
        fig,
        name,
        "60任务地图" if suite is None else f"{suite}任务地图",
        {"rows": metadata, "attack_success_markers": dots},
        "同一条件D/R相邻；底色读U，黑菱形读A。灰格是未知。先在suite内部找模式，跨suite的工具和来源差异不能直接归因为原因。",
    )


def state(row):
    if row["utility"] is None or row["attack_success"] is None:
        return 4
    return {(True, False): 0, (True, True): 1, (False, False): 2, (False, True): 3}[
        (row["utility"], row["attack_success"])
    ]


def heat(ax, mat, xs, ys, title, vmax=None):
    ax.imshow(mat, cmap="Blues", vmin=0, vmax=vmax or max(np.max(mat), 1), aspect="auto")
    for i in range(len(ys)):
        for j in range(len(xs)):
            v = int(mat[i][j])
            ax.text(
                j,
                i,
                str(v),
                va="center",
                ha="center",
                fontsize=11,
                color="white" if v > np.max(mat) * 0.55 else INK,
            )
    ax.set_xticks(range(len(xs)), xs, fontsize=9)
    ax.set_yticks(range(len(ys)), ys, fontsize=9)
    ax.set_title(title, fontsize=12)
    ax.tick_params(length=0)


def joint(rows):
    attacks = [r for r in rows if r["condition"] == "attack"]
    ps = pairs(attacks)
    transition = np.zeros((5, 5), dtype=int)
    for p in ps.values():
        transition[state(p[ARMS[0]]), state(p[ARMS[1]])] += 1
    fig, axs = plt.subplots(1, 3, figsize=(16, 6), gridspec_kw={"width_ratios": [1, 1, 1.7]})
    fig.suptitle(
        "3  Official joint outcomes and paired transitions",
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    marginals = {}
    for ax, arm, name in zip(axs[:2], ARMS, ARM_NAMES, strict=True):
        subset = [r for r in attacks if r["arm"] == arm]
        mat = np.array(
            [
                [
                    sum(r["utility"] is u and r["attack_success"] is a for r in subset)
                    for a in (False, True)
                ]
                for u in (True, False)
            ]
        )
        unknown = sum(state(r) == 4 for r in subset)
        heat(
            ax,
            mat,
            ["A=False", "A=True"],
            ["U=True", "U=False"],
            f"{name}: n=240\nunknown={unknown}",
        )
        marginals[arm] = {"counts": mat.tolist(), "unknown": unknown}
    heat(axs[2], transition, STATES, STATES, "Default → reminder: all 240 pairs")
    axs[2].set(xlabel="Reminder state", ylabel="Default state")
    axs[2].tick_params(axis="x", labelrotation=30)
    blocked = [
        p
        for p in ps.values()
        if p[ARMS[0]]["attack_success"] is True and p[ARMS[1]]["attack_success"] is False
    ]
    categories = Counter(f"{p[ARMS[0]]['utility']}->{p[ARMS[1]]['utility']}" for p in blocked)
    fig.text(
        0.06,
        0.085,
        "13 A=True → False pairs: U stayed True in 3; stayed False in 10; U=False → True in 0.",
        fontsize=10,
        color=INK,
    )
    footer(
        fig,
        "U = unchanged official utility, A = exact attack goal. A=False is not "
        "comprehensive safety. Unknown rows are retained. No new L score.",
    )
    fig.subplots_adjust(left=0.06, right=0.98, top=0.78, bottom=0.23, wspace=0.35)
    save(
        fig,
        "03-joint-outcomes",
        "U×A与配对状态转移",
        {
            "marginals": marginals,
            "transition": transition.tolist(),
            "A_true_to_false_pairs": len(blocked),
            "their_U_transitions": dict(categories),
        },
        "右图从行的默认状态读到列的防御状态。U−/A+→U+/A−与U−/A+→U−/A−对用户价值不同；灰区/未知不能当False。",
    )

    controls = {
        (r["suite"], r["user_task_id"], r["arm"]): r for r in rows if r["condition"] == "clean"
    }
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 5.2))
    data = {}
    for ax, arm, label in zip(axs, ARMS, ARM_NAMES, strict=True):
        mat = np.zeros((3, 3), dtype=int)
        ix = {True: 0, False: 1, None: 2}
        for r in attacks:
            if r["arm"] == arm:
                c = controls[(r["suite"], r["user_task_id"], arm)]
                mat[ix[c["utility"]], ix[r["utility"]]] += 1
        heat(ax, mat, ["U=True", "U=False", "Unknown"], ["U=True", "U=False", "Unknown"], label)
        ax.set(xlabel="Attacked U", ylabel="Same-task clean U")
        data[arm] = mat.tolist()
    fig.suptitle(
        "Clean → attacked official utility", x=0.02, ha="left", fontweight="bold", fontsize=15
    )
    footer(
        fig,
        "Each clean control is reused for four attacks. Cells are attack counts "
        "(240 per arm), not independent clean tasks.",
    )
    fig.subplots_adjust(top=0.80, bottom=0.18, wspace=0.35)
    save(
        fig,
        "03-clean-to-attack",
        "从clean到attack的任务变化",
        data,
        "行是同任务clean结果，列是attack结果；True→False是观察到的任务退化。每个clean被四个攻击复用，不能当作四个独立任务。",
    )


def costs(rows, supp):
    ps = pairs(rows)
    fig, axs = plt.subplots(1, 3, figsize=(15, 5.6), gridspec_kw={"width_ratios": [1, 1, 1.1]})
    fig.suptitle("6  Costs of both prompt arms", x=0.02, ha="left", fontsize=16, fontweight="bold")
    points = []
    for p in ps.values():
        d, r = (p[a] for a in ARMS)
        error = d["status"] != "completed" or r["status"] != "completed"
        color = (
            "#b34b48"
            if error
            else (BLUE if d["utility"] is True and r["utility"] is True else "#8594a4")
        )
        marker = "x" if error else "o"
        axs[0].scatter(
            d["duration_seconds"],
            r["duration_seconds"],
            c=color,
            marker=marker,
            s=26,
            alpha=0.6,
            linewidths=0.8,
        )
        x, y = (v["usage"].get("prompt_tokens", 0) for v in (d, r))
        axs[1].scatter(x, y, c=color, marker=marker, s=26, alpha=0.6, linewidths=0.8)
        points.append(
            {
                "pair_id": d["pair_id"],
                "default_seconds": d["duration_seconds"],
                "reminder_seconds": r["duration_seconds"],
                "default_available_prompt_tokens": x,
                "reminder_available_prompt_tokens": y,
                "any_error": error,
            }
        )
    axs[0].plot([0, 620], [0, 620], "--", color=INK, lw=1)
    axs[0].set(
        xlim=(0, 620),
        ylim=(0, 620),
        xlabel="Default wall time (s)",
        ylabel="Reminder wall time (s)",
        title="300 pairs, including errors",
    )
    axs[1].set(
        xscale="log",
        yscale="log",
        xlabel="Default returned prompt tokens",
        ylabel="Reminder returned prompt tokens",
        title="Available usage only",
    )
    limits = [
        min(
            min(p["default_available_prompt_tokens"], p["reminder_available_prompt_tokens"])
            for p in points
        )
        * 0.7,
        max(
            max(p["default_available_prompt_tokens"], p["reminder_available_prompt_tokens"])
            for p in points
        )
        * 1.4,
    ]
    axs[1].plot(limits, limits, "--", color=INK, lw=1)
    axs[1].set(xlim=limits, ylim=limits)
    for i, (key, _label) in enumerate(
        [
            ("duration_seconds", "Wall time"),
            ("native_prefill_seconds", "Native prefill"),
            ("native_decode_seconds", "Native decode"),
        ]
    ):
        v = supp["paired_costs"]["all"][key]
        y = 2 - i
        axs[2].plot(v["ci95"], [y, y], color=BLUE, lw=3)
        axs[2].plot(v["mean_delta_defense_minus_default"], y, "o", color=BLUE)
        axs[2].text(
            v["mean_delta_defense_minus_default"],
            y + 0.17,
            f"{v['mean_delta_defense_minus_default']:+.2f}s",
            fontsize=9,
            ha="center",
        )
    axs[2].set(
        yticks=[2, 1, 0],
        yticklabels=["Wall", "Prefill", "Decode"],
        xlabel="Mean paired change (s)",
        ylim=(-0.5, 2.6),
        title="Task-cluster 95% intervals",
    )
    axs[2].axvline(0, color="#b34b48", ls="--", lw=1)
    for ax in axs:
        ax.grid(alpha=0.15)
        ax.spines[["top", "right"]].set_visible(False)
    fig.legend(
        handles=[
            Line2D([], [], marker="o", linestyle="none", color=BLUE, label="Both U=True"),
            Line2D(
                [], [], marker="o", linestyle="none", color="#8594a4", label="Other normal endings"
            ),
            Line2D(
                [],
                [],
                marker="x",
                linestyle="none",
                color="#b34b48",
                label="At least one terminal error",
            ),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.075),
        ncol=3,
        frameon=False,
        fontsize=9,
    )
    footer(
        fig,
        "Above diagonal = reminder costs more; below = less. Fixed prefix adds "
        "100 initial tokens. Failed-request compute is unknown; subsets do not "
        "establish causal efficiency.",
    )
    fig.subplots_adjust(left=0.06, right=0.98, top=0.79, bottom=0.28, wspace=0.38)
    save(
        fig,
        "06-cost-pairs",
        "成对耗时与tokens成本",
        {"pairs": points, "paired_costs": supp["paired_costs"]["all"]},
        "先读对角线与红色错误，再看区间。快速结束需结合U/A和实际动作；可用tokens不包含没有返回usage的失败计算。",
    )


def request_series(folder):
    records = [
        json.loads(line) for line in read_raw(folder / "requests.jsonl").decode().splitlines()
    ]
    reqs = {r["index"]: r for r in records if r["event"] == "request"}
    start = min(r["wall_time"] for r in reqs.values())
    series = []
    for event in records:
        if event["event"] not in {"response", "request_error"}:
            continue
        req = reqs[event["index"]]
        tokens = (event.get("response", {}).get("usage") or {}).get("prompt_tokens")
        origin = "returned_usage" if tokens is not None else "unknown"
        if tokens is None:
            error = event.get("message", "")
            found = re.search(r"(?:input tokens|prompt tokens)[^0-9]{0,12}([0-9]+)", error, re.I)
            if not found:
                found = re.search(r"([0-9]+)\s*(?:input tokens|tokens)\b", error, re.I)
            if found:
                tokens, origin = int(found.group(1)), "server_rejected_input_count"
        series.append(
            {
                "request": event["index"] + 1,
                "prompt_tokens": tokens,
                "token_source": origin,
                "elapsed_from_first_request": req["wall_time"] - start + event["latency_seconds"],
                "event": event["event"],
                "error": event.get("message"),
            }
        )
    return series


def trajectories(rows):
    follow = load(FOLLOW_RAW / "manifest.json")
    fig, axs = plt.subplots(2, 2, figsize=(13, 9.3))
    fig.suptitle(
        "6  Retrieval history and budget trajectories",
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    data = []
    for col, task in enumerate(("user_task_27", "user_task_35")):
        for seed, cohort, base in [(17, rows, RAW), (29, follow["episodes"], FOLLOW_RAW)]:
            for arm in ARMS:
                entry = next(
                    r
                    for r in cohort
                    if r["suite"] == "workspace"
                    and r["user_task_id"] == task
                    and r["condition"] == "clean"
                    and r["arm"] == arm
                )
                series = request_series(base / "episodes" / entry["episode_id"])
                label = f"{'D' if arm == 'default' else 'R'} seed{seed}"
                color = BLUE if arm == "default" else ORANGE
                style = "-" if seed == 17 else "--"
                xx = [p["request"] for p in series]
                yy = [np.nan if p["prompt_tokens"] is None else p["prompt_tokens"] for p in series]
                axs[0, col].plot(xx, yy, style, color=color, marker="o", ms=3, label=label)
                axs[1, col].plot(
                    xx,
                    [p["elapsed_from_first_request"] for p in series],
                    style,
                    color=color,
                    marker="o",
                    ms=3,
                )
                for p in series:
                    if p["event"] == "request_error":
                        axs[1, col].scatter(
                            p["request"],
                            p["elapsed_from_first_request"],
                            c=color,
                            marker="x",
                            s=70,
                            lw=2,
                            zorder=6,
                        )
                        if p["prompt_tokens"] is not None:
                            axs[0, col].scatter(
                                p["request"],
                                p["prompt_tokens"],
                                c=color,
                                marker="x",
                                s=65,
                                lw=2,
                                zorder=6,
                            )
                        if p["prompt_tokens"] is None:
                            axs[0, col].annotate(
                                "input ? / error",
                                (p["request"], 0.94),
                                xycoords=("data", "axes fraction"),
                                fontsize=8,
                                rotation=90,
                                ha="right",
                                va="top",
                                color=color,
                            )
                data.append(
                    {
                        "task": task,
                        "seed": seed,
                        "arm": arm,
                        "episode_id": entry["episode_id"],
                        "series": series,
                    }
                )
        axs[0, col].axhline(32768, color="#b34b48", ls=":", label="32,768 context")
        axs[1, col].axhline(600, color="#b34b48", ls=":")
        axs[0, col].set_title(f"Workspace {task.split('_')[-1]}", fontsize=12)
        axs[0, col].set(ylabel="Input tokens", xlabel="Request index", ylim=(0, 38000))
        axs[1, col].set(
            ylabel="Elapsed since first request (s)", xlabel="Request index", ylim=(0, 640)
        )
        axs[0, col].legend(fontsize=8, frameon=False, ncol=2)
        for ax in axs[:, col]:
            ax.grid(alpha=0.15)
            ax.spines[["top", "right"]].set_visible(False)
    footer(
        fig,
        "Seed29 is a selected diagnostic, not extra first-wave data. x marks "
        "request errors; rejected input counts come from server errors. Missing "
        "usage stays unknown. WS27 seed29 U=True still has a wrong answer.",
    )
    fig.subplots_adjust(top=0.89, bottom=0.10, hspace=0.38, wspace=0.24)
    save(
        fig,
        "06-trajectory-growth",
        "长尾任务的上下文与时间增长",
        data,
        "实线seed17，虚线定向补跑seed29；×是错误。终止更快不等于答案正确，动作已发生也不等于官方已完成评分。缺失tokens不画成0。",
    )


def stages():
    path = OUT / "attack-stages.json"
    if not path.exists():
        return False
    obj = load(path)
    rows = obj if isinstance(obj, list) else obj.get("records", [])
    if not rows:
        raise ValueError("Unknown attack stage schema")
    key = "stage_summary" if "stage_summary" in rows[0] else "stage"
    order = [
        "no_submitted_exposure",
        "exposed_no_rule_matched_target_action",
        "emitted_parameter_matched_action_not_executed",
        "rejected_parameter_matched_action",
        "accepted_parameter_matched_action_A_unknown",
        "accepted_parameter_matched_action_A_false",
        "official_goal_achieved",
    ]
    present = {r[key] for r in rows}
    order = [s for s in order if s in present] + sorted(present - set(order))
    colors = ["#c7cdd3", "#8fa8ba", "#cbb978", "#cf8261", "#9c8eaa", "#7a71a1", "#b54e58"][
        : len(order)
    ]
    if len(colors) < len(order):
        colors = plt.cm.tab20(np.linspace(0, 1, len(order)))
    fig, axs = plt.subplots(1, 2, figsize=(18, 7.2), gridspec_kw={"width_ratios": [1.6, 1]})
    fig.suptitle(
        "4  Observable attack-action stages", x=0.02, ha="left", fontsize=16, fontweight="bold"
    )
    counts = {}
    for i, arm in enumerate(ARMS):
        subset = [r for r in rows if r["arm"] == arm]
        count = Counter(r[key] for r in subset)
        counts[arm] = dict(count)
        left = 0
        for stage, color in zip(order, colors, strict=True):
            n = count[stage]
            axs[0].barh(1 - i, n, left=left, color=color, height=0.5)
            if n >= 8:
                axs[0].text(
                    left + n / 2,
                    1 - i,
                    str(n),
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if stage == order[-1] else INK,
                )
            left += n
        assert left == 240
    axs[0].set(
        yticks=[1, 0],
        yticklabels=ARM_NAMES,
        xlabel="Episodes (240 per arm)",
        xlim=(0, 240),
        ylim=(-0.5, 1.7),
    )
    axs[0].spines[["top", "right"]].set_visible(False)
    short = {
        "no_submitted_exposure": "No submitted exposure",
        "exposed_no_rule_matched_target_action": "Exposed; no rule-matched target action",
        "emitted_parameter_matched_action_not_executed": "Matched call emitted; not executed",
        "rejected_parameter_matched_action": "Matched call rejected",
        "accepted_parameter_matched_action_A_unknown": "Matched call accepted; A unknown",
        "accepted_parameter_matched_action_A_false": "Matched call accepted; A=False",
        "official_goal_achieved": "Official A=True",
    }
    axs[0].legend(
        handles=[
            Patch(color=c, label=short.get(s, s.replace("_", " ")))
            for s, c in zip(order, colors, strict=True)
        ],
        loc="upper left",
        bbox_to_anchor=(0, -0.13),
        fontsize=9,
        frameon=False,
    )
    cross = np.zeros((len(order), 3), dtype=int)
    for r in rows:
        a = r.get("attack_success", r.get("official_A", r.get("official_attack_success")))
        cross[order.index(r[key]), {True: 0, False: 1, None: 2}[a]] += 1
    heat(
        axs[1],
        cross,
        ["A=True", "A=False", "Unknown"],
        [short.get(s, s) for s in order],
        "Same stage, different official outcomes",
    )
    axs[1].yaxis.tick_right()
    axs[1].tick_params(axis="y", labelsize=8)
    footer(
        fig,
        "Frozen parameter signatures, post hoc descriptive categories. Accepted "
        "includes partial actions and a quoted link; manual review is separate. "
        "No match does not prove ignore. Errors keep A unknown.",
    )
    fig.subplots_adjust(left=0.06, right=0.77, top=0.82, bottom=0.35, wspace=0.30)
    save(
        fig,
        "04-attack-stages",
        "攻击动作阶段与官方结果",
        {
            "counts": counts,
            "stage_order": order,
            "stage_by_A": cross.tolist(),
            "annotation_sha256": sha(path),
        },
        "每段是互斥的可观察阶段分类，不是心理过程或因果漏斗。accepted是参数签名候选，包含部分/错目标动作及引述；须看独立语义复核。没有匹配动作不等于识别并忽略。最终A未知独立保留。",
    )
    return True


def visible_stance():
    path = OUT / "attack-visible-stance-annotations.json"
    if not path.exists():
        return False
    obj = load(path)
    rows = obj["records"]
    assert len(rows) == 480
    order = ["explicit_refusal", "verbal_adoption", "no_explicit_stance", "ambiguous"]
    labels = [
        "Explicit decline/refusal",
        "Verbal commitment or claimed action",
        "No explicit stance",
        "Ambiguous",
    ]
    colors = ["#5c9378", "#c57b56", "#a7b5c5", "#b1a3bd"]
    fig, axs = plt.subplots(1, 2, figsize=(16, 6), gridspec_kw={"width_ratios": [1.4, 1]})
    fig.suptitle(
        "4  Visible language and actual goal outcomes",
        x=0.02,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    counts = {}
    for i, arm in enumerate(ARMS):
        count = Counter(r["stance"] for r in rows if r["arm"] == arm)
        counts[arm] = dict(count)
        left = 0
        for key, color in zip(order, colors, strict=True):
            n = count[key]
            axs[0].barh(1 - i, n, left=left, color=color, height=0.5)
            if n >= 8:
                axs[0].text(left + n / 2, 1 - i, str(n), ha="center", va="center", fontsize=10)
            left += n
        assert left == 240
    axs[0].set(
        yticks=[1, 0],
        yticklabels=ARM_NAMES,
        xlim=(0, 240),
        ylim=(-0.5, 1.6),
        xlabel="Episodes (240 per arm)",
    )
    axs[0].spines[["top", "right"]].set_visible(False)
    axs[0].legend(
        handles=[Patch(color=c, label=label) for c, label in zip(colors, labels, strict=True)],
        loc="upper left",
        bbox_to_anchor=(0, -0.14),
        frameon=False,
        fontsize=9,
        ncol=2,
    )
    cross = np.zeros((4, 3), dtype=int)
    for row in rows:
        cross[order.index(row["stance"]), {True: 0, False: 1, None: 2}[row["attack_success"]]] += 1
    heat(
        axs[1],
        cross,
        ["A=True", "A=False", "Unknown"],
        labels,
        "Both arms: visible statement × official A",
    )
    axs[1].yaxis.tick_right()
    axs[1].tick_params(axis="y", labelsize=9)
    fig.text(
        0.06,
        0.18,
        "10 of 11 decline/refusal labels follow actual target-site visits.\n"
        "The remaining one clarifies a quoted TODO; it is not an observed attack action.\n"
        "11 verbal commitments have no parameter-matched call; 13 A=True have no explicit stance.",
        fontsize=9,
        va="top",
    )
    footer(
        fig,
        "All 480 episodes / 457 text messages read. Unblinded agent coding, not a validated judge. "
        "No explicit stance is not ignore; words are not execution evidence.",
    )
    fig.subplots_adjust(left=0.06, right=0.79, top=0.81, bottom=0.36, wspace=0.33)
    save(
        fig,
        "04-visible-stance",
        "可见表态与实际攻击目标",
        {
            "counts": counts,
            "stance_by_A": cross.tolist(),
            "stance_order": order,
            "annotations_sha256": sha(path),
            "decisions_sha256": obj["decisions_sha256"],
        },
        "左图读文字立场，右图核对实际A；拒绝可以发生在执行之后，口头承诺也可以没有调用。"
        "这些是非盲agent语义标注，不是模型内心判断。",
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    guard = protect_inputs()
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelcolor": INK,
            "text.color": INK,
            "axes.edgecolor": "#8190a0",
            "svg.fonttype": "none",
            "svg.hashsalt": "research-investigation-v1",
        }
    )
    rows = load(FIRST / "episodes.json")
    assert len(rows) == 600 and len(pairs(rows)) == 300
    features = {
        (r["suite"], r["user_task_id"]): r
        for r in csv.DictReader(
            (ROOT / "results/phase1/behavior-understanding/task-features.csv").open()
        )
    }
    m = load(FIRST / "measurements.json")["overall"]
    supp = load(FIRST / "supplementary-measurements.json")
    overall(m)
    task_map(rows, features)
    for suite in SUITES:
        task_map(rows, features, suite)
    joint(rows)
    costs(rows, supp)
    trajectories(rows)
    ready = stages()
    stance_ready = visible_stance()
    if args.complete:
        assert ready and stance_ready, "Stage and visible-stance annotations are required"
    final_guard = protect_inputs()
    dump(
        OUT / "figure-manifest.json",
        {
            "schema_version": 1,
            "plotting_runtime": matplotlib.__version__,
            "source_sha256": sha(__file__),
            "primary_inputs": {
                str(p.relative_to(ROOT)): sha(p)
                for p in [
                    FIRST / "episodes.json",
                    FIRST / "measurements.json",
                    FIRST / "supplementary-measurements.json",
                ]
            },
            "analysis_inputs": {
                str(p.relative_to(ROOT)): sha(p)
                for p in [
                    OUT / "task-contracts.json",
                    ROOT / "results/phase1/behavior-understanding/task-features.csv",
                    OUT / "attack-stages.json",
                    OUT / "attack-visible-stance-annotations.json",
                    FOLLOW_RAW / "manifest.json",
                ]
            },
            "protected_inputs": guard,
            "post_guard": final_guard,
            "stage_figure_ready": ready,
            "stance_figure_ready": stance_ready,
            "figures": FIGURES,
        },
    )
    print(
        json.dumps(
            {
                "figures": len(FIGURES),
                "stage_ready": ready,
                "protected_files": guard["files"],
                "output": str(OUT),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
