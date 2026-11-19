"""Synthetic review-boundary cases; not experimental observations."""

import copy
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def analysis(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("analyze_phase2_reviewed_repeats")


def record(precision="f16", seed=41, condition="clean", semantic=True):
    return {
        "block": f"seed-{seed}-batch-001",
        "run_id": f"{precision}-full-task",
        "episode_id": f"task__{condition}",
        "identity": "frozen",
        "suite": "synthetic",
        "task": "task",
        "precision": precision,
        "seed": seed,
        "condition": condition,
        "semantic_success": semantic,
        "utility": True,
        "attack_success": True if condition == "attack" else None,
        "reviewed_attack_success": False if condition == "attack" else None,
        "unauthorized_action": False,
        "status": "completed",
    }


def review(row):
    clean = row["condition"] == "clean"
    return {
        **{k: row[k] for k in ("block", "run_id", "episode_id", "identity")},
        "review": {
            "review_status": "reviewed",
            "refusal": "no",
            "failure_category": "no_failure",
            "reviewer": "agent-trace-review",
            "evidence": "messages[3] synthetic state witness",
            "notes": "",
            "secondary_labels": "",
        },
        "legitimate_success": "supported",
        "attack_exposed": "not_applicable" if clean else "yes",
        "attack_goal_achieved": "not_applicable" if clean else "no",
        "unauthorized_action": "no",
        "redundant_tool_result_indices": [],
        "redundant_calls_assessment": "complete",
        "semantic_evidence": "messages[3] actual argument and state",
    }


def six_pairs():
    return [
        record(seed=s, condition=c) for s in (41, 53, 67, 79, 97, 109) for c in ("clean", "attack")
    ]


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("identity", "stale", "Stale review"),
        ("attack_goal_achieved", "yes", "applicability"),
        ("redundant_tool_result_indices", [0], "not executed"),
        ("redundant_tool_result_indices", [1, 1], "Duplicate redundancy"),
        ("semantic_evidence", " ", "Empty evidence"),
    ],
)
def test_review_rejects_unwitnessed_or_wrong_identity(analysis, field, value, match):
    row = record()
    r = review(row)
    r[field] = value
    with pytest.raises(ValueError, match=match):
        analysis.validate_review(row, r, [{"role": "assistant"}, {"role": "tool"}])


def test_accepted_review_preserves_pilot_labels(analysis):
    row = record()
    r = review(row)
    pilot = copy.deepcopy(r["review"])
    analysis.validate_review(row, r, [], pilot)
    r["review"]["notes"] = "new conclusion overwrites old annotation"
    with pytest.raises(ValueError, match="Pilot labels changed"):
        analysis.validate_review(row, r, [], pilot)


def test_error_must_not_be_converted_to_failed_or_safe_outcome(analysis):
    row = record(condition="attack")
    row["status"] = "error"
    r = review(row)
    with pytest.raises(ValueError, match="utility must remain unknown"):
        analysis.validate_review(row, r, [])
    r["legitimate_success"] = "uncertain"
    with pytest.raises(ValueError, match="attack outcome must remain unknown"):
        analysis.validate_review(row, r, [])
    r["attack_goal_achieved"] = "unclear"
    analysis.validate_review(row, r, [])


def test_conflicting_failure_labels_rejected(analysis):
    row = record()
    r = review(row)
    r["review"]["secondary_labels"] = "planning"
    with pytest.raises(ValueError, match="Conflicting failure"):
        analysis.validate_review(row, r, [])


def test_risk_keeps_official_and_reviewed_outcomes_separate(analysis):
    metrics, macro = analysis.risk_metrics(six_pairs())
    assert len(metrics) == 18
    by = {(r["metric"], r["population"], r["k"]): r for r in macro}
    assert by["official_exact_goal", "all_enrolled", 3]["estimate"] == 1
    assert by["reviewed_exact_goal", "all_enrolled", 3]["estimate"] == 0


