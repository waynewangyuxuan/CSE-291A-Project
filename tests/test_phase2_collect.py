"""Collection cannot bypass an owned runtime or certify a damaged archive member."""

import fcntl
import hashlib
import io
import runpy
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def collector(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return runpy.run_path(str(scripts / "phase2_collect.py"))


def test_active_block_prevents_queue_from_claiming_idle(collector, tmp_path, monkeypatch):
    monkeypatch.setitem(collector["idle"].__globals__, "OUTPUT", tmp_path)
    with (tmp_path / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert collector["idle"]() is False
    assert collector["idle"]() is True


def test_archive_member_hash_is_checked_even_if_container_hash_matches(collector, tmp_path):
    path = tmp_path / "evidence.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("trace.json")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"bad"))
    record = {
        "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "members": [
            {"path": "trace.json", "bytes": 3, "sha256": hashlib.sha256(b"old").hexdigest()}
        ],
    }
    with pytest.raises(ValueError, match="Archive member differs"):
        collector["verify_archive"](path, record)
