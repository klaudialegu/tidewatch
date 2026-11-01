"""Command-line entry point. No cluster access yet: commands arrive slice by slice."""

import typer

from tidewatch import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Scan a Kubernetes app, diagnose common failures, write a ticket-ready report."""


@app.command()
def version() -> None:
    """Print the Tidewatch version."""
    typer.echo(__version__)
