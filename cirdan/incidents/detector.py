"""Incident detection: drift findings + unhealthy state + error clusters → incidents.

Detection is idempotent: each condition maps to a dedup key, existing open
incidents are updated rather than duplicated, and incidents whose condition
has cleared move to verifying → resolved on subsequent passes.
"""

from __future__ import annotations

import hashlib

from cirdan.graph.diff import Finding
from cirdan.graph.queries import GraphQueries
from cirdan.graph.store import GraphStore
from cirdan.incidents.store import Incident, IncidentStore
from cirdan.telemetry.clusters import cluster_events
from cirdan.telemetry.events import EventStore
from cirdan.util import now_iso

ERROR_CLUSTER_THRESHOLD = 5

_SEVERITY = {"critical": "critical", "warning": "high", "info": "warning"}


def _incident_id(key: str) -> str:
    digest = hashlib.sha1(key.encode()).hexdigest()[:6]
    return f"inc-{now_iso()[:10]}-{digest}"


def _condition_keys_from_findings(findings: list[Finding]) -> dict[str, dict]:
    conditions: dict[str, dict] = {}
    for finding in findings:
        if finding.kind in {"undeclared_runtime"}:  # informational, not an incident
            continue
        key = f"{finding.kind}:{finding.node_id}"
        conditions[key] = {
            "title": finding.summary,
            "severity": _SEVERITY.get(finding.severity, "warning"),
            "affected": [finding.node_id],
            "signals": [finding.kind],
            "evidence": finding.evidence,
        }
    return conditions


def _condition_keys_from_clusters(store: GraphStore, clusters: list[dict]) -> dict[str, dict]:
    conditions: dict[str, dict] = {}
    for cluster in clusters:
        if cluster["severity"] != "error" or cluster["count"] < ERROR_CLUSTER_THRESHOLD:
            continue
        affected: list[str] = []
        for name in cluster["services"]:
            node = store.resolve(name)
            if node:
                affected.append(node.id)
        affected += [r for r in cluster["resources"] if store.get_node(r)]
        subject = cluster["services"][0] if cluster["services"] else (
            cluster["resources"][0] if cluster["resources"] else "unknown"
        )
        key = f"error_cluster:{cluster['key']}"
        conditions[key] = {
            "title": f"error spike in {subject}: {cluster['template'][:80]}",
            "severity": "high",
            "affected": sorted(set(affected)),
            "signals": ["error_cluster"],
            "evidence": [
                f"{cluster['count']} occurrences between {cluster['first_ts']} and {cluster['last_ts']}",
                f"sample: {cluster['sample'][:200]}",
            ],
        }
    return conditions


def detect_incidents(
    store: GraphStore,
    incident_store: IncidentStore,
    findings: list[Finding],
    events: EventStore,
    window_seconds: float = 3600,
) -> list[Incident]:
    """One detection pass. Returns incidents that were created or updated."""
    clusters = cluster_events(events.recent(window_seconds=window_seconds, severity=("error", "warning")))
    conditions = _condition_keys_from_findings(findings)
    conditions.update(_condition_keys_from_clusters(store, clusters))

    touched: list[Incident] = []
    queries = GraphQueries(store)

    for key, cond in conditions.items():
        incident = incident_store.find_open_by_key(key)
        if incident is None:
            incident = Incident(
                id=_incident_id(key),
                key=key,
                title=cond["title"],
                severity=cond["severity"],
                affected_nodes=cond["affected"],
                signals=cond["signals"],
                evidence=cond["evidence"],
            )
            incident.transition("active" if cond["severity"] in {"high", "critical"} else "suspicious",
                                "condition detected")
        else:
            incident.updated_at = now_iso()
            incident.evidence = list(dict.fromkeys(incident.evidence + cond["evidence"]))
            if incident.status == "verifying":
                incident.transition("active", "condition still present after action")
        # Blast radius: who depends on the affected nodes.
        dependents: set[str] = set()
        for nid in incident.affected_nodes:
            for d in queries.dependents(nid, max_depth=2):
                dependents.add(d["node"].id)
        if dependents:
            incident.diagnosis.setdefault("blast_radius", sorted(dependents))
        incident_store.upsert(incident)
        touched.append(incident)

    # Close incidents whose condition cleared.
    open_keys = set(conditions)
    for incident in incident_store.list(include_resolved=False):
        if incident.key in open_keys:
            continue
        if incident.status in {"active", "suspicious", "investigating"}:
            incident.transition("verifying", "condition no longer detected")
            incident_store.upsert(incident)
            touched.append(incident)
        elif incident.status == "verifying":
            incident.transition("resolved", "condition stayed clear")
            incident_store.upsert(incident)
            touched.append(incident)
    return touched
