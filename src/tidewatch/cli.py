"""Command-line entry point. Thin: the logic lives in the library (ADR 0001)."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from tidewatch import __version__
from tidewatch.collect import NamespaceNotFound, load_clients
from tidewatch.collect import collect as collect_snapshot
from tidewatch.diagnose import diagnose
from tidewatch.findings import Report
from tidewatch.snapshot import Snapshot, SnapshotLoadError, load

app = typer.Typer(no_args_is_help=True, add_completion=False)
err = Console(stderr=True, highlight=False, soft_wrap=True)
STYLE = {"critical": "bold red", "warning": "bold yellow", "info": "bold blue"}

NamespaceOpt = Annotated[str, typer.Option("--namespace", "-n", help="Namespace to inspect.")]
ContextOpt = Annotated[
    str | None, typer.Option("--context", help="kubeconfig context (default: current).")
]


def fail(message: str) -> typer.Exit:
    """Print an error without interpreting it as rich markup (cluster text can contain [...])."""
    err.print(Text.assemble(("error:", "red"), " ", message))
    return typer.Exit(1)


def write_json(path: Path, data: str) -> None:
    try:
        path.write_text(data)
    except OSError as exc:
        raise fail(f"cannot write {path}: {exc}") from None


def take_snapshot(namespace: str, context: str | None) -> Snapshot:
    context_name = context
    try:
        core, apps, context_name = load_clients(context)
        return collect_snapshot(core, apps, namespace, context=context_name)
    except NamespaceNotFound:
        raise fail(
            f"namespace '{namespace}' not found in {context_name or 'the current context'}"
        ) from None
    except Exception as exc:  # show a clean message, not a traceback
        raise fail(f"could not collect from the cluster: {exc}") from None


@app.callback()
def main() -> None:
    """Scan a Kubernetes app, diagnose common failures, write a ticket-ready report."""


@app.command()
def version() -> None:
    """Print the Tidewatch version."""
    typer.echo(__version__)


@app.command()
def collect(
    namespace: NamespaceOpt,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write JSON here (default: stdout).")
    ] = None,
    context: ContextOpt = None,
) -> None:
    """Take a read-only snapshot of one namespace (pods, deployments, events, recent logs)."""
    snapshot = take_snapshot(namespace, context)
    data = snapshot.model_dump_json(indent=2) + "\n"
    if output is None:
        typer.echo(data, nl=False)
    else:
        write_json(output, data)
    err.print(
        Text(
            f"collected {len(snapshot.pods)} pods, {len(snapshot.deployments)} deployments, "
            f"{len(snapshot.events)} events from "
            f"{snapshot.context or 'current context'}/{namespace}"
            + (f" -> {output}" if output else "")
        )
    )


def print_report(report: Report) -> None:
    """Text objects only (no markup parsing), so log lines with [...] print literally."""
    out = Console(highlight=False, soft_wrap=True)
    where = f"{report.context or 'snapshot'}/{report.namespace}"
    counts = ", ".join(f"{n} {s}" for s, n in report.counts.items())
    out.print(Text.assemble(("Tidewatch scan", "bold"), f"  {where}  ({counts})"))
    if not report.findings:
        out.print(Text(f"No findings in {where}.", style="green"))
        return
    for f in report.findings:
        out.print()
        target = f.object + (f" (container {f.container})" if f.container else "")
        out.print(
            Text.assemble(
                (f.severity.upper(), STYLE[f.severity]), "  ", (f.title, "bold"), f"  [{f.rule_id}]"
            )
        )
        out.print(Text(f"  {target}"))
        out.print(Text(f"  {f.summary}"))
        sections = [
            ("Evidence", [f"- {e}" for e in f.evidence]),
            ("Log excerpt", [f"| {line}" for line in f.log_excerpt]),
            ("Next steps", [f"{i}. {s}" for i, s in enumerate(f.next_steps, 1)]),
        ]
        for heading, lines in sections:
            if lines:
                out.print(Text(f"  {heading}:", style="bold"))
                for line in lines:
                    out.print(Text(f"    {line}"))


@app.command()
def scan(
    namespace: Annotated[
        str | None,
        typer.Option(
            "--namespace", "-n", help="Namespace to scan (optional with --from; must match)."
        ),
    ] = None,
    context: ContextOpt = None,
    from_file: Annotated[
        Path | None,
        typer.Option("--from", help="Scan a saved snapshot instead of the cluster."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the report as JSON instead of text.")
    ] = False,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Also write the report JSON here.")
    ] = None,
) -> None:
    """Diagnose common failures in one namespace and print a ticket-ready report."""
    if output is not None and from_file is not None and output.resolve() == from_file.resolve():
        raise fail(f"refusing to overwrite the input snapshot {from_file} with the report")
    if from_file is not None:
        try:
            snapshot = load(from_file, namespace)
        except SnapshotLoadError as exc:
            raise fail(str(exc)) from None
    elif namespace is None:
        raise fail("--namespace is required unless --from is given")
    else:
        snapshot = take_snapshot(namespace, context)
    report = diagnose(snapshot)
    data = report.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json(output, data)
    if as_json:
        typer.echo(data, nl=False)
    else:
        print_report(report)
