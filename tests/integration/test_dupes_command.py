"""Integration tests for the dupes command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from fcompare.cli import app
from fcompare.commands.dupes import DeleteKeep, deletion_plan, find_duplicates
from fcompare.engine.walker import WalkOptions

runner = CliRunner()


def make_tree(base: Path, spec: dict) -> None:
    """Create files and directories from a spec dict. Values are file contents (str or bytes)."""
    for name, value in spec.items():
        target = base / name
        if isinstance(value, dict):
            target.mkdir(parents=True, exist_ok=True)
            make_tree(target, value)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value if isinstance(value, bytes) else value.encode())


# ---------------------------------------------------------------------------
# find_duplicates logic
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    def test_finds_exact_duplicate_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same content", "b.txt": "same content"})

        groups = find_duplicates(tmp_path, WalkOptions())

        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_finds_three_way_duplicate(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "dup", "b.txt": "dup", "c.txt": "dup"})

        groups = find_duplicates(tmp_path, WalkOptions())

        assert len(groups) == 1
        assert len(groups[0].files) == 3

    def test_no_false_positives_same_size_different_content(self, tmp_path: Path) -> None:
        # Both files are 4 bytes but different content.
        make_tree(tmp_path, {"a.txt": "aaaa", "b.txt": "bbbb"})

        groups = find_duplicates(tmp_path, WalkOptions())

        assert groups == []

    def test_single_file_not_reported(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"only.txt": "unique"})

        groups = find_duplicates(tmp_path, WalkOptions())

        assert groups == []

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        groups = find_duplicates(tmp_path, WalkOptions())
        assert groups == []

    def test_ignores_files_below_min_size(self, tmp_path: Path) -> None:
        # Two identical small files that should be skipped.
        make_tree(tmp_path, {"a.txt": "hi", "b.txt": "hi"})

        groups = find_duplicates(tmp_path, WalkOptions(), min_size=10)

        assert groups == []

    def test_includes_files_at_exactly_min_size(self, tmp_path: Path) -> None:
        content = "x" * 5
        make_tree(tmp_path, {"a.txt": content, "b.txt": content})

        groups = find_duplicates(tmp_path, WalkOptions(), min_size=5)

        assert len(groups) == 1

    def test_recurse_false_ignores_subdirectory_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"top.txt": "dup", "sub": {"nested.txt": "dup"}})

        groups = find_duplicates(tmp_path, WalkOptions(recurse=False))

        # Only top.txt is seen; no duplicates at the top level.
        assert groups == []

    def test_recurse_true_finds_cross_subdirectory_dupes(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a": {"f.txt": "same"}, "b": {"f.txt": "same"}})

        groups = find_duplicates(tmp_path, WalkOptions(recurse=True))

        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_exclude_pattern_filters_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "dup", "b.txt": "dup", "c.log": "dup"})

        groups = find_duplicates(tmp_path, WalkOptions(exclude_patterns=["*.log"]))

        # a.txt and b.txt are duplicates; c.log excluded → still 1 group of 2.
        assert len(groups) == 1
        paths = {r.path.name for r in groups[0].files}
        assert "c.log" not in paths

    def test_sorted_by_wasted_bytes_descending(self, tmp_path: Path) -> None:
        # Group 1: 3 copies of a 100-byte file → 200 bytes wasted.
        # Group 2: 2 copies of a 10-byte file  → 10 bytes wasted.
        big = "B" * 100
        small = "S" * 10
        make_tree(
            tmp_path,
            {"b1.txt": big, "b2.txt": big, "b3.txt": big, "s1.txt": small, "s2.txt": small},
        )

        groups = find_duplicates(tmp_path, WalkOptions())

        assert len(groups) == 2
        assert groups[0].wasted_bytes > groups[1].wasted_bytes

    def test_sha256_matches_file_content(self, tmp_path: Path) -> None:
        import hashlib

        content = b"known content"
        make_tree(tmp_path, {"a.bin": content, "b.bin": content})

        groups = find_duplicates(tmp_path, WalkOptions())

        assert groups[0].sha256 == hashlib.sha256(content).hexdigest()

    def test_multiple_independent_dupe_groups(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {"a1.txt": "group_a", "a2.txt": "group_a", "b1.txt": "group_b", "b2.txt": "group_b"},
        )

        groups = find_duplicates(tmp_path, WalkOptions())

        assert len(groups) == 2


# ---------------------------------------------------------------------------
# deletion_plan
# ---------------------------------------------------------------------------


class TestDeletionPlan:
    def _make_group(self, tmp_path: Path, filenames: list[str]) -> object:
        from fcompare.engine.models import DupeGroup, FileRecord

        files = []
        for i, name in enumerate(filenames):
            p = tmp_path / name
            p.write_bytes(b"x")
            files.append(
                FileRecord(path=p, rel_path=Path(name), size=1, mtime=float(i * 100))
            )
        return DupeGroup(sha256="abc", files=files)

    def test_keep_oldest_deletes_newer_files(self, tmp_path: Path) -> None:
        group = self._make_group(tmp_path, ["old.txt", "mid.txt", "new.txt"])  # type: ignore[arg-type]
        to_delete = deletion_plan(group, DeleteKeep.oldest)  # type: ignore[arg-type]
        names = {r.path.name for r in to_delete}
        assert "old.txt" not in names
        assert len(to_delete) == 2

    def test_keep_newest_deletes_older_files(self, tmp_path: Path) -> None:
        group = self._make_group(tmp_path, ["old.txt", "mid.txt", "new.txt"])  # type: ignore[arg-type]
        to_delete = deletion_plan(group, DeleteKeep.newest)  # type: ignore[arg-type]
        names = {r.path.name for r in to_delete}
        assert "new.txt" not in names
        assert len(to_delete) == 2

    def test_keep_first_keeps_alphabetically_first(self, tmp_path: Path) -> None:
        group = self._make_group(tmp_path, ["charlie.txt", "alpha.txt", "bravo.txt"])  # type: ignore[arg-type]
        to_delete = deletion_plan(group, DeleteKeep.first)  # type: ignore[arg-type]
        names = {r.path.name for r in to_delete}
        assert "alpha.txt" not in names
        assert len(to_delete) == 2


# ---------------------------------------------------------------------------
# CLI via CliRunner
# ---------------------------------------------------------------------------


class TestDupesCLI:
    def test_dupes_json_output_is_valid_json(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same", "b.txt": "same"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--format", "json"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert "summary" in parsed
        assert parsed["summary"]["groups"] == 1

    def test_dupes_json_no_dupes_reports_zero(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "unique_a", "b.txt": "unique_b"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["groups"] == 0
        assert parsed["summary"]["reclaimable_bytes"] == 0

    def test_dupes_rich_output_contains_summary(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same", "b.txt": "same"})

        result = runner.invoke(app, ["dupes", str(tmp_path)])

        assert result.exit_code == 0
        assert "dupe group" in result.output

    def test_dupes_cli_exits_nonzero_for_missing_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["dupes", str(tmp_path / "nope")])
        assert result.exit_code != 0

    def test_dupes_cli_exits_nonzero_for_file_argument(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_bytes(b"x")
        result = runner.invoke(app, ["dupes", str(f)])
        assert result.exit_code != 0

    def test_dupes_delete_keep_without_confirm_does_not_delete(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same", "b.txt": "same"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--delete-keep", "oldest"])

        assert result.exit_code == 0
        # Both files must still exist.
        assert (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").exists()
        assert "would delete" in result.output

    def test_dupes_confirm_without_delete_keep_exits_nonzero(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "x"})
        result = runner.invoke(app, ["dupes", str(tmp_path), "--confirm"])
        assert result.exit_code != 0

    def test_dupes_json_groups_contain_file_paths(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "dup", "b.txt": "dup"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--format", "json"])

        parsed = json.loads(result.output)
        file_paths = parsed["groups"][0]["files"]
        assert len(file_paths) == 2
        assert all(str(tmp_path) in p for p in file_paths)

    def test_dupes_rich_no_dupes_shows_no_duplicates_message(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "unique_a", "b.txt": "unique_b"})

        result = runner.invoke(app, ["dupes", str(tmp_path)])

        assert result.exit_code == 0
        assert "No duplicate" in result.output

    def test_dupes_confirm_deletes_redundant_file(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same", "b.txt": "same"})

        result = runner.invoke(
            app, ["dupes", str(tmp_path), "--delete-keep", "first", "--confirm"]
        )

        assert result.exit_code == 0
        remaining = [f for f in tmp_path.iterdir() if f.is_file()]
        assert len(remaining) == 1
        assert "deleted" in result.output

    def test_dupes_delete_keep_no_dupes_reports_nothing_to_delete(self, tmp_path: Path) -> None:
        # Single unique file — deletion plan has nothing to do.
        make_tree(tmp_path, {"only.txt": "unique"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--delete-keep", "oldest"])

        assert result.exit_code == 0
        assert (tmp_path / "only.txt").exists()

    def test_dupes_verbose_exits_zero_and_produces_results(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "same", "b.txt": "same", "c.txt": "unique"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--verbose"])

        assert result.exit_code == 0
        assert "dupe group" in result.output

    def test_dupes_verbose_json_suppresses_progress_output(self, tmp_path: Path) -> None:
        # --verbose is silently ignored with --format json so pipe consumers
        # receive clean JSON with no terminal escape sequences mixed in.
        make_tree(tmp_path, {"a.txt": "dup", "b.txt": "dup"})

        result = runner.invoke(app, ["dupes", str(tmp_path), "--verbose", "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["groups"] == 1

    def test_dupes_verbose_result_matches_non_verbose(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.txt": "dup", "b.txt": "dup", "c.txt": "unique"})

        quiet = runner.invoke(app, ["dupes", str(tmp_path), "--format", "json"])
        verbose = runner.invoke(app, ["dupes", str(tmp_path), "--verbose", "--format", "json"])

        assert json.loads(quiet.output)["summary"] == json.loads(verbose.output)["summary"]
