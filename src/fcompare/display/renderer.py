"""Format dispatch and shared enums for the display layer."""

from enum import StrEnum

from fcompare.engine.models import DiffResult, DupeGroup


class OutputFormat(StrEnum):
    """Supported output formats for all subcommands."""

    rich = "rich"
    json = "json"


class ShowFilter(StrEnum):
    """Which file categories to include in diff output."""

    all = "all"
    modified = "modified"
    unique = "unique"
    same = "same"


def render_diff(result: DiffResult, fmt: OutputFormat, show: ShowFilter) -> None:
    """Dispatch diff output to the appropriate format renderer."""
    if fmt == OutputFormat.json:
        from fcompare.display.json_renderer import render_diff_json

        render_diff_json(result, show)
    else:
        from fcompare.display.rich_renderer import render_diff_rich

        render_diff_rich(result, show)


def render_dupes(groups: list[DupeGroup], root: str, fmt: OutputFormat) -> None:
    """Dispatch dupes output to the appropriate format renderer."""
    if fmt == OutputFormat.json:
        from fcompare.display.json_renderer import render_dupes_json

        render_dupes_json(groups)
    else:
        from fcompare.display.rich_renderer import render_dupes_rich

        render_dupes_rich(groups, root)
