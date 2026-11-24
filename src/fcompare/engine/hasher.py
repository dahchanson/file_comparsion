"""Parallel SHA-256 hashing and partial pre-hashing of FileRecord collections."""

import hashlib
import os
import threading
import warnings
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from queue import SimpleQueue

from fcompare.engine.fs_info import StorageProfile, storage_profile
from fcompare.engine.models import FileRecord

# 1 MB — optimal for NVMe on Apple Silicon and matches SMB3 MaxReadSize / APFS clusters.
CHUNK_SIZE: int = 1 * 1024 * 1024

# Direct network filesystems (SMBfs, NFS, AFP) have high per-file open/close RTTs;
# 32 concurrent workers hide metadata latency across small files.
_NETWORK_WORKER_COUNT: int = 32

# Network-backed APFS disk images (.dmg / .sparsebundle over SMBfs) funnel all reads
# through diskimagesiod into a single backing file or 8 MB bands. Moderate concurrency
# saturates SMB3 read pipelining + CPU AES-XTS decryption without 32-way random-seek
# thrashing on NAS storage.
_DISK_IMAGE_NETWORK_WORKER_COUNT: int = 6

# Partial pre-hash parameters for eliminating same-size non-duplicates in dupes:
# 64 KB head + 64 KB tail (4 KB-block-aligned for encrypted APFS extents).
_PREHASH_WINDOW_BYTES: int = 64 * 1024
PREHASH_MIN_SIZE: int = 256 * 1024
_APFS_BLOCK_ALIGN: int = 4096

_thread_local = threading.local()


def _get_read_buffer(size: int) -> tuple[bytearray, memoryview]:
    """Return a reusable per-thread buffer of ``size`` bytes (zero heap churn)."""
    attr = f"buf_{size}"
    cached: tuple[bytearray, memoryview] | None = getattr(_thread_local, attr, None)
    if cached is None or len(cached[0]) != size:
        raw = bytearray(size)
        view = memoryview(raw)
        setattr(_thread_local, attr, (raw, view))
        return raw, view
    return cached


def hash_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of the file at ``path``.

    Streams unbuffered reads into a reusable thread-local 1 MB buffer via
    ``readinto()`` — avoiding both Python ``BufferedReader`` double-copying and
    per-chunk 1 MB heap allocations. Raises OSError on any read failure.
    """
    digest = hashlib.sha256()
    raw, view = _get_read_buffer(CHUNK_SIZE)
    with path.open("rb", buffering=0) as fh:
        while n := fh.readinto(raw):
            digest.update(view[:n])
    return digest.hexdigest()


def partial_hash_file(path: Path, size: int) -> str:
    """Return a fast head+tail SHA-256 fingerprint for pre-filtering duplicates.

    Reads the first 64 KB and (when ``size > 128 KB``) the final ~64 KB aligned
    to a 4 KB APFS encryption block boundary.
    """
    digest = hashlib.sha256(size.to_bytes(8, "little", signed=False))
    raw, view = _get_read_buffer(_PREHASH_WINDOW_BYTES)
    with path.open("rb", buffering=0) as fh:
        n = fh.readinto(raw)
        if n:
            digest.update(view[:n])
        if size > _PREHASH_WINDOW_BYTES * 2:
            tail_offset = max(
                _PREHASH_WINDOW_BYTES,
                ((size - _PREHASH_WINDOW_BYTES) // _APFS_BLOCK_ALIGN) * _APFS_BLOCK_ALIGN,
            )
            fh.seek(tail_offset, os.SEEK_SET)
            while n := fh.readinto(raw):
                digest.update(view[:n])
    return digest.hexdigest()


def _plan_execution(
    records: list[FileRecord],
    max_workers: int | None,
) -> tuple[int, list[FileRecord]]:
    """Choose worker count and optimal file read order for ``records``."""
    profile = storage_profile(records[0].path)

    if max_workers is None:
        if profile == StorageProfile.NETWORK_FS:
            workers = min(len(records), _NETWORK_WORKER_COUNT)
        elif profile == StorageProfile.NETWORK_DISK_IMAGE:
            workers = min(len(records), _DISK_IMAGE_NETWORK_WORKER_COUNT)
        else:
            workers = os.cpu_count() or 1
    else:
        workers = max_workers

    if len(records) <= 1:
        return workers, records

    if profile == StorageProfile.NETWORK_DISK_IMAGE:
        # Sort by APFS inode ascending to approximate physical extent / sparsebundle
        # band allocation order, preserving sequential NAS read-ahead.
        ordered = sorted(records, key=lambda r: (r.ino == 0, r.ino, str(r.path)))
    else:
        # Longest Processing Time first (LPT): start largest files immediately so
        # no single multi-GB file straggles on 1 core at the end of the batch.
        ordered = sorted(records, key=lambda r: r.size, reverse=True)

    return workers, ordered


def hash_records(
    records: list[FileRecord],
    max_workers: int | None = None,
    on_file_hashed: Callable[[FileRecord], None] | None = None,
) -> list[FileRecord]:
    """Return a new list of FileRecords with the ``sha256`` field populated.

    Spawns ``workers`` persistent consumer tasks pulling from an atomic deque and
    streaming results over a C-backed ``SimpleQueue`` — allocating only O(workers)
    Future objects instead of O(files) and avoiding per-file Condition lock contention.
    """
    if not records:
        return []

    workers, ordered = _plan_execution(records, max_workers)
    work_deque: deque[FileRecord] = deque(ordered)
    results_q: SimpleQueue[tuple[FileRecord, str | None, OSError | None]] = SimpleQueue()
    num_tasks = min(workers, len(ordered))

    def _worker() -> None:
        while True:
            try:
                rec = work_deque.popleft()
            except IndexError:
                return
            try:
                digest = hash_file(rec.path)
                results_q.put((rec, digest, None))
            except OSError as exc:
                results_q.put((rec, None, exc))

    hashed: list[FileRecord] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker) for _ in range(num_tasks)]
        for _ in range(len(ordered)):
            record, digest, err = results_q.get()
            if err is not None or digest is None:
                warnings.warn(
                    f"Could not read file for hashing, skipping: {record.path} ({err})",
                    stacklevel=2,
                )
                continue
            hashed_record = replace(record, sha256=digest)
            hashed.append(hashed_record)
            if on_file_hashed is not None:
                on_file_hashed(hashed_record)
        for f in futures:
            f.result()

    return hashed


def prehash_records(
    records: list[FileRecord],
    max_workers: int | None = None,
) -> list[tuple[FileRecord, str]]:
    """Compute partial head+tail digests in parallel for candidate duplicate records."""
    if not records:
        return []

    workers, ordered = _plan_execution(records, max_workers)
    work_deque: deque[FileRecord] = deque(ordered)
    results_q: SimpleQueue[tuple[FileRecord, str | None, OSError | None]] = SimpleQueue()
    num_tasks = min(workers, len(ordered))

    def _worker() -> None:
        while True:
            try:
                rec = work_deque.popleft()
            except IndexError:
                return
            try:
                digest = partial_hash_file(rec.path, rec.size)
                results_q.put((rec, digest, None))
            except OSError as exc:
                results_q.put((rec, None, exc))

    results: list[tuple[FileRecord, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker) for _ in range(num_tasks)]
        for _ in range(len(ordered)):
            record, digest, err = results_q.get()
            if err is not None or digest is None:
                warnings.warn(
                    f"Could not read file for hashing, skipping: {record.path} ({err})",
                    stacklevel=2,
                )
                continue
            results.append((record, digest))
        for f in futures:
            f.result()

    return results
