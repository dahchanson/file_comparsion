"""Duplicate file detection command: fcompare dupes <dir>."""

from collections import defaultdict
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from fcompare.display.renderer import OutputFormat, render_dupes
from fcompare.engine.hasher import hash_records
from fcompare.engine.models import DupeGroup, FileRecord
from fcompare.engine.walker import WalkOptions, walk

_console = Console(stderr=True)


class DeleteKeep(StrEnum):
    """Which copy to keep when generating a deletion plan."""

    oldest = "oldest"
    newest = "newest"
    first = "first"


def find_duplicates(
    root: Path,
    options: WalkOptions,
    min_size: int = 1,
) -> list[DupeGroup]:
    """Return groups of files within ``root`` that share an identical SHA-256 digest.

    Uses a two-pass algorithm: files are first grouped by size (zero I/O cost),
    then only size-collision candidates are hashed. This eliminates hashing work
    for the majority of files on typical directory trees.

    Groups are sorted by wasted_bytes descending (largest waste first).
    Single-file groups are never returned.

    Args:
        root: Directory to search.
        options: Walker configuration (recursion, exclusions, symlinks).
        min_size: Skip files strictly smaller than this many bytes.
    """
    all_records = [r for r in walk(root, options) if r.size >= min_size]

    # First pass: group by size — unique-sized files cannot be duplicates.
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for record in all_records:
        by_size[record.size].append(record)

    candidates = [
        record
        for records in by_size.values()
        if len(records) > 1
        for record in records
    ]

    if not candidates:
        return []

    hashed = hash_records(candidates)

    # Second pass: group by digest.
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
    """Return the files that would be deleted from a group under the given keep strategy.

    The one keeper is determined by strategy; all other copies are returned.
    """
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
    if confirm and delete_keep is None:
        _console.print("[red]--confirm requires --delete-keep.[/red]")
        raise typer.Exit(code=1)

    options = WalkOptions(
        recurse=recurse,
        follow_symlinks=follow_symlinks,
        exclude_patterns=exclude or [],
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        progress.add_task("Scanning for duplicates…", total=None)
        groups = find_duplicates(directory, options, min_size)

    render_dupes(groups, str(directory), fmt)

    if delete_keep is not None:
        _print_deletion_plan(groups, delete_keep, confirm)


def _print_deletion_plan(
    groups: list[DupeGroup], keep: DeleteKeep, confirm: bool
) -> None:
    to_delete = [f for g in groups for f in deletion_plan(g, keep)]

    if not to_delete:
        _console.print("[dim]No files to delete.[/dim]")
        return

    if not confirm:
        _console.print(
            f"\n[bold]Deletion plan[/bold] [dim](keeping {keep.value}; "
            "pass --confirm to execute)[/dim]"
        )
        for record in to_delete:
            _console.print(f"  [red]would delete:[/red] {record.path}")
        return

    _console.print(f"\n[bold red]Deleting {len(to_delete)} file(s)…[/bold red]")
    for record in to_delete:
        try:
            record.path.unlink()
            _console.print(f"  [red]deleted:[/red] {record.path}")
        except OSError as exc:
            _console.print(f"  [yellow]could not delete {record.path}: {exc}[/yellow]")
