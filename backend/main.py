from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Config
from .disk_manager import get_disks

app = FastAPI(title="disksync")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_config = Config()


@app.get("/disks")
async def list_disks():
    disks = get_disks(_config)
    return {
        "disks": [
            {
                "id": d.id,
                "mount_point": d.mount_point,
                "total_gb": d.total_gb,
                "used_gb": d.used_gb,
                "free_gb": d.free_gb,
                "health": d.health,
            }
            for d in disks
        ]
    }
