"""Command line interface for ChronoGuard."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from chronoguard import __version__

app = typer.Typer(
    name="chronoguard",
    help=(
        "Run LLM agents as if it were a past date, and measure how well the "
        "blinding holds.\n\n"
        "ChronoGuard filters tool results by publication date (tool leakage) "
        "and probes the model for facts it already knows (parametric leakage)."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    _version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            help="Print the ChronoGuard version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """ChronoGuard: point-in-time leakage guard for LLM agents."""


@app.command()
def version() -> None:
    """Print the installed ChronoGuard version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m chronoguard`
    main()
