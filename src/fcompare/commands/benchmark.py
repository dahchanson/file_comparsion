"""Hash algorithm and I/O strategy benchmark: fcompare benchmark."""

import hashlib
import mmap
import os
import statistics
import tempfile
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

try:
    import blake3 as _blake3_mod

    HAS_BLAKE3 = True
except ImportError:
    HAS_BLAKE3 = False

_console = Console()

_CHUNK_SIZES: dict[str, int] = {
    "64 KB (current)": 64 * 1024,
    "1 MB": 1 * 1024 * 1024,
    "4 MB": 4 * 1024 * 1024,
}


# ---------------------------------------------------------------------------
# I/O strategies
# ---------------------------------------------------------------------------


def _bench_chunked(path: Path, algo: str, chunk_size: int, iterations: int) -> list[float]:
    """Explicit read() loop — current implementation strategy."""
    file_size = path.stat().st_size
    samples: list[float] = []
    for _ in range(iterations):
        h = _blake3_mod.blake3() if algo == "blake3" else hashlib.new(algo)  # type: ignore[union-attr]
        t0 = time.perf_counter()
        with path.open("rb") as fh:
            while chunk := fh.read(chunk_size):
                h.update(chunk)
        h.hexdigest()
        samples.append(_mb_per_sec(file_size, time.perf_counter() - t0))
    return samples


def _bench_file_digest(path: Path, algo: str, iterations: int) -> list[float]:
    """hashlib.file_digest() — C-level read loop, no Python overhead (3.11+)."""
    file_size = path.stat().st_size
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        with path.open("rb") as fh:
            hashlib.file_digest(fh, algo)
        samples.append(_mb_per_sec(file_size, time.perf_counter() - t0))
    return samples


def _bench_mmap(path: Path, algo: str, iterations: int) -> list[float]:
    """Memory-mapped I/O — kernel manages prefetch, no extra userspace copy."""
    file_size = path.stat().st_size
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        with path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                if algo == "blake3":
                    _blake3_mod.blake3(mm).hexdigest()  # type: ignore[union-attr]
                else:
                    h = hashlib.new(algo)
                    h.update(mm)
                    h.hexdigest()
        samples.append(_mb_per_sec(file_size, time.perf_counter() - t0))
    return samples


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mb_per_sec(file_size_bytes: int, elapsed: float) -> float:
    return (file_size_bytes / (1024 * 1024)) / elapsed


def _create_test_file(path: Path, size_mb: int) -> None:
    seed = os.urandom(1024 * 1024)  # 1 MB of random data, repeated
    with path.open("wb") as fh:
        for _ in range(size_mb):
            fh.write(seed)
        os.fsync(fh.fileno())


def _fmt_speed(samples: list[float]) -> str:
    med = statistics.median(samples)
    lo = min(samples)
    hi = max(samples)
    return f"{med:>7.1f}  [dim]({lo:.0f}–{hi:.0f})[/dim]"


def _speedup(samples: list[float], baseline: float) -> Text:
    ratio = statistics.median(samples) / baseline
    if ratio >= 1.10:
        style = "bold green"
    elif ratio >= 0.95:
        style = "dim"
    else:
        style = "red"
    return Text(f"{ratio:.2f}x", style=style)


