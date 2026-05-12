from pathlib import Path

from backend.config import Config
from backend.disk_manager import DiskInfo, _parse_lsblk_output, get_disks


def test_mock_returns_three_disks(tmp_path: Path) -> None:
    config = Config(path=tmp_path / "empty.json")
    disks = get_disks(config)
    assert len(disks) == 3
    for d in disks:
        assert isinstance(d, DiskInfo)
        assert d.health == "healthy"
        assert d.total_gb == 2000


def test_mock_disk_values() -> None:
    config = Config()
    disks = get_disks(config)
    assert disks[0].id == "1"
    assert disks[0].used_gb == 850
    assert disks[0].free_gb == 1150
    assert disks[2].used_gb == 0
    assert disks[2].free_gb == 2000


def test_parse_lsblk_output_empty() -> None:
    result = _parse_lsblk_output('{"blockdevices": []}')
    assert result == []


def test_parse_lsblk_output_with_disk() -> None:
    raw = """
    {
      "blockdevices": [
        {
          "name": "sda1",
          "mountpoints": ["/mnt/disk1"],
          "size": 2000398934016
        }
      ]
    }
    """
    result = _parse_lsblk_output(raw)
    assert len(result) == 1
    assert result[0]["mount_point"] == "/mnt/disk1"
    assert result[0]["total_gb"] == 1863.0
    assert result[0]["id"] == "sda1"
