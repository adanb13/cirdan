"""Deterministic show-request routing: "show me X" → ViewSpec, no LLM required.

The calling agent is the language model; Cirdan only needs to map intent
keywords onto its closed set of view types and resolve the subject node.
"""

from __future__ import annotations

import re

from cirdan.engine import CirdanEngine
from cirdan.access.context import render_access_report
from cirdan.graph.schema import Node
from cirdan.ui.view_spec import ViewComponent, ViewSpec, graph_component_data

_STOPWORDS = {
    "show", "me", "the", "a", "an", "of", "for", "as", "all", "this", "that", "what",
    "map", "graph", "view", "system", "infrastructure", "infra", "dependency",
    "dependencies", "depends", "on", "to", "from", "last", "night", "nights",
    "incident", "incidents", "timeline", "public", "entry", "points", "exposed",
    "state", "status", "health", "running", "is", "are", "and", "everything",
    "service", "services", "make", "generate", "topology", "with", "in",
}


def _resolve_subject(engine: CirdanEngine, request: str) -> Node | None:
    tokens = re.findall(r"[a-zA-Z0-9_.-]+", request.lower())
    candidates = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    best: Node | None = None
    for token in candidates:
        node = engine.store.resolve(token)
        if node and (best is None or len(node.name) > len(best.name)):
            best = node
    return best


def build_view(engine: CirdanEngine, request: str) -> ViewSpec:
    text = request.lower()
    subject = _resolve_subject(engine, request)

    if re.search(r"incident|outage|broke|failure|timeline|overnight|happened", text):
        return _incident_view(engine, subject)
    if re.search(r"depend|connect|chain|upstream|downstream", text):
        return _dependency_view(engine, subject, request)
    if re.search(r"public|exposed|entry\s*point|attack\s*surface", text):
        return _entrypoints_view(engine)
    if re.search(r"access|permission|capabilit|can\s+(do|the agent)", text):
        return _access_view(engine)
    if re.search(r"state|status|health|running|unhealthy|failing", text):
        return _state_view(engine)
    if subject is not None and not re.search(r"map|topology|everything", text):
        return _dependency_view(engine, subject, request)
    return _topology_view(engine)


def _topology_view(engine: CirdanEngine, title: str = "Infrastructure map") -> ViewSpec:
    nodes = engine.store.all_nodes()
    edges = engine.store.all_edges()
    fp = engine.fingerprint
    facts = {
        "Primary runtime": fp.primary_runtime or "unknown",
        "Primary cloud": fp.primary_cloud or "none",
        "Nodes": str(len(nodes)),
        "Edges": str(len(edges)),
    }
    return ViewSpec(
        view_type="topology",
        title=title,
        components=[
            ViewComponent(type="SummaryCard", title="Overview", data={"text": "", "facts": facts}),
            ViewComponent(type="TopologyGraph", title="Topology", data=graph_component_data(nodes, edges)),
        ],
    )


def _dependency_view(engine: CirdanEngine, subject: Node | None, request: str) -> ViewSpec:
    if subject is None:
        spec = _topology_view(engine, title="Infrastructure map (no specific component matched)")
        spec.components.insert(
            0,
            ViewComponent(
                type="Markdown",
                data={"text": f"Could not match a component in: “{request}” — showing the full map."},
            ),
        )
        return spec
    deps = engine.queries.dependencies(subject.id)
    dependents = engine.queries.dependents(subject.id)
    neighborhood = engine.queries.neighbors(subject.id, depth=2)
    rows = [
        [d["node"].name, d["node"].type, d["via"].relation.value, d["via"].confidence.value,
         "; ".join(d["via"].evidence[:1])]
        for d in deps
    ]
    dependent_rows = [
        [d["node"].name, d["node"].type, d["via"].relation.value, d["via"].confidence.value,
         "; ".join(d["via"].evidence[:1])]
        for d in dependents
    ]
    return ViewSpec(
        view_type="dependency",
        title=f"{subject.name}: dependencies",
        scope={"node": subject.id},
        components=[
            ViewComponent(
                type="SummaryCard",
                title=subject.name,
                data={
                    "text": f"{subject.type} · origin {subject.origin.value} · confidence {subject.confidence.value}",
                    "facts": {
                        "Depends on": str(len(deps)),
                        "Depended on by": str(len(dependents)),
                        "State": str(subject.attrs.get("health") or subject.attrs.get("state") or "unknown"),
                    },
                },
            ),
            ViewComponent(
                type="DependencyGraph",
                title="Neighborhood (depth 2)",
                data={"root": subject.id, **graph_component_data(neighborhood["nodes"], neighborhood["edges"])},
            ),
            ViewComponent(
                type="StateTable",
                title=f"{subject.name} depends on",
                data={"columns": ["Component", "Type", "Relation", "Confidence", "Evidence"], "rows": rows},
            ),
            ViewComponent(
                type="StateTable",
                title=f"Depends on {subject.name}",
                data={"columns": ["Component", "Type", "Relation", "Confidence", "Evidence"], "rows": dependent_rows},
            ),
        ],
    )


