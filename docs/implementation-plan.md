# fcompare — Implementation Plan

**Date:** 2026-05-31
**Status:** Active
**Depends on:** design.md

---

## Phase 1 — Project Scaffold & Core Engine

### 1.1 Project Scaffold

- [ ] Create `pyproject.toml` with:
  - Package name `fcompare`, version `0.1.0`
  - Python `>=3.11`
  - Dependencies: `typer~=0.12`, `rich~=13.0`
  - Dev dependencies: `pytest`, `pytest-cov`, `mypy`, `ruff`
  - Entry point: `fcompare = "fcompare.cli:app"`
- [ ] Create `src/fcompare/__init__.py` (empty, version string only)
- [ ] Create `src/fcompare/__main__.py` (calls `cli.app()`)
- [ ] Create `src/fcompare/cli.py` (bare Typer app, no subcommands yet)
- [ ] Create `Makefile` with targets: `lint`, `test`, `install-dev`
- [ ] Create `.gitignore`
- [ ] Initialize git repo

**Acceptance:** `pipx install -e .` succeeds; `fcompare --help` prints usage.

---

### 1.2 Data Models (`engine/models.py`)

- [ ] `FileRecord` dataclass:
  - `path: Path` — absolute path
  - `rel_path: Path` — path relative to walk root
  - `sha256: str` — hex digest (empty string until hashed)
  - `size: int` — bytes
  - `mtime: float` — modification timestamp
- [ ] `DiffResult` dataclass:
  - `only_in_left: list[FileRecord]`
  - `only_in_right: list[FileRecord]`
  - `modified: list[tuple[FileRecord, FileRecord]]` — (left, right) pairs
  - `identical: list[tuple[FileRecord, FileRecord]]`
  - `left_root: Path`
  - `right_root: Path`
- [ ] `DupeGroup` dataclass:
  - `sha256: str`
  - `files: list[FileRecord]`
  - Property `wasted_bytes: int` — `size * (len(files) - 1)`
- [ ] Unit tests: construction, equality, `wasted_bytes` calculation

**Acceptance:** `pytest tests/unit/test_models.py` passes.

---

### 1.3 Walker (`engine/walker.py`)

- [ ] `WalkOptions` dataclass:
  - `recurse: bool = True`
  - `follow_symlinks: bool = False`
  - `include_hidden: bool = False`
  - `exclude_patterns: list[str] = []`
- [ ] `walk(root: Path, options: WalkOptions) -> Iterator[FileRecord]`
  - Uses `Path.rglob("*")` or `Path.iterdir()` based on `recurse`
  - Applies symlink and hidden-file filters
  - Applies each exclude pattern via `fnmatch`
  - Catches `PermissionError` per file; emits warning via `warnings.warn`, continues
  - Sets `rel_path` relative to `root`
  - Does NOT hash — returns records with `sha256 = ""`
- [ ] Unit tests (using `tmp_path`):
  - `test_walk_returns_all_files_in_flat_dir`
  - `test_walk_recurse_false_excludes_subdirectories`
  - `test_walk_skips_hidden_files_by_default`
  - `test_walk_include_hidden_when_flag_set`
  - `test_walk_exclude_pattern_filters_matching_files`
  - `test_walk_skips_symlinks_by_default`
  - `test_walk_follow_symlinks_when_flag_set`
  - `test_walk_permission_error_skips_and_warns`

**Acceptance:** `pytest tests/unit/test_walker.py` passes.

---

### 1.4 Hasher (`engine/hasher.py`)

- [ ] `CHUNK_SIZE: int = 65536` constant (64 KB)
- [ ] `hash_file(path: Path) -> str`
  - Opens file in binary mode, streams in `CHUNK_SIZE` chunks
  - Returns lowercase hex SHA-256 digest
  - Raises `OSError` on read failure (caller handles)
