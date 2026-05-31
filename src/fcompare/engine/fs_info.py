"""Filesystem type detection for I/O strategy selection.

Uses macOS's BSD `stat -f "%T" <path>` to identify the filesystem type
of a mount point. Results are cached per resolved path to avoid repeated
subprocess invocations when operating on files from the same tree.

This module is macOS-specific. The BSD `stat -f` format flag is not
available on GNU coreutils (Linux). Since fcompare targets macOS, this
is intentional.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

# Filesystem type names that indicate network-mounted, latency-bound I/O.
# Hashing on these requires more concurrent workers to hide round-trip latency.
NETWORK_FS_TYPES: frozenset[str] = frozenset(
    {"smbfs", "nfs", "afpfs", "cifs", "webdavfs", "ftpfs"}
)


@lru_cache(maxsize=64)
def fs_type(path: Path) -> str:
    """Return the lowercase filesystem type name for the mount containing path.

    Returns ``"unknown"`` if the type cannot be determined (e.g., subprocess
    failure, timeout, or unsupported platform).

    Results are cached so repeated calls for files in the same directory tree
    incur only one subprocess invocation.
    """
    try:
        result = subprocess.run(
            ["stat", "-f", "%T", str(path.resolve())],
            capture_output=True,
            text=True,
            timeout=2,
        )
        name = result.stdout.strip().lower()
        return name if name else "unknown"
    except Exception:
        return "unknown"


def is_network_fs(path: Path) -> bool:
    """Return True if path resides on a network-mounted filesystem.

    Network filesystems have high per-operation latency and benefit from
    significantly more concurrent reader threads than local NVMe storage.
    """
    return fs_type(path) in NETWORK_FS_TYPES
