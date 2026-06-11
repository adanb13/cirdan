"""Static adapter: Helm charts → chart nodes and declared chart dependencies."""

from __future__ import annotations

import yaml

from cirdan.adapters.base import Adapter, Signal
from cirdan.adapters.common import classify_component, node_id
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, Origin, Relation


class HelmAdapter(Adapter):
    name = "helm"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _charts(self) -> list[tuple[str, dict]]:
        charts = []
        for path in self.walk_files(names=("chart.yaml",)):
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            if isinstance(data, dict) and data.get("name"):
                charts.append((self.rel(path), data))
        return charts

    def fingerprint(self) -> list[Signal]:
        return [
            Signal(system="helm", weight=0.7, evidence=f"Helm chart '{data.get('name')}' at {rel}")
            for rel, data in self._charts()
        ] + (
            [Signal(system="kubernetes", weight=0.3, evidence="Helm charts imply Kubernetes")]
            if self._charts()
            else []
        )

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for rel, data in self._charts():
            chart_name = str(data["name"])
            node_type, prefix = classify_component(chart_name)
            nid = node_id(prefix, chart_name)
            result.nodes.append(
                Node(
                    id=nid,
                    type=node_type,
                    name=chart_name,
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    evidence=[f"Helm chart at {rel}"],
                    attrs={"helm_chart": True, "chart_version": data.get("version")},
                )
            )
            for dep in data.get("dependencies", []) or []:
                if not isinstance(dep, dict) or not dep.get("name"):
                    continue
                dep_name = str(dep["name"])
                dep_type, dep_prefix = classify_component(dep_name)
                dep_id = node_id(dep_prefix, dep_name)
                result.nodes.append(
                    Node(
                        id=dep_id,
                        type=dep_type,
                        name=dep_name,
                        origin=Origin.STATIC,
                        source_adapter=self.name,
                        confidence=Confidence.INFERRED,
                        evidence=[f"chart dependency of '{chart_name}' in {rel}"],
                        attrs={"helm_chart": True},
                    )
                )
                result.edges.append(
                    Edge(
                        source=nid,
                        target=dep_id,
                        relation=Relation.DEPENDS_ON,
                        confidence=Confidence.EXTRACTED,
                        evidence=[f"dependencies in {rel}"],
                    )
                )
        return result
