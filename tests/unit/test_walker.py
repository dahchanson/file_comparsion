"""Unit tests for engine/walker.py."""

import warnings
from pathlib import Path

import pytest

from fcompare.engine.walker import WalkOptions, walk


def make_tree(base: Path, spec: dict) -> None:
    """Recursively create files and directories from a spec dict.

    Keys ending in '/' are directories; all others are files whose value is
    the byte content to write.
    """
    for name, value in spec.items():
        target = base / name
        if isinstance(value, dict):
            target.mkdir(parents=True, exist_ok=True)
            make_tree(target, value)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value if isinstance(value, bytes) else value.encode())


# ---------------------------------------------------------------------------
# Basic traversal
# ---------------------------------------------------------------------------


class TestWalkBasic:
    def test_returns_all_files_in_flat_dir(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "aaa", "b.txt": "bbb", "c.txt": "ccc"})
        records = list(walk(tmp_path))
        names = {r.rel_path.name for r in records}
        assert names == {"a.txt", "b.txt", "c.txt"}

    def test_returns_files_in_subdirectories(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"sub": {"deep.txt": "x"}})
        records = list(walk(tmp_path))
        assert any(r.rel_path == Path("sub/deep.txt") for r in records)

    def test_does_not_yield_directories(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"subdir": {"file.txt": "x"}})
        records = list(walk(tmp_path))
        for r in records:
            assert r.path.is_file()

    def test_rel_path_is_relative_to_root(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a": {"b": {"c.txt": "data"}}})
        records = list(walk(tmp_path))
        assert records[0].rel_path == Path("a/b/c.txt")

    def test_size_and_mtime_populated(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_bytes(b"hello")
        records = list(walk(tmp_path))
        assert len(records) == 1
        assert records[0].size == 5
        assert records[0].mtime > 0

    def test_sha256_is_empty_string(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_bytes(b"x")
        records = list(walk(tmp_path))
        assert records[0].sha256 == ""

    def test_empty_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert list(walk(tmp_path)) == []

    def test_default_options_used_when_none_passed(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_bytes(b"x")
        records = list(walk(tmp_path, None))
        assert len(records) == 1


# ---------------------------------------------------------------------------
# recurse=False
# ---------------------------------------------------------------------------


class TestWalkRecurse:
    def test_recurse_false_excludes_subdirectory_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"top.txt": "t", "sub": {"deep.txt": "d"}})
        opts = WalkOptions(recurse=False)
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert names == {"top.txt"}
        assert "deep.txt" not in names

    def test_recurse_true_includes_subdirectory_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"top.txt": "t", "sub": {"deep.txt": "d"}})
        records = list(walk(tmp_path, WalkOptions(recurse=True)))
        names = {r.rel_path.name for r in records}
        assert "top.txt" in names
        assert "deep.txt" in names


# ---------------------------------------------------------------------------
# Hidden files
# ---------------------------------------------------------------------------


class TestWalkHidden:
    def test_skips_hidden_files_by_default(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {".hidden": "h", "visible.txt": "v"})
        records = list(walk(tmp_path))
        names = {r.rel_path.name for r in records}
        assert ".hidden" not in names
        assert "visible.txt" in names

    def test_skips_files_inside_hidden_directory_by_default(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {".hiddendir": {"file.txt": "x"}, "visible.txt": "v"})
        records = list(walk(tmp_path))
        names = {r.rel_path.name for r in records}
        assert "file.txt" not in names

    def test_include_hidden_yields_hidden_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {".hidden": "h", "visible.txt": "v"})
        opts = WalkOptions(include_hidden=True)
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert ".hidden" in names
        assert "visible.txt" in names


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestWalkExcludePatterns:
    def test_exclude_by_extension(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"keep.txt": "k", "drop.log": "d"})
        opts = WalkOptions(exclude_patterns=["*.log"])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert "keep.txt" in names
        assert "drop.log" not in names

    def test_exclude_specific_filename(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"keep.txt": "k", "exclude_me.txt": "e"})
        opts = WalkOptions(exclude_patterns=["exclude_me.txt"])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert "keep.txt" in names
        assert "exclude_me.txt" not in names

    def test_multiple_exclude_patterns(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "a", "b.log": "b", "c.tmp": "c"})
        opts = WalkOptions(exclude_patterns=["*.log", "*.tmp"])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert names == {"a.txt"}

    def test_no_exclude_patterns_keeps_all(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "a", "b.log": "b"})
        records = list(walk(tmp_path, WalkOptions(exclude_patterns=[])))
        assert len(records) == 2


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


class TestWalkSymlinks:
    def test_skips_symlinks_by_default(self, tmp_path: Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.write_bytes(b"real")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)
        records = list(walk(tmp_path))
        paths = {r.path for r in records}
        assert link not in paths
        assert real_file in paths

    def test_follow_symlinks_includes_symlink_files(self, tmp_path: Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.write_bytes(b"real")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)
        opts = WalkOptions(follow_symlinks=True)
        records = list(walk(tmp_path, opts))
        paths = {r.path for r in records}
        assert link in paths
        assert real_file in paths


# ---------------------------------------------------------------------------
# Permission errors
# ---------------------------------------------------------------------------


class TestWalkPermissionErrors:
    def test_permission_error_on_file_emits_warning_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_tree(tmp_path, {"good.txt": "g", "bad.txt": "b"})

        original_is_file = Path.is_file

        def flaky_is_file(self: Path) -> bool:
            if self.name == "bad.txt":
                raise PermissionError("denied")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", flaky_is_file)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = list(walk(tmp_path))

        names = {r.rel_path.name for r in records}
        assert "good.txt" in names
        assert "bad.txt" not in names
        assert any("bad.txt" in str(w.message) for w in caught)

    def test_permission_error_on_stat_emits_warning_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_tree(tmp_path, {"good.txt": "g", "bad.txt": "b"})

        original_stat = Path.stat
        # is_symlink() calls stat(follow_symlinks=False) internally; so does the explicit
        # entry.stat() call in the walker.  We let the first lstat call through (for
        # is_symlink) and raise on the second (the walker's explicit stat call).
        lstat_calls: dict[str, int] = {}

        def flaky_stat(self: Path, *, follow_symlinks: bool = True) -> object:
            if self.name == "bad.txt" and not follow_symlinks:
                lstat_calls[str(self)] = lstat_calls.get(str(self), 0) + 1
                if lstat_calls[str(self)] > 1:
                    raise PermissionError("stat denied")
            return original_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", flaky_stat)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = list(walk(tmp_path))

        names = {r.rel_path.name for r in records}
        assert "good.txt" in names
        assert "bad.txt" not in names
        assert any("bad.txt" in str(w.message) for w in caught)

    def test_exclude_full_path_pattern_filters_nested_file(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"keep.txt": "k", "sub": {"drop.txt": "d"}})
        # Pattern matches full absolute path string, not just filename.
        opts = WalkOptions(exclude_patterns=[str(tmp_path / "sub" / "drop.txt")])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert "keep.txt" in names
        assert "drop.txt" not in names
