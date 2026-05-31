"""Parallel SHA-256 hashing of FileRecord collections."""

import hashlib
import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from fcompare.engine.models import FileRecord

# 64 KB balances syscall overhead against memory pressure for large files.
CHUNK_SIZE: int = 65536


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
) -> list[FileRecord]:
    """Return a new list of FileRecords with the ``sha256`` field populated.

    Hashing runs in a ThreadPoolExecutor — hashlib releases the GIL so
    workers run in true parallel on multi-core machines. Records whose files
    cannot be read are skipped with a warning and omitted from the result.

    Args:
        records: FileRecords to hash. Each must have a valid ``path``.
        max_workers: Thread pool size. Defaults to os.cpu_count().

    Returns:
        New list of FileRecord instances (originals are not mutated).
    """
    if not records:
        return []

    workers = max_workers if max_workers is not None else os.cpu_count()
    hashed: list[FileRecord] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
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
            hashed.append(replace(record, sha256=digest))

    return hashed
