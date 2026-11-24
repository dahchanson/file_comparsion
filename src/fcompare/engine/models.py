"""Core data models shared across all fcompare subcommands."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FileRecord:
    """Represents a single file discovered during a directory walk.

    ``sha256`` is empty string until the hasher populates it.
    ``rel_path`` is relative to the walk root, used as the key for cross-directory comparison.
    ``ino`` is the filesystem inode number (0 when unknown), used to preserve physical
    allocation order on network-backed APFS disk images.
    """

    path: Path
    rel_path: Path
    size: int
    mtime: float
    sha256: str = field(default="")
    ino: int = field(default=0)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileRecord):
            return NotImplemented
        return self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)


@dataclass(slots=True)
class DiffResult:
    """Result produced by the ``diff`` command comparing two directory trees."""

    left_root: Path
    right_root: Path
    only_in_left: list[FileRecord] = field(default_factory=list)
    only_in_right: list[FileRecord] = field(default_factory=list)
    # Each tuple is (left_record, right_record) for the same relative path.
    modified: list[tuple[FileRecord, FileRecord]] = field(default_factory=list)
    identical: list[tuple[FileRecord, FileRecord]] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        """Total unique relative paths considered across both sides."""
        return (
            len(self.only_in_left)
            + len(self.only_in_right)
            + len(self.modified)
            + len(self.identical)
        )


@dataclass(slots=True)
class DupeGroup:
    """A set of files that share an identical SHA-256 digest.

    Always contains at least two members — single-file groups are never returned
    by the duplicate finder.
    """

    sha256: str
    files: list[FileRecord] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        """Bytes consumed by redundant copies (all copies minus one canonical copy)."""
        if not self.files:
            return 0
        return self.files[0].size * (len(self.files) - 1)
