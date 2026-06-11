import os

import pytest

from cirdan.engine import CirdanEngine
from cirdan.telemetry.clusters import cluster_events, template_key
from cirdan.telemetry.events import Event, log_line_to_event
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def engine(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    eng = CirdanEngine(compose_app)
    eng._access = make_access(docker_read=True, docker_write=True)
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.builder().run_static()
    eng.builder().run_live()
    return eng


def test_template_key_groups_similar_messages():
    a = template_key("connection to 10.0.3.21:5432 refused (attempt 7)")
    b = template_key("connection to 10.9.1.4:5432 refused (attempt 12)")
    assert a == b


def test_log_line_severity():
    event = log_line_to_event("ERROR database connection refused", "docker", "container:x", "api")
    assert event.severity == "error"
    assert event.error_family


def test_cluster_events():
    events = [
        log_line_to_event(f"ERROR database connection refused attempt {i}", "docker", "container:x", "api")
        for i in range(6)
    ] + [log_line_to_event("WARN slow query 142ms", "docker", "container:x", "api")]
    clusters = cluster_events(events)
    assert clusters[0]["count"] == 6
    assert clusters[0]["severity"] == "error"
    assert clusters[0]["services"] == ["api"]


def test_detect_incidents_from_drift_and_errors(engine):
    for i in range(8):
        engine.events.add(
            Event(severity="error", provider="docker", resource="container:compose-app-api-1",
                  service="api", message=f"ERROR database connection refused attempt {i}",
                  error_family=template_key("ERROR database connection refused attempt 1")),
        )
    touched = engine.detect_incidents(ingest=False)
    assert touched
    open_incidents = engine.incidents.list()
    keys = {i.key.split(":")[0] for i in open_incidents}
    assert "error_cluster" in keys
    assert "unhealthy" in keys or "declared_not_running" in keys

    spike = next(i for i in open_incidents if i.key.startswith("error_cluster"))
    assert spike.status == "active"
    assert "api" in spike.title
    # api's dependents (web) are in the blast radius
    assert any("web" in n for n in spike.diagnosis.get("blast_radius", []))

    # Incident node lands in the graph with AFFECTS edges.
    node = engine.store.get_node(f"incident:{spike.id}")
    assert node is not None

    # Re-running does not duplicate.
    before = len(engine.incidents.list(include_resolved=True))
    engine.detect_incidents(ingest=False)
    assert len(engine.incidents.list(include_resolved=True)) == before


def test_incident_resolution_lifecycle(engine):
    for i in range(8):
        engine.events.add(
            Event(severity="error", provider="docker", resource="container:compose-app-api-1",
                  service="api", message="ERROR boom",
                  error_family=template_key("ERROR boom")),
        )
    engine.detect_incidents(ingest=False)
    spike = next(i for i in engine.incidents.list() if i.key.startswith("error_cluster"))

    # Clear the events (simulate recovery) and run two more passes.
    with engine.store.lock:
        engine.store.conn.execute("DELETE FROM events")
        engine.store.conn.commit()
    engine.detect_incidents(ingest=False)
    assert engine.incidents.get(spike.id).status == "verifying"
    engine.detect_incidents(ingest=False)
    assert engine.incidents.get(spike.id).status == "resolved"


def test_explain_incident(engine):
    for i in range(8):
        engine.events.add(
            Event(severity="error", provider="docker", resource="container:compose-app-api-1",
                  service="api", message="ERROR database connection refused",
                  error_family=template_key("ERROR database connection refused")),
        )
    engine.detect_incidents(ingest=False)
    spike = next(i for i in engine.incidents.list() if i.key.startswith("error_cluster"))
    report = engine.explain_incident(spike.id)
    assert "# Incident:" in report
    assert "Blast radius" in report
    assert "error clusters" in report.lower()
