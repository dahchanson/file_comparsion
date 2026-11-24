"""Filesystem type detection for I/O strategy selection.

Parses macOS ``mount`` and ``hdiutil`` output to identify the filesystem type,
locality, and backing topology of the mount containing a given path. System-wide
mount and disk-image tables are parsed at most once per process.

Implementation note: macOS ``stat -f "%T"`` returns the *file type* (e.g. ``/``
for directory) — NOT the filesystem type name. Parsing ``mount`` is the only
reliable approach without ctypes.

APFS note: macOS does not expose encryption status through mount(8). Both
encrypted and unencrypted APFS volumes report ``apfs`` as the filesystem type.
On internal NVMe, hardware AES-XTS encryption is inline; on attached encrypted
APFS disk images (``.dmg`` / ``.sparsebundle``), AES-XTS decryption runs on CPU
via ``apfs.kext`` and all block reads funnel through ``diskimagesiod``.
"""

import plistlib
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

# Captures device, mountpoint, and full flags string from mount output lines.
# Format:  <device> on </mount/point> (<flags>)
# Non-greedy /.+? stops at first " (" so mountpoints with spaces work.
_MOUNT_RE = re.compile(r"^(.+?) on (/.*?) \(([^)]+)\)")

# Extracts the whole-disk node (/dev/diskN) from a slice path (/dev/diskNsMsK).
_WHOLE_DISK_RE = re.compile(r"^(/dev/disk\d+)(?:s\d+)*$")

# Filesystem type names that indicate network-mounted, latency-bound I/O.
NETWORK_FS_TYPES: frozenset[str] = frozenset(
    {"smbfs", "nfs", "afpfs", "cifs", "webdavfs", "ftpfs"}
)

# Filesystem type names confirmed as local block-device mounts.
LOCAL_FS_TYPES: frozenset[str] = frozenset(
    {"apfs", "hfs", "exfat", "msdos", "ntfs", "udf"}
)


class StorageProfile(StrEnum):
    """I/O execution profile for a target filesystem."""

    LOCAL = "local"
    NETWORK_FS = "network_fs"
    NETWORK_DISK_IMAGE = "network_disk_image"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _MountEntry:
    mountpoint: str
    device: str
    fstype: str
    is_local: bool


@dataclass(frozen=True, slots=True)
class _DiskImageEntry:
    image_path: str
    whole_disks: frozenset[str]
    mountpoints: frozenset[str]


def _whole_disk(dev: str) -> str:
    """Return ``/dev/diskN`` for a ``/dev/diskNsM`` device path, or ``""``."""
    m = _WHOLE_DISK_RE.match(dev)
    return m.group(1) if m else ""


@lru_cache(maxsize=1)
def _parsed_mounts() -> tuple[_MountEntry, ...]:
    """Parse ``mount`` once and return entries ordered longest-mountpoint first."""
    try:
        result = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=2
        )
        entries: list[_MountEntry] = []
        for line in result.stdout.splitlines():
            m = _MOUNT_RE.match(line)
            if not m:
                continue
            device = m.group(1).strip()
            mountpoint = m.group(2)
            flags = [f.strip().lower() for f in m.group(3).split(",")]
            fstype = flags[0] if flags else "unknown"
            is_local = "local" in flags
            entries.append(
                _MountEntry(
                    mountpoint=mountpoint,
                    device=device,
                    fstype=fstype,
                    is_local=is_local,
                )
            )
        entries.sort(key=lambda e: len(e.mountpoint), reverse=True)
        return tuple(entries)
    except Exception:
        return ()


