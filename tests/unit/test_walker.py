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
    def test_permission_error_on_scandir_emits_warning_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        make_tree(tmp_path, {"good_dir": {"good.txt": "g"}, "bad_dir": {"secret.txt": "s"}})
        original_scandir = os.scandir

        def flaky_scandir(path: str):  # type: ignore[no-untyped-def]
            if Path(path).name == "bad_dir":
                raise PermissionError("directory denied")
            return original_scandir(path)

        monkeypatch.setattr("fcompare.engine.walker.os.scandir", flaky_scandir)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = list(walk(tmp_path))

        names = {r.rel_path.name for r in records}
        assert names == {"good.txt"}
        assert any("bad_dir" in str(w.message) for w in caught)

    def test_permission_error_on_entry_emits_warning_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        make_tree(tmp_path, {"good.txt": "g", "bad.txt": "b"})
        original_scandir = os.scandir

        class FlakyDirEntry:
            def __init__(self, inner: os.DirEntry[str]) -> None:
                self._inner = inner
                self.name = inner.name
                self.path = inner.path

            def is_symlink(self) -> bool:
                return self._inner.is_symlink()

            def is_dir(self, *, follow_symlinks: bool = True) -> bool:
                return self._inner.is_dir(follow_symlinks=follow_symlinks)

            def is_file(self, *, follow_symlinks: bool = True) -> bool:
                if self.name == "bad.txt":
                    raise PermissionError("denied")
                return self._inner.is_file(follow_symlinks=follow_symlinks)

            def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
                return self._inner.stat(follow_symlinks=follow_symlinks)

        class FlakyScandirIter:
            def __init__(self, inner_it: object) -> None:
                self._it = inner_it

            def __enter__(self) -> "FlakyScandirIter":
                self._it.__enter__()  # type: ignore[attr-defined]
                return self

            def __iter__(self):  # type: ignore[no-untyped-def]
                return (FlakyDirEntry(e) for e in self._it)  # type: ignore[attr-defined]

            def __exit__(self, *exc: object) -> None:
                self._it.__exit__(*exc)  # type: ignore[attr-defined]

        monkeypatch.setattr(
            "fcompare.engine.walker.os.scandir",
            lambda p: FlakyScandirIter(original_scandir(p)),
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = list(walk(tmp_path))

        names = {r.rel_path.name for r in records}
        assert "good.txt" in names
        assert "bad.txt" not in names
        assert any("bad.txt" in str(w.message) for w in caught)

    def test_prunes_hidden_and_excluded_directories_without_descending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        make_tree(
            tmp_path,
            {
                "visible": {"ok.txt": "1"},
                ".Spotlight-V100": {"index.db": "2"},
                "node_modules": {"pkg": {"index.js": "3"}},
            },
        )
        scanned_dirs: list[str] = []
        original_scandir = os.scandir

        def tracking_scandir(path: str):  # type: ignore[no-untyped-def]
            scanned_dirs.append(Path(path).name)
            return original_scandir(path)

        monkeypatch.setattr("fcompare.engine.walker.os.scandir", tracking_scandir)

        opts = WalkOptions(exclude_patterns=["node_modules"])
        records = list(walk(tmp_path, opts))

        assert [r.rel_path for r in records] == [Path("visible/ok.txt")]
        assert ".Spotlight-V100" not in scanned_dirs
        assert "node_modules" not in scanned_dirs

    def test_follow_symlinks_avoids_infinite_directory_cycle(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f.txt").write_bytes(b"ok")
        (sub / "loop").symlink_to(tmp_path)

        records = list(walk(tmp_path, WalkOptions(follow_symlinks=True)))
        assert len(records) == 1
        assert records[0].ino > 0

    def test_exclude_full_path_pattern_filters_nested_file(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"keep.txt": "k", "sub": {"drop.txt": "d"}})
        # Pattern matches full absolute path string, not just filename.
        opts = WalkOptions(exclude_patterns=[str(tmp_path / "sub" / "drop.txt")])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert "keep.txt" in names
        assert "drop.txt" not in names

    def test_exclude_relative_path_glob_filters_nested_file(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"keep.txt": "k", "sub": {"drop.txt": "d"}})
        opts = WalkOptions(exclude_patterns=["sub/*.txt"])
        records = list(walk(tmp_path, opts))
        names = {r.rel_path.name for r in records}
        assert names == {"keep.txt"}
