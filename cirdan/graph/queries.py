"""Graph traversal queries used by the CLI, MCP tools, and view generation."""

from __future__ import annotations

import networkx as nx

from cirdan.graph.schema import DEPENDENCY_RELATIONS, Edge, Node, NodeType
from cirdan.graph.store import GraphStore

WORKLOAD_TYPES = {
    NodeType.SERVICE.value,
    NodeType.WORKLOAD.value,
    NodeType.CONTAINER.value,
    NodeType.POD.value,
    NodeType.DEPLOYMENT.value,
    NodeType.STATEFUL_SET.value,
    NodeType.DAEMON_SET.value,
    NodeType.SYSTEMD_UNIT.value,
}

ENTRYPOINT_TYPES = {
    NodeType.LOAD_BALANCER.value,
    NodeType.INGRESS.value,
    NodeType.DNS_RECORD.value,
}

BAD_STATES = {
    "unhealthy", "failed", "crashloopbackoff", "error", "degraded",
    "notready", "absent", "restarting", "dead",
}


class GraphQueries:
    def __init__(self, store: GraphStore):
        self.store = store

    # -- neighborhood -------------------------------------------------------

    def neighbors(self, node_id: str, depth: int = 1) -> dict:
        nodes: dict[str, Node] = {}
        edges: dict[tuple, Edge] = {}
        frontier = {node_id}
        seen = {node_id}
        root = self.store.get_node(node_id)
        if root:
            nodes[root.id] = root
        for _ in range(depth):
            next_frontier: set[str] = set()
            for nid in frontier:
                for edge in self.store.edges_for(nid):
                    edges[edge.key] = edge
                    for other in (edge.source, edge.target):
                        if other not in seen:
                            seen.add(other)
                            next_frontier.add(other)
                            node = self.store.get_node(other)
                            if node:
                                nodes[node.id] = node
            frontier = next_frontier
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    def shortest_path(self, source_id: str, target_id: str) -> list[Node] | None:
        graph = self.store.to_networkx().to_undirected()
        if source_id not in graph or target_id not in graph:
            return None
        try:
            path = nx.shortest_path(graph, source_id, target_id)
        except nx.NetworkXNoPath:
            return None
        return [self.store.get_node(nid) for nid in path]

    # -- dependencies --------------------------------------------------------

    def _dependency_traverse(self, node_id: str, direction: str, max_depth: int = 5) -> list[dict]:
        """Walk dependency-relation edges out (dependencies) or in (dependents)."""
        results: list[dict] = []
        seen = {node_id}
        frontier = [(node_id, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for edge in self.store.edges_for(current, direction="out" if direction == "deps" else "in"):
                if edge.relation not in DEPENDENCY_RELATIONS:
                    continue
                other = edge.target if direction == "deps" else edge.source
                if other in seen:
                    continue
                seen.add(other)
                node = self.store.get_node(other)
                if node:
                    results.append({"node": node, "via": edge, "depth": depth + 1})
                    frontier.append((other, depth + 1))
        return results

    def dependencies(self, node_id: str, max_depth: int = 5) -> list[dict]:
        return self._dependency_traverse(node_id, "deps", max_depth)

    def dependents(self, node_id: str, max_depth: int = 5) -> list[dict]:
        return self._dependency_traverse(node_id, "dependents", max_depth)

    # -- catalog -------------------------------------------------------------

    def services(self) -> list[Node]:
        return sorted(self.store.find_nodes(type=NodeType.SERVICE.value), key=lambda n: n.name)

    def workloads(self) -> list[Node]:
        out = [n for n in self.store.all_nodes() if n.type in WORKLOAD_TYPES]
        return sorted(out, key=lambda n: (n.type, n.name))

    def unhealthy(self) -> list[Node]:
        out = []
        for node in self.store.all_nodes():
            state = str(node.attrs.get("health") or node.attrs.get("state") or "").lower()
            if state in BAD_STATES:
                out.append(node)
            elif node.attrs.get("ready_replicas") is not None and node.attrs.get("replicas") is not None:
                if int(node.attrs["ready_replicas"]) < int(node.attrs["replicas"]):
                    out.append(node)
        return out

    def public_entrypoints(self) -> list[dict]:
        """Things reachable from outside: LBs, ingresses, and published ports."""
        results = []
        for node in self.store.all_nodes():
            reasons = []
            if node.type in ENTRYPOINT_TYPES:
                reasons.append(f"{node.type} resource")
            if node.attrs.get("public"):
                reasons.append("marked public")
            ports = node.attrs.get("published_ports") or []
            if ports:
                reasons.append(f"published ports: {', '.join(str(p) for p in ports)}")
            if node.attrs.get("service_type") in {"LoadBalancer", "NodePort"}:
                reasons.append(f"Kubernetes Service type {node.attrs['service_type']}")
            if reasons:
                results.append({"node": node, "reasons": reasons})
        return sorted(results, key=lambda r: r["node"].name)

    def subgraph(self, node_ids: list[str], depth: int = 1) -> dict:
        nodes: dict[str, Node] = {}
        edges: dict[tuple, Edge] = {}
        for nid in node_ids:
            result = self.neighbors(nid, depth=depth)
            for node in result["nodes"]:
                nodes[node.id] = node
            for edge in result["edges"]:
                edges[edge.key] = edge
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}