- [ ] `hash_records(records: list[FileRecord], max_workers: int | None = None) -> list[FileRecord]`
  - Submits `hash_file` calls to `ThreadPoolExecutor`
  - Returns new `FileRecord` list with `sha256` populated
  - Skips records where `hash_file` raises; logs warning with path
  - `max_workers` defaults to `os.cpu_count()`
- [ ] Unit tests (using `tmp_path`):
  - `test_hash_file_returns_correct_sha256`
  - `test_hash_file_is_consistent_across_calls`
  - `test_hash_file_large_file_streams_correctly` (write 5 MB synthetic file)
  - `test_hash_records_populates_all_records`
  - `test_hash_records_skips_unreadable_file`

**Acceptance:** `pytest tests/unit/test_hasher.py` passes.

---

## Phase 2 — `diff` Command

### 2.1 Diff Logic (`commands/diff.py`)

- [ ] `compare_directories(left: Path, right: Path, options: WalkOptions) -> DiffResult`
  - Walk left and right into `FileRecord` lists
  - Hash all records (parallel)
  - Build `dict[str, FileRecord]` keyed by `str(rel_path)` for each side
  - Classify:
    - Key in left only → `only_in_left`
    - Key in right only → `only_in_right`
    - Key in both, same hash → `identical`
    - Key in both, different hash → `modified`
  - Return `DiffResult`
- [ ] Typer subcommand `diff`:
  - Arguments: `left: Path`, `right: Path`
  - Options: `--format`, `--show`, `--no-recurse`, `--exclude`, `--follow-symlinks`
  - Validates both paths exist and are directories
  - Calls `compare_directories`, passes result to renderer

### 2.2 Rich Renderer — Diff (`display/rich_renderer.py`)

- [ ] `render_diff(result: DiffResult, show: str) -> None`
  - Header: `Directory Comparison: <left>  ↔  <right>`
  - Rows filtered by `show` flag (`all|modified|unique|same`)
  - Color coding:
    - `✓ same` → green
    - `~ modified` → yellow
    - `+ only in left` → blue
    - `- only in right` → red
  - Footer summary line: file counts per category
  - Progress bar during hashing (Rich `Progress`)

### 2.3 JSON Renderer — Diff (`display/json_renderer.py`)

- [ ] `render_diff_json(result: DiffResult) -> None`
  - Prints JSON to stdout:
    ```json
    {
      "left": "/abs/path",
      "right": "/abs/path",
      "summary": {"identical": N, "modified": N, "only_left": N, "only_right": N},
      "modified": [{"rel_path": "...", "left_sha256": "...", "right_sha256": "..."}],
      "only_left": ["rel_path", ...],
      "only_right": ["rel_path", ...]
    }
    ```

### 2.4 Integration Tests (`tests/integration/test_diff_command.py`)

- [ ] Fixture: `make_dir_tree(tmp_path, spec: dict)` helper that creates files with known content
- [ ] `test_diff_identical_directories_reports_all_same`
- [ ] `test_diff_detects_modified_file`
- [ ] `test_diff_detects_file_only_in_left`
- [ ] `test_diff_detects_file_only_in_right`
- [ ] `test_diff_recurse_false_ignores_subdirectory_files`
- [ ] `test_diff_exclude_pattern_filters_files`
- [ ] `test_diff_json_format_output_is_valid_json`
- [ ] `test_diff_empty_directories`

**Acceptance:** `fcompare diff <dir1> <dir2>` produces correct colored output; `--format=json` produces valid JSON.

---

## Phase 3 — `dupes` Command

### 3.1 Dupe Logic (`commands/dupes.py`)

- [ ] `find_duplicates(root: Path, options: WalkOptions, min_size: int = 1) -> list[DupeGroup]`
  - Walk root into `FileRecord` list
  - Filter by `size >= min_size`
  - Group by size: `dict[int, list[FileRecord]]`
  - Discard size groups with only 1 member (cannot be duplicates)
  - Hash remaining records (parallel)
  - Group by SHA-256: `dict[str, list[FileRecord]]`
  - Return groups with 2+ members, sorted by `wasted_bytes` descending
