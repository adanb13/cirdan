import os

import pytest

from cirdan import __version__
from cirdan.engine import CirdanEngine
from tests.conftest import FIXTURES, make_access

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cirdan.api.http import build_app  # noqa: E402


@pytest.fixture
def client(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    engine = CirdanEngine(compose_app)
    engine._access = make_access(docker_read=True, docker_write=True)
    engine.store.kv_set("access_context", engine._access.model_dump_json())
    engine.builder().run_static()
    engine.builder().run_live()
    with TestClient(build_app(engine)) as test_client:
        test_client.engine = engine
        yield test_client


def test_health_and_graph(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/openapi.json").json()["info"]["version"] == __version__
    graph = client.get("/graph").json()
    assert graph["counts"]["nodes"] > 5


def test_query_endpoint(client):
    resp = client.post("/graph/query", json={"question": "what depends on postgres?"})
    assert "api" in resp.json()["answer"]


def test_node_endpoints(client):
    resp = client.get("/graph/nodes/database:postgres")
    assert resp.json()["node"]["name"] == "postgres"
    resp = client.get("/graph/nodes/postgres/neighbors?depth=1")
    assert resp.status_code == 200
    assert resp.json()["nodes"]


def test_actions_roundtrip(client):
    actions = client.get("/actions", params={"node": "container:compose-app-api-1"}).json()
    restart = next(a for a in actions if a["name"] == "restart")
    record = client.post("/actions/execute", json={"action_id": restart["id"]}).json()
    assert record["result"]["ok"] is True
    verification = client.post(f"/actions/{record['record_id']}/verify").json()
    assert verification["status"] == "succeeded"


def test_otlp_log_ingest(client):
    payload = {
        "resourceLogs": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "api"}}]},
            "scopeLogs": [{
                "logRecords": [
                    {"body": {"stringValue": "ERROR database connection refused"}, "severityText": "ERROR"},
                    {"body": {"stringValue": "request handled in 12ms"}},
                ]
            }],
        }]
    }
    resp = client.post("/v1/logs", json=payload)
    assert resp.json()["accepted"] == 2
    events = client.engine.events.recent(window_seconds=60, severity=("error",))
    assert any("database connection refused" in e.message for e in events)


def test_views_generate(client):
    resp = client.post("/views/generate", json={"request": "show api dependencies"})
    data = resp.json()
    assert data["view_type"] == "dependency"
    assert len(data["paths"]) == 3
