"""Engine facade: one wiring point for CLI, MCP server, HTTP API, and daemon.

Holds config, access context, graph store, and audit log. Access context and
fingerprint are cached in the store with a short TTL so read commands
(query/show) don't re-probe the environment on every invocation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cirdan.access.context import AccessContext, detect_access
from cirdan.audit import AuditWriter
from cirdan.config import CirdanConfig
from cirdan.fingerprint.engine import Fingerprint, fingerprint_environment
from cirdan.graph.builder import GraphBuilder
from cirdan.graph.diff import Finding, compute_drift
from cirdan.graph.queries import GraphQueries
from cirdan.graph.store import GraphStore

ACCESS_TTL_SECONDS = 600


def _age_seconds(ts: str) -> float:
    try:
        then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


class CirdanEngine:
    def __init__(self, config: CirdanConfig):
        self.config = config
        out = config.ensure_output_dirs()
        self.audit = AuditWriter(out / "audit.jsonl")
        self.store = GraphStore(config.db_path)
        self.queries = GraphQueries(self.store)
        self._access: AccessContext | None = None
        self._fingerprint: Fingerprint | None = None

    @classmethod
    def open(cls, path: str = ".", config_file: str | None = None) -> "CirdanEngine":
        from cirdan.config import load_config

        return cls(load_config(path, config_file))

    # -- access & fingerprint -------------------------------------------------

    @property
    def access(self) -> AccessContext:
        if self._access is None:
            cached = self.store.kv_get("access_context")
            if cached:
                ctx = AccessContext.model_validate_json(cached)
                if _age_seconds(ctx.detected_at) < ACCESS_TTL_SECONDS:
                    self._access = ctx
            if self._access is None:
                self.refresh_access()
        return self._access

    def refresh_access(self) -> AccessContext:
        self._access = detect_access(self.config)
        self.store.kv_set("access_context", self._access.model_dump_json())
        return self._access

    @property
    def fingerprint(self) -> Fingerprint:
        if self._fingerprint is None:
            cached = self.store.kv_get("fingerprint")
            if cached:
                fp = Fingerprint.model_validate_json(cached)
                if _age_seconds(fp.detected_at) < ACCESS_TTL_SECONDS:
                    self._fingerprint = fp
            if self._fingerprint is None:
                self.refresh_fingerprint()
        return self._fingerprint

    def refresh_fingerprint(self) -> Fingerprint:
        self._fingerprint = fingerprint_environment(self.config, self.access)
        self.store.kv_set("fingerprint", self._fingerprint.model_dump_json())
        self.audit.write(
            "fingerprint",
            f"fingerprinted environment: runtime={self._fingerprint.primary_runtime} "
            f"cloud={self._fingerprint.primary_cloud}",
        )
        return self._fingerprint

    # -- graph ------------------------------------------------------------------

    def builder(self) -> GraphBuilder:
        return GraphBuilder(self.config, self.access, self.store, self.audit)

    def live_systems(self) -> set[str]:
        caps = self.access.capabilities
        systems = set()
        if caps.get("docker_read"):
            systems.add("docker")
        if caps.get("kubernetes_read"):
            systems.add("kubernetes")
        if caps.get("systemd"):
            systems.add("systemd")
        if caps.get("aws_read"):
            systems.add("aws")
        return systems

    def drift(self) -> list[Finding]:
        return compute_drift(self.store, self.live_systems())

    # -- map: the full pipeline --------------------------------------------------

    def map(self, live: bool | None = None) -> dict:
        """Access → fingerprint → static + live discovery → drift → artifacts."""
        from cirdan.graph import export as gexport
        from cirdan.reports import build_infra_report
        from cirdan.ui.render import render_html
        from cirdan.ui.view_spec import ViewComponent, ViewSpec, graph_component_data
        from cirdan.util import dump_json
        from cirdan.access.redaction import redact_obj

        out = self.config.ensure_output_dirs()
        access = self.refresh_access()
        fp = self.refresh_fingerprint()
        builder = self.builder()
        summary: dict = {"adapters": builder.run_static()}
        do_live = live if live is not None else bool(self.live_systems())
        if do_live:
            summary["adapters"].update(builder.run_live())
        findings = self.drift()
        incidents = self.incident_list()

        # JSON artifacts
        (out / "access.json").write_text(dump_json(redact_obj(access.model_dump())))
        (out / "fingerprint.json").write_text(dump_json(redact_obj(fp.model_dump())))
        gexport.export_graph(self.store, out)
        gexport.export_services(self.store, out)
        gexport.export_dependencies(self.store, out)
        gexport.export_schema(out)
        gexport.export_runtime_state(self.store, out)

        # Human artifacts
        report = build_infra_report(self.store, fp, access, findings, incidents)
        (out / "INFRA_REPORT.md").write_text(report)
        nodes, edges = self.store.all_nodes(), self.store.all_edges()
        spec = ViewSpec(
            view_type="topology",
            title=f"{self.config.project or self.config.root_path.name}: infrastructure map",
            components=[
                ViewComponent(
                    type="SummaryCard",
                    title="Overview",
                    data={"text": "", "facts": {
                        "Primary runtime": fp.primary_runtime or "unknown",
                        "Primary cloud": fp.primary_cloud or "none",
                        "Nodes": str(len(nodes)),
                        "Edges": str(len(edges)),
                        "Findings": str(len(findings)),
                    }},
                ),
                ViewComponent(type="TopologyGraph", title="Topology",
                              data=graph_component_data(nodes, edges)),
            ],
        )
        (out / "infra.html").write_text(render_html(spec))

        summary.update(
            nodes=len(nodes), edges=len(edges),
            findings=[f.model_dump() for f in findings],
            artifacts=[str(out / name) for name in (
                "infra.html", "INFRA_REPORT.md", "infra.graph.json", "fingerprint.json",
                "access.json", "services.json", "dependencies.json", "runtime-state.json",
            )],
            fingerprint=fp.model_dump(),
        )
        self.audit.write("map", f"mapped infrastructure: {len(nodes)} nodes, {len(edges)} edges, "
                                f"{len(findings)} findings", live=do_live)
        return summary

    # -- views ---------------------------------------------------------------------

    def save_view(self, spec, formats: tuple[str, ...] = ("html", "md", "json")) -> list[str]:
        from cirdan.graph.schema import Node, NodeType, Origin
        from cirdan.ui.render import render_html, render_markdown, view_slug
        from cirdan.util import dump_json
        from cirdan.access.redaction import redact_obj

        out = self.config.output_dir / "views" / "generated"
        out.mkdir(parents=True, exist_ok=True)
        slug = view_slug(spec.title)
        paths = []
        if "html" in formats:
            path = out / f"{slug}.html"
            path.write_text(render_html(spec))
            paths.append(str(path))
        if "md" in formats:
            path = out / f"{slug}.md"
            path.write_text(render_markdown(spec))
            paths.append(str(path))
        if "json" in formats:
            path = out / f"{slug}.view.json"
            path.write_text(dump_json(redact_obj(spec.model_dump())))
            paths.append(str(path))
        self.store.upsert_node(
            Node(
                id=f"view:{slug}", type=NodeType.GENERATED_VIEW.value, name=spec.title,
                origin=Origin.DERIVED, source_adapter="ui",
                evidence=[f"generated {spec.view_type} view"], attrs={"paths": paths},
            )
        )
        self.audit.write("view", f"generated view '{spec.title}'", paths=paths)
        return paths

    # -- telemetry & incidents ----------------------------------------------------

    @property
    def events(self):
        from cirdan.telemetry.events import EventStore

        if not hasattr(self, "_events"):
            self._events = EventStore(self.store)
        return self._events

    @property
    def incidents(self):
        from cirdan.incidents.store import IncidentStore

        if not hasattr(self, "_incidents"):
            self._incidents = IncidentStore(self.store)
        return self._incidents

    def ingest_telemetry(self, max_targets: int = 10) -> int:
        """Pull recent logs for unhealthy/drifting components through live adapters."""
        from cirdan.adapters.registry import get_adapters
        from cirdan.telemetry.events import log_line_to_event

        targets: dict[str, object] = {}
        for node in self.queries.unhealthy():
            targets[node.id] = node
        for finding in self.drift():
            node = self.store.get_node(finding.node_id)
            if node and node.origin.value in ("live", "both"):
                targets[node.id] = node
        adapters = {a.name: a for a in get_adapters(self.config, self.access, kind="live")}
        count = 0
        for node in list(targets.values())[:max_targets]:
            adapter = adapters.get(node.source_adapter)
            if adapter is None:
                continue
            try:
                lines = adapter.collect_logs(node.id, lines=self.config.telemetry.log_tail_lines)
            except Exception:
                continue
            for line in lines:
                event = log_line_to_event(line, provider=adapter.name, resource=node.id, service=node.name)
                if event.severity != "info":
                    self.events.add(event)
                    count += 1
        return count

    def detect_incidents(self, ingest: bool = True) -> list:
        """One detection pass: ingest telemetry, evaluate conditions, persist incidents."""
        from cirdan.graph.schema import Confidence, Edge, Node, NodeType, Origin, Relation
        from cirdan.incidents.detector import detect_incidents

        if ingest:
            self.ingest_telemetry()
        findings = self.drift()
        touched = detect_incidents(
            self.store, self.incidents, findings, self.events,
            window_seconds=self.config.telemetry.error_window_seconds * 6,
        )
        for incident in touched:
            self.store.upsert_node(
                Node(
                    id=f"incident:{incident.id}", type=NodeType.INCIDENT.value, name=incident.title,
                    origin=Origin.DERIVED, source_adapter="incidents",
                    confidence=Confidence.INFERRED,
                    evidence=incident.evidence[:5],
                    attrs={"status": incident.status, "severity": incident.severity,
                           "incident_id": incident.id},
                )
            )
            for nid in incident.affected_nodes:
                if self.store.get_node(nid):
                    self.store.upsert_edge(
                        Edge(source=f"incident:{incident.id}", target=nid,
                             relation=Relation.AFFECTS, confidence=Confidence.INFERRED,
                             evidence=incident.evidence[:2])
                    )
            self.audit.write("incident", f"{incident.id} → {incident.status}: {incident.title}",
                             severity=incident.severity)
        self.incidents.export(self.config.ensure_output_dirs())
        return touched

    def explain_incident(self, incident_id: str) -> str | None:
        from cirdan.incidents.reports import explain_incident

        incident = self.incidents.get(incident_id)
        if incident is None:
            return None
        return explain_incident(incident, self.store, self.events)

    def incident_list(self, include_resolved: bool = False) -> list[dict]:
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT data FROM incidents ORDER BY started_at DESC"
            ).fetchall()
        incidents = [json.loads(r["data"]) for r in rows]
        if not include_resolved:
            incidents = [i for i in incidents if i.get("status") != "resolved"]
        return incidents
