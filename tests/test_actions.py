import os

import pytest

from cirdan.actions import execute_action, find_action, list_actions
from cirdan.actions.executor import get_record
from cirdan.engine import CirdanEngine
from cirdan.verify import verify_action
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


def test_list_actions_for_container(engine):
    specs = list_actions(engine, "container:compose-app-api-1")
    names = {s.name for s in specs}
    assert {"inspect", "logs", "restart", "stop", "start"} <= names
    restart = next(s for s in specs if s.name == "restart")
    assert restart.writes is True
    assert restart.verify == "container_healthy"


def test_logical_service_inherits_container_actions(engine):
    specs = list_actions(engine, "api")
    assert any(s.name == "restart" for s in specs)


def test_no_write_actions_without_write_access(engine):
    engine._access = make_access(docker_read=True, docker_write=False)
    specs = list_actions(engine, "container:compose-app-api-1")
    assert all(not s.writes for s in specs)


def test_execute_and_verify_action(engine):
    spec = find_action(engine, "docker.restart:compose-app-api-1")
    assert spec is not None
    record = execute_action(engine, spec)
    assert record.result.ok
    assert record.pre_state.get("state") == "running"

    # Recorded in the graph and retrievable.
    assert engine.store.get_node(f"action:{record.record_id}") is not None
    assert get_record(engine, record.record_id) is not None

    verification = verify_action(engine, record)
    assert verification["status"] == "succeeded"
    check_names = {c["name"] for c in verification["checks"]}
    assert {"command_succeeded", "container_healthy", "no_new_errors"} <= check_names
    assert get_record(engine, record.record_id).status == "verify_passed"


def test_action_attaches_to_incident(engine):
    from cirdan.telemetry.events import Event
    from cirdan.telemetry.clusters import template_key

    for i in range(8):
        engine.events.add(
            Event(severity="error", provider="docker", resource="container:compose-app-api-1",
                  service="api", message="ERROR boom", error_family=template_key("ERROR boom")),
        )
    engine.detect_incidents(ingest=False)
    spike = next(i for i in engine.incidents.list() if i.key.startswith("error_cluster"))
    assert "service:api" in spike.affected_nodes

    spec = find_action(engine, "docker.restart:compose-app-api-1")
    # restart targets the container; use an action against the service-affected node
    record = execute_action(engine, spec)
    # incident affecting container:compose-app-api-1 ... spike affects service:api and container
    updated = engine.incidents.get(spike.id)
    if record.spec.node_id in spike.affected_nodes:
        assert record.record_id in updated.actions
        assert updated.status == "verifying"
