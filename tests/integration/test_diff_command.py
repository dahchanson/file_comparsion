"""Integration tests for the diff command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from fcompare.cli import app
from fcompare.commands.diff import compare_directories
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
# compare_directories logic
# ---------------------------------------------------------------------------


class TestCompareDirectories:
    def test_identical_directories_reports_all_same(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        spec = {"a.txt": "aaa", "sub": {"b.txt": "bbb"}}
        make_tree(left, spec)
        make_tree(right, spec)

        result = compare_directories(left, right, WalkOptions())

        assert len(result.identical) == 2
        assert result.modified == []
        assert result.only_in_left == []
        assert result.only_in_right == []

    def test_detects_modified_file(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"f.txt": "version A"})
        make_tree(right, {"f.txt": "version B"})

        result = compare_directories(left, right, WalkOptions())

        assert len(result.modified) == 1
        assert str(result.modified[0][0].rel_path) == "f.txt"
        assert result.identical == []

    def test_detects_file_only_in_left(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"common.txt": "x", "exclusive.txt": "y"})
        make_tree(right, {"common.txt": "x"})

        result = compare_directories(left, right, WalkOptions())

        assert len(result.only_in_left) == 1
        assert result.only_in_left[0].rel_path == Path("exclusive.txt")

    def test_detects_file_only_in_right(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"common.txt": "x"})
        make_tree(right, {"common.txt": "x", "exclusive.txt": "z"})

        result = compare_directories(left, right, WalkOptions())

        assert len(result.only_in_right) == 1
        assert result.only_in_right[0].rel_path == Path("exclusive.txt")

    def test_empty_directories(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()

        result = compare_directories(left, right, WalkOptions())

        assert result.total_files == 0

    def test_recurse_false_ignores_subdirectory_files(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"top.txt": "same", "sub": {"nested.txt": "left only"}})
        make_tree(right, {"top.txt": "same"})

        result = compare_directories(left, right, WalkOptions(recurse=False))

        assert len(result.identical) == 1
        assert result.only_in_left == []

    def test_exclude_pattern_filters_files(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"keep.txt": "a", "drop.log": "b"})
        make_tree(right, {"keep.txt": "a"})

        result = compare_directories(left, right, WalkOptions(exclude_patterns=["*.log"]))

        # drop.log excluded from left, so only keep.txt remains → 1 identical
        assert len(result.identical) == 1
        assert result.only_in_left == []

    def test_keys_files_by_relative_path(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"sub": {"f.txt": "left content"}})
        make_tree(right, {"sub": {"f.txt": "right content"}})

        result = compare_directories(left, right, WalkOptions())

        assert len(result.modified) == 1
        assert result.modified[0][0].rel_path == Path("sub/f.txt")

    def test_total_files_matches_sum_of_all_categories(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"same.txt": "s", "modified.txt": "A", "left_only.txt": "L"})
        make_tree(right, {"same.txt": "s", "modified.txt": "B", "right_only.txt": "R"})

        result = compare_directories(left, right, WalkOptions())

        assert result.total_files == (
            len(result.identical)
            + len(result.modified)
            + len(result.only_in_left)
            + len(result.only_in_right)
        )


# ---------------------------------------------------------------------------
# CLI via CliRunner
# ---------------------------------------------------------------------------


class TestDiffCLI:
    def test_diff_json_format_output_is_valid_json(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"f.txt": "hello"})
        make_tree(right, {"f.txt": "hello"})

        result = runner.invoke(app, ["diff", str(left), str(right), "--format", "json"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert "summary" in parsed
        assert parsed["summary"]["identical"] == 1

    def test_diff_json_contains_modified_entry(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"f.txt": "version A"})
        make_tree(right, {"f.txt": "version B"})

        result = runner.invoke(app, ["diff", str(left), str(right), "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["modified"] == 1
        assert parsed["modified"][0]["rel_path"] == "f.txt"
        assert parsed["modified"][0]["left_sha256"] != parsed["modified"][0]["right_sha256"]

    def test_diff_cli_exits_nonzero_for_missing_left_dir(self, tmp_path: Path) -> None:
        right = tmp_path / "right"
        right.mkdir()
        result = runner.invoke(app, ["diff", str(tmp_path / "nope"), str(right)])
        assert result.exit_code != 0

    def test_diff_cli_exits_nonzero_for_file_instead_of_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_bytes(b"x")
        right = tmp_path / "right"
        right.mkdir()
        result = runner.invoke(app, ["diff", str(f), str(right)])
        assert result.exit_code != 0

    def test_diff_rich_output_contains_summary_line(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"a.txt": "x", "b.txt": "y"})
        make_tree(right, {"a.txt": "x", "b.txt": "CHANGED"})

        result = runner.invoke(app, ["diff", str(left), str(right)])

        assert result.exit_code == 0
        assert "identical" in result.output
        assert "modified" in result.output

    def test_diff_show_modified_only_excludes_identical(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"same.txt": "s", "changed.txt": "A"})
        make_tree(right, {"same.txt": "s", "changed.txt": "B"})

        result = runner.invoke(
            app, ["diff", str(left), str(right), "--format", "json", "--show", "modified"]
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "modified" in parsed
        assert "identical" not in parsed

    def test_diff_rich_shows_only_left_and_only_right_markers(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"left_only.txt": "L"})
        make_tree(right, {"right_only.txt": "R"})

        result = runner.invoke(app, ["diff", str(left), str(right)])

        assert result.exit_code == 0
        assert "only left" in result.output
        assert "only right" in result.output

    def test_diff_rich_show_filter_with_no_matches_shows_empty_message(
        self, tmp_path: Path
    ) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        make_tree(left, {"f.txt": "same"})
        make_tree(right, {"f.txt": "same"})

        # --show modified but all files are identical → no rows.
        result = runner.invoke(app, ["diff", str(left), str(right), "--show", "modified"])

        assert result.exit_code == 0
        assert "no files match" in result.output

    def test_diff_version_flag_outputs_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