def _table(title: str, subtitle: str = "") -> Table:
    hdr = f"[bold]{title}[/bold]" + (f"  [dim]{subtitle}[/dim]" if subtitle else "")
    _console.print(hdr)
    tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    tbl.add_column("Strategy", style="cyan", min_width=30)
    tbl.add_column("Median MB/s  (min–max)", justify="right")
    tbl.add_column("vs SHA-256 baseline", justify="right")
    return tbl


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def benchmark_cmd(
    size_mb: int = typer.Option(256, "--size-mb", help="Synthetic test file size in MB.", min=16),
    iterations: int = typer.Option(
        3, "--iterations", "-n", help="Runs per strategy (median is reported).", min=1, max=20
    ),
) -> None:
    """Benchmark SHA-256, SHA-512, and BLAKE3 across I/O strategies.

    Creates a synthetic test file and measures hashing throughput (MB/s)
    for each algorithm, chunk size, and I/O approach. Use the results to
    choose the fastest algorithm and I/O configuration for your hardware.

    Install BLAKE3 support first:
        pip install 'fcompare[benchmark]'
    """
    if not HAS_BLAKE3:
        _console.print(
            "[yellow]blake3 not installed — BLAKE3 rows will be skipped.[/yellow]\n"
            "  Install: [bold]pip install 'fcompare\\[benchmark]'[/bold]\n"
        )

    algos = ["sha256", "sha512"] + (["blake3"] if HAS_BLAKE3 else [])
    chunk_64k = _CHUNK_SIZES["64 KB (current)"]
    chunk_4mb = _CHUNK_SIZES["4 MB"]

    _console.print(
        f"[bold]fcompare benchmark[/bold]  "
        f"[dim]{size_mb} MB synthetic file · {iterations} iterations · "
        f"warm OS page cache after first run[/dim]"
    )
    _console.print(Rule())

    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "benchmark.bin"
        with _console.status(f"Creating {size_mb} MB test file…"):
            _create_test_file(test_file, size_mb)

        # ------------------------------------------------------------------ #
        # Establish sha256 baseline used throughout for relative comparisons  #
        # ------------------------------------------------------------------ #
        with _console.status("Warming cache and establishing baseline…"):
            baseline_samples = _bench_chunked(test_file, "sha256", chunk_64k, iterations)
        baseline_mb_s = statistics.median(baseline_samples)

        # ------------------------------------------------------------------ #
        # Section 1: Algorithm comparison at current 64 KB chunk size         #
        # ------------------------------------------------------------------ #
        tbl1 = _table("Section 1 — Algorithm comparison", "64 KB chunks")
        s1: dict[str, list[float]] = {}
        for algo in algos:
            with _console.status(f"  {algo} @ 64 KB …"):
                s1[algo] = _bench_chunked(test_file, algo, chunk_64k, iterations)
        for algo, samples in s1.items():
            tbl1.add_row(algo, _fmt_speed(samples), _speedup(samples, baseline_mb_s))
        _console.print(tbl1)
        _console.print()

        # ------------------------------------------------------------------ #
        # Section 2: Chunk size sweep per algorithm                           #
        # ------------------------------------------------------------------ #
        _console.print("[bold]Section 2 — Chunk size comparison[/bold]")
        for algo in algos:
            tbl2 = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            tbl2.add_column(f"  {algo}", style="cyan", min_width=22)
            tbl2.add_column("Median MB/s  (min–max)", justify="right")
            tbl2.add_column("vs sha256 64 KB", justify="right")
            for label, size in _CHUNK_SIZES.items():
                with _console.status(f"  {algo} @ {label} …"):
                    samples = _bench_chunked(test_file, algo, size, iterations)
                tbl2.add_row(label, _fmt_speed(samples), _speedup(samples, baseline_mb_s))
            _console.print(tbl2)
        _console.print()

        # ------------------------------------------------------------------ #
        # Section 3: I/O strategy comparison at 4 MB chunks                  #
        # ------------------------------------------------------------------ #
        tbl3 = _table("Section 3 — I/O strategy comparison", "4 MB chunks")
        s3: dict[str, list[float]] = {}

        for algo in ["sha256", "sha512"]:
            with _console.status(f"  {algo}  read() 4 MB …"):
                s3[f"{algo}  read() 4 MB"] = _bench_chunked(
                    test_file, algo, chunk_4mb, iterations
                )
            with _console.status(f"  {algo}  file_digest() …"):
                s3[f"{algo}  file_digest()"] = _bench_file_digest(test_file, algo, iterations)
            with _console.status(f"  {algo}  mmap …"):
                s3[f"{algo}  mmap"] = _bench_mmap(test_file, algo, iterations)

        if HAS_BLAKE3:
            with _console.status("  blake3  read() 4 MB …"):
                s3["blake3  read() 4 MB"] = _bench_chunked(
                    test_file, "blake3", chunk_4mb, iterations
                )
            with _console.status("  blake3  mmap …"):
                s3["blake3  mmap"] = _bench_mmap(test_file, "blake3", iterations)

        for label, samples in s3.items():
            tbl3.add_row(label, _fmt_speed(samples), _speedup(samples, baseline_mb_s))
        _console.print(tbl3)
        _console.print()

        # ------------------------------------------------------------------ #
        # Summary                                                             #
        # ------------------------------------------------------------------ #
        all_results = {**s1, **s3}
        winner = max(all_results, key=lambda k: statistics.median(all_results[k]))
        winner_speed = statistics.median(all_results[winner])

        _console.print(Rule())
        _console.print(
            f"  [bold green]Fastest:[/bold green] [cyan]{winner.strip()}[/cyan]  "
            f"[bold]{winner_speed:.1f} MB/s[/bold]  "
            f"([green]{winner_speed / baseline_mb_s:.2f}x[/green] vs SHA-256 64 KB)"
        )
        _console.print(
            f"  [dim]SHA-256 baseline: {baseline_mb_s:.1f} MB/s[/dim]"
        )
        _console.print()
