"""Unit tests for engine/hasher.py."""

import hashlib
import warnings
from pathlib import Path

import pytest

from fcompare.engine.hasher import CHUNK_SIZE, hash_file, hash_records
from fcompare.engine.models import FileRecord


def make_record(path: Path, size: int = 0) -> FileRecord:
    return FileRecord(path=path, rel_path=path.name, size=size, mtime=0.0)  # type: ignore[arg-type]


def expected_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------


class TestHashFile:
    def test_returns_correct_sha256_for_known_content(self, tmp_path: Path) -> None:
        content = b"hello fcompare"
        f = tmp_path / "f.txt"
        f.write_bytes(content)
        assert hash_file(f) == expected_sha256(content)

    def test_returns_lowercase_hex(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"abc")
        result = hash_file(f)
        assert result == result.lower()
        assert all(c in "0123456789abcdef" for c in result)

    def test_digest_length_is_64_hex_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"data")
        assert len(hash_file(f)) == 64

    def test_consistent_across_repeated_calls(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"consistent content")
        assert hash_file(f) == hash_file(f)

    def test_different_content_produces_different_digest(self, tmp_path: Path) -> None:
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert hash_file(f1) != hash_file(f2)

    def test_empty_file_returns_known_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        # SHA-256 of empty input is well-known.
        assert hash_file(f) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_large_file_streamed_correctly(self, tmp_path: Path) -> None:
        # 5 MB — well above CHUNK_SIZE, exercises multi-chunk streaming.
        content = b"x" * (5 * 1024 * 1024)
        f = tmp_path / "large.bin"
        f.write_bytes(content)
        assert hash_file(f) == expected_sha256(content)

    def test_file_exactly_one_chunk_size(self, tmp_path: Path) -> None:
        content = b"z" * CHUNK_SIZE
        f = tmp_path / "exact.bin"
        f.write_bytes(content)
        assert hash_file(f) == expected_sha256(content)

    def test_raises_oserror_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            hash_file(tmp_path / "nonexistent.txt")


# ---------------------------------------------------------------------------
# hash_records
# ---------------------------------------------------------------------------


class TestHashRecords:
    def test_populates_sha256_on_all_records(self, tmp_path: Path) -> None:
        files = [(tmp_path / f"f{i}.txt") for i in range(5)]
        for f in files:
            f.write_bytes(f.name.encode())
        records = [make_record(f) for f in files]

        result = hash_records(records)

        assert len(result) == 5
        for r in result:
            assert len(r.sha256) == 64
            assert r.sha256 == expected_sha256(r.path.name.encode())

    def test_does_not_mutate_original_records(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"data")
        original = make_record(f)
        assert original.sha256 == ""

        hash_records([original])

        assert original.sha256 == ""  # original untouched

    def test_returns_empty_list_for_empty_input(self) -> None:
        assert hash_records([]) == []

    def test_skips_unreadable_file_and_emits_warning(self, tmp_path: Path) -> None:
        good = tmp_path / "good.txt"
        good.write_bytes(b"good")
        bad = tmp_path / "bad.txt"
        bad.write_bytes(b"bad")

        good_record = make_record(good)
        bad_record = make_record(bad)

        # Make the bad file unreadable by patching hash_file to raise for it.
        original_hash_file = __import__(
            "fcompare.engine.hasher", fromlist=["hash_file"]
        ).hash_file

        import fcompare.engine.hasher as hasher_mod

        def patched(path: Path) -> str:
            if path == bad:
                raise OSError("permission denied")
            return original_hash_file(path)

        hasher_mod.hash_file = patched
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = hash_records([good_record, bad_record])
        finally:
            hasher_mod.hash_file = original_hash_file

        result_paths = {r.path for r in result}
        assert good in result_paths
        assert bad not in result_paths
        assert any("bad.txt" in str(w.message) for w in caught)

    def test_respects_max_workers_parameter(self, tmp_path: Path) -> None:
        files = [tmp_path / f"f{i}.txt" for i in range(4)]
        for f in files:
            f.write_bytes(b"x")
        records = [make_record(f) for f in files]
        # max_workers=1 forces serial execution — must still produce correct results.
        result = hash_records(records, max_workers=1)
        assert len(result) == 4
        assert all(r.sha256 == expected_sha256(b"x") for r in result)

    def test_identical_content_produces_identical_digests(self, tmp_path: Path) -> None:
        content = b"same content"
        files = [tmp_path / f"f{i}.txt" for i in range(3)]
        for f in files:
            f.write_bytes(content)
        records = [make_record(f) for f in files]

        result = hash_records(records)
        digests = {r.sha256 for r in result}
        assert len(digests) == 1
        assert digests.pop() == expected_sha256(content)
