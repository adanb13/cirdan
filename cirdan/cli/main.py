"""Cirdan CLI entry points: `cirdan` and `cirdand`."""

from __future__ import annotations

import typer
from rich.console import Console

import cirdan as cirdan_pkg
from cirdan.config import load_config

app = typer.Typer(
    name="cirdan",
    help="Cirdan: AI infrastructure cartographer. Fingerprints, graphs, and watches live systems.",
    no_args_is_help=True,
)
daemon_app = typer.Typer(
    name="cirdand",
    help="Cirdan always-on daemon.",
    no_args_is_help=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
):
    if version:
        console.print(f"cirdan {cirdan_pkg.__version__}")
        raise typer.Exit()


@app.command()
def map(
    path: str = typer.Argument(".", help="Project root."),
    live: bool = typer.Option(None, "--live/--no-live", help="Force live discovery on/off (default: auto)."),
    out: str = typer.Option(None, "--out", help="Output directory (default: cirdan-out)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the run summary as JSON."),
):
    """Fingerprint the environment and build the full infrastructure map + artifacts."""
    from cirdan.engine import CirdanEngine
    from cirdan.fingerprint.engine import render_fingerprint
    from cirdan.fingerprint import Fingerprint
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path)
    if out:
        engine.config.output.dir = out
    summary = engine.map(live=live)
    if json_out:
        console.print_json(dump_json(summary))
        return
    console.print(render_fingerprint(Fingerprint.model_validate(summary["fingerprint"])))
    console.print(f"Graph: [bold]{summary['nodes']}[/bold] nodes, [bold]{summary['edges']}[/bold] edges")
    findings = summary["findings"]
    if findings:
        console.print(f"\nFindings ({len(findings)}):")
        for f in findings:
            color = {"critical": "red", "warning": "yellow"}.get(f["severity"], "blue")
            console.print(f"  [{color}]●[/{color}] {f['summary']}")
    console.print("\nGenerated:")
    for artifact in summary["artifacts"]:
        console.print(f"  - {artifact}")


@app.command()
def query(
    question: str = typer.Argument(..., help='e.g. "what depends on postgres?"'),
    path: str = typer.Option(".", "--path", help="Project root."),
    json_out: bool = typer.Option(False, "--json", help="Emit structured JSON."),
):
    """Ask the infrastructure graph a question (deterministic, no LLM)."""
    from cirdan.engine import CirdanEngine
    from cirdan.query import answer_query
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path)
    result = answer_query(engine, question)
    if json_out:
        console.print_json(dump_json(result))
    else:
        console.print(result["answer"])


@app.command()
def show(
    request: str = typer.Argument(..., help='e.g. "show checkout-api as a dependency graph"'),
    path: str = typer.Option(".", "--path", help="Project root."),
    fmt: str = typer.Option("all", "--format", help="html, md, json, term, or all."),
):
    """Generate a view of the system on demand (Agentic UI)."""
    from cirdan.engine import CirdanEngine
    from cirdan.ui.render import render_terminal
    from cirdan.ui.router import build_view

    engine = CirdanEngine.open(path)
    spec = build_view(engine, request)
    render_terminal(spec, console)
    if fmt != "term":
        formats = ("html", "md", "json") if fmt == "all" else (fmt,)
        paths = engine.save_view(spec, formats=formats)
        console.print("\nGenerated:")
        for p in paths:
            console.print(f"  - {p}")


@app.command()
def incidents(
    path: str = typer.Option(".", "--path", help="Project root."),
    all: bool = typer.Option(False, "--all", help="Include resolved incidents."),
    detect: bool = typer.Option(True, "--detect/--no-detect", help="Run a detection pass first."),
    json_out: bool = typer.Option(False, "--json"),
):
    """List incidents (runs a detection pass over current state and telemetry)."""
    from cirdan.engine import CirdanEngine
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path)
    if detect:
        engine.detect_incidents()
    items = engine.incidents.list(include_resolved=all)
    if json_out:
        console.print_json(dump_json([i.model_dump() for i in items]))
        return
    if not items:
        console.print("[green]No incidents.[/green]")
        return
    from rich.table import Table

    table = Table()
    for col in ("Id", "Status", "Severity", "Started", "Title"):
        table.add_column(col)
    for inc in items:
        color = {"critical": "red", "high": "red", "warning": "yellow"}.get(inc.severity, "white")
        table.add_row(inc.id, inc.status, f"[{color}]{inc.severity}[/{color}]", inc.started_at, inc.title)
    console.print(table)


@app.command()
def explain(
    target: str = typer.Argument(..., help="Incident id (or prefix), or a node name."),
    path: str = typer.Option(".", "--path", help="Project root."),
):
    """Explain an incident or a graph node with its evidence."""
    from cirdan.engine import CirdanEngine
    from cirdan.query import answer_query

    engine = CirdanEngine.open(path)
    report = engine.explain_incident(target)
    if report is not None:
        console.print(report)
        return
    result = answer_query(engine, target)
    console.print(result["answer"])


@app.command()
def fingerprint(
    path: str = typer.Argument(".", help="Project root."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """Fingerprint the environment: runtimes, clouds, IaC, telemetry, access."""
    from cirdan.access import detect_access
    from cirdan.fingerprint import fingerprint_environment, render_fingerprint
    from cirdan.util import dump_json

    config = load_config(path)
    ctx = detect_access(config)
    fp = fingerprint_environment(config, ctx)
    if json_out:
        console.print_json(dump_json(fp.model_dump()))
    else:
        console.print(render_fingerprint(fp))


@app.command()
def access(
    path: str = typer.Argument(".", help="Project root."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
):
    """Detect and report what the current agent/session can access."""
    from cirdan.access import detect_access, render_access_report
    from cirdan.util import dump_json

    config = load_config(path)
    ctx = detect_access(config)
    if json_out:
        console.print_json(dump_json(ctx.model_dump()))
    else:
        console.print(render_access_report(ctx))


if __name__ == "__main__":
    app()
