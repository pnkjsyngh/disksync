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

Set `"mock": false` in `config/config.json` to detect real disks via `lsblk`.
