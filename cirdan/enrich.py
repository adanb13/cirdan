"""Graph enrichment: hand an agent the things deterministic adapters couldn't
connect, and let it contribute the missing knowledge through the write surface."""

from __future__ import annotations

import shlex
from pathlib import Path

from cirdan.engine import CirdanEngine
from cirdan.graph.contrib import agent_contributions
from cirdan.graph.schema import Confidence, NodeType, Origin, Relation

INTERESTING_TYPES = {
    NodeType.SERVICE.value, NodeType.DATABASE.value, NodeType.QUEUE.value,
    NodeType.CACHE.value, NodeType.BUCKET.value, NodeType.PIPELINE.value,
    NodeType.LOAD_BALANCER.value, "CloudResource",
}

BRIEF_RULES = """\
## Contribution rules

1. **Read first** — inspect the docs and configs listed above; check what already
   exists with `cirdan query "<name>"` before adding anything.
2. **Evidence is mandatory** — every contribution must quote its source, e.g.
   `--evidence "README.md: 'the worker drains the orders queue nightly'"`.
3. **You contribute beliefs, not facts** — everything you add is recorded as
   INFERRED (use `--ambiguous` when the source is unclear). You cannot override
   what the deterministic adapters extracted.
4. Prefer connecting existing nodes over creating new ones. Create a node only
   for a real component the scanners missed (e.g. an external SaaS dependency).

## How to contribute

    cirdan graph add-edge <source> <target> <RELATION> --evidence "…"
    cirdan graph add-node <prefix:name> --type <Type> --name <name> --evidence "…"
    cirdan graph annotate <node> --evidence "…"

Relations: CONNECTS_TO, DEPENDS_ON, CALLS, READS_FROM, WRITES_TO, DEPLOYS,
ROUTES_TO, OWNS, CONTAINS, OBSERVED_IN. (MCP equivalents: upsert_edge,
upsert_node, annotate_node, get_enrichment_targets.)
"""


def enrichment_targets(engine: CirdanEngine) -> dict:
    """What the deterministic lane left unconnected or uncertain."""
    nodes = engine.store.all_nodes()
    degree: dict[str, int] = {}
    for edge in engine.store.all_edges():
        degree[edge.source] = degree.get(edge.source, 0) + 1
        degree[edge.target] = degree.get(edge.target, 0) + 1

    docs: list[str] = []
    for node in nodes:
        if node.type == NodeType.REPOSITORY.value:
            docs += list(node.attrs.get("docs") or [])
            docs += list(node.attrs.get("dockerfiles") or [])

    isolated = [
        n for n in nodes
        if degree.get(n.id, 0) == 0 and n.type in INTERESTING_TYPES
        and not n.source_adapter.startswith("agent:")
    ]
    uncertain = [
        n for n in nodes
        if n.confidence in (Confidence.INFERRED, Confidence.AMBIGUOUS)
        and n.origin != Origin.DERIVED and not n.source_adapter.startswith("agent:")
    ]
    unlinked_iac = [
        n for n in nodes if n.source_adapter == "terraform" and degree.get(n.id, 0) == 0
    ]
    deploys_edges = {
        e.source for e in engine.store.all_edges() if e.relation == Relation.DEPLOYS
    }
    pipelines_without_deploys = [
        n for n in nodes
        if n.type == NodeType.PIPELINE.value and n.attrs.get("deploys") and n.id not in deploys_edges
    ]
    return {
        "docs": sorted(set(docs)),
        "isolated": isolated,
        "uncertain": uncertain,
        "unlinked_iac": unlinked_iac,
        "pipelines_without_deploys": pipelines_without_deploys,
    }


def summarize_targets(targets: dict) -> str:
    """One-line human summary, e.g. '9 targets: 5 isolated, 2 unlinked IaC, 2 uncertain'."""
    parts = [
        (len(targets["isolated"]), "isolated"),
        (len(targets["unlinked_iac"]), "unlinked IaC"),
        (len(targets["pipelines_without_deploys"]), "pipelines without deploys"),
        (len(targets["uncertain"]), "uncertain"),
    ]
    total = sum(count for count, _ in parts)
    if not total:
        return "0 targets"
    return f"{total} targets: " + ", ".join(f"{count} {label}" for count, label in parts if count)


def _node_lines(nodes: list, limit: int = 20) -> list[str]:
    lines = [f"- `{n.id}` ({n.type}, {n.confidence.value})" for n in nodes[:limit]]
    if len(nodes) > limit:
        lines.append(f"- …and {len(nodes) - limit} more")
    return lines or ["- none"]


def build_enrichment_brief(engine: CirdanEngine) -> str:
    targets = enrichment_targets(engine)
    nodes = engine.store.all_nodes()
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.type] = counts.get(node.type, 0) + 1
    lines = [
        "# Cirdan graph-enrichment brief",
        "",
        "You are enriching Cirdan's infrastructure graph with knowledge its",
        "deterministic scanners cannot extract: relationships described in docs,",
        "implied by code, or known to operators.",
        "",
        f"Graph today: {len(nodes)} nodes, {len(engine.store.all_edges())} edges — "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8]),
        "",
        "## Read these first",
        "",
    ]
    lines += [f"- {d}" for d in targets["docs"]] or ["- (no docs indexed)"]
    lines += ["", "## Components with no connections (find their place)", ""]
    lines += _node_lines(targets["isolated"])
    lines += ["", "## IaC resources unlinked to anything live", ""]
    lines += _node_lines(targets["unlinked_iac"])
    lines += ["", "## Pipelines that look like they deploy something (link them)", ""]
    lines += _node_lines(targets["pipelines_without_deploys"])
    lines += ["", "## Uncertain claims worth confirming or correcting", ""]
    lines += _node_lines(targets["uncertain"])
    lines += ["", BRIEF_RULES]
    if engine.config.project == "system":
        lines.append("Note: this is the machine-level (system) graph — append `--system` "
                     "to every `cirdan` command above.")

    out_dir = engine.config.output_dir / "enrichment"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "brief.md"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def resolve_enrich_command(engine: CirdanEngine, override: str | None) -> str | None:
    if override:
        return override
    if engine.config.enrich.command:
        return engine.config.enrich.command
    from cirdan.agents.installer import detect_enrich_command

    detected = detect_enrich_command()
    return detected[1] if detected else None


async def run_enrichment(engine: CirdanEngine, command_template: str, brief_file: str,
                         timeout: float = 900.0) -> tuple[bool, dict]:
    """Invoke the agent against the brief; return (ok, contribution diff)."""
    from cirdan.incidents.responder import run_agent_command

    before = agent_contributions(engine)
    before_nodes = {n.id for n in before["nodes"]}
    before_edges = {e.key for e in before["edges"]}

    argv = shlex.split(command_template.format(brief_file=brief_file))
    ok, _note = await run_agent_command(engine, argv, label="enrich", timeout=timeout,
                                        subject=Path(brief_file).name)

    after = agent_contributions(engine)
    diff = {
        "nodes": [n for n in after["nodes"] if n.id not in before_nodes],
        "edges": [e for e in after["edges"] if e.key not in before_edges],
    }
    engine.audit.write(
        "enrich",
        f"enrichment pass added {len(diff['nodes'])} nodes, {len(diff['edges'])} edges",
    )
    return ok, diff
