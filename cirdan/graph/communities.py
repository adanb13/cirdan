"""Subsystem detection: Louvain communities over the infrastructure graph.

Graphify clusters its code graph with Leiden; for infrastructure the same idea
groups the topology into subsystems (a service with its database, cache, queue,
and ingress tends to land in one community). Louvain ships with networkx —
Leiden would add an igraph dependency for marginal gain at infra-graph sizes.
Computed at export time with a fixed seed, never persisted.
"""

from __future__ import annotations

import networkx as nx

from cirdan.graph.store import GraphStore


def compute_communities(store: GraphStore, resolution: float = 1.0) -> dict[str, int]:
    """node_id → community index, ordered by community size (0 = largest)."""
    multi = store.to_networkx().to_undirected()
    graph = nx.Graph(multi)
    if graph.number_of_nodes() == 0:
        return {}
    from networkx.algorithms.community import louvain_communities

    communities = louvain_communities(graph, resolution=resolution, seed=42)
    ordered = sorted(communities, key=lambda c: (-len(c), min(c)))
    return {node_id: index for index, members in enumerate(ordered) for node_id in members}
