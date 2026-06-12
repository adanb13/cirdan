"""Agent contributions to the graph — the LLM lane of the hybrid model.

Deterministic adapters extract; agents *contribute*: relationships read out of
docs, runbooks, and code that no parser rule can see. Guardrails keep the graph
honest — every contribution carries evidence, is capped at INFERRED/AMBIGUOUS
confidence (EXTRACTED is reserved for deterministic adapters; merge keeps the
strongest label so an agent can never upgrade a claim), and is attributed to
the contributing agent.
"""

from __future__ import annotations

import re

from cirdan.access.redaction import redact_text
from cirdan.engine import CirdanEngine
from cirdan.graph.schema import Confidence, Edge, Node, NodeType, Origin, Relation

_NODE_ID_RE = re.compile(r"^[a-z0-9_-]+:.+$")
_AGENT_CONFIDENCES = (Confidence.INFERRED, Confidence.AMBIGUOUS)


class ContributionError(ValueError):
    """Rejection with a message the contributing agent can act on."""


def _clean_evidence(evidence: list[str] | None) -> list[str]:
    if not evidence or not isinstance(evidence, list):
        raise ContributionError(
            "evidence is required: a non-empty list of strings, e.g. "
            "['ARCHITECTURE.md says payment-worker consumes the orders queue']"
        )
    cleaned = [redact_text(str(item).strip()) for item in evidence if str(item).strip()]
    if not cleaned:
        raise ContributionError("evidence items must be non-empty strings")
    return cleaned


def _check_confidence(confidence: Confidence | str) -> Confidence:
    try:
        confidence = Confidence(confidence)
    except ValueError:
        raise ContributionError(
            f"invalid confidence '{confidence}'; agents may use: "
            + " or ".join(c.value for c in _AGENT_CONFIDENCES)
        ) from None
    if confidence not in _AGENT_CONFIDENCES:
        raise ContributionError(
            f"agents may not claim {confidence.value}; use INFERRED (or AMBIGUOUS). "
            "EXTRACTED is reserved for deterministic adapters."
        )
    return confidence


def contribute_node(
    engine: CirdanEngine,
    node_id: str,
    type: str,
    name: str,
    evidence: list[str],
    attrs: dict | None = None,
    agent: str = "unknown",
    confidence: Confidence | str = Confidence.INFERRED,
) -> Node:
    evidence = _clean_evidence(evidence)
    confidence = _check_confidence(confidence)
    if not _NODE_ID_RE.match(node_id or ""):
        raise ContributionError(
            f"node_id '{node_id}' must look like 'prefix:name', e.g. 'queue:orders' or 'service:billing'"
        )
    valid_types = {t.value for t in NodeType}
    if type not in valid_types:
        raise ContributionError(f"invalid node type '{type}'; valid: {', '.join(sorted(valid_types))}")
    if not name or not str(name).strip():
        raise ContributionError("name is required")
    node = engine.store.upsert_node(
        Node(
            id=node_id,
            type=type,
            name=str(name).strip(),
            origin=Origin.STATIC,  # declared knowledge; merges to BOTH if live discovery confirms
            source_adapter=f"agent:{agent}",
            confidence=confidence,
            evidence=evidence,
            attrs=attrs or {},
        )
    )
    engine.audit.write("contribution", f"agent:{agent} contributed node {node_id}",
                       evidence=evidence[:2])
    return node


def contribute_edge(
    engine: CirdanEngine,
    source: str,
    target: str,
    relation: Relation | str,
    evidence: list[str],
    attrs: dict | None = None,
    agent: str = "unknown",
    confidence: Confidence | str = Confidence.INFERRED,
) -> Edge:
    evidence = _clean_evidence(evidence)
    confidence = _check_confidence(confidence)
    try:
        relation = Relation(relation)
    except ValueError:
        raise ContributionError(
            f"invalid relation '{relation}'; valid: {', '.join(r.value for r in Relation)}"
        ) from None
    source_node = engine.store.resolve(source)
    if source_node is None:
        raise ContributionError(
            f"source '{source}' does not match any node — create it first with "
            "add-node/upsert_node, or check the name with `cirdan query`"
        )
    target_node = engine.store.resolve(target)
    if target_node is None:
        raise ContributionError(
            f"target '{target}' does not match any node — create it first with "
            "add-node/upsert_node, or check the name with `cirdan query`"
        )
    if source_node.id == target_node.id:
        raise ContributionError("source and target resolve to the same node")
    edge_attrs = dict(attrs or {})
    edge_attrs["source_adapter"] = f"agent:{agent}"  # edges have no source_adapter column
    edge = engine.store.upsert_edge(
        Edge(
            source=source_node.id,
            target=target_node.id,
            relation=relation,
            confidence=confidence,
            evidence=evidence,
            attrs=edge_attrs,
        )
    )
    engine.audit.write(
        "contribution",
        f"agent:{agent} contributed edge {source_node.id} —{relation.value}→ {target_node.id}",
        evidence=evidence[:2],
    )
    return edge


def annotate_node(
    engine: CirdanEngine,
    ref: str,
    evidence: list[str] | None = None,
    attrs: dict | None = None,
    agent: str = "unknown",
) -> Node:
    """Add evidence/attributes to an existing node without disturbing its
    origin or confidence (mutates the loaded model and re-upserts)."""
    node = engine.store.resolve(ref)
    if node is None:
        raise ContributionError(f"no node matching '{ref}'")
    if not evidence and not attrs:
        raise ContributionError("nothing to annotate: provide evidence and/or attrs")
    if evidence:
        node.evidence = list(dict.fromkeys(node.evidence + _clean_evidence(evidence)))
    if attrs:
        node.attrs = {**node.attrs, **attrs}
    node.attrs.setdefault("annotated_by", f"agent:{agent}")
    merged = engine.store.upsert_node(node)
    engine.audit.write("contribution", f"agent:{agent} annotated {node.id}")
    return merged


def agent_contributions(engine: CirdanEngine) -> dict:
    """Everything in the graph that an agent (rather than an adapter) put there."""
    nodes = [n for n in engine.store.all_nodes() if n.source_adapter.startswith("agent:")]
    edges = [
        e for e in engine.store.all_edges()
        if str(e.attrs.get("source_adapter", "")).startswith("agent:")
    ]
    return {"nodes": nodes, "edges": edges}
