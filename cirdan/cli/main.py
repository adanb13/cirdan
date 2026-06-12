"""Cirdan CLI entry points: `cirdan` and `cirdand`."""

from __future__ import annotations

import typer
from rich.console import Console

import cirdan as cirdan_pkg
from cirdan.config import load_config

app = typer.Typer(
    name="cirdan",
    help="Cirdan: AI infrastructure cartographer. Fingerprints, graphs, and watches live systems.",
    no_args_is_help=False,
)
daemon_app = typer.Typer(
    name="cirdand",
    help="Cirdan always-on daemon.",
    no_args_is_help=True,
)
console = Console()
status_console = Console(stderr=True)


def _attach_progress(engine) -> None:
    """Stream pipeline steps to stderr so stdout stays clean for --json consumers."""
    engine.progress = lambda message: status_console.print(f"[dim]  · {message}[/dim]")


@daemon_app.callback()
def _daemon_main():
    """Cirdan always-on daemon."""


def _nudge_if_outdated() -> None:
    """One dim stderr line when PyPI has a newer release; humans only, never pipelines."""
    import sys

    if not sys.stderr.isatty():
        return
    try:
        from cirdan.update_check import check_for_update, upgrade_hint

        newer = check_for_update()
        if newer:
            status_console.print(
                f"[dim]cirdanops {newer} available (you have {cirdan_pkg.__version__}) — "
                f"{upgrade_hint()}[/dim]"
            )
    except Exception:
        pass


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
):
    _nudge_if_outdated()
    if version:
        console.print(f"cirdan {cirdan_pkg.__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        console.print("[bold]Get started:[/bold] cirdan setup        "
                      "[dim](or: cirdan setup --system to watch this whole machine)[/dim]")
        raise typer.Exit()


@app.command()
def map(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    live: bool = typer.Option(None, "--live/--no-live", help="Force live discovery on/off (default: auto)."),
    out: str = typer.Option(None, "--out", help="Output directory (default: cirdan-out)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the run summary as JSON."),
):
    """Fingerprint the environment and build the full infrastructure map + artifacts."""
    from cirdan.engine import CirdanEngine
    from cirdan.fingerprint.engine import render_fingerprint
    from cirdan.fingerprint import Fingerprint
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path, system=system)
    if out:
        engine.config.output.dir = out
    _attach_progress(engine)
    status_console.print(f"[bold]cirdan map[/bold] {engine.config.root_path}")
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
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    json_out: bool = typer.Option(False, "--json", help="Emit structured JSON."),
):
    """Ask the infrastructure graph a question (deterministic, no LLM)."""
    from cirdan.engine import CirdanEngine
    from cirdan.query import answer_query
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path, system=system)
    result = answer_query(engine, question)
    if json_out:
        console.print_json(dump_json(result))
    else:
        console.print(result["answer"])


@app.command()
def show(
    request: str = typer.Argument(..., help='e.g. "show checkout-api as a dependency graph"'),
    path: str = typer.Option(".", "--path", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    fmt: str = typer.Option("all", "--format", help="html, md, json, term, or all."),
):
    """Generate a view of the system on demand (Agentic UI)."""
    from cirdan.engine import CirdanEngine
    from cirdan.ui.render import render_terminal
    from cirdan.ui.router import build_view

    engine = CirdanEngine.open(path, system=system)
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
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    all: bool = typer.Option(False, "--all", help="Include resolved incidents."),
    detect: bool = typer.Option(True, "--detect/--no-detect", help="Run a detection pass first."),
    json_out: bool = typer.Option(False, "--json"),
):
    """List incidents (runs a detection pass over current state and telemetry)."""
    from cirdan.engine import CirdanEngine
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path, system=system)
    _attach_progress(engine)
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
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
):
    """Explain an incident or a graph node with its evidence."""
    from cirdan.engine import CirdanEngine
    from cirdan.query import answer_query

    engine = CirdanEngine.open(path, system=system)
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
    status_console.print("[dim]  · detecting session access (a few seconds)[/dim]")
    ctx = detect_access(config)
    status_console.print("[dim]  · fingerprinting environment[/dim]")
    fp = fingerprint_environment(config, ctx)
    if json_out:
        console.print_json(dump_json(fp.model_dump()))
    else:
        console.print(render_fingerprint(fp))


actions_app = typer.Typer(help="Discover and run actions available through inherited access.")
app.add_typer(actions_app, name="actions")


