# I/O & End-to-End Optimization Record

**Original Date:** 2026-05-31
**Updated:** 2026-11-23
**Status:** Implemented — validated on Apple Silicon (macOS, Python 3.12)

---

## 1. Hardware Characterization (`fcompare benchmark`, 256 MB warm cache)

```
Section 1 — Algorithm comparison  (64 KB chunks)
  sha256      2,129 MB/s   1.00x  ← baseline (ARMv8.2 SHA-2 hardware extensions)
  blake3      1,624 MB/s   0.76x  (ARM Neon SIMD in software)
  sha512      1,304 MB/s   0.61x

Section 2 — Chunk size sweep (SHA-256)
  64 KB       2,117 MB/s   1.00x
  1 MB        2,138 MB/s   1.01x  ← optimal balance for NVMe + SMB3 MaxReadSize
  4 MB        1,946 MB/s   0.92x  (L3/DRAM cache pressure under concurrency)
```

### Algorithm Decisions Retained
- **Keep SHA-256 as default:** Apple Silicon dedicated SHA-256 instructions (`SHA256H`/`SHA256H2`)
  outperform both SHA-512 (`0.61x`) and software BLAKE3 (`0.76x`).
- **Keep `CHUNK_SIZE = 1 MB`:** Reduces read syscalls by 16x vs. `64 KB` and matches macOS `smbfs`
  negotiated `SMB3 MaxReadSize` (`1 MB+`) and APFS cluster I/O coalescing.

---

## 2. Implemented Engine & Command Optimizations (2026-11-23)

### 2.1 Zero-Allocation Unbuffered `readinto()` (`engine/hasher.py`)
- **Why `readinto()` over `fh.read(1 MB)` and `hashlib.file_digest()`:**
  - `fh.read(1 MB)` allocated a fresh `1 MB` `bytes` object per chunk (~100,000 heap allocations
    per 100 GB) and copied through Python's `BufferedReader`.
  - CPython's `hashlib.file_digest()` uses a smaller fixed internal buffer (`256 KB`), increasing
    VFS/SMB read transitions by 4x on network mounts.
  - Opening with `buffering=0` and reading into a per-thread `bytearray(1024 * 1024)` via
    `fh.readinto()` + `memoryview` achieves **~2,055–2,169 MB/s** single-core throughput while
    cutting per-file heap allocations from `~2,053 KB` to **`0.5 KB` (-99.98%)**.
- **Why `mmap` was NOT enabled on network/general paths:**
  - On SMBfs or network-backed APFS disk images, a dropped connection during a mapped page fault
    delivers `SIGBUS`, crashing Python unrecoverably (cannot be caught as `OSError`).

### 2.2 Encrypted APFS over SMBfs Tuning (`engine/fs_info.py`, `engine/hasher.py`)
- **3-way `StorageProfile` (`LOCAL`, `NETWORK_FS`, `NETWORK_DISK_IMAGE`):**
  - Direct `smbfs`/`nfs` mounts (`NETWORK_FS`) suffer per-file `SMB2 CREATE`/`CLOSE` RTTs and use up
    to `32` workers.
  - Mounted encrypted APFS `.dmg`/`.sparsebundle` volumes backed by SMBfs (`NETWORK_DISK_IMAGE`)
    decrypt AES-XTS on CPU (`apfs.kext`) and funnel all cache-miss reads through `diskimagesiod`
    into a single `.dmg` or 8 MB band files on the NAS. Running 32 threads caused 32-way random
    seeks across NAS storage. `NETWORK_DISK_IMAGE` now uses **`6` workers** and sorts files by
    **ascending APFS inode (`st_ino`)** to sweep extents/bands sequentially and preserve NAS
    read-ahead.
  - `LOCAL` and `NETWORK_FS` sort files by **`size` descending (Longest Processing Time first)** so
    multi-GB files never straggle on a single thread at the end of a batch.
- **System-wide `mount` & `hdiutil` caching + device-match bug fix:**
  - Cached parsed `mount` and `hdiutil info -plist` once per process (`@lru_cache(maxsize=1)`),
    skipping `hdiutil` on `/`.
  - Replaced `device.rstrip("s0123456789")` (which collapsed `/dev/disk7s1` to `/dev/disk` and
    falsely matched whichever `.dmg` appeared first) with exact whole-disk regex matching
    (`/dev/diskN`) plus synthesized APFS container `mount-point` matching.

