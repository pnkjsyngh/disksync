# disksync

RAID 1 mirroring system for syncing data across multiple external hard disks.

## Architecture

- **Backend**: FastAPI (Python)
- **Frontend**: Streamlit (MVP) → migrating to custom HTML/JS
- **Storage**: RAID 1 mirroring across 3 × 2TB disks

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
