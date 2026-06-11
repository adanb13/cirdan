import os
from pathlib import Path

import pytest

from cirdan.adapters.docker import DockerAdapter
from cirdan.adapters.kubernetes import KubernetesAdapter
from cirdan.graph.builder import GraphBuilder
from cirdan.graph.diff import compute_drift
from cirdan.graph.schema import Origin
from cirdan.graph.store import GraphStore
from tests.conftest import FIXTURES, make_access

FAKE_BIN = FIXTURES / "fake-bin"


@pytest.fixture(autouse=True)
def fake_path(monkeypatch):
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}")


def test_docker_discovery(compose_app):
    access = make_access(docker_read=True, docker_write=True)
    adapter = DockerAdapter(compose_app, access)
    result = adapter.discover()
    by_id = {n.id: n for n in result.nodes}

    api = by_id["container:compose-app-api-1"]
    assert api.attrs["state"] == "running"
    assert api.attrs["health"] == "healthy"
    assert api.attrs["published_ports"] == ["8000"]

    pg = by_id["container:compose-app-postgres-1"]
    assert pg.attrs["state"] == "exited"
    assert pg.attrs["restart_count"] == 3

    # Compose labels produce logical service nodes that merge with the static graph.
    assert by_id["service:api"].origin == Origin.LIVE
    assert by_id["database:postgres"].attrs["state"] == "exited"

    relations = {(e.source, e.target, e.relation.value) for e in result.edges}
    assert ("service:api", "container:compose-app-api-1", "CREATES") in relations


def test_docker_static_live_merge_and_drift(compose_app):
    access = make_access(docker_read=True, docker_write=True)
    store = GraphStore(":memory:")
    builder = GraphBuilder(compose_app, access, store)
    builder.run_static()
    builder.run_live()

    api = store.get_node("service:api")
    assert api.origin == Origin.BOTH  # declared in compose AND observed live

    findings = compute_drift(store, live_systems={"docker"})
    kinds = {(f.kind, f.node_id) for f in findings}
    # web/redis are declared in compose but not running in the fake docker.
    assert ("declared_not_running", "loadbalancer:web") in kinds
    assert ("declared_not_running", "cache:redis") in kinds
    # stray-worker runs without any declaration.
    assert ("undeclared_runtime", "container:stray-worker") in kinds
    # exited postgres container is unhealthy.
    assert any(f.kind == "unhealthy" and "postgres" in f.node_id for f in findings)


def test_docker_logs_and_state(compose_app):
    access = make_access(docker_read=True)
    adapter = DockerAdapter(compose_app, access)
    logs = adapter.collect_logs("container:compose-app-api-1", lines=10)
    assert any("ERROR" in line for line in logs)
    state = adapter.current_state("container:compose-app-api-1")
    assert state["state"] == "running"


def test_kubernetes_discovery(k8s_aws_app):
    access = make_access(kubernetes_read=True, kubernetes_write=True)
    access.details["kubernetes_context"] = "prod-eks"
    adapter = KubernetesAdapter(k8s_aws_app, access)
    result = adapter.discover()
    store = GraphStore(":memory:")
    store.apply(result.nodes, [])
    by_id = {n.id: n for n in store.all_nodes()}

    checkout = by_id["service:checkout-api"]
    assert checkout.attrs["replicas"] == 3
    assert checkout.attrs["ready_replicas"] == 1
    assert checkout.attrs["state"] == "degraded"
    assert checkout.attrs["service_type"] == "LoadBalancer"

    crash = by_id["k8s-pod:production/checkout-api-7f9abc-x2"]
    assert crash.attrs["state"] == "crashloopbackoff"
    assert crash.attrs["restarts"] == 7

    relations = {(e.source, e.target, e.relation.value) for e in result.edges}
    assert ("service:checkout-api", "k8s-pod:production/checkout-api-7f9abc-x2", "CREATES") in relations
    assert ("k8s-pod:production/checkout-api-7f9abc-x1", "k8s-node:ip-10-0-3-21", "RUNS_ON") in relations


def test_kubernetes_drift_degraded(k8s_aws_app):
    access = make_access(kubernetes_read=True)
    access.details["kubernetes_context"] = "prod-eks"
    store = GraphStore(":memory:")
    builder = GraphBuilder(k8s_aws_app, access, store)
    builder.run_static()
    builder.run_live()

    checkout = store.get_node("service:checkout-api")
    assert checkout.origin == Origin.BOTH

    findings = compute_drift(store, live_systems={"kubernetes"})
    degraded = [f for f in findings if f.kind == "degraded_capacity"]
    assert degraded and degraded[0].node_id == "service:checkout-api"
    assert "3 replicas but only 1" in degraded[0].summary


def test_kubernetes_events(k8s_aws_app):
    access = make_access(kubernetes_read=True)
    adapter = KubernetesAdapter(k8s_aws_app, access)
    events = adapter.recent_events()
    assert events[0]["reason"] == "BackOff"
    assert events[0]["resource"] == "Pod/checkout-api-7f9abc-x2"
