"""Directory comparison command: fcompare diff <left> <right>."""

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from fcompare.display.progress import NoOpProgressReporter, ProgressReporter, make_reporter
from fcompare.display.renderer import OutputFormat, ShowFilter, render_diff
from fcompare.engine.hasher import hash_records
from fcompare.engine.models import DiffResult
from fcompare.engine.walker import WalkOptions, walk

_console = Console(stderr=True)


def compare_directories(
    left: Path,
    right: Path,
    options: WalkOptions,
    reporter: ProgressReporter | NoOpProgressReporter | None = None,
) -> DiffResult:
    """Hash all files in both directory trees and classify differences.

    Files are keyed by their path relative to their respective root, so
    ``src/config.py`` in ``left`` is compared against ``src/config.py`` in
    ``right`` regardless of absolute location.

    Args:
        left: Left-hand directory root.
        right: Right-hand directory root.
        options: Walker configuration (recursion, exclusions, symlinks).
        reporter: Progress reporter instance. Defaults to NoOpProgressReporter.

    Returns:
        DiffResult classifying every relative path as identical, modified,
        only-in-left, or only-in-right.
    """
    rep = reporter or NoOpProgressReporter()

    with rep.walk_phase(f"Scanning {left.name}…") as tick:
        left_records = []
        for r in walk(left, options):
            tick(r)
            left_records.append(r)

    with rep.walk_phase(f"Scanning {right.name}…") as tick:
        right_records = []
        for r in walk(right, options):
            tick(r)
            right_records.append(r)

    total_bytes = sum(r.size for r in left_records) + sum(r.size for r in right_records)
    cb = rep.hash_phase(
        total_files=len(left_records) + len(right_records),
        total_bytes=total_bytes,
    )

    left_hashed = hash_records(left_records, on_file_hashed=cb)
    right_hashed = hash_records(right_records, on_file_hashed=cb)

    left_map = {str(r.rel_path): r for r in left_hashed}
    right_map = {str(r.rel_path): r for r in right_hashed}

    result = DiffResult(left_root=left, right_root=right)
    all_keys = sorted(set(left_map) | set(right_map))

    for key in all_keys:
        in_left = key in left_map
        in_right = key in right_map

        if in_left and not in_right:
            result.only_in_left.append(left_map[key])
        elif in_right and not in_left:
            result.only_in_right.append(right_map[key])
        elif left_map[key].sha256 == right_map[key].sha256:
            result.identical.append((left_map[key], right_map[key]))
        else:
            result.modified.append((left_map[key], right_map[key]))

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

    # Progress output is suppressed for JSON format — pipe consumers must not
    # receive terminal escape sequences mixed into their data stream.
    use_verbose = verbose and fmt != OutputFormat.json
    reporter = make_reporter(use_verbose)

    if use_verbose:
        result = compare_directories(left, right, options, reporter)
        reporter.stop()
    elif fmt == OutputFormat.json:
        # JSON piped output: run silently with no spinner.
        result = compare_directories(left, right, options, reporter)
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_console,
            transient=True,
        ) as progress:
            progress.add_task("Hashing files…", total=None)
            result = compare_directories(left, right, options, reporter)

    render_diff(result, fmt, show)
