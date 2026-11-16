"""Plot coverage must describe the selected evidence without changing statistical estimates."""

import copy
import hashlib
import importlib
import itertools
import json
import runpy
from pathlib import Path

import pytest


@pytest.fixture
def plotter(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("plot_phase2_matrix")


@pytest.fixture
def bundle(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    analysis = runpy.run_path(str(scripts / "analyze_phase2_matrix.py"))

    def build(rows):
        cohorts = {}
        for cohort in sorted({r["cohort"] for r in rows}):
            selected = [r for r in rows if r["cohort"] == cohort]
            effects, interactions = analysis["comparisons"](selected)
            cohorts[cohort] = {
                "episodes": len(selected),
                "tasks": len({(r["suite"], r["task"]) for r in selected}),
                "configurations": analysis["aggregates"](selected),
                "paired_comparisons": effects,
                "interactions": interactions,
            }
        return {
            "status": "provisional",
            "included_episodes": len(rows),
            "reviewed_episodes": sum(r["review_status"] == "reviewed" for r in rows),
            "cohorts": cohorts,
        }

    return build


def rows_for(cohort="main"):
    from test_phase2_matrix_analysis import episode

    rows = []
    for precision, policy, (suite, task), condition in itertools.product(
        ("f16", "q8", "q4"),
        ("full", "truncate", "summary"),
        (("banking", "user_task_13"), ("slack", "user_task_1"), ("travel", "user_task_1")),
        ("clean", "attack"),
    ):
        row = episode(task, condition, True, False if condition == "attack" else None)
        row.update(cohort=cohort, precision=precision, policy=policy, suite=suite)
        rows.append(row)
    return rows


def change(rows, precision, policy, suite, condition, **values):
    row = next(
        r
        for r in rows
        if (r["precision"], r["policy"], r["suite"], r["condition"])
        == (precision, policy, suite, condition)
    )
    row.update(values)


def write_inputs(path, report, rows):
    path.mkdir()
    (path / "measurements.json").write_text(json.dumps(report))
    (path / "episodes.json").write_text(json.dumps(rows))


def test_overflow_and_other_cohort_compaction_do_not_enter_known_cells(plotter, bundle):
    rows = rows_for()
    change(rows, "f16", "full", "travel", "clean", utility=None)
    change(rows, "q4", "summary", "travel", "clean", compactions=5)
    other = rows_for("confirmation")
    change(other, "q4", "summary", "banking", "clean", compactions=1)
    report = bundle(rows + other)
    before = copy.deepcopy(report)
    scopes = plotter.plot_scope(report, rows + other)
    main = scopes["main"]
    assert main["common_tasks"] == 2
    assert main["matched"]["q4", "summary"]["clean"] == {
        "tasks": 2,
        "episodes": 2,
        "compacted_tasks": 0,
        "compacted_episodes": 0,
    }
    assert main["interactions"]["q4", "summary", "clean_utility"] == {
        "tasks": 2,
        "episodes": 8,
        "compacted_tasks": 0,
        "compacted_episodes": 0,
    }
    assert (
        scopes["confirmation"]["interactions"]["q4", "summary", "clean_utility"][
            "compacted_episodes"
        ]
        == 1
    )
    assert report == before


def test_task_counts_deduplicate_four_cells_and_conditions_stay_separate(plotter, bundle):
    rows = rows_for()
    change(rows, "f16", "summary", "banking", "clean", compactions=3)
    change(rows, "q4", "summary", "banking", "clean", compactions=2)
    change(rows, "f16", "full", "slack", "attack", attack_success=None)
    change(rows, "q4", "summary", "slack", "attack", compactions=1)
    scope = plotter.plot_scope(bundle(rows), rows)["main"]
    assert scope["interactions"]["q4", "summary", "clean_utility"] == {
        "tasks": 3,
        "episodes": 12,
        "compacted_tasks": 1,
        "compacted_episodes": 2,
    }
    assert scope["interactions"]["q4", "summary", "attack_success_common_clean"] == {
        "tasks": 2,
        "episodes": 8,
        "compacted_tasks": 0,
        "compacted_episodes": 0,
    }
    assert scope["matched"]["q4", "summary"]["attack"]["compacted_episodes"] == 1


def sensitivity_rows():
    rows = rows_for()
    for r in rows:
        if r["condition"] == "clean" and (
            r["suite"] == "banking" or (r["precision"] == "q4" and r["suite"] == "slack")
        ):
            r["secondary_labels"] = "other;scorer_false_positive"
        if r["condition"] == "attack" and r["suite"] == "travel":
            r["attack_success"] = None if r["precision"] == "f16" else True
            r["secondary_labels"] = "scorer_false_positive"
        if r["condition"] == "attack" and r["suite"] == "slack" and r["precision"] == "f16":
            r["attack_success"] = True
    return rows


def test_clean_sensitivity_retains_attacked_false_positive_and_unknown(plotter, bundle):
    rows = sensitivity_rows()
    report = bundle(rows)
    scope = plotter.plot_scope(report, rows)["main"]
    assert scope["own_excluded"]["f16", "full"] == {("banking", "user_task_13")}
    assert scope["own_excluded"]["q4", "full"] == {
        ("banking", "user_task_13"),
        ("slack", "user_task_1"),
    }
    assert scope["common_excluded"] == scope["own_excluded"]["q4", "full"]
    config = report["cohorts"]["main"]["configurations"][0]
    assert config["attack_success_common_clean"]["lower_bound"] == 1 / 3
    assert config["attack_success_common_clean"]["upper_bound"] == 2 / 3
    metric = config["reviewed_clean_eligibility_sensitivity"]["common_clean_ASR"]
    assert (metric["success"], metric["n"], metric["unknown"]) == (0, 1, 1)
    assert (metric["lower_bound"], metric["upper_bound"]) == (0, 1)


def test_unreviewed_clean_success_is_excluded_from_sensitivity_only(plotter, bundle):
    rows = rows_for()
    change(rows, "q4", "full", "travel", "clean", review_status="pending")
    scope = plotter.plot_scope(bundle(rows), rows)["main"]
    assert scope["common_tasks"] == 3
    assert scope["common_excluded"] == {("travel", "user_task_1")}


def test_no_common_clean_cases_are_not_zero_asr(plotter, bundle):
    rows = rows_for()
    for r in rows:
        if r["precision"] == "q4" and r["condition"] == "clean":
            r["utility"] = None
    report = bundle(rows)
    scope = plotter.plot_scope(report, rows)["main"]
    assert scope["common_tasks"] == 0
    assert scope["interactions"]["q4", "summary", "attack_success_common_clean"] == {
        "tasks": 0,
        "episodes": 0,
        "compacted_tasks": 0,
        "compacted_episodes": 0,
    }
    assert (
        report["cohorts"]["main"]["configurations"][0]["attack_success_common_clean"]["lower_bound"]
        is None
    )


@pytest.mark.parametrize(
    "corruption", ["duplicate", "missing_pair", "compaction", "eligibility", "DID"]
)
def test_mixed_or_incomplete_inputs_are_rejected(plotter, bundle, corruption):
    rows = rows_for()
    report = bundle(rows)
    if corruption == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif corruption == "missing_pair":
        rows[-1]["condition"] = "other"
    elif corruption == "compaction":
        rows[0]["compactions"] = 1
    elif corruption == "eligibility":
        report["cohorts"]["main"]["configurations"][0]["common_clean_tasks"].pop()
    else:
        report["cohorts"]["main"]["interactions"][0]["known_tasks"] -= 1
    with pytest.raises(ValueError):
        plotter.plot_scope(report, rows)


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("estimated", "95% CI [-0.200, +0.300] (estimated)"),
        ("degenerate", "95% CI [+0.000, +0.000] (degenerate)"),
        ("insufficient_tasks_within_suite", "95% CI withheld: insufficient within-suite tasks"),
    ],
)
def test_ci_status_comes_from_export_without_recomputation(plotter, kind, expected):
    uncertainty = {
        "status": "estimated" if kind == "degenerate" else kind,
        "interval_95": [0, 0]
        if kind == "degenerate"
        else [-0.2, 0.3]
        if kind == "estimated"
        else None,
        "degenerate": kind == "degenerate",
    }
    assert plotter.interval_label(uncertainty) == expected


