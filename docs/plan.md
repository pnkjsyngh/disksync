# disksync - Development Plan

## Project Overview

A self-hosted web application that keeps 3 external hard disks mirrored for redundancy, with family sharing for 2 users and phone photo backup. Runs on a dedicated, always-on mini PC on the home network.

-----

## Core Problem

3 × 2TB portable external hard disks hold roughly the same data, copied by hand over time, and have drifted apart. They need to be reconciled once, then kept identical automatically, while each disk stays independently readable on Linux and Windows.

-----

## Decisions Log

| Topic | Decision | Notes |
|---|---|---|
| Host | Dedicated, always-on NUC-style mini PC (i3/i5/i7) | Replaces Raspberry Pi |
| OS | Ubuntu Server LTS | disksync runs as a systemd service |
| Disk connection | Powered USB hub | USB dropouts must be tolerated |
| Mirroring | App-level sync (not mdadm/btrfs) | mdadm rejected: full 2TB resync on every USB dropout, and disks unusable standalone |
| Source of truth | One primary disk; other two are mirrors | No multi-master conflict resolution |
| Write path | Write to primary, background copy to mirrors | Brief single-copy window is accepted |
| Sync timing | On change (inotify on primary) + hourly full sweep | Sweep catches missed events and reconnected disks |
| Failover | Auto-promote most up-to-date mirror; dashboard warning | Until user acknowledges |
| Filesystem | Keep current formats, no reformat | Must stay readable on Linux + Windows; check with `lsblk -f` |
| Direct edits | Allowed on other machines; on reconnect show diff vs primary, user approves | Pull into primary or overwrite |
| Deletes | Move to `.disksync-trash/` on mirrors, purge after retention (default 30 days) | |
| Integrity | Hash every file on copy + monthly scrub of all disks | Hash algorithm: BLAKE2b or xxHash (fast) |
| Existing data | Must be preserved; disks are drifted copies | First sync = union of all 3, conflicts flagged for review, nothing deleted |
| Disk identity | Filesystem UUID, not mount point / device name | `/dev/sdX` changes across USB reconnects |
| Phone backup | Immich (free/OSS), Docker, iPhone app | disksync mirrors Immich's library folder; Immich DB backed up too |
| Users | 2 users, `/user1/`, `/user2/`, `/shared/` | |
| Remote access | LAN only for now | Tailscale is the likely option later |
| Alerts | Dashboard only | No email/push for now |
| Frontend | Streamlit now, custom HTML/JS later | Migration kept as a later phase |
| Persistence | JSON for config; SQLite for file index/hashes/sync state | Index of 3 × 2TB of files is too large for JSON |

-----

## Technical Stack

- **Backend**: Python + FastAPI
- **Frontend**: Streamlit (MVP) → custom HTML/JS (later phase)
- **Sync**: Python sync engine (rsync-style copy + hash verify), `watchdog`/inotify on primary
- **Disk info**: `lsblk --json --bytes`, `shutil.disk_usage`, `smartctl` (smartmontools) for health
- **State**: `config/config.json` + SQLite (`data/disksync.db`)
- **Phone backup**: Immich in Docker Compose
- **Service**: systemd units for backend + frontend

-----

## Milestones

### M1: Real Disk Status + First-Sync Dry Run (next)

Read-only. Nothing is written to the disks.

- [x] Fix `lsblk` parsing (`--bytes`, partitions/children, all mountpoints)
- [x] Real used/free via `shutil.disk_usage`
- [x] SMART health via `smartctl -H` (graceful fallback when USB bridge blocks SMART) — needs smartmontools + root on host
- [x] Identify disks by filesystem UUID in config; report filesystem type
- [x] Config: mark primary disk; reload without restart
- [x] Scanner: walk each disk, record path/size/mtime/hash into SQLite
- [x] 3-way diff report: identical / missing on some disks / same path, different content
- [x] Dashboard: disk status + diff report view
- [x] CI: pytest workflow on PRs; fix cwd-dependent test

### M2: Initial Reconcile

- [ ] Review UI for conflicts (pick version per file; keep both as option)
- [ ] Union merge onto primary, with hash verification
- [ ] Mirror primary → secondaries; verify by hash
- [ ] Sync log (file + dashboard view)

### M3: Continuous Mirroring

- [ ] inotify watcher on primary + hourly sweep
- [ ] Copy queue with retry; tolerate disk disconnects mid-copy (temp file + atomic rename)
- [ ] `.disksync-trash/` with retention purge
- [ ] Direct-edit detection on reconnect → diff → approve
- [ ] Failover: auto-promote most up-to-date mirror, dashboard alert
- [ ] Monthly scrub job; report and repair mismatches from good copies
- [ ] Dashboard alerts: disk missing, SMART warning, sync failure, >90% full
- [ ] systemd services, auto-mount by UUID (fstab with `nofail`)

### M4: Family Sharing + Immich

- [ ] Auth for 2 users (hashed passwords, session cookies)
- [ ] `/user1/`, `/user2/`, `/shared/` folders with access control
- [ ] Upload/download through web UI (writes to primary)
- [ ] Immich via Docker Compose; library on primary disk; iPhone app setup
- [ ] Include Immich Postgres dumps in mirrored data

### M5: Custom UI + Hardening

