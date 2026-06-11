"""Graph builder: runs adapters, applies results, resolves inferred references.

Adapters may emit edges pointing at `ref:<name>` placeholders when they infer a
dependency on something they did not themselves discover. The builder resolves
those by name against the whole graph, creating an INFERRED node when nothing
matches (e.g. an external RDS hostname seen only in an env var).
"""

from __future__ import annotations

from cirdan.access.context import AccessContext
from cirdan.adapters.base import Adapter
from cirdan.adapters.registry import get_adapters
from cirdan.audit import AuditWriter
from cirdan.config import CirdanConfig
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, Origin
from cirdan.graph.store import GraphStore


class GraphBuilder:
    def __init__(
        self,
        config: CirdanConfig,
        access: AccessContext,
        store: GraphStore,
        audit: AuditWriter | None = None,
    ):
        self.config = config
        self.access = access
        self.store = store
        self.audit = audit

    def _apply_result(self, adapter: Adapter, result: DiscoveryResult) -> tuple[int, int]:
        pending_refs: list[Edge] = []
        for node in result.nodes:
            self.store.upsert_node(node)
        for edge in result.edges:
            if edge.target.startswith("ref:") or edge.source.startswith("ref:"):
                pending_refs.append(edge)
            else:
                self.store.upsert_edge(edge)
        for edge in pending_refs:
            self._resolve_ref_edge(adapter, edge)
        if adapter.kind == "live":
            seen = {n.id for n in result.nodes}
            self.store.sync_live_absent(adapter.name, seen)
        return len(result.nodes), len(result.edges)

    def _resolve_ref_edge(self, adapter: Adapter, edge: Edge) -> None:
        name = (edge.target if edge.target.startswith("ref:") else edge.source).split(":", 1)[1]
        node = self.store.resolve(name)
        if node is None and edge.attrs.get("resolve_only"):
            return
        if node is None:
            hint = edge.attrs.get("target_hint") or {}
            node = self.store.upsert_node(
                Node(
                    id=f"{hint.get('prefix', 'service')}:{name}",
                    type=hint.get("type", "Service"),
                    name=name,
                    origin=Origin.STATIC,
                    source_adapter=adapter.name,
                    confidence=Confidence.INFERRED,
                    evidence=[f"referenced as '{hint.get('host', name)}' but not declared anywhere Cirdan can see"],
                    attrs={"external": True} if "." in str(hint.get("host", "")) else {},
                )
            )
        attrs = {k: v for k, v in edge.attrs.items() if k not in ("target_hint", "resolve_only")}
        if edge.target.startswith("ref:"):
            resolved = edge.model_copy(update={"target": node.id, "attrs": attrs})
        else:
            resolved = edge.model_copy(update={"source": node.id, "attrs": attrs})
        if resolved.source != resolved.target:
            self.store.upsert_edge(resolved)

    def _run(self, kind: str) -> dict:
        summary: dict[str, dict] = {}
        for adapter in get_adapters(self.config, self.access, kind=kind):
            try:
                result = adapter.discover()
            except Exception as exc:  # one broken adapter must not kill the map
                summary[adapter.name] = {"error": str(exc)}
                if self.audit:
                    self.audit.write("adapter-error", f"{adapter.name} discovery failed", error=str(exc))
                continue
            nodes, edges = self._apply_result(adapter, result)
            summary[adapter.name] = {"nodes": nodes, "edges": edges}
            if self.audit and (nodes or edges):
                self.audit.write(
                    "discovery", f"{adapter.name} discovered {nodes} nodes, {edges} edges", kind=kind
                )
        return summary

    def run_static(self) -> dict:
        return self._run("static")

    def run_live(self) -> dict:
        return self._run("live")
