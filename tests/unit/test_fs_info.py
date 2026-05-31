"""Unit tests for engine/fs_info.py."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fcompare.engine.fs_info import (
    LOCAL_FS_TYPES,
    NETWORK_FS_TYPES,
    fs_type,
    is_local_fs,
    is_mmap_safe,
    is_network_fs,
)


def _mock_stat(fs_name: str) -> MagicMock:
    """Return a mock subprocess.CompletedProcess with the given fs type name."""
    m = MagicMock()
    m.stdout = fs_name + "\n"
    m.returncode = 0
    return m


def _clear_cache() -> None:
    fs_type.cache_clear()


# ---------------------------------------------------------------------------
# fs_type
# ---------------------------------------------------------------------------


class TestFsType:
    def setup_method(self) -> None:
        _clear_cache()

    def test_returns_lowercase_fs_name(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("APFS")):
            result = fs_type(tmp_path)
        assert result == "apfs"

    def test_strips_whitespace_from_output(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("  smbfs  ")):
            result = fs_type(tmp_path)
        assert result == "smbfs"

    def test_returns_unknown_on_subprocess_exception(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run", side_effect=FileNotFoundError("no stat")
        ):
            result = fs_type(tmp_path)
        assert result == "unknown"

    def test_returns_unknown_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="stat", timeout=2),
        ):
            result = fs_type(tmp_path)
        assert result == "unknown"

    def test_returns_unknown_on_empty_output(self, tmp_path: Path) -> None:
        m = MagicMock()
        m.stdout = ""
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=m):
            result = fs_type(tmp_path)
        assert result == "unknown"

    def test_result_is_cached_across_calls(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("apfs")
        ) as mock_run:
            fs_type(tmp_path)
            fs_type(tmp_path)
        assert mock_run.call_count == 1

    def test_cache_is_independent_per_path(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a"
        path_a.mkdir()
        path_b = tmp_path / "b"
        path_b.mkdir()
        with patch(
            "fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("apfs")
        ) as mock_run:
            fs_type(path_a)
            fs_type(path_b)
        assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# is_network_fs
# ---------------------------------------------------------------------------


class TestIsNetworkFs:
    def setup_method(self) -> None:
        _clear_cache()

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_true_for_all_network_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_network_fs(tmp_path) is True

    @pytest.mark.parametrize("fsname", sorted(LOCAL_FS_TYPES) + ["unknown"])
    def test_false_for_local_and_unknown_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_network_fs(tmp_path) is False

    def test_false_when_subprocess_raises(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run", side_effect=OSError("unreachable")
        ):
            assert is_network_fs(tmp_path) is False


# ---------------------------------------------------------------------------
# is_local_fs
# ---------------------------------------------------------------------------


class TestIsLocalFs:
    def setup_method(self) -> None:
        _clear_cache()

    @pytest.mark.parametrize("fsname", sorted(LOCAL_FS_TYPES))
    def test_true_for_all_local_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_local_fs(tmp_path) is True

    def test_true_for_apfs_encrypted_and_unencrypted(self, tmp_path: Path) -> None:
        # macOS reports "apfs" for both; encryption is transparent at the storage layer.
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("apfs")):
            assert is_local_fs(tmp_path) is True

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_false_for_all_network_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_local_fs(tmp_path) is False

    def test_false_for_unknown(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", side_effect=OSError("no stat")):
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
    def test_true_for_all_local_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_mmap_safe(tmp_path) is True

    def test_true_for_apfs(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("apfs")):
            assert is_mmap_safe(tmp_path) is True

    @pytest.mark.parametrize("fsname", sorted(NETWORK_FS_TYPES))
    def test_false_for_all_network_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_mmap_safe(tmp_path) is False

    def test_false_for_smbfs(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("smbfs")):
            assert is_mmap_safe(tmp_path) is False

    def test_false_for_unknown(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", side_effect=OSError("no stat")):
            assert is_mmap_safe(tmp_path) is False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestFsTypeConstants:
    def test_network_fs_types_is_frozenset(self) -> None:
        assert isinstance(NETWORK_FS_TYPES, frozenset)

    def test_local_fs_types_is_frozenset(self) -> None:
        assert isinstance(LOCAL_FS_TYPES, frozenset)

    def test_both_constants_all_lowercase(self) -> None:
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
