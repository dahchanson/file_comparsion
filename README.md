# fcompare

A macOS CLI utility for comparing directories and finding duplicate files by SHA-256 hash.

## Installation

```bash
pipx install fcompare
```

Or install from source:

```bash
git clone <repo>
cd fcompare
pipx install .
```

## Commands

### `fcompare diff` — Compare two directories

Recursively compares all files in two directories by SHA-256 hash and reports which files are
identical, modified, or unique to one side.

```bash
fcompare diff <left-dir> <right-dir> [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--format [rich\|json]` | `rich` | Output format |
| `--show [all\|modified\|unique\|same]` | `all` | Filter which categories to display |
| `--recurse / --no-recurse` | recurse | Walk subdirectories |
| `--exclude PATTERN` | — | Glob pattern to skip (repeatable) |
| `--follow-symlinks` | off | Follow symbolic links |

**Examples:**

```bash
# Compare two directories, show everything
fcompare diff ~/Documents/v1 ~/Documents/v2

# Show only files that differ
fcompare diff ~/project-a ~/project-b --show modified

# Machine-readable output, exclude build artifacts
fcompare diff src/ backup/src/ --format json --exclude "*.pyc" --exclude "__pycache__"

# Pipe JSON output to jq
fcompare diff dir1/ dir2/ --format json | jq '.summary'
```

**Rich output example:**

```
Directory Comparison: /path/to/dir1  ↔  /path/to/dir2
────────────────────────────────────────────────────────
  [✓] same        docs/readme.md
  [~] modified    src/config.py
  [+] only left   src/legacy.py
  [-] only right  src/new_feature.py
────────────────────────────────────────────────────────
  Total 4 files  1 identical  1 modified  1 only in left  1 only in right
```

---

### `fcompare dupes` — Find duplicate files

Scans a directory for files with identical SHA-256 hashes. Uses a size-first pre-filter to
minimize I/O: only files that share a size with another file are actually hashed.

```bash
fcompare dupes <directory> [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--format [rich\|json]` | `rich` | Output format |
| `--min-size BYTES` | `1` | Skip files smaller than this many bytes |
| `--exclude PATTERN` | — | Glob pattern to skip (repeatable) |
| `--recurse / --no-recurse` | recurse | Walk subdirectories |
| `--follow-symlinks` | off | Follow symbolic links |
| `--delete-keep [oldest\|newest\|first]` | — | Print a deletion plan (does not delete) |
| `--confirm` | off | Execute the deletion plan (requires `--delete-keep`) |

**Examples:**

```bash
# Find all duplicate files
fcompare dupes ~/Downloads

# Find duplicate files larger than 1 MB
fcompare dupes ~/Photos --min-size 1048576

# JSON output for scripting
fcompare dupes ~/Documents --format json | jq '.summary.reclaimable_bytes'

# Preview what would be deleted, keeping the newest copy
fcompare dupes ~/Photos --delete-keep newest

# Actually delete the redundant copies (keep newest)
fcompare dupes ~/Photos --delete-keep newest --confirm
```

**Rich output example:**

```
Duplicate Files in: /path/to/dir
──────────────────────────────────────────
  SHA-256: a3f8c2d1…9c12  (3 copies, 2.4 MB wasted)
    /photos/vacation/IMG_001.jpg
    /photos/backup/IMG_001.jpg
    /archive/old/IMG_001.jpg

  SHA-256: b91d4e77…3f20  (2 copies, 512.0 KB wasted)
    /docs/report.pdf
    /docs/report_final.pdf

──────────────────────────────────────────
  2 dupe groups  │  3 redundant files  │  2.9 MB reclaimable
```

---

## Development

```bash
# Install with dev dependencies
make install-dev

# Run linter and type checker
make lint

# Run test suite
make test

# Run tests with coverage report
make test-cov

# Build a distributable wheel
make build
```

## Safety notes

- `fcompare diff` is always read-only.
- `fcompare dupes --delete-keep` prints a **plan only** — no files are touched.
- Deletion requires the explicit `--confirm` flag. There is no undo.
