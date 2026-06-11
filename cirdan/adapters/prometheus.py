"""Live adapter: Prometheus HTTP API for scrape targets and instant queries."""

from __future__ import annotations

import httpx

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation


class PrometheusAdapter(Adapter):
    name = "prometheus"
    kind = "live"

    @property
    def url(self) -> str | None:
        return self.access.details.get("prometheus_url")

    def available(self) -> bool:
        return self.access.can("prometheus_read") and bool(self.url)

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            resp = httpx.get(f"{self.url}{path}", params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return data.get("data")
        except (httpx.HTTPError, ValueError):
            pass
        return None

    def fingerprint(self) -> list[Signal]:
        data = self._get("/api/v1/targets")
        if data is None:
            return []
        active = len(data.get("activeTargets", []))
        return [Signal(system="prometheus", weight=0.6, evidence=f"{active} active scrape targets")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        data = self._get("/api/v1/targets")
        if data is None:
            return result
        by_job: dict[str, list[dict]] = {}
        for target in data.get("activeTargets", []):
            job = (target.get("labels") or {}).get("job", "unknown")
            by_job.setdefault(job, []).append(target)
        for job, targets in by_job.items():
            up = sum(1 for t in targets if t.get("health") == "up")
            nid = f"metrics:{job}"
            result.nodes.append(
                Node(id=nid, type=NodeType.METRIC_SERIES.value, name=f"{job} metrics",
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=[f"Prometheus job '{job}' with {len(targets)} targets at {self.url}"],
                     attrs={"job": job, "targets": len(targets), "targets_up": up,
                            "state": "up" if up == len(targets) else "degraded"})
            )
            result.edges.append(
                Edge(source=nid, target=f"ref:{job}", relation=Relation.OBSERVED_IN,
                     confidence=Confidence.INFERRED,
                     evidence=[f"Prometheus job name '{job}' matches a service name"],
                     attrs={"resolve_only": True,
                            "target_hint": {"type": NodeType.SERVICE.value, "prefix": "service", "host": job}})
            )
        return result

    def query(self, promql: str) -> list[dict]:
        data = self._get("/api/v1/query", params={"query": promql})
        return data.get("result", []) if isinstance(data, dict) else []
