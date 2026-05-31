"""Rich terminal renderer for diff and dupes output."""

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from fcompare.display.renderer import ShowFilter
from fcompare.engine.models import DiffResult, DupeGroup

_console = Console()


def render_diff_rich(result: DiffResult, show: ShowFilter) -> None:
    """Render a DiffResult as a colored, human-readable terminal report."""
    _console.print()
    _console.print(
        f"[bold]Directory Comparison:[/bold] {result.left_root}  [dim]↔[/dim]  {result.right_root}"
    )
    _console.print(Rule())

    rows: list[Text] = []

    if show in (ShowFilter.all, ShowFilter.same):
        for left, _ in result.identical:
            rows.append(_diff_row("✓", "green", "same     ", str(left.rel_path)))

    if show in (ShowFilter.all, ShowFilter.modified):
        for left, right in result.modified:
            label = f"~ modified   {left.sha256[:8]}…  →  {right.sha256[:8]}…"
            rows.append(_diff_row("~", "yellow", "modified ", str(left.rel_path), label))

    if show in (ShowFilter.all, ShowFilter.unique):
        for record in result.only_in_left:
            rows.append(_diff_row("+", "blue", "only left", str(record.rel_path)))
        for record in result.only_in_right:
            rows.append(_diff_row("-", "red", "only right", str(record.rel_path)))

    if rows:
        for row in rows:
            _console.print(row)
    else:
        _console.print("[dim]  (no files match the selected filter)[/dim]")

    _console.print(Rule())
    _console.print(
        f"  [dim]Total {result.total_files} files[/dim]"
        f"  [green]{len(result.identical)} identical[/green]"
        f"  [yellow]{len(result.modified)} modified[/yellow]"
        f"  [blue]{len(result.only_in_left)} only in left[/blue]"
        f"  [red]{len(result.only_in_right)} only in right[/red]"
    )
    _console.print()


def render_dupes_rich(groups: list[DupeGroup], root: str) -> None:
    """Render duplicate file groups as a colored terminal report."""
    _console.print()
    _console.print(f"[bold]Duplicate Files in:[/bold] {root}")
    _console.print(Rule())

    if not groups:
        _console.print("[dim]  No duplicate files found.[/dim]")
        _console.print(Rule())
        _console.print("  [dim]0 duplicate groups  │  0 redundant files  │  0 B reclaimable[/dim]")
        _console.print()
        return

    for group in groups:
        wasted = _human_bytes(group.wasted_bytes)
        _console.print(
            f"  [bold cyan]SHA-256:[/bold cyan] {group.sha256[:8]}…{group.sha256[-4:]}"
            f"  [dim]({len(group.files)} copies, {wasted} wasted)[/dim]"
        )
        for record in group.files:
            _console.print(f"    [dim]{record.path}[/dim]")
        _console.print()

    total_groups = len(groups)
    total_redundant = sum(len(g.files) - 1 for g in groups)
    total_wasted = sum(g.wasted_bytes for g in groups)

    _console.print(Rule())
    _console.print(
        f"  [dim]{total_groups} dupe groups"
        f"  │  {total_redundant} redundant files"
        f"  │  {_human_bytes(total_wasted)} reclaimable[/dim]"
    )
    _console.print()


def _diff_row(symbol: str, color: str, label: str, path: str, detail: str = "") -> Text:
    t = Text()
    t.append(f"  [{symbol}] ", style=f"bold {color}")
    t.append(f"{label:<10}", style=color)
    t.append(f"  {path}")
    if detail:
        t.append(f"  [dim]{detail}[/dim]")
    return t


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"  # pragma: no cover — TB branch always exits the loop first
