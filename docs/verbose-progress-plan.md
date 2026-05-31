# Verbose Progress Reporting — Design & Implementation Plan

**Date:** 2026-05-31
**Status:** Active
**Depends on:** design.md, implementation-plan.md

---

## Problem

Both `diff` and `dupes` display a non-updating spinner during the hash phase.
For large directories (tens of thousands of files, multi-GB photo libraries) this looks
identical to a hang for minutes at a time. The user has no visibility into:

- How many files were found
- How many have been hashed vs. remain
- Current throughput
- Estimated time remaining

---

## Goal

Add a `--verbose` / `-v` flag to both commands that replaces the silent spinner with
a multi-phase Rich progress display. Default (non-verbose) behaviour is unchanged.

---

## Proposed Output

### `fcompare diff --verbose`

```
  Scanning left…          3,241 files
  Scanning right…         3,198 files
  Hashing 6,439 files  ━━━━━━━━━━━━━━━━━━━  62%  3,989/6,439  14.2 MB/s  eta 0:00:18
```

### `fcompare dupes --verbose`

```
  Scanning…               18,432 files found
  Size filter…            1,204 candidates  (17,228 skipped — unique size)
  Hashing 1,204 files  ━━━━━━━━━━━━━━━━━━━  88%  1,059/1,204   8.6 MB/s  eta 0:00:04
```

---

## Architecture

### Root cause

`hash_records()` runs all hashing in a `ThreadPoolExecutor` and only returns after
all futures complete. The caller has no window to update a progress bar between files.

The fix: add an optional `on_file_hashed` callback to `hash_records()`. It is called
from the `as_completed` loop — one call per file, on the main thread, after the worker
thread finishes. This keeps the engine layer free of Rich/display dependencies while
giving the display layer a hook for per-file updates.

### New module: `display/progress.py`

A `ProgressReporter` class that owns a Rich `Progress` instance and exposes the
phase transitions that both commands need:

```
ProgressReporter
  .walk_phase(label: str) -> context manager
      Shows a spinner + live file count ticking up as files are discovered.
      Caller wraps walk() with this and feeds it each FileRecord.

  .hash_phase(total_files: int, total_bytes: int) -> on_file_hashed callback
      Creates a bounded progress bar with file count, MB/s, ETA.
      Returns a callable to pass into hash_records(on_file_hashed=...).

  .print_filter_summary(total: int, candidates: int) -> None
      Prints "N candidates (M skipped)" for the dupes size-filter step.
```

When `verbose=False`, `ProgressReporter` is replaced by a no-op stub that satisfies
the same interface — so the command logic branches only once (at the flag check), not
at every display call.

---

## Detailed Changes

### 1. `engine/hasher.py`

Add an optional callback to `hash_records()`:

```python
from collections.abc import Callable

def hash_records(
    records: list[FileRecord],
    max_workers: int | None = None,
    on_file_hashed: Callable[[FileRecord], None] | None = None,
) -> list[FileRecord]:
    ...
    for future in as_completed(future_to_record):
        record = future_to_record[future]
        try:
            digest = future.result()
        except OSError as exc:
            warnings.warn(...)
            continue
        hashed_record = replace(record, sha256=digest)
        hashed.append(hashed_record)
        if on_file_hashed is not None:
            on_file_hashed(hashed_record)   # ← new
```

**Backward compatible** — default is `None`, existing callers unchanged.

---

### 2. `display/progress.py` (new file)

```python
class ProgressReporter:
    """Manages Rich progress display for multi-phase hashing operations."""

    def walk_phase(self, label: str) -> AbstractContextManager[Callable[[FileRecord], None]]:
        """Context manager; yields a tick() callable to call per discovered file."""
        ...

    def hash_phase(
        self, total_files: int, total_bytes: int
    ) -> Callable[[FileRecord], None]:
        """Returns an on_file_hashed callback for hash_records()."""
        ...

    def print_filter_summary(self, total: int, candidates: int) -> None:
        """Print the size-filter reduction line (dupes only)."""
        ...

    def stop(self) -> None:
        """Finalize and clear progress display."""
        ...


class NoOpProgressReporter:
    """Drop-in no-op used when --verbose is not set."""

    def walk_phase(self, label: str) -> AbstractContextManager[...]:
        ...   # yields a no-op tick

    def hash_phase(self, total_files: int, total_bytes: int) -> Callable[...]:
        return lambda r: None

    def print_filter_summary(self, total: int, candidates: int) -> None:
        pass

    def stop(self) -> None:
        pass
```

**Rich Progress columns for hash phase:**

| Column | Type | Notes |
|---|---|---|
| `SpinnerColumn` | animated | always on |
| `TextColumn` | description | "Hashing N files…" |
| `BarColumn` | progress bar | bounded when total known |
| `TaskProgressColumn` | `{done}/{total}` | file counts |
| `TransferSpeedColumn` | MB/s | uses `completed` = bytes hashed |
| `TimeRemainingColumn` | ETA | requires bounded total |

