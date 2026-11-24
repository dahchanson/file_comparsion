"""Duplicate file detection command: fcompare dupes <dir>."""

from collections import defaultdict
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from fcompare.display.progress import NoOpProgressReporter, ProgressReporter, make_reporter
from fcompare.display.renderer import OutputFormat, render_dupes
from fcompare.engine.hasher import PREHASH_MIN_SIZE, hash_records, prehash_records
from fcompare.engine.models import DupeGroup, FileRecord
from fcompare.engine.walker import WalkOptions, walk


class DeleteKeep(StrEnum):
    """Which copy to keep when generating a deletion plan."""

    oldest = "oldest"
    newest = "newest"
    first = "first"


def find_duplicates(
    root: Path,
    options: WalkOptions,
    min_size: int = 1,
    reporter: ProgressReporter | NoOpProgressReporter | None = None,
) -> list[DupeGroup]:
    """Return groups of files within ``root`` that share an identical SHA-256 digest.

    Uses a three-stage filter to minimize disk and SMB network I/O:
      1. Size grouping during the walk (zero I/O, no single-item list allocations
         for uniquely-sized files).
      2. Partial 64 KB head + 64 KB tail pre-hash for size-colliding files larger
         than ``PREHASH_MIN_SIZE`` (256 KB), eliminating fixed-size non-duplicates
         (e.g. ``.sparsebundle`` bands, RAW/WAV frames) with minimal reads.
      3. Full SHA-256 verification only for surviving candidates.

    Groups are sorted by wasted_bytes descending (largest waste first).
    Single-file groups are never returned.
    """
    rep = reporter or NoOpProgressReporter()

    first_by_size: dict[int, FileRecord] = {}
    dupes_by_size: dict[int, list[FileRecord]] = {}
    total_files = 0

    with rep.walk_phase("Scanning…") as tick:
        for r in walk(root, options):
            if r.size >= min_size:
                tick(r)
                total_files += 1
                sz = r.size
                existing_list = dupes_by_size.get(sz)
                if existing_list is not None:
                    existing_list.append(r)
                elif sz in first_by_size:
                    dupes_by_size[sz] = [first_by_size.pop(sz), r]
                else:
                    first_by_size[sz] = r

    first_by_size.clear()

    candidates = [record for records in dupes_by_size.values() for record in records]
    dupes_by_size.clear()

    rep.print_filter_summary(total=total_files, candidates=len(candidates))

    if not candidates:
        return []

    # Second pass: partial head+tail pre-hash for large size-collision candidates.
    small_candidates: list[FileRecord] = []
    large_candidates: list[FileRecord] = []
    for record in candidates:
        if record.size > PREHASH_MIN_SIZE:
            large_candidates.append(record)
        else:
            small_candidates.append(record)

    final_candidates = small_candidates
    if large_candidates:
        by_prehash: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
        for record, partial_digest in prehash_records(large_candidates):
            by_prehash[(record.size, partial_digest)].append(record)
        for group_records in by_prehash.values():
            if len(group_records) > 1:
                final_candidates.extend(group_records)

    if not final_candidates:
        return []

    cb = rep.hash_phase(
        total_files=len(final_candidates),
        total_bytes=sum(r.size for r in final_candidates),
    )
    hashed = hash_records(final_candidates, on_file_hashed=cb)

    # Third pass: group by full SHA-256 digest.
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for record in hashed:
        by_hash[record.sha256].append(record)

    groups = [
        DupeGroup(sha256=digest, files=sorted(records, key=lambda r: str(r.path)))
        for digest, records in by_hash.items()
        if len(records) > 1
    ]

    return sorted(groups, key=lambda g: g.wasted_bytes, reverse=True)


def deletion_plan(group: DupeGroup, keep: DeleteKeep) -> list[FileRecord]:
    """Return the files that would be deleted from a group under the given keep strategy."""
    if keep == DeleteKeep.oldest:
        keeper = min(group.files, key=lambda r: r.mtime)
    elif keep == DeleteKeep.newest:
        keeper = max(group.files, key=lambda r: r.mtime)
    else:  # first — alphabetically first absolute path
        keeper = min(group.files, key=lambda r: str(r.path))
    return [r for r in group.files if r.path != keeper.path]


def dupes_cmd(
    directory: Path = typer.Argument(
        ..., help="Directory to search for duplicates.", exists=True, file_okay=False
    ),
    fmt: OutputFormat = typer.Option(
        OutputFormat.rich, "--format", "-f", help="Output format."
    ),
    min_size: int = typer.Option(
        1, "--min-size", help="Skip files smaller than this many bytes.", min=0
    ),
    exclude: list[str] | None = typer.Option(
        None, "--exclude", "-e", help="Glob pattern to exclude (repeatable)."
    ),
    recurse: bool = typer.Option(True, "--recurse/--no-recurse", help="Walk subdirectories."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Follow symlinks."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-file hashing progress."
    ),
    delete_keep: DeleteKeep | None = typer.Option(
        None,
        "--delete-keep",
        help="Print a deletion plan keeping the oldest/newest/first copy.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Execute the deletion plan (requires --delete-keep). Files are permanently removed.",
    ),
) -> None:
    """Find files with identical SHA-256 hashes within a directory."""
    stderr_console = Console(stderr=True)
    if confirm and delete_keep is None:
        stderr_console.print("[red]--confirm requires --delete-keep.[/red]")
        raise typer.Exit(code=1)

    options = WalkOptions(
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        exclude_patterns=exclude or [],
    )

    use_verbose = verbose and fmt != OutputFormat.json
    reporter = make_reporter(use_verbose)

    if use_verbose:
        groups = find_duplicates(directory, options, min_size, reporter)
        reporter.stop()
    elif fmt == OutputFormat.json:
        groups = find_duplicates(directory, options, min_size, reporter)
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=stderr_console,
            transient=True,
        ) as progress:
            progress.add_task("Scanning for duplicates…", total=None)
            groups = find_duplicates(directory, options, min_size, reporter)

    render_dupes(groups, str(directory), fmt)

    if delete_keep is not None:
        _print_deletion_plan(groups, delete_keep, confirm, stderr_console)


def _print_deletion_plan(
    groups: list[DupeGroup],
    keep: DeleteKeep,
    confirm: bool,
    console: Console,
) -> None:
    to_delete = [f for g in groups for f in deletion_plan(g, keep)]

    if not to_delete:
        console.print("[dim]No files to delete.[/dim]")
        return

    if not confirm:
        console.print(
            f"\n[bold]Deletion plan[/bold] [dim](keeping {keep.value}; "
            "pass --confirm to execute)[/dim]"
        )
        for record in to_delete:
            console.print(f"  [red]would delete:[/red] {record.path}")
        return

    console.print(f"\n[bold red]Deleting {len(to_delete)} file(s)…[/bold red]")
    for record in to_delete:
        try:
            record.path.unlink()
            console.print(f"  [red]deleted:[/red] {record.path}")
        except OSError as exc:
            console.print(f"  [yellow]could not delete {record.path}: {exc}[/yellow]")
