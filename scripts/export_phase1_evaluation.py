#!/usr/bin/env python3
"""Export the complete frozen Phase 1 evaluation, offline and without rescoring.

Run from the repository root using the evaluation environment:
  PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py --check-only
  PYTHONHASHSEED=0 .venv/bin/python scripts/export_phase1_evaluation.py

Check-only validates collection and all three review obligations without writing
the final snapshot or curated files. A failed gate never publishes curated output.
Existing snapshots are immutable; changed inputs require a different destination.
No model, endpoint, service, or runtime-lock operation is performed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import audit_phase1_results as auditor
import export_phase1_clean_checkpoint as helper
import select_phase1_review_cases as selector

from agent_tradeoffs import analysis
from agent_tradeoffs.config import stable_hash

AUDIT_IDENTITY_FIELDS = (
    "campaign_identity",
    "plan_sha256",
    "task_manifest_sha256",
    "auditor_sha256",
    "runtime_semantic_sha256",
    "evidence_inventory_sha256",
)
SELECTION_FIELDS = (
    "selection_order",
    "selection_stage",
    "selection_sha256",
    "outcome_stratum",
    "cell_stratum",
)
SELECTOR_FIELDS = selector.KEY_FIELDS | {
    "condition",
    "suite",
    "injection_task_id",
    "attack_variant",
    "user_task_id",
    "repeat_id",
    "status",
    "utility",
    "exposed",
    "attack_success",
}


def audit_identity(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in AUDIT_IDENTITY_FIELDS}


def complete_audit(plan: Path, campaign: Path) -> dict[str, Any]:
    report = auditor.audit_results(plan, campaign, require_complete=True)
    helper.require(
        report["status"] == "passed_complete"
        and report["campaign_identity"] == helper.FROZEN_IDENTITY
        and report["clean_controls"] == 120
        and report["paired_attacks"] == 480
        and report["attacks_without_finalized_clean_control"] == 0,
        "Frozen complete collection/control audit failed",
    )
    return report


def review_gate(rows, base, annotations, base_audit):
    """Use the frozen selectors; enforce their CLI's full-cohort prerequisites."""
    helper.require(
        len(rows) == 600
        and Counter(row["condition"] for row in rows) == {"clean": 120, "attack": 480}
        and len({(row["suite"], row["user_task_id"]) for row in rows}) == 60
        and {row["repeat_id"] for row in rows} == {"seed-17", "seed-29"}
        and len(
            {
                (row["suite"], row["injection_task_id"], row["attack_variant"])
                for row in rows
                if row["condition"] == "attack"
            }
        )
        == 16
        and len({selector.episode_key(row) for row in rows}) == 600
        and len({selector.episode_key(row) for row in base}) == len(base) == 30
        and base_audit["complete"]
        and not base_audit["provisional_selection"],
        "Review selection requires the final 600-episode cohort and native base30",
    )
    base_keys = {selector.episode_key(row) for row in base}
    helper.require(
        stable_hash(sorted(base_keys)) == base_audit["selection_sha256"],
        "Native base30 selection digest differs from its episode keys",
    )
    selected, selection = selector.select_cases(rows, base)
    coverage = selector.review_coverage(rows, base, selected, annotations)
    clean_keys = {selector.episode_key(row) for row in rows if row["condition"] == "clean"}
    extra_keys = {selector.episode_key(row) for row in selected}
    cell_keys = {}
    for row in rows:
        if row["condition"] == "attack":
            cell = row["suite"], row["injection_task_id"], row["attack_variant"]
            cell_keys.setdefault(cell, set()).add(selector.episode_key(row))
    exhausted = [list(cell) for cell, keys in sorted(cell_keys.items()) if keys <= base_keys]
    combined_keys = base_keys | extra_keys
    # Native base CSV rows omit attack_variant; selection has validated their identities.
    combined_cells = {
        (row["suite"], row["injection_task_id"], row["attack_variant"])
        for row in rows
        if selector.episode_key(row) in combined_keys and row["condition"] == "attack"
    }
    helper.require(
        selection["selection_complete"]
        and all(row["condition"] == "attack" for row in selected)
        and not (extra_keys & base_keys)
        and selection["empty_cells_after_exclusion"] == exhausted
        and combined_cells == set(cell_keys)
        and coverage["all_clean"]["clean_episodes"] == coverage["all_clean"]["reviewed"] == 120
        and coverage["all_clean"]["unreviewed"] == 0
        and coverage["base_gate_total"] == coverage["base_gate_reviewed"] == 30
        and coverage["additional_total"] == coverage["additional_reviewed"] == 30
        and coverage["review_obligations_complete"],
        "Final review incomplete: require all120 clean, final base30 and additional30 attacks; "
        + json.dumps(coverage, sort_keys=True),
    )
    return {
        "complete": True,
        "additional_selection": selection,
        "review_coverage": coverage,
        "overlap_counts": {
            "clean_and_base": len(clean_keys & base_keys),
            "base_and_additional": len(base_keys & extra_keys),
            "clean_and_additional": len(clean_keys & extra_keys),
            "distinct_required_episodes": len(clean_keys | base_keys | extra_keys),
            "note": "Required union = 180 minus clean/base overlap; these are reviewed episodes, "
            "not independent observations.",
        },
        "attack_cell_coverage": {
            "required_cells": len(cell_keys),
            "base_and_additional_cells": len(combined_cells),
            "empty_cells_after_exclusion_verified": True,
        },
    }, selected


