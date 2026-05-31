"""Filesystem type detection for I/O strategy selection.

Parses macOS ``mount`` output to identify the filesystem type and locality of
the mount containing a given path. Results are cached per resolved path.

Implementation note: macOS ``stat -f "%T"`` returns the *file type* (e.g. ``/``
for directory) — NOT the filesystem type name. Parsing ``mount`` is the only
reliable approach without ctypes.

APFS note: macOS does not expose encryption status through mount(8). Both
encrypted and unencrypted APFS volumes report ``apfs`` as the filesystem type.
Hardware AES-XTS encryption (T2/Secure Enclave) is transparent; I/O behaviour
is identical. Locality is determined by the ``local`` flag in mount output:
  - local APFS (NVMe, USB, Thunderbolt): ``(apfs, local, ...)``   → local
  - network-backed APFS (iSCSI, etc.):   ``(apfs, ...)`` no local → network
"""

import re
import subprocess
from functools import lru_cache
from pathlib import Path

# Captures mountpoint and full flags string from mount output lines.
# Format:  <device> on </mount/point> (<flags>)
# Non-greedy /.+? stops at first " (" so mountpoints with spaces work.
_MOUNT_RE = re.compile(r".+ on (/.*?) \(([^)]+)\)")

# Filesystem type names that indicate network-mounted, latency-bound I/O.
# Hashing on these requires more concurrent workers to hide round-trip latency.
NETWORK_FS_TYPES: frozenset[str] = frozenset(
    {"smbfs", "nfs", "afpfs", "cifs", "webdavfs", "ftpfs"}
)

# Filesystem type names confirmed as local block-device mounts.
# mmap() is safe and efficient on all of these when the "local" flag is present.
#
# "apfs"  — Apple File System, encrypted or unencrypted (both report "apfs").
#            Only safe for local I/O when mount output includes the "local" flag.
# "hfs"   — HFS+, legacy macOS volumes and some external drives.
# "exfat" — Common on FAT-formatted USB/SD drives shared with Windows.
# "msdos" — FAT32, older external media.
# "ntfs"  — Read-only NTFS mounts via macOS's built-in driver.
# "udf"   — Optical discs and some USB drives.
LOCAL_FS_TYPES: frozenset[str] = frozenset(
    {"apfs", "hfs", "exfat", "msdos", "ntfs", "udf"}
)


@lru_cache(maxsize=64)
def _mount_info(path: Path) -> tuple[str, bool]:
    """Return ``(fstype, is_local)`` for the mount containing path.

    ``is_local`` is True when the mount output includes the ``local`` flag,
    indicating a block-device-backed mount rather than a network mount.

    Returns ``("unknown", False)`` if the mount cannot be determined.
    """
    try:
        resolved = str(path.resolve())
        result = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=2
        )
        best_len = 0
        best_type = "unknown"
        best_local = False
        for line in result.stdout.splitlines():
            m = _MOUNT_RE.match(line)
            if not m:
                continue
            mountpoint = m.group(1)
            flags = [f.strip().lower() for f in m.group(2).split(",")]
            fstype = flags[0] if flags else "unknown"
            is_local = "local" in flags
            if _is_under(resolved, mountpoint) and len(mountpoint) > best_len:
                best_len = len(mountpoint)
                best_type = fstype
                best_local = is_local
        return best_type, best_local
    except Exception:
        return "unknown", False


def fs_type(path: Path) -> str:
    """Return the lowercase filesystem type name for the mount containing path.

    Returns ``"unknown"`` if the type cannot be determined.
    Results are cached per resolved path.
    """
    return _mount_info(path)[0]


