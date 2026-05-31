# I/O Optimization Plan for `engine/hasher.py`

**Date:** 2026-05-31
**Status:** Active — informed by `fcompare benchmark` results on Apple Silicon (M-series)

---

## Benchmark Results (256 MB synthetic file, 3 iterations, warm OS page cache)

```
Section 1 — Algorithm comparison  (64 KB chunks)
  sha256      2,129 MB/s   1.00x  ← baseline
  blake3      1,624 MB/s   0.76x
  sha512      1,304 MB/s   0.61x

Section 2 — Chunk size sweep
  sha256  64 KB    2,117 MB/s   1.00x  (current)
  sha256  1 MB     2,138 MB/s   1.01x
  sha256  4 MB     1,946 MB/s   0.92x

  blake3  64 KB    1,624 MB/s   0.77x
  blake3  1 MB     1,659 MB/s   0.78x
  blake3  4 MB     1,544 MB/s   0.73x

Section 3 — I/O strategy  (4 MB chunks)
  sha256  read() 4 MB    1,966 MB/s   0.93x
  sha256  file_digest()  2,107 MB/s   0.99x
  sha256  mmap           2,147 MB/s   1.01x   ← fastest SHA-256
  blake3  read() 4 MB    1,543 MB/s   0.73x
  blake3  mmap           1,621 MB/s   0.76x
```

---

## What the Results Mean

### Why SHA-256 wins on Apple Silicon

Apple M-series chips implement dedicated SHA-2 hardware units via ARMv8.2 SHA
extensions (`SHA256H`, `SHA256H2`, `SHA256SU0`, `SHA256SU1` instructions).
Python's `hashlib` reaches these through OpenSSL, which detects and uses them
automatically. The result is ~2.1 GB/s — well above typical software SHA implementations.

SHA-512 is slower on this hardware because the SHA-512 hardware units (`SHA512H`,
`SHA512SU0`) are optimized for Bitcoin-adjacent use cases and do not benefit from
the same silicon area investment. This is the opposite of the theoretical expectation
for 64-bit CPUs.

BLAKE3 runs in software at ~1.6 GB/s using ARM Neon SIMD. Respectable, but cannot
compete with dedicated SHA-256 hardware on this specific chip.

### Is the tool I/O-bound?

At 2.1 GB/s (SHA-256) the compute rate approaches Apple Silicon NVMe sequential
read speed (~7 GB/s). With `os.cpu_count()` parallel workers (default), multiple
files are hashed concurrently. The effective per-worker compute speed therefore
sits well below the storage ceiling — meaning the tool is likely **I/O-bound**
on large real-world directories where files are not in the OS page cache.

---

## Recommended Changes

### 1. Increase `CHUNK_SIZE` from 64 KB to 1 MB (Low effort, ~1% gain)

The current 64 KB was conservative (appropriate for HDDs). On NVMe SSDs, 1 MB
chunks reduce per-read syscall overhead with no memory pressure downside since
the previous chunk is released before the next is read.

**4 MB chunks are slower** (~0.92x) because the larger buffer stays pinned in
CPU L3/DRAM longer, increasing memory pressure under parallel hashing.

```python
# engine/hasher.py — change the constant
CHUNK_SIZE: int = 1 * 1024 * 1024   # 1 MB — optimal for NVMe on Apple Silicon
```

**Do not change:** The dynamic is file-size-dependent and hardware-dependent.
A future `config.py` constant with env-var override is the right path if tuning
is needed across environments.

---

### 2. Offer `hashlib.file_digest()` as an alternative read path (Low effort, ~1% gain)

Python 3.11 added `hashlib.file_digest(fh, algo)` — a C-level implementation
that avoids the Python `while chunk := fh.read()` loop overhead. It's
approximately equivalent to mmap for SHA-256 (~2,107 MB/s vs ~2,147 MB/s) but
requires no additional memory mapping.

```python
def hash_file(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()
```

**Trade-off:** `file_digest()` does not support blake3 or any non-`hashlib`
algorithm. If we ever switch algorithms, we'd revert to the manual loop.

---

### 3. Memory-mapped I/O for large files (Medium effort, ~1% gain for SHA-256)

mmap is the fastest single-threaded option for SHA-256 on this hardware
(2,147 MB/s, ~1% over 64 KB read()). Benefits increase on cold cache because
the kernel can apply readahead optimizations without userspace intervention.

The tradeoff: mmap maps the entire file into virtual address space. For files
larger than available RAM, the kernel will page-fault in regions on demand
(which is fine), but for thousands of files in parallel, the combined virtual
address space usage can be significant on memory-constrained machines.

**Proposed implementation:**

```python
import mmap

# Threshold above which mmap is preferred over read().
# Below this size, the mmap overhead (fd open, MAP_SHARED setup) is not amortized.
_MMAP_THRESHOLD_BYTES: int = 64 * 1024 * 1024  # 64 MB

def hash_file(path: Path) -> str:
    size = path.stat().st_size
    if size >= _MMAP_THRESHOLD_BYTES:
        with path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return hashlib.sha256(mm).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
```

**Risk:** `mmap.mmap(..., 0)` with length=0 maps the entire file. For files
that grow between `stat()` and `mmap()` (rare but possible), the length must
be re-validated. Wrap in try/except `ValueError`/`OSError`.

---

## What NOT to Implement

### SHA-512

**Do not switch to SHA-512.** Benchmark result: 0.61x vs SHA-256 on Apple Silicon.
The ARM SHA-256 hardware is faster than SHA-512 hardware on M-series chips. This
is the opposite of what theoretical analysis of 64-bit CPUs predicts, and confirms
that hardware characterization must come before algorithm selection.

### BLAKE3 as default

**Do not switch to BLAKE3 as the default.** Benchmark result: 0.76x vs SHA-256
on Apple Silicon. BLAKE3 is a software implementation and cannot compete with
dedicated hardware. BLAKE3 would be the right default on hardware *without*
SHA-2 acceleration (older x86, embedded ARM without SHA extensions), but that
is not the target platform for this tool.

BLAKE3 remains available via the `[benchmark]` optional dependency for users
who want to experiment.

### GPU hashing

Not viable on macOS: CUDA unavailable, OpenCL removed, Metal Compute would
require writing MSL shaders with no off-the-shelf Python support, and the
data-transfer overhead to/from GPU would negate gains for streaming file I/O.

---

## Implementation Priority

| Change | Effort | Expected gain | Recommendation |
|---|---|---|---|
| Increase `CHUNK_SIZE` to 1 MB | 1 line | ~1% | **Do it** |
| `hashlib.file_digest()` | 3 lines | ~1% | **Do it** (simplifies code) |
| mmap for large files (≥64 MB) | ~20 lines | ~1–5% on cold cache | Optional |
| SHA-512 | 1 line | **−39%** | Do not do |
| BLAKE3 as default | small | **−24%** | Do not do |

---

## Caveats

All results are warm OS page cache. For cold cache (first access, files not
recently read), the storage I/O bottleneck (~7 GB/s NVMe) dominates regardless
of algorithm choice. The ~2% gains shown above are effectively noise at the
storage ceiling.

The meaningful optimizations for cold-cache performance are:
- More parallel workers (already implemented via `ThreadPoolExecutor`)
- The size-first pre-filter in `dupes` (already implemented — eliminates ~80-90%
  of hashing work on typical photo libraries)

Run `fcompare benchmark --size-mb 512` periodically on new hardware to re-validate
assumptions.
