from __future__ import annotations

import httpx
import streamlit as st

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="disksync", layout="wide")
st.title("disksync — RAID 1 Disk Dashboard")

if st.button("Refresh"):
    st.cache_data.clear()

try:
    resp = httpx.get(f"{API_BASE}/disks", timeout=5)
    resp.raise_for_status()
    data = resp.json()
except Exception as e:
    st.error(f"Could not reach backend at {API_BASE}/disks: {e}")
    st.stop()

disks = data["disks"]
st.subheader(f"Disk Status ({len(disks)} disk(s) configured)")

for disk in disks:
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns([1, 2, 2, 1])
        col1.metric("Disk", disk["id"])
        col2.metric("Mount Point", disk["mount_point"])
        col3.metric("Health", disk["health"])
        used_pct = (
            round(disk["used_gb"] / disk["total_gb"] * 100, 1)
            if disk["total_gb"]
            else 0
        )
        col4.metric("Used", f"{used_pct}%")

        st.progress(
            used_pct / 100,
            text=f"{disk['used_gb']:.0f} GB / {disk['total_gb']:.0f} GB used ({disk['free_gb']:.0f} GB free)",
        )
