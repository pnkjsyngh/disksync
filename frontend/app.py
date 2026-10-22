from __future__ import annotations

import os
from datetime import datetime

import httpx
import streamlit as st

API_BASE = os.environ.get("DISKSYNC_API", "http://127.0.0.1:8000")
GB = 1024**3

st.set_page_config(page_title="disksync", layout="wide")
st.title("disksync")


def fetch(path: str) -> dict:
    resp = httpx.get(f"{API_BASE}{path}", timeout=30)
    resp.raise_for_status()
    return resp.json()


try:
    disks = fetch("/disks")["disks"]
except Exception as e:
    st.error(f"Could not reach backend at {API_BASE}: {e}")
    st.stop()

st.button("Refresh")

tab_disks, tab_mirror, tab_report = st.tabs(["Disks", "Mirror", "Diff report"])

with tab_disks:
    st.subheader(f"Disk Status ({len(disks)} disk(s) configured)")
    for disk in disks:
        with st.container(border=True):
            title = disk.get("label") or f"Disk {disk['id']}"
            if disk.get("is_primary"):
                title += "  ·  PRIMARY"
            st.markdown(f"**{title}**")
            if not disk.get("present", True):
                st.error(f"Disk {disk['id']} is not connected (UUID {disk.get('uuid')})")
                continue
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            col1.metric("Mount Point", disk["mount_point"])
            col2.metric("Filesystem", disk.get("fstype") or "?")
            col3.metric("Health", disk["health"])
            used_pct = round(disk["used_gb"] / disk["total_gb"] * 100, 1) if disk["total_gb"] else 0
            col4.metric("Used", f"{used_pct}%")
            if used_pct >= 90:
                st.warning("Disk is over 90% full")
            st.progress(
                used_pct / 100,
                text=f"{disk['used_gb']:.0f} GB / {disk['total_gb']:.0f} GB used ({disk['free_gb']:.0f} GB free)",
            )
            st.caption(f"{disk.get('device')} · {disk.get('model')} · serial {disk.get('serial')} · UUID {disk.get('uuid')}")

with tab_mirror:
    try:
        m = fetch("/mirror")
    except Exception as e:
        st.error(f"Could not load mirror status: {e}")
        m = None
    if m:
        status = m["status"]
        if m["paused"]:
            st.error(f"Copying is PAUSED: {m['paused']}. Check the cause, then run `python -m backend.mirror --resume`.")
        if not m["settings"]["auto_copy_new"]:
            st.info("Automatic copying is off (dry run only). Set mirror.auto_copy_new in config/config.json to enable it.")
        if not status:
            st.info("No sweep yet. Start the service or run `python -m backend.mirror --once`.")
        else:
            when = datetime.fromtimestamp(status["time"]).strftime("%Y-%m-%d %H:%M")
            cols = st.columns(4)
            cols[0].metric("Last sweep", when)
            cols[1].metric("Result", status["result"])
            cols[2].metric("New files", status.get("new_files", 0))
            cols[3].metric("Copied", status.get("copied", 0))
            if status.get("message"):
                st.caption(status["message"])
            for disk_id, problems in status.get("unavailable_disks", {}).items():
                st.warning(f"Disk {disk_id} skipped: {'; '.join(problems)}")
            if status.get("to_copy") and status["result"] != "copied":
                with st.expander(f"Waiting to be copied: {status['new_files']} file(s)"):
                    st.dataframe(status["to_copy"], use_container_width=True)
            if status.get("settling_count"):
                with st.expander(f"Recently modified, copied once quiet: {status['settling_count']}"):
                    st.write(status["settling"])
            labels = {
                "missing_on_primary": "Missing on the primary (deleted or renamed there?) - kept on the other disks",
                "changed": "Different between disks (changed on the primary?) - not updated automatically",
            }
            for key, count in status.get("review_counts", {}).items():
                if count:
                    with st.expander(f"Needs review - {labels.get(key, key)}: {count}"):
                        st.write(status["review"][key])

with tab_report:
    try:
        report = fetch("/report")
    except Exception as e:
        st.error(f"Could not load report: {e}")
        st.stop()

    if not report["scanned_disks"]:
        st.info("No scan yet. Run `python -m backend.scanner` on the server.")
        st.stop()

    for disk_id, scan in report["scans"].items():
        finished = scan.get("finished_at")
        when = datetime.fromtimestamp(finished).strftime("%Y-%m-%d %H:%M") if finished else "in progress"
        st.caption(f"Disk {disk_id}: scanned {when}, {scan.get('file_count')} files, {scan.get('errors')} errors")

    s = report["summary"]
    counts = s["counts"]
    cols = st.columns(5)
    cols[0].metric("Logical files", f"{s['logical_files']:,}")
    cols[1].metric("Union size", f"{s['union_bytes'] / GB:.1f} GB")
    cols[2].metric("Identical", f"{counts.get('identical', 0):,}")
    cols[3].metric("Conflicts", counts.get("conflict", 0))
    cols[4].metric("Missing somewhere", counts.get("missing_somewhere", 0))

    st.markdown("**Files only on one disk**")
    st.table(
        [
            {"disk": d, "files": v["files"], "GB": round(v["bytes"] / GB, 2)}
            for d, v in s["only_on_one_disk"].items()
        ]
    )

    labels = {
        "conflict": "Conflicts (same path, different content)",
        "unverified": "Unverified (same size, different time; run scanner --hash)",
        "missing_somewhere": "Missing on some disks",
        "case_mismatch": "Name differs only by case across disks",
        "case_collision": "Case collisions on one disk",
        "windows_invalid": "Names invalid on Windows",
    }
    for key, label in labels.items():
        items = report["detail"].get(key, [])
        total = report["detail_total"].get(key, 0)
        if not total:
            continue
        with st.expander(f"{label}: {total}"):
            if total > len(items):
                st.caption(f"Showing first {len(items)}; see data/report.json for all.")
            rows = []
            for item in items:
                if "copies" in item:
                    rows.append(
                        {
                            "path": item["path"],
                            "on disks": ", ".join(item["copies"]),
                            "missing on": ", ".join(item.get("missing_on", [])),
                            "sizes": ", ".join(str(c["size"]) for c in item["copies"].values()),
                        }
                    )
                else:
                    rows.append({k: str(v) for k, v in item.items()})
            st.dataframe(rows, use_container_width=True)
