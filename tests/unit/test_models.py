"""Unit tests for engine/models.py."""

from pathlib import Path

from fcompare.engine.models import DiffResult, DupeGroup, FileRecord


def make_record(
    path: str = "/a/file.txt",
    rel_path: str = "file.txt",
    size: int = 100,
    mtime: float = 1000.0,
    sha256: str = "abc123",
) -> FileRecord:
    return FileRecord(
        path=Path(path),
        rel_path=Path(rel_path),
        size=size,
        mtime=mtime,
        sha256=sha256,
    )


# ---------------------------------------------------------------------------
# FileRecord
# ---------------------------------------------------------------------------


class TestFileRecord:
    def test_sha256_defaults_to_empty_string(self) -> None:
        record = FileRecord(
            path=Path("/a/file.txt"),
            rel_path=Path("file.txt"),
            size=100,
            mtime=1000.0,
        )
        assert record.sha256 == ""

    def test_equality_based_on_path(self) -> None:
        r1 = make_record(path="/a/file.txt")
        r2 = make_record(path="/a/file.txt", sha256="different_hash")
        assert r1 == r2

    def test_inequality_different_paths(self) -> None:
        r1 = make_record(path="/a/file.txt")
        r2 = make_record(path="/b/file.txt")
        assert r1 != r2

    def test_equality_returns_not_implemented_for_non_record(self) -> None:
        r = make_record()
        assert r.__eq__("not a record") is NotImplemented

    def test_hashable_can_be_used_in_set(self) -> None:
        r1 = make_record(path="/a/file.txt")
        r2 = make_record(path="/a/file.txt")
        r3 = make_record(path="/b/file.txt")
        assert len({r1, r2, r3}) == 2

    def test_fields_stored_correctly(self) -> None:
        r = make_record(path="/x/y.txt", rel_path="y.txt", size=512, mtime=9999.9, sha256="ff")
        assert r.path == Path("/x/y.txt")
        assert r.rel_path == Path("y.txt")
        assert r.size == 512
        assert r.mtime == 9999.9
        assert r.sha256 == "ff"


# ---------------------------------------------------------------------------
# DiffResult
# ---------------------------------------------------------------------------


class TestDiffResult:
    def test_defaults_to_empty_lists(self) -> None:
        result = DiffResult(left_root=Path("/a"), right_root=Path("/b"))
        assert result.only_in_left == []
        assert result.only_in_right == []
        assert result.modified == []
        assert result.identical == []

    def test_total_files_sums_all_categories(self) -> None:
        r = make_record()
        result = DiffResult(
            left_root=Path("/a"),
            right_root=Path("/b"),
            only_in_left=[r],
            only_in_right=[r],
            modified=[(r, r), (r, r)],
            identical=[(r, r)],
        )
        assert result.total_files == 5

    def test_total_files_empty_result_is_zero(self) -> None:
        result = DiffResult(left_root=Path("/a"), right_root=Path("/b"))
        assert result.total_files == 0

    def test_roots_stored_correctly(self) -> None:
        result = DiffResult(left_root=Path("/left"), right_root=Path("/right"))
        assert result.left_root == Path("/left")
        assert result.right_root == Path("/right")

    def test_mutable_lists_are_independent_across_instances(self) -> None:
        r1 = DiffResult(left_root=Path("/a"), right_root=Path("/b"))
        r2 = DiffResult(left_root=Path("/c"), right_root=Path("/d"))
        r1.only_in_left.append(make_record())
        assert r2.only_in_left == []


# ---------------------------------------------------------------------------
# DupeGroup
# ---------------------------------------------------------------------------


class TestDupeGroup:
    def test_wasted_bytes_two_copies(self) -> None:
        files = [make_record(size=1024), make_record(path="/b/file.txt", size=1024)]
        group = DupeGroup(sha256="deadbeef", files=files)
        assert group.wasted_bytes == 1024

    def test_wasted_bytes_three_copies(self) -> None:
        files = [
            make_record(path="/a/f.txt", size=512),
            make_record(path="/b/f.txt", size=512),
            make_record(path="/c/f.txt", size=512),
        ]
        group = DupeGroup(sha256="deadbeef", files=files)
        assert group.wasted_bytes == 1024

    def test_wasted_bytes_single_file_is_zero(self) -> None:
        group = DupeGroup(sha256="deadbeef", files=[make_record(size=999)])
        assert group.wasted_bytes == 0

    def test_wasted_bytes_empty_group_is_zero(self) -> None:
        group = DupeGroup(sha256="deadbeef", files=[])
        assert group.wasted_bytes == 0

    def test_sha256_stored_correctly(self) -> None:
        group = DupeGroup(sha256="abc123", files=[])
        assert group.sha256 == "abc123"

    def test_mutable_lists_are_independent_across_instances(self) -> None:
        g1 = DupeGroup(sha256="aaa")
        g2 = DupeGroup(sha256="bbb")
        g1.files.append(make_record())
        assert g2.files == []
