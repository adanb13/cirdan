"""Static adapter: Kubernetes YAML manifests → declared workloads, services, ingresses."""

from __future__ import annotations

import yaml

from cirdan.adapters.base import Adapter, Signal
from cirdan.adapters.common import classify_component, infer_connections, node_id
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job"}
HANDLED_KINDS = WORKLOAD_KINDS | {"Service", "Ingress", "Namespace", "ConfigMap"}


class K8sManifestsAdapter(Adapter):
    name = "kubernetes-manifests"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _documents(self) -> list[tuple[str, dict]]:
        docs: list[tuple[str, dict]] = []
        for path in self.walk_files(".yaml", ".yml"):
            if path.name.startswith(("docker-compose", "compose")):
                continue
            try:
                text = path.read_text()
            except OSError:
                continue
            if "apiVersion" not in text or "kind" not in text:
                continue
            try:
                for doc in yaml.safe_load_all(text):
                    if isinstance(doc, dict) and doc.get("kind") in HANDLED_KINDS and doc.get("apiVersion"):
                        docs.append((self.rel(path), doc))
            except yaml.YAMLError:
                continue
        return docs

    def fingerprint(self) -> list[Signal]:
        docs = self._documents()
        signals = []
        if docs:
            kinds = sorted({d.get("kind", "?") for _, d in docs})
            signals.append(
                Signal(
                    system="kubernetes",
                    weight=0.5,
                    evidence=f"{len(docs)} Kubernetes manifests in repo ({', '.join(kinds[:6])})",
                )
            )
        return signals

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        docs = self._documents()
        for rel, doc in docs:
            kind = doc["kind"]
            meta = doc.get("metadata") or {}
            name = meta.get("name", "unnamed")
            namespace = meta.get("namespace", "default")
            evidence = [f"{kind} '{name}' in {rel}"]
            if kind == "Namespace":
                result.nodes.append(
                    Node(
                        id=node_id("namespace", name),
                        type=NodeType.NAMESPACE.value,
                        name=name,
                        origin=Origin.STATIC,
                        source_adapter=self.name,
                        evidence=evidence,
                    )
                )
            elif kind in WORKLOAD_KINDS:
                self._workload(result, doc, kind, name, namespace, rel, evidence)
            elif kind == "Service":
                spec = doc.get("spec") or {}
                node_type, prefix = classify_component(name)
                attrs = {"kubernetes_kind": "Service", "namespace": namespace}
                svc_type = spec.get("type", "ClusterIP")
                attrs["service_type"] = svc_type
                ports = [str(p.get("port")) for p in spec.get("ports", []) or [] if isinstance(p, dict)]
                if ports:
                    attrs["ports"] = ports
                if svc_type in {"LoadBalancer", "NodePort"}:
                    attrs["public"] = True
                result.nodes.append(
                    Node(
                        id=node_id(prefix, name),
                        type=node_type,
                        name=name,
                        origin=Origin.STATIC,
                        source_adapter=self.name,
                        evidence=evidence,
                        attrs=attrs,
                    )
                )
            elif kind == "Ingress":
                ingress_id = node_id("ingress", name)
                hosts = []
                backends: set[str] = set()
                for rule in (doc.get("spec") or {}).get("rules", []) or []:
                    if rule.get("host"):
                        hosts.append(rule["host"])
                    for http_path in (rule.get("http") or {}).get("paths", []) or []:
                        svc = ((http_path.get("backend") or {}).get("service") or {}).get("name")
                        if svc:
                            backends.add(svc)
                result.nodes.append(
                    Node(
                        id=ingress_id,
                        type=NodeType.INGRESS.value,
                        name=name,
                        origin=Origin.STATIC,
                        source_adapter=self.name,
                        evidence=evidence,
                        attrs={"namespace": namespace, "hosts": hosts, "public": True},
                    )
                )
                for backend in sorted(backends):
                    result.edges.append(
                        Edge(
                            source=ingress_id,
                            target=f"ref:{backend}",
                            relation=Relation.ROUTES_TO,
                            confidence=Confidence.EXTRACTED,
                            evidence=[f"Ingress '{name}' backend service '{backend}' in {rel}"],
                            attrs={"target_hint": {"type": NodeType.SERVICE.value, "prefix": "service", "host": backend}},
                        )
                    )
        return result

    def _workload(self, result: DiscoveryResult, doc: dict, kind: str, name: str,
                  namespace: str, rel: str, evidence: list[str]) -> None:
        spec = doc.get("spec") or {}
        node_type, prefix = classify_component(name)
        nid = node_id(prefix, name)
        attrs: dict = {"kubernetes_kind": kind, "namespace": namespace}
        if "replicas" in spec:
            attrs["replicas"] = spec.get("replicas")
        template = spec.get("template") or (spec.get("jobTemplate") or {}).get("spec", {}).get("template") or {}
        containers = ((template.get("spec") or {}).get("containers")) or []
        images = [c.get("image") for c in containers if isinstance(c, dict) and c.get("image")]
        if images:
            attrs["images"] = images
        result.nodes.append(
            Node(
                id=nid,
                type=node_type,
                name=name,
                origin=Origin.STATIC,
                source_adapter=self.name,
                evidence=evidence,
                attrs=attrs,
            )
        )
        result.edges.append(
            Edge(
                source=node_id("namespace", namespace),
                target=nid,
                relation=Relation.CONTAINS,
                confidence=Confidence.EXTRACTED,
                evidence=[f"namespace '{namespace}' in {rel}"],
            )
        )
        env: dict[str, str] = {}
        for container in containers:
            for item in (container.get("env") or []) if isinstance(container, dict) else []:
                if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                    env[str(item["name"])] = str(item["value"])
        for ref in infer_connections(env, rel):
            result.edges.append(
                Edge(
                    source=nid,
                    target=f"ref:{ref.name}",
                    relation=Relation.CONNECTS_TO,
                    confidence=Confidence.INFERRED,
                    evidence=[ref.evidence],
                    attrs={"target_hint": {"type": ref.node_type, "prefix": ref.prefix, "host": ref.host}},
                )
            )
