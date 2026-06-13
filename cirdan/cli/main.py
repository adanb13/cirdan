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


def _scope_notice(requested_path: str) -> None:
    from pathlib import Path

    status_console.print(
        f"[dim]no cirdan project at {Path(requested_path).resolve()} — using system scope (~/.cirdan); "
        f"pass --system to make this explicit, or run [bold]cirdan setup[/bold] in a project directory[/dim]"
    )


def _open_engine(path: str = ".", config_file: str | None = None, system: bool = False):
    """CirdanEngine.open + the CLI's scope-fallback notice and clean permission errors."""
    from cirdan.engine import CirdanEngine

    try:
        engine = CirdanEngine.open(path, config_file=config_file, system=system)
    except OSError as exc:
        target = getattr(exc, "filename", None) or "output directory"
        reason = getattr(exc, "strerror", None) or str(exc)
        console.print(f"[red]cannot create {target}:[/red] {reason} — run cirdan inside a project "
                      f"directory, or use --system for the machine scope (set up with: cirdan setup --system)")
        raise typer.Exit(1)
    if engine.scope_fallback:
        _scope_notice(engine.scope_fallback)
    return engine


@daemon_app.callback()
def _daemon_main():
    """Cirdan always-on daemon."""


def _first_run_hint(invoked_subcommand: str | None) -> None:
    """One-time welcome on a machine's very first cirdan invocation.

    The closest thing to a post-install message: pip/uv can't print one
    (wheels install without executing project code), so we greet on first run.
    """
    import os
    import sys
    from pathlib import Path

    try:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        marker = Path(base) / "cirdan" / "first-run"
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
        if sys.stderr.isatty() and invoked_subcommand not in ("setup", "install"):
            status_console.print(
                "[bold]Welcome to Cirdan.[/bold] Set it up in one go: "
                "[bold]cirdan setup --system[/bold] [dim](watch this whole machine)[/dim] "
                "or [bold]cirdan install --project[/bold] [dim](this repo)[/dim]"
            )
    except Exception:
        pass


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
    _first_run_hint(ctx.invoked_subcommand)
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
    resolution: float = typer.Option(None, "--resolution",
                                     help="Subsystem granularity (Louvain resolution; higher = more groups)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the run summary as JSON."),
):
    """Fingerprint the environment and build the full infrastructure map + artifacts."""
    from cirdan.fingerprint.engine import render_fingerprint
    from cirdan.fingerprint import Fingerprint
    from cirdan.util import dump_json

    engine = _open_engine(path, system=system)
    if out:
        engine.config.output.dir = out
    if resolution is not None:
        engine.config.output.community_resolution = resolution
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
    question: str = typer.Argument(..., help='e.g. "what is running?", "what depends on postgres?"'),
    path: str = typer.Option(".", "--path", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    json_out: bool = typer.Option(False, "--json", help="Emit structured JSON."),
):
    """Ask the infrastructure graph a question (deterministic, no LLM)."""
    from cirdan.query import answer_query
    from cirdan.util import dump_json

    engine = _open_engine(path, system=system)
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
    from cirdan.ui.render import render_terminal
    from cirdan.ui.router import build_view

    engine = _open_engine(path, system=system)
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
    from cirdan.util import dump_json

    engine = _open_engine(path, system=system)
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
    from cirdan.query import answer_query

    engine = _open_engine(path, system=system)
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
    from cirdan.util import dump_json

    engine = _open_engine(path, system=system)
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
    from cirdan.verify import verify_action

    engine = _open_engine(path, system=system)
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


graph_app = typer.Typer(help="Contribute agent knowledge to the graph (evidence required).")
app.add_typer(graph_app, name="graph")


def _parse_attrs(pairs: list[str]) -> dict:
    attrs = {}
    for pair in pairs or []:
        if "=" not in pair:
            console.print(f"[red]--attr needs k=v, got:[/red] {pair}")
            raise typer.Exit(1)
        key, value = pair.split("=", 1)
        attrs[key] = value
    return attrs


def _contribution_guard(fn):
    from cirdan.graph.contrib import ContributionError

    try:
        return fn()
    except ContributionError as exc:
        console.print(f"[red]rejected:[/red] {exc}")
        raise typer.Exit(1)


