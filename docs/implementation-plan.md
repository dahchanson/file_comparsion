# fcompare — Implementation Plan & Status

**Original Date:** 2026-05-31
**Updated:** 2026-11-23
**Status:** Completed (Phases 1–6)
**Depends on:** design.md, io-optimization-plan.md, verbose-progress-plan.md

---

## Phase 1 — Project Scaffold & Core Engine

### 1.1 Project Scaffold
- [x] `pyproject.toml` (`fcompare` 0.1.0, Python `>=3.11`, `typer~=0.12`, `rich~=13.0`, dev & benchmark extras)
- [x] `src/fcompare/__init__.py`, `__main__.py`, `cli.py`
- [x] `Makefile` (`install-dev`, `lint`, `test`, `test-cov`, `build`, `clean`), `.gitignore`, MIT `LICENSE`

### 1.2 Data Models (`engine/models.py`)
- [x] Slotted dataclasses (`@dataclass(slots=True)`): `FileRecord` (including `ino`), `DiffResult`, `DupeGroup`
- [x] Unit tests in `tests/unit/test_models.py` (100% coverage)

### 1.3 Filesystem Detection (`engine/fs_info.py`)
- [x] System-wide cached `mount` and `hdiutil info -plist` parsers
- [x] `StorageProfile` (`LOCAL`, `NETWORK_FS`, `NETWORK_DISK_IMAGE`, `UNKNOWN`) + `is_network_fs`, `is_local_fs`, `is_mmap_safe`
- [x] Whole-disk (`/dev/diskN`) and synthesized APFS container `mount-point` matching for SMB-backed `.dmg`/`.sparsebundle` mounts
- [x] Unit tests in `tests/unit/test_fs_info.py`

### 1.4 Walker (`engine/walker.py`)
- [x] `WalkOptions` slotted dataclass (`recurse`, `follow_symlinks`, `include_hidden`, `exclude_patterns`)
- [x] Iterative `os.scandir()` walker with pre-descent hidden/excluded directory pruning, single `DirEntry.stat()`, compiled glob regex, `st_ino` capture, and symlink loop detection
- [x] Unit tests in `tests/unit/test_walker.py`

### 1.5 Hasher (`engine/hasher.py`)
- [x] `CHUNK_SIZE = 1 MB`, reusable thread-local `bytearray` + unbuffered `readinto()` (`hash_file`)
- [x] 4 KB-aligned 64 KB head + 64 KB tail partial pre-hasher (`partial_hash_file`, `prehash_records`)
- [x] Profile-aware worker sizing & read ordering (`os.cpu_count()` + LPT for `LOCAL`; `32` + LPT for `NETWORK_FS`; `6` + ascending `st_ino` for `NETWORK_DISK_IMAGE`)
- [x] Persistent `deque` + `SimpleQueue` worker pool (`O(workers)` memory, zero per-file lock contention)
- [x] Unit tests in `tests/unit/test_hasher.py` (100% coverage)

---

## Phase 2 — `diff` Command (`commands/diff.py`)

- [x] `compare_directories()` with immediate `only_in_left`/`only_in_right` classification (zero file reads)
- [x] Size-mismatch short-circuit when modified digests are unneeded (`--fast`, `--show same`, `--show unique`)
- [x] Device-aware execution: single unified pool when `st_dev` matches; concurrent walk & hash pools across distinct devices
- [x] Rich (`display/rich_renderer.py`) and JSON (`display/json_renderer.py`) renderers + `--verbose` progress
- [x] Integration tests in `tests/integration/test_diff_command.py`

---

## Phase 3 — `dupes` Command (`commands/dupes.py`)

- [x] Three-stage duplicate finder: single-pass size bucketing → 128 KB head+tail pre-hash (`> 256 KB`) → full SHA-256 verification
- [x] Safe deletion planning (`--delete-keep [oldest|newest|first]`) gated behind explicit `--confirm`
- [x] Rich and JSON renderers + `--verbose` filter summary and progress
- [x] Integration tests in `tests/integration/test_dupes_command.py`

---

## Phase 4 — `benchmark` Command & Verbose Progress

- [x] `fcompare benchmark` (`commands/benchmark.py`) comparing SHA-256, SHA-512, and optional BLAKE3 across chunk sizes and I/O strategies
- [x] `ProgressReporter` / `NoOpProgressReporter` (`display/progress.py`) with batched walk ticks and file-count + byte-rate hash bars (`tests/unit/test_progress.py`)

---

## Phase 5 — Packaging & Quality Gates

- [x] `ruff check src/ tests/` — zero warnings/errors
- [x] `mypy src/` (`strict = true`) — zero errors across all 17 source files
- [x] `pytest tests/ --cov=src/fcompare --cov-fail-under=90` — **219 passed, 96.94% coverage**
- [x] Editable install & GitHub repository (`trunk`) verified

---

## Phase 6 — Performance & Encrypted APFS-over-SMBfs Optimization

- [x] 8.3x–26.5x faster directory walks via `os.scandir()` + subtree pruning
- [x] 3.6x–4.7x faster `diff` (up to 10x less read I/O) and 5.9x faster `dupes` (6.2x less read I/O, 2.8x lower peak RAM)
- [x] +20% single-stream hashing throughput (`~2,055 MB/s`) with 99.98% reduction in per-file heap allocations
- [x] 70.7x faster multi-path filesystem detection and dedicated `NETWORK_DISK_IMAGE` scheduling for encrypted APFS volumes over SMBfs
