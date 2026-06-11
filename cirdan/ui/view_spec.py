"""View specs: structured descriptions of generated UI, rendered to HTML/MD/terminal.

A view is data plus layout intent — never arbitrary code. Component types are a
closed set so every renderer knows how to draw each one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cirdan.util import now_iso

COMPONENT_TYPES = {
    "SummaryCard",       # data: {text, facts: {label: value}}
    "TopologyGraph",     # data: {nodes: [...], edges: [...]}
    "DependencyGraph",   # data: {root, nodes, edges}
    "StateTable",        # data: {columns: [...], rows: [[...]]}
    "LogClusterTable",   # data: {columns, rows}
    "IncidentTimeline",  # data: {entries: [{ts, label, detail, severity}]}
    "MetricChart",       # data: {metric, points: [{ts, value}]} (rendered as table/sparkline)
    "AccessReport",      # data: {text}
    "ActionPanel",       # data: {actions: [{id, name, description, writes, command}]}
    "Markdown",          # data: {text}
}


class ViewComponent(BaseModel):
    type: str
    title: str = ""
    data: dict = Field(default_factory=dict)


class ViewSpec(BaseModel):
    view_type: str
    title: str
    scope: dict = Field(default_factory=dict)
    components: list[ViewComponent] = Field(default_factory=list)
    generated_at: str = Field(default_factory=now_iso)


def graph_component_data(nodes: list, edges: list) -> dict:
    """Convert graph Node/Edge models to the plain payload graph components use."""
    return {
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "type": n.type,
                "origin": n.origin.value,
                "confidence": n.confidence.value,
                "state": n.attrs.get("health") or n.attrs.get("state"),
                "evidence": n.evidence[:5],
                "attrs": {k: v for k, v in n.attrs.items() if isinstance(v, (str, int, float, bool))},
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "relation": e.relation.value,
                "confidence": e.confidence.value,
                "evidence": e.evidence[:3],
            }
            for e in edges
        ],
    }
