"""Prepare and run fresh-seed Full-context pairs using the frozen Phase 2 driver."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import itertools
import json
import os
import re
import tarfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import phase2_seed_diagnostics as evidence
from phase2_collect import verify_archive
from pydantic import Field, model_validator

from agent_tradeoffs.config import ExperimentConfig
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runtime import sha256_file

WRAPPER = Path(__file__).resolve()


class Task(evidence.FrozenInput):
    suite: Literal["banking", "slack", "workspace", "travel"]
    task: Annotated[str, Field(pattern=r"^user_task_[0-9]+$")]


class Selection(evidence.FrozenInput):
    schema_version: Literal[1]
    qualification_source: str
    qualification_sha256: evidence.Digest
    reviewed_table_sha256: evidence.Digest
    seeds: Annotated[list[int], Field(min_length=1, max_length=12)]
    tasks: Annotated[list[Task], Field(min_length=1, max_length=24)]
    tasks_per_block: Annotated[int, Field(ge=1, le=4)] = 4
    block_budget_seconds: Annotated[int, Field(ge=1200, le=5400)] = 5400

    @model_validator(mode="after")
    def unique_fresh_attempts(self):
        keys = [(t.suite, t.task) for t in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate task")
        if len(set(self.seeds)) != len(self.seeds) or any(
            seed in {17, 29} or not 0 <= seed < 2**31 for seed in self.seeds
        ):
            raise ValueError("Use unique fresh seeds, excluding historical 17 and 29")
        return self


def output_root(value):
    path = Path(value).absolute()
    evidence.require(
        path.parent == Path.cwd() / "artifacts"
        and re.fullmatch(r"phase2-repeat-study-[a-z0-9][a-z0-9-]*", path.name),
        "Use artifacts/phase2-repeat-study-<version>",
    )
    evidence.require(path.resolve() == path, "Output root cannot be a symlink")
    evidence.require(not any(p.is_symlink() for p in path.rglob("*")), "Output symlink refused")
    return path


def qualified(selection):
    path = Path(selection.qualification_source)
    evidence.require(sha256_file(path) == selection.qualification_sha256, "Qualification changed")
    qualification = evidence.read(path)
    rows = qualification["tasks"]
    for name, digest in qualification.get("source_hashes", {}).items():
        evidence.require(
            sha256_file(Path(name)) == digest, f"Qualification evidence changed: {name}"
        )
    keys = [(r["suite"], r["task"]) for r in rows]
    evidence.require(len(keys) == len(set(keys)), "Duplicate qualification task")
    eligible = {(r["suite"], r["task"]) for r in rows if r["eligible"] is True}
    selected = {(t.suite, t.task) for t in selection.tasks}
    evidence.require(selected == eligible, "Use the complete qualified main-task set")


def derive(selection, root, parent):
    matrix, plans, rows, _ = parent
    qualified(selection)
    evidence.require(
        selection.reviewed_table_sha256 == sha256_file(evidence.PUBLIC / "analysis/episodes.csv"),
        "Reviewed table changed",
    )
    sources = {}
    for block, plan in plans.items():
        if not block.startswith("main-"):
            continue
        for entry in plan["runs"]:
            if entry["policy"] == "full":
                sources[(entry["suite"], entry["user_task_id"], entry["precision"])] = (
                    block,
                    entry,
                )
    files, blocks = {}, []
    permutations = list(itertools.permutations(evidence.driver.FORMATS))
    chunks = [
        selection.tasks[i : i + selection.tasks_per_block]
        for i in range(0, len(selection.tasks), selection.tasks_per_block)
    ]
    for seed_index, seed in enumerate(selection.seeds):
        for chunk_index, tasks in enumerate(chunks):
            block_id = f"seed-{seed}-batch-{chunk_index + 1:03d}"
            order = permutations[(seed_index + chunk_index) % len(permutations)]
            runs = []
            for precision in order:
                for task in tasks:
                    block, original = sources[(task.suite, task.task, precision)]
                    source_manifest = (
                        Path(original["config"]["runner"]["output_root"])
                        / original["run_id"]
                        / "manifest.json"
                    )
                    manifest = evidence.read(source_manifest)
                    pairs = [
                        r for r in rows if r["block"] == block and r["run_id"] == original["run_id"]
                    ]
                    evidence.require(
                        len(pairs) == 2
                        and {r["condition"] for r in pairs} == {"clean", "attack"}
                        and all(r["identity"] == manifest["identity"] for r in pairs),
                        "Original paired identity differs",
                    )
                    config = copy.deepcopy(original["config"])
                    evidence.require(
                        config["model"]["seed"] == 17
                        and config["experiment"]["mode"] == "paired"
                        and config["context"]["policy"] == "full",
                        "Expected original seed17 Full pair",
                    )
                    config["model"]["seed"] = seed
                    config["runner"]["output_root"] = str(root / block_id / "runs")
                    config["runner"]["runtime_metadata_path"] = str(
                        root / "runtime" / precision / "server.json"
                    )
                    ExperimentConfig.model_validate(config)
                    runs.append(
                        {
                            **{
                                k: original[k]
                                for k in ("run_id", "precision", "policy", "suite", "user_task_id")
                            },
                            "config": config,
                            "source_pair": {
                                "block": block,
                                "run_id": original["run_id"],
                                "identity": manifest["identity"],
                                "manifest_sha256": sha256_file(source_manifest),
                                "runtime": manifest["runtime"]["semantic_identity"],
                                "summarizer_runtime": None,
                                "episodes": [
                                    {
                                        k: r[k]
                                        for k in (
                                            "episode_id",
                                            "condition",
                                            "source",
                                            "trace_sha256",
                                            "result_sha256",
                                        )
                                    }
                                    for r in pairs
                                ],
                            },
                        }
                    )
            plan = {
                "block_id": block_id,
                "cohort": "historically_qualified_fresh_seed_repeats",
                "seed": seed,
                "task_order": [[t.suite, t.task] for t in tasks],
                "precision_order": list(order),
                "budget_seconds": selection.block_budget_seconds,
                "expected_episodes": len(runs) * 2,
                "runs": runs,
            }
            name = f"configs/{block_id}.json"
            files[name] = evidence.encoded(plan)
            blocks.append(
                {
                    "block_id": block_id,
                    "plan": str(root / name),
                    "sha256": hashlib.sha256(files[name]).hexdigest(),
                    "expected_episodes": plan["expected_episodes"],
                }
            )
    runtime_hashes = {}
    for role in (*evidence.driver.FORMATS, "summarizer"):
        original = (evidence.FORMAL_CONFIGS / f"runtime-{role}.toml").read_text()
        text, count = re.subn(
            r'^output_root = "[^"\n]+"$',
            f'output_root = "{root / "runtime" / role}"',
            original,
            flags=re.MULTILINE,
        )
        before = tomllib.loads(original)
        before["runtime"]["output_root"] = str(root / "runtime" / role)
        evidence.require(count == 1 and before == tomllib.loads(text), "Unexpected runtime change")
        files[f"configs/runtime-{role}.toml"] = text.encode()
        runtime_hashes[role] = hashlib.sha256(text.encode()).hexdigest()
    derived_matrix = {
        "campaign_id": root.name,
        **{
            k: matrix[k]
            for k in ("implementation", "driver_sha256", "protocol_sha256", "template_sha256")
        },
        "runtime_configs_sha256": runtime_hashes,
        "scope": (
            "Fresh repeats of independently baseline-qualified tasks; "
            "no pooling with formal or selected diagnostic cohorts"
        ),
        "blocks": blocks,
    }
    files["configs/matrix.json"] = evidence.encoded(derived_matrix)
    files["selection.json"] = evidence.encoded(selection.model_dump(mode="json"))
    return files, derived_matrix


@contextmanager
def adapted(root, block):
    previous = evidence.BLOCK
    try:
        evidence.BLOCK = block
        with evidence.adapted(root):
            yield
    finally:
        evidence.BLOCK = previous


def validate_plans(root, matrix):
    for block in matrix["blocks"]:
        plan = evidence.read(Path(block["plan"]))
        with adapted(root, block["block_id"]):
            evidence.validate_resume(root, plan, matrix)


def materialize(root, selection_path):
    selection = Selection.model_validate_json(selection_path.read_text())
    evidence.require(
        not selection_path.resolve().is_relative_to(root), "Selection must be external"
    )
    with evidence.formal_locks():
        parent = evidence.reviewed_parent()
        files, matrix = derive(selection, root, parent)
        hashes = parent[3] | evidence.local_runtime_hashes()
        hashes[str(WRAPPER)] = sha256_file(WRAPPER)
        hashes[selection.qualification_source] = selection.qualification_sha256
        hashes.update(evidence.read(Path(selection.qualification_source)).get("source_hashes", {}))
        files["receipt.json"] = evidence.encoded(
            {
                "kind": "qualified_fresh_seed_repeats",
                "selection_source": str(selection_path.resolve()),
                "selection_sha256": sha256_file(selection_path),
                "source_hashes": hashes,
            }
        )
        root.mkdir(parents=True, exist_ok=True)
        with evidence.locked([root / ".adapter.lock"], create=True):
            for name, data in files.items():
                path = root / name
                evidence.require(
                    not path.exists() or path.read_bytes() == data,
                    f"Frozen preparation changed: {name}",
                )
            for name, data in files.items():
                path = root / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(path)
            validate_plans(root, matrix)
    return {
        "status": "prepared_not_launched",
        "blocks": len(matrix["blocks"]),
        "episodes": sum(b["expected_episodes"] for b in matrix["blocks"]),
    }


def inspect(root):
    receipt = evidence.read(root / "receipt.json")
    path = Path(receipt["selection_source"])
    evidence.require(sha256_file(path) == receipt["selection_sha256"], "Selection changed")
    for name, digest in receipt["source_hashes"].items():
        evidence.require(sha256_file(Path(name)) == digest, f"Frozen input changed: {name}")
    selection = Selection.model_validate_json(path.read_text())
    parent = evidence.reviewed_parent()
    files, matrix = derive(selection, root, parent)
    for name, data in files.items():
        evidence.require((root / name).read_bytes() == data, f"Frozen plan changed: {name}")
    expected = {Path(name).name for name in files if name.startswith("configs/")}
    evidence.require(
        {p.name for p in (root / "configs").iterdir()} == expected, "Unselected config"
    )
    validate_plans(root, matrix)
    return matrix


def archive_block(root, block):
    directory = root / block
    campaign = evidence.read(directory / "campaign.json")
    audit = evidence.read(directory / "collection-audit.json")
    evidence.require(
        campaign["status"] == "completed" and audit["checks"] == "passed",
        "Only completed, audited blocks can be archived",
    )
    paths = {
        p for p in (directory / "runs").rglob("*") if p.is_file() and not p.name.startswith(".")
    }
    paths.update({directory / "campaign.json", directory / "collection-audit.json"})
    paths.update(p for p in (root / "configs").glob("*") if p.is_file())
    paths.update({root / "selection.json", root / "receipt.json", WRAPPER})
    paths.update(Path("src/agent_tradeoffs").glob("*.py"))
    paths.update(
        Path(name)
        for name in (
            "scripts/phase2_matrix.py",
            "scripts/phase2_seed_diagnostics.py",
            "scripts/audit_phase2_spike.py",
            "scripts/phase2_collect.py",
            "scripts/analyze_phase2_matrix.py",
            "uv.lock",
            "pyproject.toml",
            "results/phase2-calibration/selected-protocol.json",
            "configs/phase2-calibration/plan-before-action.json",
        )
    )
    members = []
    for path in sorted(paths):
        relative = path.resolve().relative_to(Path.cwd())
        evidence.require(not path.is_symlink(), "Archive symlink refused")
        members.append(
            {"path": str(relative), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    archive = root / "archives" / f"{block}.tar.gz"
    manifest = archive.with_suffix("").with_suffix(".manifest.json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if manifest.exists():
        record = evidence.read(manifest)
        evidence.require(record["members"] == members, "Archived block inputs changed")
        verify_archive(archive, record)
        return record
    temporary = archive.with_suffix(".tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        for member in members:
            data = Path(member["path"]).read_bytes()
            evidence.require(
                hashlib.sha256(data).hexdigest() == member["sha256"], "Archive input changed"
            )
            item = tarfile.TarInfo(member["path"])
            item.size = len(data)
            item.mode = 0o644
            bundle.addfile(item, io.BytesIO(data))
    temporary.replace(archive)
    record = {
        "kind": "repeat_collection_evidence_review_separate",
        "block": block,
        "episodes": audit["episodes_checked"],
        "review_gate": "pending",
        "original_root": str(Path.cwd()),
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256_file(archive),
        "members": members,
    }
    verify_archive(archive, record)
    atomic_json(manifest, record)
    return record


def operate(command, root, block, max_blocks=1):
    matrix = evidence.read(root / "configs/matrix.json")
    if command == "status":
        states = []
        for entry in matrix["blocks"]:
            with adapted(root, entry["block_id"]):
                state = evidence.driver.status(entry["block_id"])
                states.append({k: v for k, v in state.items() if k != "episodes"})
        return {
            "blocks": states,
            "finalized": sum(s["finalized"] for s in states),
            "expected": sum(s["expected"] for s in states),
            "review_gate": "separate_pending_review",
        }
    with evidence.formal_locks(), evidence.locked([root / ".adapter.lock"]):
        matrix = inspect(root)
        if command in {"run", "queue"}:
            evidence.require(max_blocks >= 1, "max-blocks must be positive")
            if command == "run":
                evidence.require(
                    block in {b["block_id"] for b in matrix["blocks"]},
                    "Select one declared block",
                )
                selected = [block]
            else:
                selected = []
                for item in matrix["blocks"]:
                    with adapted(root, item["block_id"]):
                        state = evidence.driver.status(item["block_id"])
                    if state["status"] != "completed":
                        selected.append(item["block_id"])
                selected = selected[:max_blocks]
            evidence.vacant_ports()
            receipt = {
                "status": "running",
                "pid": os.getpid(),
                "selected_blocks": selected,
                "completed_blocks": [],
                "active_block": None,
            }
            try:
                for current in selected:
                    receipt["active_block"] = current
                    atomic_json(root / "queue.json", receipt)
                    with adapted(root, current):
                        evidence.driver.run(current)
                    inspect(root)
                    archive_block(root, current)
                    receipt["completed_blocks"].append(current)
                receipt.update(status="selected_blocks_complete", active_block=None)
                atomic_json(root / "queue.json", receipt)
            except BaseException as error:
                receipt.update(status="stopped", error=f"{type(error).__name__}: {error}")
                atomic_json(root / "queue.json", receipt)
                raise
        return {"status": "validated", "block": block, "review_gate": "separate_pending_review"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "validate", "run", "queue", "status"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--block")
    parser.add_argument("--max-blocks", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        root = output_root(args.root)
        if args.command == "prepare":
            evidence.require(args.selection is not None, "prepare needs --selection")
            result = materialize(root, args.selection)
        else:
            result = operate(args.command, root, args.block, args.max_blocks)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
