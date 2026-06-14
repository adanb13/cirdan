"""Evidence-backed incident explanation, deterministic (the agent does deeper reasoning)."""

from __future__ import annotations

from cirdan.access.redaction import redact_text
from cirdan.graph.queries import GraphQueries
from cirdan.graph.store import GraphStore
from cirdan.incidents.store import Incident
from cirdan.telemetry.clusters import cluster_events
from cirdan.telemetry.events import EventStore


def explain_incident(
    incident: Incident,
    store: GraphStore,
    events: EventStore,
    window_seconds: float = 7200,
) -> str:
    queries = GraphQueries(store)
    lines = [
        f"# Incident: {incident.title}",
        "",
        f"- **Id:** {incident.id}",
        f"- **Status:** {incident.status}",
        f"- **Severity:** {incident.severity}",
        f"- **Started:** {incident.started_at}",
        f"- **Last update:** {incident.updated_at}",
        "",
        "## Affected",
        "",
    ]
    for nid in incident.affected_nodes:
        node = store.get_node(nid)
        if node:
            state = node.attrs.get("health") or node.attrs.get("state") or "unknown"
            lines.append(f"- **{node.name}** ({node.type}) — current state: {state}")
        else:
            lines.append(f"- {nid}")
    blast = (incident.diagnosis or {}).get("blast_radius") or []
    if blast:
        lines += ["", "## Blast radius (dependents of affected nodes)", ""]
        for nid in blast:
            node = store.get_node(nid)
            lines.append(f"- {node.name if node else nid}")

    lines += ["", "## Evidence", ""]
    for item in incident.evidence:
        lines.append(f"- {item}")

    # Correlated error clusters around the affected services.
    related = []
    for nid in incident.affected_nodes:
        node = store.get_node(nid)
        if node:
            related += events.recent(window_seconds=window_seconds, severity=("error", "warning"),
                                     service=node.name)
    clusters = cluster_events(related)
    if clusters:
        lines += ["", "## Correlated error clusters", ""]
        for cluster in clusters[:5]:
            lines.append(
                f"- `{cluster['template'][:100]}` × {cluster['count']} "
                f"({cluster['first_ts']} → {cluster['last_ts']})"
            )

    # Recent deploy-ish neighbors (pipelines, deploys) for correlation hints.
    correlated: set[str] = set()
    for nid in incident.affected_nodes:
        for entry in queries.neighbors(nid, depth=1)["nodes"]:
            if entry.type in {"Deploy", "Pipeline"}:
                correlated.add(entry.name)
    if correlated:
        lines += ["", "## Possibly related deploy machinery", ""]
        lines += [f"- {name}" for name in sorted(correlated)]

    if incident.history:
        lines += ["", "## Timeline", ""]
        for entry in incident.history:
            lines.append(f"- {entry['ts']} — {entry['status']}: {entry.get('note', '')}")
    if incident.actions:
        lines += ["", "## Actions attempted", ""]
        lines += [f"- {a}" for a in incident.actions]
    return redact_text("\n".join(lines) + "\n")
