# fcompare — macOS File Utility: Design Document

**Date:** 2026-05-31 (Updated: 2026-11-23)
**Status:** Implemented
**Author:** Senior Staff Engineer

---

## 1. Purpose

`fcompare` is a single macOS CLI utility with extensible subcommands for file analysis. Core use
cases are directory diffing (by relative path, size, and SHA-256 hash), duplicate detection within
a directory tree, and hardware/I/O hashing benchmarks. It is tuned for both local Apple Silicon
NVMe storage and network-mounted filesystems — including encrypted APFS disk images (`.dmg` /
`.sparsebundle`) mounted over SMBfs.

---

## 2. Repository Layout

```
fcompare/
├── LICENSE
├── Makefile
├── pyproject.toml
├── README.md
├── docs/
│   ├── design.md                  ← this file
│   ├── implementation-plan.md
│   ├── io-optimization-plan.md
│   └── verbose-progress-plan.md
├── src/
│   └── fcompare/
│       ├── __init__.py            ← package version
│       ├── __main__.py            ← entry point: `python -m fcompare`
│       ├── cli.py                 ← Typer app, subcommand registration
│       ├── engine/
│       │   ├── fs_info.py         ← mount/hdiutil inspection & StorageProfile detection
│       │   ├── walker.py          ← iterative os.scandir traversal with subtree pruning
│       │   ├── hasher.py          ← unbuffered readinto SHA-256, partial pre-hash, worker pool
│       │   └── models.py          ← slotted dataclasses: FileRecord, DiffResult, DupeGroup
│       ├── commands/
│       │   ├── diff.py            ← `fcompare diff <dir1> <dir2>`
│       │   ├── dupes.py           ← `fcompare dupes <dir>`
│       │   └── benchmark.py       ← `fcompare benchmark`
│       └── display/
│           ├── progress.py        ← Rich live multi-phase progress (--verbose) & no-op stub
│           ├── renderer.py        ← dispatches to format-specific renderers
│           ├── rich_renderer.py   ← colored terminal report (default)
│           └── json_renderer.py   ← --format=json for scripting
└── tests/
    ├── unit/
    │   ├── test_fs_info.py
    │   ├── test_hasher.py
    │   ├── test_models.py
    │   ├── test_progress.py
    │   └── test_walker.py
    └── integration/
        ├── test_diff_command.py
        └── test_dupes_command.py
```

---

## 3. Core Data Models (`engine/models.py`)

All models use `@dataclass(slots=True)` to eliminate per-instance `__dict__` overhead across large
directory trees:

```python
FileRecord(path: Path, rel_path: Path, size: int, mtime: float, sha256: str = "", ino: int = 0)
DiffResult(left_root: Path, right_root: Path, only_in_left, only_in_right, modified, identical)
DupeGroup(sha256: str, files: list[FileRecord])
```

`StorageProfile` (`engine/fs_info.py`):

```python
StorageProfile = LOCAL | NETWORK_FS | NETWORK_DISK_IMAGE | UNKNOWN
```

---

## 4. Core Engine

### 4.1 `engine/fs_info.py`

- Parses macOS `mount` and `hdiutil info -plist` at most once per process (`@lru_cache(maxsize=1)`),
  skipping `hdiutil` entirely when the resolved mountpoint is `/`.
- Classifies target paths into a 3-way `StorageProfile`:
  - `LOCAL`: Local block-device mounts (`apfs`, `hfs`, `exfat`, `msdos`, `ntfs`, `udf` with the
    `local` mount flag and no network-backed disk image).
  - `NETWORK_FS`: Direct network mounts (`smbfs`, `nfs`, `afpfs`, `cifs`, `webdavfs`, `ftpfs`) or
    APFS lacking the `local` flag (e.g., iSCSI).
  - `NETWORK_DISK_IMAGE`: APFS volumes bearing the `local` flag whose backing `.dmg` or
    `.sparsebundle` resides on a network mount. Matches both exact whole-disk nodes (`/dev/diskN`)
    and synthesized APFS container `mount-point` entries from `hdiutil`.

### 4.2 `engine/walker.py`

- Uses an iterative stack over `os.scandir()` rather than `pathlib.Path.rglob("*")`:
  - **Pre-descent directory pruning:** Hidden directories (`.git`, `.Spotlight-V100`, `.fseventsd`,
    `.Trashes`) and excluded directories (`--exclude`) are filtered by name *before* descending or
    calling `stat()`, avoiding unnecessary SMB round-trips and encrypted APFS catalog B-tree faults.
  - **Single `stat` per file:** Uses `DirEntry` `d_type` from `readdir` for symlink/dir/file checks
    at zero extra syscall cost, calling `DirEntry.stat()` once per yielded file and capturing
    `st_ino` for physical allocation-order sorting.
  - **Compiled exclude globs:** Translates all `--exclude` patterns via `fnmatch.translate()` into
    a single combined regex matched against filename, relative path, and full path.
  - **Symlink loop safety:** Tracks visited `(st_dev, st_ino)` pairs when `follow_symlinks=True`.
  - **Resilience:** `OSError` on `scandir()` or individual entries warns and continues.

### 4.3 `engine/hasher.py`

- **Zero-allocation streaming (`hash_file`):** Opens files unbuffered (`buffering=0`) and reads
  `1 MB` chunks into a reusable per-thread `bytearray`/`memoryview` via `readinto()`, avoiding
  `BufferedReader` double-copying and per-chunk heap allocations while matching SMB3 `MaxReadSize`.
- **4 KB-aligned partial pre-hashing (`partial_hash_file` / `prehash_records`):** Hashes file size
  plus the first `64 KB` and final `64 KB` (aligned to a `4096`-byte APFS encryption block boundary)
  for rapid candidate elimination in `dupes`.
