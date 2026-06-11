"""Live adapter: Kubernetes via the kubectl access the session already has."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from cirdan.adapters.base import ActionSpec, Adapter, Signal
from cirdan.adapters.common import classify_component, node_id
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation
from cirdan.util import now_iso, parse_json, run_cmd

KUBECTL_TIMEOUT = 15


def _kubectl_json(args: list[str], timeout: float = KUBECTL_TIMEOUT) -> dict | None:
    res = run_cmd(["kubectl", *args, "-o", "json", "--request-timeout=10s"], timeout=timeout)
    if not res.ok:
        return None
    data = parse_json(res.stdout)
    return data if isinstance(data, dict) else None


class KubernetesAdapter(Adapter):
    name = "kubernetes"
    kind = "live"

    def available(self) -> bool:
        return self.access.can("kubernetes_read")

    def fingerprint(self) -> list[Signal]:
        data = _kubectl_json(["get", "deployments", "-A"])
        if data is None:
            return []
        count = len(data.get("items", []))
        return [Signal(system="kubernetes", weight=0.7, evidence=f"{count} deployments discovered via kubectl")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        context = self.access.details.get("kubernetes_context", "cluster")
        cluster_id = node_id("cluster", context)
        result.nodes.append(
            Node(
                id=cluster_id,
                type=NodeType.CLUSTER.value,
                name=context,
                origin=Origin.LIVE,
                source_adapter=self.name,
                evidence=[f"kubectl context '{context}'"],
            )
        )
        self._nodes(result, cluster_id)
        self._namespaces(result, cluster_id)
        deploy_by_ns = self._workloads(result)
        self._pods(result, deploy_by_ns)
        self._services(result)
        self._ingresses(result)
        return result

    def _nodes(self, result: DiscoveryResult, cluster_id: str) -> None:
        data = _kubectl_json(["get", "nodes"])
        for item in (data or {}).get("items", []):
            name = item["metadata"]["name"]
            conditions = {c["type"]: c["status"] for c in (item.get("status", {}).get("conditions") or [])}
            ready = conditions.get("Ready") == "True"
            nid = node_id("k8s-node", name)
            result.nodes.append(
                Node(
                    id=nid,
                    type=NodeType.COMPUTE_NODE.value,
                    name=name,
                    origin=Origin.LIVE,
                    source_adapter=self.name,
                    evidence=["kubectl get nodes"],
                    attrs={
                        "state": "ready" if ready else "notready",
                        "conditions": {k: v for k, v in conditions.items() if v == "True"},
                        "kubelet_version": item.get("status", {}).get("nodeInfo", {}).get("kubeletVersion"),
                    },
                )
            )
            result.edges.append(
                Edge(source=cluster_id, target=nid, relation=Relation.CONTAINS,
                     confidence=Confidence.EXTRACTED, evidence=["node registered in cluster"])
            )

    def _namespaces(self, result: DiscoveryResult, cluster_id: str) -> None:
        data = _kubectl_json(["get", "namespaces"])
        for item in (data or {}).get("items", []):
            name = item["metadata"]["name"]
            nid = node_id("namespace", name)
            result.nodes.append(
                Node(
                    id=nid, type=NodeType.NAMESPACE.value, name=name,
                    origin=Origin.LIVE, source_adapter=self.name,
                    evidence=["kubectl get namespaces"],
                )
            )
            result.edges.append(
                Edge(source=cluster_id, target=nid, relation=Relation.CONTAINS,
                     confidence=Confidence.EXTRACTED, evidence=["namespace in cluster"])
            )

    def _workloads(self, result: DiscoveryResult) -> dict[tuple[str, str], str]:
        """Deployments/StatefulSets/DaemonSets → logical service nodes. Returns (ns, name) → node id."""
        mapping: dict[tuple[str, str], str] = {}
        for kind in ("deployments", "statefulsets", "daemonsets"):
            data = _kubectl_json(["get", kind, "-A"])
            for item in (data or {}).get("items", []):
                meta = item["metadata"]
                name, namespace = meta["name"], meta.get("namespace", "default")
                spec, status = item.get("spec", {}), item.get("status", {})
                node_type, prefix = classify_component(name)
                nid = node_id(prefix, name)
                mapping[(namespace, name)] = nid
                replicas = spec.get("replicas")
                ready = status.get("readyReplicas", 0)
                containers = (spec.get("template", {}).get("spec", {}) or {}).get("containers", []) or []
                images = [c.get("image") for c in containers if c.get("image")]
                attrs = {
                    "kubernetes_kind": kind.rstrip("s").capitalize(),
                    "namespace": namespace,
                    "images": images,
                    "ready_replicas": ready,
                }
                if replicas is not None:
                    attrs["replicas"] = replicas
                    attrs["state"] = "ready" if ready >= replicas else "degraded"
                result.nodes.append(
                    Node(
                        id=nid, type=node_type, name=name,
                        origin=Origin.LIVE, source_adapter=self.name,
                        evidence=[f"kubectl get {kind} -A: {namespace}/{name}"],
                        attrs=attrs,
                    )
                )
                result.edges.append(
                    Edge(source=node_id("namespace", namespace), target=nid,
                         relation=Relation.CONTAINS, confidence=Confidence.EXTRACTED,
                         evidence=[f"in namespace {namespace}"])
                )
        return mapping

    def _pods(self, result: DiscoveryResult, deploy_by_ns: dict[tuple[str, str], str]) -> None:
        data = _kubectl_json(["get", "pods", "-A"])
        for item in (data or {}).get("items", []):
            meta = item["metadata"]
            name, namespace = meta["name"], meta.get("namespace", "default")
            status = item.get("status", {})
            phase = status.get("phase")
            container_statuses = status.get("containerStatuses") or []
            restarts = sum(c.get("restartCount", 0) for c in container_statuses)
            ready = all(c.get("ready") for c in container_statuses) if container_statuses else False
            waiting_reasons = [
                ((c.get("state") or {}).get("waiting") or {}).get("reason")
                for c in container_statuses
            ]
            waiting_reasons = [w for w in waiting_reasons if w]
            state = phase.lower() if phase else "unknown"
            if "CrashLoopBackOff" in waiting_reasons:
                state = "crashloopbackoff"
            elif phase == "Running" and not ready:
                state = "notready"
            pid = node_id("k8s-pod", f"{namespace}/{name}")
            result.nodes.append(
                Node(
                    id=pid, type=NodeType.POD.value, name=name,
                    origin=Origin.LIVE, source_adapter=self.name,
                    evidence=["kubectl get pods -A"],
                    attrs={
                        "namespace": namespace,
                        "state": state,
                        "restarts": restarts,
                        "node": item.get("spec", {}).get("nodeName"),
                        "waiting_reasons": waiting_reasons,
                    },
                )
            )
            node_name = item.get("spec", {}).get("nodeName")
            if node_name:
                result.edges.append(
                    Edge(source=pid, target=node_id("k8s-node", node_name),
                         relation=Relation.RUNS_ON, confidence=Confidence.EXTRACTED,
                         evidence=["pod scheduled on node"])
                )
            owner = self._owner_workload(item, namespace)
            if owner and (namespace, owner) in deploy_by_ns:
                result.edges.append(
                    Edge(source=deploy_by_ns[(namespace, owner)], target=pid,
                         relation=Relation.CREATES, confidence=Confidence.EXTRACTED,
                         evidence=["pod ownerReferences"])
                )

    @staticmethod
    def _owner_workload(pod: dict, namespace: str) -> str | None:
        for ref in pod["metadata"].get("ownerReferences", []) or []:
            kind, name = ref.get("kind"), ref.get("name", "")
            if kind == "ReplicaSet":
                return name.rsplit("-", 1)[0]  # deployment name
            if kind in {"StatefulSet", "DaemonSet", "Job"}:
                return name
        return None

    def _services(self, result: DiscoveryResult) -> None:
        data = _kubectl_json(["get", "services", "-A"])
        for item in (data or {}).get("items", []):
            meta = item["metadata"]
            name, namespace = meta["name"], meta.get("namespace", "default")
            if namespace == "kube-system" or name == "kubernetes":
                continue
            spec = item.get("spec", {})
            node_type, prefix = classify_component(name)
            attrs = {
                "namespace": namespace,
                "service_type": spec.get("type", "ClusterIP"),
                "cluster_ip": spec.get("clusterIP"),
            }
            if spec.get("type") in {"LoadBalancer", "NodePort"}:
                attrs["public"] = True
            result.nodes.append(
                Node(
                    id=node_id(prefix, name), type=node_type, name=name,
                    origin=Origin.LIVE, source_adapter=self.name,
                    evidence=[f"kubectl get services -A: {namespace}/{name}"],
                    attrs=attrs,
                )
            )

    def _ingresses(self, result: DiscoveryResult) -> None:
        data = _kubectl_json(["get", "ingresses", "-A"])
        for item in (data or {}).get("items", []):
            meta = item["metadata"]
            name, namespace = meta["name"], meta.get("namespace", "default")
            iid = node_id("ingress", name)
            hosts, backends = [], set()
            for rule in (item.get("spec", {}).get("rules") or []):
                if rule.get("host"):
                    hosts.append(rule["host"])
                for p in ((rule.get("http") or {}).get("paths") or []):
                    svc = ((p.get("backend") or {}).get("service") or {}).get("name")
                    if svc:
                        backends.add(svc)
            result.nodes.append(
                Node(
                    id=iid, type=NodeType.INGRESS.value, name=name,
                    origin=Origin.LIVE, source_adapter=self.name,
                    evidence=["kubectl get ingresses -A"],
                    attrs={"namespace": namespace, "hosts": hosts, "public": True},
                )
            )
            for backend in sorted(backends):
                result.edges.append(
                    Edge(source=iid, target=f"ref:{backend}", relation=Relation.ROUTES_TO,
                         confidence=Confidence.EXTRACTED, evidence=[f"ingress backend {backend}"],
                         attrs={"target_hint": {"type": NodeType.SERVICE.value, "prefix": "service", "host": backend}})
                )

    # -- telemetry/state -----------------------------------------------------

    def collect_logs(self, scope: str, lines: int = 200) -> list[str]:
        node = self._scope_to_ref(scope)
        if not node:
            return []
        kind, namespace, name = node
        if kind == "pod":
            res = run_cmd(["kubectl", "logs", "-n", namespace, name, "--tail", str(lines)], timeout=20)
        else:
            res = run_cmd(["kubectl", "logs", "-n", namespace, f"deploy/{name}", "--tail", str(lines)], timeout=20)
        return res.stdout.splitlines() if res.ok else []

    def current_state(self, scope: str) -> dict:
        node = self._scope_to_ref(scope)
        if not node:
            return {}
        kind, namespace, name = node
        kubectl_kind = "pod" if kind == "pod" else "deployment"
        data = _kubectl_json(["get", kubectl_kind, "-n", namespace, name])
        if not data:
            return {"error": "not found"}
        return {"status": data.get("status", {}), "checked_at": now_iso()}

    def _scope_to_ref(self, scope: str) -> tuple[str, str, str] | None:
        """scope: 'k8s-pod:ns/name' | 'service:name' | plain name."""
        if scope.startswith("k8s-pod:"):
            ns_name = scope.split(":", 1)[1]
            if "/" in ns_name:
                namespace, name = ns_name.split("/", 1)
                return ("pod", namespace, name)
        name = scope.split(":", 1)[-1]
        namespace = self._find_namespace(name)
        return ("deployment", namespace or "default", name)

    def _find_namespace(self, workload: str) -> str | None:
        data = _kubectl_json(["get", "deployments", "-A"])
        for item in (data or {}).get("items", []):
            if item["metadata"]["name"] == workload:
                return item["metadata"].get("namespace")
        return None

    def recent_events(self, since_resource_version: str | None = None) -> list[dict]:
        data = _kubectl_json(["get", "events", "-A", "--sort-by=.metadata.creationTimestamp"])
        events = []
        for item in (data or {}).get("items", []):
            involved = item.get("involvedObject", {})
            events.append(
                {
                    "provider": "kubernetes",
                    "type": item.get("type"),
                    "reason": item.get("reason"),
                    "message": item.get("message"),
                    "resource": f"{involved.get('kind', '?')}/{involved.get('name', '?')}",
                    "namespace": involved.get("namespace"),
                    "timestamp": item.get("lastTimestamp") or item.get("eventTime"),
                    "count": item.get("count", 1),
                }
            )
        return events

    async def watch(self) -> AsyncIterator[dict]:
        """Poll Kubernetes events; the API watch needs a client lib, polling keeps us CLI-only."""
        seen: set[tuple] = set()
        first_pass = True
        while True:
            for event in self.recent_events():
                key = (event["resource"], event["reason"], event["timestamp"])
                if key in seen:
                    continue
                seen.add(key)
                if not first_pass:
                    yield event
            first_pass = False
            if len(seen) > 5000:
                seen = set(list(seen)[-2000:])
            await asyncio.sleep(15)

    # -- actions ---------------------------------------------------------------

    def actions(self, node: Node) -> list[ActionSpec]:
        specs: list[ActionSpec] = []
        namespace = node.attrs.get("namespace", "default")
        if node.type == NodeType.POD.value:
            name = node.name
            specs += [
                ActionSpec(id=f"k8s.logs:{namespace}/{name}", node_id=node.id, adapter=self.name,
                           name="logs", description=f"Logs from pod {namespace}/{name}",
                           argv=["kubectl", "logs", "-n", namespace, name, "--tail", "200"]),
                ActionSpec(id=f"k8s.describe:{namespace}/{name}", node_id=node.id, adapter=self.name,
                           name="describe", description=f"Describe pod {namespace}/{name}",
                           argv=["kubectl", "describe", "pod", "-n", namespace, name]),
            ]
            if self.access.can("kubernetes_write"):
                specs.append(
                    ActionSpec(id=f"k8s.delete-pod:{namespace}/{name}", node_id=node.id, adapter=self.name,
                               name="delete-pod",
                               description=f"Delete pod {namespace}/{name} (controller recreates it)",
                               argv=["kubectl", "delete", "pod", "-n", namespace, name],
                               writes=True, verify="workload_ready")
                )
        elif node.attrs.get("kubernetes_kind") in {"Deployment", "Statefulset", "Daemonset"}:
            kind = node.attrs["kubernetes_kind"].lower()
            name = node.name
            specs.append(
                ActionSpec(id=f"k8s.rollout-status:{namespace}/{name}", node_id=node.id, adapter=self.name,
                           name="rollout-status", description=f"Rollout status of {kind}/{name}",
                           argv=["kubectl", "rollout", "status", f"{kind}/{name}", "-n", namespace, "--timeout=5s"])
            )
            if self.access.can("kubernetes_write"):
                specs.append(
                    ActionSpec(id=f"k8s.rollout-restart:{namespace}/{name}", node_id=node.id, adapter=self.name,
                               name="rollout-restart", description=f"Rolling restart of {kind}/{name}",
                               argv=["kubectl", "rollout", "restart", f"{kind}/{name}", "-n", namespace],
                               writes=True, verify="workload_ready")
                )
        return specs
