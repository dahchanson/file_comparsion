"""Unit tests for engine/fs_info.py."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fcompare.engine.fs_info import (
    LOCAL_FS_TYPES,
    NETWORK_FS_TYPES,
    _is_disk_image_on_network,
    _is_under,
    _mount_info,
    fs_type,
    is_local_fs,
    is_mmap_safe,
    is_network_fs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_mount(output: str) -> MagicMock:
    m = MagicMock()
    m.stdout = output
    m.returncode = 0
    return m


def _local_mount(mountpoint: str, fstype: str, extra_flags: str = "") -> str:
    """Build a realistic mount line for a local filesystem."""
    flags = f"{fstype}, local" + (f", {extra_flags}" if extra_flags else "")
    return f"/dev/diskXsY on {mountpoint} ({flags})"


def _network_mount(device: str, mountpoint: str, fstype: str) -> str:
    """Build a realistic mount line for a network filesystem (no 'local' flag)."""
    return f"{device} on {mountpoint} ({fstype}, nodev, nosuid)"


def _clear_cache() -> None:
    _mount_info.cache_clear()


# ---------------------------------------------------------------------------
# _is_under
# ---------------------------------------------------------------------------


class TestIsUnder:
    def test_root_matches_any_path(self) -> None:
        assert _is_under("/Users/foo/bar", "/") is True

    def test_exact_match(self) -> None:
        assert _is_under("/Volumes/share", "/Volumes/share") is True

    def test_child_path_matches(self) -> None:
        assert _is_under("/Volumes/share/subdir/file.txt", "/Volumes/share") is True

    def test_prefix_without_slash_does_not_match(self) -> None:
        # /Volumes/share2 must not match /Volumes/share
        assert _is_under("/Volumes/share2/file.txt", "/Volumes/share") is False

    def test_unrelated_path_does_not_match(self) -> None:
        assert _is_under("/Users/foo", "/Volumes/share") is False


# ---------------------------------------------------------------------------
# _mount_info  (tests the subprocess parsing directly)
# ---------------------------------------------------------------------------


class TestMountInfo:
    def setup_method(self) -> None:
        _clear_cache()

    def test_detects_local_apfs_from_root(self, tmp_path: Path) -> None:
        output = _local_mount("/", "apfs", "sealed, journaled") + "\n"
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "apfs"
        assert is_local is True

    def test_detects_smbfs_network_mount(self, tmp_path: Path) -> None:
        # Simulate tmp_path falling under /, so both mounts match; longest wins.
        smbfs_line = _network_mount("//server/share", "/", "smbfs")
        output = smbfs_line + "\n"
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "smbfs"
        assert is_local is False

    def test_longest_mount_wins(self, tmp_path: Path) -> None:
        # Root is apfs/local; a subdirectory mounted as smbfs overrides it.
        root_line = _local_mount("/", "apfs")
        sub_line = _network_mount("//server/share", str(tmp_path), "smbfs")
        output = root_line + "\n" + sub_line + "\n"
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "smbfs"
        assert is_local is False

    def test_apfs_without_local_flag(self, tmp_path: Path) -> None:
        # APFS without "local" — network-backed iSCSI style.
        line = "/dev/diskX on / (apfs, journaled)"
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            return_value=_mock_mount(line + "\n"),
        ):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "apfs"
        assert is_local is False

    def test_mountpoint_with_spaces_parsed_correctly(self, tmp_path: Path) -> None:
        line = "/dev/diskXsY on /Volumes/My Backup Drive (apfs, local, journaled)"
        # Provide a path that falls under the spaced mountpoint.
        target = Path("/Volumes/My Backup Drive/subdir/file.txt")
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            return_value=_mock_mount(line + "\n" + _local_mount("/", "apfs") + "\n"),
        ):
            fstype, is_local = _mount_info(target)
        assert fstype == "apfs"
        assert is_local is True

    def test_returns_unknown_on_subprocess_failure(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            side_effect=FileNotFoundError("no mount"),
        ):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "unknown"
        assert is_local is False

    def test_returns_unknown_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="mount", timeout=2),
        ):
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "unknown"
        assert is_local is False

    def test_returns_unknown_when_no_mount_matches(self, tmp_path: Path) -> None:
        # Mount output has no entry covering tmp_path.
        output = "/dev/diskX on /some/unrelated/path (apfs, local)\n"
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)):
            _clear_cache()
            fstype, is_local = _mount_info(tmp_path)
        assert fstype == "unknown"

    def test_result_cached_across_calls(self, tmp_path: Path) -> None:
        output = _local_mount("/", "apfs") + "\n"
        with patch(
            "fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)
        ) as mock_run:
            _mount_info(tmp_path)
            _mount_info(tmp_path)
        assert mock_run.call_count == 1

    def test_real_smbfs_mount_line_format(self, tmp_path: Path) -> None:
        # Exact format from macOS for an SMBfs mount.
        smbfs = (
            "//chad@nvx._smb._tcp.local/chad-data on /Volumes/chad-data "
            "(smbfs, nodev, nosuid, mounted by chanson)"
        )
        target = Path("/Volumes/chad-data/testdir/file.txt")
        output = _local_mount("/", "apfs") + "\n" + smbfs + "\n"
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_mount(output)):
            _clear_cache()
            fstype, is_local = _mount_info(target)
        assert fstype == "smbfs"
        assert is_local is False


# ---------------------------------------------------------------------------
# fs_type  (thin wrapper over _mount_info)
# ---------------------------------------------------------------------------


class TestFsType:
    def setup_method(self) -> None:
        _clear_cache()

    def test_returns_fstype_string(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)):
            assert fs_type(tmp_path) == "apfs"

    def test_returns_unknown_on_error(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("unknown", False)):
            assert fs_type(tmp_path) == "unknown"


# ---------------------------------------------------------------------------
# is_network_fs
# ---------------------------------------------------------------------------


class TestIsNetworkFs:
    def setup_method(self) -> None:
        _clear_cache()

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_true_for_all_network_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, False)):
            assert is_network_fs(tmp_path) is True

    @pytest.mark.parametrize("fsname", sorted(LOCAL_FS_TYPES))
    def test_false_for_local_fs_types_with_local_flag(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, True)):
            assert is_network_fs(tmp_path) is False

    def test_false_for_unknown(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("unknown", False)):
            assert is_network_fs(tmp_path) is False

    def test_true_for_apfs_without_local_flag(self, tmp_path: Path) -> None:
        # APFS without "local" = network-backed block storage (iSCSI etc.)
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", False)):
            assert is_network_fs(tmp_path) is True

    def test_false_for_apfs_with_local_flag(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)):
            assert is_network_fs(tmp_path) is False

    def test_smbfs_is_network(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("smbfs", False)):
            assert is_network_fs(tmp_path) is True

    def test_nfs_is_network(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("nfs", False)):
            assert is_network_fs(tmp_path) is True

    def test_apfs_local_backed_by_network_dmg_is_network(self, tmp_path: Path) -> None:
        # APFS with local flag but disk image stored on SMBfs share.
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)), \
             patch("fcompare.engine.fs_info._is_disk_image_on_network", return_value=True):
            assert is_network_fs(tmp_path) is True

    def test_apfs_local_not_backed_by_network_dmg_is_not_network(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)), \
             patch("fcompare.engine.fs_info._is_disk_image_on_network", return_value=False):
            assert is_network_fs(tmp_path) is False


# ---------------------------------------------------------------------------
# is_local_fs
# ---------------------------------------------------------------------------


class TestIsLocalFs:
    def setup_method(self) -> None:
        _clear_cache()

    @pytest.mark.parametrize("fsname", sorted(LOCAL_FS_TYPES))
    def test_true_for_all_local_fs_types_with_local_flag(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, True)):
            assert is_local_fs(tmp_path) is True

    def test_true_for_encrypted_apfs(self, tmp_path: Path) -> None:
        # macOS reports "apfs" for both; encryption transparent at storage layer.
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)):
            assert is_local_fs(tmp_path) is True

    def test_false_for_apfs_without_local_flag(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", False)):
            assert is_local_fs(tmp_path) is False

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_false_for_all_network_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, False)):
            assert is_local_fs(tmp_path) is False

    def test_false_for_unknown(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("unknown", False)):
            assert is_local_fs(tmp_path) is False

    def test_local_and_network_sets_are_disjoint(self) -> None:
        assert LOCAL_FS_TYPES.isdisjoint(NETWORK_FS_TYPES)


# ---------------------------------------------------------------------------
# is_mmap_safe
# ---------------------------------------------------------------------------


class TestIsMmapSafe:
    def setup_method(self) -> None:
        _clear_cache()

    @pytest.mark.parametrize("fsname", sorted(LOCAL_FS_TYPES))
    def test_true_for_local_fs_with_local_flag(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, True)):
            assert is_mmap_safe(tmp_path) is True

    def test_true_for_encrypted_apfs(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)):
            assert is_mmap_safe(tmp_path) is True

    def test_false_for_apfs_without_local_flag(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", False)):
            assert is_mmap_safe(tmp_path) is False

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_false_for_network_fs(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=(fsname, False)):
            assert is_mmap_safe(tmp_path) is False

    def test_false_for_smbfs(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("smbfs", False)):
            assert is_mmap_safe(tmp_path) is False

    def test_false_for_unknown(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info._mount_info", return_value=("unknown", False)):
            assert is_mmap_safe(tmp_path) is False

    def test_false_for_apfs_disk_image_on_network(self, tmp_path: Path) -> None:
        # APFS local flag present but dmg lives on SMBfs → not mmap-safe.
        with patch("fcompare.engine.fs_info._mount_info", return_value=("apfs", True)), \
             patch("fcompare.engine.fs_info._is_disk_image_on_network", return_value=True):
            assert is_mmap_safe(tmp_path) is False


# ---------------------------------------------------------------------------
# _is_disk_image_on_network
# ---------------------------------------------------------------------------


class TestIsDiskImageOnNetwork:
    def setup_method(self) -> None:
        _mount_info.cache_clear()
        _is_disk_image_on_network.cache_clear()

    def _make_hdiutil_plist(self, image_path: str, dev_entry: str) -> bytes:
        import plistlib
        data = {
            "images": [
                {
                    "image-path": image_path,
                    "system-entities": [{"dev-entry": dev_entry}],
                }
            ]
        }
        return plistlib.dumps(data)

    def test_returns_false_on_hdiutil_failure(self, tmp_path: Path) -> None:
        mount_output = f"/dev/disk7s1 on {tmp_path} (apfs, local)\n"
        hdi_mock = MagicMock()
        hdi_mock.returncode = 1
        hdi_mock.stdout = ""
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            side_effect=[_mock_mount(mount_output), hdi_mock],
        ):
            assert _is_disk_image_on_network(tmp_path) is False

    def test_returns_false_when_no_device_found(self, tmp_path: Path) -> None:
        # No mount line matches — no device to look up.
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            return_value=_mock_mount("/dev/disk3s1 on /unrelated (apfs, local)\n"),
        ):
            assert _is_disk_image_on_network(tmp_path) is False

    def test_returns_true_when_dmg_on_network_share(self, tmp_path: Path) -> None:
        mount_output = (
            f"/dev/disk7s1 on {tmp_path} (apfs, local)\n"
            "/dev/disk3s1 on / (apfs, local)\n"
            "//server/share on /Volumes/smb (smbfs, nodev)\n"
        )
        plist_bytes = self._make_hdiutil_plist(
            image_path="/Volumes/smb/archive.dmg",
            dev_entry="/dev/disk7",
        )
        hdi_mock = MagicMock()
        hdi_mock.returncode = 0
        hdi_mock.stdout = plist_bytes.decode()

        def _run_side_effect(cmd: list, **kw: object) -> MagicMock:
            if cmd[0] == "mount":
                return _mock_mount(mount_output)
            return hdi_mock

        with patch("fcompare.engine.fs_info.subprocess.run", side_effect=_run_side_effect):
            result = _is_disk_image_on_network(tmp_path)
        assert result is True

    def test_returns_false_when_dmg_on_local_disk(self, tmp_path: Path) -> None:
        mount_output = (
            f"/dev/disk7s1 on {tmp_path} (apfs, local)\n"
            "/dev/disk3s1 on / (apfs, local)\n"
        )
        plist_bytes = self._make_hdiutil_plist(
            image_path="/Users/chanson/images/archive.dmg",
            dev_entry="/dev/disk7",
        )
        hdi_mock = MagicMock()
        hdi_mock.returncode = 0
        hdi_mock.stdout = plist_bytes.decode()

        def _run_side_effect(cmd: list, **kw: object) -> MagicMock:
            if cmd[0] == "mount":
                return _mock_mount(mount_output)
            return hdi_mock

        with patch("fcompare.engine.fs_info.subprocess.run", side_effect=_run_side_effect):
            result = _is_disk_image_on_network(tmp_path)
        assert result is False

    def test_returns_false_on_exception(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run", side_effect=OSError("no hdiutil")
        ):
            assert _is_disk_image_on_network(tmp_path) is False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestFsTypeConstants:
    def test_both_constants_are_frozensets(self) -> None:
        assert isinstance(NETWORK_FS_TYPES, frozenset)
        assert isinstance(LOCAL_FS_TYPES, frozenset)

    def test_all_entries_lowercase(self) -> None:
        for name in NETWORK_FS_TYPES | LOCAL_FS_TYPES:
            assert name == name.lower()

    def test_network_contains_expected_types(self) -> None:
        for expected in ("smbfs", "nfs", "afpfs", "cifs"):
            assert expected in NETWORK_FS_TYPES

    def test_local_contains_expected_types(self) -> None:
        for expected in ("apfs", "hfs", "exfat", "msdos", "ntfs"):
            assert expected in LOCAL_FS_TYPES

    def test_apfs_is_local_not_network(self) -> None:
        assert "apfs" in LOCAL_FS_TYPES
        assert "apfs" not in NETWORK_FS_TYPES

    def test_sets_are_disjoint(self) -> None:
        assert LOCAL_FS_TYPES.isdisjoint(NETWORK_FS_TYPES)