def _entrypoints_view(engine: CirdanEngine) -> ViewSpec:
    entries = engine.queries.public_entrypoints()
    rows = [
        [e["node"].name, e["node"].type, e["node"].origin.value, "; ".join(e["reasons"])]
        for e in entries
    ]
    return ViewSpec(
        view_type="entrypoints",
        title="Public entry points",
        components=[
            ViewComponent(
                type="SummaryCard",
                data={"text": f"{len(rows)} components are reachable from outside.", "facts": {}},
            ),
            ViewComponent(
                type="StateTable",
                title="Entry points",
                data={"columns": ["Component", "Type", "Origin", "Why it is public"], "rows": rows},
            ),
        ],
    )


def _access_view(engine: CirdanEngine) -> ViewSpec:
    return ViewSpec(
        view_type="access",
        title="Access report",
        components=[ViewComponent(type="AccessReport", data={"text": render_access_report(engine.access)})],
    )


def _state_view(engine: CirdanEngine) -> ViewSpec:
    from cirdan.graph.queries import BAD_STATES

    rows = []
    for node in engine.queries.workloads():
        state = node.attrs.get("health") or node.attrs.get("state") or "unknown"
        replicas = ""
        if node.attrs.get("replicas") is not None:
            replicas = f"{node.attrs.get('ready_replicas', '?')}/{node.attrs['replicas']}"
        rows.append([node.name, node.type, node.origin.value, str(state), replicas])
    rows.sort(key=lambda r: (r[3].lower() not in BAD_STATES, r[3].lower() in {"running", "healthy"}, r[0]))
    findings = engine.drift()
    finding_rows = [[f.severity, f.kind, f.summary] for f in findings]
    components = [
        ViewComponent(
            type="StateTable",
            title="Workload state",
            data={"columns": ["Component", "Type", "Origin", "State", "Ready"], "rows": rows},
        )
    ]
    if finding_rows:
        components.append(
            ViewComponent(
                type="StateTable",
                title="Findings",
                data={"columns": ["Severity", "Kind", "Summary"], "rows": finding_rows},
            )
        )
    return ViewSpec(view_type="state", title="Runtime state", components=components)


def _incident_view(engine: CirdanEngine, subject: Node | None) -> ViewSpec:
    incidents = engine.incident_list(include_resolved=True)
    if subject is not None:
        incidents = [
            i for i in incidents
            if subject.id in (i.get("affected_nodes") or []) or subject.name in str(i.get("title", ""))
        ] or incidents
    entries = []
    for inc in incidents[:20]:
        entries.append(
            {
                "ts": inc.get("started_at", ""),
                "label": inc.get("title", inc.get("id", "incident")),
                "detail": (inc.get("diagnosis") or {}).get("summary") or f"status: {inc.get('status')}",
                "severity": inc.get("severity", "warning"),
            }
        )
    components: list[ViewComponent] = [
        ViewComponent(
            type="SummaryCard",
            data={
                "text": f"{len([i for i in incidents if i.get('status') != 'resolved'])} active, "
                        f"{len(incidents)} total in memory.",
                "facts": {},
            },
        ),
        ViewComponent(type="IncidentTimeline", title="Incidents", data={"entries": entries}),
    ]
    if subject is not None:
        neighborhood = engine.queries.neighbors(subject.id, depth=1)
        components.append(
            ViewComponent(
                type="DependencyGraph",
                title=f"Around {subject.name}",
                data={"root": subject.id, **graph_component_data(neighborhood["nodes"], neighborhood["edges"])},
            )
        )
    return ViewSpec(view_type="incident_timeline", title="Incident timeline", components=components)
