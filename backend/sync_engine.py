"""Copy files that are missing on some disks from a disk that has them.

Usage:
    python -m backend.sync_engine                       # rescan + dry run (default)
    python -m backend.sync_engine --apply --plan-id ID  # apply a reviewed plan

Safety rules (see AGENTS.md): copy, never move; never overwrite; never
delete user data; verify every copy by hash; stop on the first problem.
Files whose copies disagree (conflicts, unverified, case clashes) are
never touched here and are listed for the user to resolve.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import db
from .config import Config
from .disk_manager import get_disks
from .report import classify, load_groups, match_key

CHUNK = 1024 * 1024
TMP_PREFIX = ".disksync-tmp-"
# Keep this much free on every destination after the copy, on top of the
# bytes being written.
FREE_SPACE_MARGIN = 5 * 1024**3


class SyncError(Exception):
    """A pre-flight check or copy failed; nothing further is attempted."""


@dataclass
class CopyOp:
    src_disk: str
    dst_disk: str
    path: str  # relative path on the source disk
    size: int
    mtime: float


@dataclass
class Plan:
    ops: list[CopyOp]
    skipped: dict[str, list[str]]  # reason -> paths that will not be copied
    bytes_per_disk: dict[str, int]

    @property
    def plan_id(self) -> str:
        # Fingerprint of exactly what will be copied, so --apply only runs
        # the plan the user reviewed and refuses if anything has changed.
        h = hashlib.sha256()
        for op in sorted(self.ops, key=lambda o: (o.dst_disk, o.path)):
            h.update(f"{op.src_disk}|{op.dst_disk}|{op.path}|{op.size}|{op.mtime}\n".encode())
        return h.hexdigest()[:12]


def build_plan(conn, config: Config) -> Plan:
    scanned = [r["disk_id"] for r in conn.execute("SELECT disk_id FROM scans WHERE finished_at IS NOT NULL")]
    ops: list[CopyOp] = []
    skipped: dict[str, list[str]] = {}
    for group in load_groups(conn).values():
        missing = [d for d in scanned if d not in group.copies]
        if not missing:
            continue
        any_path = next(iter(group.copies.values()))["path"]
        if group.collisions:
            skipped.setdefault("case collision on a disk", []).append(any_path)
            continue
        if len(group.copies) > 1:
            status = classify(group, config.mtime_tolerance_s)
            if status != "identical":
                skipped.setdefault(f"copies disagree ({status})", []).append(any_path)
                continue
        # Prefer the primary disk as the source when it has the file.
        src_disk = config.primary if config.primary in group.copies else sorted(group.copies)[0]
        src = group.copies[src_disk]
        for dst_disk in missing:
            ops.append(CopyOp(src_disk, dst_disk, src["path"], src["size"], src["mtime"]))
    bytes_per_disk: dict[str, int] = {}
    for op in ops:
        bytes_per_disk[op.dst_disk] = bytes_per_disk.get(op.dst_disk, 0) + op.size
    ops.sort(key=lambda o: (o.path, o.dst_disk))
    return Plan(ops, skipped, bytes_per_disk)


def check_disks(config: Config, disk_ids: set[str]) -> list[str]:
    """Return problems that make it unsafe to read/write these disks.

    A disk configured with a UUID must be present, mounted at its configured
    mount point, and that mount must really be the disk (not an empty
    directory left behind on the system drive after an unplug).
    """
    problems = []
    disks = {d.id: d for d in get_disks(config)} if not config.mock else {}
    for disk_id in sorted(disk_ids):
        cfg = config.disks.get(disk_id)
        if cfg is None:
            problems.append(f"disk {disk_id} is not configured")
            continue
        if cfg.uuid:
            info = disks.get(disk_id)
            if info is None or not info.present:
                problems.append(f"disk {disk_id} ({cfg.label}) is not connected")
            elif info.mount_point != cfg.mount:
                problems.append(f"disk {disk_id} is mounted at {info.mount_point}, expected {cfg.mount}")
            elif not os.path.ismount(cfg.mount):
                problems.append(f"{cfg.mount} is not a mount point")
        elif not os.path.isdir(cfg.mount):
            problems.append(f"disk {disk_id} mount {cfg.mount} does not exist")
    return problems


def check_space(config: Config, plan: Plan) -> list[str]:
    problems = []
    for disk_id, needed in plan.bytes_per_disk.items():
        free = shutil.disk_usage(config.disks[disk_id].mount).free
        if needed + FREE_SPACE_MARGIN > free:
            problems.append(
                f"disk {disk_id} needs {needed / 1024**3:.1f} GB + {FREE_SPACE_MARGIN / 1024**3:.0f} GB margin, "
                f"only {free / 1024**3:.1f} GB free"
            )
    return problems


def resolve_dest(root: Path, rel: str) -> Path:
    """Map rel onto root, reusing existing folders whose names differ only by case.

    exFAT matches names case-insensitively but NTFS under Linux does not, so
    without this a copy into "Photos/" could create a second "photos/" folder.
    """
    current = root
    parts = rel.split("/")
    for part in parts[:-1]:
        candidate = current / part
        if not candidate.exists() and current.is_dir():
            key = match_key(part)
            for entry in os.scandir(current):
                if entry.is_dir(follow_symlinks=False) and match_key(entry.name) == key:
                    candidate = current / entry.name
                    break
        current = candidate
    return current / parts[-1]


def _exists_ignoring_case(path: Path) -> bool:
    if path.exists():
        return True
    if not path.parent.is_dir():
        return False
    key = match_key(path.name)
    return any(match_key(e.name) == key for e in os.scandir(path.parent))


_libc = ctypes.CDLL(None, use_errno=True)
_RENAME_NOREPLACE = 1
_AT_FDCWD = -100


def rename_noreplace(src: Path, dst: Path) -> None:
    """Atomically rename src to dst, failing if dst already exists."""
    res = _libc.renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
    if res == 0:
        return
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
    if err in (errno.EINVAL, errno.ENOSYS):
        # Filesystem doesn't support RENAME_NOREPLACE. disksync is the only
        # writer, so check-then-rename is the best available fallback.
        if _exists_ignoring_case(dst):
            raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
        os.rename(src, dst)
        return
    raise OSError(err, os.strerror(err), str(dst))


def _hash_from_disk(path: Path) -> str:
    h = hashlib.blake2b(digest_size=20)
    fd = os.open(path, os.O_RDONLY)
    try:
        # Drop cached pages so the check reads what actually reached the disk.
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        with os.fdopen(fd, "rb", closefd=False) as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
    finally:
        os.close(fd)
    return h.hexdigest()


def copy_verified(src: Path, dst: Path, expected_size: int, expected_mtime: float) -> str:
    """Copy src to dst via a temp file, verify by hash, return the hash."""
    st = src.stat()
    if st.st_size != expected_size or st.st_mtime != expected_mtime:
        raise SyncError(f"source changed since scan: {src}")
    if _exists_ignoring_case(dst):
        raise SyncError(f"destination already exists, not overwriting: {dst}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f"{TMP_PREFIX}{uuid.uuid4().hex[:8]}-{dst.name}"
    src_hash = hashlib.blake2b(digest_size=20)
    try:
        # "xb" refuses to open an existing file, so this can't clobber anything.
        with open(src, "rb") as fin, open(tmp, "xb") as fout:
            while chunk := fin.read(CHUNK):
                src_hash.update(chunk)
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        digest = src_hash.hexdigest()
        if _hash_from_disk(tmp) != digest:
            raise SyncError(f"hash mismatch after copy: {dst}")
        os.utime(tmp, (st.st_atime, st.st_mtime))
        rename_noreplace(tmp, dst)
    except BaseException:
        # Only ever removes the temp file this call created, never user data.
        if tmp.exists():
            tmp.unlink()
        raise
    dir_fd = os.open(dst.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # some filesystems don't support fsync on directories
    finally:
        os.close(dir_fd)
    return digest


def log_path(config: Config) -> Path:
    return config.db_path.parent / "sync-log.jsonl"


def _log(config: Config, entry: dict[str, Any]) -> None:
    path = log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"time": time.time(), **entry}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def apply_plan(
    config: Config,
    plan: Plan,
    progress: Callable[[int, int, CopyOp], None] | None = None,
) -> int:
    problems = check_disks(config, {o.src_disk for o in plan.ops} | {o.dst_disk for o in plan.ops})
    if not problems:
        problems = check_space(config, plan)
    if problems:
        raise SyncError("; ".join(problems))

    conn = db.connect(config.db_path)
    done = 0
    try:
        for i, op in enumerate(plan.ops):
            if progress:
                progress(i, len(plan.ops), op)
            src = Path(config.disks[op.src_disk].mount) / op.path
            dst = resolve_dest(Path(config.disks[op.dst_disk].mount), op.path)
            base = {"src_disk": op.src_disk, "dst_disk": op.dst_disk, "path": op.path, "size": op.size}
            try:
                digest = copy_verified(src, dst, op.size, op.mtime)
            except (OSError, SyncError) as e:
                _log(config, {**base, "result": "error", "error": str(e)})
                raise SyncError(f"stopped after {done} copies: {e}") from e
            rel = dst.relative_to(config.disks[op.dst_disk].mount).as_posix()
            with conn:
                conn.execute(
                    "INSERT INTO files (disk_id, path, size, mtime, hash) VALUES (?, ?, ?, ?, ?)",
                    (op.dst_disk, rel, op.size, op.mtime, digest),
                )
                conn.execute(
                    "UPDATE files SET hash = ? WHERE disk_id = ? AND path = ?",
                    (digest, op.src_disk, op.path),
                )
            _log(config, {**base, "dst_path": rel, "hash": digest, "result": "copied"})
            done += 1
    finally:
        conn.close()
    return done


def write_plan_file(config: Config, plan: Plan) -> Path:
    out = config.db_path.parent / "sync-plan.json"
    out.write_text(
        json.dumps(
            {
                "plan_id": plan.plan_id,
                "bytes_per_disk": plan.bytes_per_disk,
                "skipped": plan.skipped,
                "ops": [asdict(o) for o in plan.ops],
            },
            indent=2,
        )
    )
    return out


def main() -> None:
    from .scanner import scan_all

    parser = argparse.ArgumentParser(description="Copy files missing on some disks (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="actually copy files")
    parser.add_argument("--plan-id", help="plan id from the dry run; required with --apply")
    parser.add_argument("--no-scan", action="store_true", help="use the existing index instead of rescanning")
    args = parser.parse_args()
    if args.apply and not args.plan_id:
        parser.error("--apply requires --plan-id from a dry run")

    config = Config()
    disk_ids = set(config.disks)
    problems = check_disks(config, disk_ids)
    if problems:
        raise SystemExit("Refusing to continue:\n  " + "\n  ".join(problems))

    if not args.no_scan:
        print("Rescanning disks (read-only)...")
        for r in scan_all(config):
            if r["errors"]:
                raise SystemExit(f"Scan of disk {r['disk_id']} had {len(r['errors'])} errors; resolve before syncing.")

    conn = db.connect(config.db_path)
    plan = build_plan(conn, config)
    conn.close()

    gb = 1024**3
    print(f"\nPlan {plan.plan_id}: {len(plan.ops)} copies")
    for disk_id in sorted(plan.bytes_per_disk):
        n = sum(1 for o in plan.ops if o.dst_disk == disk_id)
        free = shutil.disk_usage(config.disks[disk_id].mount).free
        print(
            f"  to disk {disk_id} ({config.disks[disk_id].label}): {n} files, "
            f"{plan.bytes_per_disk[disk_id] / gb:.2f} GB  (free now {free / gb:.1f} GB)"
        )
    sources = sorted({o.src_disk for o in plan.ops})
    if sources:
        print(f"  source disk(s): {', '.join(sources)} (read only)")
    folders: dict[tuple[str, str], int] = {}
    for o in plan.ops:
        key = (o.dst_disk, o.path.rsplit("/", 1)[0] if "/" in o.path else "(root)")
        folders[key] = folders.get(key, 0) + 1
    print("  by folder:")
    for (d, folder), n in sorted(folders.items()):
        print(f"    disk {d} <- {folder}: {n} files")
    for reason, paths in plan.skipped.items():
        print(f"  NOT copied ({reason}): {len(paths)} files")
    problems = check_space(config, plan)
    for p in problems:
        print(f"  ! {p}")
    print(f"Full plan: {write_plan_file(config, plan)}")

    if not args.apply:
        print(f"\nDry run only; nothing was written to the disks.\nTo apply: python -m backend.sync_engine --apply --plan-id {plan.plan_id}")
        return
    if args.plan_id != plan.plan_id:
        raise SystemExit(f"Plan id {args.plan_id} does not match the current plan {plan.plan_id}; re-run the dry run and review it.")
    if problems:
        raise SystemExit("Refusing to apply: not enough free space.")
    if not plan.ops:
        print("Nothing to copy.")
        return

    def progress(i: int, total: int, op: CopyOp) -> None:
        print(f"[{i + 1}/{total}] disk {op.src_disk} -> {op.dst_disk}: {op.path}", flush=True)

    try:
        n = apply_plan(config, plan, progress)
    except SyncError as e:
        raise SystemExit(f"STOPPED: {e}\nSee {log_path(config)}")
    print(f"\nCopied and verified {n} files. Log: {log_path(config)}")


if __name__ == "__main__":
    main()