def preflight(plan: Path, campaign: Path, annotations: Path) -> dict[str, Any]:
    """The complete evidence audit is the first gate, before any output is created."""
    audit = complete_audit(plan, campaign)
    campaign_file = campaign / "manifest.json" if campaign.is_dir() else campaign
    parent_bytes = campaign_file.read_bytes()
    annotation_rows = selector.read_csv(annotations, selector.KEY_FIELDS | {"review_status"})
    _, records, _ = analysis.collect_records(campaign_file)
    analysis.merge_annotations(records, annotations)
    base, base_audit = analysis.select_review_sample(records, 600)

    def strings(row):
        return {key: "" if row.get(key) is None else str(row[key]) for key in SELECTOR_FIELDS}

    review_gate(
        [strings(row) for row in records],
        [strings(row) for row in base],
        annotation_rows,
        base_audit,
    )
    paths = {
        "source-campaign-manifest.json": campaign_file,
        "annotations.csv": annotations,
        "sources/analysis.py": Path(analysis.__file__),
        "sources/select_phase1_review_cases.py": Path(selector.__file__),
        "sources/export_phase1_evaluation.py": Path(__file__),
        "sources/export_phase1_clean_checkpoint.py": Path(helper.__file__),
        "sources/audit_phase1_results.py": Path(auditor.__file__),
        "sources/trace-review-protocol.md": Path("docs/trace-review-protocol.md"),
        "sources/campaign-plan.json": plan,
        "sources/task-manifest.json": Path(json.loads(plan.read_text())["task_manifest"]),
    }
    snapshots = {name: path.read_bytes() for name, path in paths.items()}
    helper.require(snapshots["source-campaign-manifest.json"] == parent_bytes, "Parent changed")
    sources = [
        helper.source_info(path, snapshots[name], tracked=name != "source-campaign-manifest.json")
        for name, path in paths.items()
    ]
    identity = stable_hash(
        {
            "audit": audit_identity(audit),
            "sources": [{"path": source["path"], "sha256": source["sha256"]} for source in sources],
        }
    )
    return {
        "identity": identity,
        "audit": audit,
        "plan": plan,
        "campaign": campaign_file,
        "snapshots": snapshots,
        "sources": sources,
    }


