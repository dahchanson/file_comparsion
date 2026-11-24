"""Directory comparison command: fcompare diff <left> <right>."""

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from fcompare.display.progress import NoOpProgressReporter, ProgressReporter, make_reporter
from fcompare.display.renderer import OutputFormat, ShowFilter, render_diff
from fcompare.engine.hasher import hash_records
from fcompare.engine.models import DiffResult, FileRecord
from fcompare.engine.walker import WalkOptions, walk


def _walk_side(
    root: Path,
    options: WalkOptions,
    rep: ProgressReporter | NoOpProgressReporter,
    label: str,
) -> list[FileRecord]:
    with rep.walk_phase(label) as tick:
        out: list[FileRecord] = []
        for r in walk(root, options):
            tick(r)
            out.append(r)
        return out


def _same_device(left: Path, right: Path) -> bool:
    try:
        return os.stat(left).st_dev == os.stat(right).st_dev
    except OSError:
        return True


def compare_directories(
    left: Path,
    right: Path,
    options: WalkOptions,
    reporter: ProgressReporter | NoOpProgressReporter | None = None,
    *,
    need_modified_hashes: bool = True,
) -> DiffResult:
    """Compare two directory trees by relative path, size, and SHA-256 digest.

    Optimizations:
      - Files unique to ``left`` or ``right`` are classified immediately from
        walk metadata without reading or hashing file contents.
      - When ``need_modified_hashes`` is False (e.g. ``--show same``,
        ``--show unique``, or ``--fast``), shared paths whose byte sizes differ
        are classified immediately as ``modified`` with zero disk/network reads.
      - When ``left`` and ``right`` reside on separate devices (e.g. local NVMe
        vs an encrypted APFS volume over SMBfs), walking and hashing run
        concurrently across both storage channels; when on the same device,
        hashing runs in a single unified pool to avoid head/band contention.
    """
    rep = reporter or NoOpProgressReporter()
    same_dev = _same_device(left, right)

    if same_dev:
        left_records = _walk_side(left, options, rep, f"Scanning {left.name}…")
        right_records = _walk_side(right, options, rep, f"Scanning {right.name}…")
    else:
        with ThreadPoolExecutor(max_workers=2) as walk_pool:
            fut_l = walk_pool.submit(_walk_side, left, options, rep, f"Scanning {left.name}…")
            fut_r = walk_pool.submit(_walk_side, right, options, rep, f"Scanning {right.name}…")
            left_records = fut_l.result()
            right_records = fut_r.result()

    left_by_rel = {str(r.rel_path): r for r in left_records}
    right_by_rel = {str(r.rel_path): r for r in right_records}

    all_keys = sorted(left_by_rel.keys() | right_by_rel.keys())

    left_to_hash: list[FileRecord] = []
    right_to_hash: list[FileRecord] = []
    fast_modified: dict[str, tuple[FileRecord, FileRecord]] = {}

    for key in all_keys:
        l_rec = left_by_rel.get(key)
        r_rec = right_by_rel.get(key)
        if l_rec is None or r_rec is None:
            # Unique to one side — SHA-256 is never needed.
            continue
        if not need_modified_hashes and l_rec.size != r_rec.size:
            fast_modified[key] = (l_rec, r_rec)
            continue
        left_to_hash.append(l_rec)
        right_to_hash.append(r_rec)

    hashed_left_map: dict[str, FileRecord] = {}
    hashed_right_map: dict[str, FileRecord] = {}

    total_to_hash = len(left_to_hash) + len(right_to_hash)
    if total_to_hash > 0:
        total_bytes = sum(r.size for r in left_to_hash) + sum(r.size for r in right_to_hash)
        cb = rep.hash_phase(total_files=total_to_hash, total_bytes=total_bytes)

        if same_dev:
            # Single device: hash together in one pool so inode/LPT ordering is global.
            left_paths = {r.path for r in left_to_hash}
            combined_hashed = hash_records(left_to_hash + right_to_hash, on_file_hashed=cb)
            for r in combined_hashed:
                if r.path in left_paths:
                    hashed_left_map[str(r.rel_path)] = r
                else:
                    hashed_right_map[str(r.rel_path)] = r
        else:
            # Distinct devices (e.g. NVMe + SMB): run independent per-profile pools concurrently.
            cb_lock = threading.Lock()

            def thread_safe_cb(rec: FileRecord, _cb: Callable[[FileRecord], None] = cb) -> None:
                with cb_lock:
                    _cb(rec)

            with ThreadPoolExecutor(max_workers=2) as side_pool:
                fut_lh = side_pool.submit(hash_records, left_to_hash, None, thread_safe_cb)
                fut_rh = side_pool.submit(hash_records, right_to_hash, None, thread_safe_cb)
                hashed_left_map = {str(r.rel_path): r for r in fut_lh.result()}
                hashed_right_map = {str(r.rel_path): r for r in fut_rh.result()}

    result = DiffResult(left_root=left, right_root=right)

    for key in all_keys:
        in_left_orig = key in left_by_rel
        in_right_orig = key in right_by_rel

        if in_left_orig and not in_right_orig:
            result.only_in_left.append(left_by_rel[key])
        elif in_right_orig and not in_left_orig:
            result.only_in_right.append(right_by_rel[key])
        elif key in fast_modified:
            result.modified.append(fast_modified[key])
        else:
            lh = hashed_left_map.get(key)
            rh = hashed_right_map.get(key)
            if lh is None and rh is None:
                continue
            if lh is None and rh is not None:
                result.only_in_right.append(rh)
            elif rh is None and lh is not None:
                result.only_in_left.append(lh)
            elif lh is not None and rh is not None:
                if lh.sha256 == rh.sha256:
                    result.identical.append((lh, rh))
                else:
                    result.modified.append((lh, rh))

    return result


def diff_cmd(
    left: Path = typer.Argument(..., help="Left directory.", exists=True, file_okay=False),
    right: Path = typer.Argument(..., help="Right directory.", exists=True, file_okay=False),
    fmt: OutputFormat = typer.Option(
        OutputFormat.rich, "--format", "-f", help="Output format."
    ),
    show: ShowFilter = typer.Option(
        ShowFilter.all, "--show", "-s", help="Which file categories to display."
    ),
    recurse: bool = typer.Option(True, "--recurse/--no-recurse", help="Walk subdirectories."),
    exclude: list[str] | None = typer.Option(
        None, "--exclude", "-e", help="Glob pattern to exclude (repeatable)."
    ),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Follow symlinks."),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Skip hashing shared files whose byte sizes already differ.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-file hashing progress."
    ),
) -> None:
    """Compare two directories by SHA-256 hash and show differences."""
    options = WalkOptions(
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        exclude_patterns=exclude or [],
    )

    need_modified_hashes = not fast and show in (ShowFilter.all, ShowFilter.modified)

    # Progress output is suppressed for JSON format — pipe consumers must not
    # receive terminal escape sequences mixed into their data stream.
    use_verbose = verbose and fmt != OutputFormat.json
    reporter = make_reporter(use_verbose)

    if use_verbose:
        result = compare_directories(
            left, right, options, reporter, need_modified_hashes=need_modified_hashes
        )
        reporter.stop()
    elif fmt == OutputFormat.json:
        # JSON piped output: run silently with no spinner.
        result = compare_directories(
            left, right, options, reporter, need_modified_hashes=need_modified_hashes
        )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=Console(stderr=True),
            transient=True,
        ) as progress:
            progress.add_task("Hashing files…", total=None)
            result = compare_directories(
                left, right, options, reporter, need_modified_hashes=need_modified_hashes
            )

    render_diff(result, fmt, show)
