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
