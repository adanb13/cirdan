"""MCP server: Cirdan as shared infrastructure memory for any MCP-capable agent.

Every tool returns JSON-serializable data backed by the same engine the CLI
uses, so agents and humans always see the same graph.
"""

from __future__ import annotations

import json

from cirdan.access.redaction import redact_obj
from cirdan.engine import CirdanEngine


def _dump(obj: object) -> str:
    return json.dumps(redact_obj(obj), indent=1, default=str)


def build_mcp_server(engine: CirdanEngine):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "cirdan",
        instructions=(
            "Cirdan maps and watches the live infrastructure this session can access. "
            "Query it before answering questions about runtime, deployment, services, "
            "dependencies, logs, state, errors, or incidents. Write actions execute "
            "through the session's own access and are recorded and verified."
        ),
    )

    # -- discovery -----------------------------------------------------------

    @mcp.tool()
    def fingerprint_system() -> str:
        """Detect runtimes, clouds, IaC, and telemetry with confidence and evidence."""
        return _dump(engine.refresh_fingerprint().model_dump())

    @mcp.tool()
    def get_access_context() -> str:
        """What the current agent/session can access (capability mirror, not permissions)."""
        return _dump(engine.access.model_dump())

    @mcp.tool()
    def map_infrastructure(live: bool = True) -> str:
        """Run full discovery (static + live) and regenerate all cirdan-out artifacts."""
        summary = engine.map(live=live)
        return _dump({k: summary[k] for k in ("adapters", "live", "nodes", "edges", "findings", "artifacts")})

    # -- graph -----------------------------------------------------------------

    @mcp.tool()
    def query_infra_graph(question: str) -> str:
        """Ask the graph a question, e.g. 'what depends on postgres?' or 'what broke?'."""
        from cirdan.query import answer_query

        return _dump(answer_query(engine, question))

    @mcp.tool()
    def get_node(node_ref: str) -> str:
        """Fetch one node (by id or fuzzy name) with evidence, attributes, and edges."""
        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        edges = engine.store.edges_for(node.id)
        return _dump({"node": node.model_dump(), "edges": [e.model_dump() for e in edges]})

    @mcp.tool()
    def get_neighbors(node_ref: str, depth: int = 1) -> str:
        """Neighborhood subgraph around a node."""
        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        result = engine.queries.neighbors(node.id, depth=depth)
        return _dump({
            "nodes": [n.model_dump() for n in result["nodes"]],
            "edges": [e.model_dump() for e in result["edges"]],
        })

    @mcp.tool()
    def shortest_path(source: str, target: str) -> str:
        """Shortest path between two components."""
        a, b = engine.store.resolve(source), engine.store.resolve(target)
        if a is None or b is None:
            return _dump({"error": "source or target not found"})
        path = engine.queries.shortest_path(a.id, b.id)
        if path is None:
            return _dump({"path": None, "note": "no path"})
        return _dump({"path": [n.model_dump() for n in path]})

    @mcp.tool()
    def list_services() -> str:
        """All logical services with state and origin."""
        return _dump([n.model_dump() for n in engine.queries.services()])

    @mcp.tool()
    def list_dependencies(node_ref: str) -> str:
        """What a component depends on (transitive)."""
        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        deps = engine.queries.dependencies(node.id)
        return _dump([{"node": d["node"].model_dump(), "relation": d["via"].relation.value,
                       "depth": d["depth"]} for d in deps])

    @mcp.tool()
    def list_dependents(node_ref: str) -> str:
        """What depends on a component (transitive blast radius)."""
        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        deps = engine.queries.dependents(node.id)
        return _dump([{"node": d["node"].model_dump(), "relation": d["via"].relation.value,
                       "depth": d["depth"]} for d in deps])

    @mcp.tool()
    def list_public_entrypoints() -> str:
        """Everything reachable from outside, with the reasons."""
        return _dump([{"node": e["node"].model_dump(), "reasons": e["reasons"]}
                      for e in engine.queries.public_entrypoints()])

    @mcp.tool()
    def list_runtime_workloads() -> str:
        """Current workloads (services, containers, pods, units) with live state."""
        return _dump([n.model_dump() for n in engine.queries.workloads()])

    # -- telemetry ----------------------------------------------------------------

    @mcp.tool()
    def get_recent_errors(window_minutes: int = 60, service: str = "") -> str:
        """Recent error/warning events, clustered by message template."""
        from cirdan.telemetry.clusters import cluster_events

        events = engine.events.recent(window_seconds=window_minutes * 60,
                                      severity=("error", "warning"),
                                      service=service or None)
        return _dump({"clusters": cluster_events(events), "total_events": len(events)})

    @mcp.tool()
    def get_logs(node_ref: str, lines: int = 100) -> str:
        """Tail logs for a component through the owning live adapter."""
        from cirdan.adapters.registry import get_adapters

        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        for adapter in get_adapters(engine.config, engine.access, kind="live"):
            try:
                log_lines = adapter.collect_logs(node.id, lines=lines)
            except Exception:
                continue
            if log_lines:
                from cirdan.access.redaction import redact_text

                return _dump({"node": node.id, "adapter": adapter.name,
                              "lines": [redact_text(l) for l in log_lines[-lines:]]})
        return _dump({"node": node.id, "lines": [], "note": "no adapter returned logs"})

    @mcp.tool()
    def get_state(node_ref: str) -> str:
        """Live state of a component through the owning adapter."""
        from cirdan.adapters.registry import get_adapters

        node = engine.store.resolve(node_ref)
        if node is None:
            return _dump({"error": f"no node matching '{node_ref}'"})
        states = {}
        for adapter in get_adapters(engine.config, engine.access, kind="live"):
            try:
                state = adapter.current_state(node.id)
            except Exception:
                continue
            if state:
                states[adapter.name] = state
        return _dump({"node": node.id, "graph_attrs": node.attrs, "live_state": states})

    # -- incidents -------------------------------------------------------------------

    @mcp.tool()
    def list_incidents(include_resolved: bool = False, detect_first: bool = True) -> str:
        """Open (and optionally resolved) incidents. Runs a detection pass by default."""
        if detect_first:
            engine.detect_incidents()
        return _dump([i.model_dump() for i in engine.incidents.list(include_resolved=include_resolved)])

    @mcp.tool()
    def explain_incident(incident_id: str) -> str:
        """Evidence-backed markdown explanation of an incident."""
        report = engine.explain_incident(incident_id)
        return report if report is not None else _dump({"error": f"no incident '{incident_id}'"})

    # -- actions --------------------------------------------------------------------

    @mcp.tool()
    def list_available_actions(node_ref: str) -> str:
        """Actions currently possible against a component with this session's access."""
        from cirdan.actions import list_actions

        return _dump([s.model_dump() for s in list_actions(engine, node_ref)])

    @mcp.tool()
    def execute_action(action_id: str) -> str:
        """Execute an action id from list_available_actions. Recorded, redacted, audited."""
        from cirdan.actions import execute_action as _execute, find_action

        spec = find_action(engine, action_id)
        if spec is None:
            return _dump({"error": f"unknown or unavailable action '{action_id}'"})
        record = _execute(engine, spec)
        return _dump(record.model_dump())

    @mcp.tool()
    def verify_action(record_id: str) -> str:
        """Verify the outcome of a previously executed action (act-… record id)."""
        from cirdan.actions.executor import get_record
        from cirdan.verify import verify_action as _verify

        record = get_record(engine, record_id)
        if record is None:
            return _dump({"error": f"no action record '{record_id}'"})
        return _dump(_verify(engine, record))

    # -- views & audit -----------------------------------------------------------------

    @mcp.tool()
    def generate_view(request: str) -> str:
        """Generate an Agentic UI view ('show me …') and save html/md/json artifacts."""
        from cirdan.ui.render import render_markdown
        from cirdan.ui.router import build_view

        spec = build_view(engine, request)
        paths = engine.save_view(spec)
        return _dump({"view_type": spec.view_type, "title": spec.title,
                      "paths": paths, "markdown": render_markdown(spec)})

    @mcp.tool()
    def generate_report() -> str:
        """Regenerate INFRA_REPORT.md and return its contents."""
        engine.export_artifacts()
        return (engine.config.output_dir / "INFRA_REPORT.md").read_text()

    @mcp.tool()
    def get_audit(limit: int = 50) -> str:
        """Recent audit entries: what Cirdan observed, generated, executed, verified."""
        return _dump(engine.audit.tail(limit))

    return mcp


async def run_stdio(engine: CirdanEngine) -> None:
    server = build_mcp_server(engine)
    await server.run_stdio_async()


def serve_stdio(engine: CirdanEngine) -> None:
    build_mcp_server(engine).run(transport="stdio")
