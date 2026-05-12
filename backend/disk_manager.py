from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass
class DiskInfo:
    id: str
    mount_point: str
    total_gb: float
    used_gb: float
    free_gb: float
    health: str


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


def _parse_lsblk_output(raw: str) -> list[dict[str, Any]]:
    parsed = json.loads(raw)
    blockdevices = parsed.get("blockdevices", [])
    result: list[dict[str, Any]] = []
    for dev in blockdevices:
        mount = (
            dev.get("mountpoints", [None])[0]
            if isinstance(dev.get("mountpoints"), list)
            else dev.get("mountpoint")
        )
        if not mount:
            continue
        size_bytes = dev.get("size", 0)
        total_gb = (
            round(size_bytes / (1024**3), 1)
            if isinstance(size_bytes, (int, float))
            else 0
        )
        result.append(
            {
                "id": dev.get("name", ""),
                "mount_point": mount,
                "total_gb": total_gb,
                "used_gb": 0,
                "free_gb": total_gb,
                "health": "unknown",
            }
        )
    return result


def _get_real_disks(config: Config) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--output", "name,mountpoints,size"],
            capture_output=True,
            text=True,
            check=True,
        )
        disks = _parse_lsblk_output(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        disks = []

    configured = config.disks
    if not configured:
        return disks

    disk_map = {m: d for d, m in configured.items()}

    matched: list[dict[str, Any]] = []
    for disk in disks:
        mp = disk["mount_point"]
        disk_id = disk_map.get(str(mp))
        if disk_id:
            disk["id"] = disk_id
            matched.append(disk)

    return matched


def get_disks(config: Config) -> list[DiskInfo]:
    if config.mock:
        raw = MOCK_DISKS
    else:
        raw = _get_real_disks(config)

    return [
        DiskInfo(
            id=d["id"],
            mount_point=d["mount_point"],
            total_gb=d["total_gb"],
            used_gb=d["used_gb"],
            free_gb=d["free_gb"],
            health=d["health"],
        )
        for d in raw
    ]
