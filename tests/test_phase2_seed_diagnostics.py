"""Offline diagnostic selection, provenance and exclusion tests; no model execution."""

import copy
import csv
import fcntl
import hashlib
import importlib
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    return importlib.import_module("phase2_seed_diagnostics")


@pytest.fixture
def parent(tmp_path, monkeypatch, adapter):
    for name in ("configs", "src/agent_tradeoffs"):
        shutil.copytree(REPO / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in (
        "uv.lock",
        "pyproject.toml",
        "results/phase2-calibration/selected-protocol.json",
        "scripts/phase2_matrix.py",
        "scripts/phase2_collect.py",
        "scripts/analyze_phase2_matrix.py",
        "scripts/audit_phase2_spike.py",
        "scripts/phase2_seed_diagnostics.py",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(adapter, "WRAPPER", tmp_path / "scripts/phase2_seed_diagnostics.py")
    root = tmp_path / "artifacts/phase2-main-v1"
    root.mkdir(parents=True)
    for lock in (".lock", ".queue.lock"):
        (root / lock).touch()
    matrix_path = Path("configs/phase2-main-v1/matrix.json")
    matrix = adapter.read(matrix_path)
    rows, blocks = [], []
    for block in matrix["blocks"]:
        block_id = block["block_id"]
        plan_path = Path(block["plan"])
        plan = adapter.read(plan_path)
        write(root / block_id / "campaign.json", {"status": "completed"})
        members, audit_rows = [], []
        for entry in plan["runs"]:
            identity = hashlib.sha256((block_id + entry["run_id"]).encode()).hexdigest()
            directory = Path(entry["config"]["runner"]["output_root"]) / entry["run_id"]
            manifest = {
                "identity": identity,
                "config": entry["config"],
                "runtime": {"semantic_identity": {"model": entry["precision"]}},
                "summarizer_runtime": {"semantic_identity": {"model": "f16"}}
                if entry["policy"] == "summary"
                else None,
            }
            write(directory / "manifest.json", manifest)
            members.append(
                {
                    "path": str(directory / "manifest.json"),
                    "sha256": digest(directory / "manifest.json"),
                }
            )
            for episode in adapter.plan_episodes(
                adapter.ExperimentConfig.model_validate(entry["config"])
            ):
                source = directory / "episodes" / episode["episode_id"]
                rows.append(
                    {
                        "block": block_id,
                        "cohort": plan["cohort"],
                        "run_id": entry["run_id"],
                        "episode_id": episode["episode_id"],
                        "identity": identity,
                        "condition": episode["condition"],
                        "source": str(source),
                        "trace_sha256": "1" * 64,
                        "result_sha256": "2" * 64,
                        "review_status": "reviewed",
                    }
                )
                audit_rows.append(
                    {
                        "run": entry["run_id"],
                        "episode_id": episode["episode_id"],
                        "identity": identity,
                    }
                )
        review = Path("results/phase2-main-v1/reviews") / f"{block_id}.csv"
        review.parent.mkdir(parents=True, exist_ok=True)
        with review.open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=("run", "episode_id", "identity"))
            writer.writeheader()
            writer.writerows(audit_rows)
        write(
            review.with_suffix(".verification.json"),
            {
                "checks": "passed",
                "partial": False,
                "episodes_checked": plan["expected_episodes"],
                "reviewed_episodes": plan["expected_episodes"],
                "expected_episodes": plan["expected_episodes"],
                "plan_sha256": digest(plan_path),
                "annotations_sha256": digest(review),
                "auditor_sha256": digest(Path(adapter.auditor.__file__)),
                "episodes": audit_rows,
            },
        )
        archive = root / "archives" / f"{block_id}.tar.gz"
        archive.parent.mkdir(exist_ok=True)
        archive.write_bytes(b"synthetic archive boundary; not runtime proof")
        write(archive.with_suffix("").with_suffix(".manifest.json"), {"members": members})
        blocks.append({"block": block_id, "episodes": len(audit_rows), "reviewed": len(audit_rows)})
    table = Path("results/phase2-main-v1/analysis/episodes.csv")
    table.parent.mkdir(parents=True)
    table.write_bytes(adapter.csv_bytes(rows))
    write(table.with_suffix(".json"), rows)
    write(
        table.with_name("measurements.json"),
        {
            "status": "final_reviewed",
            "expected_episodes": 1098,
            "included_episodes": 1098,
            "reviewed_episodes": 1098,
            "matrix_sha256": digest(matrix_path),
            "analysis_sha256": digest(Path(adapter.analysis.__file__)),
            "blocks": blocks,
        },
    )
    write(
        root / "queue.json",
        {
            "status": "collection_complete",
            "active_block": None,
            "identity": {
                "matrix_sha256": digest(matrix_path),
                "controller_sha256": digest(Path("scripts/phase2_collect.py")),
            },
            "archived_blocks": [b["block_id"] for b in matrix["blocks"]],
        },
    )
    monkeypatch.setattr(
        adapter.analysis, "load_rows", lambda: (copy.deepcopy(rows), copy.deepcopy(blocks))
    )
    model = tmp_path / "verified-local-model"
    model.write_bytes(b"synthetic pinned artifact")
    monkeypatch.setattr(adapter, "local_runtime_hashes", lambda: {str(model): digest(model)})
    selection = {
        "schema_version": 1,
        "seed": 29,
        "reviewed_table_sha256": digest(table),
        "hypotheses": [
            {
                "id": "selected-flip",
                "question": "Does the selected matched flip persist at seed 29?",
                "sides": [
                    {"precision": "f16", "policy": "full"},
                    {"precision": "q4", "policy": "summary"},
                ],
                "tasks": [
                    {
                        "suite": "banking",
                        "task": "user_task_1",
                        "block": "main-001",
                        "source_runs": ["f16-full-banking1", "q4-summary-banking1"],
                    }
                ],
            }
        ],
    }
    write(tmp_path / "selection.json", selection)
    return tmp_path


def invoke(adapter, parent, command, *extra):
    args = [command, "--root", str(parent / "artifacts/phase2-seed-diagnostics-test"), *extra]
    if command == "prepare":
        args += ["--selection", str(parent / "selection.json")]
    adapter.main(args)


def mutate(path, change):
    value = json.loads(path.read_text())
    change(value)
    write(path, value)


def test_exact_selected_pairs_and_only_seed_and_output_paths_change(adapter, parent):
    invoke(adapter, parent, "prepare")
    root = parent / "artifacts/phase2-seed-diagnostics-test"
    plan = adapter.read(root / "configs/seed-29.json")
    with adapter.adapted(root):
        assert adapter.driver.load_block("seed-29")[1] == plan
        assert adapter.driver.status("seed-29")["expected"] == 4
    assert plan["expected_episodes"] == 4
    assert [r["run_id"] for r in plan["runs"]] == ["f16-full-banking1", "q4-summary-banking1"]
    originals = {
        r["run_id"]: r for r in adapter.read(Path("configs/phase2-main-v1/main-001.json"))["runs"]
    }
    for row in plan["runs"]:
        expected = copy.deepcopy(originals[row["run_id"]]["config"])
        expected["model"]["seed"] = 29
        expected["runner"]["output_root"] = str(root / "seed-29/runs")
        expected["runner"]["runtime_metadata_path"] = str(
            root / "runtime" / row["precision"] / "server.json"
        )
        if row["policy"] == "summary":
            expected["context"]["runtime_metadata_path"] = str(
                root / "runtime/summarizer/server.json"
            )
            assert expected["context"]["summarizer"]["seed"] == 0
        assert row["config"] == expected
        assert row["source_pair"]["identity"]
        assert [r["condition"] for r in row["source_pair"]["episodes"]] == ["clean", "attack"]
    before = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()
    }
    invoke(adapter, parent, "prepare")
    invoke(adapter, parent, "validate")
    invoke(adapter, parent, "status")
    assert before == {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()
    }
    assert not (root / "runtime").exists()
    assert not (root / "seed-29").exists()