@lru_cache(maxsize=1)
def _parsed_disk_images() -> tuple[_DiskImageEntry, ...]:
    """Parse ``hdiutil info -plist`` once into attached disk-image descriptors."""
    try:
        hdi_result = subprocess.run(
            ["hdiutil", "info", "-plist"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if hdi_result.returncode != 0:
            return ()
        info: dict[str, Any] = plistlib.loads(hdi_result.stdout.encode())
        out: list[_DiskImageEntry] = []
        for image in info.get("images", []):
            image_path = image.get("image-path", "")
            if not image_path:
                continue
            disks: set[str] = set()
            mounts: set[str] = set()
            for entity in image.get("system-entities", []):
                dev = entity.get("dev-entry", "")
                wd = _whole_disk(dev)
                if wd:
                    disks.add(wd)
                mp = entity.get("mount-point", "")
                if mp:
                    mounts.add(mp)
            if disks or mounts:
                out.append(
                    _DiskImageEntry(
                        image_path=image_path,
                        whole_disks=frozenset(disks),
                        mountpoints=frozenset(mounts),
                    )
                )
        return tuple(out)
    except Exception:
        return ()


def _find_mount_entry(path: Path) -> _MountEntry | None:
    """Return the most specific ``_MountEntry`` covering ``path``."""
    try:
        resolved = str(path.resolve())
    except Exception:
        return None
    for entry in _parsed_mounts():
        if _is_under(resolved, entry.mountpoint):
            return entry
    return None


class _MountInfoLookup:
    """Cached ``(fstype, is_local)`` lookup with unified cache clearing."""

    def __init__(self) -> None:
        self._dir_cache: dict[Path, tuple[str, bool]] = {}

    def __call__(self, path: Path) -> tuple[str, bool]:
        cached = self._dir_cache.get(path)
        if cached is not None:
            return cached
        entry = _find_mount_entry(path)
        res = (entry.fstype, entry.is_local) if entry is not None else ("unknown", False)
        if len(self._dir_cache) >= 512:
            self._dir_cache.clear()
        self._dir_cache[path] = res
        return res

    def cache_clear(self) -> None:
        self._dir_cache.clear()
        _parsed_mounts.cache_clear()
        _parsed_disk_images.cache_clear()
        _is_disk_image_on_network.cache_clear()


class _DiskImageNetworkLookup:
    """Cached check for whether ``path`` resides on a network-backed disk image."""

    def __init__(self) -> None:
        self._cache: dict[Path, bool] = {}

    def __call__(self, path: Path) -> bool:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        res = self._compute(path)
        if len(self._cache) >= 512:
            self._cache.clear()
        self._cache[path] = res
        return res

    def _compute(self, path: Path) -> bool:
        entry = _find_mount_entry(path)
        if entry is None or not entry.device.startswith("/dev/"):
            return False
        # The root OS volume is never an attached network disk image.
        if entry.mountpoint == "/":
            return False

        target_disk = _whole_disk(entry.device)
        for img in _parsed_disk_images():
            if entry.mountpoint in img.mountpoints or (
                target_disk and target_disk in img.whole_disks
            ):
                return is_network_fs(Path(img.image_path))
        return False

    def cache_clear(self) -> None:
        self._cache.clear()
        _parsed_mounts.cache_clear()
        _parsed_disk_images.cache_clear()


_mount_info = _MountInfoLookup()
_is_disk_image_on_network = _DiskImageNetworkLookup()


def fs_type(path: Path) -> str:
    """Return the lowercase filesystem type name for the mount containing path."""
    return _mount_info(path)[0]


def storage_profile(path: Path) -> StorageProfile:
    """Classify ``path`` into a ``StorageProfile`` for worker and I/O tuning.

    Distinguishes direct network shares (``NETWORK_FS`` — high per-file open/close
    latency, benefits from 32 workers) from network-backed APFS disk images
    (``NETWORK_DISK_IMAGE`` — encrypted/unencrypted ``.dmg`` or ``.sparsebundle``
    over SMBfs where reads funnel through ``diskimagesiod`` and benefit from
    moderate concurrency and sequential inode ordering).
    """
    fstype, is_local = _mount_info(path)
    if fstype in NETWORK_FS_TYPES or (fstype == "apfs" and not is_local):
        return StorageProfile.NETWORK_FS
    if fstype == "apfs" and is_local and _is_disk_image_on_network(path):
        return StorageProfile.NETWORK_DISK_IMAGE
    if fstype in LOCAL_FS_TYPES and is_local:
        return StorageProfile.LOCAL
    return StorageProfile.UNKNOWN


def is_network_fs(path: Path) -> bool:
    """Return True if path resides on a network-mounted or network-backed filesystem."""
    return storage_profile(path) in (
        StorageProfile.NETWORK_FS,
        StorageProfile.NETWORK_DISK_IMAGE,
    )


def is_local_fs(path: Path) -> bool:
    """Return True if path resides on a known local block-device filesystem."""
    fstype, is_local = _mount_info(path)
    return fstype in LOCAL_FS_TYPES and is_local


def is_mmap_safe(path: Path) -> bool:
    """Return True if memory-mapped I/O is safe and efficient for this path."""
    return storage_profile(path) == StorageProfile.LOCAL


def _is_under(path_str: str, mountpoint: str) -> bool:
    """Return True if path_str is at or below mountpoint."""
    if mountpoint == "/":
        return True
    return path_str == mountpoint or path_str.startswith(mountpoint + "/")
