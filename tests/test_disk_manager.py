import json
from pathlib import Path

from backend.config import Config
from backend.disk_manager import DiskInfo, _match, _parse_lsblk_output, get_disks


def write_config(tmp_path: Path, data: dict) -> Config:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return Config(path=path)


def test_mock_returns_three_disks(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "empty.json")
    disks = get_disks(config)
    assert len(disks) == 3
    for d in disks:
        assert isinstance(d, DiskInfo)
        assert d.health == "healthy"
        assert d.total_gb == 2000


def test_mock_disk_values(tmp_path: Path) -> None:
    config = write_config(tmp_path, {"mock": True, "primary": "1"})
    disks = get_disks(config)
    assert disks[0].id == "1"
    assert disks[0].used_gb == 850
    assert disks[0].free_gb == 1150
    assert disks[0].is_primary
    assert not disks[1].is_primary
    assert disks[2].used_gb == 0
    assert disks[2].free_gb == 2000


def test_config_accepts_legacy_and_uuid_forms(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        {"disks": {"1": "/mnt/disk1", "2": {"uuid": "ABCD", "mount": "/mnt/disk2", "label": "HD2"}}},
    )
    disks = config.disks
    assert disks["1"].mount == "/mnt/disk1"
    assert disks["1"].uuid is None
    assert disks["2"].uuid == "ABCD"
    assert disks["2"].label == "HD2"


def test_parse_lsblk_output_empty() -> None:
    result = _parse_lsblk_output('{"blockdevices": []}')
    assert result == []


def test_parse_lsblk_output_with_partition() -> None:
    raw = json.dumps(
        {
            "blockdevices": [
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "type": "disk",
                    "size": 2000398934016,
                    "mountpoints": [None],
                    "tran": "usb",
                    "model": "Portable",
                    "serial": "NT3DKZZL",
                    "children": [
                        {
                            "name": "sdb1",
                            "path": "/dev/sdb1",
                            "type": "part",
                            "size": 2000396836864,
                            "fstype": "ntfs",
                            "uuid": "5418644300C99F56",
                            "label": "FamilyHD_3",
                            "mountpoints": ["/media/u/FamilyHD_3"],
                        }
                    ],
                }
            ]
        }
    )
    result = _parse_lsblk_output(raw)
    assert len(result) == 1
    disk = result[0]
    assert disk["mount_point"] == "/media/u/FamilyHD_3"
    assert disk["total_gb"] == 1863.0
    assert disk["uuid"] == "5418644300C99F56"
    assert disk["fstype"] == "ntfs"
    assert disk["model"] == "Portable"
    assert disk["parent_device"] == "/dev/sdb"


def test_match_prefers_uuid_over_mount(tmp_path: Path) -> None:
    config = write_config(tmp_path, {"disks": {"1": {"uuid": "5c3e-3410", "mount": "/old/path"}}})
    devices = [
        {"mount_point": "/old/path", "uuid": "OTHER"},
        {"mount_point": "/media/u/HD1", "uuid": "5C3E-3410"},
    ]
    assert _match(config.disks["1"], devices)["mount_point"] == "/media/u/HD1"


def test_missing_disk_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.disk_manager._list_block_devices", lambda: [])
    config = write_config(tmp_path, {"mock": False, "disks": {"1": {"uuid": "X", "mount": "/mnt/x"}}})
    disks = get_disks(config)
    assert len(disks) == 1
    assert not disks[0].present
    assert disks[0].health == "missing"
