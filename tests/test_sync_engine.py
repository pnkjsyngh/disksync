import json
import os
from pathlib import Path

import pytest

from backend import db, sync_engine
from backend.config import Config
from backend.scanner import scan_all
from backend.sync_engine import SyncError, apply_plan, build_plan, copy_verified, resolve_dest

MTIME = 1_700_000_000


def make_disks(tmp_path: Path, layout: dict[str, dict[str, bytes]], primary: str = "2") -> Config:
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
        json.dumps({"mock": False, "primary": primary, "disks": disks, "db_path": str(tmp_path / "data" / "t.db")})
    )
    return Config(path=cfg)


def plan_for(config: Config):
    scan_all(config)
    conn = db.connect(config.db_path)
    try:
        return build_plan(conn, config)
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def no_space_margin(monkeypatch):
    monkeypatch.setattr(sync_engine, "FREE_SPACE_MARGIN", 0)


def snapshot(root: Path) -> dict[str, tuple[bytes, float]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_plan_copies_missing_files_from_primary(tmp_path: Path) -> None:
    config = make_disks(
        tmp_path,
        {"1": {"a/x.jpg": b"x"}, "2": {"a/x.jpg": b"x", "a/new.jpg": b"new"}, "3": {"a/x.jpg": b"x"}},
    )
    plan = plan_for(config)
    assert [(o.src_disk, o.dst_disk, o.path) for o in plan.ops] == [("2", "1", "a/new.jpg"), ("2", "3", "a/new.jpg")]
    assert plan.bytes_per_disk == {"1": 3, "3": 3}


def test_plan_copies_to_primary_from_other_disk(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"only1.txt": b"1"}, "2": {}})
    plan = plan_for(config)
    assert [(o.src_disk, o.dst_disk) for o in plan.ops] == [("1", "2")]


def test_plan_skips_disagreeing_copies(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"f": b"short"}, "2": {"f": b"different"}, "3": {}})
    plan = plan_for(config)
    assert plan.ops == []
    assert plan.skipped == {"copies disagree (conflict)": ["f"]}


def test_dry_run_plan_writes_nothing(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a/new.jpg": b"new"}})
    before = {d: snapshot(tmp_path / d) for d in ("disk1", "disk2")}
    plan_for(config)
    assert {d: snapshot(tmp_path / d) for d in ("disk1", "disk2")} == before


def test_apply_copies_verifies_and_preserves_source(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a/b/new.jpg": b"payload" * 1000}, "3": {}})
    src_before = snapshot(tmp_path / "disk2")
    plan = plan_for(config)
    assert apply_plan(config, plan) == 2
    for d in ("disk1", "disk3"):
        copied = tmp_path / d / "a/b/new.jpg"
        assert copied.read_bytes() == b"payload" * 1000
        assert copied.stat().st_mtime == MTIME
    assert snapshot(tmp_path / "disk2") == src_before
    assert not list(tmp_path.rglob(".disksync-tmp-*"))
    log = [json.loads(line) for line in (tmp_path / "data" / "sync-log.jsonl").read_text().splitlines()]
    assert [e["result"] for e in log] == ["copied", "copied"]
    # After applying, a fresh plan has nothing left to do.
    assert plan_for(config).ops == []


def test_apply_never_overwrites_existing_destination(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"f.txt": b"source"}})
    plan = plan_for(config)
    # A file appears at the destination after the plan was made.
    (tmp_path / "disk1" / "F.TXT").write_bytes(b"precious")
    with pytest.raises(SyncError, match="already exists"):
        apply_plan(config, plan)
    assert (tmp_path / "disk1" / "F.TXT").read_bytes() == b"precious"
    assert not (tmp_path / "disk1" / "f.txt").exists() or (tmp_path / "disk1" / "f.txt").read_bytes() == b"precious"


def test_apply_stops_if_source_changed(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.txt": b"one", "b.txt": b"two"}})
    plan = plan_for(config)
    (tmp_path / "disk2" / "a.txt").write_bytes(b"changed!")
    with pytest.raises(SyncError, match="source changed"):
        apply_plan(config, plan)
    # Stopped at the first problem: b.txt was not copied either.
    assert not (tmp_path / "disk1" / "b.txt").exists()
    assert not (tmp_path / "disk1" / "a.txt").exists()


def test_apply_refuses_when_disk_missing(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.txt": b"one"}})
    plan = plan_for(config)
    os.rename(tmp_path / "disk1", tmp_path / "unplugged")
    with pytest.raises(SyncError, match="does not exist"):
        apply_plan(config, plan)
    assert not (tmp_path / "disk1").exists()


def test_apply_refuses_without_space(tmp_path: Path, monkeypatch) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.txt": b"one"}})
    plan = plan_for(config)
    monkeypatch.setattr(sync_engine, "FREE_SPACE_MARGIN", 10**18)
    with pytest.raises(SyncError, match="free"):
        apply_plan(config, plan)
    assert not (tmp_path / "disk1" / "a.txt").exists()


def test_hash_mismatch_removes_only_temp_file(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    os.utime(src, (MTIME, MTIME))
    dst = tmp_path / "out" / "src.bin"
    monkeypatch.setattr(sync_engine, "_hash_from_disk", lambda p: "bad")
    with pytest.raises(SyncError, match="hash mismatch"):
        copy_verified(src, dst, 4, MTIME)
    assert not dst.exists()
    assert list((tmp_path / "out").iterdir()) == []
    assert src.read_bytes() == b"data"


def test_resolve_dest_reuses_folder_with_different_case(tmp_path: Path) -> None:
    (tmp_path / "Photos" / "2024").mkdir(parents=True)
    assert resolve_dest(tmp_path, "photos/2024/x.jpg") == tmp_path / "Photos" / "2024" / "x.jpg"
    assert resolve_dest(tmp_path, "new/x.jpg") == tmp_path / "new" / "x.jpg"


def test_plan_id_changes_when_plan_changes(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {"a.txt": b"one"}})
    first = plan_for(config).plan_id
    assert plan_for(config).plan_id == first
    (tmp_path / "disk2" / "b.txt").write_bytes(b"two")
    assert plan_for(config).plan_id != first


def test_scanner_ignores_leftover_temp_files(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {}, "2": {".disksync-tmp-abcd-x.jpg": b"partial"}})
    assert plan_for(config).ops == []
