"""Recursive directory walker that yields FileRecord objects."""

import fnmatch
import os
import re
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fcompare.engine.models import FileRecord


@dataclass(slots=True)
class WalkOptions:
    """Controls which files the walker includes or skips."""

    recurse: bool = True
    follow_symlinks: bool = False
    include_hidden: bool = False
    exclude_patterns: list[str] = field(default_factory=list)


def walk(root: Path, options: WalkOptions | None = None) -> Iterator[FileRecord]:
    """Yield a FileRecord for every file under ``root`` that passes all filters.

    Uses an iterative ``os.scandir`` traversal so:
      - Hidden and excluded directories (e.g. ``.git``, ``.Spotlight-V100``,
        ``node_modules``) are pruned *before* descending into them, avoiding
        unnecessary catalog B-tree faults on SMB-backed APFS volumes.
      - Entry types come from ``readdir`` ``d_type`` without extra ``lstat``/``stat``
        syscalls, requiring only a single ``DirEntry.stat()`` per yielded file.

    Files or directories that raise OSError (including PermissionError, network
    timeouts, and disconnected SMBfs mounts) are skipped with a warning.

    Args:
        root: Directory to walk. Must exist.
        options: Filtering configuration. Defaults to WalkOptions().
    """
    if options is None:
        options = WalkOptions()

    exclude_re = _compile_excludes(options.exclude_patterns)
    include_hidden = options.include_hidden
    follow_symlinks = options.follow_symlinks
    recurse = options.recurse

    root_str = os.fspath(root)
    prefix_len = len(root_str) if root_str.endswith(os.sep) else len(root_str) + 1

    visited_dirs: set[tuple[int, int]] | None = None
    if follow_symlinks and recurse:
        try:
            st_root = os.stat(root_str)
            visited_dirs = {(st_root.st_dev, st_root.st_ino)}
        except OSError:
            visited_dirs = set()

    stack: list[str] = [root_str]

    while stack:
        cur_dir = stack.pop()
        try:
            sc_it = os.scandir(cur_dir)
        except OSError:
            warnings.warn(f"Cannot access directory, skipping: {cur_dir}", stacklevel=2)
            continue

        with sc_it:
            for entry in sc_it:
                name = entry.name

                # Fast name check before any stat/lstat syscall: prunes hidden files
                # AND hidden directories (.git, .Spotlight-V100, .Trashes, ._*).
                if not include_hidden and name.startswith("."):
                    continue

                entry_path = entry.path
                rel_str = entry_path[prefix_len:]

                # Prune excluded files AND excluded directories before descending.
                if exclude_re is not None and (
                    exclude_re.match(name)
                    or exclude_re.match(rel_str)
                    or exclude_re.match(entry_path)
                ):
                    continue

                try:
                    if entry.is_symlink() and not follow_symlinks:
                        continue

                    if entry.is_dir(follow_symlinks=follow_symlinks):
                        if recurse:
                            if visited_dirs is not None:
                                dir_st = entry.stat(follow_symlinks=True)
                                dev_ino = (dir_st.st_dev, dir_st.st_ino)
                                if dev_ino in visited_dirs:
                                    continue
                                visited_dirs.add(dev_ino)
                            stack.append(entry_path)
                        continue

                    if not entry.is_file(follow_symlinks=follow_symlinks):
                        continue

                    stat = entry.stat(follow_symlinks=follow_symlinks)
                except OSError:
                    warnings.warn(f"Cannot access, skipping: {entry_path}", stacklevel=2)
                    continue

                yield FileRecord(
                    path=Path(entry_path),
                    rel_path=Path(rel_str),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    ino=stat.st_ino,
                )


def _compile_excludes(patterns: list[str]) -> re.Pattern[str] | None:
    """Compile glob patterns into a single regex, or None if empty."""
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in patterns))
