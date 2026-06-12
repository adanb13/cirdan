import os

import pytest

from cirdan.engine import CirdanEngine
from cirdan.graph.communities import compute_communities
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def engine(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    eng = CirdanEngine(compose_app)
    eng._access = make_access(docker_read=True)
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.builder().run_static()
    eng.builder().run_live()
    return eng


def test_communities_deterministic(engine):
    first = compute_communities(engine.store)
    second = compute_communities(engine.store)
    assert first == second
    assert first  # non-empty
    # api and its database are tightly connected (depends_on + connects_to)
    # and must land in the same subsystem.
    assert first["service:api"] == first["database:postgres"]
    # At least one real multi-member subsystem exists.
    from collections import Counter

    assert Counter(first.values()).most_common(1)[0][1] >= 3


def test_resolution_accepted(engine):
    low = compute_communities(engine.store, resolution=0.5)
    high = compute_communities(engine.store, resolution=2.0)
    assert set(low) == set(high)  # same nodes, possibly different groupings


def test_exports_carry_community(engine):
    summary = engine.map(live=True)
    assert summary["nodes"] > 0
    import json

    graph = json.loads((engine.config.output_dir / "infra.graph.json").read_text())
    api = next(n for n in graph["nodes"] if n["id"] == "service:api")
    assert isinstance(api["community"], int)

    report = (engine.config.output_dir / "INFRA_REPORT.md").read_text()
    assert "## Subsystems" in report
    html = (engine.config.output_dir / "infra.html").read_text()
    assert "color by subsystem" in html


def test_empty_graph_ok(tmp_path):
    from cirdan.graph.store import GraphStore

    assert compute_communities(GraphStore(":memory:")) == {}
