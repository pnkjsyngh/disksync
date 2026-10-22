"""Background mirroring of new files from the primary disk (M3).

Usage:
    python -m backend.mirror --once     # one sweep, then exit
    python -m backend.mirror            # sweep hourly and shortly after changes on the primary
    python -m backend.mirror --resume   # clear a pause once its cause has been looked at

Only files that are new on the primary are copied to the other disks, and
only when "mirror": {"auto_copy_new": true} is set in config/config.json;
otherwise every sweep is a dry run. Everything else -- files changed on the
primary, files missing from the primary (deleted or renamed there),
conflicts -- is only reported in data/mirror-status.json for the user to
decide. The primary is never written to. The first copy or scan error
pauses copying until the user runs --resume.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import db
from .config import Config
from .report import classify, load_groups
from .scanner import ALWAYS_IGNORE, hash_needed, is_ignored, scan_all
from .sync_engine import Plan, SourceChanged, SyncError, apply_plan, build_plan, check_disks

# Cap the path lists written to the status file; counts are always complete.
LIST_LIMIT = 200


def status_path(config: Config) -> Path:
    return config.db_path.parent / "mirror-status.json"


def pause_path(config: Config) -> Path:
    return config.db_path.parent / "mirror-paused.json"


def paused_reason(config: Config) -> str | None:
    path = pause_path(config)
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("reason", "unknown")


def pause(config: Config, reason: str) -> None:
    path = pause_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"time": time.time(), "reason": reason}))


def resume(config: Config) -> bool:
    path = pause_path(config)
    if not path.exists():
        return False
    path.unlink()
    return True


def split_plan(plan: Plan, primary: str, now: float, settle_s: float) -> tuple[Plan, list[str], list[str]]:
    """Split a plan into (auto-copyable plan, settling paths, missing-on-primary paths).

    Auto-copyable means copied from the primary to another disk. A file that
    another disk has but the primary lacks was most likely deleted or renamed
    on the primary, so it is never copied back automatically. A file modified
    within settle_s may still be being written, so it waits for a later sweep.
    """
    missing_on_primary = {o.path for o in plan.ops if o.dst_disk == primary}
    auto, settling = [], set()
    for op in plan.ops:
        if op.path in missing_on_primary:
            continue
        if op.src_disk != primary:
            # Can't happen while the primary is scanned (it is always the
            # preferred source), but never auto-copy between other disks.
            missing_on_primary.add(op.path)
            continue
        if 0 <= now - op.mtime < settle_s:
            settling.add(op.path)
            continue
        auto.append(op)
    bytes_per_disk: dict[str, int] = {}
    for op in auto:
        bytes_per_disk[op.dst_disk] = bytes_per_disk.get(op.dst_disk, 0) + op.size
    return Plan(auto, {}, bytes_per_disk), sorted(settling), sorted(missing_on_primary)


def changed_files(conn, config: Config, disks: set[str]) -> list[str]:
    """Paths whose copies on the available disks disagree (e.g. edited on the primary)."""
    paths = []
    for group in load_groups(conn).values():
        copies = {d: r for d, r in group.copies.items() if d in disks}
        if len(copies) < 2:
            continue
        group.copies = copies
        if classify(group, config.mtime_tolerance_s) != "identical":
            paths.append(next(iter(copies.values()))["path"])
    return sorted(paths)


def _write_status(config: Config, status: dict[str, Any]) -> dict[str, Any]:
    path = status_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2))
    os.replace(tmp, path)
    return status


def sweep(config: Config, now: float | None = None) -> dict[str, Any]:
    """Rescan, copy new primary files if enabled, and record what needs review."""
    now = time.time() if now is None else now
    settings = config.mirror
    primary = config.primary
    status: dict[str, Any] = {
        "time": now,
        "primary": primary,
        "auto_copy_new": bool(settings["auto_copy_new"]),
        "paused": paused_reason(config),
    }
    if not primary or primary not in config.disks:
        status["result"] = "error"
        status["message"] = "no primary disk configured"
        return _write_status(config, status)

    problems = {d: check_disks(config, {d}) for d in sorted(config.disks)}
    available = sorted(d for d, p in problems.items() if not p)
    status["unavailable_disks"] = {d: p for d, p in problems.items() if p}
    if primary not in available or len(available) < 2:
        status["result"] = "waiting"
        status["message"] = "waiting for the primary and at least one other disk"
        return _write_status(config, status)

    scans = scan_all(config, available)
    scan_errors = {r["disk_id"]: r["errors"][:20] for r in scans if r["errors"]}
    if scan_errors:
        # Read errors are an early sign of a failing disk or cable.
        reason = "scan errors on disk(s) " + ", ".join(sorted(scan_errors))
        pause(config, reason)
        status.update(result="paused", paused=reason, scan_errors=scan_errors)
        return _write_status(config, status)
    hash_needed(config)

    conn = db.connect(config.db_path)
    try:
        plan = build_plan(conn, config, set(available))
        changed = changed_files(conn, config, set(available))
    finally:
        conn.close()
    auto, settling, missing_on_primary = split_plan(plan, primary, now, float(settings["settle_s"]))
    status.update(
        new_files=len({o.path for o in auto.ops}),
        new_bytes=auto.bytes_per_disk,
        to_copy=[asdict(o) for o in auto.ops[:LIST_LIMIT]],
        settling=settling[:LIST_LIMIT],
        settling_count=len(settling),
        review={
            "missing_on_primary": missing_on_primary[:LIST_LIMIT],
            "changed": changed[:LIST_LIMIT],
            **{reason: paths[:LIST_LIMIT] for reason, paths in plan.skipped.items()},
        },
        review_counts={
            "missing_on_primary": len(missing_on_primary),
            "changed": len(changed),
            **{reason: len(paths) for reason, paths in plan.skipped.items()},
        },
        copied=0,
    )

    if not auto.ops:
        status["result"] = "up to date"
    elif not status["auto_copy_new"]:
        status["result"] = "dry run"
    elif status["paused"]:
        status["result"] = "paused"
    else:
        try:
            status["copied"] = apply_plan(config, auto)
            status["result"] = "copied"
        except SyncError as e:
            if isinstance(e.__cause__, SourceChanged):
                # The file is still being written or was just removed; the
                # next sweep plans again from a fresh scan.
                status["result"] = "retry later"
            else:
                pause(config, str(e))
                status["result"] = "paused"
                status["paused"] = str(e)
            status["message"] = str(e)
        except Exception as e:
            pause(config, f"unexpected error: {e!r}")
            status.update(result="paused", paused=f"unexpected error: {e!r}")
            _write_status(config, status)
            raise
    return _write_status(config, status)


class PrimaryWatcher:
    """Signal changes anywhere under the primary disk via inotify.

    It only says *that* something changed; the sweep works out what. If the
    disk is unmounted the watcher marks itself broken and is recreated later.
    """

    def __init__(self, root: str, ignore: list[str]) -> None:
        from inotify_simple import INotify, flags

        self._flags = flags
        self._mask = (
            flags.CREATE | flags.CLOSE_WRITE | flags.MOVED_TO | flags.MOVED_FROM | flags.DELETE | flags.ATTRIB
        )
        self.root = root
        self.ignore = [*ALWAYS_IGNORE, *ignore]
        self.inotify = INotify()
        self.paths: dict[int, str] = {}
        self.broken = False
        self._add_tree(root)

    def _add_tree(self, top: str) -> None:
        for current, dirs, _ in os.walk(top):
            rel = os.path.relpath(current, self.root).replace(os.sep, "/")
            if rel != "." and is_ignored(os.path.basename(current), rel, self.ignore):
                dirs[:] = []
                continue
            dirs[:] = [
                d for d in dirs
                if not is_ignored(d, d if rel == "." else f"{rel}/{d}", self.ignore)
            ]
            wd = self.inotify.add_watch(current, self._mask)
            self.paths[wd] = current

    def wait(self, timeout_s: float) -> bool:
        """Block up to timeout_s; return True if anything changed."""
        f = self._flags
        changed = False
        for event in self.inotify.read(timeout=max(int(timeout_s * 1000), 0), read_delay=200):
            if event.mask & f.UNMOUNT or (event.mask & f.IGNORED and self.paths.get(event.wd) == self.root):
                self.broken = True
                continue
            if event.mask & f.IGNORED:
                self.paths.pop(event.wd, None)
                continue
            changed = True
            if event.mask & f.ISDIR and event.mask & (f.CREATE | f.MOVED_TO):
                parent = self.paths.get(event.wd)
                if parent:
                    try:
                        self._add_tree(os.path.join(parent, event.name))
                    except OSError:
                        pass  # removed again already; the sweep will see the result
        return changed

    def close(self) -> None:
        self.inotify.close()


def _describe(status: dict[str, Any]) -> str:
    parts = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} sweep: {status['result']}"]
    if status.get("copied"):
        parts.append(f"copied {status['copied']}")
    if status.get("new_files") and status["result"] == "dry run":
        parts.append(f"{status['new_files']} new file(s) would be copied")
    if status.get("settling_count"):
        parts.append(f"{status['settling_count']} still settling")
    review = {k: v for k, v in status.get("review_counts", {}).items() if v}
    if review:
        parts.append("review: " + ", ".join(f"{k} {v}" for k, v in review.items()))
    if status.get("paused"):
        parts.append(f"PAUSED: {status['paused']}")
    if status.get("message"):
        parts.append(status["message"])
    return "; ".join(parts)


def run_forever(config: Config) -> None:
    watcher: PrimaryWatcher | None = None
    next_sweep = 0.0  # sweep once at startup
    last_change: float | None = None
    while True:
        config.reload()
        settings = config.mirror
        primary_mount = config.disks[config.primary].mount if config.primary in config.disks else None
        if (watcher is None or watcher.broken) and primary_mount and os.path.ismount(primary_mount):
            if watcher:
                watcher.close()
            try:
                watcher = PrimaryWatcher(primary_mount, config.ignore)
            except OSError as e:
                # e.g. the inotify watch limit; hourly sweeps still run.
                print(f"watcher unavailable, relying on periodic sweeps: {e}", flush=True)
                watcher = None

        now = time.time()
        due = next_sweep if last_change is None else min(next_sweep, last_change + settings["debounce_s"])
        timeout = max(due - now, 0)
        if watcher and not watcher.broken:
            if watcher.wait(min(timeout, 60)):
                last_change = time.time()
        else:
            time.sleep(min(timeout, 60))

        now = time.time()
        if now >= next_sweep or (last_change is not None and now - last_change >= settings["debounce_s"]):
            try:
                status = sweep(config, now)
                print(_describe(status), flush=True)
            except Exception as e:
                # Keep the service alive, but stop copying until the user has
                # looked at it; later sweeps still scan and report.
                if not paused_reason(config):
                    pause(config, f"unexpected error: {e!r}")
                status = {"settling_count": 0}
                print(f"sweep failed, copying paused: {e!r}", flush=True)
            last_change = None
            next_sweep = now + float(settings["sweep_interval_min"]) * 60
            if status.get("settling_count"):
                next_sweep = min(next_sweep, now + float(settings["settle_s"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror new files from the primary disk to the others.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="run a single sweep and exit")
    group.add_argument("--resume", action="store_true", help="clear a pause after an error has been checked")
    args = parser.parse_args()

    config = Config()
    if args.resume:
        print("Resumed." if resume(config) else "Not paused.")
        return
    if args.once:
        status = sweep(config)
        print(_describe(status))
        print(f"Status: {status_path(config)}")
        return
    run_forever(config)


if __name__ == "__main__":
    main()