def test_progress_is_read_only_while_all_collection_locks_are_held(
    adapter, parent, monkeypatch, capsys
):
    invoke(adapter, parent, "prepare")
    root = parent / "artifacts/phase2-seed-diagnostics-test"
    write(
        root / "seed-29/campaign.json",
        {"status": "running", "active_run": "f16-full-banking1", "pid": 123},
    )
    capsys.readouterr()
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError("A progress query must not validate models or operate inference")

    monkeypatch.setattr(adapter, "inspect", forbidden)
    monkeypatch.setattr(adapter, "local_runtime_hashes", forbidden)
    monkeypatch.setattr(adapter, "vacant_ports", forbidden)
    monkeypatch.setattr(adapter.driver, "run", forbidden)
    with adapter.formal_locks(), adapter.locked([root / ".adapter.lock"]):
        invoke(adapter, parent, "status")
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "running"
    assert result["active_run"] == "f16-full-banking1"
    assert result["finalized"] == 0 and result["expected"] == 4
    assert result["validation"] == "status_only_not_full_evidence_validation"
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(seed=17),
        lambda s: s.update(agent_temperature=0),
        lambda s: s["hypotheses"][0]["sides"][0].update(budget=9999),
        lambda s: s["hypotheses"][0]["tasks"].append(copy.deepcopy(s["hypotheses"][0]["tasks"][0])),
        lambda s: s["hypotheses"][0].update(sides=s["hypotheses"][0]["sides"][:1]),
        lambda s: s["hypotheses"][0].update(question=" "),
        lambda s: s["hypotheses"][0]["tasks"][0]["source_runs"].reverse(),
        lambda s: s.update(reviewed_table_sha256="0" * 64),
    ],
)
def test_invalid_or_unmatched_selection_creates_nothing(adapter, parent, change):
    mutate(parent / "selection.json", change)
    with pytest.raises(SystemExit):
        invoke(adapter, parent, "prepare")
    assert not (parent / "artifacts/phase2-seed-diagnostics-test").exists()