@graph_app.command("add-node")
def graph_add_node(
    node_id: str = typer.Argument(..., help="Namespaced id, e.g. queue:orders."),
    type: str = typer.Option(..., "--type", help="Node type, e.g. Queue, Service, Database."),
    name: str = typer.Option(None, "--name", help="Display name (default: id suffix)."),
    evidence: list[str] = typer.Option(..., "--evidence", help="Source quote (repeatable)."),
    attr: list[str] = typer.Option(None, "--attr", help="k=v attribute (repeatable)."),
    ambiguous: bool = typer.Option(False, "--ambiguous", help="Mark AMBIGUOUS instead of INFERRED."),
    agent: str = typer.Option("cli", "--agent", help="Contributing agent name."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan)."),
):
    """Contribute a node the scanners missed (evidence required, INFERRED-capped)."""
    from cirdan.graph.contrib import contribute_node
    from cirdan.graph.schema import Confidence

    engine = _open_engine(path, system=system)
    node = _contribution_guard(lambda: contribute_node(
        engine, node_id, type=type, name=name or node_id.split(":", 1)[1],
        evidence=list(evidence), attrs=_parse_attrs(attr), agent=agent,
        confidence=Confidence.AMBIGUOUS if ambiguous else Confidence.INFERRED,
    ))
    console.print(f"[green]added[/green] {node.id} ({node.type}, {node.confidence.value})")


@graph_app.command("add-edge")
def graph_add_edge(
    source: str = typer.Argument(..., help="Source node id or name."),
    target: str = typer.Argument(..., help="Target node id or name."),
    relation: str = typer.Argument(..., help="e.g. CONNECTS_TO, DEPENDS_ON, DEPLOYS."),
    evidence: list[str] = typer.Option(..., "--evidence", help="Source quote (repeatable)."),
    attr: list[str] = typer.Option(None, "--attr", help="k=v attribute (repeatable)."),
    ambiguous: bool = typer.Option(False, "--ambiguous", help="Mark AMBIGUOUS instead of INFERRED."),
    agent: str = typer.Option("cli", "--agent", help="Contributing agent name."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan)."),
):
    """Contribute a relationship between existing nodes (evidence required)."""
    from cirdan.graph.contrib import contribute_edge
    from cirdan.graph.schema import Confidence

    engine = _open_engine(path, system=system)
    edge = _contribution_guard(lambda: contribute_edge(
        engine, source, target, relation, evidence=list(evidence),
        attrs=_parse_attrs(attr), agent=agent,
        confidence=Confidence.AMBIGUOUS if ambiguous else Confidence.INFERRED,
    ))
    console.print(f"[green]added[/green] {edge.source} —{edge.relation.value}→ {edge.target} "
                  f"({edge.confidence.value})")


@graph_app.command("annotate")
def graph_annotate(
    ref: str = typer.Argument(..., help="Node id or name."),
    evidence: list[str] = typer.Option(None, "--evidence", help="Source quote (repeatable)."),
    attr: list[str] = typer.Option(None, "--attr", help="k=v attribute (repeatable)."),
    agent: str = typer.Option("cli", "--agent"),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan)."),
):
    """Attach evidence or attributes to an existing node."""
    from cirdan.graph.contrib import annotate_node

    engine = _open_engine(path, system=system)
    node = _contribution_guard(lambda: annotate_node(
        engine, ref, evidence=list(evidence) if evidence else None,
        attrs=_parse_attrs(attr), agent=agent,
    ))
    console.print(f"[green]annotated[/green] {node.id} ({len(node.evidence)} evidence items)")


