"""Fresh repeat selection, isolation and immutable preparation checks; no inference."""

import copy
import importlib
import json
import shutil
from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


@pytest.fixture
def study(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    module = importlib.import_module("phase2_repeat_study")
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "configs/phase2-main-v1"
    source.mkdir(parents=True)
    for p in (REPO / "configs/phase2-main-v1").glob("runtime-*.toml"):
        shutil.copyfile(p, source / p.name)
    report = tmp_path / "results/phase2-main-v1/analysis/episodes.csv"
    report.parent.mkdir(parents=True)
    report.write_text("synthetic test source\n")
    plan = json.loads((REPO / "configs/phase2-main-v1/main-003.json").read_text())
    plan["runs"] = [r for r in plan["runs"] if r["policy"] == "full" and r["suite"] == "banking"]
    rows = []
    for entry in plan["runs"]:
        path = Path(entry["config"]["runner"]["output_root"]) / entry["run_id"] / "manifest.json"
        write(
            path,
            {
                "identity": "synthetic-original",
                "runtime": {"semantic_identity": {"precision": entry["precision"]}},
            },
        )
        for condition in ("clean", "attack"):
            rows.append(
                {
                    "block": "main-003",
                    "run_id": entry["run_id"],
                    "condition": condition,
                    "identity": "synthetic-original",
                    "episode_id": f"test__{condition}",
                    "source": "synthetic",
                    "trace_sha256": "a" * 64,
                    "result_sha256": "b" * 64,
                }
            )
    matrix = json.loads((REPO / "configs/phase2-main-v1/matrix.json").read_text())
    parent = (matrix, {"main-003": plan}, rows, {})
    qualification = tmp_path / "qualification.json"
    write(qualification, {"tasks": [{"suite": "banking", "task": "user_task_7", "eligible": True}]})
    selection = module.Selection(
        schema_version=1,
        qualification_source=str(qualification),
        qualification_sha256=module.sha256_file(qualification),
        reviewed_table_sha256=module.sha256_file(report),
        seeds=[41, 53, 67, 79, 97, 109],
        tasks=[{"suite": "banking", "task": "user_task_7"}],
    )
    monkeypatch.setattr(module.evidence, "reviewed_parent", lambda: parent)
    monkeypatch.setattr(module.evidence, "local_runtime_hashes", lambda: {})
    monkeypatch.setattr(module.evidence, "formal_locks", nullcontext)
    return module, selection, parent, tmp_path / "artifacts/phase2-repeat-study-test"


def test_fresh_pairs_change_only_seed_and_owned_paths(study):
    module, selection, parent, root = study
    files, matrix = module.derive(selection, root, parent)
    assert sum(b["expected_episodes"] for b in matrix["blocks"]) == 36
    orders = []
    for block in matrix["blocks"]:
        plan = json.loads(files[f"configs/{block['block_id']}.json"])
        orders.append(tuple(plan["precision_order"]))
        for entry in plan["runs"]:
            original = next(
                r for r in parent[1]["main-003"]["runs"] if r["run_id"] == entry["run_id"]
            )
            restored = copy.deepcopy(entry["config"])
            assert restored["model"]["seed"] == plan["seed"]
            assert Path(restored["runner"]["output_root"]).is_relative_to(root)
            restored["model"]["seed"] = 17
            for key in ("output_root", "runtime_metadata_path"):
                restored["runner"][key] = original["config"]["runner"][key]
            assert restored == original["config"]
    assert len(set(orders)) == 6


@pytest.mark.parametrize("seeds", [[17], [29], [41, 41], [-1]])
def test_historical_or_duplicate_seeds_rejected(study, seeds):
    module, selection, _, _ = study
    with pytest.raises(ValidationError, match="unique fresh seeds"):
        module.Selection.model_validate(selection.model_dump() | {"seeds": seeds})


def test_unqualified_task_cannot_enter_repeat_study(study):
    module, selection, parent, root = study
    path = Path(selection.qualification_source)
    write(path, {"tasks": [{"suite": "banking", "task": "user_task_7", "eligible": False}]})
    selection = selection.model_copy(update={"qualification_sha256": module.sha256_file(path)})
    with pytest.raises(ValueError, match="complete qualified"):
        module.derive(selection, root, parent)


def test_preparation_is_repeatable_and_rejects_changed_plan(study):
    module, selection, _, root = study
    source = Path("selection.json")
    write(source, selection.model_dump(mode="json"))
    result = module.materialize(root, source)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert result == {"status": "prepared_not_launched", "blocks": 6, "episodes": 36}
    assert module.materialize(root, source) == result
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    plan = root / "configs/seed-41-batch-001.json"
    changed = json.loads(plan.read_text())
    changed["runs"][0]["config"]["model"]["temperature"] = 0.1
    write(plan, changed)
    with pytest.raises(ValueError, match="Frozen preparation changed"):
        module.materialize(root, source)
    with pytest.raises(ValueError, match="Frozen plan changed"):
        module.inspect(root)


def test_source_pair_identity_change_rejected(study):
    module, selection, parent, root = study
    parent[2][0]["identity"] = "wrong-parent"
    with pytest.raises(ValueError, match="paired identity differs"):
        module.derive(selection, root, parent)


def test_runtime_output_does_not_escape_owned_root(study):
    module, _, _, root = study
    assert module.output_root(root) == root
    with pytest.raises(ValueError, match="Use artifacts"):
        module.output_root(root.parent / "phase2-main-v1")
    root.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = root.parent.parent / "elsewhere"
    elsewhere.mkdir()
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        module.output_root(root)


def test_queue_runs_only_next_declared_block_by_default(study, monkeypatch):
    module, selection, _, root = study
    source = Path("selection.json")
    write(source, selection.model_dump(mode="json"))
    module.materialize(root, source)
    matrix = module.evidence.read(root / "configs/matrix.json")
    first = matrix["blocks"][0]["block_id"]
    completed = {first}
    monkeypatch.setattr(module, "inspect", lambda root: matrix)
    monkeypatch.setattr(module.evidence, "vacant_ports", lambda: None)
    monkeypatch.setattr(module, "archive_block", lambda root, block: None)
    monkeypatch.setattr(
        module.evidence.driver,
        "status",
        lambda block: {"status": "completed" if block in completed else "not_started"},
    )
    monkeypatch.setattr(module.evidence.driver, "run", completed.add)
    module.operate("queue", root, None)
    receipt = module.evidence.read(root / "queue.json")
    assert completed == {"seed-41-batch-001", "seed-53-batch-001"}
    assert receipt["completed_blocks"] == ["seed-53-batch-001"]
    assert receipt["status"] == "selected_blocks_complete"


def test_queue_stops_on_driver_failure_without_retry(study, monkeypatch):
    module, selection, _, root = study
    source = Path("selection.json")
    write(source, selection.model_dump(mode="json"))
    module.materialize(root, source)
    matrix = module.evidence.read(root / "configs/matrix.json")
    monkeypatch.setattr(module, "inspect", lambda root: matrix)
    monkeypatch.setattr(module.evidence, "vacant_ports", lambda: None)
    monkeypatch.setattr(module, "archive_block", lambda root, block: None)
    monkeypatch.setattr(module.evidence.driver, "status", lambda block: {"status": "not_started"})

    def fail(block):
        raise RuntimeError("broken service")

    monkeypatch.setattr(module.evidence.driver, "run", fail)
    with pytest.raises(RuntimeError, match="broken service"):
        module.operate("queue", root, None, max_blocks=6)
    receipt = module.evidence.read(root / "queue.json")
    assert receipt["status"] == "stopped"
    assert receipt["active_block"] == "seed-41-batch-001"
    assert receipt["completed_blocks"] == []


def test_archive_preserves_audited_bytes_and_rejects_changed_evidence(study, monkeypatch):
    module, selection, _, root = study
    for name in ("src/agent_tradeoffs",):
        shutil.copytree(REPO / name, Path(name), ignore=shutil.ignore_patterns("__pycache__"))
    for name in (
        "scripts/phase2_repeat_study.py",
        "scripts/phase2_matrix.py",
        "scripts/phase2_seed_diagnostics.py",
        "scripts/audit_phase2_spike.py",
        "scripts/phase2_collect.py",
        "scripts/analyze_phase2_matrix.py",
        "uv.lock",
        "pyproject.toml",
        "results/phase2-calibration/selected-protocol.json",
        "configs/phase2-calibration/plan-before-action.json",
    ):
        target = Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    monkeypatch.setattr(module, "WRAPPER", Path("scripts/phase2_repeat_study.py").resolve())
    source = Path("selection.json")
    write(source, selection.model_dump(mode="json"))
    module.materialize(root, source)
    block = "seed-41-batch-001"
    write(root / block / "campaign.json", {"status": "completed"})
    write(root / block / "collection-audit.json", {"checks": "passed", "episodes_checked": 6})
    raw = root / block / "runs/test/trace.json"
    write(raw, {"actual_evidence_fixture": [1, 2, 3]})
    first = module.archive_block(root, block)
    assert first["episodes"] == 6
    assert module.archive_block(root, block) == first
    assert all(not Path(row["path"]).is_absolute() for row in first["members"])
    module.verify_archive(root / "archives" / f"{block}.tar.gz", first)
    write(raw, {"changed": True})
    with pytest.raises(ValueError, match="Archived block inputs changed"):
        module.archive_block(root, block)
