# Verbose Progress Reporting — Design & Implementation Notes

**Original Date:** 2026-05-31
**Updated:** 2026-11-23
**Status:** Implemented
**Depends on:** design.md, implementation-plan.md

---

## Problem & Goal

Without `--verbose`, `diff` and `dupes` show an indeterminate spinner while hashing. On multi-GB
photo libraries or SMB-mounted APFS archives, users need live visibility into discovered file
counts, size/pre-hash filter reduction, hashing throughput (MB/s), and byte-accurate ETA.

Adding `--verbose` / `-v` replaces the transient spinner with a multi-phase Rich progress display on
`stderr` while keeping default and `--format json` behavior clean for scripting.

---

## Output Format

### `fcompare diff --verbose`

```
  Scanning left…   3,241 files ━━━━━━━━━━━━━━━━━━━━━━━━━
  Scanning right…  3,198 files ━━━━━━━━━━━━━━━━━━━━━━━━━
  Hashing 6,120 files ━━━━━━━━━━━━━━╺━━━━━━ 3,989/6,120  112.4 MB/s  0:00:18  0:00:31
```

*(Note: `Hashing` count reflects only shared files requiring SHA-256 verification; unique files and
`--fast` size-mismatched files are classified without hashing.)*

### `fcompare dupes --verbose`

```
  Scanning…  18,432 files ━━━━━━━━━━━━━━━━━━━━━━━━━
  Size filter:  1,204 candidates  (17,228 skipped — unique size)
  Hashing 312 files ━━━━━━━━━━━━━━━━━━╺━━ 274/312  98.6 MB/s  0:00:04  0:00:29
```

*(Note: when size-colliding files exceed `256 KB`, the 128 KB head+tail pre-hash further narrows
candidates before the full `Hashing` phase begins.)*

---

## Implemented Architecture (`display/progress.py`)

### Engine decoupling
`hash_records()` accepts `on_file_hashed: Callable[[FileRecord], None] | None = None`, invoked on
the main thread as worker threads push completed digests onto the result `SimpleQueue` (with a
thread lock when `diff` runs two device pools concurrently). `engine/` has zero dependency on
`rich` or `display/`.

### `ProgressReporter` & `NoOpProgressReporter`
Both expose identical methods instantiated via `make_reporter(use_verbose)`:
- `walk_phase(label: str) -> ContextManager[Callable[[FileRecord], None]]`
- `hash_phase(total_files: int, total_bytes: int) -> Callable[[FileRecord], None]`
- `print_filter_summary(total: int, candidates: int) -> None`
- `stop() -> None`

### Production Refinements (2026-11-23)

1. **Batched walk-phase updates (`_WALK_UPDATE_BATCH = 128`):**
   - Updating Rich task descriptions and acquiring Rich's internal `RLock` on every file created
     unnecessary overhead on fast `os.scandir()` walks over 100k+ files.
   - `tick()` increments an integer counter and updates Rich every `128` files, then writes the
     exact final `total=count, completed=count` in the context manager's `finally` block.
2. **Accurate file counts alongside byte-weighted speed & ETA:**
   - Using Rich's built-in `MofNCompleteColumn` with `total=total_bytes` previously rendered raw
     byte counts (`1048576/2097152`) instead of file counts, and `max(record.size, 1)` overflowed
     `total_bytes` when 0-byte files were present.
   - `ProgressReporter` now tracks byte progress on the task (`total=max(total_bytes, 1)`,
     `advance=record.size`) so `TransferSpeedColumn` and `TimeRemainingColumn` remain byte-accurate,
     while rendering `{task.fields[counts]}` (`done_files/total_files`) for exact file counts and
     cleanly completing `total_bytes == 0` batches.
3. **Thread safety across concurrent `diff` pools:**
   - When `diff` compares directories across two distinct devices (`left` NVMe + `right` SMB), both
     walk phases and hash callbacks safely update `ProgressReporter` concurrently.
