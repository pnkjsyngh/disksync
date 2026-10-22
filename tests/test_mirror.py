import json
import os
import time
from pathlib import Path

import pytest

from backend import mirror, sync_engine
from backend.config import Config
from backend.mirror import PrimaryWatcher, resume, sweep

MTIME = 1_700_000_000


def make_disks(tmp_path: Path, layout: dict[str, dict[str, bytes]], **mirror_settings) -> Config:
    disks = {}
    for disk_id, files in layout.items():
        root = tmp_path / f"disk{disk_id}"
        root.mkdir()
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            os.utime(p, (MTIME, MTIME))
        disks[disk_id] = {"mount": str(root)}
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "mock": False,
                "primary": "2",
                "disks": disks,
                "db_path": str(tmp_path / "data" / "t.db"),
                "mirror": mirror_settings,
            }
        )
    )
    return Config(path=cfg)


@pytest.fixture(autouse=True)
def no_space_margin(monkeypatch):
    monkeypatch.setattr(sync_engine, "FREE_SPACE_MARGIN", 0)


def snapshot(root: Path) -> dict[str, tuple[bytes, float]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_sweep_is_dry_run_by_default(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"new.jpg": b"n"}, "3": {}})
    before = {d: snapshot(tmp_path / d) for d in ("disk1", "disk2", "disk3")}
    status = sweep(config)
    assert status["result"] == "dry run"
    assert status["new_files"] == 1
    assert {d: snapshot(tmp_path / d) for d in ("disk1", "disk2", "disk3")} == before
    assert json.loads(mirror.status_path(config).read_text())["result"] == "dry run"


def test_sweep_copies_new_primary_files_when_enabled(tmp_path: Path) -> None:
    config = make_disks(
        tmp_path, {"1": {"old.jpg": b"o"}, "2": {"old.jpg": b"o", "a/new.jpg": b"new"}, "3": {"old.jpg": b"o"}},
        auto_copy_new=True,
    )
    primary_before = snapshot(tmp_path / "disk2")
    status = sweep(config)
    assert status["result"] == "copied"
    assert status["copied"] == 2
    for d in ("disk1", "disk3"):
        assert (tmp_path / d / "a/new.jpg").read_bytes() == b"new"
        assert (tmp_path / d / "a/new.jpg").stat().st_mtime == MTIME
    assert snapshot(tmp_path / "disk2") == primary_before
    assert sweep(config)["result"] == "up to date"


def test_recently_modified_files_wait_to_settle(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"old.jpg": b"o"}}, auto_copy_new=True, settle_s=300)
    fresh = tmp_path / "disk2" / "fresh.mov"
    fresh.write_bytes(b"still being written")
    status = sweep(config)
    assert status["settling"] == ["fresh.mov"]
    assert not (tmp_path / "disk1" / "fresh.mov").exists()
    assert (tmp_path / "disk1" / "old.jpg").exists()
    # Once it has been quiet long enough, it is copied.
    assert sweep(config, now=time.time() + 301)["copied"] == 1
    assert (tmp_path / "disk1" / "fresh.mov").read_bytes() == b"still being written"


def test_files_missing_on_primary_are_only_reported(tmp_path: Path) -> None:
    # Deleted or renamed on the primary: never copied back, never removed.
    config = make_disks(
        tmp_path, {"1": {"gone.jpg": b"g"}, "2": {}, "3": {"gone.jpg": b"g"}}, auto_copy_new=True
    )
    before = {d: snapshot(tmp_path / d) for d in ("disk1", "disk2", "disk3")}
    status = sweep(config)
    assert status["result"] == "up to date"
    assert status["review"]["missing_on_primary"] == ["gone.jpg"]
    assert {d: snapshot(tmp_path / d) for d in ("disk1", "disk2", "disk3")} == before


def test_changed_files_are_only_reported(tmp_path: Path) -> None:
    config = make_disks(
        tmp_path, {"1": {"doc.txt": b"old version"}, "2": {"doc.txt": b"new version!"}}, auto_copy_new=True
    )
    status = sweep(config)
    assert status["review"]["changed"] == ["doc.txt"]
    assert (tmp_path / "disk1" / "doc.txt").read_bytes() == b"old version"


