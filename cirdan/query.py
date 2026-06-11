"""Deterministic natural-language-ish query routing over the graph.

Returns both a human-readable answer and structured data. Anything that
doesn't match a known intent returns the relevant subgraph so the calling
agent (which is the LLM) can reason over it.
"""

from __future__ import annotations

import re

from cirdan.engine import CirdanEngine
from cirdan.fingerprint.engine import render_fingerprint
from cirdan.graph.schema import Node

_STOPWORDS = {
    "what", "which", "who", "is", "are", "the", "this", "that", "on", "in", "of",
    "depends", "depend", "dependents", "does", "do", "can", "system", "running",
    "agent", "here", "broke", "failing", "failed", "unhealthy", "services",
    "service", "exposed", "publicly", "public", "infrastructure", "last", "hour",
    "to", "with", "and", "a", "an", "everything", "show", "list", "all",
}


def _subject(engine: CirdanEngine, text: str) -> Node | None:
    tokens = re.findall(r"[a-zA-Z0-9_.-]+", text.lower())
    best: Node | None = None
    for token in tokens:
        if token in _STOPWORDS or len(token) < 3:
            continue
        node = engine.store.resolve(token)
        if node and (best is None or len(node.name) > len(best.name)):
            best = node
    return best


def answer_query(engine: CirdanEngine, question: str) -> dict:
    text = question.lower().strip()
    subject = _subject(engine, text)

    if re.search(r"(what|which).*(running on|infrastructure|system is|platform)|where am i", text):
        return _what_is_this(engine)
    if re.search(r"depends on|dependents|what uses|who uses|relies on", text) and subject:
        if re.search(r"what does|depend(s)? on what|its dependencies", text):
            return _dependencies(engine, subject)
        return _dependents(engine, subject)
    if re.search(r"depend", text) and subject:
        return _dependencies(engine, subject)
    if re.search(r"public|exposed|entry\s?point|internet|attack surface", text):
        return _entrypoints(engine)
    if re.search(r"broke|broken|failing|failed|unhealthy|wrong|crash|error", text):
        return _whats_broken(engine)
    if re.search(r"(what|which).*(can|able).*(do|access)|capabilit|permission", text):
        return _what_can_agent_do(engine)
    if subject is not None:
        return _describe(engine, subject)
    counts: dict[str, int] = {}
    for node in engine.store.all_nodes():
        counts[node.type] = counts.get(node.type, 0) + 1
    return {
        "answer": (
            "No matching intent or component. The graph holds "
            + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            + ". Try `cirdan query \"what depends on <name>\"`, or use `infra.graph.json` directly."
        ),
        "data": {"node_type_counts": counts},
    }


def _what_is_this(engine: CirdanEngine) -> dict:
    fp = engine.fingerprint
    return {"answer": render_fingerprint(fp).rstrip(), "data": fp.model_dump()}


def _dependents(engine: CirdanEngine, subject: Node) -> dict:
    deps = engine.queries.dependents(subject.id)
    direct = [d for d in deps if d["depth"] == 1]
    lines = [f"{subject.name} has {len(direct)} direct dependents:"]
    lines += [f"- {d['node'].name} ({d['via'].relation.value}, {d['via'].confidence.value})" for d in direct]
    indirect = [d for d in deps if d["depth"] > 1]
    if indirect:
        lines.append(f"…and {len(indirect)} transitive: " + ", ".join(d["node"].name for d in indirect))
    if not deps:
        lines = [f"Nothing in the graph depends on {subject.name}."]
    return {
        "answer": "\n".join(lines),
        "data": {"node": subject.id, "dependents": [
            {"id": d["node"].id, "relation": d["via"].relation.value, "depth": d["depth"]} for d in deps
        ]},
    }


def _dependencies(engine: CirdanEngine, subject: Node) -> dict:
    deps = engine.queries.dependencies(subject.id)
    lines = [f"{subject.name} depends on {len(deps)} components:"] if deps else [
        f"{subject.name} has no outgoing dependencies in the graph."
    ]
    lines += [
        f"- {d['node'].name} ({d['node'].type}, via {d['via'].relation.value}, {d['via'].confidence.value})"
        for d in deps
    ]
    return {
        "answer": "\n".join(lines),
        "data": {"node": subject.id, "dependencies": [
            {"id": d["node"].id, "relation": d["via"].relation.value, "depth": d["depth"]} for d in deps
        ]},
    }


def _entrypoints(engine: CirdanEngine) -> dict:
    entries = engine.queries.public_entrypoints()
    lines = [f"{len(entries)} publicly reachable components:"] if entries else ["No public entry points detected."]
    lines += [f"- {e['node'].name} ({e['node'].type}): {'; '.join(e['reasons'])}" for e in entries]
    return {
        "answer": "\n".join(lines),
        "data": {"entrypoints": [{"id": e["node"].id, "reasons": e["reasons"]} for e in entries]},
    }


def _whats_broken(engine: CirdanEngine) -> dict:
    findings = engine.drift()
    incidents = engine.incident_list()
    unhealthy = engine.queries.unhealthy()
    lines: list[str] = []
    if incidents:
        lines.append("Active incidents:")
        lines += [f"- {i.get('title')} (status {i.get('status')})" for i in incidents]
    if findings:
        lines.append("Findings:")
        lines += [f"- [{f.severity}] {f.summary}" for f in findings]
    if not lines:
        lines = ["Nothing is currently failing as far as Cirdan can observe."
                 if not unhealthy else "Unhealthy components: " + ", ".join(n.name for n in unhealthy)]
    return {
        "answer": "\n".join(lines),
        "data": {
            "incidents": incidents,
            "findings": [f.model_dump() for f in findings],
            "unhealthy": [n.id for n in unhealthy],
        },
    }


def _what_can_agent_do(engine: CirdanEngine) -> dict:
    caps = engine.access.capabilities
    nice = {
        "file_read": "read repo files", "file_write": "write repo files", "shell": "run shell",
        "docker_read": "inspect Docker", "docker_write": "operate Docker containers",
        "kubernetes_read": "query Kubernetes", "kubernetes_write": "update Kubernetes workloads",
        "aws_read": "query AWS", "systemd": "inspect systemd units", "journald": "read journald logs",
        "prometheus_read": "query Prometheus",
    }
    have = [v for k, v in nice.items() if caps.get(k)]
    missing = [v for k, v in nice.items() if not caps.get(k)]
    lines = ["Current agent/session capabilities:"] + [f"- {c}" for c in have]
    if missing:
        lines.append("Not available: " + ", ".join(missing))
    return {"answer": "\n".join(lines), "data": {"capabilities": caps}}


def _describe(engine: CirdanEngine, subject: Node) -> dict:
    neighborhood = engine.queries.neighbors(subject.id, depth=1)
    state = subject.attrs.get("health") or subject.attrs.get("state")
    lines = [
        f"{subject.name}: {subject.type} (origin {subject.origin.value}, confidence {subject.confidence.value})"
        + (f", state {state}" if state else "")
    ]
    for edge in neighborhood["edges"]:
        lines.append(f"- {edge.source} —{edge.relation.value}→ {edge.target}")
    if subject.evidence:
        lines.append("Evidence: " + "; ".join(subject.evidence[:3]))
    return {
        "answer": "\n".join(lines),
        "data": {
            "node": subject.model_dump(),
            "edges": [e.model_dump() for e in neighborhood["edges"]],
        },
    }
