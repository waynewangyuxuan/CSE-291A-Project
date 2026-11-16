"""Render cohort-separated formal figures with visible scope and unknown outcomes."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import shutil
import tempfile
import textwrap
from pathlib import Path

ROOT = Path("results/phase2-main-v1/analysis")
POLICIES = ("full", "truncate", "summary")
PRECISIONS = ("f16", "q8", "q4")
CONFIGS = tuple(itertools.product(PRECISIONS, POLICIES))


def task_key(row):
    return row["suite"], row["task"]


def task_names(tasks):
    return (
        ", ".join(f"{s.title()}{t.removeprefix('user_task_')}" for s, t in sorted(tasks)) or "None"
    )


def compaction_coverage(rows):
    compacted = [r for r in rows if r["compactions"] > 0]
    return {
        "tasks": len({task_key(r) for r in rows}),
        "episodes": len(rows),
        "compacted_tasks": len({task_key(r) for r in compacted}),
        "compacted_episodes": len(compacted),
    }


def coverage_label(coverage):
    return (
        f"{coverage['compacted_tasks']}/{coverage['tasks']} tasks; "
        f"{coverage['compacted_episodes']}/{coverage['episodes']} ep."
    )


def checked_score(score, rows, field):
    n = len(rows)
    success = sum(r[field] is True for r in rows)
    unknown = sum(r[field] is None for r in rows)
    expected = (
        n,
        success,
        unknown,
        success / n if n else None,
        (success + unknown) / n if n else None,
    )
    actual = tuple(score[k] for k in ("n", "success", "unknown", "lower_bound", "upper_bound"))
    if actual != expected:
        raise ValueError("Measurements/episodes disagree on outcome counts or bounds")


def plot_scope(report, episodes):
    if report["included_episodes"] != len(episodes) or report["reviewed_episodes"] != sum(
        r["review_status"] == "reviewed" for r in episodes
    ):
        raise ValueError("Measurements/episodes disagree on export coverage")
    if set(report["cohorts"]) != {r["cohort"] for r in episodes}:
        raise ValueError("Measurements/episodes disagree on cohorts")
    scopes = {}
    for cohort, data in report["cohorts"].items():
        rows = [r for r in episodes if r["cohort"] == cohort]
        tasks = {task_key(r) for r in rows}
        index = {}
        for row in rows:
            key = row["precision"], row["policy"], task_key(row), row["condition"]
            if key in index:
                raise ValueError("Duplicate episode cell")
            if type(row["compactions"]) is not int or row["compactions"] < 0:
                raise ValueError("Invalid applied compaction count")
            if any(
                row[k] is not None and type(row[k]) is not bool
                for k in ("utility", "attack_success")
            ):
                raise ValueError("Invalid binary outcome")
            index[key] = row
        expected = {
            (*c, t, condition) for c in CONFIGS for t in tasks for condition in ("clean", "attack")
        }
        if set(index) != expected or data["tasks"] != len(tasks) or data["episodes"] != len(rows):
            raise ValueError("Incomplete or mismatched cohort/configuration clean+attack pairs")
        configs = {(c["precision"], c["policy"]): c for c in data["configurations"]}
        if set(configs) != set(CONFIGS) or len(data["configurations"]) != len(CONFIGS):
            raise ValueError("Expected exactly nine configurations")
        own = {c: {t for t in tasks if index[*c, t, "clean"]["utility"] is True} for c in CONFIGS}
        reviewed = {
            c: {
                t
                for t in own[c]
                if index[*c, t, "clean"]["review_status"] == "reviewed"
                and "scorer_false_positive"
                not in {
                    index[*c, t, "clean"]["failure_category"],
                    *re.split(r"[|;]", index[*c, t, "clean"]["secondary_labels"]),
                }
            }
            for c in CONFIGS
        }
        common = set.intersection(*own.values())
        reviewed_common = set.intersection(*reviewed.values())
        matched = {}
        for c, config in configs.items():
            sensitivity = config["reviewed_clean_eligibility_sensitivity"]
            for condition in ("clean", "attack"):
                if config["context"][f"{condition}_compacted"] != sum(
                    index[*c, t, condition]["compactions"] > 0 for t in tasks
                ):
                    raise ValueError(
                        "Measurements/episodes disagree on applied compaction coverage"
                    )
            for actual, wanted in (
                (config["own_clean_tasks"], own[c]),
                (config["common_clean_tasks"], common),
                (sensitivity["eligible_tasks"], reviewed[c]),
                (sensitivity["common_tasks"], reviewed_common),
            ):
                if len(actual) != len(wanted) or set(map(tuple, actual)) != wanted:
                    raise ValueError("Measurements/episodes disagree on clean eligibility")
            for condition, field, metric in (
                ("clean", "utility", "clean_utility"),
                ("attack", "utility", "attacked_utility"),
                ("attack", "attack_success", "attack_success_all"),
            ):
                checked_score(config[metric], [index[*c, t, condition] for t in tasks], field)
            for selected, metric in (
                (own[c], config["attack_success_own_clean"]),
                (common, config["attack_success_common_clean"]),
                (reviewed[c], sensitivity["own_clean_ASR"]),
                (reviewed_common, sensitivity["common_clean_ASR"]),
            ):
                checked_score(metric, [index[*c, t, "attack"] for t in selected], "attack_success")
            clean = [index[*c, t, "clean"] for t in common]
            for metric, costs in config["common_clean_costs"].items():
                if costs["n"] != sum(r[metric] is not None for r in clean):
                    raise ValueError("Measurements/episodes disagree on matched cost counts")
            complete = sum(
                all(r[k] is not None for k in config["common_clean_costs"])
                and not r["memory_sampling_errors"]
                for r in clean
            )
            if config["common_clean_cost_coverage"] != {
                "expected": len(clean),
                "complete": complete,
            }:
                raise ValueError("Measurements/episodes disagree on matched cost coverage")
            matched[c] = {
                condition: compaction_coverage([index[*c, t, condition] for t in common])
                for condition in ("clean", "attack")
            }
        interactions = {}
        for row in data["interactions"]:
            q, p, outcome = row["precision"], row["policy"], row["outcome"]
            cells = ((q, p), (q, "full"), ("f16", p), ("f16", "full"))
            if outcome == "clean_utility":
                selected, condition, field = tasks, "clean", "utility"
            elif outcome == "attack_success_common_clean":
                selected, condition, field = common, "attack", "attack_success"
            else:
                raise ValueError("Unknown interaction outcome")
            known = {
                t
                for t in selected
                if all(index[*c, t, condition][field] is not None for c in cells)
            }
            if (row["n"], row["known_tasks"]) != (len(selected), len(known)):
                raise ValueError("Measurements/episodes disagree on interaction task selection")
            key = q, p, outcome
            if key in interactions:
                raise ValueError("Duplicate interaction")
            interactions[key] = compaction_coverage(
                [index[*c, t, condition] for t in known for c in cells]
            )
        if set(interactions) != set(
            itertools.product(
                ("q8", "q4"),
                ("truncate", "summary"),
                ("clean_utility", "attack_success_common_clean"),
            )
        ):
            raise ValueError("Expected eight interactions")
        scopes[cohort] = {
            "matched": matched,
            "interactions": interactions,
            "own_excluded": {c: own[c] - reviewed[c] for c in CONFIGS},
            "common_excluded": common - reviewed_common,
            "common_tasks": len(common),
        }
    return scopes


def interval_label(uncertainty):
    interval = uncertainty["interval_95"]
    if uncertainty["status"] == "insufficient_tasks_within_suite" and interval is None:
        return "95% CI withheld: insufficient within-suite tasks"
    if uncertainty["status"] != "estimated" or interval is None:
        raise ValueError("Invalid exported interaction CI status")
    suffix = " (degenerate)" if uncertainty["degenerate"] else " (estimated)"
    return f"95% CI [{interval[0]:+.3f}, {interval[1]:+.3f}]" + suffix


def read_stable(path):
    before = path.stat()
    blob = path.read_bytes()
    after = path.stat()
    fields = ("st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError(f"Input changed while reading: {path}")
    return blob, (hashlib.sha256(blob).hexdigest(), *(getattr(after, k) for k in fields))


def require_unchanged(states):
    for path, state in states.items():
        if not path.is_file() or read_stable(path)[1] != state:
            raise ValueError(f"Input changed; refusing figure publication: {path}")


def render(input_dir, output_dir, provisional=False):
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).absolute()
    if output_dir.is_symlink() or output_dir.resolve() in (input_dir, *input_dir.parents):
        raise ValueError("Output must not replace inputs or their ancestors")
    paths = (input_dir / "measurements.json", input_dir / "episodes.json", Path(__file__).resolve())
    blobs, states = {}, {}
    for path in paths:
        blobs[path], states[path] = read_stable(path)
    require_unchanged(states)
    report, episodes = (json.loads(blobs[p]) for p in paths[:2])
    if report["status"] != "final_reviewed" and not provisional:
        raise ValueError("Figures require final review or explicit --provisional labeling")
    scopes = plot_scope(report, episodes)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".phase2-plots-", dir=output_dir.parent) as temp:
        staged, previous = Path(temp) / "figures", Path(temp) / "previous"
        staged.mkdir()
        for cohort, data in report["cohorts"].items():
            figures(report, cohort, data, scopes[cohort], staged / cohort)
        hashes = {str(p): state[0] for p, state in states.items()}
        hashes.update(
            {
                str(output_dir / p.relative_to(staged)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(staged.glob("*/*"))
                if p.is_file()
            }
        )
        (staged / "provenance.json").write_text(
            json.dumps(
                {
                    "status": report["status"],
                    "included_episodes": report["included_episodes"],
                    "sha256": hashes,
                },
                indent=2,
            )
            + "\n"
        )
        require_unchanged(states)
        had_previous = output_dir.exists()
        if had_previous:
            output_dir.rename(previous)
        try:
            staged.rename(output_dir)
            require_unchanged(states)
        except BaseException:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            if had_previous:
                previous.rename(output_dir)
            raise
    return {
        "status": report["status"],
        "cohorts": list(report["cohorts"]),
        "output": str(output_dir),
    }


def save(fig, path):
    import matplotlib.pyplot as plt

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


def figures(report, cohort, data, scope, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    matplotlib.rcParams["svg.hashsalt"] = "phase2-main-v1"
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

    fig = plt.figure(figsize=(13, 9), layout="constrained")
    grid = fig.add_gridspec(2, 2, height_ratios=(3, 2))
    axes = [fig.add_subplot(grid[0, i]) for i in range(2)]
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
                xytext=((4, 8), (4, 20), (10, 44))[i % 3],
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
    table_ax = fig.add_subplot(grid[1, :])
    table_ax.axis("off")
    table_rows = []
    for i, c in enumerate(configs):
        coverage = scope["matched"][c["precision"], c["policy"]]
        security = c["attack_success_common_clean"]
        costs = c["common_clean_costs"]
        complete = c["common_clean_cost_coverage"]
        shown = security["n"] and not security["unknown"] and complete["complete"] == security["n"]
        table_rows.append(
            [
                f"{i + 1} {labels[i]}" + (" [omitted]" if not shown else ""),
                f"{costs['seconds']['n']} / {costs['run_aggregate_peak_rss_bytes']['n']}",
                f"{security['success']}/{security['n']}  ?{security['unknown']}",
                coverage_label(coverage["clean"]),
                coverage_label(coverage["attack"]),
            ]
        )
    table = table_ax.table(
        cellText=table_rows,
        colLabels=[
            "Arm",
            "Clean cost n\ntime / RSS",
            "Attack ASR\ntrue / n; ?unknown",
            "Applied compaction: clean",
            "Applied compaction: attacked",
        ],
        colWidths=[0.25, 0.12, 0.15, 0.24, 0.24],
        bbox=[0, 0, 1, 0.88],
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.7)
    table_ax.text(
        0.5,
        0.98,
        f"Matched set: {scope['common_tasks']} all-nine common-clean tasks. "
        "Costs use clean episodes; ASR uses their attacked partners.",
        fontsize=9,
        ha="center",
        va="top",
        transform=table_ax.transAxes,
    )
    no_compaction = not any(
        v[condition]["compacted_episodes"]
        for v in scope["matched"].values()
        for condition in ("clean", "attack")
    )
    if not scope["common_tasks"]:
        coverage_note = "No common-clean tasks; matched costs and ASR are undefined."
    elif no_compaction:
        coverage_note = (
            "No matched episodes compacted; these costs do not measure applied compression."
        )
    else:
        coverage_note = "Compaction coverage counts tasks and episodes separately by condition."
    fig.supxlabel(
        "Vertical lines: unknown utility range. Checker sensitivity is separate. "
        "RSS spans a pair, not unique physical RAM.\n"
        + coverage_note
        + " Omitted arms lack complete matched costs or known ASR.",
        fontsize=8,
    )
    save(fig, output / "03-tradeoffs")

    fig, ax = plt.subplots(figsize=(13, 9), layout="constrained")
    pairs = [(p, c) for p in ("q8", "q4") for c in ("truncate", "summary")]
    outcomes = ("clean_utility", "attack_success_common_clean")
    values = np.full((4, 2), np.nan)
    for row in data["interactions"]:
        i, j = pairs.index((row["precision"], row["policy"])), outcomes.index(row["outcome"])
        value = row["difference_in_differences"]
        if value is not None:
            values[i, j] = value
        coverage = scope["interactions"][row["precision"], row["policy"], row["outcome"]]
        condition = "clean" if row["outcome"] == "clean_utility" else "attack"
        estimate = f"DID {value:+.3f}" if value is not None else "DID unavailable"
        ax.text(
            j,
            i,
            f"{estimate}\nKnown / selected tasks: {row['known_tasks']}/{row['n']}\n"
            f"Compacted: {coverage['compacted_tasks']}/{coverage['tasks']} tasks; "
            f"{coverage['compacted_episodes']}/{coverage['episodes']} {condition} ep.\n"
            + interval_label(row["uncertainty"]),
            ha="center",
            va="center",
            fontsize=9,
            linespacing=1.6,
        )
    im = ax.imshow(values, vmin=-2, vmax=2, cmap="RdBu_r", aspect="auto")
    ax.set_xticks(range(2), ["Clean utility", "Attack success\non common-clean"])
    ax.set_yticks(range(4), [f"{p.upper()} × {c.title()}" for p, c in pairs])
    fig.colorbar(im, ax=ax, label="Difference in differences (rate units)", shrink=0.8)
    ax.set_title("Factor interaction relative to F16 / Full\n" + title, fontsize=11)
    fig.supxlabel(
        "(Quantized policy − quantized Full) − (F16 policy − F16 Full); known paired tasks only.\n"
        "Coverage: four episodes per known task, in the indicated condition; "
        "a task counts if any cell compacted.\n"
        "No compaction in selected cells means compression effects are untested; "
        "zero-width CIs do not prove equivalence.\n"
        "CI: exported 95% suite-stratified task bootstrap; "
        "unknown outcomes and seed variation are not modeled.",
        fontsize=8,
    )
    save(fig, output / "04-interactions")

    exclusions = [
        textwrap.fill(task_names(scope["own_excluded"][c["precision"], c["policy"]]), 100)
        for c in configs
    ]
    common_excluded = textwrap.fill(task_names(scope["common_excluded"]), 135)
    lines = sum(s.count("\n") + 1 for s in exclusions)
    fig = plt.figure(
        figsize=(15, 8.5 + 0.22 * lines + 0.2 * common_excluded.count("\n")), layout="constrained"
    )
    grid = fig.add_gridspec(3, 2, height_ratios=(6, 1.5 + 0.25 * lines, 0.9))
    axes = [fig.add_subplot(grid[0, 0])]
    axes.append(fig.add_subplot(grid[0, 1], sharey=axes[0]))
    colors = ("#377c9c", "#8653a0")
    for ax, official_key, sensitivity_key, name in zip(
        axes,
        ("attack_success_own_clean", "attack_success_common_clean"),
        ("own_clean_ASR", "common_clean_ASR"),
        ("Own-clean ASR", "All-nine common-clean ASR"),
        strict=True,
    ):
        for i, c in enumerate(configs):
            values = (c[official_key], c["reviewed_clean_eligibility_sensitivity"][sensitivity_key])
            for offset, color, value in zip((-0.18, 0.18), colors, values, strict=True):
                y = i + offset
                if value["n"]:
                    lower, upper = value["lower_bound"], value["upper_bound"]
                    ax.barh(y, lower, color=color, height=0.30)
                    ax.barh(y, upper - lower, left=lower, color="#d8dde3", hatch="///", height=0.30)
                    count = f"{value['success']}/{value['n']}  ?{value['unknown']}"
                else:
                    count = "n=0; undefined"
                ax.text(1.03, y, count, va="center", fontsize=8, color=color)
        ax.set_title(name, fontsize=11)
        ax.set_xlim(0, 1.4)
        ax.set_xticks([0, 0.5, 1], ["0", "50%", "100%"])
        ax.grid(axis="x", alpha=0.2)
    axes[0].set_yticks(range(len(configs)), labels)
    axes[0].invert_yaxis()
    axes[1].tick_params(labelleft=False)
    table_ax = fig.add_subplot(grid[1, :])
    table_ax.axis("off")
    table = table_ax.table(
        cellText=[[label, excluded] for label, excluded in zip(labels, exclusions, strict=True)],
        colLabels=["Arm", "Tasks excluded from its official own-clean set"],
        colWidths=[0.23, 0.77],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    for i, excluded in enumerate(exclusions, 1):
        for j in range(2):
            table[i, j].set_height(table[i, j].get_height() * (excluded.count("\n") + 1))
    table_ax.set_title(
        "Excluded from the official all-nine common-clean set:\n" + common_excluded,
        fontsize=10,
        pad=12,
    )
    fig.suptitle("Checker sensitivity: clean eligibility — " + title, fontsize=13)
    note_ax = fig.add_subplot(grid[2, :])
    note_ax.axis("off")
    note_ax.legend(
        handles=[
            Patch(color=colors[0], label="Official clean-success eligibility"),
            Patch(color=colors[1], label="Reviewed clean success; no false-positive label"),
            Patch(facecolor="#d8dde3", hatch="///", label="Unknown-outcome bounds (not CI)"),
        ],
        loc="upper center",
        ncol=3,
        fontsize=9,
    )
    note_ax.text(
        0.5,
        0.46,
        "Counts: true / selected attacked episodes; ? = unknown. "
        "Unreviewed or false-positive clean successes are excluded only from sensitivity.\n"
        "Official scores are unchanged. "
        "Attacked false-positive labels alone do not remove clean eligibility.\n"
        "This is clean-eligibility sensitivity; "
        "whole-flagged-task exclusion and paired sensitivity intervals are not shown.",
        fontsize=8,
        ha="center",
        va="top",
        transform=note_ax.transAxes,
    )
    save(fig, output / "06-checker-sensitivity")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provisional", action="store_true")
    parser.add_argument("--input-dir", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "figures")
    args = parser.parse_args()
    print(json.dumps(render(args.input_dir, args.output_dir, args.provisional)))