- [ ] Typer subcommand `dupes`:
  - Argument: `directory: Path`
  - Options: `--format`, `--min-size`, `--exclude`, `--no-recurse`, `--delete-keep`, `--confirm`
  - `--delete-keep` without `--confirm` prints plan only (never deletes)
  - `--delete-keep` + `--confirm` deletes the non-kept files

### 3.2 Rich Renderer — Dupes (`display/rich_renderer.py`)

- [ ] `render_dupes(groups: list[DupeGroup]) -> None`
  - Header: `Duplicate Files in: <dir>`
  - Per group: truncated SHA-256, copy count, wasted bytes
  - Per file in group: indented absolute path
  - Footer summary: group count, redundant file count, total reclaimable bytes

### 3.3 JSON Renderer — Dupes (`display/json_renderer.py`)

- [ ] `render_dupes_json(groups: list[DupeGroup]) -> None`
  - Prints JSON:
    ```json
    {
      "summary": {"groups": N, "redundant_files": N, "reclaimable_bytes": N},
      "groups": [
        {"sha256": "...", "size": N, "files": ["/abs/path", ...]},
        ...
      ]
    }
    ```

### 3.4 Integration Tests (`tests/integration/test_dupes_command.py`)

- [ ] `test_dupes_finds_exact_duplicate_files`
- [ ] `test_dupes_ignores_files_below_min_size`
- [ ] `test_dupes_no_false_positives_different_content_same_size`
- [ ] `test_dupes_single_file_not_reported`
- [ ] `test_dupes_recurse_false_ignores_subdirs`
- [ ] `test_dupes_exclude_pattern_filters_files`
- [ ] `test_dupes_json_output_is_valid_json`
- [ ] `test_dupes_delete_keep_without_confirm_does_not_delete`
- [ ] `test_dupes_empty_directory`

**Acceptance:** `fcompare dupes <dir>` correctly identifies duplicates; size-first optimization confirmed by adding an instrumented code path in tests.

---

## Phase 4 — Packaging & Distribution

- [ ] `pyproject.toml` — finalize classifiers, license, URLs
- [ ] `README.md` — installation, quickstart, all flags documented
- [ ] `Makefile` targets:
  - `make install-dev` — `pip install -e ".[dev]"`
  - `make lint` — `ruff check src/ tests/ && mypy src/`
  - `make test` — `pytest --cov=src/fcompare --cov-report=term-missing`
  - `make build` — `python -m build`
- [ ] Smoke test: `pipx install dist/fcompare-0.1.0-py3-none-any.whl`
- [ ] Verify `fcompare --help`, `fcompare diff --help`, `fcompare dupes --help` all render correctly
- [ ] macOS path: confirm `~/.local/bin` is on `$PATH` after `pipx install`

---

## Phase 5 — Quality Gates

Run before declaring v0.1.0 complete:

```bash
make lint          # zero ruff errors, zero mypy errors
make test          # ≥ 90% line coverage on src/fcompare/
fcompare diff ~/Documents ~/Downloads           # manual smoke test
fcompare dupes ~/Downloads --format=json | jq . # JSON output valid
fcompare dupes ~/Downloads --min-size=1048576   # large files only
```

---

## Sequencing & Estimates

| Phase | Est. Time | Blocking? |
|---|---|---|
| 1.1 Scaffold | 30 min | Blocks all |
| 1.2 Models | 30 min | Blocks 2.1, 3.1 |
| 1.3 Walker | 45 min | Blocks 2.1, 3.1 |
| 1.4 Hasher | 45 min | Blocks 2.1, 3.1 |
| 2.1–2.4 diff command | 2 hr | Independent of phase 3 |
| 3.1–3.4 dupes command | 2 hr | Independent of phase 2 |
| 4 Packaging | 1 hr | Depends on 2 + 3 |
| 5 Quality gates | 30 min | Depends on 4 |

**Total estimate: ~8 hours of focused implementation.**

Phases 2 and 3 can be developed in parallel once Phase 1 is complete.