- [ ] Migrate frontend from Streamlit to custom HTML/JS
- [ ] Sync history and audit log
- [ ] Optional: encryption at rest, compression
- [ ] Optional: remote access via Tailscale
- [ ] Optional: email/push alerts

-----

## Key Design Details

### Disk Mapping

Configuration-driven, by filesystem UUID:

```json
{
  "mock": false,
  "primary": "1",
  "disks": {
    "1": {"uuid": "XXXX-XXXX", "mount": "/mnt/disk1"},
    "2": {"uuid": "YYYY-YYYY", "mount": "/mnt/disk2"},
    "3": {"uuid": "ZZZZ-ZZZZ", "mount": "/mnt/disk3"}
  },
  "trash_retention_days": 30
}
```

Swapping a disk means updating config only.

### Sync Model

```
          writes (web UI, Immich, Samba later)
                        │
                        ▼
                 ┌─────────────┐
                 │  Primary    │  ← source of truth
                 └──────┬──────┘
        inotify + hourly sweep, hash-verified copy
              ┌─────────┴─────────┐
              ▼                   ▼
        ┌──────────┐        ┌──────────┐
        │ Mirror A │        │ Mirror B │
        └──────────┘        └──────────┘
```

- A disk that reconnects after a dropout catches up incrementally from the index.
- Changes found on a mirror that didn't come from primary are treated as direct edits → user review.

### Storage Quotas

No artificial limits. Alert at 90% full.

### Network Access

LAN only. Backend on `:8000`, Streamlit on `:8501`. Frontend backend URL must be configurable (not hard-coded to `127.0.0.1`). Tighten CORS to the frontend origin.

-----

## Hardware

- ✓ Mini PC (NUC-style, Intel i3/i5/i7), Ubuntu Server LTS
- ✓ 3 × 2TB portable external hard disks
- Powered USB 3 hub (good quality; USB dropouts are the main hardware risk)
- Optional: UPS for clean shutdowns

-----

## File Structure (Target)

```
disksync/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── disk_manager.py
│   ├── scanner.py        # walk + hash + index
│   ├── sync_engine.py    # primary → mirrors
│   ├── db.py             # SQLite access layer
│   └── auth.py
├── frontend/
│   └── app.py
├── config/config.json
├── data/disksync.db
├── deploy/               # systemd units, docker-compose for Immich
├── logs/sync.log
├── tests/
└── requirements.txt
```

-----

## Success Metrics

- [ ] All 3 disks hold identical data, verified by hash
- [ ] Existing data reconciled with no losses
- [ ] Disk status (usage, SMART, presence) accurately reported
- [ ] USB dropout recovers with incremental catch-up, no full resync
- [ ] iPhone photos back up automatically via Immich
- [ ] 2 users can log in with folder separation
- [ ] Disk swap possible with only config changes

-----

## Risk Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| USB hub dropout | Disk disappears mid-sync | UUID identity, temp file + atomic rename, incremental catch-up |
| Data loss in first reconcile | Permanent loss | Read-only dry run first; union merge; nothing deleted |
| Primary disk failure | Writes interrupted | Auto-promote most current mirror |
| Silent corruption | Bad data mirrored | Hash on copy, monthly scrub, repair from good copy |
| Mirror drift via direct edits | Inconsistency | Detect on reconnect, user approves |
| exFAT/NTFS limits on Linux | Permissions, case, journaling quirks | Keep current formats; avoid relying on Unix permissions on disks |
| SMART blocked by USB bridge | No health data | Fallback to `smartctl -d sat`; show "unknown" otherwise |

-----

## Current Disks (recorded 2026-10-22)

| Id | Label | Device | FS | UUID | Hardware |
|---|---|---|---|---|---|
| 1 | FamilyHD_1 | sdd1 | exFAT | 5C3E-3410 | Toshiba MQ04UBD200 |
| 2 | FamilyHD_2 | sdc1 | NTFS | 5418644300C99F4C | Seagate "Portable" NT3DKZX7 |
| 3 | FamilyHD_3 | sdb1 | NTFS | 5418644300C99F56 | Seagate "Portable" NT3DKZZL |

Host runs Ubuntu desktop (not Server); disks are auto-mounted under `/media/mopsie/` until fstab mounts are set up in M3. An unused 140 GB ext4 partition (`sda5`) is available for index/staging.

SMART: works on disk 1 (Toshiba, healthy) via the `deploy/disksync-smart` sudo helper. The Seagate Portable enclosures (USB `0bc2:2344`, disks 2 and 3) block SMART in every `-d sat` mode, so their health shows "unknown"; scan read errors and scrub hash mismatches are the failure signal for them.

### First M1 diff (metadata scan)

- 86,353 files (628.9 GB) identical on all 3 disks; no conflicts, case issues or Windows-invalid names
- 590 files (6.7 GB) only on disk 2, all in `Pankaj Files/PICS_phone`
- Disks were previously mirrored with FreeFileSync (`sync.ffs_db`) and used on macOS
- Not scanned (ignored): `.Trashes` holds ~236 GB on each disk (macOS trash never emptied) and `$RECYCLE.BIN` ~52 GB on disk 1. This accounts for the gap between `df` usage and the scanned size.

## Open Items

- Decide what to do with ~236 GB macOS `.Trashes` per disk and 52 GB `$RECYCLE.BIN` on disk 1 (review, recover or empty)
- Primary: disk 2 (most complete, NTFS/journaled); confirm
- Decide trash retention period (default 30 days)
