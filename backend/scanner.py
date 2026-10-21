"""Read-only indexing of disk contents into SQLite.

Usage:
    python -m backend.scanner            # metadata scan of all configured disks
    python -m backend.scanner --hash     # then hash files whose metadata disagrees
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .config import Config

CHUNK = 1024 * 1024


def is_ignored(name: str, rel: str, patterns: list[str]) -> bool:
    """Patterns starting with "/" match a path from the disk root (e.g.
    "/Seagate"); all others match a file or folder name at any depth."""
    for p in patterns:
        if p.startswith("/"):
            if fnmatch.fnmatchcase(rel, p[1:]):
                return True
        elif fnmatch.fnmatchcase(name, p):
            return True
    return False


def walk(root: str | Path, ignore: list[str], errors: list[str]) -> Iterator[tuple[str, int, float]]:
    """Yield (relative_path, size, mtime) for every regular file under root.

    Symlinks are not followed; ignored names are pruned at any depth.
    """
    root = str(root)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as e:
            errors.append(f"{current}: {e}")
            continue
        for entry in entries:
            rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
            if is_ignored(entry.name, rel, ignore):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    yield rel, st.st_size, st.st_mtime
            except OSError as e:
                errors.append(f"{entry.path}: {e}")


def scan_disk(config: Config, disk_id: str) -> dict:
    disk = config.disks[disk_id]
    conn = db.connect(config.db_path)
    started = time.time()
    errors: list[str] = []
    # Walk first, write after: holding a write transaction for the whole walk
    # would make parallel scans of other disks wait on the database lock.
    found = list(walk(disk.mount, config.ignore, errors))
    total = sum(size for _, size, _ in found)

    # Keep hashes for files whose size and mtime are unchanged since the last
    # scan, so re-scanning doesn't throw away hours of hashing.
    known = {
        r["path"]: (r["size"], r["mtime"], r["hash"])
        for r in conn.execute(
            "SELECT path, size, mtime, hash FROM files WHERE disk_id = ? AND hash IS NOT NULL",
            (disk_id,),
        )
    }

    def row(rel: str, size: int, mtime: float) -> tuple:
        prev = known.get(rel)
        digest = prev[2] if prev and prev[0] == size and prev[1] == mtime else None
        return (disk_id, rel, size, mtime, digest)

    with conn:
        conn.execute("DELETE FROM files WHERE disk_id = ?", (disk_id,))
        conn.executemany(
            "INSERT INTO files (disk_id, path, size, mtime, hash) VALUES (?, ?, ?, ?, ?)",
            (row(*f) for f in found),
        )
        conn.execute(
            "INSERT OR REPLACE INTO scans (disk_id, started_at, finished_at, file_count, total_bytes, errors) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (disk_id, started, time.time(), len(found), total, len(errors)),
        )
    conn.close()
    return {"disk_id": disk_id, "files": len(found), "bytes": total, "errors": errors}


def hash_file(path: str | Path) -> str:
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def hash_disk(config: Config, disk_id: str, paths: list[str]) -> int:
    disk = config.disks[disk_id]
    conn = db.connect(config.db_path)
    done = 0
    for rel in paths:
        try:
            digest = hash_file(Path(disk.mount) / rel)
        except OSError:
            continue
        with conn:
            conn.execute(
                "UPDATE files SET hash = ? WHERE disk_id = ? AND path = ?",
                (digest, disk_id, rel),
            )
        done += 1
    conn.close()
    return done


def scan_all(config: Config, disk_ids: list[str] | None = None) -> list[dict]:
    disk_ids = disk_ids or list(config.disks)
    # Create the schema and switch to WAL once, up front: doing it from
    # several threads at once on a new database fails with "database is locked".
    db.connect(config.db_path).close()
    # Each disk sits on its own USB device, so scanning them in parallel
    # is roughly as fast as scanning the slowest one.
    with ThreadPoolExecutor(max_workers=len(disk_ids)) as pool:
        return list(pool.map(lambda d: scan_disk(config, d), disk_ids))


def hash_needed(config: Config) -> list[dict]:
    from .report import files_needing_hash

    conn = db.connect(config.db_path)
    todo = files_needing_hash(conn, config)
    conn.close()
    with ThreadPoolExecutor(max_workers=max(len(todo), 1)) as pool:
        counts = list(pool.map(lambda d: hash_disk(config, d, todo[d]), todo))
    return [{"disk_id": d, "hashed": n} for d, n in zip(todo, counts)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Index disk contents (read-only).")
    parser.add_argument("--disks", nargs="*", help="disk ids to scan (default: all)")
    parser.add_argument("--hash", action="store_true", help="hash files whose size matches but mtime differs")
    parser.add_argument("--no-scan", action="store_true", help="skip the metadata scan")
    args = parser.parse_args()

    config = Config()
    if not args.no_scan:
        for r in scan_all(config, args.disks):
            print(f"disk {r['disk_id']}: {r['files']} files, {r['bytes'] / 1024**3:.1f} GB, {len(r['errors'])} errors")
            for e in r["errors"][:20]:
                print(f"  ! {e}")
    if args.hash:
        for r in hash_needed(config):
            print(f"disk {r['disk_id']}: hashed {r['hashed']} files")


if __name__ == "__main__":
    main()
