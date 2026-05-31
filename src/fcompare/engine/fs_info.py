"""Filesystem type detection for I/O strategy selection.

Uses macOS's BSD `stat -f "%T" <path>` to identify the filesystem type
of a mount point. Results are cached per resolved path to avoid repeated
subprocess invocations when operating on files from the same tree.

This module is macOS-specific. The BSD `stat -f` format flag is not
available on GNU coreutils (Linux). Since fcompare targets macOS, this
is intentional.

APFS note: macOS does not expose encryption status through statfs. Both
encrypted and unencrypted APFS volumes report the filesystem type as "apfs".
Encryption is handled transparently at the storage layer by the APFS volume
manager; from the OS perspective the I/O characteristics are identical.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

# Filesystem type names that indicate network-mounted, latency-bound I/O.
# Hashing on these requires more concurrent workers to hide round-trip latency.
NETWORK_FS_TYPES: frozenset[str] = frozenset(
    {"smbfs", "nfs", "afpfs", "cifs", "webdavfs", "ftpfs"}
)

# Filesystem type names confirmed as local block-device mounts.
# mmap() is safe and efficient on all of these.
#
# "apfs"  — Apple File System, encrypted or unencrypted (both report "apfs").
#            Hardware AES-XTS encryption via the T2/Secure Enclave is
#            transparent to the OS; I/O behaviour is identical to unencrypted.
# "hfs"   — HFS+, legacy macOS volumes and some external drives.
# "exfat" — Common on FAT-formatted USB/SD drives shared with Windows.
# "msdos" — FAT32, older external media.
# "ntfs"  — Read-only NTFS mounts via macOS's built-in driver.
# "udf"   — Optical discs and some USB drives.
LOCAL_FS_TYPES: frozenset[str] = frozenset(
    {"apfs", "hfs", "exfat", "msdos", "ntfs", "udf"}
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


def is_local_fs(path: Path) -> bool:
    """Return True if path resides on a known local block-device filesystem.

    Returns False for network mounts and for filesystems whose type cannot
    be determined (``"unknown"``).
    """
    return fs_type(path) in LOCAL_FS_TYPES


def is_mmap_safe(path: Path) -> bool:
    """Return True if memory-mapped I/O is safe and efficient for this path.

    mmap() raises OSError on network-mounted filesystems (SMBfs, NFS, AFP).
    It is safe and beneficial on all entries in LOCAL_FS_TYPES, including
    both encrypted and unencrypted APFS volumes.
    """
    return is_local_fs(path)
