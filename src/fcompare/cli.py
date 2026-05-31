"""Top-level Typer application. Subcommands are registered here as the project grows."""

import typer

from fcompare import __version__

app = typer.Typer(
    name="fcompare",
    help="File comparison and duplicate detection utility.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fcompare {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """File comparison and duplicate detection for macOS."""
