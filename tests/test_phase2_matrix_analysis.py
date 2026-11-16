"""Unknown outcomes and false-positive clean scores must not become resistance."""

import runpy
from pathlib import Path

import pytest


@pytest.fixture
def analysis(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return runpy.run_path(str(scripts / "analyze_phase2_matrix.py"))


def episode(task, condition, utility, attack_success, secondary=""):
    return {
        "precision": "f16",
        "policy": "full",
        "suite": "slack",
        "task": task,
        "condition": condition,
        "utility": utility,
        "attack_success": attack_success,
        "review_status": "reviewed",
        "refusal": "no",
        "failure_category": "no_failure",
        "secondary_labels": secondary,
        "status": "completed",
        "error_type": None,
        "memory_sampling_errors": [],
        "first_finalized_episode_on_process": False,
        "compactions": 0,
        "literal_exposure_requests": 1,
        "literal_removal_after_exposure": False,
        "repeated_calls": 0,
        **dict.fromkeys(
            (
                "seconds",
                "agent_tokens",
                "agent_prompt_tokens",
                "agent_completion_tokens",
                "summary_tokens",
                "summary_prompt_tokens",
                "summary_completion_tokens",
                "all_model_tokens",
                "summary_seconds",
                "tokenizer_seconds",
                "agent_peak_rss_bytes",
                "summary_peak_rss_bytes",
                "run_aggregate_peak_rss_bytes",
            ),
            1,
        ),
    }


def test_unknown_and_incapable_pairs_remain_separate(analysis):
    rows = [
        episode("a", "clean", True, None),
        episode("a", "attack", None, None),
        episode("b", "clean", False, None),
        episode("b", "attack", False, False),
        episode("c", "clean", None, None),
        episode("c", "attack", False, True),
    ]
    summary = analysis["aggregates"](rows)[0]
    assert summary["attack_success_all"] == {
        "n": 3,
        "success": 1,
        "failure": 1,
        "unknown": 1,
        "lower_bound": 1 / 3,
        "upper_bound": 2 / 3,
        "known_only_rate": 0.5,
    }
    conditional = summary["attack_success_own_clean"]
    assert conditional["n"] == conditional["unknown"] == 1
    assert conditional["lower_bound"] == 0 and conditional["upper_bound"] == 1
    assert conditional["known_only_rate"] is None


def test_reviewed_false_positive_is_excluded_only_from_sensitivity(analysis):
    rows = [
        episode("a", "clean", True, None, "scope_expansion|scorer_false_positive"),
        episode("a", "attack", True, False),
        episode("b", "clean", True, None),
        episode("b", "attack", True, True),
    ]
    summary = analysis["aggregates"](rows)[0]
    assert summary["attack_success_own_clean"]["n"] == 2
    assert summary["clean_utility"]["success"] == 2
    sensitivity = summary["reviewed_clean_eligibility_sensitivity"]["own_clean_ASR"]
    assert sensitivity["n"] == sensitivity["success"] == 1


def test_paired_flips_exclude_unknowns_without_dropping_their_count(analysis):
    result = analysis["paired"](
        {("s", "a"): False, ("s", "b"): True, ("s", "c"): None},
        {("s", "a"): True, ("s", "b"): False, ("s", "c"): True},
    )
    assert result["n"] == 3 and result["unknown_pairs"] == 1
    assert result["false_to_true"] == result["true_to_false"] == 1
    assert result["delta_known"] == 0
    assert result["delta_unknown_bounds"] == [0, 1 / 3]
    with pytest.raises(ValueError, match="same task set"):
        analysis["paired"]({"a": True}, {"b": True})


def test_repeat_detection_ignores_structural_call_ids(analysis):
    assert (
        analysis["repeated_calls"](
            [
                {"function": "lookup", "args": {"x": 1}, "id": "first"},
                {"function": "lookup", "args": {"x": 1}, "id": "second"},
                {"function": "lookup", "args": {"x": 2}, "id": "third"},
            ]
        )
        == 1
    )


def test_refusal_with_indeterminate_necessity_remains_unknown(analysis):
    row = episode("a", "clean", False, None)
    row.update(refusal="yes", failure_category="unclear")
    result = analysis["aggregates"]([row])[0]["unnecessary_refusal"]
    assert result["unknown"] == result["n"] == 1
    assert result["failure"] == result["success"] == 0


@pytest.mark.parametrize("invalid", ["missing", "sampling_error"])
def test_incomplete_costs_cannot_establish_dominance(analysis, invalid):
    rows = []
    for precision, policy in analysis["CONFIGURATIONS"]:
        for task in ("a", "b"):
            for condition in ("clean", "attack"):
                row = episode(task, condition, True, False if condition == "attack" else None)
                row.update(precision=precision, policy=policy)
                if precision == "f16":
                    row["seconds"] = 2
                rows.append(row)
    baseline = analysis["dominance"](analysis["aggregates"](rows))
    assert {"dominates": ("q8", "full"), "dominated": ("f16", "full")} in baseline["edges"]
    row = next(
        r
        for r in rows
        if (r["precision"], r["policy"], r["task"], r["condition"]) == ("q8", "full", "a", "clean")
    )
    if invalid == "missing":
        row["run_aggregate_peak_rss_bytes"] = None
    else:
        row["memory_sampling_errors"] = ["process exited during sampling"]
    result = analysis["dominance"](analysis["aggregates"](rows))
    assert ("q8", "full") not in result["eligible_configurations"]
    assert all(("q8", "full") not in edge.values() for edge in result["edges"])


def test_paired_intervals_preserve_pair_direction_and_suite_coverage(analysis):
    a = {("a", "1"): False, ("a", "2"): True, ("b", "1"): True, ("b", "2"): True}
    b = {("a", "1"): True, ("a", "2"): True, ("b", "1"): False, ("b", "2"): False}
    forward = analysis["paired"](a, b)["uncertainty"]
    reverse = analysis["paired"](b, a)["uncertainty"]
    assert forward["tasks_by_suite"] == {"a": 2, "b": 2}
    assert forward["interval_95"] == [-v for v in reversed(reverse["interval_95"])]
    sparse = analysis["paired"]({("s", "1"): False}, {("s", "1"): True})
    assert sparse["uncertainty"]["status"] == "insufficient_tasks_within_suite"
    assert sparse["uncertainty"]["interval_95"] is None
