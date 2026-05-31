# fcompare — macOS File Utility: Design Document

**Date:** 2026-05-31
**Status:** Accepted
**Author:** Senior Staff Engineer

---

## 1. Purpose

`fcompare` is a single macOS CLI utility with extensible subcommands for file analysis. Initial
use cases are directory diffing (by SHA-256 hash) and duplicate detection within a directory tree.
The architecture is designed to accommodate a growing set of file-analysis use cases without
restructuring the core engine.

---

## 2. Repository Layout

```
fcompare/
├── pyproject.toml
├── README.md
├── docs/
│   ├── design.md                ← this file
│   └── implementation-plan.md
├── src/
│   └── fcompare/
│       ├── __main__.py          ← entry point: `python -m fcompare`
│       ├── cli.py               ← Typer app, subcommand registration
│       ├── engine/
│       │   ├── walker.py        ← recursive directory traversal with filtering
│       │   ├── hasher.py        ← streaming SHA-256, thread-pool parallel hashing
│       │   └── models.py        ← FileRecord, DiffResult, DupeGroup (dataclasses)
│       ├── commands/
│       │   ├── diff.py          ← `fcompare diff <dir1> <dir2>`
│       │   └── dupes.py         ← `fcompare dupes <dir>`
│       └── display/
│           ├── renderer.py      ← dispatches to format-specific renderers
│           ├── rich_renderer.py ← colored tree/table output (default)
│           └── json_renderer.py ← --format=json for scripting
└── tests/
    ├── unit/
    │   ├── test_hasher.py
    │   ├── test_walker.py
    │   └── test_models.py
    └── integration/
        ├── test_diff_command.py
        └── test_dupes_command.py
```

---

## 3. Core Data Models (`engine/models.py`)

```
FileRecord(path: Path, rel_path: Path, sha256: str, size: int, mtime: float)
DiffResult(only_in_left, only_in_right, modified, identical)
DupeGroup(sha256: str, files: list[FileRecord])
```

---

## 4. Core Engine

### 4.1 `engine/hasher.py`

- Streams files in 64 KB chunks — never loads full file into memory.
- Uses `concurrent.futures.ThreadPoolExecutor`; hashlib releases the GIL for true parallelism.
- Worker count defaults to `os.cpu_count()`.
- Returns `dict[Path, str]` (path → hex digest).

### 4.2 `engine/walker.py`

- Wraps `pathlib.Path.rglob("*")` with configurable filtering.
- Filters: hidden files (opt-in), symlinks (skipped by default), glob exclude patterns.
- Handles `PermissionError` gracefully: warns and continues.
- Yields `FileRecord` objects with `rel_path` preserved relative to the walk root.

---

## 5. Subcommands

### 5.1 `fcompare diff <left-dir> <right-dir>`

**Algorithm:**
1. Hash all files in both directories in parallel.
2. Build two dicts: `{relative_path → sha256}`.
3. Classify each path into: `only_left`, `only_right`, `same`, `modified`.

**Visual output:**
```
Directory Comparison: /path/a  ↔  /path/b
─────────────────────────────────────────────────
  ✓ same          docs/readme.md
  ~ modified      src/config.py
  + only in left  src/legacy.py
  - only in right src/new_feature.py
─────────────────────────────────────────────────
Summary: 142 files  │  98 identical  │  12 modified  │  18 unique to each
```

**Flags:**
- `--format [rich|json|csv]`
- `--show [all|modified|unique|same]` — filter output rows
- `--no-recurse`
- `--exclude PATTERN` — glob pattern, repeatable
- `--follow-symlinks`

---

### 5.2 `fcompare dupes <dir>`

**Algorithm:**
1. Walk directory; collect all files.
2. First pass: group by file size — eliminates most non-duplicates at zero I/O cost.
3. Second pass: hash only files that share a size with at least one other file.
4. Report groups with 2+ identical SHA-256 digests.

