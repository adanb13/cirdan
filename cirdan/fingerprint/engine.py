"""Fingerprint engine: answers "where am I, and what is this system?"

Signals come from two places: live access probes (can we talk to Docker, the
Kubernetes API, AWS STS, ...) and static adapters (compose files, manifests,
Terraform, charts found in the repo). Per-system confidence combines signal
weights as 1 - prod(1 - w), so several weak signals add up but never reach 1.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from cirdan.access.context import AccessContext
from cirdan.adapters.base import Signal
from cirdan.adapters.registry import get_adapters
from cirdan.config import CirdanConfig
from cirdan.util import now_iso

RUNTIME_SYSTEMS = ("kubernetes", "docker", "docker-compose", "systemd")
CLOUD_SYSTEMS = ("aws", "azure", "gcp")


class DetectedSystem(BaseModel):
    type: str
    confidence: float
    evidence: list[str]


class Fingerprint(BaseModel):
    detected_at: str = Field(default_factory=now_iso)
    detected_systems: list[DetectedSystem] = Field(default_factory=list)
    primary_runtime: str | None = None
    primary_cloud: str | None = None
    iac: list[str] = Field(default_factory=list)
    telemetry: list[str] = Field(default_factory=list)
    access_context: dict = Field(default_factory=dict)

    def confidence_for(self, system: str) -> float:
        for det in self.detected_systems:
            if det.type == system:
                return det.confidence
        return 0.0


def access_signals(access: AccessContext) -> list[Signal]:
    caps = access.capabilities
    signals: list[Signal] = []

    def add(cond: bool, system: str, weight: float, evidence: str) -> None:
        if cond:
            signals.append(Signal(system=system, weight=weight, evidence=evidence))

    add(caps.get("docker_socket", False), "docker", 0.5, "/var/run/docker.sock is accessible")
    add(caps.get("docker_read", False), "docker", 0.85, "Docker daemon responded to the CLI")
    add(caps.get("kubeconfig", False), "kubernetes", 0.4, "kubeconfig available")
    add(caps.get("kubernetes_in_cluster", False), "kubernetes", 0.7, "in-cluster Kubernetes service account environment")
    add(caps.get("kubernetes_read", False), "kubernetes", 0.85, "kubectl can list namespaces")
    add(caps.get("aws_read", False), "aws", 0.85, "AWS caller identity succeeded")
    add(
        caps.get("aws_credentials", False) and not caps.get("aws_read", False),
        "aws", 0.3, "AWS credentials present but identity not verified",
    )
    add(caps.get("azure_config", False), "azure", 0.25, "Azure CLI configuration directory present")
    add(caps.get("gcloud_config", False), "gcp", 0.25, "gcloud configuration directory present")
    add(caps.get("systemd", False), "systemd", 0.6, "systemd is the running init system")
    add(caps.get("journald", False), "journald", 0.6, "journalctl available")
    add(caps.get("prometheus_read", False), "prometheus", 0.85,
        f"Prometheus responded at {access.details.get('prometheus_url', 'detected endpoint')}")
    return signals


def score(signals: list[Signal]) -> list[DetectedSystem]:
    by_system: dict[str, list[Signal]] = {}
    for sig in signals:
        by_system.setdefault(sig.system, []).append(sig)
    detected = []
    for system, sigs in by_system.items():
        miss = math.prod(1.0 - min(max(s.weight, 0.0), 0.99) for s in sigs)
        confidence = round(min(1.0 - miss, 0.99), 2)
        evidence = list(dict.fromkeys(s.evidence for s in sigs))
        detected.append(DetectedSystem(type=system, confidence=confidence, evidence=evidence))
    detected.sort(key=lambda d: d.confidence, reverse=True)
    return detected


def fingerprint_environment(config: CirdanConfig, access: AccessContext) -> Fingerprint:
    signals = access_signals(access)
    for adapter in get_adapters(config, access):
        try:
            signals.extend(adapter.fingerprint())
        except Exception:
            continue

    detected = score(signals)
    conf = {d.type: d.confidence for d in detected}

    def best(candidates: tuple[str, ...], threshold: float = 0.4) -> str | None:
        ranked = sorted(
            ((conf.get(c, 0.0), c) for c in candidates), reverse=True
        )
        top_conf, top = ranked[0]
        return top if top_conf >= threshold else None

    primary_runtime = best(RUNTIME_SYSTEMS)
    if primary_runtime == "docker-compose":
        primary_runtime = "docker"

    return Fingerprint(
        detected_systems=detected,
        primary_runtime=primary_runtime,
        primary_cloud=best(CLOUD_SYSTEMS),
        iac=sorted(s for s in ("terraform", "opentofu", "helm") if conf.get(s, 0) >= 0.4),
        telemetry=sorted(s for s in ("prometheus", "opentelemetry", "journald") if conf.get(s, 0) >= 0.4),
        access_context={
            "source": access.source,
            "mode": access.mode,
            "capabilities": access.capabilities,
        },
    )


def render_fingerprint(fp: Fingerprint) -> str:
    lines = ["Cirdan fingerprinted the environment.", "", "Detected:"]
    if not fp.detected_systems:
        lines.append("  nothing recognizable — empty or fully restricted environment")
    for det in fp.detected_systems:
        lines.append(f"  - {det.type}, confidence {int(det.confidence * 100)}%")
        for ev in det.evidence[:3]:
            lines.append(f"      · {ev}")
    lines += [
        "",
        f"Primary runtime: {fp.primary_runtime or 'unknown'}",
        f"Primary cloud:   {fp.primary_cloud or 'none detected'}",
    ]
    if fp.iac:
        lines.append(f"IaC:             {', '.join(fp.iac)}")
    if fp.telemetry:
        lines.append(f"Telemetry:       {', '.join(fp.telemetry)}")
    return "\n".join(lines) + "\n"
