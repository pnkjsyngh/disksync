from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Config:
    def __init__(self, path: str | Path = "config/config.json") -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"mock": True, "disks": {}}
        with open(self._path) as f:
            return json.load(f)

    @property
    def mock(self) -> bool:
        return self._data.get("mock", True)

    @property
    def disks(self) -> dict[str, str]:
        return self._data.get("disks", {})
