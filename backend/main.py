from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import Config
from .disk_manager import get_disks
from .report import build_report

app = FastAPI(title="disksync")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("DISKSYNC_CORS_ORIGINS", "http://localhost:8501").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)

_config = Config()

# Cap detail lists so the dashboard stays responsive; the CLI report
# (python -m backend.report) writes everything to data/report.json.
DETAIL_LIMIT = 500


@app.get("/disks")
async def list_disks():
    _config.reload()
    return {"disks": [asdict(d) for d in get_disks(_config)]}


@app.get("/report")
async def report():
    _config.reload()
    conn = db.connect(_config.db_path)
    try:
        result = build_report(conn, _config)
    finally:
        conn.close()
    result["detail_total"] = {k: len(v) for k, v in result["detail"].items()}
    result["detail"] = {k: v[:DETAIL_LIMIT] for k, v in result["detail"].items()}
    return result