### 2.3 Persistent `deque` + `SimpleQueue` Worker Pool (`engine/hasher.py`)
- Replaced eager `{pool.submit(...): r for r in records}` (and per-file `wait(FIRST_COMPLETED)`
  Condition lock scans) with `workers` persistent tasks consuming from an atomic `collections.deque`
  and emitting results over C-backed `SimpleQueue`. Keeps scheduling memory `O(workers)` regardless
  of file count and removes main-thread lock contention on small-file batches.

### 2.4 Iterative `os.scandir()` Walker with Subtree Pruning (`engine/walker.py`)
- Replaced `Path.rglob("*")` (which descended into `.git`, `.Spotlight-V100`, `.fseventsd`, and
  excluded directories and issued 3 `stat`/`lstat` syscalls per file) with an iterative
  `os.scandir()` stack:
  - Filters hidden/excluded names *before* descending or `stat`ing (preventing encrypted APFS
    catalog B-tree faults over SMB).
  - Uses `readdir` `d_type` + a single `DirEntry.stat()` per file and pre-compiles exclude globs
    into one combined regex.

### 2.5 Wire-Byte Elimination in `diff` and `dupes` (`commands/diff.py`, `commands/dupes.py`)
- **`diff`:** Never hashes `only_in_left` or `only_in_right`; skips hashing size-mismatched files
  automatically for `--show same`/`--show unique` and opt-in `--fast`; runs unified single-pool
  hashing when `left` and `right` share `st_dev`, or concurrent per-device pools across distinct
  mounts.
- **`dupes`:** Buckets sizes in one pass without single-item list allocations, then runs a
  4 KB-block-aligned `64 KB` head + `64 KB` tail pre-hash on size-colliding files `> 256 KB` before
  full SHA-256 hashing.
- **Models (`engine/models.py`):** Added `@dataclass(slots=True)` across all dataclasses.

---

## 3. Measured End-to-End Results (Original vs. Optimized)

Median of 5 runs on Apple Silicon (Python 3.12):

| Workload | Metric | Original | Optimized | Improvement |
|---|---|---:|---:|---:|
| Walk — 5,000 visible files | Wall time | `285.71 ms` | `34.35 ms` | **8.3x faster** (`-88.0%`) |
| Walk — 12,000 files (w/ `.Spotlight-V100`, `.git`, `node_modules`) | Wall time | `573.20 ms` | `21.64 ms` | **26.5x faster** (`-96.2%`) |
| `diff` — partial sync (default) | Wall time | `258.94 ms` | `71.82 ms` | **3.6x faster** (`-72.3%`) |
| `diff` — partial sync (`--fast` / `--show same`) | Wall time | `258.94 ms` | `55.14 ms` | **4.7x faster** (`-78.7%`) |
| `diff` — bytes read (default / `--fast`) | Disk/SMB I/O | `622.58 MB` | `222.58` / `62.50 MB` | **2.8x / 10.0x less I/O** |
| `dupes` — 2k unique + 40 × 4 MB bands + 6 × 4 MB dupes | Wall time | `135.17 ms` | `22.89 ms` | **5.9x faster** (`-83.1%`) |
| `dupes` — bytes read | Disk/SMB I/O | `184.00 MB` | `29.75 MB` | **6.2x less I/O** (`-83.8%`) |
| `dupes` — peak Python memory | `tracemalloc` | `17,471.5 KB` | `6,243.0 KB` | **2.8x lower RAM** (`-64.3%`) |
| `hash_file` — 256 MB file | Throughput | `1,712.9 MB/s` | `2,055.1 MB/s` | **+20.0% throughput** |
| `hash_file` — 256 MB heap allocations | `tracemalloc` | `2,053.0 KB` | `0.5 KB` | **-99.98% heap churn** |
| `hash_records` — mixed 188 MB w/ 128 MB tail file | Wall time | `83.93 ms` | `64.87 ms` | **1.29x faster** (`-22.7%`) |
| `fs_info.is_network_fs` — cold cache + 50 paths | Wall time | `692.41 ms` | `9.79 ms` | **70.7x faster** (`-98.6%`) |
| `FileRecord` memory — 50,000 instances | `tracemalloc` | `7,066.6 KB` | `5,894.7 KB` | **-16.6% RAM** |
