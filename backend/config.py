from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"


@dataclass
class DiskConfig:
    id: str
    mount: str
    uuid: str | None = None
    label: str | None = None


class Config:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"mock": True, "disks": {}}
        with open(self._path) as f:
            return json.load(f)

    def reload(self) -> None:
        self._data = self._load()

    @property
    def mock(self) -> bool:
        return self._data.get("mock", True)

    @property
    def primary(self) -> str | None:
        return self._data.get("primary")

    @property
    def disks(self) -> dict[str, DiskConfig]:
        # Accept both the legacy {"1": "/mnt/disk1"} form and the
        # {"1": {"uuid": ..., "mount": ...}} form.
        result: dict[str, DiskConfig] = {}
        for disk_id, value in self._data.get("disks", {}).items():
            if isinstance(value, str):
                result[disk_id] = DiskConfig(id=disk_id, mount=value)
            else:
                result[disk_id] = DiskConfig(
                    id=disk_id,
                    mount=value.get("mount", ""),
                    uuid=value.get("uuid"),
                    label=value.get("label"),
                )
        return result

    @property
    def db_path(self) -> Path:
        path = Path(self._data.get("db_path", "data/disksync.db"))
        if not path.is_absolute():
            path = self._path.resolve().parent.parent / path
        return path

    @property
    def ignore(self) -> list[str]:
        return self._data.get("ignore", [])

    @property
    def mtime_tolerance_s(self) -> float:
        return float(self._data.get("mtime_tolerance_s", 2))
