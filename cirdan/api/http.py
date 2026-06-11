"""HTTP API (optional [api] extra): the daemon as a shared team service.

Also accepts a minimal OTLP/HTTP JSON payload at /v1/logs so OpenTelemetry
collectors can forward log records into Cirdan's event store.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from cirdan.access.redaction import redact_obj, redact_text
from cirdan.engine import CirdanEngine


def build_app(engine: CirdanEngine, mcp: bool = False):
    from fastapi import Body, FastAPI, HTTPException

    mcp_server = None
    if mcp:
        from cirdan.mcp.server import build_mcp_server

        mcp_server = build_mcp_server(engine)
        mcp_server.settings.streamable_http_path = "/"

    @asynccontextmanager
    async def lifespan(app):
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title="cirdan", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "nodes": len(engine.store.all_nodes())}

    @app.get("/fingerprint")
    def fingerprint():
        return redact_obj(engine.fingerprint.model_dump())

    @app.post("/fingerprint/refresh")
    def fingerprint_refresh():
        return redact_obj(engine.refresh_fingerprint().model_dump())

    @app.get("/access")
    def access():
        return redact_obj(engine.access.model_dump())

    @app.get("/graph")
    def graph():
        from cirdan.graph.export import graph_payload

        return graph_payload(engine.store)

    @app.post("/graph/query")
    def graph_query(payload: dict = Body(...)):
        from cirdan.query import answer_query

        question = payload.get("question", "")
        if not question:
            raise HTTPException(400, "missing 'question'")
        return redact_obj(answer_query(engine, question))

    @app.get("/graph/nodes/{node_id:path}/neighbors")
    def node_neighbors(node_id: str, depth: int = 1):
        node = engine.store.resolve(node_id)
        if node is None:
            raise HTTPException(404, f"no node matching '{node_id}'")
        result = engine.queries.neighbors(node.id, depth=depth)
        return redact_obj({
            "nodes": [n.model_dump() for n in result["nodes"]],
            "edges": [e.model_dump() for e in result["edges"]],
        })

    @app.get("/graph/nodes/{node_id:path}")
    def node(node_id: str):
        found = engine.store.resolve(node_id)
        if found is None:
            raise HTTPException(404, f"no node matching '{node_id}'")
        return redact_obj({
            "node": found.model_dump(),
            "edges": [e.model_dump() for e in engine.store.edges_for(found.id)],
        })

    @app.get("/services")
    def services():
        return redact_obj([n.model_dump() for n in engine.queries.services()])

    @app.get("/incidents")
    def incidents(include_resolved: bool = False, detect: bool = False):
        if detect:
            engine.detect_incidents()
        return redact_obj([i.model_dump() for i in engine.incidents.list(include_resolved=include_resolved)])

    @app.get("/incidents/{incident_id}")
    def incident(incident_id: str):
        found = engine.incidents.get(incident_id)
        if found is None:
            raise HTTPException(404, f"no incident '{incident_id}'")
        return redact_obj(found.model_dump())

    @app.post("/incidents/{incident_id}/explain")
    def incident_explain(incident_id: str):
        report = engine.explain_incident(incident_id)
        if report is None:
            raise HTTPException(404, f"no incident '{incident_id}'")
        return {"markdown": report}

    @app.get("/actions")
    def actions(node: str):
        from cirdan.actions import list_actions

        return redact_obj([s.model_dump() for s in list_actions(engine, node)])

    @app.post("/actions/execute")
    def actions_execute(payload: dict = Body(...)):
        from cirdan.actions import execute_action, find_action

        action_id = payload.get("action_id", "")
        spec = find_action(engine, action_id)
        if spec is None:
            raise HTTPException(404, f"unknown or unavailable action '{action_id}'")
        record = execute_action(engine, spec)
        return redact_obj(record.model_dump())

    @app.post("/actions/{record_id}/verify")
    def actions_verify(record_id: str):
        from cirdan.actions.executor import get_record
        from cirdan.verify import verify_action

        record = get_record(engine, record_id)
        if record is None:
            raise HTTPException(404, f"no action record '{record_id}'")
        return redact_obj(verify_action(engine, record))

    @app.post("/views/generate")
    def views_generate(payload: dict = Body(...)):
        from cirdan.ui.render import render_markdown
        from cirdan.ui.router import build_view

        request = payload.get("request", "")
        if not request:
            raise HTTPException(400, "missing 'request'")
        spec = build_view(engine, request)
        paths = engine.save_view(spec)
        return redact_obj({"view_type": spec.view_type, "title": spec.title,
                           "paths": paths, "markdown": render_markdown(spec)})

    @app.get("/audit")
    def audit(limit: int = 100):
        return redact_obj(engine.audit.tail(limit))

    @app.post("/v1/logs")
    def otlp_logs(payload: dict = Body(...)):
        """Minimal OTLP/HTTP JSON log ingestion."""
        from cirdan.telemetry.clusters import template_key
        from cirdan.telemetry.events import Event, severity_of

        count = 0
        for resource_logs in payload.get("resourceLogs", []) or []:
            attrs = {
                a.get("key"): (a.get("value") or {}).get("stringValue")
                for a in ((resource_logs.get("resource") or {}).get("attributes") or [])
            }
            service = attrs.get("service.name", "")
            for scope_logs in resource_logs.get("scopeLogs", []) or []:
                for record in scope_logs.get("logRecords", []) or []:
                    body = (record.get("body") or {}).get("stringValue", "")
                    if not body:
                        continue
                    severity = (record.get("severityText") or "").lower()
                    if severity not in ("error", "warning"):
                        severity = severity_of(body)
                    message = redact_text(body)[:2000]
                    engine.events.add(Event(
                        source_type="log", provider="opentelemetry", severity=severity,
                        resource=service and f"service:{service}" or "", service=service,
                        message=message,
                        error_family=template_key(message) if severity != "info" else "",
                    ))
                    count += 1
        return {"accepted": count}

    if mcp_server is not None:
        app.mount("/mcp", mcp_server.streamable_http_app())
    return app


async def serve_http(engine: CirdanEngine, mcp: bool = False,
                     host: str = "127.0.0.1", port: int = 8090) -> None:
    import uvicorn

    app = build_app(engine, mcp=mcp)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()
