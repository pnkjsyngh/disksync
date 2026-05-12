# RAID-Like Storage System - Complete Development Plan

## Project Overview

A single-user web-based application to manage and synchronize data across 3 external hard disks using RAID 1 (mirroring), with future expansion for 2 family members. Deployable on Raspberry Pi over local home WiFi network.

-----

## Core Problem

You have 3 × 2TB portable external hard disks that need to be synced in a RAID-like manner for data redundancy and resilience.

-----

## Technical Stack

### Backend

**Language/Framework**: Python + FastAPI

- Lightweight, Raspberry Pi-friendly
- Strong file/disk management capabilities

### Frontend

**MVP**: Streamlit
**Production**: Lightweight custom HTML/JS (Raspberry Pi optimization)

- Rapid initial development with Streamlit
- Migrate to custom UI for resource efficiency on Pi

### Storage Model

**RAID Type**: RAID 1 (Mirroring)

- Data replicated identically across all 3 disks
- Tolerates 1-2 disk failures
- Usable storage: 2TB (limited by smallest disk)

### Data Persistence

**Initial**: JSON files (config and state)
**Future Path**: SQLite migration with abstracted data layer

- Simple, no database complexity initially
- Abstracted data layer enables future migration without major refactoring

### Operating Systems

**Development OS**: Linux
**Deployment OS**: Linux (Raspberry Pi OS)

-----

## Features - Phased Approach

### Phase 1: MVP - Single User Core (Weeks 1-4)

- [ ] Detect and monitor 3 external disks
- [ ] Display disk status (capacity, usage, health)
- [ ] Simple file upload/download interface
- [ ] Basic sync operation (one-way or two-way)
- [ ] Sync logs and status dashboard
- [ ] Configuration file for disk mapping

### Phase 2: Family Sharing (Weeks 5-8)

- [ ] Add 2-user authentication (you + 1 family member)
- [ ] User folder structure: `/user1/`, `/user2/`, `/shared/`
- [ ] Multi-user upload capability
- [ ] All uploads auto-sync across all 3 disks
- [ ] User session management
- [ ] Basic access control (own folder + shared folder)

### Phase 3: Optimization & Deployment (Weeks 9+)

- [ ] Incremental sync (only changed files)
- [ ] File integrity verification (checksums/hashing)
- [ ] Disk health monitoring and alerts
- [ ] Migrate frontend to lightweight custom UI
- [ ] Raspberry Pi optimization and testing
- [ ] Optional: compression support
- [ ] Optional: encryption support
- [ ] Sync history and audit logs

-----

## Key Design Decisions

### 1. Disk Mapping Strategy

**Approach**: Configuration-driven (Option 1)

- Mount points stored in `config.json`
- Example:

```json
{
"disk_1": "/mnt/disk1",
"disk_2": "/mnt/disk2",
"disk_3": "/mnt/disk3"
}
```
- Swap disks by updating config file only, no code changes required
- Future-proof and hardware-agnostic

### 2. Storage Quotas

**Approach**: No artificial limits

- Physical disk space is the only constraint
- Alert when approaching full (e.g., 90% capacity)
- First come, first served for all users

### 3. Network Access

**Scope**: Local WiFi only (home network)

- No internet exposure
- Simple security model
- Family accesses via browser on same network
- Access URL: `http://<raspberry_pi_ip>:8000`

### 4. Authentication

**Approach**: Basic username/password for 2 users

- Simple credential storage (encrypted JSON or environment variables initially)
- No complex permission system
- User-based folder separation

-----

## Hardware Requirements

### What You Have

- ✓ 3 × 2TB portable external hard disks
- Total usable storage: 2TB (RAID 1 mirroring)

### What You Need to Acquire

|Item |Cost |Notes |
|-------------------------|-------------|------------------------------|
|Raspberry Pi 4 (8GB RAM) |~$75 |Required for performance |
|Powered USB Hub |~$25-30 |Important for powering 3 disks|
|Power Supply for Pi |~$15-20 |5V 3A minimum, quality matters|
|SD Card (64GB+) |~$10-15 |Class A1 or better recommended|
|**Total Additional Cost**|**~$120-140**|One-time investment |

### Optional but Recommended

- Ethernet cable (more stable than WiFi)
- Pi case with heatsinks (good for sustained I/O)

