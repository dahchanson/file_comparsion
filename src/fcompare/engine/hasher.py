"""Parallel SHA-256 hashing of FileRecord collections."""

import hashlib
import os
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from fcompare.engine.fs_info import is_network_fs
from fcompare.engine.models import FileRecord

# 1 MB — optimal for NVMe on Apple Silicon; reduces syscall overhead vs 64 KB.
CHUNK_SIZE: int = 1 * 1024 * 1024

# Network-mounted filesystems are latency-bound rather than compute-bound.
# More concurrent workers hide per-operation round-trip time and saturate
# the link. Capped to avoid overwhelming SMB servers with connection limits.
_NETWORK_WORKER_COUNT: int = 32


def hash_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of the file at ``path``.

    Streams the file in CHUNK_SIZE chunks — never holds the full contents
    in memory. Raises OSError on any read failure; callers must handle it.
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def hash_records(
    records: list[FileRecord],
    max_workers: int | None = None,
    on_file_hashed: Callable[[FileRecord], None] | None = None,
) -> list[FileRecord]:
    """Return a new list of FileRecords with the ``sha256`` field populated.

    Hashing runs in a ThreadPoolExecutor — hashlib releases the GIL so
    workers run in true parallel on multi-core machines. Records whose files
    cannot be read are skipped with a warning and omitted from the result.

    Thread count adapts to filesystem type: network-mounted paths (SMBfs, NFS,
    AFP) use _NETWORK_WORKER_COUNT threads to hide round-trip latency; local
    paths use os.cpu_count().

    Args:
        records: FileRecords to hash. Each must have a valid ``path``.
        max_workers: Override automatic thread count selection. Defaults to
            auto-detect based on filesystem type.
        on_file_hashed: Optional callback invoked on the calling thread after
            each file completes hashing. Receives the populated FileRecord.
            Use this to drive progress bars without polling.

    Returns:
        New list of FileRecord instances (originals are not mutated).
    """
    if not records:
        return []

    if max_workers is None:
        if is_network_fs(records[0].path):
            # Cap to number of records so we don't spin up idle threads.
            max_workers = min(len(records), _NETWORK_WORKER_COUNT)
        else:
            max_workers = os.cpu_count()

    hashed: list[FileRecord] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_record = {pool.submit(hash_file, r.path): r for r in records}

        for future in as_completed(future_to_record):
            record = future_to_record[future]
            try:
                digest = future.result()
            except OSError as exc:
                warnings.warn(
                    f"Could not read file for hashing, skipping: {record.path} ({exc})",
                    stacklevel=2,
                )
                continue
            hashed_record = replace(record, sha256=digest)
            hashed.append(hashed_record)
            if on_file_hashed is not None:
                on_file_hashed(hashed_record)

    return hashed