@pytest.mark.parametrize("filename", ["measurements.json", "episodes.json"])
def test_input_drift_during_render_preserves_previous_publication(
    plotter,
    bundle,
    tmp_path,
    monkeypatch,
    filename,
):
    rows = rows_for()
    inputs, output = tmp_path / "inputs", tmp_path / "figures"
    write_inputs(inputs, bundle(rows), rows)
    output.mkdir()
    (output / "previous.txt").write_text("keep this publication")

    def rendering_with_input_change(report, cohort, data, scope, destination):
        destination.mkdir()
        (destination / "partial.svg").write_text("staged only")
        path = inputs / filename
        path.write_bytes(path.read_bytes() + b"\n")

    monkeypatch.setattr(plotter, "figures", rendering_with_input_change)
    with pytest.raises(ValueError, match="Input changed; refusing"):
        plotter.render(inputs, output, provisional=True)
    assert [p.name for p in output.iterdir()] == ["previous.txt"]
    assert (output / "previous.txt").read_text() == "keep this publication"
    assert not list(tmp_path.glob(".phase2-plots-*"))


def test_provisional_gate_precedes_any_output(plotter, bundle, tmp_path):
    rows = rows_for()
    inputs, output = tmp_path / "inputs", tmp_path / "figures"
    write_inputs(inputs, bundle(rows), rows)
    with pytest.raises(ValueError, match="final review"):
        plotter.render(inputs, output)
    assert not output.exists()