def is_network_fs(path: Path) -> bool:
    """Return True if path resides on a network-mounted filesystem.

    Catches three cases:
    1. Explicit network filesystem types (smbfs, nfs, afpfs, cifs, …).
    2. APFS without the ``local`` flag — network-backed block storage (iSCSI).
    3. APFS disk images (``.dmg``) whose backing file is on a network mount —
       e.g. a ``.dmg`` stored on an SMBfs share and attached with hdiutil.
       These appear as ``(apfs, local, …)`` in mount output but all I/O
       ultimately traverses the network share.

    Network filesystems benefit from more concurrent reader threads to hide
    per-operation round-trip latency.
    """
    fstype, is_local = _mount_info(path)
    if fstype in NETWORK_FS_TYPES:
        return True
    # APFS without the "local" flag indicates network-backed block storage.
    if fstype == "apfs" and not is_local:
        return True
    # APFS that IS "local" might still be a disk image on a network share.
    return bool(fstype == "apfs" and is_local and _is_disk_image_on_network(path))


def is_local_fs(path: Path) -> bool:
    """Return True if path resides on a known local block-device filesystem.

    Returns False for network mounts, for APFS lacking the ``local`` flag,
    and for filesystems whose type cannot be determined.
    """
    fstype, is_local = _mount_info(path)
    return fstype in LOCAL_FS_TYPES and is_local


def is_mmap_safe(path: Path) -> bool:
    """Return True if memory-mapped I/O is safe and efficient for this path.

    mmap() raises OSError on network-mounted filesystems (SMBfs, NFS, AFP)
    and on APFS volumes without the ``local`` flag. It is also unsafe for
    APFS disk images backed by a network share — the block device appears
    local but all I/O traverses the network, and a dropped connection
    during a mapped read produces an OSError mid-hash.

    Safe for: local APFS (encrypted or unencrypted), HFS+, exFAT, MSDOS,
    NTFS, UDF — all with the ``local`` flag present and no network backing.
    """
    return is_local_fs(path) and not is_network_fs(path)


@lru_cache(maxsize=64)
def _is_disk_image_on_network(path: Path) -> bool:
    """Return True if path is on an APFS disk image backed by a network share.

    Uses ``hdiutil info -plist`` to enumerate attached disk images and find the
    one whose device node matches the mount containing path. If the image file
    (``.dmg``) itself lives on a network filesystem, returns True.

    Example: /Volumes/archive10 is APFS (local) but its backing file is
    /Volumes/chad-data/archive10.dmg, which is on an SMBfs mount — so all I/O
    ultimately traverses the network.

    Returns False on any error or if the mount has no disk image backing.
    """
    import plistlib

    try:
        # Find the device node for the mount containing path.
        resolved = str(path.resolve())
        mount_result = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=2
        )
        device = ""
        best_len = 0
        for line in mount_result.stdout.splitlines():
            # Extract device from "device on /mountpoint (..."
            dm = re.match(r"^(/dev/\S+) on (/.+?) \(", line)
            if not dm:
                continue
            mountpoint = dm.group(2)
            if _is_under(resolved, mountpoint) and len(mountpoint) > best_len:
                best_len = len(mountpoint)
                device = dm.group(1)

        if not device:
            return False

        # Ask hdiutil for all attached images and find the one using that device.
        hdi_result = subprocess.run(
            ["hdiutil", "info", "-plist"],
            capture_output=True, text=True, timeout=5,
        )
        if hdi_result.returncode != 0:
            return False

        info = plistlib.loads(hdi_result.stdout.encode())
        for image in info.get("images", []):
            image_path = image.get("image-path", "")
            for entity in image.get("system-entities", []):
                dev = entity.get("dev-entry", "")
                # This image is backing our device — check if image-path is network.
                if dev.startswith(device.rstrip("s0123456789")) and image_path:
                    return is_network_fs(Path(image_path))
        return False
    except Exception:
        return False


def _is_under(path_str: str, mountpoint: str) -> bool:
    """Return True if path_str is at or below mountpoint."""
    if mountpoint == "/":
        return True
    return path_str == mountpoint or path_str.startswith(mountpoint + "/")
