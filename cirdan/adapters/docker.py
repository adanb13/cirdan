"""Live adapter: Docker Engine via the docker CLI the session already has."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator

from cirdan.adapters.base import ActionSpec, Adapter, Signal
from cirdan.adapters.common import classify_component, node_id
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation
from cirdan.util import parse_json, parse_json_lines, run_cmd

COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


class DockerAdapter(Adapter):
    name = "docker"
    kind = "live"

    def available(self) -> bool:
        return self.access.can("docker_read")

    def fingerprint(self) -> list[Signal]:
        res = run_cmd(["docker", "ps", "-q"], timeout=5)
        if res.ok:
            count = len([l for l in res.stdout.splitlines() if l.strip()])
            return [Signal(system="docker", weight=0.6, evidence=f"{count} running containers")]
        return []

    def _containers(self) -> list[dict]:
        res = run_cmd(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=10)
        if not res.ok:
            return []
        rows = parse_json_lines(res.stdout)
        ids = [r["ID"] for r in rows if r.get("ID")]
        if not ids:
            return []
        inspect = run_cmd(["docker", "inspect", *ids], timeout=15)
        details = parse_json(inspect.stdout) if inspect.ok else None
        return details if isinstance(details, list) else []

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        details = self._containers()
        hostname = socket.gethostname()
        host_id = node_id("host", hostname)
        result.nodes.append(
            Node(
                id=host_id,
                type=NodeType.HOST.value,
                name=hostname,
                origin=Origin.LIVE,
                source_adapter=self.name,
                evidence=["Docker daemon reachable on this host"],
                attrs={"docker_version": self.access.details.get("docker_server_version")},
            )
        )
        for item in details:
            name = (item.get("Name") or "").lstrip("/") or item.get("Id", "")[:12]
            state = item.get("State") or {}
            cfg = item.get("Config") or {}
            labels = cfg.get("Labels") or {}
            image = cfg.get("Image", "")
            health = ((state.get("Health") or {}).get("Status")) if state.get("Health") else None
            ports = []
            for binding in ((item.get("NetworkSettings") or {}).get("Ports") or {}).values():
                for b in binding or []:
                    if b.get("HostPort"):
                        ports.append(b["HostPort"])
            cid = node_id("container", name)
            attrs = {
                "image": image,
                "state": state.get("Status"),
                "health": health,
                "restart_count": item.get("RestartCount", 0),
                "started_at": state.get("StartedAt"),
                "exit_code": state.get("ExitCode"),
                "compose_project": labels.get(COMPOSE_PROJECT_LABEL),
                "compose_service": labels.get(COMPOSE_SERVICE_LABEL),
            }
            if ports:
                attrs["published_ports"] = sorted(set(ports))
                attrs["public"] = True
            result.nodes.append(
                Node(
                    id=cid,
                    type=NodeType.CONTAINER.value,
                    name=name,
                    origin=Origin.LIVE,
                    source_adapter=self.name,
                    evidence=[f"docker inspect {name}"],
                    attrs={k: v for k, v in attrs.items() if v is not None},
                )
            )
            result.edges.append(
                Edge(
                    source=cid,
                    target=host_id,
                    relation=Relation.RUNS_ON,
                    confidence=Confidence.EXTRACTED,
                    evidence=["container runs on local Docker daemon"],
                )
            )
            # Link to the logical service when this container came from compose.
            svc = labels.get(COMPOSE_SERVICE_LABEL)
            if svc:
                svc_type, prefix = classify_component(svc, image)
                sid = node_id(prefix, svc)
                running = state.get("Status") == "running"
                result.nodes.append(
                    Node(
                        id=sid,
                        type=svc_type,
                        name=svc,
                        origin=Origin.LIVE,
                        source_adapter=self.name,
                        evidence=[f"compose service label on container {name}"],
                        attrs={"state": state.get("Status"), "health": health}
                        if running or health
                        else {"state": state.get("Status")},
                    )
                )
                result.edges.append(
                    Edge(
                        source=sid,
                        target=cid,
                        relation=Relation.CREATES,
                        confidence=Confidence.EXTRACTED,
                        evidence=[f"compose label {COMPOSE_SERVICE_LABEL}={svc}"],
                    )
                )
        return result

    def collect_logs(self, scope: str, lines: int = 200) -> list[str]:
        name = scope.split(":", 1)[-1]
        res = run_cmd(["docker", "logs", "--tail", str(lines), name], timeout=15)
        merged = (res.stdout + "\n" + res.stderr).strip()
        return merged.splitlines() if merged else []

    def current_state(self, scope: str) -> dict:
        name = scope.split(":", 1)[-1]
        res = run_cmd(["docker", "inspect", name], timeout=10)
        data = parse_json(res.stdout) if res.ok else None
        if isinstance(data, list) and data:
            state = data[0].get("State") or {}
            return {
                "state": state.get("Status"),
                "health": ((state.get("Health") or {}).get("Status")),
                "restart_count": data[0].get("RestartCount", 0),
                "started_at": state.get("StartedAt"),
                "exit_code": state.get("ExitCode"),
            }
        return {"error": res.stderr.strip() or "not found"}

    def actions(self, node: Node) -> list[ActionSpec]:
        if node.type != NodeType.CONTAINER.value:
            return []
        name = node.name
        specs = [
            ActionSpec(
                id=f"docker.inspect:{name}", node_id=node.id, adapter=self.name,
                name="inspect", description=f"Inspect container {name}",
                argv=["docker", "inspect", name],
            ),
            ActionSpec(
                id=f"docker.logs:{name}", node_id=node.id, adapter=self.name,
                name="logs", description=f"Fetch recent logs from {name}",
                argv=["docker", "logs", "--tail", "200", name],
            ),
        ]
        if self.access.can("docker_write"):
            specs += [
                ActionSpec(
                    id=f"docker.restart:{name}", node_id=node.id, adapter=self.name,
                    name="restart", description=f"Restart container {name}",
                    argv=["docker", "restart", name], writes=True, verify="container_healthy",
                ),
                ActionSpec(
                    id=f"docker.stop:{name}", node_id=node.id, adapter=self.name,
                    name="stop", description=f"Stop container {name}",
                    argv=["docker", "stop", name], writes=True,
                ),
                ActionSpec(
                    id=f"docker.start:{name}", node_id=node.id, adapter=self.name,
                    name="start", description=f"Start container {name}",
                    argv=["docker", "start", name], writes=True, verify="container_healthy",
                ),
            ]
        return specs

    async def watch(self) -> AsyncIterator[dict]:
        proc = await asyncio.create_subprocess_exec(
            "docker", "events", "--format", "{{json .}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                event = parse_json(line.decode(errors="replace"))
                if isinstance(event, dict):
                    yield {
                        "provider": "docker",
                        "type": event.get("Type"),
                        "action": event.get("Action"),
                        "resource": (event.get("Actor") or {}).get("Attributes", {}).get("name"),
                        "raw": event,
                    }
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=3)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.communicate()
            else:
                await proc.communicate()
