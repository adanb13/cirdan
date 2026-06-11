"""JSON artifact exports: infra.graph.json, services.json, dependencies.json, graph.schema.json."""

from __future__ import annotations

from pathlib import Path

from cirdan.access.redaction import redact_obj
from cirdan.graph.queries import GraphQueries
from cirdan.graph.schema import Edge, Node, NodeType
from cirdan.graph.store import GraphStore
from cirdan.util import dump_json, now_iso


def graph_payload(store: GraphStore) -> dict:
    nodes = store.all_nodes()
    edges = store.all_edges()
    return redact_obj(
        {
            "generated_at": now_iso(),
            "generator": "cirdan",
            "counts": {"nodes": len(nodes), "edges": len(edges)},
            "nodes": [n.model_dump() for n in nodes],
            "edges": [e.model_dump() for e in edges],
        }
    )


def export_graph(store: GraphStore, out_dir: Path) -> Path:
    path = out_dir / "infra.graph.json"
    path.write_text(dump_json(graph_payload(store)))
    return path


def export_services(store: GraphStore, out_dir: Path) -> Path:
    queries = GraphQueries(store)
    services = []
    for node in queries.services():
        deps = queries.dependencies(node.id, max_depth=1)
        services.append(
            {
                "id": node.id,
                "name": node.name,
                "origin": node.origin.value,
                "confidence": node.confidence.value,
                "attrs": node.attrs,
                "evidence": node.evidence,
                "direct_dependencies": [d["node"].id for d in deps],
            }
        )
    path = out_dir / "services.json"
    path.write_text(dump_json(redact_obj({"generated_at": now_iso(), "services": services})))
    return path


def export_dependencies(store: GraphStore, out_dir: Path) -> Path:
    from cirdan.graph.schema import DEPENDENCY_RELATIONS

    deps = [
        {
            "source": e.source,
            "target": e.target,
            "relation": e.relation.value,
            "confidence": e.confidence.value,
            "evidence": e.evidence,
        }
        for e in store.all_edges()
        if e.relation in DEPENDENCY_RELATIONS
    ]
    path = out_dir / "dependencies.json"
    path.write_text(dump_json(redact_obj({"generated_at": now_iso(), "dependencies": deps})))
    return path


def export_schema(out_dir: Path) -> Path:
    payload = {
        "node": Node.model_json_schema(),
        "edge": Edge.model_json_schema(),
        "node_types": [t.value for t in NodeType],
    }
    path = out_dir / "graph.schema.json"
    path.write_text(dump_json(payload))
    return path


def export_runtime_state(store: GraphStore, out_dir: Path) -> Path:
    live = [
        {
            "id": n.id,
            "type": n.type,
            "name": n.name,
            "state": n.attrs.get("state"),
            "health": n.attrs.get("health"),
            "attrs": n.attrs,
            "last_seen": n.last_seen,
        }
        for n in store.all_nodes()
        if n.origin.value in ("live", "both")
    ]
    path = out_dir / "runtime-state.json"
    path.write_text(dump_json(redact_obj({"generated_at": now_iso(), "resources": live})))
    return path
