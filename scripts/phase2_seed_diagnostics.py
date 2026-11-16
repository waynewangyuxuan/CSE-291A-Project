"""Prepare explicitly selected seed-29 pairs after the fully reviewed formal collection."""

from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import hashlib
import io
import json
import re
import socket
import subprocess
import sys
import tomllib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Annotated, Literal

import analyze_phase2_matrix as analysis
import audit_phase2_spike as auditor
import phase2_matrix as driver
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_tradeoffs.config import ExperimentConfig, stable_hash
from agent_tradeoffs.runner import implementation_identity, plan_episodes
from agent_tradeoffs.runtime import load_runtime_config, server_command, sha256_file

FORMAL_CONFIGS = Path("configs/phase2-main-v1")
FORMAL_OUTPUT = Path("artifacts/phase2-main-v1")
PUBLIC = Path("results/phase2-main-v1")
WRAPPER = Path(__file__).resolve()
BLOCK = "seed-29"
Name = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Side(FrozenInput):
    precision: Literal["f16", "q8", "q4"]
    policy: Literal["full", "truncate", "summary"]


class Task(FrozenInput):
    suite: Literal["banking", "slack", "workspace", "travel"]
    task: Annotated[str, Field(pattern=r"^user_task_[0-9]+$")]
    block: Annotated[str, Field(pattern=r"^(main|confirmation)-[0-9]{3}$")]
    source_runs: tuple[Name, Name]


