"""Static-vs-live drift detection: what the repo declares versus what is running."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cirdan.graph.schema import NodeType, Origin
from cirdan.graph.store import GraphStore

# Which static declarations each live system should be able to confirm.
DECLARED_BY_SYSTEM = {
    "docker": {"docker-compose"},
    "kubernetes": {"kubernetes-manifests"},
    "systemd": {"systemd-units"},
}

RUNTIME_NODE_TYPES = {
    NodeType.SERVICE.value, NodeType.DATABASE.value, NodeType.CACHE.value,
    NodeType.QUEUE.value, NodeType.LOAD_BALANCER.value, NodeType.SYSTEMD_UNIT.value,
}


class Finding(BaseModel):
    kind: str
    severity: str  # info | warning | critical
    node_id: str
    summary: str
    evidence: list[str] = Field(default_factory=list)


def compute_drift(store: GraphStore, live_systems: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    checkable_adapters: set[str] = set()
    for system in live_systems:
        checkable_adapters |= DECLARED_BY_SYSTEM.get(system, set())

    for node in store.all_nodes():
        attrs = node.attrs
        # Declared replica count vs observed ready replicas.
        replicas, ready = attrs.get("replicas"), attrs.get("ready_replicas")
        if node.origin in (Origin.BOTH, Origin.LIVE) and replicas is not None and ready is not None:
            if int(ready) < int(replicas):
                findings.append(
                    Finding(
                        kind="degraded_capacity", severity="warning", node_id=node.id,
                        summary=f"{node.name} declares {replicas} replicas but only {ready} are ready",
                        evidence=[f"declared replicas={replicas}", f"ready replicas={ready}"],
                    )
                )
        # Declared but no live counterpart, when the matching live adapter ran.
        if (
            node.origin == Origin.STATIC
            and node.type in RUNTIME_NODE_TYPES
            and node.source_adapter in checkable_adapters
            and not attrs.get("external")
        ):
            if attrs.get("live_state") == "absent":
                findings.append(
                    Finding(
                        kind="disappeared", severity="critical", node_id=node.id,
                        summary=f"{node.name} was running earlier but is now gone",
                        evidence=[f"declared in {node.source_adapter}", "previously observed live"],
                    )
                )
            else:
                findings.append(
                    Finding(
                        kind="declared_not_running", severity="warning", node_id=node.id,
                        summary=f"{node.name} is declared ({node.source_adapter}) but nothing matching is running",
                        evidence=node.evidence[:2],
                    )
                )
        # Running with no declaration anywhere in the repo.
        if (
            node.origin == Origin.LIVE
            and node.type == NodeType.CONTAINER.value
            and not attrs.get("compose_service")
        ):
            findings.append(
                Finding(
                    kind="undeclared_runtime", severity="info", node_id=node.id,
                    summary=f"container {node.name} is running but not declared in the repo",
                    evidence=node.evidence[:2],
                )
            )
        # Unhealthy live state.
        state = str(attrs.get("health") or attrs.get("state") or "").lower()
        if state in {"unhealthy", "failed", "crashloopbackoff", "exited", "notready"} and node.origin != Origin.STATIC:
            findings.append(
                Finding(
                    kind="unhealthy", severity="critical" if state != "exited" else "warning",
                    node_id=node.id,
                    summary=f"{node.type} {node.name} is {state}",
                    evidence=node.evidence[:2],
                )
            )
    return findings