def test_cap_is_over_distinct_complete_pairs(adapter):
    def hypothesis(n):
        return {
            "id": f"h-{n}",
            "question": "Named follow-up",
            "sides": [
                {"precision": "f16", "policy": "full"},
                {"precision": "q4", "policy": "full"},
            ],
            "tasks": [
                {
                    "suite": "travel",
                    "task": f"user_task_{i}",
                    "block": "main-001",
                    "source_runs": [f"f16-full-travel{i}", f"q4-full-travel{i}"],
                }
                for i in range(n * 5, n * 5 + 5)
            ],
        }

    with pytest.raises(ValueError, match="32 episodes"):
        adapter.Selection.model_validate(
            {
                "schema_version": 1,
                "seed": 29,
                "reviewed_table_sha256": "0" * 64,
                "hypotheses": [hypothesis(0), hypothesis(1)],
            }
        )


@pytest.mark.parametrize(
    "target,change",
    [
        ("artifacts/phase2-main-v1/queue.json", lambda s: s.update(status="running")),
        (
            "results/phase2-main-v1/analysis/measurements.json",
            lambda s: s.update(status="provisional"),
        ),
        (
            "results/phase2-main-v1/analysis/measurements.json",
            lambda s: s.update(included_episodes=1097),
        ),
        (
            "results/phase2-main-v1/reviews/main-001.verification.json",
            lambda s: s.update(partial=True),
        ),
        (
            "results/phase2-main-v1/reviews/main-001.verification.json",
            lambda s: s["episodes"][0].update(identity="wrong"),
        ),
    ],
)
def test_premature_or_stale_parent_refuses_prepare_and_run(
    adapter, parent, target, change, monkeypatch
):
    invoke(adapter, parent, "prepare")
    mutate(parent / target, change)
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    for command in ("prepare", "run"):
        with pytest.raises(SystemExit):
            invoke(adapter, parent, command)
    assert not (parent / "artifacts/phase2-seed-diagnostics-test/seed-29").exists()