@actions_app.command("list")
def actions_list(
    node: str = typer.Argument(..., help="Node id or name, e.g. container:web or checkout-api."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    json_out: bool = typer.Option(False, "--json"),
):
    """List actions currently possible against a component."""
    from cirdan.actions import list_actions
    from cirdan.engine import CirdanEngine
    from cirdan.util import dump_json

    engine = CirdanEngine.open(path, system=system)
    specs = list_actions(engine, node)
    if json_out:
        console.print_json(dump_json([s.model_dump() for s in specs]))
        return
    if not specs:
        console.print(f"No actions available for '{node}' with current access.")
        return
    from rich.table import Table

    table = Table()
    for col in ("Action id", "Description", "Command", "Writes"):
        table.add_column(col)
    for spec in specs:
        table.add_row(spec.id, spec.description, " ".join(spec.argv),
                      "[red]yes[/red]" if spec.writes else "no")
    console.print(table)


@actions_app.command("run")
def actions_run(
    action_id: str = typer.Argument(..., help="Action id from `cirdan actions list`."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation for write actions."),
    verify_after: bool = typer.Option(True, "--verify/--no-verify", help="Verify outcome afterwards."),
):
    """Execute an action using the session's own access, record it, and verify."""
    from cirdan.actions import execute_action, find_action
    from cirdan.engine import CirdanEngine
    from cirdan.verify import verify_action

    engine = CirdanEngine.open(path, system=system)
    spec = find_action(engine, action_id)
    if spec is None:
        console.print(f"[red]Unknown or unavailable action:[/red] {action_id}")
        raise typer.Exit(1)
    if spec.writes and not yes:
        typer.confirm(f"Run write action `{' '.join(spec.argv)}`?", abort=True)
    record = execute_action(engine, spec)
    result = record.result
    console.print(f"[bold]{record.record_id}[/bold] {spec.id} → "
                  + ("[green]ok[/green]" if result.ok else f"[red]failed rc={result.returncode}[/red]"))
    if result.stdout.strip():
        console.print(result.stdout.strip()[-2000:])
    if result.stderr.strip():
        console.print(f"[dim]{result.stderr.strip()[-1000:]}[/dim]")
    if verify_after and spec.writes:
        verification = verify_action(engine, record)
        color = "green" if verification["status"] == "succeeded" else "red"
        console.print(f"Verification: [{color}]{verification['status']}[/{color}] — {verification['summary']}")
        for check in verification["checks"]:
            mark = "✓" if check["result"] == "pass" else "✗"
            console.print(f"  {mark} {check['name']}: {check['detail']}")


@app.command()
def respond(
    incident_id: str = typer.Argument(..., help="Incident id (or prefix) to respond to."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Write/print the brief and the command without executing."),
):
    """Compose an incident brief and invoke the configured responder agent once."""
    import asyncio

    from cirdan.engine import CirdanEngine
    from cirdan.incidents.responder import IncidentResponder, render_command

    engine = CirdanEngine.open(path, system=system)
    incident = engine.incidents.get(incident_id)
    if incident is None:
        console.print(f"[red]No incident:[/red] {incident_id}")
        raise typer.Exit(1)
    resp = IncidentResponder(engine)
    brief_file = resp.write_brief(incident)
    console.print(f"Brief: [bold]{brief_file}[/bold]")
    command = engine.config.responder.command
    if not command:
        console.print("[yellow]No responder.command configured[/yellow] — brief-only mode. "
                      "Set it in cirdan.yaml or rerun `cirdan install --project`.")
        return
    argv = render_command(command, incident, brief_file)
    if dry_run:
        console.print(f"Would run: [bold]{' '.join(argv)}[/bold]")
        return
    ok = asyncio.run(resp.invoke(incident))
    console.print("[green]responder completed[/green]" if ok else "[red]responder failed[/red]"
                  + " — see cirdan-out/audit.jsonl")


@app.command()
def verify(
    record_id: str = typer.Argument(..., help="Action record id (act-…)."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
):
    """Re-verify the outcome of a previously executed action."""
    from cirdan.actions.executor import get_record
    from cirdan.engine import CirdanEngine
    from cirdan.verify import verify_action

    engine = CirdanEngine.open(path, system=system)
    record = get_record(engine, record_id)
    if record is None:
        console.print(f"[red]No action record:[/red] {record_id}")
        raise typer.Exit(1)
    verification = verify_action(engine, record)
    color = "green" if verification["status"] == "succeeded" else "red"
    console.print(f"[{color}]{verification['status']}[/{color}] — {verification['summary']}")
    for check in verification["checks"]:
        mark = "✓" if check["result"] == "pass" else "✗"
        console.print(f"  {mark} {check['name']}: {check['detail']}")


@app.command()
def watch(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
):
    """Foreground live view: stream runtime events and incident changes (Ctrl-C to stop)."""
    import asyncio

    from cirdan.daemon import CirdanDaemon
    from cirdan.engine import CirdanEngine

    engine = CirdanEngine.open(path, system=system)

    def on_event(item: dict) -> None:
        if item["kind"] == "event":
            ev = item["event"]
            color = {"error": "red", "warning": "yellow"}.get(ev["severity"], "dim")
            console.print(f"[{color}]●[/{color}] [dim]{ev['ts']}[/dim] "
                          f"{ev['provider']} {ev['resource']} — {ev['message'][:160]}")
        elif item["kind"] == "incident":
            inc = item["incident"]
            console.print(f"[bold red]incident[/bold red] {inc['id']} [{inc['status']}] {inc['title']}")

    daemon = CirdanDaemon(engine, on_event=on_event)
    console.print("[bold]cirdan watch[/bold] — streaming events; Ctrl-C to stop.")

    async def _run():
        await daemon.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\nstopped.")
    except Exception as exc:
        from cirdan.daemon.lock import DaemonAlreadyRunning

        if isinstance(exc, DaemonAlreadyRunning):
            console.print(f"[red]{exc}[/red] — use [bold]cirdan status[/bold] / [bold]cirdan stop[/bold].")
            raise typer.Exit(1)
        raise


@daemon_app.command("serve")
def daemon_serve(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    config: str = typer.Option(None, "--config", help="Path to cirdan.yaml."),
    mcp: bool = typer.Option(False, "--mcp", help="Also serve MCP."),
    http: bool = typer.Option(False, "--http", help="Also serve the HTTP API."),
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
):
    """Run the Always ON daemon: watch, refresh, detect, export — until stopped."""
    import asyncio

    from cirdan.daemon import CirdanDaemon
    from cirdan.engine import CirdanEngine

    engine = CirdanEngine.open(path, config_file=config, system=system)
    if host:
        engine.config.daemon.host = host
    if port:
        engine.config.daemon.port = port
    daemon = CirdanDaemon(engine)

    async def _run():
        await daemon.start()
        extras = []
        if http:
            from cirdan.api.http import serve_http

            extras.append(asyncio.create_task(
                serve_http(engine, mcp=mcp,
                           host=engine.config.daemon.host, port=engine.config.daemon.port)
            ))
        elif mcp:
            from cirdan.mcp.server import run_stdio

            extras.append(asyncio.create_task(run_stdio(engine)))
        try:
            await asyncio.gather(*extras) if extras else await asyncio.Event().wait()
        finally:
            await daemon.stop()

    console.print(f"[bold]cirdand[/bold] serving (root={engine.config.root_path}, "
                  f"mcp={mcp}, http={http}); Ctrl-C to stop.")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\nstopped.")
    except Exception as exc:
        from cirdan.daemon.lock import DaemonAlreadyRunning

        if isinstance(exc, DaemonAlreadyRunning):
            console.print(f"[red]{exc}[/red] — use [bold]cirdan status[/bold] / [bold]cirdan stop[/bold].")
            raise typer.Exit(1)
        raise


def _lock_path_for(path: str, system: bool):
    from cirdan.config import CirdanConfig

    config = CirdanConfig.system() if system else load_config(path)
    return config.output_dir / "cirdand.lock"


@app.command("status")
@daemon_app.command("status")
def daemon_status(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Machine-level scope (~/.cirdan)."),
):
    """Show whether a cirdand instance is running for this scope."""
    from cirdan.daemon.lock import holder

    info = holder(_lock_path_for(path, system))
    scope = "system scope" if system else "this project"
    if info is None:
        console.print(f"cirdand: [yellow]not running[/yellow] for {scope}.")
        raise typer.Exit(3)
    console.print(f"cirdand: [green]running[/green] (pid {info.get('pid')}, since {info.get('started_at')})")


@app.command("stop")
@daemon_app.command("stop")
def daemon_stop(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Machine-level scope (~/.cirdan)."),
    timeout: float = typer.Option(10.0, "--timeout", help="Seconds to wait for shutdown."),
):
    """Stop the running cirdand instance for this scope."""
    import os
    import signal
    import time

    from cirdan.daemon.lock import holder

    lock_path = _lock_path_for(path, system)
    info = holder(lock_path)
    scope = "system scope" if system else "this project"
    if info is None or not info.get("pid"):
        console.print(f"cirdand: [yellow]not running[/yellow] for {scope}.")
        raise typer.Exit(3)
    pid = int(info["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print(f"pid {pid} already gone.")
        raise typer.Exit(0)
    except PermissionError:
        console.print(f"[red]no permission to signal pid {pid}[/red] (owned by another user?)")
        raise typer.Exit(1)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if holder(lock_path) is None:
            console.print(f"cirdand (pid {pid}) [green]stopped[/green].")
            return
        time.sleep(0.3)
    console.print(f"[yellow]cirdand (pid {pid}) still shutting down after {timeout}s[/yellow] — "
                  f"check again with cirdan status.")
    raise typer.Exit(1)


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
