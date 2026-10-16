"""Synthetic structural fixtures for final review gates; never benchmark evidence."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def annotation(row):
    return {
        **{key: row[key] for key in ("run_id", "episode_id", "identity")},
        "review_status": "reviewed",
        "refusal": "no",
        "failure_category": "no_failure",
        "reviewer": "synthetic-fixture-only",
        "evidence": "Synthetic structural review; no real trace or model result.",
    }


@pytest.fixture
def synthetic_review(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    driver = importlib.import_module("export_phase1_evaluation")
    records = []
    variants = [(None, None)] + [
        (style, goal)
        for style in ("direct_append", "ignore_previous")
        for goal in ("synthetic-goal-a", "synthetic-goal-b")
    ]
    for suite, tasks in {"banking": 8, "slack": 16, "workspace": 24, "travel": 12}.items():
        for seed in (17, 29):
            for task in range(tasks):
                for style, goal in variants:
                    records.append(
                        {
                            "run_id": f"phase1-f16-s{seed}-{suite}-{style or 'clean'}",
                            "episode_id": f"synthetic-task-{task}-{goal or 'clean'}",
                            "identity": f"synthetic-{suite}-{seed}-{style}",
                            "suite": suite,
                            "user_task_id": f"synthetic-task-{task}",
                            "repeat_id": f"seed-{seed}",
                            "condition": "clean" if style is None else "attack",
                            "injection_task_id": goal,
                            "attack_variant": style,
                            "status": "completed",
                            "utility": task % 2 == 0,
                            "attack_success": goal == "synthetic-goal-a" if goal else None,
                            "exposed": style is not None,
                            "invalid_model_output": False,
                            "tool_errors": 0,
                            "review_status": "reviewed",
                        }
                    )
    base, base_audit = driver.analysis.select_review_sample(records, 600)

    def strings(row):
        return {
            key: "" if row.get(key) is None else str(row[key]) for key in driver.SELECTOR_FIELDS
        }

    rows, base = [strings(row) for row in records], [strings(row) for row in base]
    extra, _ = driver.selector.select_cases(rows, base)
    required = {
        driver.selector.episode_key(row)
        for row in [*base, *extra, *[r for r in rows if r["condition"] == "clean"]]
    }
    annotations = [annotation(row) for row in rows if driver.selector.episode_key(row) in required]
    return driver, rows, base, base_audit, annotations


def test_complete_review_counts_distinct_union_without_double_counting_clean(synthetic_review):
    driver, rows, base, base_audit, annotations = synthetic_review
    review, extra = driver.review_gate(rows, base, annotations, base_audit)
    clean_keys = {driver.selector.episode_key(row) for row in rows if row["condition"] == "clean"}
    base_keys = {driver.selector.episode_key(row) for row in base}
    extra_keys = {driver.selector.episode_key(row) for row in extra}

    assert review["complete"]
    assert review["review_coverage"]["all_clean"]["reviewed"] == 120
    assert len(base_keys) == len(extra_keys) == 30
    assert clean_keys & base_keys  # This fixture exercises real overlap between obligations.
    assert not (base_keys & extra_keys)
    assert not (clean_keys & extra_keys)
    assert review["overlap_counts"]["distinct_required_episodes"] == len(annotations)
    assert len(annotations) == len(clean_keys | base_keys | extra_keys) < 180


@pytest.mark.parametrize("missing_obligation", ["clean", "base", "extra"])
def test_each_review_obligation_is_required_even_with_native_base_gate_true(
    synthetic_review, missing_obligation
):
    driver, rows, base, base_audit, annotations = synthetic_review
    extra, _ = driver.selector.select_cases(rows, base)
    base_keys = {driver.selector.episode_key(row) for row in base}
    candidates = {
        "clean": [
            row
            for row in rows
            if row["condition"] == "clean" and driver.selector.episode_key(row) not in base_keys
        ],
        "base": [row for row in base if row["condition"] == "attack"],
        "extra": extra,
    }
    missing = driver.selector.episode_key(candidates[missing_obligation][0])
    remaining = [row for row in annotations if driver.selector.episode_key(row) != missing]

    assert base_audit["complete"] is True
    coverage = driver.selector.review_coverage(rows, base, extra, remaining)
    if missing_obligation in {"clean", "extra"}:
        assert coverage["base_gate_reviewed"] == coverage["base_gate_total"] == 30
    with pytest.raises(ValueError, match="Final review incomplete"):
        driver.review_gate(rows, base, remaining, base_audit)


def test_base_exhausted_cell_is_allowed_when_combined_review_covers_all_cells(synthetic_review):
    driver, rows, _, base_audit, _ = synthetic_review
    exhausted_cell = ("banking", "synthetic-goal-a", "direct_append")

    def cell(row):
        return row["suite"], row["injection_task_id"], row["attack_variant"]

    exhausted_rows = [row for row in rows if cell(row) == exhausted_cell]
    # Both seeds of this 8-task cell fit in base30; no additional case can remain.
    assert len(exhausted_rows) == 16
    base = (
        exhausted_rows
        + [row for row in rows if cell(row) != exhausted_cell][: 30 - len(exhausted_rows)]
    )
    base_audit = {
        **base_audit,
        "selection_sha256": driver.stable_hash(
            sorted(driver.selector.episode_key(r) for r in base)
        ),
    }
    review, extra = driver.review_gate(rows, base, [annotation(row) for row in rows], base_audit)

    assert review["complete"]
    assert review["additional_selection"]["empty_cells_after_exclusion"] == [list(exhausted_cell)]
    assert len(extra) == 30
    assert exhausted_cell not in {cell(row) for row in extra}
    combined = {cell(row) for row in [*base, *extra] if row["condition"] == "attack"}
    assert len(combined) == 16
    assert review["attack_cell_coverage"]["base_and_additional_cells"] == 16


def test_stale_native_base_selection_digest_is_rejected(synthetic_review):
    driver, rows, base, base_audit, annotations = synthetic_review
    stale = {**base_audit, "selection_sha256": "0" * 64}
    with pytest.raises(ValueError, match="selection digest differs"):
        driver.review_gate(rows, base, annotations, stale)
