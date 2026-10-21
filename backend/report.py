"""Three-way comparison of indexed disk contents.

Usage:
    python -m backend.report     # print summary, write full report JSON

Files are matched across disks by path, ignoring case and Unicode
normalization, because exFAT is case-insensitive while NTFS on Linux is not.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db
from .config import Config

WINDOWS_INVALID_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def match_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def windows_problem(path: str) -> str | None:
    for part in path.split("/"):
        if WINDOWS_INVALID_CHARS.search(part):
            return "invalid character"
        if part != part.rstrip(". "):
            return "trailing dot or space"
        if part.split(".")[0].upper() in WINDOWS_RESERVED:
            return "reserved name"
    return None


@dataclass
class Group:
    """One logical file, as seen on each disk that has it."""

    key: str
    copies: dict[str, sqlite3.Row] = field(default_factory=dict)
    collisions: dict[str, list[str]] = field(default_factory=dict)


def load_groups(conn: sqlite3.Connection) -> dict[str, Group]:
    groups: dict[str, Group] = {}
    for row in conn.execute("SELECT disk_id, path, size, mtime, hash FROM files ORDER BY path"):
        key = match_key(row["path"])
        group = groups.setdefault(key, Group(key))
        disk_id = row["disk_id"]
        if disk_id in group.copies:
            # Two paths on one disk differing only by case: fine on NTFS under
            # Linux, but they can't both be copied to exFAT or used on Windows.
            group.collisions.setdefault(disk_id, [group.copies[disk_id]["path"]]).append(row["path"])
            continue
        group.copies[disk_id] = row
    return groups


def classify(group: Group, tolerance: float) -> str:
    """Return "identical", "conflict" or "unverified" for the copies present.

    Size mismatch is a definite conflict. Same size and mtime within
    tolerance counts as identical (exFAT stores coarser timestamps than
    NTFS). Same size but different mtime needs a hash to decide.
    """
    rows = list(group.copies.values())
    if len({r["size"] for r in rows}) > 1:
        return "conflict"
    mtimes = [r["mtime"] for r in rows]
    if max(mtimes) - min(mtimes) <= tolerance:
        return "identical"
    hashes = [r["hash"] for r in rows]
    if None in hashes:
        return "unverified"
    return "identical" if len(set(hashes)) == 1 else "conflict"


def files_needing_hash(conn: sqlite3.Connection, config: Config) -> dict[str, list[str]]:
    todo: dict[str, list[str]] = defaultdict(list)
    for group in load_groups(conn).values():
        if len(group.copies) > 1 and classify(group, config.mtime_tolerance_s) == "unverified":
            for disk_id, row in group.copies.items():
                if row["hash"] is None:
                    todo[disk_id].append(row["path"])
    return dict(todo)


def _describe(group: Group) -> dict[str, Any]:
    return {
        "path": next(iter(group.copies.values()))["path"],
        "copies": {
            d: {"path": r["path"], "size": r["size"], "mtime": r["mtime"], "hash": r["hash"]}
            for d, r in sorted(group.copies.items())
        },
    }


def build_report(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    scanned = [r["disk_id"] for r in conn.execute("SELECT disk_id FROM scans WHERE finished_at IS NOT NULL ORDER BY disk_id")]
    groups = load_groups(conn)
    tolerance = config.mtime_tolerance_s

    counts: dict[str, int] = defaultdict(int)
    sizes: dict[str, int] = defaultdict(int)
    only_on: dict[str, int] = defaultdict(int)
    only_on_bytes: dict[str, int] = defaultdict(int)
    detail: dict[str, list[dict[str, Any]]] = defaultdict(list)
    union_bytes = 0

    for group in groups.values():
        biggest = max(r["size"] for r in group.copies.values())
        union_bytes += biggest
        present = sorted(group.copies)
        status = classify(group, tolerance)

        if len(present) < len(scanned):
            counts["missing_somewhere"] += 1
            sizes["missing_somewhere"] += biggest
            entry = _describe(group)
            entry["missing_on"] = [d for d in scanned if d not in group.copies]
            detail["missing_somewhere"].append(entry)
            if len(present) == 1:
                only_on[present[0]] += 1
                only_on_bytes[present[0]] += biggest
        if len(present) > 1 or len(scanned) == 1:
            counts[status] += 1
            sizes[status] += biggest
            if status != "identical":
                detail[status].append(_describe(group))

        if len({r["path"] for r in group.copies.values()}) > 1:
            counts["case_mismatch"] += 1
            detail["case_mismatch"].append(_describe(group))
        if group.collisions:
            counts["case_collision"] += 1
            detail["case_collision"].append({"key": group.key, "paths": group.collisions})
        for row in group.copies.values():
            problem = windows_problem(row["path"])
            if problem:
                counts["windows_invalid"] += 1
                detail["windows_invalid"].append({"path": row["path"], "problem": problem})
                break

    scans = {
        r["disk_id"]: dict(r)
        for r in conn.execute("SELECT * FROM scans ORDER BY disk_id")
    }
    return {
        "scanned_disks": scanned,
        "scans": scans,
        "summary": {
            "logical_files": len(groups),
            "union_bytes": union_bytes,
            "counts": dict(counts),
            "bytes": dict(sizes),
            "only_on_one_disk": {d: {"files": only_on[d], "bytes": only_on_bytes[d]} for d in scanned},
        },
        "detail": dict(detail),
    }


def main() -> None:
    config = Config()
    conn = db.connect(config.db_path)
    report = build_report(conn, config)
    conn.close()

    out = config.db_path.parent / "report.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    s = report["summary"]
    gb = 1024**3
    print(f"Disks scanned: {', '.join(report['scanned_disks'])}")
    print(f"Logical files: {s['logical_files']}  (union size {s['union_bytes'] / gb:.1f} GB)")
    for name in ("identical", "conflict", "unverified", "missing_somewhere", "case_mismatch", "case_collision", "windows_invalid"):
        n = s["counts"].get(name, 0)
        b = s["bytes"].get(name)
        print(f"  {name:<18} {n:>8}" + (f"  {b / gb:8.1f} GB" if b is not None else ""))
    for d, v in s["only_on_one_disk"].items():
        print(f"  only on disk {d}:     {v['files']:>8}  {v['bytes'] / gb:8.1f} GB")
    print(f"Full report: {out}")


if __name__ == "__main__":
    main()