The `advance()` call passes `bytes_hashed = record.size` as `completed` delta so
`TransferSpeedColumn` and `TimeRemainingColumn` work correctly (speed in bytes/s,
ETA based on remaining bytes).

---

### 3. `commands/diff.py`

Add `--verbose / -v` flag. Thread into `compare_directories()`:

```python
def compare_directories(
    left: Path,
    right: Path,
    options: WalkOptions,
    reporter: ProgressReporter | NoOpProgressReporter | None = None,
) -> DiffResult:
    rep = reporter or NoOpProgressReporter()

    with rep.walk_phase(f"Scanning {left.name}…") as tick:
        left_records = [r for r in walk(left, options) if not tick(r) or True]

    with rep.walk_phase(f"Scanning {right.name}…") as tick:
        right_records = [r for r in walk(right, options) if not tick(r) or True]

    cb = rep.hash_phase(
        total_files=len(left_records) + len(right_records),
        total_bytes=sum(r.size for r in left_records + right_records),
    )
    left_hashed = hash_records(left_records, on_file_hashed=cb)
    right_hashed = hash_records(right_records, on_file_hashed=cb)
    ...
```

In `diff_cmd`, construct `ProgressReporter()` or `NoOpProgressReporter()` based on
the `--verbose` flag and pass it in. The existing transient spinner is removed when
`verbose=True` (the ProgressReporter owns the display).

---

### 4. `commands/dupes.py`

Add `--verbose / -v` flag. Thread into `find_duplicates()`:

```python
def find_duplicates(
    root: Path,
    options: WalkOptions,
    min_size: int = 1,
    reporter: ProgressReporter | NoOpProgressReporter | None = None,
) -> list[DupeGroup]:
    rep = reporter or NoOpProgressReporter()

    with rep.walk_phase("Scanning…") as tick:
        all_records = [r for r in walk(root, options) if r.size >= min_size and not tick(r) or True]

    # size-filter step
    ...build by_size, candidates...
    rep.print_filter_summary(total=len(all_records), candidates=len(candidates))

    cb = rep.hash_phase(
        total_files=len(candidates),
        total_bytes=sum(r.size for r in candidates),
    )
    hashed = hash_records(candidates, on_file_hashed=cb)
    ...
```

---

### 5. `cli.py` — no changes needed

`--verbose` is per-command, not global, because the two commands have different
phase structures. A global flag would require threading it through the entire call
chain without meaningful benefit.

---

## File Change Summary

| File | Change |
|---|---|
| `src/fcompare/engine/hasher.py` | Add `on_file_hashed` callback param |
| `src/fcompare/display/progress.py` | **New** — `ProgressReporter`, `NoOpProgressReporter` |
| `src/fcompare/commands/diff.py` | Add `--verbose` flag, `reporter` param on `compare_directories` |
| `src/fcompare/commands/dupes.py` | Add `--verbose` flag, `reporter` param on `find_duplicates` |
| `tests/unit/test_hasher.py` | Test callback fires per-file with correct record |
| `tests/unit/test_progress.py` | **New** — test ProgressReporter phases, NoOpProgressReporter |
| `tests/integration/test_diff_command.py` | Test `--verbose` flag doesn't break output |
| `tests/integration/test_dupes_command.py` | Test `--verbose` flag doesn't break output |

---

## Implementation Sequence

### Step 1 — `engine/hasher.py` callback (30 min)

Minimal, isolated, backward-compatible. Add the callback and a unit test.
All existing tests must still pass unchanged.

### Step 2 — `display/progress.py` (1 hr)

Implement `ProgressReporter` and `NoOpProgressReporter`. Unit test both:
- `ProgressReporter` tick callback advances the task correctly
- `NoOpProgressReporter` is a silent no-op (never raises, never prints)
- Both satisfy the same interface

### Step 3 — Wire into `diff` command (45 min)

- Refactor `compare_directories()` to accept `reporter`
- Add `--verbose` flag to `diff_cmd`
- Integration test: `--verbose` produces "Scanning" and "Hashing" in stderr;
  final stdout result is identical to non-verbose run

### Step 4 — Wire into `dupes` command (45 min)

- Refactor `find_duplicates()` to accept `reporter`
- Add `--verbose` flag to `dupes_cmd`
- Integration test: filter summary line appears in `--verbose` output;
  stdout result identical to non-verbose

### Step 5 — Lint, coverage, smoke test (30 min)

- `ruff + mypy` clean
- Coverage ≥ 90% (target: maintain ~97%)
- Manual smoke: `fcompare diff ~/large-dir1 ~/large-dir2 --verbose`

---

## Design Constraints

- Engine layer (`engine/`) must not import from `display/` — progress is a display concern.
- `NoOpProgressReporter` must be the default — verbose off by default, no behaviour change.
- `compare_directories()` and `find_duplicates()` remain unit-testable without a Rich console.
- Walk phase uses an indeterminate counter (total unknown); hash phase uses a bounded bar (total known before hashing starts).
- Progress writes to `stderr`; results write to `stdout` — pipe-friendly.