@app.command()
def enrich(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Write/print the brief without invoking the agent."),
    command: str = typer.Option(None, "--command", help="Agent command template ({brief_file} placeholder)."),
    agent: str = typer.Option(None, "--agent", help="Which detected agent CLI to use (claude, codex, hermes, …)."),
    timeout: float = typer.Option(900.0, "--timeout", help="Seconds before the agent run is killed."),
):
    """Hand your agent an enrichment brief so it can contribute the knowledge
    the deterministic scanners missed (docs, implied dependencies, IaC links)."""
    import asyncio
    import shutil as _shutil

    from cirdan.enrich import (
        build_enrichment_brief, enrichment_targets, resolve_enrich_command,
        run_enrichment, summarize_targets,
    )

    if command is None and agent is not None:
        from cirdan.agents.installer import AGENT_ENRICH_COMMANDS

        known = dict(AGENT_ENRICH_COMMANDS)
        if agent not in known:
            console.print(f"[red]Unknown agent '{agent}'[/red] — supported: {', '.join(known)}")
            raise typer.Exit(1)
        if not _shutil.which(agent):
            console.print(f"[red]{agent} is not on PATH[/red]")
            raise typer.Exit(1)
        command = known[agent]

    engine = _open_engine(path, system=system)
    console.print(f"Targets: [bold]{summarize_targets(enrichment_targets(engine))}[/bold]")
    brief_file = build_enrichment_brief(engine)
    console.print(f"Brief: [bold]{brief_file}[/bold]")
    template = resolve_enrich_command(engine, command)
    if template is None:
        console.print("[yellow]No agent CLI detected[/yellow] — pass --command "
                      "'your-agent {brief_file}' or work through the brief manually.")
        raise typer.Exit(1)
    if dry_run:
        console.print(f"Would run: [bold]{template.format(brief_file=brief_file)}[/bold]")
        return
    ok, diff = asyncio.run(run_enrichment(engine, template, brief_file, timeout=timeout))
    color = "green" if ok else "red"
    console.print(f"agent run: [{color}]{'ok' if ok else 'failed'}[/{color}]")
    console.print(f"contributed: [bold]{len(diff['nodes'])}[/bold] nodes, "
                  f"[bold]{len(diff['edges'])}[/bold] edges")
    for node in diff["nodes"]:
        console.print(f"  + {node.id} ({node.type}) — {node.evidence[0] if node.evidence else ''}")
    for edge in diff["edges"]:
        console.print(f"  + {edge.source} —{edge.relation.value}→ {edge.target} — "
                      f"{edge.evidence[0] if edge.evidence else ''}")


@app.command()
def respond(
    incident_id: str = typer.Argument(..., help="Incident id (or prefix) to respond to."),
    path: str = typer.Option(".", "--path"),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Write/print the brief and the command without executing."),
):
    """Compose an incident brief and invoke the configured responder agent once."""
    import asyncio

    from cirdan.incidents.responder import IncidentResponder, render_command

    engine = _open_engine(path, system=system)
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
    from cirdan.verify import verify_action

    engine = _open_engine(path, system=system)
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

    engine = _open_engine(path, system=system)

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

    engine = _open_engine(path, config_file=config, system=system)
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
    from cirdan.config import resolve_scope

    config, fell_back = resolve_scope(path, system=system)
    return config.output_dir / "cirdand.lock", fell_back


@app.command("status")
@daemon_app.command("status")
def daemon_status(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Machine-level scope (~/.cirdan)."),
):
    """Show whether a cirdand instance is running for this scope."""
    from cirdan.daemon.lock import holder

    lock_path, fell_back = _lock_path_for(path, system)
    if fell_back:
        _scope_notice(path)
    info = holder(lock_path)
    scope = "system scope" if (system or fell_back) else "this project"
    if info is None:
        console.print(f"cirdand: [yellow]not running[/yellow] for {scope}.")
        raise typer.Exit(3)
    from cirdan.util import iso_to_local

    console.print(f"cirdand: [green]running[/green] (pid {info.get('pid')}, "
                  f"since {iso_to_local(info.get('started_at'))})")


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

    lock_path, fell_back = _lock_path_for(path, system)
    if fell_back:
        _scope_notice(path)
    info = holder(lock_path)
    scope = "system scope" if (system or fell_back) else "this project"
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


def _flag_decisions(responder: bool | None, do_map: bool | None, daemon: bool | None,
                    enrich: bool | None = None) -> dict:
    only: dict[str, bool] = {"agents": True, "mcp": True}
    for name, flag in (("responder", responder), ("map", do_map), ("daemon", daemon),
                       ("enrich", enrich)):
        if flag is not None:
            only[name] = flag
    return only


