import json
import os
from pathlib import Path

from backend import db
from backend.config import Config
from backend.report import build_report, windows_problem
from backend.scanner import hash_needed, is_ignored, scan_all


def make_disks(tmp_path: Path, layout: dict[str, dict[str, bytes]]) -> Config:
    disks = {}
    for disk_id, files in layout.items():
        root = tmp_path / f"disk{disk_id}"
        root.mkdir()
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            os.utime(p, (1_700_000_000, 1_700_000_000))
        disks[disk_id] = {"mount": str(root)}
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "mock": False,
                "disks": disks,
                "db_path": str(tmp_path / "test.db"),
                "ignore": ["$RECYCLE.BIN", "._*", "/Seagate"],
            }
        )
    )
    return Config(path=cfg)


def report_for(config: Config) -> dict:
    conn = db.connect(config.db_path)
    try:
        return build_report(conn, config)
    finally:
        conn.close()


def test_identical_and_missing(tmp_path: Path) -> None:
    config = make_disks(
        tmp_path,
        {
            "1": {"a/x.jpg": b"same", "a/only1.txt": b"1"},
            "2": {"a/x.jpg": b"same"},
            "3": {"a/x.jpg": b"same"},
        },
    )
    scan_all(config)
    r = report_for(config)
    assert r["summary"]["counts"]["identical"] == 1
    assert r["summary"]["counts"]["missing_somewhere"] == 1
    assert r["summary"]["only_on_one_disk"]["1"]["files"] == 1
    assert r["detail"]["missing_somewhere"][0]["missing_on"] == ["2", "3"]


def test_size_mismatch_is_conflict(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"f": b"short"}, "2": {"f": b"longer content"}})
    scan_all(config)
    assert report_for(config)["summary"]["counts"]["conflict"] == 1


def test_mtime_mismatch_needs_hash_then_resolves(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"f": b"abcd", "g": b"same"}, "2": {"f": b"wxyz", "g": b"same"}})
    os.utime(tmp_path / "disk2" / "f", (1_700_000_100, 1_700_000_100))
    os.utime(tmp_path / "disk2" / "g", (1_700_000_100, 1_700_000_100))
    scan_all(config)
    assert report_for(config)["summary"]["counts"]["unverified"] == 2

    hash_needed(config)
    counts = report_for(config)["summary"]["counts"]
    assert counts["conflict"] == 1  # f: same size, different bytes
    assert counts["identical"] == 1  # g: only the timestamp differs
    assert "unverified" not in counts


def test_rescan_keeps_hashes_for_unchanged_files(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"f": b"abcd"}, "2": {"f": b"abcd"}})
    os.utime(tmp_path / "disk2" / "f", (1_700_000_100, 1_700_000_100))
    scan_all(config)
    hash_needed(config)
    scan_all(config)
    assert report_for(config)["summary"]["counts"]["identical"] == 1


def test_case_mismatch_across_disks(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"Photos/IMG.jpg": b"x"}, "2": {"photos/img.jpg": b"x"}})
    scan_all(config)
    counts = report_for(config)["summary"]["counts"]
    assert counts["case_mismatch"] == 1
    assert counts["identical"] == 1  # matched as the same file despite case
    assert "missing_somewhere" not in counts


def test_case_collision_on_one_disk(tmp_path: Path) -> None:
    config = make_disks(tmp_path, {"1": {"photos/img.jpg": b"x", "Photos/IMG.jpg": b"x"}})
    scan_all(config)
    r = report_for(config)
    assert r["summary"]["counts"]["case_collision"] == 1
    assert sorted(r["detail"]["case_collision"][0]["paths"]["1"]) == ["Photos/IMG.jpg", "photos/img.jpg"]


def test_ignored_files_are_skipped(tmp_path: Path) -> None:
    config = make_disks(
        tmp_path,
        {"1": {"$RECYCLE.BIN/junk": b"x", "a/._meta": b"x", "Seagate/reg.xml": b"x", "a/Seagate/keep": b"x"}},
    )
    scan_all(config)
    conn = db.connect(config.db_path)
    paths = {r["path"] for r in conn.execute("SELECT path FROM files")}
    conn.close()
    assert paths == {"a/Seagate/keep"}


def test_is_ignored_anchored() -> None:
    assert is_ignored("Seagate", "Seagate", ["/Seagate"])
    assert not is_ignored("Seagate", "a/Seagate", ["/Seagate"])
    assert is_ignored("._x", "a/._x", ["._*"])


def test_windows_problem() -> None:
    assert windows_problem("a/b:c.txt") == "invalid character"
    assert windows_problem("a/trailing. /x") == "trailing dot or space"
    assert windows_problem("CON.txt") == "reserved name"
    assert windows_problem("Family pics/IMG_001.JPG") is None
