"""Progress reporting for multi-phase hashing operations.

Two implementations share the same interface:
  ProgressReporter     — Rich live progress bars, used when --verbose is set.
  NoOpProgressReporter — Silent no-op, used when --verbose is not set.

Commands instantiate one via make_reporter(verbose) and pass it through;
no branching is required at each call site.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from fcompare.engine.models import FileRecord

_console = Console(stderr=True)


class ProgressReporter:
    """Rich-backed progress reporter for walk + hash phases."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
            console=_console,
            transient=False,
        )
        self._progress.start()

    @contextmanager
    def walk_phase(self, label: str) -> Generator[Callable[[FileRecord], None], None, None]:
        """Context manager for the directory-walk phase.

        Yields a tick() callable. Call tick(record) for each file discovered
        to keep the live count updated. The task is marked complete on exit.
        """
        task: TaskID = self._progress.add_task(label, total=None)
        count = 0

        def tick(record: FileRecord) -> None:
            nonlocal count
            count += 1
            self._progress.update(
                task,
                description=f"{label}  [dim]{count:,} files[/dim]",
            )

        try:
            yield tick
        finally:
            self._progress.update(task, total=count, completed=count)

    def hash_phase(
        self,
        total_files: int,
        total_bytes: int,
    ) -> Callable[[FileRecord], None]:
        """Create a bounded progress bar for the hashing phase.

        Returns an on_file_hashed callback to pass into hash_records().
        Advances the bar by record.size bytes per file so TransferSpeedColumn
        and TimeRemainingColumn operate on bytes (MB/s, byte-accurate ETA).
        """
        label = f"Hashing [bold]{total_files:,}[/bold] files"
        task: TaskID = self._progress.add_task(label, total=total_bytes)

        def on_file_hashed(record: FileRecord) -> None:
            self._progress.advance(task, advance=max(record.size, 1))

        return on_file_hashed

    def print_filter_summary(self, total: int, candidates: int) -> None:
        """Print the size-filter reduction line used by the dupes command."""
        skipped = total - candidates
        _console.print(
            f"  [dim]Size filter:[/dim]  "
            f"[bold]{candidates:,}[/bold] candidates  "
            f"[dim]({skipped:,} skipped — unique size)[/dim]"
        )

    def stop(self) -> None:
        """Stop and finalize the Rich progress display."""
        self._progress.stop()


class NoOpProgressReporter:
    """Silent drop-in used when --verbose is not set."""

    @contextmanager
    def walk_phase(self, label: str) -> Generator[Callable[[FileRecord], None], None, None]:
        yield lambda _record: None

    def hash_phase(
        self,
        total_files: int,
        total_bytes: int,
    ) -> Callable[[FileRecord], None]:
        return lambda _record: None

    def print_filter_summary(self, total: int, candidates: int) -> None:
        pass

    def stop(self) -> None:
        pass


def make_reporter(verbose: bool) -> ProgressReporter | NoOpProgressReporter:
    """Return the appropriate reporter based on the --verbose flag."""
    return ProgressReporter() if verbose else NoOpProgressReporter()