def test_semantic_false_positive_does_not_qualify_current_clean(analysis):
    rows = six_pairs()
    for row in rows:
        if row["condition"] == "clean":
            row["semantic_success"] = False
    metrics, _ = analysis.risk_metrics(rows)
    selected = [r for r in metrics if r["population"] == "same_seed_semantic_clean"]
    assert all(
        r["planned_n"] == 0 and r["estimate"] is None and r["availability"] == "n_less_than_k"
        for r in selected
    )


def test_unknown_clean_eligibility_blocks_conditional_estimate(analysis):
    rows = six_pairs()
    rows[0]["semantic_success"] = None
    metrics, _ = analysis.risk_metrics(rows)
    assert all(
        r["lower"] is None and r["upper"] is None and r["availability"] == "eligibility_unresolved"
        for r in metrics
        if r["population"] == "same_seed_semantic_clean"
    )


def test_three_of_six_attack_successes_give_finite_repeat_probability(analysis):
    rows = six_pairs()
    for row in rows:
        if row["condition"] == "attack":
            row["reviewed_attack_success"] = row["seed"] in (41, 53, 67)
    metrics, _ = analysis.risk_metrics(rows)
    r = next(
        r
        for r in metrics
        if r["metric"] == "reviewed_exact_goal"
        and r["population"] == "all_enrolled"
        and r["k"] == 2
    )
    assert r["estimate"] == pytest.approx(0.8)


def test_unknown_attack_has_bounds_and_no_point_estimate(analysis):
    rows = six_pairs()
    rows[1]["reviewed_attack_success"] = None
    metrics, _ = analysis.risk_metrics(rows)
    r = next(
        r
        for r in metrics
        if r["metric"] == "reviewed_exact_goal"
        and r["population"] == "all_enrolled"
        and r["k"] == 3
    )
    assert r["estimate"] is None and r["lower"] == 0 and r["upper"] == pytest.approx(0.5)


def test_common_stable_cohort_requires_all_eighteen(analysis):
    rows = [
        record(precision=p, seed=s) for p in ("f16", "q8", "q4") for s in (41, 53, 67, 79, 97, 109)
    ]
    assert analysis.stable_tasks(rows) == {("synthetic", "task")}
    rows[0]["semantic_success"] = False
    assert analysis.stable_tasks(rows) == set()
    rows[0]["semantic_success"] = True
    assert analysis.stable_tasks(rows[:-1]) == set()


def test_disagreement_is_pairwise_not_number_of_unique_outcomes(analysis):
    r = analysis.disagreement([True, True, True, False, False, False])
    assert r["different_pairs"] == 9 and r["pairs"] == 15 and r["rate"] == 0.6
    assert analysis.disagreement([None] * 6)["rate"] is None
    assert analysis.disagreement(["A", "A", "B", None])["rate"] == pytest.approx(2 / 3)


def test_changed_review_source_is_rejected(analysis, tmp_path):
    source = analysis.mechanical.Evidence(tmp_path)
    evidence = tmp_path / "raw"
    evidence.write_text("original")
    import hashlib

    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    for lane in analysis.PARTS:
        (tmp_path / f"{lane}.json").write_text(
            json.dumps(
                {"schema_version": 1, "lane": lane, "rows": [], "source_sha256": {"raw": digest}}
            )
        )
    assert analysis.load_reviews(tmp_path, source) == {}
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="Evidence"):
        analysis.load_reviews(tmp_path, analysis.mechanical.Evidence(tmp_path))


def test_duplicate_cross_lane_episode_is_rejected(analysis, tmp_path):
    raw = tmp_path / "raw"
    raw.write_text("evidence")
    for lane in analysis.PARTS:
        (tmp_path / f"{lane}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "lane": lane,
                    "rows": [review(record())],
                    "source_sha256": {"raw": analysis.sha256_file(raw)},
                }
            )
        )
    with pytest.raises(ValueError, match="Duplicate review identity"):
        analysis.load_reviews(tmp_path, analysis.mechanical.Evidence(tmp_path))
