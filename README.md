# fcompare

A fast macOS CLI utility for comparing directories and finding duplicate files by SHA-256 hash — optimized for both local Apple Silicon NVMe storage and network-mounted / encrypted APFS volumes over SMBfs.

## Installation

```bash
pipx install fcompare
```

Or install from source:

```bash
git clone https://github.com/dahchanson/file_comparsion.git
cd file_comparsion
pipx install .
```

## Commands

### `fcompare diff` — Compare two directories

Recursively compares files in two directories by relative path, size, and SHA-256 hash, reporting which files are identical, modified, or unique to one side.

- Files unique to either directory are classified immediately without reading file contents.
- When comparing across separate devices (e.g., local NVMe vs. an external drive or SMB-mounted APFS volume), walking and hashing run concurrently with per-device I/O tuning.

```bash
fcompare diff <left-dir> <right-dir> [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--format, -f [rich\|json]` | `rich` | Output format |
| `--show, -s [all\|modified\|unique\|same]` | `all` | Filter which categories to display |
| `--recurse / --no-recurse` | recurse | Walk subdirectories |
| `--exclude, -e PATTERN` | — | Glob pattern to skip (repeatable) |
| `--follow-symlinks` | off | Follow symbolic links |
| `--fast` | off | Skip hashing shared files whose byte sizes already differ |
| `--verbose, -v` | off | Show live scan and hashing progress bars |

**Examples:**

```bash
# Compare two directories, show everything
fcompare diff ~/Documents/v1 ~/Documents/v2

# Fast comparison against a network-mounted backup with live progress
fcompare diff ~/Photos /Volumes/Archive/Photos --fast --verbose

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
  [~] modified    src/config.py  ~ modified   a3f8c2d1…  →  91b4e077…
  [+] only left   src/legacy.py
  [-] only right  src/new_feature.py
────────────────────────────────────────────────────────
  Total 4 files  1 identical  1 modified  1 only in left  1 only in right
```

---

### `fcompare dupes` — Find duplicate files

Scans a directory for files with identical SHA-256 hashes using a three-stage filter to minimize disk and network I/O:
1. **Size grouping** during directory traversal (eliminates uniquely-sized files with zero reads).
2. **Partial head+tail pre-hash** (4 KB-aligned 64 KB start + 64 KB end) for large size-colliding files (`> 256 KB`), rejecting fixed-size non-duplicates (such as `.sparsebundle` bands or uncompressed media frames) before reading full files.
3. **Full SHA-256 verification** for surviving candidates.

```bash
fcompare dupes <directory> [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--format, -f [rich\|json]` | `rich` | Output format |
| `--min-size BYTES` | `1` | Skip files smaller than this many bytes |
| `--exclude, -e PATTERN` | — | Glob pattern to skip (repeatable) |
| `--recurse / --no-recurse` | recurse | Walk subdirectories |
| `--follow-symlinks` | off | Follow symbolic links |
| `--verbose, -v` | off | Show live scan, size-filter reduction, and hashing progress |
| `--delete-keep [oldest\|newest\|first]` | — | Print a deletion plan (does not delete) |
| `--confirm` | off | Execute the deletion plan (requires `--delete-keep`) |

**Examples:**

```bash
# Find all duplicate files with live progress
fcompare dupes ~/Downloads --verbose

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

### `fcompare benchmark` — Hardware & I/O Throughput Benchmark

Benchmarks SHA-256, SHA-512, and optional BLAKE3 (`pip install 'fcompare[benchmark]'`) across chunk sizes and I/O strategies on your hardware.

```bash
fcompare benchmark --size-mb 256 --iterations 3
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

# Run tests with coverage report (≥ 90% required)
make test-cov

# Build a distributable wheel
make build
```

## Safety notes

- `fcompare diff` is always read-only.
- `fcompare dupes --delete-keep` prints a **plan only** — no files are touched.
- Deletion requires the explicit `--confirm` flag. There is no undo.
