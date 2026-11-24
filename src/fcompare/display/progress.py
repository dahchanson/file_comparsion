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
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from fcompare.engine.models import FileRecord

# Batch walk-phase Rich description updates to avoid per-file lock + string churn
# on trees with hundreds of thousands of files.
_WALK_UPDATE_BATCH: int = 128


class ProgressReporter:
    """Rich-backed progress reporter for walk + hash phases."""

    def __init__(self) -> None:
        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.fields[counts]}"),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )
        self._progress.start()

    @contextmanager
    def walk_phase(self, label: str) -> Generator[Callable[[FileRecord], None], None, None]:
        """Context manager for the directory-walk phase.

        Yields a tick() callable. Updates the live count in batches to keep
        walk overhead negligible on large trees, then sets the exact count on exit.
        """
        task: TaskID = self._progress.add_task(label, total=None, counts="")
        count = 0

        def tick(_record: FileRecord) -> None:
            nonlocal count
            count += 1
            if count % _WALK_UPDATE_BATCH == 0:
                self._progress.update(
                    task,
                    description=f"{label}  [dim]{count:,} files[/dim]",
                )

        try:
            yield tick
        finally:
            self._progress.update(
                task,
                description=f"{label}  [dim]{count:,} files[/dim]",
                total=count,
                completed=count,
            )

    def hash_phase(
        self,
        total_files: int,
        total_bytes: int,
    ) -> Callable[[FileRecord], None]:
        """Create a bounded progress bar for the hashing phase.

        Tracks byte completion on the task (driving ``TransferSpeedColumn`` and
        byte-weighted ``TimeRemainingColumn``) while displaying accurate
        ``done/total`` file counts in the ``counts`` column.
        """
        label = f"Hashing [bold]{total_files:,}[/bold] files"
        effective_total = max(total_bytes, 1)
        task: TaskID = self._progress.add_task(
            label,
            total=effective_total,
            counts=f"0/{total_files:,}",
        )
        done_files = 0

        def on_file_hashed(record: FileRecord) -> None:
            nonlocal done_files
            done_files += 1
            completed_update: dict[str, object] = {"counts": f"{done_files:,}/{total_files:,}"}
            if done_files >= total_files and total_bytes == 0:
                completed_update["completed"] = effective_total
                self._progress.update(task, **completed_update)  # type: ignore[arg-type]
            else:
                self._progress.update(
                    task,
                    advance=record.size,
                    **completed_update,  # type: ignore[arg-type]
                )

        return on_file_hashed

    def print_filter_summary(self, total: int, candidates: int) -> None:
        """Print the size-filter reduction line used by the dupes command."""
        skipped = total - candidates
        self._console.print(
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
