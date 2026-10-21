from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import Config, DiskConfig

GB = 1024**3


@dataclass
class DiskInfo:
    id: str
    mount_point: str
    total_gb: float
    used_gb: float
    free_gb: float
    health: str
    present: bool = True
    label: str | None = None
    uuid: str | None = None
    fstype: str | None = None
    device: str | None = None
    model: str | None = None
    serial: str | None = None
    is_primary: bool = False


MOCK_DISKS: list[dict[str, Any]] = [
    {
        "id": "1",
        "mount_point": "/mnt/disk1",
        "total_gb": 2000,
        "used_gb": 850,
        "free_gb": 1150,
        "health": "healthy",
    },
    {
        "id": "2",
        "mount_point": "/mnt/disk2",
        "total_gb": 2000,
        "used_gb": 920,
        "free_gb": 1080,
        "health": "healthy",
    },
    {
        "id": "3",
        "mount_point": "/mnt/disk3",
        "total_gb": 2000,
        "used_gb": 0,
        "free_gb": 2000,
        "health": "healthy",
    },
]

LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS,TRAN,MODEL,SERIAL"


def _parse_lsblk_output(raw: str) -> list[dict[str, Any]]:
    """Flatten `lsblk --json --bytes` output into one entry per mounted filesystem.

    Partitions inherit transport/model/serial from their parent disk, since
    lsblk only reports those on the whole-disk device.
    """
    parsed = json.loads(raw)
    result: list[dict[str, Any]] = []

    def walk(dev: dict[str, Any], parent: dict[str, Any] | None) -> None:
        mounts = dev.get("mountpoints")
        if not isinstance(mounts, list):
            mounts = [dev.get("mountpoint")]
        mount = next((m for m in mounts if m), None)
        if mount:
            size = dev.get("size", 0)
            total_gb = round(size / GB, 1) if isinstance(size, (int, float)) else 0
            source = parent or dev
            result.append(
                {
                    "id": dev.get("name", ""),
                    "device": dev.get("path"),
                    "mount_point": mount,
                    "total_gb": total_gb,
                    "used_gb": 0,
                    "free_gb": total_gb,
                    "health": "unknown",
                    "uuid": dev.get("uuid"),
                    "label": dev.get("label"),
                    "fstype": dev.get("fstype"),
                    "model": source.get("model"),
                    "serial": source.get("serial"),
                    "parent_device": parent.get("path") if parent else dev.get("path"),
                }
            )
        for child in dev.get("children", []) or []:
            walk(child, dev if dev.get("type") == "disk" else parent)

    for dev in parsed.get("blockdevices", []):
        walk(dev, None)
    return result


def _apply_usage(disk: dict[str, Any]) -> None:
    try:
        usage = shutil.disk_usage(disk["mount_point"])
    except OSError:
        return
    disk["total_gb"] = round(usage.total / GB, 1)
    disk["used_gb"] = round(usage.used / GB, 1)
    disk["free_gb"] = round(usage.free / GB, 1)


def _smart_health(device: str | None) -> str:
    """Return "healthy", "failing" or "unknown".

    smartctl usually needs root; without it (or behind a USB bridge that
    blocks SMART) we report "unknown" rather than failing the request.
    """
    if not device or not shutil.which("smartctl"):
        return "unknown"
    for extra in ([], ["-d", "sat"]):
        try:
            proc = subprocess.run(
                ["smartctl", "-H", "-j", *extra, device],
                capture_output=True,
                text=True,
                timeout=15,
            )
            data = json.loads(proc.stdout or "{}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            continue
        status = data.get("smart_status", {})
        if "passed" in status:
            return "healthy" if status["passed"] else "failing"
    return "unknown"


def _list_block_devices() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS],
            capture_output=True,
            text=True,
            check=True,
        )
        return _parse_lsblk_output(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return []


def _match(configured: DiskConfig, devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    # UUID is stable across USB reconnects; mount point is a fallback for
    # configs that don't record one.
    if configured.uuid:
        for dev in devices:
            if (dev.get("uuid") or "").lower() == configured.uuid.lower():
                return dev
        return None
    for dev in devices:
        if dev["mount_point"] == configured.mount:
            return dev
    return None


def _get_real_disks(config: Config) -> list[dict[str, Any]]:
    devices = _list_block_devices()
    configured = config.disks
    if not configured:
        for dev in devices:
            _apply_usage(dev)
        return devices

    result: list[dict[str, Any]] = []
    for disk_id, disk_cfg in configured.items():
        dev = _match(disk_cfg, devices)
        if dev is None:
            result.append(
                {
                    "id": disk_id,
                    "mount_point": disk_cfg.mount,
                    "total_gb": 0,
                    "used_gb": 0,
                    "free_gb": 0,
                    "health": "missing",
                    "present": False,
                    "label": disk_cfg.label,
                    "uuid": disk_cfg.uuid,
                }
            )
            continue
        disk = {**dev, "id": disk_id}
        _apply_usage(disk)
        disk["health"] = _smart_health(dev.get("parent_device"))
        result.append(disk)
    return result


def get_disks(config: Config) -> list[DiskInfo]:
    raw = MOCK_DISKS if config.mock else _get_real_disks(config)
    fields = DiskInfo.__dataclass_fields__
    return [
        DiskInfo(
            **{k: v for k, v in d.items() if k in fields},
            is_primary=d["id"] == config.primary,
        )
        for d in raw
    ]
