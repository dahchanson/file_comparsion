"""Unit tests for engine/fs_info.py."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fcompare.engine.fs_info import NETWORK_FS_TYPES, fs_type, is_network_fs


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
        # subprocess.run called only once despite two fs_type calls.
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
        # Different resolved paths → two subprocess calls.
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

    @pytest.mark.parametrize("fsname", ["apfs", "hfs", "exfat", "msdos", "ntfs", "unknown"])
    def test_false_for_local_fs_types(self, tmp_path: Path, fsname: str) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat(fsname)):
            assert is_network_fs(tmp_path) is False

    def test_false_when_fs_type_returns_unknown(self, tmp_path: Path) -> None:
        with patch(
            "fcompare.engine.fs_info.subprocess.run", side_effect=OSError("unreachable")
        ):
            assert is_network_fs(tmp_path) is False

    def test_smbfs_is_network(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("smbfs")):
            assert is_network_fs(tmp_path) is True

    def test_nfs_is_network(self, tmp_path: Path) -> None:
        with patch("fcompare.engine.fs_info.subprocess.run", return_value=_mock_stat("nfs")):
            assert is_network_fs(tmp_path) is True


# ---------------------------------------------------------------------------
# NETWORK_FS_TYPES constant
# ---------------------------------------------------------------------------


class TestNetworkFsTypes:
    def test_is_frozenset(self) -> None:
        assert isinstance(NETWORK_FS_TYPES, frozenset)

    def test_all_lowercase(self) -> None:
        assert all(name == name.lower() for name in NETWORK_FS_TYPES)

    def test_contains_expected_types(self) -> None:
        for expected in ("smbfs", "nfs", "afpfs", "cifs"):
            assert expected in NETWORK_FS_TYPES
