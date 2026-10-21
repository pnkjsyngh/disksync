# Project Guidelines

## Data safety (highest priority, overrides everything below)

The disks this project manages (`/media/mopsie/FamilyHD_1`, `FamilyHD_2`,
`FamilyHD_3`, and any mount listed in `config/config.json`) hold
irreplaceable family data. Losing any of it is unacceptable. When in doubt,
stop and ask the user.

### Agents (and humans running commands)
- Treat the data disks as read-only. Never delete, move, rename, overwrite,
  truncate, `chmod`/`chown`, `touch` or otherwise modify anything on them
  without the user's explicit approval for that specific action, given in
  the current conversation. This includes "junk" such as `.Trashes`,
  `$RECYCLE.BIN`, `._*` files and vendor folders.
- Never run disk-level commands against the data disks: `mkfs`, `fdisk`,
  `parted`, `gparted`, `wipefs`, `dd`, `shred`, `mdadm`, `fsck`/`ntfsfix`
  (repair modes), `tune2fs`, `ntfslabel`/`exfatlabel`, or anything that
  changes partitions, filesystems or labels.
- Do not unmount, remount, power off or re-plug disks without asking.
- Never run `rm -rf`, `find -delete`, `rsync --delete` or similar against
  any path on a data disk, and never with a variable or glob that could
  expand to one.
- Running disksync code that writes to the disks requires the user's
  approval of that run, after they have seen its dry-run output.
- Tests must only use temporary directories, never the real disks or
  `config/config.json`.

### Code that writes to the disks
- Read-only is the default. Every write operation has a dry-run mode that
  is the default, prints exactly what would change, and needs an explicit
  flag/confirmation to apply.
- Copy, never move. The source file is never modified.
- Write to a temporary file in the destination folder, verify its hash
  against the source, then atomically rename into place.
- Never overwrite an existing file at the destination; report it as a
  conflict for the user to resolve.
- Never delete. Removals (once supported) go to `.disksync-trash/` with
  retention, never straight to deletion, and are never applied
  automatically to all disks at once.
- Before changing any file, at least one other verified copy of it must
  exist on another disk.
- Stop on the first unexpected error (disk missing, I/O error, hash
  mismatch, low space); do not retry by overwriting or skip-and-continue.
- Log every write (path, disk, hash, time) to an append-only log.
- Any code path that writes to the disks needs tests covering the dry-run
  default, conflict handling and hash verification.

## Overview
This repository contains the source code for the disksync project.

## Coding Standards
- Follow existing code style in the project
- Write clear, maintainable code
- Add comments for complex logic
- Keep functions focused and single-purpose

## Build & Test
- Run tests before submitting changes
- Ensure all linting passes
- Verify backwards compatibility when applicable

## Communication
- Be concise in commit messages
- Reference issue numbers when applicable
- Ask questions when requirements are unclear
