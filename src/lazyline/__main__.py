"""CLI entry point for lazyline."""

from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Show version and exit.")
    ] = False,
) -> None:
    """Lazyline CLI."""
    if version:
        from lazyline import __version__

        typer.echo(f"lazyline {__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()