- **Profile-aware concurrency & ordering (`_plan_execution`):**
  - `LOCAL`: `os.cpu_count()` workers, sorted by `size` descending (Longest Processing Time first)
    to prevent large-file tail stragglers.
  - `NETWORK_FS`: up to `32` workers (hiding per-file SMB/NFS open/close RTTs), sorted by `size`
    descending.
  - `NETWORK_DISK_IMAGE`: up to `6` workers, sorted by ascending APFS inode (`st_ino`) so reads
    through `diskimagesiod` sweep `.dmg` extents and `.sparsebundle` 8 MB bands sequentially rather
    than inducing 32-way random seeks on NAS storage.
- **Low-contention worker pool:** Spawns `workers` persistent consumer tasks pulling from an atomic
  `collections.deque` and streaming results through a C-backed `SimpleQueue` (`O(workers)` memory,
  zero per-file `Future` / lock churn).

---

## 5. Subcommands

### 5.1 `fcompare diff <left-dir> <right-dir>`

**Algorithm:**
1. Check whether `left` and `right` share `st_dev`. Walk sequentially on a shared device or
   concurrently across distinct devices (e.g., local NVMe vs. SMB-mounted volume).
2. Partition relative-path keys immediately:
   - `only_in_left` / `only_in_right` are classified with **zero file reads**.
   - When `need_modified_hashes` is false (`--fast`, `--show same`, or `--show unique`), shared
     paths with differing byte sizes (`left.size != right.size`) are classified immediately as
     `modified` with **zero file reads**.
3. Hash remaining shared files:
   - Same device (`st_dev` equal): single unified `hash_records()` call for global LPT/`st_ino`
     ordering and zero pool-drain bubble.
   - Distinct devices: two concurrent `hash_records()` pools so each device uses its own optimal
     `StorageProfile`.
4. Classify shared keys into `identical` or `modified`.

**Flags:**
- `--format, -f [rich|json]`
- `--show, -s [all|modified|unique|same]`
- `--recurse / --no-recurse`
- `--exclude, -e PATTERN` (repeatable)
- `--follow-symlinks`
- `--fast` — skip hashing shared files whose byte sizes already differ
- `--verbose, -v` — live scan and hashing progress on `stderr`

---

### 5.2 `fcompare dupes <dir>`

**Algorithm:**
1. **Stage 1 — Single-pass size bucketing:** Group files by byte size during `walk()` without
   allocating single-element lists for uniquely-sized files.
2. **Stage 2 — Partial head+tail pre-hash:** For size-colliding files `> 256 KB`, compute 128 KB
   head+tail digests and discard unique `(size, partial_digest)` entries.
3. **Stage 3 — Full SHA-256 verification:** Hash surviving candidates and report groups with 2+
   identical digests, sorted by `wasted_bytes` descending.

**Flags:**
- `--format, -f [rich|json]`
- `--min-size BYTES` (default: `1`)
- `--exclude, -e PATTERN` (repeatable)
- `--recurse / --no-recurse`
- `--follow-symlinks`
- `--verbose, -v`
- `--delete-keep [oldest|newest|first]` — prints deletion plan only unless `--confirm` is set
- `--confirm` — executes deletion plan (requires `--delete-keep`)

---

### 5.3 `fcompare benchmark`

Creates a synthetic file in a temporary directory and benchmarks SHA-256, SHA-512, and optional
BLAKE3 (`pip install 'fcompare[benchmark]'`) across chunk sizes (`64 KB`, `1 MB`, `4 MB`) and I/O
strategies (`read()`, `hashlib.file_digest()`, `mmap`).

---

## 6. Display Layer (`display/`)

- `renderer.py` dispatches typed results (`DiffResult` or `list[DupeGroup]`) to lazy-imported
  format renderers (`rich_renderer.py` or `json_renderer.py`).
- `progress.py` provides `ProgressReporter` (writing Rich progress bars to `stderr` with batched
  128-file walk updates, exact `done/total` file counts, MB/s transfer speed, and byte-weighted ETA)
  and `NoOpProgressReporter` (used when `--verbose` is off or `--format json` is active).

---

## 7. Key Design Decisions

### 7.1 Relative-path keying & unshared-path skip in `diff`
Comparing by `rel_path` matches mirroring/sync semantics, and intersecting `rel_path` keys prior to
hashing avoids reading unshared subtrees over slow network links.

### 7.2 Three-stage filter in `dupes`
Combining zero-I/O size grouping with a 4 KB-aligned 128 KB head+tail pre-hash eliminates both
uniquely-sized files (photos, documents) and fixed-size non-duplicates (`.sparsebundle` 8 MB bands,
RAW/WAV frames, VM segments) before full-file reads.

### 7.3 Dedicated `NETWORK_DISK_IMAGE` profile (Encrypted APFS over SMBfs)
An encrypted APFS `.dmg`/`.sparsebundle` mounted over SMBfs runs AES-XTS in software (`apfs.kext`)
and funnels all block reads through `diskimagesiod` into one `.dmg` or band set on the NAS. Using 6
workers sorted by ascending `st_ino` saturates SMB3 read pipelining without causing 32-way random
seeks, and avoiding `mmap` prevents unrecoverable `SIGBUS` crashes on transient network drops.

### 7.4 No destructive action without `--confirm`
`--delete-keep` prints a dry-run plan only; `--confirm` is required to unlink files.

---

## 8. Out of Scope (v1)

- Line-by-line text diffing (use `diff` or `difftastic`)
- GUI / menu bar app
- Agent-based SSH / cloud object store (`S3`, `GCS`) traversal (mounted SMBfs/NFS/AFP shares and
  network-backed disk images *are* supported)