**Visual output:**
```
Duplicate Files in: /path/to/dir
─────────────────────────────────
  SHA-256: a3f8…9c12   (3 copies, 2.4 MB wasted)
    /photos/vacation/IMG_001.jpg
    /photos/backup/IMG_001.jpg
    /archive/old/IMG_001.jpg

  SHA-256: b91d…4e77   (2 copies, 512 KB wasted)
    /docs/report.pdf
    /docs/report_final.pdf
─────────────────────────────────
Summary: 5 dupe groups  │  8 redundant files  │  12.1 MB reclaimable
```

**Flags:**
- `--format [rich|json|csv]`
- `--min-size BYTES` — skip files smaller than this (default: 1)
- `--exclude PATTERN`
- `--no-recurse`
- `--delete-keep [oldest|newest|first]` — prints a deletion plan; does NOT delete without `--confirm`

---

## 6. Display Layer (`display/`)

`renderer.py` selects the renderer based on `--format`. Each renderer receives the typed result
object (`DiffResult` or `list[DupeGroup]`) and is responsible only for presentation.

Adding a new output format requires only a new renderer file — no changes to command logic.

| Format | Module | Use |
|---|---|---|
| `rich` (default) | `rich_renderer.py` | Interactive terminal, color, progress |
| `json` | `json_renderer.py` | Scripting, piping to `jq` |
| `csv` | _(phase 2)_ | Spreadsheet export |

---

## 7. Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| CLI framework | `typer` (~0.12) | Type-annotated, Click-based, clean `--help` |
| Terminal UI | `rich` (~13.x) | Tables, trees, progress bars, color |
| Hashing | stdlib `hashlib` | No dependency, releases GIL, SHA-256 built-in |
| Concurrency | stdlib `concurrent.futures` | Thread pool fits CPU-bound hashlib |
| Path handling | stdlib `pathlib` | Idiomatic Python 3.11+ |
| Testing | `pytest` | Standard; `tmp_path` fixture for synthetic trees |
| Packaging | `pyproject.toml` + `pipx` | `pipx install fcompare` → isolated binary |

External dependencies are limited to `typer` and `rich`. Both have strong CVE histories.

---

## 8. Key Design Decisions

### 8.1 Relative-path keying for diff

Both directories are walked with paths relative to their respective roots. `src/config.py` in
`dir_a` is compared to `src/config.py` in `dir_b` regardless of absolute location. This is the
semantically correct comparison for directory mirroring and sync validation use cases.

### 8.2 Size-first filter in dupes

Avoids hashing the vast majority of files. On a large photo library this typically reduces hashing
work by 80–90%, because most files are uniquely sized.

### 8.3 No in-place deletion without `--confirm`

`--delete-keep` prints a plan only. Destructive action requires the additional `--confirm` flag.
This is a non-negotiable safety constraint — the utility will never delete files silently.

### 8.4 Renderer abstraction

The command layer produces typed result objects. The display layer consumes them. Command logic
never contains format strings or terminal escape codes.

### 8.5 `pipx` as distribution target

Keeps `fcompare` isolated from the system Python. Users run `pipx install fcompare` and get a
clean binary at `~/.local/bin/fcompare`. A Homebrew formula stub is a future option.

---

## 9. Performance Targets

| Operation | Target |
|---|---|
| Hash 10,000 files (mixed sizes) | < 3 seconds on Apple M-series |
| Directory diff of 50,000 files per side | < 15 seconds |
| Dupe scan of 100 GB photo library | < 5 minutes (size-filter eliminates most I/O) |

---

## 10. Future Use Cases (Planned Subcommands)

The subcommand pattern makes these zero-restructuring additions:

| Subcommand | Purpose |
|---|---|
| `fcompare permissions <dir>` | Report files with unexpected modes/ownership |
| `fcompare largest <dir>` | Top-N files by size (reuses walker) |
| `fcompare sync <src> <dst>` | rsync-like dry-run using the diff engine |
| `fcompare watch <dir1> <dir2>` | Live re-diff on FS events (watchdog integration) |

---

## 11. Out of Scope (v1)

- Content-level diff (line-by-line text diff) — use `diff` or `difftastic` for this
- GUI / menu bar app
- Network/remote directory comparison
- Cloud storage integration (S3, GDrive)