@pytest.mark.parametrize("lock", [".lock", ".queue.lock"])
def test_formal_lock_refuses_preparation_and_execution(adapter, parent, lock, monkeypatch):
    invoke(adapter, parent, "prepare")
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    with (parent / "artifacts/phase2-main-v1" / lock).open("r") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for command in ("prepare", "run"):
            with pytest.raises(SystemExit):
                invoke(adapter, parent, command)


@pytest.mark.parametrize(
    "target",
    [
        "scripts/phase2_seed_diagnostics.py",
        "scripts/phase2_collect.py",
        "src/agent_tradeoffs/analysis.py",
        "verified-local-model",
        "results/phase2-main-v1/analysis/episodes.csv",
        "artifacts/phase2-main-v1/main-001/runs/f16-full-banking1/manifest.json",
        "artifacts/phase2-seed-diagnostics-test/configs/runtime-q4.toml",
    ],
)
def test_drift_refuses_before_inference(adapter, parent, target, monkeypatch):
    invoke(adapter, parent, "prepare")
    path = parent / target
    path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    with pytest.raises(SystemExit):
        invoke(adapter, parent, "run")
    assert not (parent / "artifacts/phase2-seed-diagnostics-test/seed-29").exists()


def test_config_expansion_seed_and_summary_drift_are_rejected(adapter, parent):
    invoke(adapter, parent, "prepare")
    plan = parent / "artifacts/phase2-seed-diagnostics-test/configs/seed-29.json"
    original = plan.read_bytes()
    for change in (
        lambda p: p["runs"].append(copy.deepcopy(p["runs"][0])),
        lambda p: p["runs"][0]["config"]["model"].update(seed=30),
        lambda p: p["runs"][1]["config"]["context"]["summarizer"].update(seed=29),
    ):
        mutate(plan, change)
        with pytest.raises(SystemExit):
            invoke(adapter, parent, "validate")
        plan.write_bytes(original)


def test_incompatible_resume_and_selection_change_preserve_state(adapter, parent, monkeypatch):
    invoke(adapter, parent, "prepare")
    root = parent / "artifacts/phase2-seed-diagnostics-test"
    campaign = root / "seed-29/campaign.json"
    write(campaign, {"identity": {"wrong": "identity"}, "status": "interrupted"})
    before = campaign.read_bytes()
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    with pytest.raises(SystemExit):
        invoke(adapter, parent, "run")
    assert campaign.read_bytes() == before
    mutate(
        parent / "selection.json",
        lambda s: s["hypotheses"][0].update(question="Changed after selection"),
    )
    with pytest.raises(SystemExit):
        invoke(adapter, parent, "prepare")
    assert campaign.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(identity="mismatched-identity"),
        lambda m: m["config"]["model"].update(seed=17),
        lambda m: m["episodes"][0].update(user_task_id="user_task_3"),
    ],
)
def test_partial_pair_resume_checks_saved_identity_before_launch(
    adapter, parent, monkeypatch, change
):
    invoke(adapter, parent, "prepare")
    root = parent / "artifacts/phase2-seed-diagnostics-test"
    plan = adapter.read(root / "configs/seed-29.json")
    matrix = adapter.read(root / "configs/matrix.json")
    write(
        root / "seed-29/campaign.json",
        {
            "status": "interrupted",
            "identity": {
                "plan_sha256": adapter.stable_hash(plan),
                "implementation": matrix["implementation"],
                "driver_sha256": matrix["driver_sha256"],
                "matrix_sha256": digest(root / "configs/matrix.json"),
            },
        },
    )
    entry = plan["runs"][0]
    config = adapter.ExperimentConfig.model_validate(entry["config"])
    episodes = adapter.plan_episodes(config)
    semantic = entry["source_pair"]["runtime"]
    manifest = {
        "status": "interrupted",
        "config": entry["config"],
        "episodes": episodes,
        "implementation": matrix["implementation"],
        "runtime": {"semantic_identity": semantic},
        "summarizer_runtime": None,
        "identity": adapter.stable_hash(
            {
                "config": config.identity_config(),
                "episodes": episodes,
                "implementation": matrix["implementation"],
                "runtime": semantic,
                "summarizer_runtime": None,
            }
        ),
    }
    path = root / "seed-29/runs" / entry["run_id"] / "manifest.json"
    write(path, manifest)
    invoke(adapter, parent, "validate")
    mutate(path, change)
    before = path.read_bytes()
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    for command in ("prepare", "run"):
        with pytest.raises(SystemExit):
            invoke(adapter, parent, command)
    assert path.read_bytes() == before


