import os

import pytest

from cirdan.engine import CirdanEngine
from cirdan.graph.contrib import (
    ContributionError,
    agent_contributions,
    annotate_node,
    contribute_edge,
    contribute_node,
)
from cirdan.graph.schema import Confidence, Origin
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


def test_rejects_missing_evidence(engine):
    with pytest.raises(ContributionError, match="evidence is required"):
        contribute_node(engine, "queue:orders", type="Queue", name="orders", evidence=[])
    with pytest.raises(ContributionError, match="evidence"):
        contribute_edge(engine, "api", "postgres", "DEPENDS_ON", evidence=["   "])


def test_rejects_extracted_confidence(engine):
    with pytest.raises(ContributionError, match="EXTRACTED is reserved"):
        contribute_edge(engine, "api", "postgres", "DEPENDS_ON",
                        evidence=["x"], confidence="EXTRACTED")


def test_rejects_bad_relation_type_and_id(engine):
    with pytest.raises(ContributionError, match="invalid relation"):
        contribute_edge(engine, "api", "postgres", "TALKS_TO", evidence=["x"])
    with pytest.raises(ContributionError, match="invalid node type"):
        contribute_node(engine, "thing:x", type="Widget", name="x", evidence=["x"])
    with pytest.raises(ContributionError, match="prefix:name"):
        contribute_node(engine, "no-prefix", type="Queue", name="x", evidence=["x"])


def test_rejects_missing_endpoint_and_self_edge(engine):
    with pytest.raises(ContributionError, match="does not match any node"):
        contribute_edge(engine, "api", "no-such-thing-xyz", "DEPENDS_ON", evidence=["x"])
    with pytest.raises(ContributionError, match="same node"):
        contribute_edge(engine, "service:api", "api", "DEPENDS_ON", evidence=["x"])


def test_node_and_edge_roundtrip_with_attribution(engine):
    node = contribute_node(engine, "queue:orders", type="Queue", name="orders",
                           evidence=["docs/arch.md: 'orders flow through a queue'"],
                           agent="claude")
    assert node.source_adapter == "agent:claude"
    assert node.confidence == Confidence.INFERRED
    assert node.origin == Origin.STATIC

    edge = contribute_edge(engine, "api", "queue:orders", "WRITES_TO",
                           evidence=["docs/arch.md: 'api enqueues orders'"], agent="claude")
    assert edge.attrs["source_adapter"] == "agent:claude"
    assert edge.source == "service:api"

    contributions = agent_contributions(engine)
    assert [n.id for n in contributions["nodes"]] == ["queue:orders"]
    assert [e.key for e in contributions["edges"]] == [("service:api", "queue:orders", "WRITES_TO")]


def test_evidence_is_redacted(engine):
    node = contribute_node(engine, "service:ext", type="Service", name="ext",
                           evidence=["config has DATABASE_PASSWORD=hunter2 in it"])
    assert "hunter2" not in node.evidence[0]


def test_annotate_preserves_live_node_origin(engine):
    before = engine.store.get_node("container:compose-app-api-1")
    assert before.origin == Origin.LIVE
    node = annotate_node(engine, "container:compose-app-api-1",
                         evidence=["runbook.md: 'api container is the canary'"], agent="claude")
    assert node.origin == Origin.LIVE  # not corrupted to BOTH/STATIC
    assert node.confidence == before.confidence
    assert any("canary" in e for e in node.evidence)


def test_annotate_requires_something(engine):
    with pytest.raises(ContributionError, match="nothing to annotate"):
        annotate_node(engine, "api")


def test_contributions_survive_live_refresh(engine):
    contribute_node(engine, "queue:orders", type="Queue", name="orders", evidence=["doc"])
    contribute_edge(engine, "api", "queue:orders", "WRITES_TO", evidence=["doc"])
    engine.builder().run_live()
    contributions = agent_contributions(engine)
    assert len(contributions["nodes"]) == 1
    assert len(contributions["edges"]) == 1


def test_agent_cannot_upgrade_deterministic_claim(engine):
    edge = contribute_edge(engine, "api", "postgres", "DEPENDS_ON",
                           evidence=["doc"], confidence="AMBIGUOUS")
    # compose declared this DEPENDS_ON as EXTRACTED; merge keeps the stronger label.
    assert edge.confidence == Confidence.EXTRACTED


def test_cli_add_edge_roundtrip(monkeypatch, tmp_path):
    """CLI and test must share one db: use a tmp project copy with default paths."""
    import shutil

    from typer.testing import CliRunner

    from cirdan.cli.main import app
    from cirdan.config import CirdanConfig

    root = tmp_path / "proj"
    shutil.copytree(FIXTURES / "repos" / "compose-app", root)
    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    eng = CirdanEngine(CirdanConfig(root=str(root)))
    eng._access = make_access()
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.builder().run_static()

    runner = CliRunner()
    result = runner.invoke(app, [
        "graph", "add-edge", "api", "redis", "READS_FROM",
        "--evidence", "README: 'api caches sessions in redis'",
        "--agent", "test", "--path", str(root),
    ])
    assert result.exit_code == 0, result.output
    assert "READS_FROM" in result.output

    bad = runner.invoke(app, [
        "graph", "add-edge", "api", "nothing-here-xyz", "READS_FROM",
        "--evidence", "x", "--path", str(root),
    ])
    assert bad.exit_code == 1
    assert "rejected" in bad.output
