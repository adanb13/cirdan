"""Static adapter: nginx configs → reverse-proxy nodes and upstream routes."""

from __future__ import annotations

import re

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation

SERVER_RE = re.compile(r"server\s*\{")
SERVER_NAME_RE = re.compile(r"server_name\s+([^;]+);")
LISTEN_RE = re.compile(r"listen\s+([^;]+);")
PROXY_PASS_RE = re.compile(r"proxy_pass\s+([a-z]+)://([a-zA-Z0-9_.-]+)(?::(\d+))?[^;]*;")


class NginxAdapter(Adapter):
    name = "nginx"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _confs(self) -> list[tuple[str, str]]:
        out = []
        for path in self.walk_files(".conf"):
            try:
                text = path.read_text()
            except OSError:
                continue
            if SERVER_RE.search(text) and ("nginx" in path.name.lower() or "proxy_pass" in text or "server_name" in text):
                out.append((self.rel(path), text))
        return out

    def fingerprint(self) -> list[Signal]:
        confs = self._confs()
        if not confs:
            return []
        return [Signal(system="nginx", weight=0.6, evidence=f"nginx config: {confs[0][0]}")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for rel, text in self._confs():
            names = SERVER_NAME_RE.findall(text)
            display = names[0].split()[0] if names else rel.rsplit("/", 1)[-1]
            nid = f"loadbalancer:nginx-{display}"
            listens = [l.strip() for l in LISTEN_RE.findall(text)]
            result.nodes.append(
                Node(
                    id=nid,
                    type=NodeType.LOAD_BALANCER.value,
                    name=f"nginx ({display})",
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    evidence=[f"server block in {rel}"],
                    attrs={"listen": listens, "server_names": names, "public": True, "role": "reverse-proxy"},
                )
            )
            for scheme, host, port in PROXY_PASS_RE.findall(text):
                result.edges.append(
                    Edge(
                        source=nid,
                        target=f"ref:{host.split('.')[0]}",
                        relation=Relation.ROUTES_TO,
                        confidence=Confidence.INFERRED,
                        evidence=[f"proxy_pass {scheme}://{host} in {rel}"],
                        attrs={"target_hint": {"type": NodeType.SERVICE.value, "prefix": "service", "host": host}},
                    )
                )
        return result