class Hypothesis(FrozenInput):
    id: Name
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    sides: tuple[Side, Side]
    tasks: Annotated[list[Task], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def matched_sides(self):
        if not self.question.strip() or self.sides[0] == self.sides[1]:
            raise ValueError("A hypothesis needs a question and two distinct configuration sides")
        return self


class Selection(FrozenInput):
    schema_version: Literal[1]
    seed: Literal[29]
    reviewed_table_sha256: Digest
    hypotheses: Annotated[list[Hypothesis], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def bounded_pairs(self):
        names = [h.id for h in self.hypotheses]
        cells = [
            (task.suite, task.task, side.precision, side.policy)
            for h in self.hypotheses
            for task in h.tasks
            for side in h.sides
        ]
        if len(names) != len(set(names)) or len(cells) != len(set(cells)):
            raise ValueError("Duplicate hypothesis or task/configuration pair")
        if len(cells) * 2 > 32:
            raise ValueError("Seed-29 diagnostics are capped at 32 episodes")
        return self


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def output_root(path):
    path = Path(path).absolute()
    require(
        path.parent == Path.cwd() / "artifacts"
        and re.fullmatch(r"phase2-seed-diagnostics-[a-z0-9][a-z0-9-]*", path.name),
        "Use a separate artifacts/phase2-seed-diagnostics-<name> root",
    )
    require(path.resolve() == path, "Supplementary root cannot be a symlink")
    require(not any(p.is_symlink() for p in path.rglob("*")), "Supplementary symlink refused")
    return path


@contextmanager
def locked(paths, *, create=False):
    with ExitStack() as stack:
        for path in paths:
            handle = stack.enter_context(path.open("a" if create else "r"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(f"Active collection/diagnostic owns {path}") from error
        yield


def formal_locks():
    return locked([FORMAL_OUTPUT / ".queue.lock", FORMAL_OUTPUT / ".lock"])


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        )
    return output.getvalue().encode()


def reviewed_parent():
    matrix_path = FORMAL_CONFIGS / "matrix.json"
    matrix = read(matrix_path)
    queue_path = FORMAL_OUTPUT / "queue.json"
    queue = read(queue_path)
    require(
        queue["status"] == "collection_complete" and queue.get("active_block") is None,
        "Original queue is not complete",
    )
    require(
        queue["identity"]
        == {
            "matrix_sha256": sha256_file(matrix_path),
            "controller_sha256": sha256_file(Path("scripts/phase2_collect.py")),
        }
        and queue["archived_blocks"] == [b["block_id"] for b in matrix["blocks"]],
        "Original queue identity/coverage differs",
    )
    report_path = PUBLIC / "analysis/measurements.json"
    report = read(report_path)
    require(
        report["status"] == "final_reviewed"
        and all(
            report[k] == 1098
            for k in ("expected_episodes", "included_episodes", "reviewed_episodes")
        ),
        "Final reviewed 1098-episode export is required",
    )
    require(
        matrix["implementation"] == implementation_identity()
        and matrix["driver_sha256"] == sha256_file(Path(driver.__file__))
        and matrix["protocol_sha256"] == sha256_file(driver.PROTOCOL)
        and matrix["template_sha256"] == sha256_file(driver.TEMPLATES)
        and report["matrix_sha256"] == sha256_file(matrix_path)
        and report["analysis_sha256"] == sha256_file(Path(analysis.__file__)),
        "Frozen parent implementation/protocol/export changed",
    )
    for name, digest in matrix["runtime_configs_sha256"].items():
        require(
            sha256_file(FORMAL_CONFIGS / f"runtime-{name}.toml") == digest, "Parent runtime changed"
        )
    rows, blocks = analysis.load_rows()
    require(
        len(rows) == 1098
        and all(r["review_status"] == "reviewed" for r in rows)
        and blocks == report["blocks"]
        and rows == read(PUBLIC / "analysis/episodes.json")
        and csv_bytes(rows) == (PUBLIC / "analysis/episodes.csv").read_bytes(),
        "Reviewed tables differ from original evidence",
    )
    paths = {
        WRAPPER,
        Path(driver.__file__),
        Path(analysis.__file__),
        Path(auditor.__file__),
        Path("scripts/phase2_collect.py"),
        Path("uv.lock"),
        Path("pyproject.toml"),
        driver.PROTOCOL,
        driver.TEMPLATES,
        matrix_path,
        queue_path,
        report_path,
        PUBLIC / "analysis/episodes.json",
        PUBLIC / "analysis/episodes.csv",
        *Path("src/agent_tradeoffs").glob("*.py"),
        *FORMAL_CONFIGS.glob("runtime-*.toml"),
    }
    plans, expected = {}, set()
    for block in matrix["blocks"]:
        block_id = block["block_id"]
        _, plan, plan_path = driver.load_block(block_id)
        plans[block_id] = plan
        campaign = read(FORMAL_OUTPUT / block_id / "campaign.json")
        require(
            campaign["status"] == "completed"
            and not (FORMAL_OUTPUT / block_id / "invalid-deployment.json").exists(),
            "Original block is incomplete or has deployment drift",
        )
        review = PUBLIC / "reviews" / f"{block_id}.csv"
        verification = review.with_suffix(".verification.json")
        gate = read(verification)
        require(
            gate["checks"] == "passed"
            and gate["partial"] is False
            and all(
                gate[k] == plan["expected_episodes"]
                for k in ("episodes_checked", "reviewed_episodes", "expected_episodes")
            )
            and gate["plan_sha256"] == sha256_file(plan_path)
            and gate["annotations_sha256"] == sha256_file(review)
            and gate["auditor_sha256"] == sha256_file(Path(auditor.__file__)),
            "Complete original review gate is missing or stale",
        )
        archive = FORMAL_OUTPUT / "archives" / f"{block_id}.tar.gz"
        inventory = archive.with_suffix("").with_suffix(".manifest.json")
        for member in read(inventory)["members"]:
            path = Path(member["path"])
            require(
                path.resolve().is_relative_to(Path.cwd()), "Original archive path escapes checkout"
            )
            require(sha256_file(path) == member["sha256"], f"Original evidence changed: {path}")
            paths.add(path)
        paths.update((plan_path, review, verification, archive, inventory))
        checked = {(r["run"], r["episode_id"]): r["identity"] for r in gate["episodes"]}
        for entry in plan["runs"]:
            for episode in plan_episodes(ExperimentConfig.model_validate(entry["config"])):
                key = block_id, entry["run_id"], episode["episode_id"]
                expected.add(key)
                matching = [r for r in rows if (r["block"], r["run_id"], r["episode_id"]) == key]
                require(
                    len(matching) == 1 and checked.get(key[1:]) == matching[0]["identity"],
                    "Original task/pair review identity differs",
                )
    require(
        len(expected) == 1098
        and expected == {(r["block"], r["run_id"], r["episode_id"]) for r in rows},
        "Original task/pair coverage differs",
    )
    return matrix, plans, rows, {str(p): sha256_file(p) for p in sorted(paths)}


def local_runtime_hashes():
    pins = {}
    for role in (*driver.FORMATS, "summarizer"):
        config = load_runtime_config(FORMAL_CONFIGS / f"runtime-{role}.toml")
        require(config.port == (8086 if role == "summarizer" else 8087), "Parent ports changed")
        path = config.model_path
        manifest = read(path.with_suffix(".manifest.json"))
        require(
            path.is_file()
            and manifest["sha256"] == config.model_artifact.sha256
            and manifest["revision"] == config.model_artifact.revision
            and manifest["size_bytes"] == path.stat().st_size
            and manifest["mtime_ns"] == path.stat().st_mtime_ns,
            "Pinned local model verification is missing/stale; no downloads are permitted",
        )
        binary = config.resolve(config.binary_path)
        for item in (
            path,
            path.with_suffix(".manifest.json"),
            path.with_suffix(".provenance.json"),
            binary,
            *sorted(binary.parent.glob("*.dylib")),
        ):
            if str(item) not in pins:
                pins[str(item)] = sha256_file(item)
        require(pins[str(path)] == config.model_artifact.sha256, "Pinned local model hash differs")
    return pins


def derive(selection, root, parent):
    matrix, plans, rows, _ = parent
    require(
        selection.reviewed_table_sha256 == sha256_file(PUBLIC / "analysis/episodes.csv"),
        "Selected reviewed-table hash differs",
    )
    runs = []
    for hypothesis in selection.hypotheses:
        for task in hypothesis.tasks:
            require(task.block in plans, "Source block is not in the original matrix")
            for side, run_id in zip(hypothesis.sides, task.source_runs, strict=True):
                originals = [r for r in plans[task.block]["runs"] if r["run_id"] == run_id]
                require(len(originals) == 1, "Source run is not in the selected block")
                original = originals[0]
                require(
                    (
                        original["suite"],
                        original["user_task_id"],
                        original["precision"],
                        original["policy"],
                    )
                    == (task.suite, task.task, side.precision, side.policy),
                    "Source run does not match the selected task/configuration side",
                )
                config = copy.deepcopy(original["config"])
                require(
                    config["model"]["seed"] == 17 and config["experiment"]["mode"] == "paired",
                    "Original pair/seed differs",
                )
                config["model"]["seed"] = 29
                config["runner"]["output_root"] = str(root / BLOCK / "runs")
                config["runner"]["runtime_metadata_path"] = str(
                    root / "runtime" / side.precision / "server.json"
                )
                if side.policy == "summary":
                    config["context"]["runtime_metadata_path"] = str(
                        root / "runtime/summarizer/server.json"
                    )
                pairs = [r for r in rows if r["block"] == task.block and r["run_id"] == run_id]
                require(
                    len(pairs) == 2 and {r["condition"] for r in pairs} == {"clean", "attack"},
                    "Source is not one clean/attack pair",
                )
                source_manifest = (
                    Path(original["config"]["runner"]["output_root"]) / run_id / "manifest.json"
                )
                manifest = read(source_manifest)
                require(
                    len({r["identity"] for r in pairs}) == 1
                    and pairs[0]["identity"] == manifest["identity"],
                    "Source pair identity differs",
                )
                runs.append(
                    {
                        **{
                            k: original[k]
                            for k in ("run_id", "precision", "policy", "suite", "user_task_id")
                        },
                        "config": config,
                        "source_pair": {
                            "hypothesis": hypothesis.id,
                            "block": task.block,
                            "run_id": run_id,
                            "identity": manifest["identity"],
                            "manifest_sha256": sha256_file(source_manifest),
                            "runtime": manifest["runtime"]["semantic_identity"],
                            "summarizer_runtime": manifest["summarizer_runtime"][
                                "semantic_identity"
                            ]
                            if manifest["summarizer_runtime"]
                            else None,
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
    require(len({r["run_id"] for r in runs}) == len(runs), "Duplicate derived run")
    plan = {
        "block_id": BLOCK,
        "cohort": "outcome_selected_seed29_diagnostics",
        "task_order": list(dict.fromkeys((r["suite"], r["user_task_id"]) for r in runs)),
        "precision_order": list(dict.fromkeys(r["precision"] for r in runs)),
        "budget_seconds": 36000,
        "expected_episodes": len(runs) * 2,
        "runs": runs,
    }
    files = {
        "selection.json": encoded(selection.model_dump(mode="json")),
        f"configs/{BLOCK}.json": encoded(plan),
    }
    runtime_hashes = {}
    for role in (*driver.FORMATS, "summarizer"):
        source = (FORMAL_CONFIGS / f"runtime-{role}.toml").read_text()
        text, count = re.subn(
            r'^output_root = "[^"\n]+"$',
            f'output_root = "{root / "runtime" / role}"',
            source,
            flags=re.MULTILINE,
        )
        require(count == 1, "Expected one runtime output_root")
        before, after = tomllib.loads(source), tomllib.loads(text)
        before["runtime"]["output_root"] = str(root / "runtime" / role)
        require(before == after, "Runtime changes exceed output-root isolation")
        data = text.encode()
        files[f"configs/runtime-{role}.toml"] = data
        runtime_hashes[role] = hashlib.sha256(data).hexdigest()
    supplementary = {
        "campaign_id": root.name,
        "implementation": matrix["implementation"],
        "driver_sha256": matrix["driver_sha256"],
        "protocol_sha256": matrix["protocol_sha256"],
        "template_sha256": matrix["template_sha256"],
        "runtime_configs_sha256": runtime_hashes,
        "scope": "Outcome-selected matched seed-29 diagnostics; never replaces original outcomes",
        "blocks": [
            {
                "block_id": BLOCK,
                "plan": str(root / "configs" / f"{BLOCK}.json"),
                "sha256": hashlib.sha256(files[f"configs/{BLOCK}.json"]).hexdigest(),
                "expected_episodes": plan["expected_episodes"],
            }
        ],
    }
    files["configs/matrix.json"] = encoded(supplementary)
    return files, json.loads(encoded(plan)), supplementary


@contextmanager
def adapted(root):
    original = driver.CONFIGS, driver.OUTPUT
    try:
        driver.CONFIGS, driver.OUTPUT = root / "configs", root
        yield
    finally:
        driver.CONFIGS, driver.OUTPUT = original


def validate_resume(root, plan, matrix):
    for role in (*driver.FORMATS, "summarizer"):
        runtime = load_runtime_config(root / "configs" / f"runtime-{role}.toml")
        metadata_path = root / "runtime" / role / "server.json"
        if metadata_path.exists():
            metadata = read(metadata_path)
            require(
                metadata["command"] == server_command(runtime)
                and Path(metadata["log_path"]).resolve().is_relative_to(root / "runtime" / role),
                "Supplementary runtime metadata escapes its owned deployment",
            )
    path = root / BLOCK / "campaign.json"
    runs_root = root / BLOCK / "runs"
    require(
        not (root / BLOCK / "invalid-deployment.json").exists(),
        "Supplementary deployment drift requires review",
    )
    if not path.exists():
        require(not runs_root.exists(), "Supplementary runs have no campaign identity")
        return
    state = read(path)
    require(
        state["identity"]
        == {
            "plan_sha256": stable_hash(plan),
            "implementation": matrix["implementation"],
            "driver_sha256": matrix["driver_sha256"],
            "matrix_sha256": sha256_file(root / "configs/matrix.json"),
        },
        "Supplementary resume identity mismatch",
    )
    require(
        {p.name for p in runs_root.iterdir()} <= {r["run_id"] for r in plan["runs"]}
        if runs_root.exists()
        else True,
        "Unselected run in supplementary output",
    )
    for entry in plan["runs"]:
        directory = runs_root / entry["run_id"]
        manifest_path = directory / "manifest.json"
        if not directory.exists():
            require(state["status"] != "completed", "Completed campaign lacks selected pair")
            continue
        manifest = read(manifest_path)
        config = ExperimentConfig.model_validate(entry["config"])
        saved = copy.deepcopy(manifest["config"])
        saved["runner"]["server_pid"] = None
        semantic = manifest["runtime"]["semantic_identity"]
        summary = (
            manifest["summarizer_runtime"]["semantic_identity"]
            if manifest["summarizer_runtime"]
            else None
        )
        episodes = plan_episodes(config)
        identity = stable_hash(
            {
                "config": config.identity_config(),
                "episodes": episodes,
                "implementation": matrix["implementation"],
                "runtime": semantic,
                "summarizer_runtime": summary,
            }
        )
        require(
            saved == entry["config"]
            and manifest["episodes"] == episodes
            and manifest["implementation"] == matrix["implementation"]
            and semantic == entry["source_pair"]["runtime"]
            and summary == entry["source_pair"]["summarizer_runtime"]
            and manifest["identity"] == identity,
            "Supplementary pair resume identity mismatch",
        )
        episode_root = directory / "episodes"
        require(
            {p.name for p in episode_root.iterdir()} <= {e["episode_id"] for e in episodes}
            if episode_root.exists()
            else True,
            "Unselected episode in supplementary pair",
        )
        for episode in episodes:
            episode_dir = episode_root / episode["episode_id"]
            result = episode_dir / "result.json"
            complete = (episode_dir / "compression.json").exists()
            if result.exists():
                value = read(result)
                require(
                    value["identity"] == identity
                    and all(value[k] == v for k, v in episode.items()),
                    "Supplementary result identity mismatch",
                )
            if complete:
                auditor.verify_episode(episode_dir, config, manifest)
            if state["status"] == "completed" or manifest["status"] == "finalized":
                require(
                    result.exists() and complete and not read(result).get("retryable"),
                    "Finalized supplementary pair is incomplete",
                )


def inspect(root):
    receipt = read(root / "receipt.json")
    selection_path = Path(receipt["selection_source"])
    require(sha256_file(selection_path) == receipt["selection_sha256"], "Selection source changed")
    selection = Selection.model_validate_json(selection_path.read_text())
    parent = reviewed_parent()
    require(
        parent[3] | local_runtime_hashes() == receipt["source_hashes"], "Frozen source set changed"
    )
    files, plan, matrix = derive(selection, root, parent)
    for name, data in files.items():
        require((root / name).read_bytes() == data, f"Frozen supplementary content changed: {name}")
    require(
        {p.name for p in (root / "configs").iterdir()}
        == {Path(n).name for n in files if n.startswith("configs/")},
        "Unselected supplementary config",
    )
    validate_resume(root, plan, matrix)
    return plan


def prepare(root, selection_path):
    require(
        not selection_path.resolve().is_relative_to(root),
        "Selection must be supplied outside its output root",
    )
    selection = Selection.model_validate_json(selection_path.read_text())
    with formal_locks():
        parent = reviewed_parent()
        files, plan, matrix = derive(selection, root, parent)
        receipt = {
            "kind": "outcome_selected_seed29_paired_diagnostics",
            "selection_source": str(selection_path.resolve()),
            "selection_sha256": sha256_file(selection_path),
            "source_hashes": parent[3] | local_runtime_hashes(),
        }
        files["receipt.json"] = encoded(receipt)
        root.mkdir(parents=True, exist_ok=True)
        with locked([root / ".adapter.lock"], create=True):
            for name, data in files.items():
                path = root / name
                require(
                    not path.exists() or path.read_bytes() == data,
                    f"Incompatible preparation: {name}",
                )
            if (root / BLOCK / "campaign.json").exists():
                validate_resume(root, plan, matrix)
            for name, data in files.items():
                path = root / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(path.suffix + ".tmp")
                    temporary.write_bytes(data)
                    temporary.replace(path)
        return {
            "status": "prepared_not_launched",
            "episodes": plan["expected_episodes"],
            "root": str(root),
        }


def vacant_ports():
    for role in (*driver.FORMATS, "summarizer"):
        config = load_runtime_config(FORMAL_CONFIGS / f"runtime-{role}.toml")
        metadata_path = config.resolve(config.output_root) / "server.json"
        if metadata_path.exists():
            metadata = read(metadata_path)
            try:
                driver.guard_runtime_process(metadata["pid"], metadata["process_identity"])
            except driver.RuntimeDeploymentChangedError:
                pass
            else:
                raise ValueError("An original inference process is still active")
    for role in ("f16", "summarizer"):
        config = load_runtime_config(FORMAL_CONFIGS / f"runtime-{role}.toml")
        family = socket.AF_INET6 if ":" in config.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as check:
            check.settimeout(1)
            require(
                check.connect_ex((config.host, config.port)) != 0,
                f"Port {config.port} is occupied; no service will be stopped",
            )


def operate(command, root, annotations=None):
    if command == "status":
        # Collection status is observational; full validation stays an explicit
        # operation, so querying progress never competes with the active run.
        with adapted(root):
            state = driver.status(BLOCK)
        return {
            **state,
            "scope": "outcome_selected_seed29_diagnostics",
            "validation": "status_only_not_full_evidence_validation",
        }
    with formal_locks(), locked([root / ".adapter.lock"], create=False):
        inspect(root)
        with adapted(root):
            if command == "run":
                vacant_ports()
                driver.run(BLOCK)
            if annotations is not None:
                require(command == "validate", "Annotations are only accepted by validate")
                require(
                    driver.status(BLOCK)["status"] == "completed",
                    "Full review requires completed supplementary collection",
                )
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(auditor.__file__)),
                        "--plan",
                        str(root / "configs" / f"{BLOCK}.json"),
                        "--annotations",
                        str(annotations),
                        "--output",
                        str(root / BLOCK / "review-audit.json"),
                    ],
                    check=True,
                )
            state = driver.status(BLOCK)
            if annotations is not None:
                state["review_gate"] = "passed"
        if command == "run":
            inspect(root)
        return {
            **state,
            "scope": "outcome_selected_seed29_diagnostics",
            "validation": "offline_identity_and_evidence_checks",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "validate", "run", "status"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args(argv)
    if (args.command == "prepare") != (args.selection is not None):
        parser.error("Only prepare requires --selection")
    if args.annotations and args.command != "validate":
        parser.error("Only validate accepts --annotations")
    try:
        root = output_root(args.root)
        result = (
            prepare(root, args.selection)
            if args.command == "prepare"
            else operate(args.command, root, args.annotations)
        )
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Seed diagnostics refused: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