-----

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Home WiFi Network                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Family Member 1 (Phone/Computer)                          │
│   Family Member 2 (Phone/Computer)                          │
│         │                                                   │
│         └─────────────────┬──────────────────────────       │
│                           │                                 │
│                   ┌───────▼────────┐                        │
│                   │ Raspberry Pi   │                        │
│                   │ ┌─────────────┐│                        │
│                   │ │ FastAPI     ││                        │
│                   │ │ Backend     ││                        │
│                   │ └─────────────┘│                        │
│                   │ ┌─────────────┐│                        │
│                   │ │ Streamlit   ││                        │
│                   │ │ Web UI      ││                        │
│                   │ └─────────────┘│                        │
│                   └────────┬───────┘                        │
│                            │                                │
│              ┌─────────────┼────────────┐                   │
│              │             │            │                   │
│         ┌───────▼──┐ ┌──────▼──┐ ┌────▼──────┐             │
│         │ Disk 1   │ │ Disk 2  │ │ Disk 3    │             │
│         │ 2TB      │ │ 2TB     │ │ 2TB       │             │
│         │ (copy)   │ │ (copy)  │ │ (copy)    │             │
│         └──────────┘ └─────────┘ └───────────┘             │
│                                                             │
│   RAID 1 Mirroring: Identical Data on All 3 Disks           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

-----

## Development Workflow

### Initial Setup

1. Set up development environment on Linux machine
2. Create project structure and version control
3. Configure disk mounting and detection

### MVP Development

1. Implement disk monitoring and health status
2. Build file upload/download endpoints
3. Implement basic RAID 1 sync logic
4. Create Streamlit UI for monitoring and operations
5. Add configuration file management
6. Comprehensive testing with actual 3 disks

### Family Sharing Addition

1. Implement user authentication system
2. Add user folder separation
3. Extend upload/download for multi-user
4. Test concurrent access and uploads

### Production Deployment

1. Migrate to lightweight custom UI (if needed)
2. Optimize for Raspberry Pi resources
3. Set up Raspberry Pi OS and environment
4. Deploy application and test end-to-end
5. Document setup and troubleshooting

-----

## File Structure (Proposed)

```
disksync/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── disk_manager.py
│   ├── sync_engine.py
│   ├── auth.py
│   └── utils.py
├── frontend/
│   ├── streamlit_app.py
│   └── pages/
├── config/
│   ├── config.json
│   └── credentials.json
├── logs/
│   └── sync.log
├── tests/
├── requirements.txt
└── README.md
```

-----

## Success Metrics

- [ ] All 3 disks successfully synced with identical data
- [ ] Web UI accessible from family devices over WiFi
- [ ] Sync completes without errors
- [ ] Disk status accurately reported
- [ ] Authentication works for 2 users
- [ ] Disk swap possible with only config changes
- [ ] Raspberry Pi deployment stable under normal use

-----

## Future Considerations

- **Encryption**: Encrypt data at rest on disks
- **Compression**: Optional compression for storage efficiency
- **Incremental Sync**: Only sync changed files instead of full copies
- **Backup**: External backup of critical data
- **Monitoring**: Email alerts for disk health issues
- **Mobile App**: Dedicated mobile app instead of web UI
- **Cloud Integration**: Optional cloud backup layer

-----

## Timeline Estimate

- **Phase 1 (MVP)**: 3-4 weeks
- **Phase 2 (Family Sharing)**: 2-3 weeks
- **Phase 3 (Optimization & Deployment)**: 2-3 weeks
- **Total**: 7-10 weeks of active development

-----

## Risk Mitigation

|Risk                           |Impact         |Mitigation                          |
|-------------------------------|---------------|------------------------------------|
|Disk failure during sync       |Data loss      |RAID 1 redundancy, checksums        |
|Concurrent upload conflicts    |Data corruption|Lock mechanism, timestamp resolution|
|Raspberry Pi performance issues|Slow syncing   |Lightweight UI, incremental sync    |
|USB power issues               |Disconnects    |Powered USB hub                     |
|Configuration errors           |System failure |Validation, backups of config       |

-----

## Notes

- Start development on Linux machine with actual 3 disks to test real-world scenarios
- Keep code simple initially; optimize later based on actual usage patterns
- Document all setup and deployment steps for reproducibility
- Consider adding monitoring/alerting as system matures
- Plan for eventual hardware upgrades (larger disks, more powerful Pi)