def _setup_summary(root, results: dict, system: bool = False) -> None:
    from cirdan.cli.setup_flow import build_steps

    console.print("\n[bold]Cirdan setup summary[/bold]")
    for step in build_steps(root, console, status_console, system=system):
        done, state = step.status()
        mark = "[green]✓[/green]" if done else "[yellow]·[/yellow]"
        console.print(f"  {mark} {step.name}: {state}")
    flag = " --system" if system else ""
    console.print(f"\nTry: [bold]cirdan query \"what is this running on?\"{flag}[/bold] · "
                  f"[bold]cirdan show \"show me the infrastructure map\"{flag}[/bold] · "
                  f"[bold]cirdan incidents{flag}[/bold]")


@app.command()
def install(
    platform: str = typer.Option(None, "--platform",
                                 help="claude, codex, cursor, gemini, or generic (default: auto-detect)."),
    all_platforms: bool = typer.Option(False, "--all-platforms", help="Install for every platform."),
    project: bool = typer.Option(False, "--project", help="Install into the current repo instead of the home directory."),
    path: str = typer.Option(".", "--path", help="Project root (with --project)."),
    responder: bool = typer.Option(None, "--responder/--no-responder",
                                   help="Wire incident auto-response without prompting."),
    responder_command: str = typer.Option(None, "--responder-command",
                                          help="Custom agent command ({brief_file} placeholder)."),
    do_map: bool = typer.Option(None, "--map/--no-map", help="Run the first map without prompting."),
    daemon: bool = typer.Option(None, "--daemon/--no-daemon", help="Start cirdand without prompting."),
    enrich: bool = typer.Option(None, "--enrich/--no-enrich",
                                help="Run the agent enrichment pass (costs agent tokens)."),
):
    """Set up Cirdan end-to-end: hook detected agents, register MCP, arm the
    responder, map the infrastructure, and start the daemon."""
    from pathlib import Path

    from cirdan.agents import install as do_install
    from cirdan.agents.installer import PLATFORMS, detect_platforms

    root = Path(path).resolve()
    if platform:
        platforms = [platform]
    elif all_platforms:
        platforms = list(PLATFORMS)
    else:
        platforms = detect_platforms()
        console.print(f"Detected agent platforms: [bold]{', '.join(platforms)}[/bold]")

    if not project:
        written = do_install(platforms=platforms, project=False, root=root)
        console.print("Installed Cirdan agent instructions (user scope):")
        for name, paths in written.items():
            console.print(f"  [bold]{name}[/bold]: {', '.join(paths)}")
        console.print("\nFor the full setup (map + daemon + responder), run "
                      "[bold]cirdan install --project[/bold] inside a project.")
        return

    from cirdan.cli.setup_flow import run_guided

    results = run_guided(
        root, console, status_console,
        only=_flag_decisions(responder, do_map, daemon, enrich),
        platforms=platforms,
        responder_command=responder_command,
    )
    _setup_summary(root, results)


@app.command()
def setup(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    all_steps: bool = typer.Option(False, "--all", help="Run every step without prompting."),
    enrich: bool = typer.Option(None, "--enrich/--no-enrich",
                                help="Run the agent enrichment pass (costs agent tokens)."),
):
    """Walk through Cirdan setup again: shows each step's current state and
    (re)runs the ones you pick — agents, MCP, responder, map, daemon, enrich."""
    from pathlib import Path

    from cirdan.cli.setup_flow import run_guided

    root = Path(path).resolve()
    only = {name: True for name in ("agents", "mcp", "responder", "map", "daemon", "enrich")} if all_steps else {}
    if enrich is not None:
        only["enrich"] = enrich
    results = run_guided(root, console, status_console, only=only or None,
                         interactive=None if not all_steps else False, system=system)
    _setup_summary(root, results, system=system)


@app.command("serve-mcp")
def serve_mcp(
    path: str = typer.Argument(".", help="Project root."),
    system: bool = typer.Option(False, "--system", help="Use the machine-level scope (~/.cirdan) instead of a project."),
    transport: str = typer.Option("stdio", "--transport", help="stdio or streamable-http."),
    port: int = typer.Option(8080, "--port", help="Port for HTTP transport."),
):
    """Serve Cirdan as an MCP server (default: stdio, for agent clients)."""
    from cirdan.mcp.server import build_mcp_server

    engine = _open_engine(path, system=system)
    server = build_mcp_server(engine)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.port = port
        server.run(transport="streamable-http")


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
