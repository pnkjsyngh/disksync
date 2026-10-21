# disksync

Self-hosted mirroring system that keeps 3 external hard disks in sync, running on a home mini PC. See [docs/plan.md](docs/plan.md).

## Architecture

- **Backend**: FastAPI (Python)
- **Frontend**: Streamlit (MVP) → migrating to custom HTML/JS
- **Storage**: app-level mirroring (primary + 2 mirrors) across 3 × 2TB disks

## Quick Start

```bash
# Create and activate venv
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start backend (port 8000)
uvicorn backend.main:app --reload

# In another terminal, start frontend (port 8501)
streamlit run frontend/app.py
```

Disks are configured by filesystem UUID in `config/config.json` (find them with `lsblk -f`).
Set `"mock": true` to use fake disks for development.

## Comparing disks (read-only)

```bash
python -m backend.scanner          # index all disks into data/disksync.db
python -m backend.scanner --hash   # also hash files whose size matches but mtime differs
python -m backend.report           # print summary, write data/report.json
```

The dashboard's "Diff report" tab shows the same results. Disk health needs
`smartmontools` and root access to `smartctl`; otherwise it shows "unknown".

## Tests

```bash
python -m pytest -q
```
