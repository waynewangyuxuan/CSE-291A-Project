"""Recovery must not escape into unrelated files."""

import runpy
from pathlib import Path

import pytest


def test_recovery_rejects_parent_absolute_and_symlink_escapes(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = runpy.run_path(str(scripts / "restore_phase2_evidence.py"))
    destination = module["destination"]
    root = tmp_path / "restore"
    root.mkdir()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    for name in ("../unrelated/file", str(outside / "file"), "link/file"):
        with pytest.raises(ValueError):
            destination(root, name)
    assert destination(root, "artifacts/block/trace.json") == root / "artifacts/block/trace.json"
    archived_path = module["archived_path"]
    assert archived_path(
        "/old/checkout/artifacts/phase2-main-v1/runtime/agent/launch/server.json"
    ) == ("artifacts/phase2-main-v1/runtime/agent/launch/server.json")
    with pytest.raises(ValueError, match="Unexpected absolute"):
        archived_path("/unrelated/private-file")
    with pytest.raises(ValueError):
        destination(root, archived_path("/old/artifacts/phase2-main-v1/runtime/../../../../escape"))