def test_drift_at_directory_swap_rolls_back_previous_publication(
    plotter,
    bundle,
    tmp_path,
    monkeypatch,
):
    rows = rows_for()
    inputs, output = tmp_path / "inputs", tmp_path / "figures"
    write_inputs(inputs, bundle(rows), rows)
    output.mkdir()
    (output / "previous.txt").write_text("previous")

    def minimal_renderer(report, cohort, data, scope, destination):
        destination.mkdir()
        (destination / "new.svg").write_text("new")

    rename = Path.rename

    def change_at_swap(path, target):
        result = rename(path, target)
        if path.name == "figures" and path.parent.name.startswith(".phase2-plots-"):
            source = inputs / "episodes.json"
            source.write_bytes(source.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(plotter, "figures", minimal_renderer)
    monkeypatch.setattr(Path, "rename", change_at_swap)
    with pytest.raises(ValueError, match="Input changed; refusing"):
        plotter.render(inputs, output, provisional=True)
    assert [p.name for p in output.iterdir()] == ["previous.txt"]
    assert (output / "previous.txt").read_text() == "previous"


def test_real_render_includes_scope_unknowns_exclusions_and_input_hashes(plotter, bundle, tmp_path):
    pytest.importorskip("matplotlib")
    rows = sensitivity_rows()
    inputs, output = tmp_path / "inputs", tmp_path / "figures"
    write_inputs(inputs, bundle(rows), rows)
    plotter.render(inputs, output, provisional=True)
    assert len(list((output / "main").iterdir())) == 18
    tradeoff = (output / "main/03-tradeoffs.svg").read_text()
    interaction = (output / "main/04-interactions.svg").read_text()
    sensitivity = (output / "main/06-checker-sensitivity.svg").read_text()
    assert "Matched set: 3 all-nine common-clean tasks" in tradeoff
    assert "0/3 tasks; 0/3 ep." in tradeoff
    assert "[omitted]" in tradeoff
    assert "Known / selected tasks: 3/3" in interaction
    assert "0/12 clean ep." in interaction
    assert "CI withheld: insufficient within-suite tasks" in interaction
    assert "1/3  ?1" in sensitivity and "0/1  ?1" in sensitivity
    assert "Banking13, Slack1" in sensitivity
    assert (
        "whole-flagged-task exclusion and paired sensitivity intervals are not shown" in sensitivity
    )
    provenance = json.loads((output / "provenance.json").read_text())
    for name in ("measurements.json", "episodes.json"):
        path = inputs / name
        assert provenance["sha256"][str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
    for path, digest in provenance["sha256"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


def test_real_empty_common_set_is_visible_and_not_plotted_as_zero(plotter, bundle, tmp_path):
    pytest.importorskip("matplotlib")
    rows = rows_for("confirmation")
    for r in rows:
        if r["precision"] == "q4" and r["condition"] == "clean":
            r["utility"] = None
    inputs, output = tmp_path / "inputs", tmp_path / "figures"
    write_inputs(inputs, bundle(rows), rows)
    plotter.render(inputs, output, provisional=True)
    tradeoff = (output / "confirmation/03-tradeoffs.svg").read_text()
    interaction = (output / "confirmation/04-interactions.svg").read_text()
    sensitivity = (output / "confirmation/06-checker-sensitivity.svg").read_text()
    assert "No common-clean tasks; matched costs and ASR are undefined" in tradeoff
    assert "DID unavailable" in interaction and "0/0 attack ep." in interaction
    assert "n=0; undefined" in sensitivity
