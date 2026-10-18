"""The diagnostic must never expand or silently shrink its selected attempts."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "retry_driver", Path(__file__).parents[1] / "scripts/run_phase1_error_retries.py"
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def runs():
    return [
        {"retry_round": repeat, "original_run_id": "source", "episode_ids": ["task_a", "task_b"]}
        for repeat in (1, 2)
    ]


def test_exact_attempt_mapping():
    DRIVER.check_attempt_mapping({("source", "task_a"), ("source", "task_b")}, runs())


@pytest.mark.parametrize("mutation", ["extra_goal", "missing", "duplicate", "wrong_round"])
def test_attempt_mapping_rejects_cohort_changes(mutation):
    entries = runs()
    if mutation == "extra_goal":
        entries[0]["episode_ids"].append("unselected_task_goal")
    elif mutation == "missing":
        entries[1]["episode_ids"].pop()
    elif mutation == "duplicate":
        entries.append(entries[0])
    else:
        entries[1]["retry_round"] = 3
    with pytest.raises(ValueError, match="exactly once"):
        DRIVER.check_attempt_mapping({("source", "task_a"), ("source", "task_b")}, entries)
