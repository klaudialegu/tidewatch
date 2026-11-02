"""Command-line entry point."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from tidewatch import __version__
from tidewatch.collect import collect as collect_snapshot
from tidewatch.collect import load_clients

app = typer.Typer(no_args_is_help=True, add_completion=False)
err = Console(stderr=True)


@app.callback()
def main() -> None:
    """Scan a Kubernetes app, diagnose common failures, write a ticket-ready report."""


@app.command()
def version() -> None:
    """Print the Tidewatch version."""
    typer.echo(__version__)


@app.command()
def collect(
    namespace: Annotated[str, typer.Option("--namespace", "-n", help="Namespace to snapshot.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write JSON here (default: stdout).")
    ] = None,
    context: Annotated[
        str | None, typer.Option("--context", help="kubeconfig context (default: current).")
    ] = None,
) -> None:
    """Take a read-only snapshot of one namespace (pods, deployments, events, recent logs)."""
    try:
        core, apps, context_name = load_clients(context)
        snapshot = collect_snapshot(core, apps, namespace, context=context_name)
    except Exception as exc:  # show a clean message, not a traceback
        err.print(f"[red]error:[/red] could not collect from the cluster: {exc}")
        raise typer.Exit(1) from None
    data = snapshot.model_dump_json(indent=2) + "\n"
    if output is None:
        typer.echo(data, nl=False)
    else:
        output.write_text(data)
    err.print(
        f"collected {len(snapshot.pods)} pods, {len(snapshot.deployments)} deployments, "
        f"{len(snapshot.events)} events from {context_name or 'current context'}/{namespace}"
        + (f" -> {output}" if output else "")
    )