def test_all_locks_span_driver_and_adapter_restores_globals_on_failure(
    adapter, parent, monkeypatch
):
    invoke(adapter, parent, "prepare")
    root = parent / "artifacts/phase2-seed-diagnostics-test"
    original = adapter.driver.CONFIGS, adapter.driver.OUTPUT
    monkeypatch.setattr(adapter, "vacant_ports", lambda: None)

    def inference_boundary(block):
        assert block == "seed-29"
        assert (adapter.driver.CONFIGS, adapter.driver.OUTPUT) == (root / "configs", root)
        for path in (
            parent / "artifacts/phase2-main-v1/.queue.lock",
            parent / "artifacts/phase2-main-v1/.lock",
            root / ".adapter.lock",
        ):
            with path.open("r") as handle, pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise RuntimeError("offline inference boundary")

    monkeypatch.setattr(adapter.driver, "run", inference_boundary)
    with pytest.raises(RuntimeError, match="offline inference boundary"):
        invoke(adapter, parent, "run")
    assert (adapter.driver.CONFIGS, adapter.driver.OUTPUT) == original
    with adapter.formal_locks():
        pass


def test_busy_port_cannot_reach_driver(adapter, parent, monkeypatch):
    invoke(adapter, parent, "prepare")
    monkeypatch.setattr(
        adapter.driver, "run", lambda block: pytest.fail("Inference must not start")
    )
    connection = MagicMock()
    connection.__enter__.return_value.connect_ex.return_value = 0
    monkeypatch.setattr(adapter.socket, "socket", lambda *args: connection)
    with pytest.raises(SystemExit):
        invoke(adapter, parent, "run")


def test_output_cannot_alias_original_or_escape_via_symlink(adapter, parent):
    for path in (
        parent / "artifacts/phase2-main-v1",
        parent / "configs/phase2-seed-diagnostics-test",
    ):
        with pytest.raises(ValueError):
            adapter.output_root(path)
    link = parent / "artifacts/phase2-seed-diagnostics-test"
    link.symlink_to(parent / "artifacts/phase2-main-v1", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        adapter.output_root(link)


def test_local_artifact_verification_needs_no_download_or_manifest_write(
    adapter, tmp_path, monkeypatch
):
    from agent_tradeoffs.runtime import ModelArtifactConfig, RuntimeConfig

    binary = tmp_path / "llama-server"
    binary.write_bytes(b"test binary")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"pinned weights")
    artifact = ModelArtifactConfig(
        revision="1" * 40,
        sha256=digest(model),
        local_dir=str(tmp_path),
        filename=model.name,
        acquisition="derived",
    )
    manifest = model.with_suffix(".manifest.json")
    write(
        manifest,
        {
            "sha256": digest(model),
            "revision": artifact.revision,
            "size_bytes": model.stat().st_size,
            "mtime_ns": model.stat().st_mtime_ns,
        },
    )
    write(model.with_suffix(".provenance.json"), {"fixture": True})
    monkeypatch.setattr(
        adapter,
        "load_runtime_config",
        lambda path: RuntimeConfig(
            binary_path=str(binary),
            port=8086 if "summarizer" in str(path) else 8087,
            model_artifact=artifact,
        ),
    )
    before = manifest.read_bytes(), manifest.stat().st_mtime_ns
    assert adapter.local_runtime_hashes()[str(model)] == digest(model)
    assert before == (manifest.read_bytes(), manifest.stat().st_mtime_ns)
    model.write_bytes(b"changed weights")
    with pytest.raises(ValueError, match="verification is missing/stale"):
        adapter.local_runtime_hashes()