def table(path: Path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def file_info(path: Path) -> dict[str, Any]:
    return helper.source_info(path, path.read_bytes())


def unchanged(inputs: dict[str, Any]) -> None:
    after = complete_audit(inputs["plan"], inputs["campaign"])
    helper.require(audit_identity(after) == audit_identity(inputs["audit"]), "Evidence changed")
    for source in inputs["sources"]:
        helper.require(
            helper.digest(Path(source["path"]).read_bytes()) == source["sha256"],
            f"Input changed during export: {source['path']}",
        )


def utility_success_costs(
    records: list[dict[str, Any]], request_rows: list[dict[str, str]]
) -> dict[str, Any]:
    """Describe costs conditional on observed official utility success.

    Episode selection deliberately uses the native boolean scores, before CSV
    serialization. The request join includes the run because episode IDs repeat
    across seeds and attack templates.
    """
    conditions = ("clean", "attack")
    selected = [
        row
        for row in records
        if row["condition"] in conditions
        and row["status"] == "completed"
        and row.get("utility") is True
    ]

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        keys = {(row["run_id"], row["episode_id"]) for row in rows}
        requests = [row for row in request_rows if (row["run_id"], row["episode_id"]) in keys]
        return {
            "selected_episodes": len(rows),
            "selected_unique_tasks": len({(row["suite"], row["user_task_id"]) for row in rows}),
            "costs": analysis._costs(rows),
            "pooled_request_metrics": helper.pooled_metrics(requests),
        }

    return {
        "selection": {
            "source": "Native episode records before CSV serialization; official scores unchanged",
            "conditions": list(conditions),
            "status": "completed",
            "utility_is_boolean_true": True,
            "request_join": ["run_id", "episode_id"],
        },
        "interpretation": "Conditioned on observed official utility success. The selection differs "
        "from status-completed and all-attempt estimates, and can select different tasks or paths "
        "in each condition. These descriptive costs are neither a causal performance comparison "
        "nor a security metric. Missing measurements remain absent; an empty subset has n=0 "
        "and undefined throughput rates, not evidence of zero cost. Only returned responses "
        "supply tokens/native timing; pooled rates retain the native numerator/time masks.",
        "overall": summarize(selected),
        "by_condition": {
            condition: summarize([row for row in selected if row["condition"] == condition])
            for condition in conditions
        },
        "by_suite_condition": {
            suite: {
                condition: summarize(
                    [
                        row
                        for row in selected
                        if row["suite"] == suite and row["condition"] == condition
                    ]
                )
                for condition in conditions
            }
            for suite in sorted({row["suite"] for row in records})
        },
    }


def export(inputs: dict[str, Any], output: Path, results: Path) -> dict[str, Any]:
    identity = inputs["identity"]
    owner = output / "export-manifest.json"
    curated = results / "evaluation-measurements.json"
    for path in (curated, results / "review-coverage.json"):
        if path.exists():
            helper.require(
                json.loads(path.read_text())["snapshot_identity"] == identity,
                f"Existing curated output belongs to another snapshot: {path}",
            )
    if owner.exists():
        manifest = json.loads(owner.read_text())
        helper.require(manifest["snapshot_identity"] == identity, "Snapshot identity differs")
        for source in manifest["files"]:
            helper.require(file_info(Path(source["path"])) == source, "Archived artifact changed")
        native = json.loads((output / "analysis/summary.json").read_text())
    else:
        helper.require(
            not output.exists() or not any(output.iterdir()), f"Unowned partial snapshot: {output}"
        )
        for name, content in inputs["snapshots"].items():
            helper.immutable_write(output / name, content)
        helper.immutable_write(output / "collection-audit.json", helper.json_bytes(inputs["audit"]))
        helper.immutable_write(
            output / "source-provenance.json",
            helper.json_bytes(
                {
                    "snapshot_identity": identity,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "git_head": helper.git("rev-parse", "HEAD"),
                    "frozen_campaign_identity": helper.FROZEN_IDENTITY,
                    **audit_identity(inputs["audit"]),
                    "sources": inputs["sources"],
                }
            ),
        )
        native = analysis.analyze_campaign(
            output / "source-campaign-manifest.json",
            output / "analysis",
            output / "annotations.csv",
            require_review=True,
        )
    helper.require(
        native["finalized_episodes"] == native["planned_episodes"] == 600
        and native["overall"]["unique_tasks"] == 60
        and native["preliminary_evaluation_ready"]
        and native["measurement_provenance"]["analysis_sha256"]
        == helper.digest(inputs["snapshots"]["sources/analysis.py"]),
        "Native full-cohort readiness/provenance failed",
    )
    episode_fields, episodes = table(output / "analysis/episodes.csv")
    request_fields, requests = table(output / "analysis/requests.csv")
    base = selector.read_csv(output / "analysis/review-sample.csv", selector.KEY_FIELDS)
    annotations = selector.read_csv(
        output / "annotations.csv", selector.KEY_FIELDS | {"review_status"}
    )
    review, selected = review_gate(episodes, base, annotations, native["review_sample"])
    helper.immutable_write(
        output / "additional-review-cases.csv",
        helper.csv_bytes(selected, [*episode_fields, *SELECTION_FIELDS]),
    )
    review.update(
        {
            "snapshot_identity": identity,
            "native_base_selection": native["review_sample"],
            "annotations": file_info(output / "annotations.csv"),
            "base_sample": file_info(output / "analysis/review-sample.csv"),
            "additional_cases": file_info(output / "additional-review-cases.csv"),
        }
    )
    helper.immutable_write(output / "review-coverage.json", helper.json_bytes(review))
    _, records, _ = analysis.collect_records(output / "source-campaign-manifest.json")
    analysis.merge_annotations(records, output / "annotations.csv")
    clean = [row for row in records if row["condition"] == "clean"]
    clean_true = sum(row["status"] == "completed" and row["utility"] is True for row in clean)
    clean_false = sum(row["status"] == "completed" and row["utility"] is False for row in clean)
    clean_unknown = len(clean) - clean_true - clean_false
    expected_controls = {
        "attack_episodes": 480,
        "verified_clean_true": 4 * clean_true,
        "verified_clean_false": 4 * clean_false,
        "unknown_clean_outcome": 4 * clean_unknown,
        "missing_clean_result": 0,
    }
    helper.require(
        all(
            native["overall"]["clean_control_coverage"][key] == value
            for key, value in expected_controls.items()
        )
        and native["overall"]["unknown_clean_episodes"] == clean_unknown
        and native["overall"]["rates"]["attack_success_clean_completable_all_attempts"][
            "denominator"
        ]
        == 4 * clean_true,
        "Native eligibility denominator differs from the frozen four-attacks-per-clean pairing",
    )
    by_suite_style = {
        suite: {
            style: analysis._summarize(
                [
                    row
                    for row in records
                    if row["suite"] == suite
                    and (row["condition"] == "clean" or row["attack_variant"] == style)
                ]
            )
            for style in sorted(native["by_attack"])
        }
        for suite in sorted(native["by_suite"])
    }
    by_suite_repeat = {
        suite: {
            repeat: analysis._summarize(
                [row for row in records if row["suite"] == suite and row["repeat_id"] == repeat]
            )
            for repeat in sorted(native["by_repeat"])
        }
        for suite in sorted(native["by_suite"])
    }
    pooled = {
        "source": "Native requests.csv rows passed through clean120 helper.pooled_metrics",
        "scope": "Only returned responses supply tokens/native timing; failed compute is unknown. "
        "Native masks pair numerator/time; rates are not means of episode rates. "
        "request_latency_seconds includes observed failed-request latency.",
        "overall": helper.pooled_metrics(requests),
        "by_condition": {
            condition: helper.pooled_metrics([r for r in requests if r["condition"] == condition])
            for condition in ("clean", "attack")
        },
        "by_suite_condition": {
            suite: {
                condition: helper.pooled_metrics(
                    [r for r in requests if r["suite"] == suite and r["condition"] == condition]
                )
                for condition in ("clean", "attack")
            }
            for suite in sorted(native["by_suite"])
        },
    }
    unchanged(inputs)
    if not owner.exists():
        helper.immutable_write(
            owner,
            helper.json_bytes(
                {
                    "schema_version": 1,
                    "complete": True,
                    "snapshot_identity": identity,
                    "files": [
                        file_info(path) for path in sorted(output.rglob("*")) if path.is_file()
                    ],
                }
            ),
        )
    full_summary = file_info(output / "analysis/summary.json")
    provenance = dict(native["measurement_provenance"])
    provenance["runs_omitted"] = {
        "run_count": len(provenance.pop("runs")),
        "reason": "Full runtime/chat-template provenance remains in the immutable native summary.",
        "full_summary": full_summary,
    }
    measurements = {
        "schema_version": 1,
        "complete": True,
        "snapshot_identity": identity,
        "scope": {
            "parent_campaign_id": native["campaign_id"],
            "frozen_campaign_identity": helper.FROZEN_IDENTITY,
            "planned_episodes": 600,
            "finalized_episodes": 600,
            "clean_episodes": 120,
            "attack_episodes": 480,
            "unique_tasks": 60,
            "runs": 24,
            "seeds": [17, 29],
            "full_cohort_evaluation_export_complete": True,
            "note": "Complete collection plus all120 clean, final base30 and extra30 reviews; "
            "official scores unchanged. Native readiness alone does not certify these obligations. "
            "This export does not certify final plots, visual QA, or the written assessment.",
        },
        "source_provenance": json.loads((output / "source-provenance.json").read_text()),
        "source_files": [file_info(owner), *json.loads(owner.read_text())["files"]],
        "native_summary": {**native, "measurement_provenance": provenance},
        "by_suite_style": by_suite_style,
        "by_suite_repeat": by_suite_repeat,
        "pooled_request_metrics": pooled,
        "utility_success_costs": utility_success_costs(records, requests),
        "collection_audit": file_info(output / "collection-audit.json"),
        "review_coverage": review,
        "limitations": [
            "60 fixed task clusters, two stochastic seeds; episodes are not 600 independent tasks.",
            "Agent trace review is not independent human annotation or a validated LLM judge.",
            "Official scores retain documented checker limitations; "
            "unknown outcomes remain unknown.",
            "Fixed run order and shared host drift confound performance comparisons.",
            "Server-process RSS excludes harness and total GPU/unified-device memory.",
        ],
    }
    publications = {
        "evaluation-measurements.json": helper.json_bytes(measurements),
        "evaluation-episodes.csv": helper.csv_bytes(episodes, episode_fields),
        "evaluation-requests.csv": helper.csv_bytes(requests, request_fields),
        "collection-audit.json": (output / "collection-audit.json").read_bytes(),
        "review-coverage.json": (output / "review-coverage.json").read_bytes(),
    }
    for name, content in publications.items():
        destination = results / name
        helper.require(
            not destination.exists() or destination.read_bytes() == content,
            f"Existing curated content differs: {destination}",
        )
    for name, content in publications.items():
        helper.immutable_write(results / name, content)
    return {
        "complete": True,
        "snapshot_identity": identity,
        "snapshot": str(output),
        "measurements": str(curated),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("configs/phase1/campaign.json"))
    parser.add_argument("--campaign", type=Path, default=Path("artifacts/campaigns/phase1-f16-v1"))
    parser.add_argument(
        "--annotations", type=Path, default=Path("results/phase1/reviewed-traces.csv")
    )
    parser.add_argument("--snapshot", type=Path, default=Path("artifacts/phase1/final-evaluation"))
    parser.add_argument("--results", type=Path, default=Path("results/phase1"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        helper.require(os.environ.get("PYTHONHASHSEED") == "0", "Run with PYTHONHASHSEED=0")
        inputs = preflight(args.plan.resolve(), args.campaign.resolve(), args.annotations.resolve())
        result = (
            {"ready": True, "snapshot_identity": inputs["identity"], "episodes": 600}
            if args.check_only
            else export(inputs, args.snapshot.resolve(), args.results.resolve())
        )
    except (ValueError, KeyError, OSError, EOFError, TypeError, StopIteration) as error:
        parser.exit(1, f"Final Phase 1 export refused: {error}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