def test_same_size_edit_is_hashed_and_reported(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"doc.txt": b"aaaa"}, "2": {"doc.txt": b"aaaa"}}, auto_copy_new=True)
    p = tmp_path / "disk2" / "doc.txt"
    p.write_bytes(b"bbbb")
    os.utime(p, (MTIME + 100, MTIME + 100))
    status = sweep(config)
    assert status["review"]["changed"] == ["doc.txt"]
    assert (tmp_path / "disk1" / "doc.txt").read_bytes() == b"aaaa"


def test_copy_error_pauses_until_resumed(tmp_path: Path, monkeypatch) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.jpg": b"a", "b.jpg": b"b"}}, auto_copy_new=True)

    def failing_copy(src, dst, size, mtime):
        raise OSError(5, "Input/output error", str(dst))

    monkeypatch.setattr(sync_engine, "copy_verified", failing_copy)
    status = sweep(config)
    assert status["result"] == "paused"
    assert "Input/output error" in status["paused"]

    monkeypatch.undo()
    monkeypatch.setattr(sync_engine, "FREE_SPACE_MARGIN", 0)
    # Still paused: nothing is copied until the user resumes.
    assert sweep(config)["result"] == "paused"
    assert not (tmp_path / "disk1" / "a.jpg").exists()
    assert resume(config)
    assert sweep(config)["copied"] == 2


def test_source_changing_mid_copy_retries_later_without_pausing(tmp_path: Path, monkeypatch) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.jpg": b"a"}}, auto_copy_new=True)

    def changed(src, dst, size, mtime):
        raise sync_engine.SourceChanged(f"source changed since scan: {src}")

    monkeypatch.setattr(sync_engine, "copy_verified", changed)
    status = sweep(config)
    assert status["result"] == "retry later"
    assert not mirror.pause_path(config).exists()


def test_never_writes_to_the_primary(tmp_path: Path, monkeypatch) -> None:
    config = make_disks(
        tmp_path, {"1": {"only1.jpg": b"1"}, "2": {"new.jpg": b"n"}, "3": {}}, auto_copy_new=True
    )
    written = []
    real = sync_engine.copy_verified

    def record(src, dst, size, mtime):
        written.append(dst)
        return real(src, dst, size, mtime)

    monkeypatch.setattr(sync_engine, "copy_verified", record)
    sweep(config)
    assert written and all(not str(p).startswith(str(tmp_path / "disk2")) for p in written)
    assert not (tmp_path / "disk2" / "only1.jpg").exists()
    # A file only on a non-primary disk is not spread to the other disks either.
    assert not (tmp_path / "disk3" / "only1.jpg").exists()


def test_missing_disk_is_skipped_and_others_still_mirrored(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"new.jpg": b"n"}, "3": {}}, auto_copy_new=True)
    os.rename(tmp_path / "disk3", tmp_path / "unplugged")
    status = sweep(config)
    assert "3" in status["unavailable_disks"]
    assert status["copied"] == 1
    assert (tmp_path / "disk1" / "new.jpg").exists()
    assert not (tmp_path / "disk3").exists()


def test_waits_when_primary_missing(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"new.jpg": b"n"}}, auto_copy_new=True)
    os.rename(tmp_path / "disk2", tmp_path / "unplugged")
    status = sweep(config)
    assert status["result"] == "waiting"
    assert list((tmp_path / "disk1").iterdir()) == []


def test_watcher_sees_new_files_in_new_folders(tmp_path: Path) -> None:
    root = tmp_path / "primary"
    (root / "existing").mkdir(parents=True)
    (root / ".Trashes").mkdir()
    watcher = PrimaryWatcher(str(root), [".Trashes"])
    try:
        assert not watcher.wait(0.1)
        (root / "existing" / "a.jpg").write_bytes(b"a")
        assert watcher.wait(1)
        (root / "new").mkdir()
        assert watcher.wait(1)
        (root / "new" / "b.jpg").write_bytes(b"b")
        assert watcher.wait(1)
        (root / ".Trashes" / "x").write_bytes(b"x")
        assert not watcher.wait(0.3)
    finally:
        watcher.close()
