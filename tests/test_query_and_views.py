import os

import pytest

from cirdan.engine import CirdanEngine
from cirdan.query import answer_query
from cirdan.ui.render import render_html, render_markdown
from cirdan.ui.router import build_view
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


def test_query_dependents(engine):
    result = answer_query(engine, "what depends on postgres?")
    assert "api" in result["answer"]
    assert result["data"]["dependents"]


def test_query_whats_broken(engine):
    result = answer_query(engine, "what broke in the last hour?")
    assert "exited" in result["answer"] or "not running" in result["answer"]
    assert result["data"]["findings"]


def test_query_capabilities(engine):
    result = answer_query(engine, "what can the agent do here?")
    assert "operate Docker containers" in result["answer"]


def test_query_unknown_returns_counts(engine):
    result = answer_query(engine, "zzz qqq xyzzy")
    assert "node_type_counts" in result["data"]
    assert "state_counts" in result["data"]
    assert "what is running" in result["answer"]
    assert "show state" in result["answer"]


def test_query_whats_running(engine):
    result = answer_query(engine, "what is running right now?")
    workloads = result["data"]["workloads"]
    assert workloads
    assert all(w["state"] for w in workloads)
    assert result["data"]["state_counts"]
    assert "running" in result["answer"]


def test_query_whats_running_does_not_shadow_fingerprint(engine):
    result = answer_query(engine, "what is running on this infrastructure?")
    assert "primary_runtime" in result["data"]


def test_view_router_dependency(engine):
    spec = build_view(engine, "show api as a dependency graph")
    assert spec.view_type == "dependency"
    assert spec.scope["node"] == "service:api"
    types = [c.type for c in spec.components]
    assert "DependencyGraph" in types and "StateTable" in types


def test_view_router_topology_default(engine):
    spec = build_view(engine, "show me the infrastructure map")
    assert spec.view_type == "topology"


def test_view_router_entrypoints(engine):
    spec = build_view(engine, "show all public entry points")
    assert spec.view_type == "entrypoints"
    rows = spec.components[1].data["rows"]
    assert any("web" in row[0] for row in rows)


def test_renderers_produce_output(engine):
    spec = build_view(engine, "show me the infrastructure map")
    html = render_html(spec)
    assert "cytoscape" in html and "service:api" in html
    md = render_markdown(spec)
    assert "# " in md and "nodes" in md


def test_save_view_writes_artifacts(engine):
    spec = build_view(engine, "show api dependencies")
    paths = engine.save_view(spec)
    assert len(paths) == 3
    for p in paths:
        assert os.path.getsize(p) > 0


def test_map_produces_artifacts(engine):
    summary = engine.map(live=True)
    assert summary["nodes"] > 5
    out = engine.config.output_dir
    for name in ("infra.html", "INFRA_REPORT.md", "infra.graph.json", "fingerprint.json", "access.json"):
        assert (out / name).is_file(), name
