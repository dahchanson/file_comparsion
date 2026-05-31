"""Recursive directory walker that yields FileRecord objects."""

import fnmatch
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fcompare.engine.models import FileRecord


@dataclass
class WalkOptions:
    """Controls which files the walker includes or skips."""

    recurse: bool = True
    follow_symlinks: bool = False
    include_hidden: bool = False
    exclude_patterns: list[str] = field(default_factory=list)


def walk(root: Path, options: WalkOptions | None = None) -> Iterator[FileRecord]:
    """Yield a FileRecord for every file under ``root`` that passes all filters.

    Files that raise PermissionError are skipped with a warning. Directories
    are never yielded — only regular files (or symlinks to files when
    follow_symlinks is True).

    Args:
        root: Directory to walk. Must exist.
        options: Filtering configuration. Defaults to WalkOptions().
    """
    if options is None:
        options = WalkOptions()

    candidates = root.rglob("*") if options.recurse else root.iterdir()

    for entry in candidates:
        # Resolve symlink status before any other check.
        is_symlink = entry.is_symlink()
        if is_symlink and not options.follow_symlinks:
            continue

        # Only yield regular files (or symlinks-to-files when allowed).
        try:
            if not entry.is_file():
                continue
        except PermissionError:
            warnings.warn(f"Permission denied, skipping: {entry}", stacklevel=2)
            continue

        # Skip hidden files/directories (any path component starting with ".").
        if not options.include_hidden and _has_hidden_component(entry, root):
            continue

        # Skip entries that match any exclude glob pattern.
        if _matches_any_exclude(entry, options.exclude_patterns):
            continue

        try:
            stat = entry.stat(follow_symlinks=options.follow_symlinks)
        except PermissionError:
            warnings.warn(f"Permission denied reading stat, skipping: {entry}", stacklevel=2)
            continue

        yield FileRecord(
            path=entry,
            rel_path=entry.relative_to(root),
            size=stat.st_size,
            mtime=stat.st_mtime,
        )


def _has_hidden_component(path: Path, root: Path) -> bool:
    """Return True if any component of path (relative to root) starts with '.'."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def _matches_any_exclude(path: Path, patterns: list[str]) -> bool:
    """Return True if the file name or any path component matches an exclude pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(path.name, pattern):
            return True
        # Also allow matching against full relative-path string for patterns like "dir/*".
        if fnmatch.fnmatch(str(path), pattern):
            return True
    return False
