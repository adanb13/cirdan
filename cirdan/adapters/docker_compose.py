"""Static adapter: Docker Compose files → declared services and dependencies."""

from __future__ import annotations

from pathlib import Path

import yaml

from cirdan.adapters.base import Adapter, Signal
from cirdan.adapters.common import classify_component, infer_connections, node_id
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


class DockerComposeAdapter(Adapter):
    name = "docker-compose"
    kind = "static"

    def _compose_files(self) -> list[Path]:
        found = [p for p in self.walk_files(names=COMPOSE_NAMES)]
        # Also docker-compose.<env>.yml overrides
        for path in self.walk_files(".yml", ".yaml"):
            if path.name.startswith(("docker-compose.", "compose.")) and path not in found:
                found.append(path)
        return found

    def available(self) -> bool:
        return self.access.can("file_read")

    def fingerprint(self) -> list[Signal]:
        return [
            Signal(system="docker-compose", weight=0.8, evidence=f"compose file: {self.rel(p)}")
            for p in self._compose_files()
        ] + [
            Signal(system="docker", weight=0.4, evidence=f"compose file implies Docker: {self.rel(p)}")
            for p in self._compose_files()[:1]
        ]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for path in self._compose_files():
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            services = data.get("services")
            if not isinstance(services, dict):
                continue
            rel = self.rel(path)
            name_to_id: dict[str, str] = {}
            for svc_name, svc in services.items():
                if not isinstance(svc, dict):
                    svc = {}
                image = str(svc.get("image", ""))
                node_type, prefix = classify_component(svc_name, image)
                nid = node_id(prefix, svc_name)
                name_to_id[svc_name] = nid
                published = []
                for port in svc.get("ports", []) or []:
                    text = str(port.get("published", port.get("target", ""))) if isinstance(port, dict) else str(port)
                    if text:
                        published.append(text.split(":")[0])
                attrs = {
                    "image": image or None,
                    "compose_file": rel,
                    "declared_by": "docker-compose",
                }
                if published:
                    attrs["published_ports"] = published
                    attrs["public"] = True
                if svc.get("deploy", {}) and isinstance(svc.get("deploy"), dict):
                    replicas = svc["deploy"].get("replicas")
                    if replicas is not None:
                        attrs["replicas"] = replicas
                result.nodes.append(
                    Node(
                        id=nid,
                        type=node_type,
                        name=svc_name,
                        origin=Origin.STATIC,
                        source_adapter=self.name,
                        confidence=Confidence.EXTRACTED,
                        evidence=[f"service '{svc_name}' defined in {rel}"],
                        attrs={k: v for k, v in attrs.items() if v is not None},
                    )
                )
            # Edges in a second pass so all names are known.
            for svc_name, svc in services.items():
                if not isinstance(svc, dict):
                    continue
                source = name_to_id[svc_name]
                depends = svc.get("depends_on") or []
                dep_names = list(depends.keys()) if isinstance(depends, dict) else list(depends)
                for dep in dep_names:
                    if dep in name_to_id:
                        result.edges.append(
                            Edge(
                                source=source,
                                target=name_to_id[dep],
                                relation=Relation.DEPENDS_ON,
                                confidence=Confidence.EXTRACTED,
                                evidence=[f"depends_on in {rel}"],
                            )
                        )
                env = svc.get("environment") or {}
                if isinstance(env, list):
                    env = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
                if isinstance(env, dict):
                    for ref in infer_connections({str(k): str(v) for k, v in env.items()}, rel):
                        target = name_to_id.get(ref.name)
                        result.edges.append(
                            Edge(
                                source=source,
                                target=target or f"ref:{ref.name}",
                                relation=Relation.CONNECTS_TO,
                                confidence=Confidence.INFERRED,
                                evidence=[ref.evidence],
                                attrs={} if target else {
                                    "target_hint": {"type": ref.node_type, "prefix": ref.prefix, "host": ref.host}
                                },
                            )
                        )
        return result
